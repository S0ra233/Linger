"""完整轮次的RP侧删除；保留平台事实和诊断，不再将被删内容提供给模型。"""
import hashlib
import json
from time import time

from pydantic import BaseModel, ConfigDict, Field

from ..storage.configuration import RevisionConflict
from .store import encoded, invalidate


# 列表、删除和来源窗口共用轮次边界；额外SQL只允许由服务端固定拼接。
def conversation_cte(sent_scope='', *, archived=False):
    visibility = 'archived_at=?' if archived else 'deleted_at IS NULL AND archived_at IS NULL'
    return '''WITH events AS (
    SELECT id,'input' AS kind,received_at AS at FROM inbound_messages WHERE instance_id=? AND id<=? AND ''' + visibility + '''
    UNION ALL SELECT id,'sent',attempted_at FROM sent_messages WHERE instance_id=? AND id<=? AND ''' + visibility + ' ' + sent_scope + '''
), edges AS (
    SELECT *,CASE WHEN row_number() OVER (ORDER BY at,kind,id)=1
        OR (kind='input' AND lag(kind) OVER (ORDER BY at,kind,id)='sent') THEN 1 ELSE 0 END AS start
    FROM events
), numbered AS (
    SELECT *,sum(start) OVER (ORDER BY at,kind,id ROWS UNBOUNDED PRECEDING) AS turn FROM edges
) '''


CONVERSATION_CTE = conversation_cte()


