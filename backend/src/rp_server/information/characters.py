"""保存角色后同步关联生活线；历史版本仅供追溯。"""
import json
from time import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..events import EventBus
from ..storage.configuration import RevisionConflict
from ..storage.database import Database
from ..roleplay.store import invalidate
from .worldbooks import WorldbookRef


class CharacterInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default='', max_length=24000)
    personality: str = Field(default='', max_length=16000)
    scenario: str = Field(default='', max_length=16000)
    dialogue_examples: str = Field(default='', max_length=24000)
    first_message: str = Field(default='', max_length=8000)
    style: str = Field(default='', max_length=4000)
    nickname: str = Field(default='', max_length=80)
    system_prompt: str = Field(default='', max_length=8000)
    post_history_instructions: str = Field(default='', max_length=8000)
    alternate_greetings: list[str] = Field(default_factory=list, max_length=32)
    worldbooks: list[WorldbookRef] = Field(default_factory=list, max_length=8)
    knowledge_libraries: list[str] = Field(default_factory=list, max_length=8)
    revision: int = Field(default=0, ge=0)


class Characters:
    def __init__(self, database: Database, events: EventBus):
        self.database, self.events = database, events

    def list(self):
        return [{**CharacterInput.model_validate(json.loads(row['config_json'])).model_dump(exclude={'revision'}), 'id': row['id'], 'revision': row['revision'],
                 'updated_at': row['updated_at']}
                for row in self.database.all('SELECT * FROM characters ORDER BY updated_at DESC')]

    @staticmethod
    def latest_worldbooks(tx, refs):
        result = []
        for ref in refs:
            row = tx.execute('SELECT revision FROM worldbooks WHERE id=?', (ref['id'],)).fetchone()
            if not row:
                raise ValueError('角色引用的世界书不存在。')
            result.append({'id': ref['id'], 'revision': row['revision']})
        return result

    @staticmethod
    def sync_instances(tx, character_id=None):
        sql = '''SELECT r.id,r.character_revision,c.revision FROM role_instances r
            JOIN characters c ON c.id=r.character_id WHERE r.character_revision<>c.revision'''
        rows = tx.execute(sql + (' AND r.character_id=?' if character_id else ''),
                          (character_id,) if character_id else ()).fetchall()
        for row in rows:
            tx.execute('UPDATE role_instances SET character_revision=? WHERE id=?', (row['revision'], row['id']))
            invalidate(tx, row['id'], '角色设定已保存并自动同步，旧计划失效。')
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (time(), 'instance.character_synced', json.dumps({'instance_id': row['id'],
                        'previous_revision': row['character_revision'], 'revision': row['revision'], 'automatic': True})))

    def refresh_worldbook(self, tx, book_id):
        for row in tx.execute('SELECT id,revision,config_json FROM characters').fetchall():
            config = json.loads(row['config_json'])
            if any(ref['id'] == book_id for ref in config.get('worldbooks', [])):
                refs = self.latest_worldbooks(tx, config['worldbooks'])
                if refs != config['worldbooks']:
                    self.write(tx, CharacterInput.model_validate({**config, 'worldbooks': refs, 'revision': row['revision']}), row['id'])

    def synchronize(self):
        """启动时衔接旧版的手动采用配置，不要求用户重新保存。"""
        with self.database.transaction() as tx:
            for row in tx.execute('SELECT id,revision,config_json FROM characters').fetchall():
                config = json.loads(row['config_json'])
                refs = self.latest_worldbooks(tx, config.get('worldbooks', []))
                if refs != config.get('worldbooks', []):
                    self.write(tx, CharacterInput.model_validate({**config, 'worldbooks': refs, 'revision': row['revision']}), row['id'])
            self.sync_instances(tx)

    def save(self, body: CharacterInput, character_id: str | None = None):
        with self.database.transaction() as tx:
            result = self.write(tx, body, character_id)
        self.events.publish('characters')
        return result

    def write(self, tx, body: CharacterInput, character_id: str | None = None, import_id: str | None = None):
        if not body.name.strip():
            raise ValueError('角色名称不能为空。')
        now, target = time(), character_id or uuid4().hex
        row = tx.execute('SELECT * FROM characters WHERE id=?', (target,)).fetchone()
        if character_id and not row:
            raise LookupError('角色不存在。')
        config = {**(json.loads(row['config_json']) if row else {}), **body.model_dump(exclude={'revision'}, exclude_unset=True)}
        config = CharacterInput.model_validate(config).model_dump(exclude={'revision'})
        config['name'] = body.name.strip()
        config['worldbooks'] = self.latest_worldbooks(tx, config['worldbooks'])
        encoded = json.dumps(config, ensure_ascii=False)
        # 管理请求还有实际字节上限；这里单独约束持久资料大小。
        if len(encoded.encode('utf-8')) > 256000 or any(len(text) > 8000 for text in body.alternate_greetings):
            raise ValueError('角色资料合计过长，请缩短后保存。')
        # 外层允许角色及关联世界书／导入来源共同提交，不嵌套事务。
        for ref in config['worldbooks']:
            if not tx.execute('SELECT 1 FROM worldbook_versions WHERE worldbook_id=? AND revision=?', (ref['id'], ref['revision'])).fetchone():
                raise ValueError('角色引用的世界书版本不存在。')
        if len({ref['id'] for ref in config['worldbooks']}) != len(config['worldbooks']):
            raise ValueError('同一角色不能重复绑定一本世界书。')
        if len(set(config['knowledge_libraries'])) != len(config['knowledge_libraries']):
            raise ValueError('资料库不能重复绑定。')
        for library_id in config['knowledge_libraries']:
            if not tx.execute('SELECT 1 FROM knowledge_libraries WHERE id=?', (library_id,)).fetchone():
                raise ValueError('角色绑定的资料库不存在。')
        revision = row['revision'] if row else 0
        if body.revision != revision:
            raise RevisionConflict('角色已被修改，请刷新后重试。')
        if row is None and tx.execute('SELECT count(*) FROM characters').fetchone()[0] >= 200:
            raise ValueError('角色数量已达 200 个。')
        if row:
            tx.execute('UPDATE characters SET config_json=?,revision=?,updated_at=? WHERE id=?',
                       (encoded, revision + 1, now, target))
        else:
            tx.execute('INSERT INTO characters VALUES (?,?,?,?,?)', (target, encoded, 1, now, now))
        tx.execute('INSERT INTO character_versions VALUES (?,?,?,?)', (target, revision + 1, encoded, now))
        tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                   (now, 'character.saved', json.dumps({'id': target, 'revision': revision + 1})))
        previous = tx.execute('SELECT import_id FROM character_sources WHERE character_id=? AND revision=?', (target, revision)).fetchone()
        source = import_id or (previous['import_id'] if previous else None)
        if source:
            tx.execute('INSERT INTO character_sources VALUES (?,?,?)', (target, revision + 1, source))
        self.sync_instances(tx, target)
        return {**config, 'id': target, 'revision': revision + 1, 'updated_at': now}
