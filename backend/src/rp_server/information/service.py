"""实例私有记忆与显式授权资料库；索引更新和有效事实同一事务提交。"""
import hashlib
import heapq
import json
import math
import unicodedata
from time import time
from uuid import uuid4

from ..roleplay.store import encoded, invalidate
from ..storage.configuration import RevisionConflict
from .models import MemoryInput
from .text import chunks, terms
from .evidence import Evidence
from .memory_governance import MemoryGovernance
from .validity import VALID_MEMORY, active_memory_sql


RELATIONS = ''' LEFT JOIN memories m ON m.id=c.memory_id
    LEFT JOIN knowledge_documents d ON d.id=c.document_id
    LEFT JOIN knowledge_libraries l ON l.id=d.library_id '''
JOIN = ' FROM information_chunks c' + RELATIONS


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class Information:
    def __init__(self, store):
        self.store, self.database, self.events = store, store.database, store.events
        self.evidence = Evidence(store)
        self.governance = MemoryGovernance(self)

    def libraries(self):
        return self.database.all('''SELECT l.*,count(d.id) AS document_count FROM knowledge_libraries l
            LEFT JOIN knowledge_documents d ON d.library_id=l.id GROUP BY l.id ORDER BY l.updated_at DESC''')

    def library(self, library_id):
        row = self.database.one('SELECT * FROM knowledge_libraries WHERE id=?', (library_id,))
        if not row:
            raise LookupError('资料库不存在。')
        return row

    def save_library(self, body, library_id=None):
        if not body.name.strip():
            raise ValueError('资料库名称不能为空。')
        target, now = library_id or uuid4().hex, time()
        with self.database.transaction() as tx:
            row = self.library(target) if library_id else None
            if (row['revision'] if row else 0) != body.revision:
                raise RevisionConflict('资料库已更新，请重新载入。')
            if not row and tx.execute('SELECT count(*) FROM knowledge_libraries').fetchone()[0] >= 200:
                raise ValueError('资料库最多 200 个。')
            tx.execute('''INSERT INTO knowledge_libraries VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,enabled=excluded.enabled,revision=excluded.revision,updated_at=excluded.updated_at''',
                (target, body.name.strip(), int(body.enabled), body.revision + 1, now, now))
            if row and row['enabled'] != int(body.enabled):
                self.invalidate_library(tx, target)
            self.audit(tx, 'library.saved', {'id': target, 'revision': body.revision + 1})
        self.events.publish('information')
        return self.library(target)

    def invalidate_library(self, tx, library_id):
        rows = tx.execute('''SELECT r.id,v.config_json FROM role_instances r JOIN character_versions v
            ON v.character_id=r.character_id AND v.revision=r.character_revision''').fetchall()
        for row in rows:
            if library_id in json.loads(row['config_json']).get('knowledge_libraries', []):
                invalidate(tx, row['id'], '授权资料库已修改或停用。')

    @staticmethod
    def audit(tx, action, details):
        tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)', (time(), action, encoded(details)))

    def documents(self, library_id, before=None):
        self.library(library_id)
        boundary = self.database.one('SELECT created_at FROM knowledge_documents WHERE id=? AND library_id=?', (before, library_id)) if before else None
        if before and boundary is None:
            raise ValueError('文档分页编号不属于该资料库。')
        at = boundary['created_at'] if boundary else None
        rows = self.database.all('''SELECT id,library_id,title,enabled,revision,updated_at,length(text) AS characters
            FROM knowledge_documents WHERE library_id=? AND (? IS NULL OR created_at<? OR (created_at=? AND id<?))
            ORDER BY created_at DESC,id DESC LIMIT 31''', (library_id, at, at, at, before))
        return {'items': rows[:30], 'next_before': rows[29]['id'] if len(rows) > 30 else None}

    def document(self, library_id, document_id):
        row = self.database.one('SELECT * FROM knowledge_documents WHERE id=? AND library_id=?', (document_id, library_id))
        if not row:
            raise LookupError('资料文档不存在。')
        return row

    def save_document(self, library_id, body, document_id=None, source=None, encoding='utf-8'):
        if not body.title.strip() or not body.text.strip() or '\x00' in body.text:
            raise ValueError('标题与正文不能为空，正文不能含二进制空字符。')
        if len(body.text.encode('utf-8')) > 512000:
            raise ValueError('单篇资料不能超过 512000 UTF-8 字节。')
        prepared = [(chunk, terms(chunk.text)) for chunk in chunks(body.text)]
        target, now = document_id or uuid4().hex, time()
        with self.database.transaction() as tx:
            self.library(library_id)
            row = self.document(library_id, target) if document_id else None
            if (row['revision'] if row else 0) != body.revision:
                raise RevisionConflict('文档已更新，请重新载入。')
            if row and row['title'] == body.title.strip() and row['text'] == body.text and row['enabled'] == int(body.enabled):
                return row
            if not row and tx.execute('SELECT count(*) FROM knowledge_documents WHERE library_id=?', (library_id,)).fetchone()[0] >= 500:
                raise ValueError('每个资料库最多 500 篇文档。')
            revision, content_hash = body.revision + 1, digest(body.text)
            tx.execute('''INSERT INTO knowledge_documents VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,text=excluded.text,digest=excluded.digest,enabled=excluded.enabled,
                revision=excluded.revision,updated_at=excluded.updated_at''',
                (target, library_id, body.title.strip(), body.text, content_hash, int(body.enabled), revision, now, now))
            tx.execute('INSERT INTO knowledge_versions VALUES (?,?,?,?,?,?,?,?)',
                       (target, revision, body.title.strip(), body.text, content_hash, source if source is not None else body.text.encode('utf-8'), encoding, now))
            self.index(tx, prepared, revision, document_id=target)
            self.invalidate_library(tx, library_id)
            self.audit(tx, 'document.saved', {'id': target, 'library_id': library_id, 'revision': revision})
        self.events.publish('information')
        return self.document(library_id, target)

    @staticmethod
    def index(tx, prepared, revision, *, memory_id=None, document_id=None):
        # 旧版本仍可追溯；查询必须与当前版本及权限联结，不能直接用索引编号越权。
        for chunk, words in prepared:
            identifier = digest(f'{memory_id or document_id}:{revision}:{chunk.ordinal}')
            tx.execute('INSERT INTO information_chunks VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (identifier, memory_id, document_id, revision, chunk.ordinal, chunk.start, chunk.end,
                        chunk.start_line, chunk.end_line, chunk.text, max(1, sum(words.values()))))
            tx.executemany('INSERT INTO information_postings VALUES (?,?,?)', ((word, identifier, count) for word, count in words.items()))

    def memories(self, instance_id, before=None):
        self.store.instance(instance_id)
        boundary = self.database.one('SELECT created_at FROM memories WHERE id=? AND instance_id=?', (before, instance_id)) if before else None
        if before and boundary is None:
            raise ValueError('记忆分页编号不属于该生活线。')
        at = boundary['created_at'] if boundary else None
        rows = self.database.all('''SELECT m.*,(SELECT count(*) FROM memory_evidence s WHERE s.memory_id=m.id) AS evidence_count,
            CASE WHEN superseded_by IS NOT NULL THEN 'superseded'
            WHEN status='active' AND NOT (''' + VALID_MEMORY + ''') THEN 'source_invalid'
            WHEN status='active' AND expires_at IS NOT NULL AND expires_at<=? THEN 'expired' ELSE status END AS effective_status
            FROM memories m WHERE instance_id=? AND (? IS NULL OR created_at<? OR (created_at=? AND id<?))
            ORDER BY created_at DESC,id DESC LIMIT 31''', (time(), instance_id, at, at, at, before))
        return {'items': rows[:30], 'next_before': rows[29]['id'] if len(rows) > 30 else None}

    def memory(self, instance_id, memory_id):
        row = self.database.one('SELECT * FROM memories WHERE id=? AND instance_id=?', (memory_id, instance_id))
        if not row:
            raise LookupError('该生活线的记忆不存在。')
        return row

    def memory_detail(self, instance_id, memory_id):
        row = self.memory(instance_id, memory_id)
        refs = self.database.all('SELECT kind,source_id,revision FROM memory_evidence WHERE memory_id=? ORDER BY kind,source_id DESC LIMIT 101', (memory_id,))
        sources = [self.evidence.read(instance_id, ref['kind'], ref['source_id']) for ref in refs[:100]]
        return {'memory': row, 'sources': sources, 'sources_limited': len(refs) > 100,
                'replaces': self.database.all('SELECT id,text,revision,superseded_at FROM memories WHERE instance_id=? AND superseded_by=? ORDER BY id', (instance_id, memory_id)),
                'replacement': self.memory(instance_id, row['superseded_by']) if row['superseded_by'] else None,
                'versions': self.database.all('SELECT * FROM memory_versions WHERE memory_id=? ORDER BY revision DESC LIMIT 20', (memory_id,))}

    def valid_memory(self, instance_id, memory_id):
        return bool(self.database.one('SELECT 1 FROM memories m WHERE instance_id=? AND id=? AND (' + VALID_MEMORY + ')', (instance_id, memory_id)))

    def candidate_target(self, instance_id, candidate, memory_id=None):
        identifier = memory_id or getattr(candidate, 'memory_id', None)
        if identifier:
            return self.memory(instance_id, identifier)
        normalized = digest(' '.join(unicodedata.normalize('NFKC', candidate.text).split()))
        return self.database.one('SELECT * FROM memories WHERE instance_id=? AND kind=? AND digest=?', (instance_id, candidate.kind, normalized))

    def unchanged_memory(self, old, body, refs, *, allow_time_clear=True):
        """内容、元数据和证据均未变化时，不推进版本、保护审核或缓存失效。"""
        unchanged = bool(old and not body.replace_sources and not body.supersedes
            and all(old[key] == getattr(body, key) for key in ('text', 'kind', 'status', 'expires_at'))
            and ('valid_from' not in body.model_fields_set or body.valid_from == old['valid_from']
                 or body.valid_from is None and not allow_time_clear))
        if not unchanged or not refs:
            return unchanged
        # refs已规范化且最多20项；一次核对所需证据，不缓存到下次操作。
        conditions = ' OR '.join('(kind=? AND source_id=?)' for _ in refs)
        present = self.database.one('SELECT count(*) AS n FROM memory_evidence WHERE memory_id=? AND (' + conditions + ')',
            (old['id'], *[value for ref in refs for value in (ref.kind, ref.id)]))
        return present['n'] == len(refs)

    def validate_candidates(self, instance_id, through, candidates, *, allow_current=False):
        seen = set()
        for body in candidates:
            normalized = digest(' '.join(unicodedata.normalize('NFKC', body.text).split()))
            match = self.candidate_target(instance_id, body)
            key = body.memory_id or (match['id'] if match else (body.kind, normalized))
            if key in seen:
                raise ValueError('同一计划不能重复修改同一记忆。')
            seen.add(key)
            replacements = self.governance.validate(instance_id, body, match)
            for previous in replacements:
                if previous['id'] in seen:
                    raise ValueError('同一计划不能重复修改或替代同一记忆。')
                seen.add(previous['id'])
            self.evidence.normalize(instance_id, body.sources, body.source_message_ids, through=through, allow_current=allow_current)
            if body.memory_id:
                try:
                    row = self.memory(instance_id, body.memory_id)
                except LookupError as exc:
                    raise ValueError('记忆编号不属于当前生活线。') from exc
                if row['revision'] != body.revision:
                    raise ValueError('记忆版本已变化，请重新读取。')
                if row['status'] == 'forgotten' and body.operation != 'forget':
                    raise ValueError('已遗忘的记忆不能由模型自动恢复。')
                collision = self.database.one('SELECT id FROM memories WHERE instance_id=? AND kind=? AND digest=?', (instance_id, body.kind, normalized))
                if body.operation != 'forget' and collision and collision['id'] != body.memory_id:
                    raise ValueError('同内容记忆已经存在，请采用原编号与版本。')
            if body.operation != 'forget' and body.kind == 'temporary' and (body.expires_at is None or body.expires_at <= time()):
                raise ValueError('临时记忆的过期时间必须在未来。')

    def write_memory(self, tx, instance_id, body: MemoryInput, memory_id=None, run_id=None, *, current_id=None):
        refs = self.evidence.normalize(instance_id, body.sources, body.source_message_ids,
                                      allow_current=current_id is not None, current_id=current_id)
        normalized = digest(' '.join(unicodedata.normalize('NFKC', body.text).split()))
        old = self.candidate_target(instance_id, body, memory_id)
        if memory_id and old['revision'] != body.revision:
            raise RevisionConflict('记忆已修改，请重新载入。')
        if not memory_id and body.revision:
            raise RevisionConflict('新记忆不能提供已有修订。')
        self.governance.validate(instance_id, body, old)
        if self.unchanged_memory(old, body, refs, allow_time_clear=memory_id is not None):
            return old
        if run_id and old and old['locked']:
            raise ValueError('人工锁定记忆只能形成待审提案，不能直接写入。')
        if run_id and old and old['status'] == 'forgotten':
            return old
        if body.kind == 'temporary' and body.status == 'active' and (body.expires_at is None or body.expires_at <= time()):
            raise ValueError('临时记忆的过期时间必须在未来。')
        collision = self.database.one('SELECT id FROM memories WHERE instance_id=? AND kind=? AND digest=?', (instance_id, body.kind, normalized))
        if memory_id and collision and collision['id'] != memory_id:
            raise ValueError('同内容记忆已存在，请编辑原条目。')
        if old is None and tx.execute('SELECT count(*) FROM memories WHERE instance_id=?', (instance_id,)).fetchone()[0] >= 5000:
            raise ValueError('本批记忆保存数量已达 5000 条；现有数据保留，暂不再新增。')
        target, revision, now = old['id'] if old else uuid4().hex, old['revision'] + 1 if old else 1, time()
        tx.execute('''INSERT INTO memories(id,instance_id,kind,text,digest,expires_at,status,revision,created_at,updated_at,valid_from)
            VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,
            text=excluded.text,digest=excluded.digest,expires_at=excluded.expires_at,status=excluded.status,
            revision=excluded.revision,updated_at=excluded.updated_at,valid_from=excluded.valid_from,
            confirmed_at=CASE WHEN memories.locked=1 THEN excluded.updated_at ELSE memories.confirmed_at END''',
            (target, instance_id, body.kind, body.text.strip(), normalized, body.expires_at, body.status, revision, now, now,
             body.valid_from if 'valid_from' in body.model_fields_set and (memory_id is not None or body.valid_from is not None)
             else old['valid_from'] if old else None))
        if body.replace_sources:
            if run_id:
                raise ValueError('只有管理员可以替换整组记忆依据。')
            tx.execute('DELETE FROM memory_sources WHERE memory_id=?', (target,))
            tx.execute('DELETE FROM memory_evidence WHERE memory_id=?', (target,))
        tx.executemany('INSERT OR IGNORE INTO memory_sources VALUES (?,?)', ((target, ref.id) for ref in refs if ref.kind == 'input'))
        tx.executemany('INSERT OR IGNORE INTO memory_evidence VALUES (?,?,?,?)', ((target, ref.kind, ref.id, ref.revision) for ref in refs))
        saved = self.memory(instance_id, target)
        tx.execute('INSERT INTO memory_versions VALUES (?,?,?,?,?)',
                   (target, revision, encoded({**body.model_dump(), 'valid_from': saved['valid_from']}), run_id, now))
        self.index(tx, [(chunk, terms(chunk.text)) for chunk in chunks(body.text)], revision, memory_id=target)
        return self.memory(instance_id, target)

    def save_memory(self, instance_id, body, memory_id=None):
        if body.kind == 'temporary':
            raise ValueError('临时记忆已退役；旧资料可查阅、遗忘或人工转为长期资料。')
        with self.database.transaction() as tx:
            self.store.instance(instance_id)
            previous = self.candidate_target(instance_id, body, memory_id)
            result = self.write_memory(tx, instance_id, body, memory_id)
            self.governance.supersede(tx, instance_id, body, result)
            if previous and previous['id'] == result['id'] and previous['revision'] == result['revision'] and not body.supersedes:
                return result
            invalidate(tx, instance_id, '管理员修改了记忆资料。')
            self.audit(tx, 'memory.saved', {'id': result['id'], 'instance_id': instance_id, 'revision': result['revision']})
        self.events.publish('information', instance_ids=(instance_id,))
        return result

    def forget(self, instance_id, memory_id, revision):
        with self.database.transaction() as tx:
            row = self.memory(instance_id, memory_id)
            if row['revision'] != revision:
                raise RevisionConflict('记忆已变化，请刷新。')
            if row['status'] == 'forgotten':
                return row
            self._forget(tx, row)
            invalidate(tx, instance_id, '管理员遗忘了记忆。')
        self.events.publish('information', instance_ids=(instance_id,))
        return self.memory(instance_id, memory_id)

    def _forget(self, tx, row):
        # 管理员可遗忘锁定、过期或退役临时条目；不通过新增／编辑入口重新激活它。
        now, revision = time(), row['revision'] + 1
        tx.execute("UPDATE memories SET status='forgotten',revision=?,updated_at=? WHERE id=?", (revision, now, row['id']))
        tx.execute('INSERT INTO memory_versions VALUES (?,?,?,?,?)',
            (row['id'], revision, encoded({**row, 'status': 'forgotten', 'revision': revision, 'updated_at': now}), None, now))
        self.audit(tx, 'memory.forgotten', {'instance_id': row['instance_id'], 'id': row['id'], 'revision': revision})

    def forget_preview(self, instance_id, kinds):
        self.store.instance(instance_id)
        rows = self.database.all("SELECT id,kind,revision,protection_revision,locked FROM memories WHERE instance_id=? AND status<>'forgotten' ORDER BY id", (instance_id,))
        counts = {kind: sum(row['kind'] == kind for row in rows) for kind in ('profile', 'temporary', 'episode')}
        chosen = [row for row in rows if row['kind'] in kinds]
        token = hashlib.sha256(encoded([instance_id, sorted(kinds), chosen]).encode()).hexdigest()
        return {'counts': counts, 'total': len(chosen), 'locked': sum(row['locked'] for row in chosen), 'token': token}

    def forget_batch(self, instance_id, body):
        with self.database.transaction() as tx:
            preview = self.forget_preview(instance_id, body.kinds)
            if preview['token'] != body.token:
                raise RevisionConflict('选中类型的记忆已变化，请重新确认数量。')
            if not preview['total']:
                raise ValueError('选中类型没有需要遗忘的记忆。')
            rows = self.database.all("SELECT * FROM memories WHERE instance_id=? AND status<>'forgotten' ORDER BY id", (instance_id,))
            for row in rows:
                if row['kind'] in body.kinds:
                    self._forget(tx, row)
            invalidate(tx, instance_id, '管理员批量遗忘了记忆。')
            self.audit(tx, 'memory.batch_forgotten', {'instance_id': instance_id, 'kinds': body.kinds, 'count': preview['total']})
        self.events.publish('information', instance_ids=(instance_id,))
        return {'status': 'forgotten', 'count': preview['total']}

    def commit_candidates(self, tx, run, candidates, *, current_id=None, allow_current=False):
        self.validate_candidates(run['instance_id'], run['input_through'], candidates, allow_current=allow_current)
        outcomes = []
        for candidate_index, candidate in enumerate(candidates):
            old = self.candidate_target(run['instance_id'], candidate)
            forgetting = candidate.operation == 'forget'
            refs = self.evidence.normalize(run['instance_id'], candidate.sources, candidate.source_message_ids,
                through=run['input_through'], allow_current=allow_current, current_id=current_id)
            body = MemoryInput(kind=old['kind'] if forgetting else candidate.kind,
                text=old['text'] if forgetting else candidate.text, status='forgotten' if forgetting else 'active',
                expires_at=old['expires_at'] if forgetting else candidate.expires_at,
                sources=refs, revision=candidate.revision, supersedes=candidate.supersedes,
                **({'valid_from': candidate.valid_from} if candidate.valid_from is not None else {}))
            if self.unchanged_memory(old, body, refs):
                continue
            if self.governance.needs_review(run['instance_id'], candidate, old):
                outcomes.append({'candidate': candidate_index, 'memory_id': old['id'] if old else None,
                    **self.governance.queue(tx, run, candidate, old, refs)})
                continue
            saved = self.write_memory(tx, run['instance_id'], body, candidate.memory_id, run['id'], current_id=current_id if allow_current else None)
            self.governance.supersede(tx, run['instance_id'], body, saved, run['id'])
        if outcomes:
            row = tx.execute('SELECT context_json FROM rp_runs WHERE id=?', (run['id'],)).fetchone()
            report = json.loads(row['context_json']) if row['context_json'] else {}
            report['memory_outcomes'] = outcomes
            tx.execute('UPDATE rp_runs SET context_json=? WHERE id=?', (encoded(report), run['id']))
            overflow = sum(item['status'] == 'queue_full' for item in outcomes)
            if overflow:
                self.audit(tx, 'memory.proposal_overflow', {'instance_id': run['instance_id'], 'run_id': run['id'], 'count': overflow})

    def scope(self, instance_id):
        instance = self.store.instance(instance_id)
        libraries = json.loads(instance['character_config']).get('knowledge_libraries', [])
        placeholders = ','.join('?' for _ in libraries) or 'NULL'
        condition = f'''((m.instance_id=? AND m.revision=c.revision AND {active_memory_sql()}) OR
            (d.revision=c.revision AND d.enabled=1 AND l.enabled=1 AND l.id IN ({placeholders})))'''
        return condition, (instance_id, time(), *libraries)

    def result(self, row, score=None):
        value = {'chunk_id': row['id'], 'memory_id': row['memory_id'], 'document_id': row['document_id'],
                'revision': row['revision'], 'kind': row.get('kind'), 'title': row.get('title') or '私有记忆',
                'library_id': row.get('library_id'), 'text': row['text'], 'start_line': row['start_line'],
                'end_line': row['end_line'], 'start_char': row['start_char'], 'end_char': row['end_char'],
                'expires_at': row.get('expires_at'), 'score': score}
        if row.get('locked'):
            value['locked'] = True
        if row.get('valid_from') is not None:
            value['valid_from'] = row['valid_from']
        return value

    def read(self, instance_id, chunk_id):
        condition, arguments = self.scope(instance_id)
        row = self.database.one('''SELECT c.*,m.kind,m.expires_at,m.locked,m.valid_from,d.title,d.library_id''' + JOIN + ' WHERE c.id=? AND ' + condition, (chunk_id, *arguments))
        if not row:
            raise LookupError('片段已更新、过期、遗忘、停用或不在授权范围。')
        return self.result(row)

    def read_snapshot(self, instance_id, references):
        """一次复核快照范围与版本；不能凭旧快照绕过当前权限、过期或遗忘。"""
        if not references:
            return []
        refs = references[:24]
        condition, arguments = self.scope(instance_id)
        rows = self.database.all('SELECT c.*,m.kind,m.expires_at,m.locked,m.valid_from,d.title,d.library_id' + JOIN + ' WHERE c.id IN ('
            + ','.join('?' for _ in refs) + ') AND ' + condition, (*[item['chunk_id'] for item in refs], *arguments))
        current = {row['id']: row for row in rows}
        return [self.result(current[ref['chunk_id']]) for ref in refs if ref['chunk_id'] in current
                and current[ref['chunk_id']]['revision'] == ref['revision']]

    def search(self, instance_id, query, top_k=6, category='all', *, automatic=False):
        words = list(terms(query, query=True))
        query_truncated = len(words) > 128
        if not words:
            return {'results': [], 'scan_complete': True, 'score_basis': 'BM25; not_probability'}
        if len(words) > 128:
            if not automatic:
                raise ValueError('关键词过多，请缩短查询。')
            words = words[-128:]
        condition, arguments = self.scope(instance_id)
        if category == 'memory':
            condition += ' AND c.memory_id IS NOT NULL'
        elif category == 'knowledge':
            condition += ' AND c.document_id IS NOT NULL'
        count = self.database.one('SELECT count(*) AS n,avg(c.token_count) AS average' + JOIN + ' WHERE ' + condition, arguments)
        if not count['n']:
            return {'results': [], 'scan_complete': True, 'score_basis': 'BM25; not_probability'}
        placeholders = ','.join('?' for _ in words)
        frequencies = self.database.all('SELECT p.term,count(*) AS n FROM information_postings p JOIN information_chunks c ON c.id=p.chunk_id ' + RELATIONS + f' WHERE p.term IN ({placeholders}) AND ' + condition + ' GROUP BY p.term', (*words, *arguments))
        df = {row['term']: row['n'] for row in frequencies}
        rows = self.database.all('SELECT p.chunk_id,p.term,p.frequency,c.token_count FROM information_postings p JOIN information_chunks c ON c.id=p.chunk_id ' + RELATIONS + f' WHERE p.term IN ({placeholders}) AND ' + condition + ' ORDER BY p.term,p.chunk_id LIMIT 100001', (*words, *arguments))
        scores = {}
        for row in rows[:100000]:
            tf = row['frequency']
            idf = math.log1p((count['n'] - df[row['term']] + .5) / (df[row['term']] + .5))
            scores[row['chunk_id']] = scores.get(row['chunk_id'], 0) + idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * row['token_count'] / count['average']))
        selected = heapq.nlargest(top_k, scores, key=lambda key: (scores[key], key))
        # 排名后批量复核权限及当前时间，避免每条结果重复读取实例和解析设定。
        fresh_condition, fresh_arguments = self.scope(instance_id)
        current = self.database.all('SELECT c.*,m.kind,m.expires_at,m.locked,m.valid_from,d.title,d.library_id' + JOIN + ' WHERE c.id IN ('
            + (','.join('?' for _ in selected) or 'NULL') + ') AND ' + fresh_condition, (*selected, *fresh_arguments))
        current = {row['id']: row for row in current}
        results = [self.result(current[key], round(scores[key], 6)) for key in selected if key in current]
        return {'results': results,
                'scan_complete': len(rows) <= 100000, 'query_terms_truncated': query_truncated,
                'scored_chunks': len(scores), 'score_basis': 'BM25; not_probability'}

    def context(self, instance_id, query, policy, retrieved=None):
        rows = self.database.all('''SELECT m.* FROM memories m WHERE instance_id=? AND ''' + active_memory_sql() + '''
            AND kind='profile'
            ORDER BY CASE kind WHEN 'profile' THEN 0 ELSE 1 END,updated_at DESC LIMIT 41''', (instance_id, time()))
        memory, knowledge, trace, used_memory, used_knowledge = [], [], [], 0, 0
        seen = set()
        for row in rows[:40]:
            cost = (len(encoded(row['text']).encode('utf-8')) + 1) // 2 + 100
            fits = used_memory + cost <= policy.memory_budget_tokens
            trace.append({'memory_id': row['id'], 'revision': row['revision'], 'selected': fits, 'reason': 'active' if fits else 'budget'})
            if fits:
                memory.append({'memory_id': row['id'], **{key: row[key] for key in ('kind','text','revision','expires_at')}})
                if row['locked']: memory[-1]['locked'] = True
                if row['valid_from'] is not None: memory[-1]['valid_from'] = row['valid_from']
                seen.add(row['id']); used_memory += cost
        if retrieved is None:
            retrieved = {'results': [], 'scan_complete': True, 'query_terms_truncated': False}
            if query.strip():
                for category, budget in [('memory', policy.memory_budget_tokens), ('knowledge', policy.knowledge_budget_tokens)]:
                    if budget:
                        batch = self.search(instance_id, query, policy.retrieval_top_k, category, automatic=True)
                        retrieved['results'].extend(batch['results'])
                        retrieved['scan_complete'] = retrieved['scan_complete'] and batch['scan_complete']
                        retrieved['query_terms_truncated'] = retrieved['query_terms_truncated'] or batch.get('query_terms_truncated', False)
        for result in retrieved['results']:
            if result['memory_id'] in seen:
                continue
            private = result['memory_id'] is not None
            cost = (len(encoded(result).encode('utf-8')) + 1) // 2 + 16
            fits = (used_memory + cost <= policy.memory_budget_tokens) if private else (used_knowledge + cost <= policy.knowledge_budget_tokens)
            trace.append({'chunk_id': result['chunk_id'], 'selected': fits, 'reason': 'matched' if fits else 'budget'})
            if fits:
                (memory if private else knowledge).append(result)
                if private:
                    seen.add(result['memory_id']); used_memory += cost
                else:
                    used_knowledge += cost
        return {'memory': memory, 'knowledge': knowledge, 'trace': trace, 'memory_tokens_estimate': used_memory,
                'knowledge_tokens_estimate': used_knowledge, 'pinned_limit_reached': len(rows) > 40,
                'keyword_scan_complete': retrieved['scan_complete'], 'query_terms_truncated': retrieved.get('query_terms_truncated', False),
                'retrieval_mode': policy.retrieval_mode, 'retrieval_diagnostics': {key: value for key, value in retrieved.items() if key != 'results'},
                'semantic_not_executed': policy.retrieval_mode != 'keyword' and retrieved.get('mode') not in {'semantic', 'hybrid'}}
