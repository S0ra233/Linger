"""模型连接管理、用途绑定与可取消的连接检查；不提供 Web 聊天。"""
import asyncio
from dataclasses import asdict, replace
import json
import logging
from time import time
from uuid import uuid4

import httpx

from ..attachments.store import ImageStore
from ..events import EventBus
from ..storage.configuration import RevisionConflict
from ..storage.credentials import Credentials
from ..storage.database import Database
from .presets import Presets
from .reasoning import resolve_effort
from .configuration import AssignmentsInput, CheckInput, ConnectionInput, ModelSnapshot
from .embedding import EmbeddingClient
from .messages import ModelMessage, TextContent
from .providers.factory import create_provider
from .providers.http import ProviderError
from .usage import AttemptUsage
from .endpoints import endpoint_base


logger = logging.getLogger('rp_server.models')
PURPOSES = ('narrator', 'summary', 'vision', 'embedding')


class MissingModel(ValueError):
    pass


class CheckLimit(ValueError):
    pass


class ModelService:
    def __init__(self, database: Database, credentials: Credentials, events: EventBus,
                 client: httpx.AsyncClient, images: ImageStore, *, gemini_proxy=None):
        self.database, self.credentials, self.events = database, credentials, events
        self.client, self.images = client, images
        self.gemini_proxy = gemini_proxy
        self.presets = Presets(database, events)
        self._checks: dict[str, asyncio.Task] = {}
        self._closing = False
        with database.transaction() as connection:
            connection.execute("""UPDATE model_checks SET status='unknown',finished_at=?,
                error='服务曾中断，检查结果未知；没有自动重发。'
                WHERE status IN ('queued','running','cancelling')""", (time(),))

    def client_for(self, snapshot: ModelSnapshot) -> httpx.AsyncClient:
        if self.gemini_proxy is None:
            return self.client
        return self.gemini_proxy.client_for(snapshot.config(), snapshot.api_key, self.client)

    def _row(self, connection_id: str) -> dict:
        row = self.database.one('SELECT * FROM model_connections WHERE id=?', (connection_id,))
        if row is None:
            raise MissingModel('连接不存在。')
        return row

    @staticmethod
    def _config(row: dict) -> ConnectionInput:
        return ConnectionInput.model_validate_json(row['config_json'])

    def _public(self, row: dict) -> dict:
        parsed = self._config(row)
        config = parsed.model_dump(exclude={'api_key', 'key_action', 'revision'})
        latest = self.checks(row['id'], limit=1)
        return {**config, 'id': row['id'], 'revision': row['revision'], 'archived': bool(row['archived']),
                'key_configured': row['credential_ref'] is not None, 'updated_at': row['updated_at'],
                'latest_check': latest[0] if latest else None,
                'resolved_endpoint_url': parsed.resolved_endpoint_url,
                'endpoint_base_url': (parsed.endpoint_url if parsed.endpoint_mode == 'base' else
                                      endpoint_base(parsed.endpoint_url, parsed.protocol))}

    def list_connections(self, include_archived: bool = False) -> list[dict]:
        rows = self.database.all('SELECT * FROM model_connections WHERE archived<=? ORDER BY created_at', (int(include_archived),))
        return [self._public(row) for row in rows]

    def save(self, body: ConnectionInput, connection_id: str | None = None) -> dict:
        previous = self._row(connection_id) if connection_id else None
        if previous and previous['archived']:
            raise ValueError('请先恢复归档连接。')
        previous_config = self._config(previous) if previous else None
        previous_ids = {model.id for model in previous_config.models} if previous_config else set()
        if any(model.id and model.id not in previous_ids for model in body.models):
            raise ValueError('模型条目不属于当前连接；新增条目应使用空编号。')
        if previous_config and previous['credential_ref'] and body.key_action == 'keep' and (
                previous_config.resolved_endpoint_url, previous_config.protocol, previous_config.provider_kind) != (
                body.resolved_endpoint_url, body.protocol, body.provider_kind):
            raise ValueError('地址、协议或提供商改变后，请重新填写密钥，或明确清除旧密钥。')
        connection_id = connection_id or uuid4().hex
        models = [model.model_copy(update={'id': model.id or uuid4().hex}) for model in body.models]
        if not body.is_embedding:
            models = [self.presets.materialize(model)[0].model_copy(update={'reasoning_effort': model.reasoning_effort}) for model in models]
        candidate = body.model_copy(update={'models': models})
        for model in models if not candidate.is_embedding else []:
            model = self.presets.materialize(model)[0]
            config = candidate.model_config_for(model)
            resolve_effort(config.reasoning, model.reasoning_effort,
                           temperature=model.temperature, max_output_tokens=model.max_output_tokens)
            if model.context_window is not None and model.max_output_tokens >= model.context_window:
                raise ValueError('最大输出必须小于模型能力窗口。')
        data = candidate.model_dump(exclude={'api_key', 'key_action', 'revision'})
        ciphertext = self.credentials.encrypt(body.api_key) if body.key_action == 'replace' else None
        reference = (uuid4().hex if ciphertext is not None else None if body.key_action == 'clear'
                     else previous['credential_ref'] if previous else None)
        now = time()
        with self.database.transaction() as connection:
            current = connection.execute('SELECT revision FROM model_connections WHERE id=?', (connection_id,)).fetchone()
            current_revision = current['revision'] if current else 0
            if current_revision != body.revision:
                raise RevisionConflict('连接已被其他页面更新，请重新载入。')
            if not current and connection.execute('SELECT count(*) FROM model_connections').fetchone()[0] >= 100:
                raise ValueError('连接数量已达 100，请整理现有连接。')
            bindings = self.assignments()
            available_ids = {model.id for model in models}
            for purpose in PURPOSES:
                binding = bindings[purpose]
                if binding and binding['connection_id'] == connection_id:
                    model = next((item for item in models if item.id == binding['model_id']), None)
                    if binding['model_id'] not in available_ids or model is None:
                        raise ValueError('有模型仍用于用途分配，请先解除绑定再移除。')
                    self._validate_binding(purpose, candidate, model)
            if ciphertext is not None:
                connection.execute('INSERT INTO encrypted_secrets VALUES (?,?,?,?)', (reference, ciphertext, now, now))
            connection.execute('''INSERT INTO model_connections(id,config_json,credential_ref,revision,created_at,updated_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json,
                credential_ref=excluded.credential_ref,revision=excluded.revision,updated_at=excluded.updated_at''',
                               (connection_id, json.dumps(data, ensure_ascii=False), reference, current_revision + 1, now, now))
            self._audit(connection, 'models.saved', {'connection_id': connection_id, 'revision': current_revision + 1})
        self.events.publish('models')
        return self._public(self._row(connection_id))

    def set_archived(self, connection_id: str, revision: int, archived: bool):
        with self.database.transaction() as connection:
            row = self._row(connection_id)
            if row['revision'] != revision:
                raise RevisionConflict('连接已更新，请重新载入。')
            if archived and any(binding and binding['connection_id'] == connection_id
                                for name, binding in self.assignments().items() if name in PURPOSES):
                raise ValueError('请先解除此连接的用途分配，再归档。')
            connection.execute('UPDATE model_connections SET archived=?,revision=revision+1,updated_at=? WHERE id=?',
                               (int(archived), time(), connection_id))
            self._audit(connection, 'models.archived' if archived else 'models.restored', {'connection_id': connection_id})
        self.events.publish('models')
        return self._public(self._row(connection_id))

    def assignments(self) -> dict:
        row = self.database.one("SELECT * FROM configurations WHERE key='model_assignments'")
        if row is None:
            return {'revision': 0, **{purpose: None for purpose in PURPOSES}}
        return {**json.loads(row['value_json']), 'revision': row['revision']}

    @staticmethod
    def _validate_binding(purpose, config, model):
        if (purpose == 'embedding') != config.is_embedding:
            raise ValueError('Embedding 与生成模型的用途不能混用。')
        if purpose == 'vision' and model.image_input is not True:
            raise ValueError('视觉用途需要明确启用图片输入的模型。')

    def save_assignments(self, body: AssignmentsInput) -> dict:
        with self.database.transaction() as connection:
            if self.assignments()['revision'] != body.revision:
                raise RevisionConflict('用途分配已被其他页面更新，请重新载入。')
            for purpose in PURPOSES:
                binding = getattr(body, purpose)
                if binding:
                    row = self._row(binding.connection_id)
                    config = self._config(row)
                    if row['archived'] or not config.enabled:
                        raise ValueError('不能分配已归档或停用的连接。')
                    model = next((model for model in config.models if model.id == binding.model_id), None)
                    if model is None:
                        raise MissingModel('绑定的模型条目不存在。')
                    self._validate_binding(purpose, config, model)
            data = body.model_dump(exclude={'revision'})
            connection.execute('''INSERT INTO configurations VALUES ('model_assignments',?,?,?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                revision=excluded.revision,updated_at=excluded.updated_at''',
                               (json.dumps(data), body.revision + 1, time()))
            self._audit(connection, 'models.assignments_saved', {'revision': body.revision + 1})
        self.events.publish('models')
        return {**data, 'revision': body.revision + 1}

    def snapshot(self, connection_id: str, model_id: str, *, revision: int | None = None) -> ModelSnapshot:
        row = self._row(connection_id)
        if revision is not None and row['revision'] != revision:
            raise RevisionConflict('配置已更新，请使用当前连接配置重新操作。')
        config = self._config(row)
        if row['archived'] or not config.enabled:
            raise ValueError('连接已归档或停用。')
        model = next((model for model in config.models if model.id == model_id), None)
        if model is None:
            raise MissingModel('模型条目不存在。')
        model, preset_config = self.presets.materialize(model)
        key = self.credentials.read(row['credential_ref']) if row['credential_ref'] else ''
        return ModelSnapshot(connection_id, row['revision'], config, model, row['credential_ref'], key, preset_config)

    def for_purpose(self, purpose: str) -> ModelSnapshot:
        if purpose not in PURPOSES:
            raise ValueError('模型用途不存在。')
        binding = self.assignments()[purpose]
        if binding is None:
            raise ValueError('尚未为此用途分配模型。')
        snapshot = self.snapshot(binding['connection_id'], binding['model_id'])
        self._validate_binding(purpose, snapshot.connection, snapshot.model)
        return snapshot

    def summary(self):
        rows = self.database.all('SELECT config_json FROM model_connections WHERE archived=0')
        assignments = self.assignments()
        return {'connections': len(rows), 'enabled': sum(json.loads(row['config_json'])['enabled'] for row in rows),
                'assigned': sum(assignments[purpose] is not None for purpose in PURPOSES)}

    def checks(self, connection_id: str | None = None, *, limit: int = 30) -> list[dict]:
        where, parameters = ('WHERE connection_id=?', (connection_id, limit)) if connection_id else ('', (limit,))
        rows = self.database.all(f'SELECT * FROM model_checks {where} ORDER BY created_at DESC LIMIT ?', parameters)
        return [{**{key: value for key, value in row.items() if key != 'result_json'},
                 'result': json.loads(row['result_json']) if row['result_json'] else None} for row in rows]

    def check(self, check_id: str) -> dict:
        row = self.database.one('SELECT * FROM model_checks WHERE id=?', (check_id,))
        if row is None:
            raise MissingModel('检查记录不存在。')
        return {**{key: value for key, value in row.items() if key != 'result_json'},
                'result': json.loads(row['result_json']) if row['result_json'] else None}

    def start_check(self, connection_id: str, body: CheckInput) -> dict:
        if self._closing or len(self._checks) >= 2:
            raise CheckLimit('连接检查最多同时进行 2 个，请稍后再试。')
        snapshot = self.snapshot(connection_id, body.model_id, revision=body.revision)
        if body.kind == 'plan' and (snapshot.connection.is_embedding or snapshot.model.tool_input is not True):
            raise ValueError('计划格式检查需要生成模型，并在模型设置中明确启用工具能力。')
        check_id = uuid4().hex
        with self.database.transaction() as connection:
            connection.execute('''INSERT INTO model_checks(id,connection_id,model_id,connection_revision,status,created_at,result_json)
                VALUES (?,?,?,?,'queued',?,?)''', (check_id, connection_id, body.model_id, body.revision, time(), json.dumps({'check_kind': body.kind})))
            self._audit(connection, 'models.check_started', {'check_id': check_id, 'connection_id': connection_id, 'kind': body.kind})
        task = asyncio.create_task(self._perform_check(check_id, snapshot, body.stream, body.kind), name=f'model-check:{check_id}')
        self._checks[check_id] = task
        task.add_done_callback(lambda completed: self._check_done(check_id, completed))
        self.events.publish('models')
        return self.check(check_id)

    def _check_done(self, check_id: str, task: asyncio.Task):
        self._checks.pop(check_id, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error('模型检查记录结算失败，编号 %s，类型 %s', check_id, type(task.exception()).__name__)

    def _finish_check(self, check_id: str, status: str, result: dict | None = None, error: str | None = None):
        with self.database.transaction() as connection:
            previous = connection.execute('SELECT result_json FROM model_checks WHERE id=?', (check_id,)).fetchone()
            saved = json.loads(previous['result_json']) if previous and previous['result_json'] else {}
            result = {**saved, **(result or {})} or None
            connection.execute('UPDATE model_checks SET status=?,result_json=?,error=?,finished_at=? WHERE id=?',
                               (status, json.dumps(result, ensure_ascii=False) if result is not None else None, error, time(), check_id))
        self.events.publish('models')

    async def _perform_check(self, check_id: str, snapshot: ModelSnapshot, stream: bool, kind: str = 'connection'):
        result = None
        request_details = {}
        try:
            model = {'connection_id': snapshot.connection_id, 'model_id': snapshot.model.id,
                     'model': snapshot.model.model, 'protocol': snapshot.connection.protocol, 'revision': snapshot.revision}
            def save_attempts(value):
                with self.database.transaction() as tx:
                    tx.execute('UPDATE model_checks SET result_json=? WHERE id=?',
                               (json.dumps({'check_kind': kind, 'model': model, **request_details, 'attempts': value}), check_id))
            attempts = AttemptUsage(save_attempts)
            with self.database.transaction() as connection:
                connection.execute("UPDATE model_checks SET status='running' WHERE id=?", (check_id,))
            self.events.publish('models')
            async with asyncio.timeout(snapshot.connection.timeout_seconds):
                if snapshot.connection.is_embedding:
                    options = snapshot.embedding_options()
                    attempts.begin()
                    async with EmbeddingClient(options, client=self.client_for(snapshot)) as embedder:
                        vectors, usage = await embedder.embed(['connection check'], purpose='query')
                    result = {'dimensions': len(vectors[0]), 'usage': usage, 'completion_status': 'completed',
                              'embedding_space': options.fingerprint()}
                else:
                    # 两类检查都只接收固定输入；计划检查只解析，不执行工具或修复重发。
                    provider = create_provider(replace(snapshot.config(), max_attempts=1), snapshot.api_key,
                                               client=self.client_for(snapshot), attachments=self.images)
                    request = snapshot.request([ModelMessage('user', [TextContent('Reply with OK only.')])])
                    if kind == 'plan':
                        from ..roleplay.plan_probe import probe_request
                        request = probe_request(snapshot)
                        request_details['tool_policy'] = request.tool_policy_trace
                    if snapshot.model.reasoning is None:
                        # 普通连接检查只需短答，避免沿用RP大输出上限挤占本地小池。
                        # 已配置思考的模型保留原预算，避免破坏其最小输出／思考组合。
                        request = replace(request, max_output_tokens=min(snapshot.model.max_output_tokens, 512 if kind == 'plan' else 64))
                    response = (await provider.stream_complete(request, lambda _: None, on_attempt=attempts) if stream
                                else await provider.complete(request, on_attempt=attempts))
                    text = response.text.replace(snapshot.api_key, '[redacted]') if snapshot.api_key else response.text
                    reason = response.finish_reason
                    if reason and snapshot.api_key:
                        reason = reason.replace(snapshot.api_key, '[redacted]')
                    result = {'text': text[:1000], 'finish_reason': reason[:200] if reason else reason,
                              'completion_status': response.completion_status,
                              'usage': asdict(response.token_usage) if response.token_usage else None,
                              'tool_calls': len(response.tool_calls)}
                    if kind == 'plan':
                        from ..roleplay.plan_probe import inspect_probe
                        from ..roleplay.store import diagnostic
                        result['format_check'] = diagnostic(inspect_probe(response, json_plan=request.output_format is not None), snapshot.api_key)
                        result['max_output_tokens'] = request.max_output_tokens
                        result['tool_policy'] = request.tool_policy_trace
            status = ('succeeded' if result['format_check']['passed'] else
                      'incomplete' if result['completion_status'] not in {'completed', 'requires_tool'} else 'failed') if kind == 'plan' else (
                      'succeeded' if result['completion_status'] == 'completed' else 'incomplete')
            self._finish_check(check_id, status, result,
                               '最小计划格式未通过；查看返回内容与字段问题。' if kind == 'plan' and status != 'succeeded' else None)
        except asyncio.CancelledError:
            self._finish_check(check_id, 'cancelled', error='检查已取消；实际请求用量可能未知。')
            raise
        except Exception as exc:
            if isinstance(exc, (ProviderError, ValueError)):
                message = str(exc)
            elif isinstance(exc, TimeoutError):
                message = '检查超时，没有自动重发。'
            else:
                message = f'检查未完成（{type(exc).__name__}）。'
            if snapshot.api_key:
                message = message.replace(snapshot.api_key, '[redacted]')
            self._finish_check(check_id, 'failed', result={**request_details, **(result or {})}, error=message[:500])

    def cancel_check(self, check_id: str):
        record = self.check(check_id)
        task = self._checks.get(check_id)
        if task is not None and not task.done():
            # 未获得执行机会的 Task 取消后不会进入 try/finally，先保留明确终态。
            task.cancel()
            self._finish_check(check_id, 'cancelled', error='检查已取消；实际请求用量可能未知。')
        return record if task is None else self.check(check_id)

    async def close(self):
        self._closing = True
        tasks = list(self._checks.items())
        for check_id, task in tasks:
            if not task.done():
                task.cancel()
                try:
                    self._finish_check(check_id, 'cancelled', error='服务关闭，检查已取消；用量可能未知。')
                except Exception as exc:
                    logger.error('关闭时检查状态未能保存，编号 %s，类型 %s', check_id, type(exc).__name__)
        await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)

    @staticmethod
    def _audit(connection, action: str, details: dict):
        connection.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                           (time(), action, json.dumps(details)))
