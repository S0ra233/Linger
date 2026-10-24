"""人工保护和明确替代关系；语义由模型／管理员判断，不另调模型推断冲突。"""
import hashlib
import json
from time import time
from uuid import uuid4

from .models import MemoryInput
from ..roleplay.store import encoded, invalidate
from ..storage.configuration import RevisionConflict


class MemoryGovernance:
    def __init__(self, information):
        self.info, self.database = information, information.database

    def validate(self, instance_id, body, target=None):
        if body.valid_from is not None and body.valid_from > time():
            raise ValueError('事实生效时间不能在未来；未来安排请使用意图。')
        if target and target['superseded_by']:
            raise ValueError('这条记忆已被替代，请读取当前新事实；历史不会自动恢复。')
        if body.supersedes and target and target['status'] != 'active':
            raise ValueError('已遗忘的条目不能作为替代旧事实的新记忆。')
        rows = []
        for ref in body.supersedes:
            old = self.info.memory(instance_id, ref.memory_id)
            if old['revision'] != ref.revision:
                raise RevisionConflict('被替代的记忆已变化，请重新读取。')
            if target and old['id'] == target['id']:
                raise ValueError('新事实不能替代自己；修改原文请直接编辑。')
            if old['status'] != 'active' or old['superseded_by']:
                raise ValueError('旧记忆已遗忘或被替代，不能再次替代。')
            if old['valid_from'] is not None and (body.valid_from if body.valid_from is not None else time()) < old['valid_from']:
                raise ValueError('新事实生效时间早于被替代事实，请核对时间与关系。')
            rows.append(old)
        return rows

    def needs_review(self, instance_id, candidate, target):
        return bool(target and target['locked'] or any(row['locked'] for row in self.validate(instance_id, candidate, target)))

    def supersede(self, tx, instance_id, body, target, run_id=None):
        for old in self.validate(instance_id, body, target):
            if run_id and old['locked']:
                raise ValueError('受保护的旧事实只能在管理员审核后替代。')
            when, now, revision = body.valid_from if body.valid_from is not None else time(), time(), old['revision'] + 1
            tx.execute('UPDATE memories SET superseded_by=?,superseded_at=?,revision=?,updated_at=? WHERE id=?',
                       (target['id'], when, revision, now, old['id']))
            snapshot = {**old, 'superseded_by': target['id'], 'superseded_at': when, 'revision': revision}
            tx.execute('INSERT INTO memory_versions VALUES (?,?,?,?,?)', (old['id'], revision, encoded(snapshot), run_id, now))
            self.info.audit(tx, 'memory.superseded', {'instance_id': instance_id, 'id': old['id'], 'by': target['id'], 'at': when, 'run_id': run_id})

    def protect(self, instance_id, memory_id, body):
        with self.database.transaction() as tx:
            old = self.info.memory(instance_id, memory_id)
            if (old['revision'], old['protection_revision']) != (body.revision, body.protection_revision):
                raise RevisionConflict('记忆或锁定状态已变化，请刷新。')
            if body.promote_to_profile and not body.locked:
                raise ValueError('转为长期画像需同时人工确认并锁定。')
            if body.locked:
                self.validate(instance_id, MemoryInput(kind=old['kind'], text=old['text'], expires_at=old['expires_at'],
                    status=old['status'], revision=old['revision']), old)
                if old['status'] != 'active' or not self.info.valid_memory(instance_id, memory_id):
                    raise ValueError('遗忘或来源失效的记忆不能通过锁定恢复，请先核对来源。')
                if old['expires_at'] is not None and old['expires_at'] <= time() and not body.promote_to_profile:
                    raise ValueError('记忆已过期；锁定不会取消期限，可明确转为长期画像。')
            if body.promote_to_profile and old['kind'] != 'profile':
                self.info.write_memory(tx, instance_id, MemoryInput(kind='profile', text=old['text'],
                    revision=old['revision'], valid_from=old['valid_from']), memory_id)
            if old['locked'] == int(body.locked) and not body.promote_to_profile:
                return self.info.memory(instance_id, memory_id)
            now = time()
            tx.execute('UPDATE memories SET locked=?,protection_revision=protection_revision+1,confirmed_at=? WHERE id=?',
                       (int(body.locked), now if body.locked else old['confirmed_at'], memory_id))
            invalidate(tx, instance_id, '管理员确认或解锁了记忆。')
            self.info.audit(tx, 'memory.protection', {'instance_id': instance_id, 'id': memory_id, **body.model_dump()})
        self.info.events.publish('information', instance_ids=(instance_id,))
        return self.info.memory(instance_id, memory_id)

    def queue(self, tx, run, candidate, target, refs):
        data = candidate.model_dump()
        data.update(sources=[ref.model_dump() for ref in refs], source_message_ids=[])
        if target:
            data.update(memory_id=target['id'], revision=target['revision'])
        payload = encoded(data)
        digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
        existing = tx.execute("SELECT id FROM memory_proposals WHERE instance_id=? AND digest=? AND status='pending'", (run['instance_id'], digest)).fetchone()
        if existing:
            return {'status': 'existing_proposal', 'proposal_id': existing['id']}
        if tx.execute("SELECT count(*) FROM memory_proposals WHERE instance_id=? AND status='pending'", (run['instance_id'],)).fetchone()[0] >= 200:
            # 完整候选仍在本次decision_json中；保护不解除，不因审核积压阻断聊天投递。
            return {'status': 'queue_full', 'proposal_id': None}
        identifier = uuid4().hex
        tx.execute('''INSERT INTO memory_proposals(id,instance_id,memory_id,proposal_json,digest,status,run_id,created_at)
            VALUES (?,?,?,?,?,'pending',?,?)''', (identifier, run['instance_id'], target['id'] if target else None,
                payload, digest, run['id'], time()))
        return {'status': 'queued', 'proposal_id': identifier}

    def proposal(self, instance_id, proposal_id):
        row = self.database.one('SELECT * FROM memory_proposals WHERE instance_id=? AND id=?', (instance_id, proposal_id))
        if not row:
            raise LookupError('记忆提案不存在。')
        return row

    def check_proposal(self, row):
        from ..roleplay.decisions import MemoryCandidate
        run = self.database.one('SELECT * FROM rp_runs WHERE id=? AND instance_id=?', (row['run_id'], row['instance_id']))
        if not run or run['status'] != 'completed' or run['context_deleted_at'] is not None:
            raise ValueError('提案的来源运行已失效。')
        candidate = MemoryCandidate.model_validate_json(row['proposal_json'])
        if candidate.memory_id is None and self.info.candidate_target(row['instance_id'], candidate) is not None:
            raise RevisionConflict('原本拟新建的事实已由其他操作建立，请查看当前记忆后重新提出，不能覆盖其新版本。')
        self.info.validate_candidates(row['instance_id'], run['input_through'], [candidate])
        return candidate

    def proposals(self, instance_id, before=None):
        self.info.store.instance(instance_id)
        boundary = self.proposal(instance_id, before)['created_at'] if before else None
        rows = self.database.all('''SELECT * FROM memory_proposals WHERE instance_id=?
            AND (? IS NULL OR created_at<? OR (created_at=? AND id<?))
            ORDER BY created_at DESC,id DESC LIMIT 31''', (instance_id, boundary, boundary, boundary, before))
        keys_by_id = {}
        for row in rows[:30]:
            data = json.loads(row['proposal_json'])
            keys = {ref['memory_id'] for ref in data.get('supersedes', [])}
            if row['memory_id']: keys.add(row['memory_id'])
            keys_by_id[row['id']] = sorted(keys)
        keys = sorted({key for values in keys_by_id.values() for key in values})
        # 只复用本次页面投影；审核入口仍在独立事务中重新读取所有目标与来源。
        targets = {item['id']: item for item in self.database.all('SELECT * FROM memories WHERE instance_id=? AND id IN ('
            + ','.join('?' for _ in keys) + ')', (instance_id, *keys))} if keys else {}
        for row in rows[:30]:
            row['targets'] = [targets[key] for key in keys_by_id[row['id']] if key in targets]
            row['effective_status'] = row['status']
            if row['status'] == 'pending':
                try:
                    self.check_proposal(row)
                except (ValueError, LookupError, RevisionConflict) as exc:
                    row.update(effective_status='stale', stale_reason=str(exc))
        pending = self.database.one("SELECT count(*) AS n FROM memory_proposals WHERE instance_id=? AND status='pending'", (instance_id,))['n']
        return {'items': rows[:30], 'next_before': rows[29]['id'] if len(rows) > 30 else None,
                'pending_count': pending, 'pending_limit': 200}

    def review(self, instance_id, proposal_id, body):
        with self.database.transaction() as tx:
            row = self.proposal(instance_id, proposal_id)
            if row['revision'] != body.revision or row['status'] != 'pending':
                raise RevisionConflict('提案已处理，请刷新。')
            if body.action == 'approve':
                try:
                    candidate = self.check_proposal(row)
                except (ValueError, LookupError, RevisionConflict) as exc:
                    raise RevisionConflict('提案已过期或依据变化，未采用：' + str(exc)) from exc
                target = self.info.candidate_target(instance_id, candidate)
                data = MemoryInput(kind=target['kind'] if candidate.operation == 'forget' else candidate.kind,
                    text=target['text'] if candidate.operation == 'forget' else candidate.text,
                    expires_at=target['expires_at'] if candidate.operation == 'forget' else candidate.expires_at,
                    status='forgotten' if candidate.operation == 'forget' else 'active', revision=candidate.revision,
                    sources=candidate.sources, supersedes=candidate.supersedes,
                    **({'valid_from': candidate.valid_from} if candidate.valid_from is not None else {}))
                new = self.info.write_memory(tx, instance_id, data, candidate.memory_id)
                self.supersede(tx, instance_id, data, new)
                invalidate(tx, instance_id, '管理员采用了受保护记忆的修改提案。')
            tx.execute('UPDATE memory_proposals SET status=?,revision=revision+1,reviewed_at=? WHERE id=?',
                       ('applied' if body.action == 'approve' else 'rejected', time(), proposal_id))
            self.info.audit(tx, 'memory.proposal_reviewed', {'instance_id': instance_id, 'id': proposal_id, 'action': body.action})
        self.info.events.publish('information', instance_ids=(instance_id,))
        return self.proposal(instance_id, proposal_id)
