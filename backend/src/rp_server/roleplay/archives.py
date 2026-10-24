"""管理员清空上下文与只读归档；归档事实不失真，私有记忆不随之遗忘。"""
import hashlib
import json
from time import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..storage.configuration import RevisionConflict
from .conversations import CONVERSATION_CTE, conversation_cte
from .store import encoded, invalidate


class ArchiveInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    token: str = Field(pattern=r'^[a-f0-9]{64}$')


class ConversationArchives:
    def __init__(self, store):
        self.store, self.database = store, store.database

    def preview(self, instance_id):
        instance = self.store.instance(instance_id)
        ends = self.database.one('''SELECT
            (SELECT coalesce(max(id),0) FROM inbound_messages WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL) AS input_id,
            (SELECT coalesce(max(id),0) FROM sent_messages WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL) AS sent_id''', (instance_id, instance_id))
        args = (instance_id, ends['input_id'], instance_id, ends['sent_id'])
        counts = self.database.one(CONVERSATION_CTE + '''SELECT coalesce(max(turn),0) AS rounds,
            coalesce(sum(kind='input'),0) AS inputs,coalesce(sum(kind='sent'),0) AS sent,
            min(at) AS from_at,max(at) AS to_at FROM numbered''', args)
        sending = bool(self.database.one("SELECT 1 FROM rp_outbox WHERE instance_id=? AND status='sending' LIMIT 1", (instance_id,)))
        plans = self.database.one("SELECT count(*) AS n FROM rp_outbox WHERE instance_id=? AND status='pending'", (instance_id,))['n']
        deliveries = self.database.one("SELECT count(*) AS n FROM rp_deliveries WHERE instance_id=? AND status IN ('pending','held')", (instance_id,))['n']
        token = hashlib.sha256(encoded([instance_id, instance['input_version'], ends, counts]).encode()).hexdigest()
        return {**counts, **ends, 'token': token, 'sending': sending, 'pending_messages': plans, 'pending_deliveries': deliveries}

    def clear(self, instance_id, body):
        now, archive_id = time(), uuid4().hex
        with self.database.transaction() as tx:
            preview = self.preview(instance_id)
            if preview['token'] != body.token:
                raise RevisionConflict('对话已变化，请重新打开确认窗口。')
            if preview['sending']:
                raise RevisionConflict('消息正在投递，请等回执返回后再清空。')
            if not preview['rounds']:
                raise ValueError('当前没有可归档的对话。')
            instance = self.store.instance(instance_id)
            contact = tx.execute('SELECT display_name,nickname FROM contacts WHERE account_id=? AND peer_id=?',
                (instance['account_id'], instance['peer_id'])).fetchone()
            name = (contact['display_name'] or contact['nickname']) if contact else ''
            tx.execute('INSERT INTO conversation_archives VALUES (?,?,?,?,?,?,?,?,?,?)',
                (archive_id, instance_id, now, name or instance['peer_id'], json.loads(instance['character_config'])['name'],
                 preview['rounds'], preview['inputs'], preview['sent'], preview['from_at'], preview['to_at']))
            for table, anchor in (('inbound_messages', preview['input_id']), ('sent_messages', preview['sent_id'])):
                tx.execute(f'UPDATE {table} SET archived_at=? WHERE instance_id=? AND id<=? AND deleted_at IS NULL AND archived_at IS NULL',
                    (now, instance_id, anchor))
            # 与删除分开标记：来源仍可支撑已提炼记忆，但原文及旧运行资料不再供模型读取。
            tx.execute('UPDATE rp_runs SET context_archived_at=? WHERE instance_id=? AND context_archived_at IS NULL', (now, instance_id))
            invalidate(tx, instance_id, '管理员清空上下文，旧待发计划已取消。')
            tx.execute("UPDATE rp_deliveries SET status='cancelled',revision=revision+1,updated_at=? WHERE instance_id=? AND status IN ('pending','held')", (now, instance_id))
            tx.execute('UPDATE role_instances SET handled_input_id=max(handled_input_id,?),attempted_version=input_version WHERE id=?', (preview['input_id'], instance_id))
            tx.execute("UPDATE context_worksets SET state_json='{}',updated_at=? WHERE instance_id=?", (now, instance_id))
            tx.execute("UPDATE rp_summaries SET status='invalidated',error='对话已归档。' WHERE instance_id=? AND status IN ('active','candidate','superseded')", (instance_id,))
            tx.execute('UPDATE rp_states SET invalidated=1,next_wakeup_at=NULL WHERE instance_id=?', (instance_id,))
            tx.execute('UPDATE rp_scenes SET invalidated=1 WHERE instance_id=?', (instance_id,))
            tx.execute('DELETE FROM worldbook_effects WHERE instance_id=?', (instance_id,))
            tx.execute("UPDATE observations SET status='cancelled',error='对话已归档。' WHERE instance_id=? AND status IN ('pending','fetching')", (instance_id,))
            tx.execute("UPDATE world_events SET status='discarded',revision=revision+1,updated_at=? WHERE instance_id=? AND status='pending'", (now, instance_id))
            tx.execute("UPDATE memory_proposals SET status='rejected',revision=revision+1,reviewed_at=? WHERE instance_id=? AND status='pending'", (now, instance_id))
            tx.execute("UPDATE character_proposals SET status='rejected',revision=revision+1,reviewed_at=? WHERE instance_id=? AND status='pending'", (now, instance_id))
            self.store.plans.sync(tx)
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                (now, 'conversation.archived', encoded({'instance_id': instance_id, 'archive_id': archive_id, 'rounds': preview['rounds']})))
        if self.store.cache:
            self.store.cache.forget_instance(instance_id)
        if self.store.extensions:
            for _, (target, task) in list(self.store.extensions.tasks.items()):
                if target == instance_id:
                    task.cancel()
        for topic in ('messages', 'contacts', 'information', 'context', 'life', 'runtime'):
            self.store.events.publish(topic, instance_ids=(instance_id,))
        return {'status': 'archived', 'archive_id': archive_id, 'rounds': preview['rounds']}

    def list(self, page=1, instance_id=None, query=''):
        if instance_id:
            self.store.instance(instance_id)
        scope = ' WHERE (? IS NULL OR instance_id=?) AND (contact_name LIKE ? OR character_name LIKE ?)'
        args = (instance_id, instance_id, '%' + query.strip() + '%', '%' + query.strip() + '%')
        total = self.database.one('SELECT count(*) AS n FROM conversation_archives' + scope, args)['n']
        page = min(page, max(1, (total + 19) // 20))
        rows = self.database.all('SELECT * FROM conversation_archives' + scope +
            ' ORDER BY created_at DESC,id DESC LIMIT 20 OFFSET ?', (*args, (page - 1) * 20))
        return {'items': rows, 'total': total, 'page': page, 'page_size': 20}

    def read(self, archive_id, page, page_size):
        archive = self.database.one('SELECT * FROM conversation_archives WHERE id=?', (archive_id,))
        if not archive:
            raise LookupError('归档不存在。')
        ceiling, at, instance_id = 9223372036854775807, archive['created_at'], archive['instance_id']
        # 单独的管理入口才使用归档范围；所有模型读取路径继续使用未归档范围。
        from .inputs import InputService
        result = InputService(self.database, self.store.events).conversation_page(instance_id, conversation_cte(archived=True),
            (instance_id, ceiling, at, instance_id, ceiling, at), page, page_size, ceiling, ceiling)
        return {**result, 'archive': archive}
