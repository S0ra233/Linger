"""近期安排只提供预期和重评节点，时钟不会把安排变成已完成事实。"""
from datetime import datetime, timedelta
from time import time
from zoneinfo import ZoneInfo
import json

from .planning_models import ScheduleUpdate
from .store import encoded, invalidate
from ..storage.configuration import RevisionConflict


class Schedules:
    def __init__(self, store, extensions):
        self.store, self.database, self.extensions = store, store.database, extensions

    def get(self, instance_id):
        row = self.database.one('SELECT * FROM rp_schedules WHERE instance_id=?', (instance_id,))
        return {'revision': row['revision'], 'entries': json.loads(row['config_json']), 'intent_id': row['intent_id']} if row else {'revision': 0, 'entries': [], 'intent_id': None}

    def project(self, instance_id, *, now=None):
        policy, plan = self.extensions.policy(instance_id), self.get(instance_id)
        now = time() if now is None else now
        if not policy['schedules_enabled']:
            return {**plan, 'enabled': False, 'occurrences': [], 'conflicts': []}
        zone = ZoneInfo(policy['timezone'])
        today = datetime.fromtimestamp(now, zone).date()
        overrides = {(item['replaces_id'], item['exception_date']): item for item in plan['entries'] if item['kind'] == 'exception'}
        occurrences = []
        def valid(item):
            return all(self.store.information.evidence.is_valid(instance_id, ref['kind'], ref['id']) for ref in item['sources'])
        for item in plan['entries']:
            if item['kind'] == 'exception' or not valid(item):
                continue
            if item['kind'] == 'routine':
                spans = []
                for day in (today + timedelta(days=index) for index in range(8)):
                    if day.weekday() not in item['weekdays']:
                        continue
                    values = []
                    for field in ('local_start', 'local_end'):
                        local = datetime.fromisoformat(day.isoformat() + 'T' + item[field]).replace(tzinfo=zone, fold=0)
                        # 夏令时不存在的本地时间跳过，歧义时间采用第一次出现。
                        if datetime.fromtimestamp(local.timestamp(), zone).replace(tzinfo=None) != local.replace(tzinfo=None):
                            break
                        values.append(local.timestamp())
                    if len(values) == 2:
                        spans.append((day.isoformat(), *values))
            else:
                spans = [(datetime.fromtimestamp(item['starts_at'], zone).date().isoformat(), item['starts_at'], item['ends_at'])]
            for day, start, end in spans:
                exception = overrides.get((item['id'], day))
                source = item
                if exception and valid(exception):
                    if exception['cancelled']:
                        continue
                    start, end, source = exception['starts_at'], exception['ends_at'], exception
                if end < now or start > now + 8 * 86400:
                    continue
                occurrences.append({'id': source['id'], 'title': source['title'], 'kind': source['kind'],
                                    'starts_at': start, 'ends_at': end, 'status': 'expected_now' if start <= now < end else 'planned', 'sources': source['sources']})
        represented = {item['id'] for item in occurrences}
        by_id = {item['id']: item for item in plan['entries']}
        for exception in overrides.values():
            original = by_id[exception['replaces_id']]
            if (exception['id'] not in represented and not exception['cancelled'] and valid(exception) and valid(original)
                    and exception['ends_at'] >= now and exception['starts_at'] <= now + 8 * 86400):
                occurrences.append({'id': exception['id'], 'title': exception['title'], 'kind': 'exception',
                    'starts_at': exception['starts_at'], 'ends_at': exception['ends_at'],
                    'status': 'expected_now' if exception['starts_at'] <= now < exception['ends_at'] else 'planned', 'sources': exception['sources']})
        occurrences.sort(key=lambda item: (item['starts_at'], item['id']))
        conflicts = []
        for index, item in enumerate(occurrences):
            for other in occurrences[index + 1:]:
                if other['starts_at'] >= item['ends_at']:
                    break
                if len(conflicts) < 32:
                    conflicts.append([item['id'], other['id']])
        return {**plan, 'enabled': True, 'timezone': policy['timezone'], 'occurrences': occurrences[:64],
                'occurrences_limited': len(occurrences) > 64, 'conflicts': conflicts, 'meaning': '预期安排，未证明行动已完成；冲突交由角色判断'}

    def validate(self, instance_id, update, through=None, *, model=False, hours=None):
        plan = self.get(instance_id)
        if update.revision != plan['revision']:
            raise RevisionConflict('日程已修改，请读取当前版本。')
        if model and not self.extensions.policy(instance_id)['schedules_enabled']:
            raise ValueError('本线未启用模型日程。')
        if model and not update.sources:
            raise ValueError('模型修改日程（含删除）需要整体变更的 sources 依据。')
        self.store.information.evidence.normalize(instance_id, update.sources, through=through)
        horizon = (self.store.settings()['schedule_hours'] if hours is None else hours) * 3600
        now = time()
        previous = {item['id']: item for item in plan['entries']}
        for item in update.entries:
            if model and not item.sources and item.model_dump() != previous.get(item.id):
                raise ValueError('模型修改日程需要已有真实依据。')
            self.store.information.evidence.normalize(instance_id, item.sources, through=through)
            if item.starts_at is not None and item.starts_at > now + horizon or item.ends_at is not None and item.ends_at > now + horizon:
                raise ValueError('日程超出配置的计划范围。')

    def write(self, tx, instance_id, update, run_id=None):
        run = self.database.one('SELECT input_through,settings_json FROM rp_runs WHERE id=?', (run_id,)) if run_id else None
        self.validate(instance_id, update, run['input_through'] if run else None, model=run_id is not None,
                      hours=json.loads(run['settings_json'])['schedule_hours'] if run else None)
        payload, now = encoded([item.model_dump() for item in update.entries]), time()
        tx.execute('INSERT INTO rp_schedules VALUES (?,?,?,NULL,?) ON CONFLICT(instance_id) DO UPDATE SET config_json=excluded.config_json,revision=excluded.revision,updated_at=excluded.updated_at',
                   (instance_id, payload, update.revision + 1, now))
        tx.execute('INSERT INTO schedule_versions VALUES (?,?,?,?,?)', (instance_id, update.revision + 1, encoded({'entries': [item.model_dump() for item in update.entries], 'change_sources': [ref.model_dump() for ref in update.sources]}), run_id, now))

    def arm(self, tx, instance_id, run_id=None):
        plan = self.get(instance_id)
        old = self.database.one('SELECT status,due_at FROM life_intents WHERE id=?', (plan['intent_id'],)) if plan['intent_id'] else None
        policy = self.extensions.policy(instance_id)
        if not policy['schedules_enabled']:
            if old:
                tx.execute("UPDATE life_intents SET status='cancelled',revision=revision+1,error='日程已停用。' WHERE id=? AND status='pending'", (plan['intent_id'],))
            return
        config, now = self.store.life.settings(), time()
        boundary = min((at for item in self.project(instance_id, now=now)['occurrences'] for at in (item['starts_at'], item['ends_at'])
                        if now + max(30, 30) <= at <= now + self.store.settings()['schedule_hours'] * 3600 and not self.store.life.quiet(at, config)), default=None)
        if old and old['status'] == 'pending' and old['due_at'] == boundary:
            return
        if old:
            tx.execute("UPDATE life_intents SET status='cancelled',revision=revision+1,error='日程评估节点已更新。' WHERE id=? AND status='pending'", (plan['intent_id'],))
        intent_id = None
        if boundary is not None:
            intent_id = self.extensions.wake(tx, instance_id, boundary, '日程边界重评：仅是预期安排，不自动视为已经行动。', run_id)
        if plan['revision']:
            tx.execute('UPDATE rp_schedules SET intent_id=? WHERE instance_id=?', (intent_id, instance_id))

    def save(self, instance_id, body):
        with self.database.transaction() as tx:
            self.store.instance(instance_id)
            self.write(tx, instance_id, body)
            self.arm(tx, instance_id)
            invalidate(tx, instance_id, '管理员更新了近期日程。')
            self.store.information.audit(tx, 'schedule.saved', {'instance_id': instance_id, 'revision': body.revision + 1})
        self.store.events.publish('life')
        return self.project(instance_id)
