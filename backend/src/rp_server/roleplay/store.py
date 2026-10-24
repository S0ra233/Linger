"""运行、有效计划和投递事实的数据库边界；这里不等待网络。"""
from dataclasses import asdict
import json
from time import time
from uuid import uuid4

from ..events import EventBus
from ..storage.configuration import RevisionConflict
from ..storage.database import Database
from .decisions import RpDecision, RuntimeSettings, SettingsUpdate


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def diagnostic(value, key: str):
    if isinstance(value, str):
        return value.replace(key, '[redacted]') if key else value
    if isinstance(value, list):
        return [diagnostic(item, key) for item in value]
    if isinstance(value, dict):
        return {name: diagnostic(item, key) for name, item in value.items()
                if name not in {'api_key', 'credential_ref', 'key_env'}}
    return value


def invalidate(tx, instance_id: str, reason: str, *, input_received: bool = False):
    tx.execute('UPDATE role_instances SET input_version=input_version+1 WHERE id=?', (instance_id,))
    version = tx.execute('SELECT input_version FROM role_instances WHERE id=?', (instance_id,)).fetchone()[0] if input_received else None
    # 连发输入把同一批临时暂停交给最新运行；管理变更则撤销异常恢复资格。
    tx.execute("UPDATE rp_deliveries SET resume_input_version=? WHERE instance_id=? AND status='held' AND resume_input_version IS NOT NULL",
               (version, instance_id))
    tx.execute("UPDATE rp_deliveries SET status='held',revision=revision+1,updated_at=?,resume_input_version=? WHERE instance_id=? AND status='pending'",
               (time(), version, instance_id))
    tx.execute("UPDATE rp_outbox SET status='cancelled',finished_at=?,error=? WHERE instance_id=? AND status='pending'",
               (time(), reason, instance_id))


class StaleRun(Exception):
    pass


