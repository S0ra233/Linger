"""世界书保存后同步关联角色；匹配读取本次角色快照中的版本。"""
from __future__ import annotations

# Python 3.13 即时求值时，类内 list 方法会遮住后续签名中的内置 list 类型。
import json
from time import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..events import EventBus
from ..storage.configuration import RevisionConflict
from ..storage.database import Database


class WorldbookRef(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    revision: int = Field(ge=1)


class WorldEntry(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(default='', max_length=200)
    content: str = Field(default='', max_length=16000)
    keys: list[str] = Field(default_factory=list, max_length=64)
    secondary_keys: list[str] = Field(default_factory=list, max_length=64)
    selective_logic: Literal['none', 'and_any', 'and_all', 'not_any', 'not_all'] = 'none'
    constant: bool = False
    enabled: bool = True
    case_sensitive: bool = False
    insertion_order: int = Field(default=100, ge=-100000, le=100000)
    priority: int = Field(default=100, ge=-100000, le=100000)
    position: Literal['before_char', 'after_char'] = 'after_char'
    run_types: list[Literal['input', 'life']] = Field(default_factory=list, max_length=2)
    sticky: int = Field(default=0, ge=0, le=1000)
    cooldown: int = Field(default=0, ge=0, le=1000)
    delay: int = Field(default=0, ge=0, le=1000)

    @model_validator(mode='after')
    def bounds(self):
        if len(set(self.run_types)) != len(self.run_types):
            raise ValueError('世界书适用运行类型不能重复。')
        if any(not value or len(value) > 200 for value in [*self.keys, *self.secondary_keys]):
            raise ValueError('每个关键词须为 1—200 字符。')
        return self


class WorldbookInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default='', max_length=4000)
    scan_depth: int = Field(default=20, ge=0, le=100)
    token_budget: int = Field(default=2048, ge=0, le=32000)
    entries: list[WorldEntry] = Field(default_factory=list, max_length=500)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def unique(self):
        if not self.name.strip() or len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError('世界书名称不能为空，条目编号不能重复。')
        if len(self.model_dump_json().encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('世界书内容超过 2 MiB。')
        return self


class Worldbooks:
    def __init__(self, database: Database, events: EventBus, characters):
        self.database, self.events = database, events
        self.characters = characters

    def list(self):
        result = []
        for row in self.database.all('SELECT * FROM worldbooks ORDER BY updated_at DESC'):
            config = json.loads(row['config_json'])
            result.append({'id': row['id'], 'name': config['name'], 'revision': row['revision'],
                           'entry_count': len(config['entries']), 'updated_at': row['updated_at']})
        return result

    def get(self, book_id: str, revision: int | None = None):
        row = self.database.one('SELECT config_json,revision FROM worldbooks WHERE id=?', (book_id,)) if revision is None else self.database.one(
            'SELECT config_json,revision FROM worldbook_versions WHERE worldbook_id=? AND revision=?', (book_id, revision))
        if not row:
            raise LookupError('世界书版本不存在。')
        source = self.database.one('SELECT import_id FROM worldbook_sources WHERE worldbook_id=? AND revision=?', (book_id, row['revision']))
        config = json.loads(row['config_json'])
        # 只补读取视图的默认值；不改写历史版本，扫描时再统一验证条目。
        config['entries'] = [{'run_types': [], 'sticky': 0, 'cooldown': 0, 'delay': 0, **entry} for entry in config['entries']]
        return {**config, 'id': book_id, 'revision': row['revision'],
                'import_id': source['import_id'] if source else None}

    def write(self, tx, body: WorldbookInput, book_id: str | None = None, import_id: str | None = None):
        target, now = book_id or uuid4().hex, time()
        row = tx.execute('SELECT revision FROM worldbooks WHERE id=?', (target,)).fetchone()
        if book_id and not row:
            raise LookupError('世界书不存在。')
        revision = row['revision'] if row else 0
        if revision != body.revision:
            raise RevisionConflict('世界书已被修改，请刷新。')
        if not row and tx.execute('SELECT count(*) FROM worldbooks').fetchone()[0] >= 200:
            raise ValueError('世界书数量已达 200 个。')
        config = body.model_dump(exclude={'revision'})
        config['name'] = body.name.strip()
        value = json.dumps(config, ensure_ascii=False)
        if row:
            tx.execute('UPDATE worldbooks SET config_json=?,revision=?,updated_at=? WHERE id=?', (value, revision + 1, now, target))
        else:
            tx.execute('INSERT INTO worldbooks VALUES (?,?,1,?,?)', (target, value, now, now))
        tx.execute('INSERT INTO worldbook_versions VALUES (?,?,?,?)', (target, revision + 1, value, now))
        previous = tx.execute('SELECT import_id FROM worldbook_sources WHERE worldbook_id=? AND revision=?', (target, revision)).fetchone()
        source = import_id or (previous['import_id'] if previous else None)
        if source:
            tx.execute('INSERT INTO worldbook_sources VALUES (?,?,?)', (target, revision + 1, source))
        tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                   (now, 'worldbook.saved', json.dumps({'id': target, 'revision': revision + 1})))
        self.characters.refresh_worldbook(tx, target)
        return {**config, 'id': target, 'revision': revision + 1}

    def save(self, body: WorldbookInput, book_id: str | None = None):
        with self.database.transaction() as tx:
            result = self.write(tx, body, book_id)
        self.events.publish('materials')
        return result

    def select(self, refs: list[dict], scan_messages: list[str], budget: int, substitute, *, run_type: Literal['input', 'life'] = 'input', instance_id=None):
        """一次扫描，不递归、不执行正则；常驻超预算明确失败，触发条目不足时留下诊断。"""
        if run_type not in {'input', 'life'}:
            raise ValueError('世界书扫描类型无效。')
        candidates, trace, used_by_book, clocks = [], [], {}, []
        for ref in refs:
            book = self.get(ref['id'], ref['revision'])
            scope = (instance_id, book['id'], book['revision'])
            clock = self.database.one('SELECT turns FROM worldbook_clocks WHERE instance_id=? AND worldbook_id=? AND revision=?', scope) if instance_id else None
            turn = clock['turns'] if clock else 0
            clocks.append({'worldbook_id': book['id'], 'revision': book['revision'], 'turns': turn})
            effects = {row['entry_id']: row for row in self.database.all('SELECT * FROM worldbook_effects WHERE instance_id=? AND worldbook_id=? AND revision=?', scope)} if instance_id else {}
            text = '\n'.join(scan_messages[-book['scan_depth']:]) if book['scan_depth'] else ''
            for raw in book['entries']:
                entry = WorldEntry.model_validate(raw)
                effect = effects.get(entry.id, {})
                sticky_active = turn < effect.get('sticky_until', 0)
                record = {'worldbook_id': book['id'], 'worldbook_name': book['name'], 'revision': book['revision'],
                          'entry_id': entry.id, 'name': entry.name, 'constant': entry.constant,
                          'run_types': entry.run_types, 'selected': False, 'estimated_tokens': 0,
                          'primary_hits': [], 'secondary_hits': [], 'scan_depth': book['scan_depth'],
                          'turn': turn, 'sticky': entry.sticky, 'cooldown': entry.cooldown, 'delay': entry.delay,
                          'sticky_active': sticky_active, 'sticky_until': effect.get('sticky_until', 0),
                          'cooldown_until': effect.get('cooldown_until', 0)}
                trace.append(record)
                reason = ('disabled' if not entry.enabled else 'empty' if not entry.content.strip()
                          else 'run_type' if entry.run_types and run_type not in entry.run_types
                          else 'delay' if turn < entry.delay
                          else 'cooldown' if not sticky_active and turn < effect.get('cooldown_until', 0) else None)
                if reason:
                    record['reason'] = reason
                    continue
                haystack = text if entry.case_sensitive else text.casefold()
                def match(key):
                    value = substitute(key)
                    return bool(value) and (value if entry.case_sensitive else value.casefold()) in haystack
                if not entry.constant and not sticky_active:
                    primary = [match(key) for key in entry.keys]
                    secondary = [match(key) for key in entry.secondary_keys]
                    record['primary_hits'] = [key for key, hit in zip(entry.keys, primary) if hit]
                    record['secondary_hits'] = [key for key, hit in zip(entry.secondary_keys, secondary) if hit]
                    if not any(primary):
                        record['reason'] = 'primary_miss' if entry.keys else 'no_primary_keys'
                        continue
                    if entry.selective_logic != 'none' and not (bool(secondary) and {
                        'and_any': any(secondary), 'and_all': all(secondary),
                        'not_any': not any(secondary), 'not_all': not all(secondary)}[entry.selective_logic]):
                        record['reason'] = 'secondary_miss'
                        continue
                content = substitute(entry.content)
                if not content.strip():
                    record['reason'] = 'empty'
                    continue
                cost = (len(content.encode('utf-8')) + 1) // 2 + 16
                record['estimated_tokens'] = cost
                candidates.append((entry, book, content, cost, record))
        selected, used = [], 0
        for entry, book, content, cost, record in sorted(candidates, key=lambda item: (not item[0].constant, -item[0].priority, -item[0].insertion_order, item[1]['id'], item[0].id)):
            total_fits = used + cost <= budget
            book_fits = used_by_book.get(book['id'], 0) + cost <= book['token_budget']
            fits = total_fits and book_fits
            record.update({'selected': fits, 'reason': ('sticky' if record['sticky_active'] else 'matched') if fits else 'total_budget' if not total_fits else 'book_budget'})
            if not fits:
                if entry.constant:
                    raise ValueError(f"常驻世界书「{book['name']} / {entry.name or entry.id}」超出{'总' if not total_fits else '本书'}预算。")
                continue
            selected.append((entry, book, content))
            used += cost
            used_by_book[book['id']] = used_by_book.get(book['id'], 0) + cost
        selected.sort(key=lambda item: (item[0].insertion_order, item[1]['id'], item[0].id))
        return {'before': [content for entry, _, content in selected if entry.position == 'before_char'],
                'after': [content for entry, _, content in selected if entry.position == 'after_char'],
                'trace': trace, 'estimated_tokens': used, 'run_type': run_type, 'clocks': clocks}

    def commit(self, tx, run):
        """只在有效 RP 计划的同一事务推进；预览、摘要和工具循环不计轮次。"""
        report = json.loads(run['context_json'] or '{}').get('worldbooks', {})
        for clock in report.get('clocks', []):
            scope = (run['instance_id'], clock['worldbook_id'], clock['revision'])
            row = tx.execute('SELECT turns FROM worldbook_clocks WHERE instance_id=? AND worldbook_id=? AND revision=?', scope).fetchone()
            if (row['turns'] if row else 0) != clock['turns']:
                raise ValueError('世界书时效已变化，本轮不能提交。')
            tx.execute('INSERT INTO worldbook_clocks VALUES (?,?,?,?) ON CONFLICT(instance_id,worldbook_id,revision) DO UPDATE SET turns=excluded.turns', (*scope, clock['turns'] + 1))
        for item in report.get('trace', []):
            if not item['selected'] or item.get('sticky_active') or not (item.get('sticky') or item.get('cooldown')):
                continue
            start = item['turn']
            sticky_until = start + max(1, item['sticky'])
            tx.execute('INSERT INTO worldbook_effects VALUES (?,?,?,?,?,?) ON CONFLICT(instance_id,worldbook_id,revision,entry_id) DO UPDATE SET sticky_until=excluded.sticky_until,cooldown_until=excluded.cooldown_until',
                (run['instance_id'], item['worldbook_id'], item['revision'], item['entry_id'], sticky_until, sticky_until + item['cooldown']))
