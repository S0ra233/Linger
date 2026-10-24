"""主动投递开关、限制和生活记录；不再创建定时模型评估。"""
from datetime import datetime
import json
from time import time
from zoneinfo import ZoneInfo

from .life_models import LifeSettings
from .store import encoded
from ..storage.configuration import RevisionConflict


class LifeService:
    def __init__(self, store, gateway):
        self.store, self.database, self.events, self.gateway = store, store.database, store.events, gateway

    def settings(self):
        row = self.database.one("SELECT * FROM configurations WHERE key='life'")
        return {**LifeSettings.model_validate({key: value for key, value in (json.loads(row['value_json']) if row else {}).items() if key in LifeSettings.model_fields}).model_dump(),
                'revision': row['revision'] if row else 0}

    def policy(self, instance_id):
        self.store.instance(instance_id)
        row = self.database.one('SELECT * FROM life_policies WHERE instance_id=?', (instance_id,))
        return {'enabled': bool(row['enabled']) if row else False, 'revision': row['revision'] if row else 0}

    @staticmethod
    def event(tx, instance_id, kind, data, run_id=None):
        return tx.execute('INSERT INTO life_events(instance_id,run_id,kind,data_json,created_at) VALUES (?,?,?,?,?)',
                          (instance_id, run_id, kind, encoded(data), time())).lastrowid

    def cancel_background(self, tx, reason, instance_id=None):
        scope, args = (' AND instance_id=?', (instance_id,)) if instance_id else ('', ())
        tx.execute("UPDATE rp_deliveries SET status='held',revision=revision+1,updated_at=? WHERE status='pending'" + scope, (time(), *args))
        tx.execute("UPDATE rp_deliveries SET resume_input_version=NULL WHERE status='held' AND resume_input_version IS NOT NULL" + scope, args)
        tx.execute("UPDATE rp_outbox SET status='cancelled',finished_at=?,error=? WHERE status='pending' AND plan_kind<>'reply'" + scope, (time(), reason, *args))

    def save_settings(self, body):
        data = body.model_dump(exclude={'revision'})
        with self.database.transaction() as tx:
            old = self.settings()
            if old['revision'] != body.revision:
                raise RevisionConflict('生活设置已变化，请重新载入。')
            if {key: value for key, value in old.items() if key != 'revision'} == data:
                return old
            tx.execute("INSERT INTO configurations VALUES ('life',?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,revision=excluded.revision,updated_at=excluded.updated_at",
                       (encoded(data), body.revision + 1, time()))
            self.cancel_background(tx, '生活设置已变化，旧主动消息已取消。')
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (time(), 'life.settings_saved', encoded({'revision': body.revision + 1, 'enabled': body.enabled})))
        self.events.publish('life')
        return self.settings()

    def save_policy(self, instance_id, body):
        with self.database.transaction() as tx:
            old = self.policy(instance_id)
            if old['revision'] != body.revision:
                raise RevisionConflict('此生活线的后台开关已变化，请重新载入。')
            if old['enabled'] == body.enabled:
                return old
            tx.execute('INSERT INTO life_policies VALUES (?,?,?,?) ON CONFLICT(instance_id) DO UPDATE SET enabled=excluded.enabled,revision=excluded.revision,updated_at=excluded.updated_at',
                       (instance_id, int(body.enabled), body.revision + 1, time()))
            self.cancel_background(tx, '此生活线的后台开关已变化，旧主动消息已取消。', instance_id)
            self.event(tx, instance_id, 'policy', {'enabled': body.enabled})
        self.events.publish('life')
        return self.policy(instance_id)

    @staticmethod
    def quiet(at, config):
        if not config['quiet_enabled']:
            return False
        local = datetime.fromtimestamp(at, ZoneInfo(config['timezone'])).strftime('%H:%M')
        start, end = config['quiet_start'], config['quiet_end']
        return start <= local < end if start < end else local >= start or local < end

    def counts(self, instance_id, exclude_run=None):
        last = self.database.one('SELECT received_at FROM inbound_messages WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL ORDER BY id DESC LIMIT 1', (instance_id,))
        # 一组短期分支计一次联系，独立长期投递分别计数；未知也占用未回复额度。
        key = "CASE WHEN plan_kind='delivery' THEN plan_id ELSE 'run:'||run_id END"
        unanswered = self.database.one("SELECT count(DISTINCT " + key + ") AS n FROM rp_outbox WHERE instance_id=? AND plan_kind<>'reply' AND started_at>? AND status IN ('sending','succeeded','unknown')", (instance_id, last['received_at'] if last else 0))['n']
        budget = self.database.one("SELECT count(DISTINCT " + key + ") AS total,count(DISTINCT CASE WHEN instance_id=? THEN " + key + " END) AS own FROM rp_outbox WHERE plan_kind<>'reply' AND started_at>?", (instance_id, time()-86400))
        return last, unanswered, budget, None

    def context(self, instance, through=None, exclude_run=None):
        config, policy = self.settings(), self.policy(instance['id'])
        _, unanswered, budget, _ = self.counts(instance['id'])
        reasons = []
        if not config['enabled'] or not policy['enabled'] or not self.store.active(instance): reasons.append('主动投递未开启或联系人已暂停')
        if self.quiet(time(), config): reasons.append('当前处于安静时段')
        if unanswered >= config['max_unanswered']: reasons.append('连续未回复联系已达上限')
        if budget['total'] >= config['global_runs'] or budget['own'] >= config['instance_runs']: reasons.append('今日主动投递额度已用完')
        return {'settings': config, 'policy': policy, 'background_contact_allowed': not reasons,
                'contact_blocked': reasons, 'unanswered_contacts': unanswered,
                'runs_24h': {'global': budget['total'], 'instance': budget['own']}, 'intents': []}

    def read_history(self, instance_id, before=None, limit=3):
        self.store.instance(instance_id)
        rows = self.database.all("""SELECT e.* FROM life_events e WHERE instance_id=? AND kind='decision' AND id<?
            AND EXISTS(SELECT 1 FROM rp_runs r WHERE r.id=e.run_id AND r.status='completed'
                AND r.context_deleted_at IS NULL AND r.context_archived_at IS NULL)
            AND NOT EXISTS (SELECT 1 FROM evidence_status es WHERE es.instance_id=e.instance_id AND es.kind='life' AND es.source_id=e.id AND es.invalid=1)
            ORDER BY id DESC LIMIT ?""", (instance_id, before or 9223372036854775807, limit + 1))
        items = []
        for row in rows[:limit]:
            data = json.loads(row['data_json'])
            text = data['life_fragment']
            items.append({'event_id': row['id'], 'run_id': row['run_id'], 'at': row['created_at'],
                'trigger_kind': data['trigger_kind'], 'life_fragment': text[:1000], 'truncated': len(text) > 1000})
        return {'items': items, 'next_before': rows[limit - 1]['id'] if len(rows) > limit else None}

    def run_context(self, run_id, instance, through):
        # 完整限制留在运行快照，不再创建到期模型评估。
        saved = {'trigger_kind': 'input', 'context': self.context(instance, through, run_id)}
        with self.database.transaction() as tx:
            tx.execute('UPDATE rp_runs SET life_json=? WHERE id=?', (encoded(saved), run_id))
        return saved

    def valid_run(self, run_id):
        run = self.database.one('SELECT trigger_kind,status FROM rp_runs WHERE id=?', (run_id,))
        return bool(run and run['status'] == 'running' and run['trigger_kind'] == 'input')

    @staticmethod
    def intent_view(row):
        value = dict(row)
        value['sources'] = json.loads(value.pop('sources_json'))
        raw = value.pop('commitment_json')
        value['commitment_source'] = json.loads(raw) if raw else None
        value['legacy'] = not bool(value.pop('fingerprint'))
        return value

    def validate(self, run, decision, runtime):
        self.store.plans.validate(run['instance_id'], decision, runtime)

    def commit(self, tx, run, decision, runtime):
        return self.event(tx, run['instance_id'], 'decision', {
            'trigger_kind': run['trigger_kind'], 'life_fragment': decision.life_fragment,
            'state_changes': decision.state_changes,
            'scene': decision.scene.model_dump() if decision.scene else None,
            'reply_mode': decision.reply_mode, 'planned_bubbles': len(decision.messages),
            'followups': len(decision.followups), 'deliveries': len(decision.deliveries)}, run['id'])

    def finish(self, tx, run_id, status, error):
        rows = tx.execute("SELECT * FROM life_intents WHERE run_id=? AND status='running'", (run_id,)).fetchall()
        if rows:
            target = 'cancelled' if status == 'cancelled' else 'interrupted' if status == 'unknown' else 'failed'
            for row in rows:
                expired = row['deadline_at'] is not None and row['deadline_at'] <= time()
                tx.execute("UPDATE life_intents SET status=?,resolution=?,outcome_reason=?,revision=revision+1,error=?,updated_at=? WHERE id=? AND status='running'",
                    ('expired' if expired else target, 'expired' if expired else target, '最终联系期限已过。' if expired else error, error, time(), row['id']))
            self.event(tx, rows[0]['instance_id'], 'wake_finished', {'status': target, 'error': error, 'intent_ids': [row['id'] for row in rows]}, run_id)

    def recover(self, tx):
        for row in tx.execute("SELECT DISTINCT run_id FROM life_intents WHERE status='running'").fetchall():
            self.finish(tx, row['run_id'], 'unknown', '服务中断；未自动重新唤醒。')

    def allow_delivery(self, row):
        if row['plan_kind'] == 'reply': return True
        instance, config = self.store.instance(row['instance_id']), self.settings()
        if not config['max_unanswered'] or not config['global_runs'] or not config['instance_runs']: return False
        if not config['enabled'] or not self.policy(row['instance_id'])['enabled'] or not self.store.active(instance) or self.quiet(time(), config): return False
        _, unanswered, budget, _ = self.counts(row['instance_id'])
        own = bool(self.database.one("SELECT 1 FROM rp_outbox WHERE run_id=? AND plan_kind='followup' AND status='succeeded' AND started_at>coalesce((SELECT max(received_at) FROM inbound_messages WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL),0) LIMIT 1", (row['run_id'], row['instance_id']))) if row['plan_kind'] == 'followup' else False
        own_budget = own and bool(self.database.one("SELECT 1 FROM rp_outbox WHERE run_id=? AND plan_kind='followup' AND started_at>? LIMIT 1", (row['run_id'], time()-86400)))
        return unanswered - int(own) < config['max_unanswered'] and budget['total'] - int(own_budget) < config['global_runs'] and budget['own'] - int(own_budget) < config['instance_runs']

    def queue(self, instance_id=None):
        if instance_id: self.store.instance(instance_id)
        rows = self.database.all('SELECT * FROM life_intents' + (' WHERE instance_id=?' if instance_id else '')
            + " ORDER BY status IN ('pending','running') DESC,CASE WHEN status IN ('pending','running') THEN due_at END,updated_at DESC LIMIT 201", (instance_id,) if instance_id else ())
        return {'items': [self.intent_view(row) for row in rows[:200]], 'truncated': len(rows) > 200}

    def timeline(self, instance_id=None, before=None):
        if instance_id: self.store.instance(instance_id)
        rows = self.database.all('SELECT * FROM life_events WHERE id<?' + (' AND instance_id=?' if instance_id else '')
            + ' ORDER BY id DESC LIMIT 41', (before or 9223372036854775807, *((instance_id,) if instance_id else ())))
        return {'items': rows[:40], 'next_before': rows[39]['id'] if len(rows) > 40 else None}