class RpStore:
    cache = None
    context = None
    media = None
    worldbooks = None
    adjustments = None
    extensions = None
    explicit_cache = None
    def __init__(self, database: Database, events: EventBus):
        self.database, self.events = database, events
        self.information = None
        self.life = None
        self.delivery_scan_offset = 0
        from .contact_plans import ContactPlans
        self.plans = ContactPlans(self)
        from .conversations import ConversationEdits
        from .archives import ConversationArchives
        self.archives = ConversationArchives(self)
        self.conversations = ConversationEdits(self)

    def settings(self):
        row = self.database.one("SELECT * FROM configurations WHERE key='rp_runtime'")
        return {**RuntimeSettings().model_dump(), **(json.loads(row['value_json']) if row else {}),
                'revision': row['revision'] if row else 0}

    def save_settings(self, body: SettingsUpdate):
        with self.database.transaction() as tx:
            row = tx.execute("SELECT revision FROM configurations WHERE key='rp_runtime'").fetchone()
            if (row['revision'] if row else 0) != body.revision:
                raise RevisionConflict('运行设置已变化，请刷新后重试。')
            tx.execute('''INSERT INTO configurations VALUES ('rp_runtime',?,?,?) ON CONFLICT(key)
                DO UPDATE SET value_json=excluded.value_json,revision=excluded.revision,updated_at=excluded.updated_at''',
                (encoded(body.model_dump(exclude={'revision'})), body.revision + 1, time()))
            if not body.enabled:
                for row in tx.execute('SELECT id FROM role_instances').fetchall():
                    invalidate(tx, row['id'], '自动运行已关闭。')
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (time(), 'rp.settings_saved', encoded({'revision': body.revision + 1, 'enabled': body.enabled})))
        self.events.publish('runtime')
        return self.settings()

    def recover(self):
        with self.database.transaction() as tx:
            tx.execute("UPDATE rp_runs SET status='unknown',finished_at=?,error='服务中断，未自动重新调用模型。' WHERE status='running'", (time(),))
            tx.execute("UPDATE rp_steps SET status='unknown',finished_at=? WHERE status='requesting'", (time(),))
            tx.execute("UPDATE rp_summaries SET status='discarded',error='进程中断，摘要未采纳。',finished_at=? WHERE status IN ('running','candidate')", (time(),))
            tx.execute("UPDATE rp_outbox SET status='unknown',finished_at=?,error='投递中断，结果未知；不会自动重发。' WHERE status='sending'", (time(),))
            # 重启不补发已经到点的计划；将来才到点的计划仍可保留。
            tx.execute("UPDATE rp_outbox SET status='cancelled',finished_at=?,error='重启时已过触发时间，未补发。' WHERE status='pending' AND due_at<=? AND (plan_kind<>'followup' OR anchor_at IS NOT NULL)", (time(), time()))
            self.plans.sync(tx)
            if self.life:
                self.life.recover(tx)

    def instance(self, instance_id: str):
        row = self.database.one('''SELECT r.*,a.enabled AS account_enabled,a.bot_id,
            c.enabled AS contact_enabled,c.instance_id AS bound_instance,c.display_name,c.nickname,
            v.config_json AS character_config FROM role_instances r JOIN qq_accounts a ON a.id=r.account_id
            JOIN contacts c ON c.account_id=r.account_id AND c.peer_id=r.peer_id
            JOIN character_versions v ON v.character_id=r.character_id AND v.revision=r.character_revision WHERE r.id=?''', (instance_id,))
        if not row:
            raise LookupError('生活线不存在。')
        if self.adjustments:
            row['character_config'] = encoded(self.adjustments.effective(row))
        return row

    @staticmethod
    def active(row):
        return row['account_enabled'] and row['contact_enabled'] and not row['paused'] and row['bound_instance'] == row['id']

    def valid(self, instance_id: str, version: int, run_id: int | None = None):
        row = self.instance(instance_id)
        return self.active(row) and row['input_version'] == version and self.settings()['enabled'] and (
            run_id is None or self.life is None or self.life.valid_run(run_id))

    def state(self, instance_id: str):
        row = self.database.one('SELECT * FROM rp_states WHERE instance_id=?', (instance_id,))
        result = ({**row, 'state': json.loads(row['state_json'])} if row else
                  {'state': {}, 'life_fragment': '', 'read_state': False, 'next_wakeup_at': None})
        if row and row['invalidated']:
            result.update(state={}, life_fragment='')
        if self.life:
            result['next_wakeup_at'] = self.database.one("SELECT min(due_at) AS at FROM life_intents WHERE instance_id=? AND status IN ('pending','running')", (instance_id,))['at']
        return result

    def candidates(self, connected_accounts: list[str]):
        if not connected_accounts:
            return []
        placeholders = ','.join('?' for _ in connected_accounts)
        return self.database.all('''SELECT r.id,r.input_version,max(m.id) AS input_through,max(m.received_at) AS last_input
            FROM role_instances r JOIN contacts c ON c.instance_id=r.id AND c.enabled=1
            JOIN qq_accounts a ON a.id=r.account_id AND a.enabled=1
            JOIN inbound_messages m ON m.instance_id=r.id AND m.id>r.handled_input_id AND m.deleted_at IS NULL AND m.archived_at IS NULL
            WHERE r.paused=0 AND r.attempted_version<>r.input_version
            AND NOT EXISTS (SELECT 1 FROM inbound_images i JOIN inbound_messages n ON n.id=i.message_id
                WHERE n.instance_id=r.id AND n.id>r.handled_input_id AND n.deleted_at IS NULL AND n.archived_at IS NULL AND i.status IN ('pending','fetching')
                AND NOT EXISTS (SELECT 1 FROM evidence_status es WHERE es.instance_id=n.instance_id AND es.kind='input' AND es.source_id=n.id AND es.invalid=1))
            AND NOT EXISTS (SELECT 1 FROM rp_outbox o JOIN rp_runs x ON x.id=o.run_id WHERE o.instance_id=r.id AND o.status IN ('sending','unknown') AND x.context_deleted_at IS NULL AND x.context_archived_at IS NULL)
            AND r.account_id IN (''' + placeholders + ') GROUP BY r.id ORDER BY last_input LIMIT 100', tuple(connected_accounts))

    def begin(self, candidate: dict, settings: RuntimeSettings):
        with self.database.transaction() as tx:
            row = self.instance(candidate['id'])
            if not self.active(row) or row['input_version'] != candidate['input_version']:
                raise StaleRun()
            if not tx.execute('''SELECT 1 FROM inbound_messages m WHERE instance_id=? AND id>? AND id<=? AND deleted_at IS NULL AND archived_at IS NULL
                AND NOT EXISTS (SELECT 1 FROM evidence_status es WHERE es.instance_id=m.instance_id AND es.kind='input' AND es.source_id=m.id AND es.invalid=1) LIMIT 1''',
                              (row['id'], row['handled_input_id'], candidate['input_through'])).fetchone():
                # 管理员已明确排除的输入保留原文；纯失效积压只推进处理游标，不调用模型或假造生活决策。
                tx.execute('UPDATE role_instances SET handled_input_id=max(handled_input_id,?),attempted_version=input_version WHERE id=?', (candidate['input_through'], row['id']))
                return None
            if self.database.one('''SELECT 1 FROM inbound_images i JOIN inbound_messages m ON m.id=i.message_id
                WHERE m.instance_id=? AND m.id>? AND m.deleted_at IS NULL AND m.archived_at IS NULL AND i.status IN ('pending','fetching')
                AND NOT EXISTS (SELECT 1 FROM evidence_status es WHERE es.instance_id=m.instance_id AND es.kind='input' AND es.source_id=m.id AND es.invalid=1) LIMIT 1''',
                (row['id'], row['handled_input_id'])):
                return None
            if self.database.one("SELECT 1 FROM rp_outbox o JOIN rp_runs r ON r.id=o.run_id WHERE o.instance_id=? AND o.status IN ('sending','unknown') AND r.context_deleted_at IS NULL AND r.context_archived_at IS NULL", (row['id'],)):
                return None
            tx.execute('UPDATE role_instances SET attempted_version=input_version WHERE id=?', (row['id'],))
            cursor = tx.execute('''INSERT INTO rp_runs(instance_id,input_version,input_through,status,settings_json,created_at)
                VALUES (?,?,?,'running',?,?)''',
                (row['id'], row['input_version'], candidate['input_through'], settings.model_dump_json(), time()))
            run_id = cursor.lastrowid
        self.events.publish('runtime')
        return run_id, row

    def model_snapshot(self, run_id: int, snapshot, versions=None):
        data = {'connection_id': snapshot.connection_id, 'connection_revision': snapshot.revision,
                'model_id': snapshot.model.id, 'protocol': snapshot.connection.protocol,
                'model': snapshot.model.model, 'parameters': snapshot.model.model_dump(),
                'preset_config': snapshot.preset_config.model_dump()}
        if versions is not None:
            data['versions'] = versions
        with self.database.transaction() as tx:
            tx.execute('UPDATE rp_runs SET model_json=? WHERE id=?', (encoded(data), run_id))

    def start_step(self, run_id: int, number: int, request, key: str, *, budget=None, repair_attempt=0):
        with self.database.transaction() as tx:
            cursor = tx.execute('''INSERT INTO rp_steps(run_id,step,request_json,response_json,status,created_at)
                VALUES (?,?,?,?,'requesting',?)''', (run_id, number, encoded(diagnostic(asdict(request), key)),
                encoded(diagnostic({'context_budget': budget, 'repair_attempt': repair_attempt,
                                    'tool_policy': request.tool_policy_trace}, key)) if budget is not None else None, time()))
        return cursor.lastrowid

    def finish_step(self, step_id: int, response: dict, tools: list[dict], key: str):
        # 工具输出属于诊断，不进入已发送聊天；只保存显式投影，不保存模型密钥或原生私有思考。
        text = encoded(diagnostic(response, key))
        with self.database.transaction() as tx:
            tx.execute("UPDATE rp_steps SET response_json=?,tools_json=?,status='returned',finished_at=? WHERE id=?",
                       (text, encoded(diagnostic(tools, key)), time(), step_id))

    def finish_run(self, run_id: int, status: str, error: str, *, failure=None):
        with self.database.transaction() as tx:
            changed = tx.execute('UPDATE rp_runs SET status=?,error=?,finished_at=? WHERE id=? AND status=\'running\'',
                       (status, error, time(), run_id))
            if changed.rowcount:
                row = tx.execute('SELECT * FROM rp_runs WHERE id=?', (run_id,)).fetchone()
                report = json.loads(row['context_json']) if row['context_json'] else {}
                if failure is not None:
                    report['failure'] = failure.model_dump()
                if status == 'failed':
                    restored = self.plans.resume_after_failure(tx, dict(row))
                    if restored:
                        report['long_plan_recovery'] = {'restored': len(restored), 'plan_ids': restored}
                if report:
                    tx.execute('UPDATE rp_runs SET context_json=? WHERE id=?', (encoded(report), run_id))
            tx.execute("UPDATE rp_steps SET status=?,finished_at=? WHERE run_id=? AND status='requesting'", (status, time(), run_id))
            tx.execute("UPDATE rp_summaries SET status='discarded',error='所属运行未提交，摘要未采纳。',finished_at=? WHERE run_id=? AND status IN ('running','candidate')", (time(), run_id))
            if self.life:
                self.life.finish(tx, run_id, status, error)
        self.events.publish('runtime')

    def commit(self, run_id: int, decision: RpDecision, settings: RuntimeSettings):
        now = time()
        dates = decision.schedule(now, settings.schedule_hours)
        with self.database.transaction() as tx:
            run = self.database.one('SELECT * FROM rp_runs WHERE id=?', (run_id,))
            if run['status'] != 'running' or not self.valid(run['instance_id'], run['input_version'], run_id):
                raise StaleRun()
            allow_current = bool(decision.life_fragment.strip() or decision.state_changes)
            if self.worldbooks:
                self.worldbooks.commit(tx, run)
            if decision.scene and self.information is None:
                raise ValueError('资料服务未接通，当前场景未提交。')
            current_id = self.life.commit(tx, run, decision, settings) if self.life else None
            if self.extensions:
                self.extensions.commit(tx, run, decision)
            if self.information:
                self.information.evidence.validate_scene(run['instance_id'], decision.scene, run['input_through'], settings.schedule_hours, allow_current)
                self.information.evidence.commit_scene(tx, run, decision.scene, current_id, allow_current)
            state = self.state(run['instance_id'])['state']
            if decision.memory_candidates:
                if self.information is None:
                    raise ValueError('记忆服务尚未接入，候选未提交。')
                self.information.commit_candidates(tx, run, decision.memory_candidates, current_id=current_id, allow_current=allow_current)
            state.update(decision.state_changes)
            if len(encoded(state).encode('utf-8')) > 32000:
                raise ValueError('角色运行状态超出保存预算。')
            tx.execute('''INSERT INTO rp_states(instance_id,state_json,life_fragment,read_state,next_wakeup_at,updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(instance_id) DO UPDATE SET
                state_json=excluded.state_json,life_fragment=excluded.life_fragment,read_state=excluded.read_state,
                next_wakeup_at=excluded.next_wakeup_at,updated_at=excluded.updated_at,invalidated=0''',
                (run['instance_id'], encoded(state), decision.life_fragment, int(decision.read_state), None, now))
            for index, (message, due) in enumerate(zip(decision.messages, dates)):
                parts = self.media.validate_parts(run['instance_id'], message.parts) if message.parts and self.media else []
                if message.parts and not parts: raise ValueError('素材服务未接通，计划未提交。')
                text = message.text or ''.join(part['text'] if part['type'] == 'text' else f"[素材：{part['name']}]" for part in parts)
                tx.execute('''INSERT INTO rp_outbox(id,run_id,instance_id,input_version,position,text,due_at,expires_at,status,parts_json)
                    VALUES (?,?,?,?,?,?,?,?,'pending',?)''',
                    (uuid4().hex, run_id, run['instance_id'], run['input_version'], index, text, due, due + settings.delivery_grace, encoded(parts)))
            self.plans.commit(tx, run, decision, settings)
            tx.execute("UPDATE rp_runs SET decision_json=?,status='completed',finished_at=? WHERE id=?", (decision.model_dump_json(), now, run_id))
            if self.adjustments:
                self.adjustments.commit(tx, run, decision.character_adjustment_proposal)
            if tx.execute("SELECT 1 FROM rp_summaries WHERE run_id=? AND instance_id=? AND input_version=? AND status='candidate'",
                          (run_id, run['instance_id'], run['input_version'])).fetchone():
                tx.execute("UPDATE rp_summaries SET status='superseded' WHERE instance_id=? AND status='active'", (run['instance_id'],))
                latest = tx.execute("SELECT max(id) FROM rp_summaries WHERE run_id=? AND status='candidate' AND input_version=?", (run_id, run['input_version'])).fetchone()[0]
                tx.execute("UPDATE rp_summaries SET status=CASE WHEN id=? THEN 'active' ELSE 'superseded' END WHERE run_id=? AND status='candidate'", (latest, run_id))
            workset = tx.execute('SELECT state_json FROM context_candidates WHERE run_id=? AND instance_id=? AND input_version=?',
                                 (run_id, run['instance_id'], run['input_version'])).fetchone()
            if workset:
                # 配置可在运行中更新；这里只采用本轮工作集，保留管理员的新配置版本。
                tx.execute('''INSERT INTO context_worksets(instance_id,state_json,updated_at) VALUES (?,?,?)
                    ON CONFLICT(instance_id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at''',
                    (run['instance_id'], workset['state_json'], now))
            if decision.reply_mode == 'silent':
                tx.execute('UPDATE role_instances SET handled_input_id=max(handled_input_id,?) WHERE id=?',
                           (run['input_through'], run['instance_id']))
        self.events.publish('runtime')

    def pending(self, connected_accounts: list[str]):
        if not connected_accounts: return []
        placeholders = ','.join('?' for _ in connected_accounts)
        rows = self.database.all("""SELECT o.*,r.account_id,r.peer_id FROM rp_outbox o JOIN role_instances r ON r.id=o.instance_id
            WHERE o.status='pending' AND o.due_at<=? AND r.account_id IN (""" + placeholders + """)
            AND (o.plan_kind<>'followup' OR o.anchor_at IS NOT NULL)
            AND NOT EXISTS(SELECT 1 FROM rp_outbox p JOIN rp_runs x ON x.id=p.run_id WHERE p.instance_id=o.instance_id AND p.status IN ('sending','unknown') AND x.context_deleted_at IS NULL AND x.context_archived_at IS NULL)
            AND NOT EXISTS(SELECT 1 FROM rp_outbox p WHERE p.run_id=o.run_id AND p.position<o.position
                AND o.plan_kind<>'delivery' AND (p.plan_kind='reply' OR p.plan_kind=o.plan_kind) AND p.status<>'succeeded')
            AND (o.plan_kind<>'followup' OR coalesce((SELECT max(p.finished_at) FROM rp_outbox p WHERE p.run_id=o.run_id AND p.plan_kind='followup' AND p.position<o.position),0)+o.minimum_gap<=?)
            ORDER BY o.due_at,o.id LIMIT 20 OFFSET ?""", (time(), *connected_accounts, time(), self.delivery_scan_offset))

        # 被静默／额度阻止的最早计划不能长期挡住后续联系人。
        self.delivery_scan_offset = self.delivery_scan_offset + len(rows) if len(rows) == 20 else 0
        return rows

    def sweep(self):
        now = time()
        with self.database.transaction() as tx:
            changed = tx.execute("""UPDATE rp_outbox SET status='cancelled',finished_at=?,error='计划已过期、暂停或上下文已变化。'
                WHERE status='pending' AND ((expires_at<? AND (plan_kind<>'followup' OR anchor_at IS NOT NULL)) OR EXISTS (
                SELECT 1 FROM role_instances r JOIN qq_accounts a ON a.id=r.account_id JOIN contacts c ON c.account_id=r.account_id AND c.peer_id=r.peer_id
                WHERE r.id=rp_outbox.instance_id AND ((r.input_version<>rp_outbox.input_version AND rp_outbox.plan_kind<>'delivery')
                OR r.paused=1 OR a.enabled=0 OR c.enabled=0 OR c.instance_id<>r.id)))""", (now, now)).rowcount
            changed += tx.execute("""UPDATE rp_outbox SET status='cancelled',finished_at=?,error='前序消息未能送达。'
                WHERE status='pending' AND plan_kind<>'delivery' AND EXISTS(SELECT 1 FROM rp_outbox p WHERE p.run_id=rp_outbox.run_id
                AND p.position<rp_outbox.position AND (p.plan_kind='reply' OR p.plan_kind=rp_outbox.plan_kind) AND p.status IN ('failed','cancelled'))""", (now,)).rowcount
            self.plans.sync(tx)
        if changed: self.events.publish('runtime')

    def claim(self, outbox_id: str):
        with self.database.transaction() as tx:
            row = self.database.one('SELECT * FROM rp_outbox WHERE id=?', (outbox_id,))
            if not row or row['status'] != 'pending' or row['due_at']>time(): return None
            instance = self.instance(row['instance_id'])
            valid = self.active(instance) and self.settings()['enabled']
            if row['plan_kind'] == 'delivery':
                valid = valid and bool(tx.execute("SELECT 1 FROM rp_deliveries WHERE id=? AND outbox_id=? AND revision=? AND status='pending'", (row['plan_id'], row['id'], row['plan_revision'])).fetchone())
            else:
                valid = valid and self.valid(row['instance_id'], row['input_version'])
            if row['expires_at'] < time() or not valid:
                tx.execute("UPDATE rp_outbox SET status='cancelled',error='计划已过期或上下文已变化。',finished_at=? WHERE id=?", (time(), outbox_id))
                self.plans.sync(tx)
                return None
            if row['plan_kind'] != 'delivery' and tx.execute("SELECT 1 FROM rp_outbox WHERE run_id=? AND position<? AND (plan_kind='reply' OR plan_kind=?) AND status<>'succeeded'", (row['run_id'], row['position'], row['plan_kind'])).fetchone(): return None
            if row['plan_kind'] == 'followup':
                previous = tx.execute("SELECT max(finished_at) FROM rp_outbox WHERE run_id=? AND plan_kind='followup' AND position<?", (row['run_id'], row['position'])).fetchone()[0]
                if row['anchor_at'] is None or previous and previous + row['minimum_gap']>time(): return None
            if tx.execute("SELECT 1 FROM rp_outbox p JOIN rp_runs r ON r.id=p.run_id WHERE p.instance_id=? AND p.id<>? AND p.status IN ('sending','unknown') AND r.context_deleted_at IS NULL AND r.context_archived_at IS NULL LIMIT 1", (row['instance_id'], row['id'])).fetchone(): return None
            if self.life and not self.life.allow_delivery(row): return None
            tx.execute("UPDATE rp_outbox SET status='sending',started_at=? WHERE id=?", (time(), outbox_id))
            self.plans.sync(tx)
        self.events.publish('runtime')
        return row

    def receipt(self, account_id: str, outbox_id: str, result: dict):
        with self.database.transaction() as tx:
            row = self.database.one('''SELECT o.*,r.account_id FROM rp_outbox o JOIN role_instances r ON r.id=o.instance_id
                WHERE o.id=? AND r.account_id=?''', (outbox_id, account_id))
            if not row:
                return
            if row['status'] not in {'sending', 'unknown'}:
                if row['confirmation_source'] != 'administrator' or result['status'] != 'succeeded':
                    return
                if row['status'] == 'failed':
                    # 人工“未送达”后仍可能收到迟到成功；让正在生成的旧判断失效。
                    invalidate(tx, row['instance_id'], '迟到成功回执修正了人工判定。')
                tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                           (time(), 'rp.receipt_corrected', encoded({'id': outbox_id, 'source': 'gateway'})))
            self._apply_receipt(tx, row, result)
        self.events.publish('runtime')

    def _apply_receipt(self, tx, row, result: dict):
        status = result['status']
        source = result.get('source', 'gateway')
        tx.execute('UPDATE rp_outbox SET status=?,message_id=?,error=?,finished_at=?,confirmation_source=? WHERE id=?',
                   (status, result.get('message_id'), result.get('error'), time(), source, row['id']))
        if status == 'succeeded':
            source_run = tx.execute('SELECT context_deleted_at,context_archived_at FROM rp_runs WHERE id=?', (row['run_id'],)).fetchone()
            tx.execute('''INSERT INTO sent_messages(instance_id,outbox_id,message_id,text,attempted_at,confirmed_at,confirmation_source,parts_json,deleted_at,archived_at)
                VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(outbox_id) DO UPDATE SET message_id=excluded.message_id,
                confirmed_at=excluded.confirmed_at,confirmation_source=excluded.confirmation_source''',
                (row['instance_id'], row['id'], result['message_id'], row['text'], row['started_at'], time(), source, row['parts_json'], source_run['context_deleted_at'], source_run['context_archived_at']))
            if row['plan_kind'] == 'reply':
                through = tx.execute('SELECT input_through FROM rp_runs WHERE id=?', (row['run_id'],)).fetchone()[0]
                tx.execute('UPDATE role_instances SET handled_input_id=max(handled_input_id,?) WHERE id=?', (through, row['instance_id']))
        elif status == 'failed':
            tx.execute("UPDATE rp_outbox SET status='cancelled',error='前一气泡投递失败。',finished_at=? WHERE run_id=? AND position>? AND status='pending' AND plan_kind<>'delivery'",
                       (time(), row['run_id'], row['position']))
        if status == 'succeeded' and row['plan_kind'] == 'reply' and not tx.execute("SELECT 1 FROM rp_outbox WHERE run_id=? AND plan_kind='reply' AND status<>'succeeded'", (row['run_id'],)).fetchone():
            tx.execute("UPDATE rp_outbox SET anchor_at=?,due_at=?+after_seconds,expires_at=?+after_seconds+(expires_at-due_at) WHERE run_id=? AND plan_kind='followup' AND status='pending' AND anchor_at IS NULL", (time(), time(), time(), row['run_id']))
        self.plans.sync(tx)
        if self.life:
            self.life.event(tx, row['instance_id'], 'delivery', {'outbox_id': row['id'], 'status': status,
                'message_id': result.get('message_id'), 'text': row['text'] if status == 'succeeded' else None,
                'confirmation_source': source}, row['run_id'])

    def control(self, instance_id: str, action: str):
        with self.database.transaction() as tx:
            self.instance(instance_id)
            invalidate(tx, instance_id, '管理员调整了运行状态。')
            tx.execute('UPDATE role_instances SET paused=?,attempted_version=-1 WHERE id=?', (int(action == 'pause'), instance_id))
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (time(), 'rp.' + action, encoded({'instance_id': instance_id})))
        self.events.publish('runtime')
        return {'action': action}

    def resolve_unknown(self, outbox_id: str, result: str, message_id: str | None):
        if result == 'succeeded' and not message_id:
            raise ValueError('确认送达需填写实际 QQ 消息编号。')
        with self.database.transaction() as tx:
            row = self.database.one('SELECT * FROM rp_outbox WHERE id=?', (outbox_id,))
            if not row or row['status'] != 'unknown':
                raise RevisionConflict('投递状态已变化，只能人工确认仍为未知的记录。')
            self._apply_receipt(tx, row, {'status': result, 'message_id': message_id, 'error': '管理员人工确认。', 'source': 'administrator'})
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (time(), 'rp.delivery_resolved', encoded({'id': outbox_id, 'status': result, 'source': 'administrator'})))
        self.events.publish('runtime')
        return {'status': result}

    def cancel_delivery(self, outbox_id: str):
        with self.database.transaction() as tx:
            row = tx.execute('SELECT status,run_id,position,plan_kind,plan_id FROM rp_outbox WHERE id=?', (outbox_id,)).fetchone()
            if not row or row['status'] != 'pending':
                raise RevisionConflict('只有尚未开始发送的计划可以取消。')
            tx.execute("UPDATE rp_outbox SET status='cancelled',error='管理员取消后续气泡。',finished_at=? WHERE run_id=? AND position>=? AND status='pending' AND ((?='delivery' AND id=?) OR (?<>'delivery' AND plan_kind<>'delivery'))",
                       (time(), row['run_id'], row['position'], row['plan_kind'], outbox_id, row['plan_kind']))
            self.plans.sync(tx)
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (time(), 'rp.delivery_cancelled', encoded({'id': outbox_id})))
        self.events.publish('runtime')
        return {'status': 'cancelled'}

    def list_runs(self, before: int | None = None, instance_id: str | None = None):
        if instance_id is not None:
            self.instance(instance_id)
        scope = ' AND instance_id=?' if instance_id is not None else ''
        parameters = (before or 9223372036854775807,) + ((instance_id,) if instance_id is not None else ())
        rows = self.database.all('''SELECT id,instance_id,input_version,input_through,status,error,created_at,finished_at,model_json,trigger_kind
            FROM rp_runs WHERE id<?''' + scope + ' ORDER BY id DESC LIMIT 31', parameters)
        return {'items': rows[:30], 'next_before': rows[29]['id'] if len(rows) > 30 else None}

    def run_detail(self, run_id: int):
        run = self.database.one('SELECT * FROM rp_runs WHERE id=?', (run_id,))
        if not run:
            raise LookupError('运行不存在。')
        steps = self.database.all('SELECT * FROM rp_steps WHERE run_id=? ORDER BY step', (run_id,))
        from .run_explanation import explain_run
        from .run_diagnostics import run_diagnostics
        return {'run': run, 'steps': steps, 'explanation': explain_run(self.database, run, steps),
                'diagnostics': run_diagnostics(self.database, run, steps)}

    def deliveries(self, instance_id: str | None = None):
        if instance_id is not None:
            self.instance(instance_id)
        scope = ' WHERE instance_id=?' if instance_id is not None else ''
        return self.database.all('SELECT * FROM rp_outbox' + scope + ''' ORDER BY
            CASE status WHEN 'unknown' THEN 0 WHEN 'sending' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
            CASE WHEN status IN ('pending','sending','unknown') THEN due_at END ASC,finished_at DESC LIMIT 200''',
            (instance_id,) if instance_id is not None else ())
