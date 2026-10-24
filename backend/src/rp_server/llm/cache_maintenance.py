"""缓存维护只消费不可变请求快照；没有工具分发、剧情提交或 QQ 发送入口。"""
import asyncio
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from time import time

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .providers.http import bearer_headers, post_json
from .providers.gemini import request_url as gemini_request_url
from .usage import AttemptUsage, normalize_usage
from .tool_policy import TOOL_POLICY_VERSION
from ..storage.configuration import RevisionConflict


class CachePolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    instance_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    connection_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    model_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    enabled: bool = False
    mode: str = Field(default='unsupported', pattern=r'^(unsupported|responses_prewarm|anthropic_prewarm|minimal|gemini_explicit)$')
    compatibility_confirmed: bool = False
    interval_seconds: int = Field(default=270, ge=1, le=604800)
    max_idle_seconds: int = Field(default=3600, ge=1, le=604800)
    daily_cap: int = Field(default=24, ge=1, le=1000)
    failure_limit: int = Field(default=2, ge=1, le=10)
    output_tokens: int = Field(default=16, ge=1, le=256)
    explicit_ttl_seconds: int = Field(default=600, ge=60, le=604800)
    active_start: str = Field(default='00:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    active_end: str = Field(default='00:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')

    @model_validator(mode='after')
    def combinations(self):
        if self.enabled and (not self.compatibility_confirmed or self.mode == 'unsupported'):
            raise ValueError('请先确认实际端点支持所选保活方式，且不会改变原目标缓存。')
        if self.max_idle_seconds < self.interval_seconds:
            raise ValueError('最长保留时长不能短于保活间隔。')
        return self


class CachePolicies(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=0)
    items: list[CachePolicy] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def unique(self):
        keys = [(item.instance_id, item.connection_id, item.model_id) for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError('同一生活线与模型不能重复配置保活。')
        return self


def model_fingerprint(snapshot):
    # 空配置也有应用默认策略，策略升级后不能继续维护旧auto资源。
    value = [snapshot.revision, snapshot.credential_ref, snapshot.model.model_dump(), snapshot.preset_config.model_dump(),
             asdict(snapshot.config().tool_policy), TOOL_POLICY_VERSION]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def scoped_snapshot(store, snapshot, instance_id):
    """与业务运行使用同一份生活线覆盖；不拿未覆盖配置比较真实缓存快照。"""
    return store.context.layered.apply(instance_id, snapshot) if store.context else snapshot


class CacheMaintenance:
    def __init__(self, store, models):
        self.store, self.models = store, models
        self.database, self.events = store.database, store.events
        self.targets, self.candidates = {}, {}
        self.tasks: dict[str, asyncio.Task] = {}
        self._last_poll = 0.0
        with self.database.transaction() as tx:
            tx.execute("UPDATE cache_calls SET status='unknown',finished_at=?,error='服务中断，未补跑。' WHERE status='running'", (time(),))

    def policies(self):
        row = self.database.one("SELECT * FROM configurations WHERE key='cache_keepalive'")
        return {'revision': row['revision'], 'items': json.loads(row['value_json'])} if row else {'revision': 0, 'items': []}

    def save(self, body):
        for item in body.items:
            self.store.instance(item.instance_id)
            snapshot = scoped_snapshot(self.store, self.models.snapshot(item.connection_id, item.model_id), item.instance_id)
            if snapshot.connection.is_embedding:
                raise ValueError('Embedding 不能进行对话缓存保活。')
            required = {'responses_prewarm': 'responses', 'anthropic_prewarm': 'anthropic_messages'}.get(item.mode)
            if required and snapshot.connection.protocol != required:
                raise ValueError('保活方式与接口协议不匹配。')
            if item.mode == 'gemini_explicit' and (snapshot.connection.protocol != 'gemini' or not snapshot.connection.options.gemini_explicit_cache_confirmed):
                raise ValueError('Gemini TTL 维护需要显式确认缓存资源能力。')
            if item.mode == 'anthropic_prewarm' and not snapshot.connection.options.cache_control:
                raise ValueError('Messages 原生预热需要原业务请求已启用缓存断点。')
        with self.database.transaction() as tx:
            if self.policies()['revision'] != body.revision:
                raise RevisionConflict('保活配置已变化，请刷新后重试。')
            tx.execute("INSERT INTO configurations VALUES ('cache_keepalive',?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,revision=excluded.revision,updated_at=excluded.updated_at",
                       (json.dumps([item.model_dump() for item in body.items]), body.revision + 1, time()))
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (time(), 'cache.settings_updated', json.dumps({'count': len(body.items)})))
        # 新设置不借旧快照自动联网，等待后续正常业务；正在请求的取消仍按真实尝试结算。
        self.targets.clear()
        self.candidates.clear()
        for task in self.tasks.values():
            if not task.done(): task.cancel()
        self.events.publish('models')
        return self.policies()

    def observes(self, instance_id, snapshot):
        return any(item['enabled'] and (item['instance_id'], item['connection_id'], item['model_id']) ==
                   (instance_id, snapshot.connection_id, snapshot.model.id) for item in self.policies()['items'])

    def capture(self, run_id, instance, snapshot, payload, usage=None):
        if not self.observes(instance['id'], snapshot): return
        # 图像大正文不无限复制进 RAM；超限保留明确的不可维护状态。
        encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        value = {'snapshot': snapshot, 'version': instance['input_version'], 'run_id': run_id,
                 'fingerprint': model_fingerprint(snapshot), 'payload': json.loads(encoded) if len(encoded.encode()) <= 2 * 1024 * 1024 else None,
                 'idle_at': None, 'next_at': None, 'failures': 0, 'status': '等待正常业务结束',
                 'last_attempt': None, 'last_delivery_id': 0,
                 'last_hit': time() if usage and usage.cache_read_tokens and usage.cache_read_tokens > 0 else None}
        if value['payload'] is None: value['status'] = '请求快照超过 2 MiB，已跳过'
        selection = payload.get('tool_choice')
        if snapshot.connection.protocol == 'anthropic_messages' and isinstance(selection, dict):
            # Messages使用对象形式的auto／none；不能把对象本身一律当作强制工具。
            selection = selection.get('type', 'unsupported')
        gemini_mode = payload.get('toolConfig', {}).get('functionCallingConfig', {}).get('mode')
        if selection not in (None, 'auto', 'none') or gemini_mode == 'ANY':
            # 不为保活撤销原生强制规则，也不向模型提交必然生成工具调用的维护请求。
            ttl_only = any(item['enabled'] and item['mode'] == 'gemini_explicit'
                and (item['instance_id'], item['connection_id'], item['model_id']) == (instance['id'], snapshot.connection_id, snapshot.model.id)
                for item in self.policies()['items'])
            if not ttl_only:
                value.update(payload=None, status='当前强制工具策略不适用于生成保活，未安排维护请求')
        self.candidates[run_id] = value

    def forget_instance(self, instance_id):
        """停止保活旧完整请求；服务商仍可保留未改前缀，不发送清缓存请求。"""
        self.targets.pop(instance_id, None)
        task = self.tasks.get(instance_id)
        if task:
            task.cancel()
        for run_id in list(self.candidates):
            if self.database.one('SELECT 1 FROM rp_runs WHERE id=? AND instance_id=?', (run_id, instance_id)):
                self.candidates.pop(run_id, None)

    def finish_business(self, run_id, accepted):
        value = self.candidates.pop(run_id, None)
        if value is not None and accepted:
            row = self.database.one('SELECT instance_id FROM rp_runs WHERE id=?', (run_id,))
            self.targets[row['instance_id']] = value
            value['scope_revision'] = self.events.revisions(('characters', 'materials', 'information', 'mcp', 'media', 'context'), instance_id=row['instance_id'])

    def catalog(self):
        def public(instance_id, value):
            return {'instance_id': instance_id, 'status': value['status'], 'next_at': value['next_at'],
                    'idle_at': value['idle_at'], 'last_attempt': value['last_attempt'], 'last_hit': value['last_hit'],
                    'coverage': '最近有效业务请求的原始前缀；下一轮共享范围由提供商决定',
                    'connection_id': value['snapshot'].connection_id, 'model_id': value['snapshot'].model.id}
        return {**self.policies(), 'targets': [public(key, value) for key, value in self.targets.items()],
                'instances': self.database.all('SELECT r.id,c.display_name,c.nickname,r.peer_id FROM role_instances r JOIN contacts c ON c.instance_id=r.id'),
                'calls': self.database.all('SELECT * FROM cache_calls ORDER BY id DESC LIMIT 50')}

    def poll(self, runtime, settings):
        now = time()
        if now < self._last_poll + 1: return
        self._last_poll = now
        policies = {(item['instance_id'], item['connection_id'], item['model_id']): item for item in self.policies()['items'] if item['enabled']}
        for instance_id, task in list(self.tasks.items()):
            if task.done():
                if not task.cancelled(): task.exception()
                del self.tasks[instance_id]
        foreground = bool(runtime.generations) or self.database.one('''SELECT 1 FROM role_instances r
            JOIN contacts c ON c.instance_id=r.id JOIN qq_accounts a ON a.id=r.account_id
            WHERE c.enabled=1 AND a.enabled=1 AND r.paused=0 AND r.attempted_version<>r.input_version
            AND EXISTS (SELECT 1 FROM inbound_messages m WHERE m.instance_id=r.id AND m.id>r.handled_input_id AND m.deleted_at IS NULL AND m.archived_at IS NULL) LIMIT 1''')
        if foreground:
            for task in self.tasks.values():
                if not task.done() and not task.cancelling(): task.cancel()
        for instance_id, value in list(self.targets.items()):
            snapshot = value['snapshot']
            policy = policies.get((instance_id, snapshot.connection_id, snapshot.model.id))
            instance = self.store.instance(instance_id)
            task = self.tasks.get(instance_id)
            try:
                current = scoped_snapshot(self.store, self.models.snapshot(snapshot.connection_id, snapshot.model.id), instance_id)
                applicable = (policy and model_fingerprint(current) == value['fingerprint']
                              and self.store.valid(instance_id, value['version'])
                              and value['scope_revision'] == self.events.revisions(('characters', 'materials', 'information', 'mcp', 'media', 'context'), instance_id=instance_id))
            except (ValueError, LookupError):
                applicable = False
            if not applicable:
                if task: task.cancel()
                value.update(status='设置或输入已变化，等待新业务快照', idle_at=None, next_at=None)
                continue
            # 短消息可能在两次poll之间开始并送达，不能只靠看到sending来重置计时。
            delivered = self.database.one('''SELECT coalesce(max(id),0) AS id FROM sent_messages
                WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL''', (instance_id,))['id']
            if delivered != value['last_delivery_id']:
                if task: task.cancel()
                value.update(last_delivery_id=delivered, idle_at=None, next_at=None)
            pending = self.database.one('''SELECT 1 FROM rp_outbox o JOIN rp_runs r ON r.id=o.run_id
                WHERE o.instance_id=? AND r.context_deleted_at IS NULL AND r.context_archived_at IS NULL
                AND (o.status IN ('sending','unknown') OR (o.status='pending'
                    AND (o.plan_kind='reply' OR (o.due_at<=? AND (o.plan_kind<>'followup' OR o.anchor_at IS NOT NULL)))))
                LIMIT 1''', (instance_id, now))
            busy = (instance_id in runtime.generations or pending or instance['handled_input_id'] <
                    (self.database.one('SELECT coalesce(max(id),0) AS id FROM inbound_messages WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL', (instance_id,))['id']))
            if busy or not settings.enabled:
                if task: task.cancel()
                value.update(status='正常业务优先，暂停计时', idle_at=None, next_at=None)
                continue
            if value['payload'] is None and policy['mode'] != 'gemini_explicit': continue
            if value['idle_at'] is None:
                value.update(idle_at=now, next_at=now + policy['interval_seconds'], status='空闲计时中')
            preferences = self.database.one("SELECT value_json FROM configurations WHERE key='preferences'")
            timezone = json.loads(preferences['value_json'])['timezone'] if preferences else 'Asia/Shanghai'
            current_time = datetime.now(ZoneInfo(timezone)).strftime('%H:%M')
            start, end = policy.get('active_start', '00:00'), policy.get('active_end', '00:00')
            in_window = start == end or (start <= current_time < end if start < end else current_time >= start or current_time < end)
            if not in_window:
                if task: task.cancel()
                value['status'] = '不在保活时段'
                continue
            if now - value['idle_at'] >= policy['max_idle_seconds'] or value['failures'] >= policy['failure_limit']:
                value.update(status='达到空闲时长或连续失败限制，已停止', next_at=None)
                continue
            if task or foreground or value['next_at'] is None or now < value['next_at']: continue
            if policy['mode'] == 'minimal':
                profile = snapshot.model.reasoning
                native = profile.effort_map.get(snapshot.model.reasoning_effort) if profile else None
                if ((profile and profile.min_output_tokens and policy['output_tokens'] < profile.min_output_tokens)
                        or profile and profile.adapter == 'anthropic_budget' and type(native) is int and native >= policy['output_tokens']
                        or profile and profile.adapter == 'anthropic_combined' and profile.thinking_budget is not None and profile.thinking_budget >= policy['output_tokens']):
                    value.update(status='维护输出预算与原模型约束冲突，已跳过', next_at=None)
                    continue
            count = self.database.one('SELECT count(*) AS n FROM cache_calls WHERE instance_id=? AND connection_id=? AND model_id=? AND created_at>?',
                                      (instance_id, snapshot.connection_id, snapshot.model.id, now - 86400))['n']
            if count >= policy['daily_cap']:
                value['status'] = '达到滚动 24 小时额度'
                continue
            if len(runtime.generations) + len(self.tasks) >= settings.concurrency: continue
            value.update(next_at=None, status='维护请求中', last_attempt=now)
            self.tasks[instance_id] = asyncio.create_task(self._perform(instance_id, value, policy), name=f'cache-{instance_id}')

    def _prepare(self, value, policy):
        snapshot = value['snapshot']
        payload = deepcopy(value['payload'])
        style, config = snapshot.connection.protocol, snapshot.config()
        mode = policy['mode']
        if mode == 'responses_prewarm':
            payload.setdefault('prompt_cache_options', {})['prewarm'] = True
        elif mode == 'anthropic_prewarm':
            payload['max_tokens'] = 0
        else:
            # 不改 tools、tool_choice 或思考；意外 tool call 也只作为维护结果丢弃。
            note = '缓存维护：不要调用工具、推进剧情或回复联系人；仅返回 <cache_keepalive/>。'
            output = payload.get('response_format')
            if style == 'responses':
                text = payload.get('text')
                output = text.get('format') if isinstance(text, dict) else None
            elif style == 'anthropic_messages':
                output = payload.get('output_config', {}).get('format')
            elif style == 'gemini' and payload.get('generationConfig', {}).get('responseMimeType') == 'application/json':
                output = {'type': 'json_schema' if payload['generationConfig'].get('responseJsonSchema') else 'json_object'}
            if isinstance(output, dict) and output.get('type') == 'json_object':
                note = '缓存维护：不要调用工具、推进剧情或回复联系人；仅返回JSON对象{"cache_keepalive":true}。'
            elif isinstance(output, dict) and output.get('type') == 'json_schema':
                # 保留原Schema和预算，不能为了维护标记破坏前缀；返回内容无论是否完整都不执行。
                note = '缓存维护：不调用工具、推进剧情或回复联系人。按现有Schema返回最小静默JSON计划：reply_mode为silent，messages、followups、deliveries为空数组，其余资料用允许的空值或默认值。此输出只统计用量，不执行。'
            if style == 'responses':
                payload['input'].append({'role': 'user', 'content': note})
                payload['max_output_tokens'] = policy['output_tokens']
            elif style == 'gemini':
                payload['contents'].append({'role': 'user', 'parts': [{'text': note}]})
                payload.setdefault('generationConfig', {})['maxOutputTokens'] = policy['output_tokens']
            else:
                payload['messages'].append({'role': 'user', 'content': note})
                field = 'max_tokens' if style == 'anthropic_messages' else config.provider_rules.output_limit_field
                payload[field] = policy['output_tokens']
        headers = {'x-api-key': snapshot.api_key, 'anthropic-version': '2023-06-01'} if style == 'anthropic_messages' else (
            {'x-goog-api-key': snapshot.api_key} if style == 'gemini' else bearer_headers(snapshot.api_key))
        if style == 'anthropic_messages' and snapshot.model.reasoning and snapshot.model.reasoning.effort_delivery == 'message':
            headers['anthropic-beta'] = 'mid-conversation-output-config-2026-07-01'
        url = config.endpoint_url
        if style == 'gemini': url = gemini_request_url(config)
        return payload, headers, url

    async def _perform(self, instance_id, value, policy):
        snapshot = value['snapshot']
        style = snapshot.connection.protocol
        with self.database.transaction() as tx:
            call_id = tx.execute('INSERT INTO cache_calls(instance_id,connection_id,model_id,model,protocol,created_at,status) VALUES (?,?,?,?,?,?,\'running\')',
                                 (instance_id, snapshot.connection_id, snapshot.model.id, snapshot.model.model, style, time())).lastrowid
        def save_usage(data):
            with self.database.transaction() as tx:
                tx.execute('UPDATE cache_calls SET usage_json=? WHERE id=?', (json.dumps(data), call_id))
        attempts = AttemptUsage(lambda count: save_usage({'attempts': count}))
        status, error = 'failed', None
        try:
            if policy['mode'] == 'gemini_explicit':
                from .explicit_cache import CacheOperation
                resource = self.database.one("SELECT * FROM explicit_caches WHERE instance_id=? AND connection_id=? AND model_id=? AND fingerprint=? AND status='active' AND expires_at>? ORDER BY created_at DESC LIMIT 1", (instance_id, snapshot.connection_id, snapshot.model.id, value['fingerprint'], time()))
                if not resource:
                    raise ValueError('当前没有可维护的显式缓存对象。')
                attempts.begin()
                await self.store.explicit_cache.operate(resource['id'], CacheOperation(revision=resource['revision'], action='extend', ttl_seconds=policy.get('explicit_ttl_seconds', 600)))
                fresh = self.database.one('SELECT status FROM explicit_caches WHERE id=?', (resource['id'],))
                if fresh['status'] != 'active':
                    raise ValueError('缓存 TTL 延长结果未确认。')
                status, error = 'extended', None
                value['status'], value['failures'] = '显式缓存 TTL 已确认延长', 0
                return
            payload, headers, url = self._prepare(value, policy)
            raw = await post_json(url, headers, payload, min(60, snapshot.connection.timeout_seconds), client=self.models.client_for(snapshot),
                                  connect_timeout=snapshot.connection.connect_timeout_seconds, max_attempts=1, on_attempt=attempts)
            usage = normalize_usage(raw.get('usageMetadata') if style == 'gemini' else raw.get('usage'), style, snapshot.connection.provider_kind)
            save_usage({'attempts': attempts.snapshot(), 'token_usage': asdict(usage) if usage else None})
            hit = usage is not None and usage.cache_read_tokens is not None and usage.cache_read_tokens > 0
            status = 'hit' if hit else 'miss' if usage is not None and usage.cache_read_tokens == 0 else 'unverified'
            value['failures'] = 0 if hit else value['failures'] + 1
            value['status'] = {'hit': '已报告缓存命中', 'miss': '未命中缓存', 'unverified': '请求已返回，缓存效果未知'}[status]
            if hit: value['last_hit'] = time()
        except asyncio.CancelledError:
            status, error = 'cancelled', '维护已取消；已经开始的尝试仍可能收费。'
            raise
        except Exception as exc:
            value['failures'] += 1
            value['status'] = '维护失败'
            error = f'维护失败（{type(exc).__name__}），未变更参数或重试。'
        finally:
            value['next_at'] = time() + policy['interval_seconds']
            with self.database.transaction() as tx:
                tx.execute('UPDATE cache_calls SET status=?,error=?,finished_at=? WHERE id=?', (status, error, time(), call_id))
            self.events.publish('models')

    async def close(self):
        for task in self.tasks.values():
            if not task.done() and not task.cancelling(): task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.targets.clear()
        self.candidates.clear()
