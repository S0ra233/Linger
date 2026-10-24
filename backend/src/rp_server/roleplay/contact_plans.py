"""结构化台词计划；时间检查和投递不调用模型，业务写入共用原提交事务。"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from time import time
from uuid import uuid4

from ..storage.configuration import RevisionConflict
from .decisions import Bubble, DeliveryChange, MessagePart, RuntimeSettings
from .store import encoded


# 暂停复核／替代版本不是永久撤销，避免每轮复核把回收列表塞满相同长期台词。
RECYCLED = "o.status='cancelled' AND (o.plan_kind<>'delivery' OR EXISTS(SELECT 1 FROM rp_deliveries d WHERE d.outbox_id=o.id AND d.status='cancelled'))"


class ContactPlans:
    def __init__(self, store):
        self.store, self.database = store, store.database

    @staticmethod
    def stored_message(row):
        parts = [MessagePart(type=part['type'], text=part.get('text', '') if part['type'] == 'text' else '',
                             asset_id=part.get('asset_id') if part['type'] == 'asset' else None,
                             revision=part.get('revision', 1)) for part in json.loads(row['parts_json'])]
        return Bubble(parts=parts) if parts else Bubble(text=row['text'])

    @staticmethod
    def signature(due, message):
        # 素材展示名称不参与身份；纯text与等价文字parts、相邻文字片段使用同一表示。
        segments = []
        parts = message.parts or [MessagePart(type='text', text=message.text)]
        for part in parts:
            if part.type == 'text':
                if segments and segments[-1][0] == 'text':
                    segments[-1] = ('text', segments[-1][1] + part.text)
                else: segments.append(('text', part.text))
            else: segments.append(('asset', part.asset_id, part.revision))
        return due, tuple(segments)

    def validate(self, instance_id, decision, runtime):
        config, now = self.store.life.settings(), time()
        previous = 0
        for item in decision.followups:
            if item.after_seconds - previous < config['followup_min_gap']:
                raise ValueError('后续分支间隔小于此生活设置的最小秒数。')
            if item.after_seconds > runtime.schedule_hours * 3600:
                raise ValueError('后续分支超出允许计划范围。')
            previous = item.after_seconds
        if not decision.deliveries: return
        identifiers = [item.id for item in decision.deliveries if item.id]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError('同一计划不能重复处理同一长期投递。')
        current = {row['id']: row for row in self.database.all("SELECT * FROM rp_deliveries WHERE instance_id=? AND EXISTS(SELECT 1 FROM rp_runs r WHERE r.id=rp_deliveries.run_id AND r.context_archived_at IS NULL) AND status IN ('pending','held','sending','unknown')", (instance_id,))}
        projected = {key: self.signature(row['due_at'], self.stored_message(row)) for key, row in current.items()}
        changed = set()
        for index, item in enumerate(decision.deliveries):
            row = current.get(item.id)
            if item.operation != 'add' and (not row or row['revision'] != item.revision or row['status'] not in {'pending', 'held'}):
                raise RevisionConflict('长期投递已变化、开始发送或不属于本生活线，请重新读取状态。')
            if item.operation == 'cancel':
                projected.pop(item.id)
                continue
            due = row['due_at'] if item.operation == 'keep' else item.timestamp()
            if not now + 1 <= due <= now + runtime.schedule_hours * 3600:
                raise ValueError('长期投递须在未来且不超出计划范围；过期事项请修改时间或取消。')
            if self.store.life.quiet(due, config):
                raise ValueError('长期投递时间处于安静时段，请调整时间。')
            message = self.stored_message(row) if item.operation == 'keep' else Bubble(text=item.text, parts=item.parts)
            if message.parts: self.store.media.validate_parts(instance_id, message.parts)
            key = item.id or f'new:{index}'
            projected[key] = self.signature(due, message)
            changed.add(key)
        if len(projected) > 16:
            raise ValueError('每条生活线最多保留16项有效长期投递。')
        for key in changed:
            if any(other != key and value == projected[key] for other, value in projected.items()):
                raise ValueError('相同时间与内容的长期投递重复；请修改、取消或保留原事项。')

    def insert_outbox(self, tx, run, kind, message, due, expires, *, identifier=None, revision=None, after=None, anchor=None, gap=1):
        parts = self.store.media.validate_parts(run['instance_id'], message.parts) if message.parts else []
        text = message.text or ''.join(part['text'] if part['type'] == 'text' else f"[素材：{part['name']}]" for part in parts)
        position = tx.execute('SELECT coalesce(max(position),-1)+1 FROM rp_outbox WHERE run_id=?', (run['id'],)).fetchone()[0]
        target = uuid4().hex
        tx.execute('''INSERT INTO rp_outbox(id,run_id,instance_id,input_version,position,text,due_at,expires_at,status,parts_json,
            plan_kind,plan_id,plan_revision,after_seconds,anchor_at,minimum_gap) VALUES (?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?)''',
            (target, run['id'], run['instance_id'], run['input_version'], position, text, due, expires, encoded(parts),
             kind, identifier, revision, after, anchor, gap))
        return target, text, parts

    def commit(self, tx, run, decision, runtime):
        self.validate(run['instance_id'], decision, runtime)
        now, config = time(), self.store.life.settings()
        if decision.messages:
            anchor = None
        else:
            row = tx.execute('SELECT received_at FROM inbound_messages WHERE id=? AND instance_id=?', (run['input_through'], run['instance_id'])).fetchone()
            anchor = row['received_at'] if row else now
        for item in decision.followups:
            due = (anchor if anchor is not None else now) + item.after_seconds
            self.insert_outbox(tx, run, 'followup', item, due, due + config['grace_seconds'],
                               after=item.after_seconds, anchor=anchor, gap=config['followup_min_gap'])
        for item in decision.deliveries:
            row = tx.execute('SELECT * FROM rp_deliveries WHERE id=? AND instance_id=?', (item.id, run['instance_id'])).fetchone() if item.id else None
            identifier, revision = (row['id'], row['revision'] + 1) if row else (uuid4().hex, 1)
            if row:
                tx.execute("UPDATE rp_outbox SET status='cancelled',finished_at=?,error='长期投递已被新修订替代。' WHERE plan_id=? AND status='pending'", (now, identifier))
            if item.operation == 'cancel':
                tx.execute("UPDATE rp_deliveries SET status='cancelled',revision=?,updated_at=?,resume_input_version=NULL WHERE id=?", (revision, now, identifier))
                continue
            if item.operation == 'keep':
                due = row['due_at']
                message = self.stored_message(row)
            else:
                due, message = item.timestamp(), Bubble(text=item.text, parts=item.parts)
            target, text, parts = self.insert_outbox(tx, run, 'delivery', message, due, due + config['grace_seconds'],
                                                    identifier=identifier, revision=revision)
            tx.execute('''INSERT INTO rp_deliveries(id,instance_id,revision,run_id,due_at,text,parts_json,status,outbox_id,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,'pending',?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,run_id=excluded.run_id,
                due_at=excluded.due_at,text=excluded.text,parts_json=excluded.parts_json,status='pending',outbox_id=excluded.outbox_id,
                updated_at=excluded.updated_at,resume_input_version=NULL''',
                (identifier, run['instance_id'], revision, run['id'], due, text, encoded(parts), target, row['created_at'] if row else now, now))
        # 有效决定省略的held仍待复核；后续另一轮失败不能把它当作本轮暂停恢复。
        tx.execute("UPDATE rp_deliveries SET resume_input_version=NULL WHERE instance_id=? AND status='held' AND resume_input_version=?",
                   (run['instance_id'], run['input_version']))

    def resume_after_failure(self, tx, run):
        """在失败收束事务内恢复新输入暂缓的旧约定，不采用失败输出或重发未知消息。"""
        if run['status'] != 'failed' or run['trigger_kind'] != 'input' or run['context_deleted_at'] is not None or run['context_archived_at'] is not None:
            return []
        instance = self.store.instance(run['instance_id'])
        raw = self.store.settings()
        if not self.store.active(instance) or instance['input_version'] != run['input_version'] or not raw['enabled']:
            return []
        if not self.store.life:
            return []
        config = self.store.life.settings()
        if not config['enabled'] or not self.store.life.policy(instance['id'])['enabled']:
            return []
        runtime = RuntimeSettings.model_validate({key: value for key, value in raw.items() if key != 'revision'})
        rows = tx.execute('''SELECT d.*,r.input_version AS source_input_version FROM rp_deliveries d
            JOIN rp_runs r ON r.id=d.run_id JOIN rp_outbox o ON o.id=d.outbox_id
            WHERE d.instance_id=? AND d.status='held' AND d.resume_input_version=?
            AND r.status='completed' AND r.context_deleted_at IS NULL AND r.context_archived_at IS NULL
            AND o.status='cancelled' AND o.started_at IS NULL AND o.plan_kind='delivery'
            AND o.plan_id=d.id AND o.plan_revision=d.revision-1 ORDER BY d.due_at LIMIT 16''',
            (instance['id'], run['input_version'])).fetchall()
        now, restored = time(), []
        for row in rows:
            due = row['due_at']
            if not now + 1 <= due <= now + runtime.schedule_hours * 3600 or self.store.life.quiet(due, config):
                continue
            source = {'id': row['run_id'], 'instance_id': instance['id'], 'input_version': row['source_input_version']}
            revision = row['revision'] + 1
            try:
                target, _, _ = self.insert_outbox(tx, source, 'delivery', self.stored_message(row),
                    due, due + config['grace_seconds'], identifier=row['id'], revision=revision)
            except (ValueError, LookupError):
                # 素材已改版或失去授权时仍待复核，不因接口失败绕过素材校验。
                continue
            tx.execute("UPDATE rp_deliveries SET status='pending',revision=?,outbox_id=?,updated_at=?,resume_input_version=NULL WHERE id=?",
                       (revision, target, now, row['id']))
            restored.append(row['id'])
        return restored

    def context(self, instance_id):
        pending = self.database.all("SELECT id,revision,due_at,text,status FROM rp_deliveries WHERE instance_id=? AND EXISTS(SELECT 1 FROM rp_runs r WHERE r.id=rp_deliveries.run_id AND r.context_archived_at IS NULL) AND status IN ('pending','held','sending','unknown') ORDER BY due_at LIMIT 16", (instance_id,))
        recent = self.database.all("SELECT d.id,d.status,CASE WHEN d.status='cancelled' THEN d.updated_at ELSE o.finished_at END AS result_at,(SELECT id FROM sent_messages WHERE outbox_id=d.outbox_id AND deleted_at IS NULL AND archived_at IS NULL) AS sent_id FROM rp_deliveries d LEFT JOIN rp_outbox o ON o.id=d.outbox_id WHERE d.instance_id=? AND EXISTS(SELECT 1 FROM rp_runs r WHERE r.id=d.run_id AND r.context_archived_at IS NULL) AND d.status IN ('succeeded','failed','cancelled') ORDER BY d.updated_at DESC LIMIT 3", (instance_id,))
        zone = ZoneInfo(self.store.life.settings()['timezone'])
        for row in pending:
            row['at'] = datetime.fromtimestamp(row.pop('due_at'), zone).isoformat(timespec='seconds')
        for row in recent:
            source = row.pop('sent_id')
            if source is not None: row['source'] = {'kind': 'sent', 'id': source, 'revision': 1}
            at = row.pop('result_at')
            if at is not None: row['at'] = datetime.fromtimestamp(at, zone).isoformat(timespec='seconds')
        return {key: value for key, value in (('pending', pending), ('recent', recent)) if value}

    def sync(self, tx):
        tx.execute('''UPDATE rp_deliveries SET status=(SELECT status FROM rp_outbox WHERE id=rp_deliveries.outbox_id),
            revision=revision+1,updated_at=? WHERE status IN ('pending','sending','unknown','failed')
            AND EXISTS(SELECT 1 FROM rp_outbox o WHERE o.id=rp_deliveries.outbox_id AND o.status<>rp_deliveries.status)''', (time(),))

    def list(self, instance_id=None, limit=200):
        if instance_id: self.store.instance(instance_id)
        clause, args = (' WHERE instance_id=?', (instance_id,)) if instance_id else ('', ())
        rows = self.database.all('SELECT * FROM rp_deliveries' + clause + ' ORDER BY CASE status WHEN \'held\' THEN 0 WHEN \'pending\' THEN 1 ELSE 2 END,CASE WHEN status IN (\'held\',\'pending\') THEN due_at ELSE -updated_at END LIMIT ?', (*args, limit))
        for row in rows:
            row.pop('resume_input_version', None)
        return rows

    def manage(self, instance_id, item: DeliveryChange):
        from .decisions import RpDecision
        with self.database.transaction() as tx:
            instance = self.store.instance(instance_id)
            # 管理变更只终止旧模型，不影响其他长期事项的修订。
            tx.execute('UPDATE role_instances SET input_version=input_version+1,attempted_version=-1 WHERE id=?', (instance_id,))
            tx.execute("UPDATE rp_outbox SET status='cancelled',finished_at=?,error='管理员修改了计划。' WHERE instance_id=? AND status='pending' AND plan_kind<>'delivery'", (time(), instance_id))
            instance = self.store.instance(instance_id)
            runtime = RuntimeSettings.model_validate({k: v for k, v in self.store.settings().items() if k != 'revision'})
            decision = RpDecision(reply_mode='silent', deliveries=[item])
            self.validate(instance_id, decision, runtime)
            run_id = tx.execute("INSERT INTO rp_runs(instance_id,input_version,input_through,status,settings_json,trigger_kind,created_at,finished_at,decision_json) VALUES (?,?,?,'completed',?,'manual',?,?,?)",
                (instance_id, instance['input_version'], instance['handled_input_id'], encoded(runtime.model_dump()), time(), time(), decision.model_dump_json())).lastrowid
            run = dict(tx.execute('SELECT * FROM rp_runs WHERE id=?', (run_id,)).fetchone())
            self.commit(tx, run, decision, runtime)
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)', (time(), 'rp.delivery_managed', encoded({'instance_id': instance_id, 'operation': item.operation, 'id': item.id})))
        self.store.events.publish('runtime')
        return self.list(instance_id)

    def recycle(self, instance_id=None, page=1):
        if instance_id: self.store.instance(instance_id)
        scope, args = (' AND r.instance_id=?', (instance_id,)) if instance_id else ('', ())
        predicate = " EXISTS(SELECT 1 FROM rp_outbox o WHERE o.run_id=r.id AND " + RECYCLED + ")" + scope
        total = self.database.one('SELECT count(*) AS n FROM rp_runs r WHERE' + predicate, args)['n']
        runs = self.database.all('SELECT r.id,r.instance_id,r.input_through,r.created_at,r.trigger_kind FROM rp_runs r WHERE' + predicate + ' ORDER BY r.id DESC LIMIT 20 OFFSET ?', (*args, (page - 1) * 20))
        for run in runs:
            run['cancelled_count'] = self.database.one("SELECT count(*) AS n FROM rp_outbox o WHERE o.run_id=? AND " + RECYCLED, (run['id'],))['n']
            sent = self.database.one("SELECT text FROM rp_outbox WHERE run_id=? AND status='succeeded' ORDER BY position LIMIT 1", (run['id'],))
            run['preview'] = sent['text'][:160] if sent else '当前回复未送达'
            if run['trigger_kind'] == 'manual': run['preview'] = '管理操作（未直接回复）'
            row = self.database.one('SELECT parts_json FROM inbound_messages WHERE id=? AND instance_id=?', (run['input_through'], run['instance_id']))
            run['input'] = ''.join(part.get('text', '') for part in json.loads(row['parts_json']) if part.get('type') == 'text')[:300] if row and run['trigger_kind'] != 'manual' else ''
        return {'items': runs, 'page': page, 'page_size': 20, 'total': total}

    def recycle_detail(self, run_id):
        if not self.database.one("SELECT 1 FROM rp_outbox o WHERE o.run_id=? AND " + RECYCLED, (run_id,)):
            raise LookupError('此运行没有已回收计划。')
        return self.database.all("SELECT o.id,o.text,o.due_at,o.finished_at,o.status,o.error,o.plan_kind,o.parts_json,o.after_seconds,o.anchor_at FROM rp_outbox o WHERE o.run_id=? AND (o.plan_kind<>'delivery' OR o.status<>'cancelled' OR (" + RECYCLED + ")) ORDER BY o.position", (run_id,))