class ConversationDelete(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    rounds: int = Field(ge=1, le=1000000)
    token: str = Field(pattern=r'^[a-f0-9]{64}$')


class ConversationEdits:
    def __init__(self, store):
        self.store, self.database, self.events = store, store.database, store.events

    def selection(self, instance_id, rounds):
        instance = self.store.instance(instance_id)
        anchors = self.database.one('''SELECT
            (SELECT coalesce(max(id),0) FROM inbound_messages WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL) AS input_id,
            (SELECT coalesce(max(id),0) FROM sent_messages WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL) AS sent_id''', (instance_id, instance_id))
        args = (instance_id, anchors['input_id'], instance_id, anchors['sent_id'])
        total = self.database.one(CONVERSATION_CTE + 'SELECT coalesce(max(turn),0) AS n FROM numbered', args)['n']
        if not 1 <= rounds <= total:
            raise ValueError(f'当前有{total}轮有效对话，请重新选择删除轮数。')
        selected = self.database.all(CONVERSATION_CTE + 'SELECT * FROM numbered WHERE turn>? ORDER BY at,kind,id', (*args, total - rounds))
        token = hashlib.sha256(encoded([instance_id, instance['input_version'], instance['handled_input_id'], anchors, total,
            [(row['kind'], row['id'], row['at']) for row in selected]]).encode('utf-8')).hexdigest()
        sending = self.database.one("SELECT 1 FROM rp_outbox WHERE instance_id=? AND status='sending' LIMIT 1", (instance_id,))
        preview = {'token': token, 'rounds': rounds, 'total': total, 'remaining': total - rounds,
            'inputs': sum(row['kind'] == 'input' for row in selected), 'sent': sum(row['kind'] == 'sent' for row in selected),
            'from_at': selected[0]['at'], 'to_at': selected[-1]['at'], 'sending': bool(sending)}
        return preview, selected

    def preview(self, instance_id, rounds):
        return self.selection(instance_id, rounds)[0]

    def remove(self, instance_id, body):
        now = time()
        with self.database.transaction() as tx:
            preview, rows = self.selection(instance_id, body.rounds)
            if preview['token'] != body.token:
                raise RevisionConflict('对话或运行状态已变化，请重新预览删除范围。')
            if preview['sending']:
                raise RevisionConflict('有消息正在投递，请等待回执后重新预览；本次未删除。')
            inputs = [row['id'] for row in rows if row['kind'] == 'input']
            sent = [row['id'] for row in rows if row['kind'] == 'sent']
            boundary = preview['from_at']
            # 以真实输入边界和选中台词的来源运行共同判定派生资料，含延迟投递。
            tx.execute('''UPDATE rp_runs SET context_deleted_at=? WHERE instance_id=? AND context_deleted_at IS NULL AND context_archived_at IS NULL
                AND (created_at>=? OR (? IS NOT NULL AND input_through>=?) OR id IN
                    (SELECT o.run_id FROM sent_messages s JOIN rp_outbox o ON o.id=s.outbox_id
                     WHERE s.instance_id=? AND s.id IN (SELECT value FROM json_each(?))))''',
                (now, instance_id, boundary, min(inputs) if inputs else None, min(inputs) if inputs else None, instance_id, encoded(sent)))
            for kind, identifiers, table in (('input', inputs, 'inbound_messages'), ('sent', sent, 'sent_messages')):
                tx.execute(f'UPDATE {table} SET deleted_at=? WHERE instance_id=? AND id IN (SELECT value FROM json_each(?))',
                    (now, instance_id, encoded(identifiers)))
                tx.executemany('''INSERT INTO evidence_status VALUES (?,?,?,1,?,1,?)
                    ON CONFLICT(instance_id,kind,source_id) DO UPDATE SET invalid=1,reason=excluded.reason,
                    revision=evidence_status.revision+1,updated_at=excluded.updated_at''',
                    ((instance_id, kind, identifier, '管理员删除了此轮对话。', now) for identifier in identifiers))
            self.invalidate_derived(tx, instance_id, now)
            self.restore_workset(tx, instance_id, boundary, now)
            # 旧请求不可提交；只取消尚未交给网关的气泡，未知回执不重发。
            invalidate(tx, instance_id, '管理员删除最近对话，旧计划已取消。')
            tx.execute("UPDATE rp_deliveries SET status='cancelled',revision=revision+1,updated_at=? WHERE instance_id=? AND status IN ('pending','held')", (time(), instance_id))
            tx.execute('UPDATE role_instances SET attempted_version=input_version WHERE id=?', (instance_id,))
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                (now, 'conversation.deleted', encoded({'instance_id': instance_id, 'rounds': body.rounds,
                    'input_ids': inputs, 'sent_ids': sent, 'remaining': preview['remaining']})))
        if self.store.cache:
            self.store.cache.forget_instance(instance_id)
        if self.store.extensions:
            for _, (target, task) in list(self.store.extensions.tasks.items()):
                if target == instance_id:
                    task.cancel()
        for topic in ('messages', 'contacts', 'information', 'context', 'life', 'runtime'):
            self.events.publish(topic, instance_ids=(instance_id,))
        return {'status': 'deleted', 'rounds': body.rounds, 'remaining': preview['remaining']}

    def invalidate_derived(self, tx, instance_id, now):
        retired = 'SELECT id FROM rp_runs WHERE instance_id=? AND context_deleted_at IS NOT NULL'
        # 生活、事件与观察也可能成为记忆的证据，须一起关闭模型读取路径。
        for kind, table, condition in (
            ('life', 'life_events', "kind='decision' AND run_id IN (" + retired + ')'),
            ('world_event', 'world_events', 'adopted_run_id IN (' + retired + ') OR source_run_id IN (' + retired + ')'),
            ('observation', 'observations', 'source_run_id IN (' + retired + ')'),
        ):
            args = (now, instance_id, instance_id, instance_id) if kind == 'world_event' else (now, instance_id, instance_id)
            tx.execute(f'''INSERT INTO evidence_status(instance_id,kind,source_id,invalid,reason,revision,updated_at)
                SELECT instance_id,'{kind}',id,1,'对话删除后派生资料失效。',1,? FROM {table} WHERE instance_id=? AND ({condition})
                ON CONFLICT(instance_id,kind,source_id) DO UPDATE SET invalid=1,reason=excluded.reason,
                revision=evidence_status.revision+1,updated_at=excluded.updated_at WHERE evidence_status.invalid=0''', args)
        affected = tx.execute('''SELECT min(created_at) AS at FROM rp_runs WHERE instance_id=?
            AND context_deleted_at=? AND status='completed' ''', (instance_id, now)).fetchone()['at']
        if affected is not None:
            # 当前累计状态没有逐字段回滚依据，不能假装精确恢复；留存记录但停止采用。
            tx.execute('UPDATE rp_states SET invalidated=1 WHERE instance_id=? AND updated_at>=?', (instance_id, affected))
        tx.execute('UPDATE rp_scenes SET invalidated=1 WHERE instance_id=? AND source_run_id IN (' + retired + ')', (instance_id, instance_id))
        tx.execute("UPDATE rp_summaries SET status='invalidated',error='包含已删除对话，停止采用。' WHERE instance_id=? AND run_id IN (" + retired + ") AND status IN ('active','candidate','superseded')", (instance_id, instance_id))
        tx.execute("UPDATE observations SET status='cancelled',error='相关对话已删除。' WHERE instance_id=? AND source_run_id IN (" + retired + ") AND status IN ('pending','fetching')", (instance_id, instance_id))
        tx.execute('DELETE FROM worldbook_effects WHERE instance_id=?', (instance_id,))
        for row in self.database.all("SELECT id,source_run_id,adopted_run_id,config_json FROM world_events WHERE instance_id=? AND status='pending'", (instance_id,)):
            refs = json.loads(row['config_json'])['sources']
            if self.retired(row['source_run_id']) or any(not self.store.information.evidence.is_valid(instance_id, ref['kind'], ref['id']) for ref in refs):
                tx.execute("UPDATE world_events SET status='discarded',revision=revision+1,updated_at=? WHERE id=?", (now, row['id']))
        for row in self.database.all("SELECT id,source_run_id,sources_json FROM life_intents WHERE instance_id=? AND status IN ('pending','running')", (instance_id,)):
            refs = json.loads(row['sources_json'])
            if self.retired(row['source_run_id']) or any(not self.store.information.evidence.is_valid(instance_id, ref['kind'], ref['id']) for ref in refs):
                tx.execute("UPDATE life_intents SET status='cancelled',revision=revision+1,resolution='source_invalid',error='相关对话已删除。',outcome_reason='相关对话已删除。',updated_at=? WHERE id=?", (now, row['id']))
        tx.execute("UPDATE character_proposals SET status='rejected',revision=revision+1,reviewed_at=? WHERE instance_id=? AND status='pending' AND run_id IN (" + retired + ')', (now, instance_id, instance_id))
        self.restore_schedule(tx, instance_id, now)

    def retired(self, run_id):
        return bool(run_id and self.database.one('SELECT 1 FROM rp_runs WHERE id=? AND context_deleted_at IS NOT NULL', (run_id,)))

    def restore_schedule(self, tx, instance_id, now):
        current = self.database.one('''SELECT s.*,v.run_id FROM rp_schedules s JOIN schedule_versions v
            ON v.instance_id=s.instance_id AND v.revision=s.revision WHERE s.instance_id=?''', (instance_id,))
        if not current or not self.retired(current['run_id']):
            return
        previous = self.database.one('''SELECT v.config_json FROM schedule_versions v LEFT JOIN rp_runs r ON r.id=v.run_id
            WHERE v.instance_id=? AND v.revision<? AND (v.run_id IS NULL OR r.context_deleted_at IS NULL AND r.context_archived_at IS NULL)
            ORDER BY v.revision DESC LIMIT 1''', (instance_id, current['revision']))
        entries = json.loads(previous['config_json'])['entries'] if previous else []
        entries = [item for item in entries if all(self.store.information.evidence.is_valid(instance_id, ref['kind'], ref['id']) for ref in item['sources'])]
        identifiers = {item['id'] for item in entries}
        entries = [item for item in entries if item['kind'] != 'exception' or item['replaces_id'] in identifiers]
        revision = current['revision'] + 1
        tx.execute("UPDATE life_intents SET status='cancelled',revision=revision+1,error='相关日程版本随对话删除停用。',updated_at=? WHERE id=? AND status IN ('pending','running')", (now, current['intent_id']))
        tx.execute('UPDATE rp_schedules SET config_json=?,revision=?,intent_id=NULL,updated_at=? WHERE instance_id=?', (encoded(entries), revision, now, instance_id))
        tx.execute('INSERT INTO schedule_versions VALUES (?,?,?,?,?)',
            (instance_id, revision, encoded({'entries': entries, 'change_sources': []}), None, now))

    def restore_workset(self, tx, instance_id, boundary, now):
        checkpoint = self.database.one('''SELECT c.state_json FROM context_candidates c JOIN rp_runs r ON r.id=c.run_id
            WHERE c.instance_id=? AND r.status='completed' AND r.context_deleted_at IS NULL AND r.context_archived_at IS NULL AND r.created_at<?
            ORDER BY r.id DESC LIMIT 1''', (instance_id, boundary))
        state = json.loads(checkpoint['state_json']) if checkpoint else {}
        summary_id = state.get('summary_id')
        if summary_id:
            # 旧版人工停用也写superseded；读取原审计避免无需迁移地复活这些摘要。
            summary = self.database.one('''SELECT 1 FROM rp_summaries s WHERE s.instance_id=? AND s.id=?
                AND s.status IN ('active','superseded') AND NOT EXISTS (
                    SELECT 1 FROM audit_events a WHERE a.action='context.summary_cleared'
                    AND json_extract(a.details_json,'$.instance_id')=s.instance_id AND a.created_at>=s.created_at)''',
                (instance_id, summary_id))
            if summary:
                tx.execute("UPDATE rp_summaries SET status='superseded' WHERE instance_id=? AND status='active' AND id<>?", (instance_id, summary_id))
                tx.execute("UPDATE rp_summaries SET status='active' WHERE instance_id=? AND id=?", (instance_id, summary_id))
            else:
                state['summary_id'] = None
        # 快照重新验证失效来源；不恢复数据库里的旧配置或覆盖用户保留轮数。
        state['state_snapshot'] = None
        tx.execute('''INSERT INTO context_worksets(instance_id,state_json,updated_at) VALUES (?,?,?)
            ON CONFLICT(instance_id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at''',
            (instance_id, encoded(state), now))
