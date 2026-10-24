"""联系人局部角色版本：提案随有效运行入库，采用和撤销均保留新版本。"""
import json
from time import time

from .adjustment_models import AdjustmentProposal
from ..roleplay.store import encoded, invalidate
from ..storage.configuration import RevisionConflict


class Adjustments:
    def __init__(self, store):
        self.store, self.database, self.events = store, store.database, store.events

    def version(self, instance_id, revision):
        if revision == 0:
            return {'revision': 0, 'overrides_json': '{}', 'sources_json': '[]', 'field_sources_json': '{}', 'reason': '原角色模板'}
        row = self.database.one('SELECT * FROM instance_character_versions WHERE instance_id=? AND revision=?', (instance_id, revision))
        if not row:
            raise LookupError('本生活线的局部设定版本不存在。')
        return row

    def sources_valid(self, instance_id, sources):
        if not sources:
            return True
        return self.database.one('''SELECT 1 FROM evidence_status WHERE instance_id=? AND invalid=1 AND
            (kind,source_id) IN (SELECT json_extract(value,'$.kind'),json_extract(value,'$.id') FROM json_each(?)) LIMIT 1''',
                                 (instance_id, encoded(sources))) is None

    def effective(self, instance):
        base = json.loads(instance['character_config'])
        row = self.version(instance['id'], instance['adjustment_revision'])
        if self.sources_valid(instance['id'], json.loads(row['sources_json'])):
            base.update(json.loads(row['overrides_json']))
        return base

    def validate(self, instance_id, through, proposal):
        if proposal is None:
            return
        self.store.information.evidence.normalize(instance_id, proposal.sources, through=through)
        effective = json.loads(self.store.instance(instance_id)['character_config']) | proposal.changes
        if len(encoded(effective).encode('utf-8')) > 256 * 1024:
            raise ValueError('调整后的角色资料超过 256 KiB。')

    def commit(self, tx, run, proposal):
        # 历史运行内的旧字符串保持原样；不迁移成没有依据的可审核提案。
        if proposal is None:
            return
        self.validate(run['instance_id'], run['input_through'], proposal)
        instance = tx.execute('SELECT * FROM role_instances WHERE id=?', (run['instance_id'],)).fetchone()
        payload = encoded(dict(sorted(proposal.changes.items())))
        refs = self.store.information.evidence.normalize(run['instance_id'], proposal.sources, through=run['input_through'])
        sources = encoded([ref.model_dump() for ref in sorted(refs, key=lambda ref: ref.id)])
        duplicate = tx.execute("SELECT 1 FROM character_proposals WHERE instance_id=? AND character_revision=? AND base_revision=? AND changes_json=? AND sources_json=? AND status='pending'",
                               (run['instance_id'], instance['character_revision'], instance['adjustment_revision'], payload, sources)).fetchone()
        if duplicate:
            return
        if tx.execute("SELECT count(*) FROM character_proposals WHERE instance_id=? AND status='pending'", (run['instance_id'],)).fetchone()[0] >= 50:
            raise ValueError('待审核角色提案已达 50 条，请先处理。')
        before = json.loads(self.store.instance(run['instance_id'])['character_config'])
        tx.execute('''INSERT INTO character_proposals(instance_id,run_id,character_revision,base_revision,reason,changes_json,before_json,sources_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)''', (run['instance_id'], run['id'], instance['character_revision'], instance['adjustment_revision'], proposal.reason, payload, encoded({key: before.get(key, '') for key in proposal.changes}), sources, time()))

    def detail(self, instance_id, before=None):
        instance = self.store.instance(instance_id)
        current = self.version(instance_id, instance['adjustment_revision'])
        rows = self.database.all('SELECT * FROM character_proposals WHERE instance_id=? AND id<? ORDER BY id DESC LIMIT 31', (instance_id, before or 9223372036854775807))
        items = []
        for row in rows[:30]:
            sources = json.loads(row.pop('sources_json'))
            row.update(sources=sources, changes=json.loads(row.pop('changes_json')))
            row['stale'] = row['base_revision'] != instance['adjustment_revision'] or row['character_revision'] != instance['character_revision'] or not self.sources_valid(instance_id, sources)
            row['before'] = json.loads(row.pop('before_json'))
            items.append(row)
        return {'revision': instance['adjustment_revision'], 'character_revision': instance['character_revision'],
                'overrides': json.loads(current['overrides_json']), 'sources_valid': self.sources_valid(instance_id, json.loads(current['sources_json'])),
                'items': items, 'next_before': rows[29]['id'] if len(rows) > 30 else None,
                'versions': self.database.all('SELECT revision,reason,proposal_id,created_at FROM instance_character_versions WHERE instance_id=? ORDER BY revision DESC LIMIT 100', (instance_id,))}

    def _write(self, tx, instance, overrides, field_sources, reason, proposal_id=None):
        template = tx.execute('SELECT config_json FROM character_versions WHERE character_id=? AND revision=?', (instance['character_id'], instance['character_revision'])).fetchone()
        if len(encoded(json.loads(template['config_json']) | overrides).encode('utf-8')) > 256 * 1024:
            raise ValueError('当前模板与目标局部设定合并后超过 256 KiB，未更改版本。')
        revision = instance['adjustment_revision'] + 1
        sources = list({(ref['kind'], ref['id']): ref for refs in field_sources.values() for ref in refs}.values())
        tx.execute('INSERT INTO instance_character_versions VALUES (?,?,?,?,?,?,?,?)',
                   (instance['id'], revision, encoded(overrides), proposal_id, reason, encoded(sources), encoded(field_sources), time()))
        tx.execute('UPDATE role_instances SET adjustment_revision=? WHERE id=?', (revision, instance['id']))
        invalidate(tx, instance['id'], '本生活线的局部角色设定已变化。')
        tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)', (time(), 'character.instance_version', encoded({'instance_id': instance['id'], 'revision': revision, 'proposal_id': proposal_id, 'reason': reason})))

    def review(self, instance_id, proposal_id, body):
        with self.database.transaction() as tx:
            instance = self.store.instance(instance_id)
            row = self.database.one('SELECT * FROM character_proposals WHERE instance_id=? AND id=?', (instance_id, proposal_id))
            if not row:
                raise LookupError('本生活线的提案不存在。')
            if row['revision'] != body.revision or row['status'] != 'pending':
                raise RevisionConflict('提案已经处理，请刷新。')
            if body.action == 'adopt':
                if row['base_revision'] != instance['adjustment_revision'] or row['character_revision'] != instance['character_revision']:
                    raise RevisionConflict('角色设定版本已经变化，不能直接采用旧提案。')
                proposal = AdjustmentProposal(reason=row['reason'], changes=json.loads(row['changes_json']), sources=json.loads(row['sources_json']))
                self.validate(instance_id, None, proposal)
                current = self.version(instance_id, instance['adjustment_revision'])
                sources = json.loads(current['sources_json'])
                if not self.sources_valid(instance_id, sources):
                    raise ValueError('当前局部设定的来源已失效，请先撤销到有效版本。')
                field_sources = json.loads(current['field_sources_json'])
                field_sources.update({key: json.loads(row['sources_json']) for key in proposal.changes})
                self._write(tx, instance, json.loads(current['overrides_json']) | proposal.changes, field_sources, proposal.reason, proposal_id)
            tx.execute('UPDATE character_proposals SET status=?,revision=revision+1,reviewed_at=? WHERE id=?', ('adopted' if body.action == 'adopt' else 'rejected', time(), proposal_id))
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)', (time(), 'character.proposal_review', encoded({'instance_id': instance_id, 'proposal_id': proposal_id, 'action': body.action})))
        self.events.publish('characters', instance_ids=(instance_id,))
        return self.detail(instance_id)

    def rollback(self, instance_id, body):
        with self.database.transaction() as tx:
            instance = self.store.instance(instance_id)
            if instance['adjustment_revision'] != body.revision:
                raise RevisionConflict('局部设定已变化，请刷新。')
            if body.target_revision == body.revision:
                raise ValueError('已经在此版本。')
            target = self.version(instance_id, body.target_revision)
            sources = json.loads(target['sources_json'])
            if not self.sources_valid(instance_id, sources):
                raise ValueError('目标版本含失效来源，请选择其他版本或回到原模板。')
            self._write(tx, instance, json.loads(target['overrides_json']), json.loads(target['field_sources_json']), f'撤销到局部版本 {body.target_revision}')
        self.events.publish('characters', instance_ids=(instance_id,))
        return self.detail(instance_id)
