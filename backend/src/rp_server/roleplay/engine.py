"""RP 工具循环：完整参数校验；本地状态只在有效计划提交时改变。"""
from dataclasses import asdict
from datetime import datetime
import json
from time import time
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from ..llm.messages import ModelMessage, TextContent, ToolResult, ToolSchema
from ..llm.providers.factory import create_provider
from ..llm.usage import AttemptUsage
from ..llm.providers.http import payload_observer, payload_transformer
from ..llm.providers.reasoning import reasoning_fields
from ..llm.tool_policy import ToolChoice, apply_rp_tool_policy, strict_parameters, decode_strict_arguments, uses_json_plan
from ..llm.structured_output import json_plan_format
from ..llm.providers.retry import ProviderError, EmptyResponseError, allow_rate_limit_retry
from .decisions import RpDecision, RuntimeSettings
from .store import RpStore, StaleRun, diagnostic, encoded
from .message_text import without_comments
from .plan_contract import (MISSING_PLAN_REPAIR, PLAN_VALIDATION_REPAIR, JSON_PLAN_REPAIR,
                            build_contract, compact_parameters, parse_json_plan, parse_tool_arguments,
                            validate_plan_fields, describe_contract)
from .run_explanation import capture_versions
from .run_diagnostics import RunError, RunIssue, RunProgress, ToolRequestTrace, field_issues
from ..information.models import SearchInput, ReadInput
from ..information.evidence_models import EvidenceReadInput, EvidenceListInput, ConversationWindowInput
from .life_models import LifeReadInput
from .tool_dispatch import RpToolDispatcher, ToolScope


class RpEngine:
    def __init__(self, store: RpStore, models, context):
        self.store, self.models, self.context_service = store, models, context

    def clock(self):
        row = self.store.database.one("SELECT value_json FROM configurations WHERE key='preferences'")
        timezone = json.loads(row['value_json'])['timezone'] if row else 'Asia/Shanghai'
        return {'server_time': time(), 'management_timezone': timezone,
                'server_datetime': datetime.now(ZoneInfo(timezone)).isoformat(timespec='seconds')}

    def tools_for_policy(self, policy, schema=None):
        return [ToolSchema(tool.name, tool.description, compact_parameters(tool.parameters))
                for tool in self.tools(schema) if policy.allow_information_tools
                or tool.name not in {'rp_search_information', 'rp_read_information', 'rp_read_conversation'}]

    def contract(self, instance_id, policy, remote_schemas=(), *, config=None):
        extensions = self.store.extensions.policy(instance_id) if self.store.extensions else {}
        json_plan = bool(config and uses_json_plan(config))
        schema, instructions = build_contract(policy, extensions, mcp=bool(remote_schemas), json_plan=json_plan,
            strict_json=json_plan and json_plan_format(config) == 'json_schema')
        tools = self.tools_for_policy(policy, schema)
        if json_plan:
            tools = [tool for tool in tools if tool.name != 'submit_rp_plan']
        return tools + list(remote_schemas), instructions, schema

    @staticmethod
    def tools(schema=None):
        return [ToolSchema('rp_read_state', '读取当前生活线状态和服务器时间；只读不改变状态。',
                           {'type': 'object', 'properties': {}, 'additionalProperties': False}),
                ToolSchema('submit_rp_plan', '提交本轮完整计划并结束：messages为台词，followups为无新输入后续，deliveries为跨聊天安排。须独占调用；普通正文不投递。',
                           schema if schema is not None else RpDecision.model_json_schema()),
                ToolSchema('rp_search_information', '资料不足时查找本生活线的有效记忆及授权资料；数量受对话策略限制，不改变状态。', SearchInput.model_json_schema()),
                ToolSchema('rp_read_information', '读取仍在当前授权范围和有效版本中的资料片段；旧编号不能越权读取。', ReadInput.model_json_schema()),
                ToolSchema('rp_read_conversation', '根据本线input或sent来源编号，按需读取附近完整对话轮次。默认前后各2轮；超预算或含失效来源的整轮略过并标明，不返回图片内容。', ConversationWindowInput.model_json_schema()),
                ToolSchema('rp_read_evidence', '读取本生活线的原始输入、确认送达台词或有效生活事件；不接受待发编号。', EvidenceReadInput.model_json_schema()),
                ToolSchema('rp_list_evidence', '分页查本线来源，返回真实编号与语义；before使用上页next_before。', EvidenceListInput.model_json_schema()),
                ToolSchema('rp_list_media', '按名称或说明查找当前生活线已授权的QQ表情包／图片；不发送。',
                           {'type': 'object', 'properties': {'query': {'type': 'string', 'maxLength': 100}}, 'additionalProperties': False}),
                ToolSchema('rp_read_life_events', '回看本生活线已采纳的生活片段；before为上页next_before，每条最多1000字符。不是新输入或投递事实。', LifeReadInput.model_json_schema())]

    def validate_plan(self, raw: dict, instance_id: str, through: int, settings: RuntimeSettings, run_id: int, *, schema=None):
        if schema is not None:
            validate_plan_fields(raw, schema)
        # 仅原生提交边界要求显式列表；管理员内部的静默决策仍保留旧构造兼容。
        if 'messages' not in raw:
            raise ValueError('messages为必填列表；reply时填写实际台词，silent时显式填写[]。')
        decision = RpDecision.model_validate(raw)
        if settings.hide_html_comments:
            # 只投影已经通过结构校验的台词；原始工具参数仍保存在步骤响应中。
            cleaned = decision.model_dump()
            for message in [*cleaned['messages'], *cleaned['followups'], *cleaned['deliveries']]:
                if message.get('operation') in {'keep', 'cancel'}:
                    continue
                if '<!--' in message['text']:
                    message['text'] = without_comments(message['text'])
                for part in message['parts']:
                    if part['type'] == 'text' and '<!--' in part['text']:
                        part['text'] = without_comments(part['text'])
                message['parts'] = [part for part in message['parts'] if part['type'] != 'text' or part['text'].strip()]
                if not message['text'].strip() and not message['parts']:
                    raise ValueError('移除HTML草稿注释后气泡没有实际台词；请重新提交正文，不得用注释代替回复。')
            decision = RpDecision.model_validate(cleaned)
        decision.schedule(time(), settings.schedule_hours)
        for message in [*decision.messages, *decision.followups, *decision.deliveries]:
            if message.parts:
                if self.store.media is None: raise ValueError('素材服务未接通。')
                self.store.media.validate_parts(instance_id, message.parts)
        allow_current = bool(decision.life_fragment.strip() or decision.state_changes)
        self.context_service.information.validate_candidates(instance_id, through, decision.memory_candidates, allow_current=allow_current)
        self.context_service.information.evidence.validate_scene(instance_id, decision.scene, through, settings.schedule_hours, allow_current)
        if self.store.adjustments:
            self.store.adjustments.validate(instance_id, through, decision.character_adjustment_proposal)
        if self.store.extensions:
            self.store.extensions.validate(self.store.database.one('SELECT * FROM rp_runs WHERE id=?', (run_id,)), decision)
        state = {**self.store.state(instance_id)['state'], **decision.state_changes}
        if len(encoded(state).encode('utf-8')) > 32000:
            raise ValueError('角色状态超出 32000 字节预算。')
        self.store.life.validate(self.store.database.one('SELECT * FROM rp_runs WHERE id=?', (run_id,)), decision, settings)
        return decision

    async def run(self, run_id: int, instance: dict, through: int, settings: RuntimeSettings, *, progress=None):
        progress = progress or RunProgress()
        async with self.context_service.mcp.scope(instance, run_id) as remote:
            try:
                decision = await self._run(run_id, instance, through, settings, remote, progress=progress)
            except BaseException:
                # 只记位置并原样传播；不让外层资源清理耗时混入已中断阶段。
                progress.freeze()
                raise
            # 正常完成后才标关闭资源；异常展开时保留原中断位置。
            progress.enter('cleanup')
        return decision

    async def _run(self, run_id, instance, through, settings, remote, *, progress=None):
        progress = progress or RunProgress()
        progress.enter('setup')
        if not self.store.valid(instance['id'], instance['input_version'], run_id):
            raise StaleRun()
        try:
            snapshot = self.models.for_purpose('narrator')
            snapshot = self.context_service.layered.apply(instance['id'], snapshot)
        except ValueError as exc:
            # 配置解析也可能涉及密钥，不把原异常文本直接写入运行。
            raise RunError('主叙事配置或凭据不可用；请检查模型用途、连接启用状态、预设及密钥。') from exc
        if snapshot.model.tool_input is not True:
            raise RunError('请在主叙事模型中明确启用工具能力。')
        self.store.model_snapshot(run_id, snapshot, capture_versions(instance, snapshot,
            self.context_service.layered.settings(instance['id'], snapshot.preset_config.context)))
        clock = {**self.clock(), 'life': self.store.life.run_context(run_id, instance, through)}
        config = snapshot.config()
        json_plan = uses_json_plan(config)
        tools, instructions, schema = self.contract(instance['id'], snapshot.preset_config.context, remote.schemas, config=config)
        strict_projection = config.tool_policy.schema_mode == 'strict' and config.api_style in {'chat_completions', 'responses'}
        if strict_projection:
            try:
                for tool in tools: strict_parameters(tool.parameters)
            except ValueError as exc:
                raise RunError(str(exc), code='configuration', stage='setup') from exc
        retrieval = self.context_service.information.semantic.capture(snapshot.preset_config.context.retrieval_mode)
        progress.enter('context')
        try:
            messages, image_count, policy = await self.context_service.prepare(run_id, instance, through, settings, snapshot,
                tools, instructions, clock, retrieval, plan_schema=schema)
        except ValueError as exc:
            raise RunError(str(exc), code='context_invalid', stage='context') from exc
        if image_count and snapshot.model.image_input is not True:
            raise RunError('本轮包含图片，请为主叙事选择明确支持图片的模型。')
        progress.enter('setup')
        provider = create_provider(snapshot.config(), snapshot.api_key, client=self.models.client_for(snapshot), attachments=self.models.images)
        dispatcher = RpToolDispatcher(self.store, self.context_service,
            ToolScope(instance=instance, through=through, run_id=run_id, policy=policy,
                      retrieval=retrieval, run_clock=clock), remote, clock=self.clock,
            validate_plan=lambda raw: self.validate_plan(raw, instance['id'], through, settings, run_id, schema=schema))
        seen, used, repairs = set(), 0, 0
        continuation = None
        repair_submission = False
        for number in range(1, settings.max_steps + 1):
            progress.enter('context', step=number, repair_attempt=repairs)
            if not self.store.valid(instance['id'], instance['input_version'], run_id):
                raise StaleRun()
            request = snapshot.request(messages, tools)
            tool_policy_trace = apply_rp_tool_policy(config, request, repair=repair_submission, schema=schema)
            if (repair_submission and self.store.explicit_cache
                    and self.store.explicit_cache.selection_locked(instance['id'], snapshot)):
                # 缓存对象绑定了toolConfig，只回到本模型的常规选择，不改定义或思考。
                normal_selection = apply_rp_tool_policy(config, request, repair=False, schema=schema)
                tool_policy_trace.update(effective=normal_selection['effective'],
                                         native=normal_selection['native'],
                                         reason='修复沿用常规工具选择以兼容显式缓存；是否复用仍按实际前缀核对。')
                request.tool_policy_trace = tool_policy_trace
            budget = self.context_service.measure(messages, snapshot, tools, settings, policy,
                instance_id=instance['id'], run_id=run_id, continuation=continuation, request=request)
            budget['plan_contract'] = describe_contract(tools, schema=schema, output='json' if json_plan else 'tool')
            if budget['over_budget']:
                self.context_service.record_blocked_budget(run_id, number, budget)
                raise RunError('本步上下文超预算；工具调用链未截断，未发送请求。', code='context_budget', stage='context', step=number, repair_attempt=repairs)
            step_id = self.store.start_step(run_id, number, request, snapshot.api_key, budget=budget, repair_attempt=repairs)
            # JSON和DeepSeek路径记录实际控制字段，区分配置意图与接口真实发送值。
            wire = ToolRequestTrace(f'rp-{run_id}-{step_id}') if json_plan or config.provider_kind == 'deepseek' else None
            if wire is not None:
                tool_policy_trace['wire'] = wire.controls
            def save_attempts(value):
                with self.store.database.transaction() as tx:
                    tx.execute('UPDATE rp_steps SET response_json=? WHERE id=?',
                               (encoded(diagnostic({'context_budget': budget, 'repair_attempt': repairs, 'tool_policy': tool_policy_trace, 'attempts': value}, snapshot.api_key)), step_id))
            attempts = AttemptUsage(save_attempts, record_events=True)
            prepared = {}
            observe = bool(self.store.cache and self.store.cache.observes(instance['id'], snapshot) or self.store.explicit_cache and self.store.explicit_cache.observes(snapshot))
            observer_token = payload_observer.set(lambda payload: prepared.update(payload=payload)) if observe else None
            def transform(payload):
                value = self.store.explicit_cache.apply(instance['id'], snapshot, payload)
                prepared['explicit_cache_name'] = value.get('cachedContent')
                return value
            transform_token = payload_transformer.set(transform) if self.store.explicit_cache and self.store.explicit_cache.observes(snapshot) else None
            retry_token = allow_rate_limit_retry.set(True)
            progress.enter('provider', step=number, step_id=step_id, repair_attempt=repairs)
            try:
                response = await provider.complete(request, on_attempt=attempts, **({'trace': wire} if wire else {}))
            except ProviderError as exc:
                # ProviderError已经将厂商正文转换为可展示错误，再过滤本连接密钥。
                error = diagnostic(str(exc), snapshot.api_key)[:1000]
                issue = RunIssue(code='empty_response' if isinstance(exc, EmptyResponseError) else 'provider_request', stage='provider', message=error,
                    step=number, step_id=step_id, repair_attempt=repairs)
                failure = {'context_budget': budget, 'repair_attempt': repairs, 'tool_policy': tool_policy_trace, 'validation_issues': [issue.model_dump()], 'error': error, 'error_kind': type(exc.__cause__).__name__ if exc.__cause__ else 'ProviderError',
                           'attempts': attempts.snapshot()}
                if isinstance(exc, EmptyResponseError):
                    failure.update(token_usage=exc.token_usage, provider_response=exc.response_summary)
                with self.store.database.transaction() as tx:
                    tx.execute("UPDATE rp_steps SET response_json=?,status='failed',finished_at=? WHERE id=? AND status='requesting'",
                               (encoded(diagnostic(failure, snapshot.api_key)), time(), step_id))
                raise RunError(error, code=issue.code, stage='provider', step=number, step_id=step_id, repair_attempt=repairs) from exc
            finally:
                allow_rate_limit_retry.reset(retry_token)
                if observer_token is not None: payload_observer.reset(observer_token)
                if transform_token is not None: payload_transformer.reset(transform_token)
            progress.enter('response', step=number, step_id=step_id, repair_attempt=repairs)
            if self.store.cache and prepared and not repair_submission and self.store.valid(instance['id'], instance['input_version'], run_id):
                self.store.cache.capture(run_id, instance, snapshot, prepared['payload'], response.token_usage)
            if self.store.explicit_cache and prepared and not repair_submission and self.store.valid(instance['id'], instance['input_version'], run_id):
                self.store.explicit_cache.capture(run_id, instance, snapshot, prepared['payload'])
            projection = {'context_budget': budget, 'repair_attempt': repairs, 'tool_policy': tool_policy_trace,
                          'plan_output': 'json' if json_plan else 'tool',
                          'text': response.text[:100000], 'completion_status': response.completion_status,
                          'attempts': attempts.snapshot(),
                          'resolved_reasoning': reasoning_fields(snapshot.config(), request),
                          'reasoning_placement': {'delivery': snapshot.model.reasoning.effort_delivery,
                              'fixed_baseline': snapshot.model.reasoning.baseline_effort} if snapshot.model.reasoning else None,
                          'explicit_cache_name': prepared.get('explicit_cache_name'),
                          'finish_reason': response.finish_reason, 'usage': response.usage,
                          'token_usage': asdict(response.token_usage) if response.token_usage else None,
                          'tool_calls': [{**asdict(call), 'arguments': call.arguments[:64000],
                                          'arguments_truncated': len(call.arguments) > 64000}
                                         for call in response.tool_calls[:32]]}
            # 完整响应先保存，工具中的等待／取消不能抹掉已经收到的用量。
            if wire is not None and config.api_style == 'anthropic_messages':
                blocks = (response.native_data or {}).get('content', [])
                projection['native_content_types'] = [block['type'][:32] for block in blocks[:64]
                    if isinstance(block, dict) and isinstance(block.get('type'), str)]
            projection['tool_execution_status'] = 'unsettled' if response.tool_calls else 'not_requested'
            projection['tools_completed'] = 0
            projection['validation_issues'] = []
            if not response.tool_calls and not json_plan:
                projection['validation_issues'].append(RunIssue(code='missing_plan', stage='response',
                    message='本步没有原生计划工具调用，未据此投递台词。', step=number, step_id=step_id,
                    repair_attempt=repairs).model_dump())
                if request.tool_choice == 'required' or isinstance(request.tool_choice, ToolChoice):
                    projection['validation_issues'].append(RunIssue(code='tool_selection', stage='response',
                        message='接口在原生必调工具约束下仍返回普通正文；未当作计划执行。',
                        step=number, step_id=step_id, repair_attempt=repairs).model_dump())
            self.store.finish_step(step_id, projection, [], snapshot.api_key)
            if not self.store.valid(instance['id'], instance['input_version'], run_id):
                raise StaleRun()
            results, decision = [], None
            if len(response.text) > 100000 or len(response.tool_calls) > 32:
                raise RunError('模型输出过长或工具调用过多；已保存有截断标记的诊断。', code='output_limit', stage='response', step=number, step_id=step_id, repair_attempt=repairs)
            if response.completion_status not in {'completed', 'requires_tool'} or bool(response.tool_calls) != (response.completion_status == 'requires_tool'):
                raise RunError('模型输出未完整结束或工具状态不一致，未执行计划。', code='incomplete_response', stage='response', step=number, step_id=step_id, repair_attempt=repairs)
            ids = [call.call_id for call in response.tool_calls]
            if len(ids) != len(set(ids)) or seen.intersection(ids) or any(len(value) > 256 for value in ids):
                raise RunError('工具调用编号重复或无效，未执行。', code='tool_identity', stage='response', step=number, step_id=step_id, repair_attempt=repairs)
            seen.update(ids)
            used += len(ids)
            if used > settings.max_tool_calls:
                raise RunError('工具调用超过本轮上限。', code='tool_limit', stage='tools', step=number, step_id=step_id, repair_attempt=repairs)
            continuation = self.context_service.budget.capture_continuation(request, snapshot,
                instance['id'], run_id, step_id, response.token_usage)
            messages.append(response.as_message())
            if json_plan and not response.tool_calls:
                progress.enter('tools', step=number, step_id=step_id, repair_attempt=repairs)
                try:
                    raw = parse_json_plan(response.text, schema)
                    if request.output_format == 'json_schema':
                        raw = decode_strict_arguments(schema, raw)
                    decision = self.validate_plan(raw, instance['id'], through, settings, run_id, schema=schema)
                except (ValueError, LookupError, RecursionError) as exc:
                    issue = RunIssue(code='plan_json' if isinstance(exc, json.JSONDecodeError) else 'plan_validation',
                        stage='tools', message='JSON计划未通过格式或业务校验。', step=number, step_id=step_id,
                        repair_attempt=repairs, fields=field_issues(exc))
                    projection['validation_issues'].append(issue.model_dump())
                    projection['plan_submission'] = {'status': 'invalid', 'committed': False}
                    self.store.finish_step(step_id, projection, [], snapshot.api_key)
                    repairs += 1
                    if repairs > 1:
                        raise RunError('JSON计划修复一次后仍不合格，输入及诊断已保留。', code='repair_exhausted',
                            stage='tools', step=number, step_id=step_id, repair_attempt=repairs - 1) from exc
                    repair_submission = True
                    errors = [{'path': item.path, 'message': item.message} for item in issue.fields]
                    messages.append(ModelMessage('user', [TextContent(JSON_PLAN_REPAIR + encoded(errors))]))
                    continue
                projection['plan_submission'] = {'status': 'validated', 'committed': False}
                self.store.finish_step(step_id, projection, [], snapshot.api_key)
                return decision
            # 混合最终提交时整批不执行，避免先发生MCP副作用、再发现计划不独占。
            mixed_submission = len(response.tool_calls) != 1 and any(call.name == 'submit_rp_plan' for call in response.tool_calls)
            selected_names = set(request.tool_choice.names) if isinstance(request.tool_choice, ToolChoice) else None
            invalid_selection = bool(selected_names and any(call.name not in selected_names for call in response.tool_calls))
            tools_complete = False
            try:
                for call in response.tool_calls:
                    progress.enter('tools', step=number, step_id=step_id, tool=diagnostic(call.name, snapshot.api_key),
                        call_id=diagnostic(call.call_id, snapshot.api_key), repair_attempt=repairs)
                    if not self.store.valid(instance['id'], instance['input_version'], run_id):
                        projection['aborted_before_tool'] = call.call_id
                        raise StaleRun()
                    failed, result, issue = False, None, None
                    try:
                        if call.name not in {tool.name for tool in tools}:
                            raise ValueError('本轮未声明此工具；按当前输出规则提交最终计划。')
                        if mixed_submission:
                            raise ValueError('最终提交必须独占一次调用；本批所有工具均未执行，请只重交submit_rp_plan。')
                        if invalid_selection:
                            issue = RunIssue(code='tool_selection', stage='tools',
                                message='返回工具不符合本次原生选择；本批均未执行。', step=number, step_id=step_id,
                                tool=call.name[:128], call_id=call.call_id[:256], repair_attempt=repairs)
                            raise ValueError('只允许本次指定的工具；本批均未执行。')
                        raw = parse_tool_arguments(call.arguments)
                        if strict_projection:
                            declaration = next((tool for tool in tools if tool.name == call.name), None)
                            if declaration is not None:
                                raw = decode_strict_arguments(declaration.parameters, raw)
                        outcome = await dispatcher.execute(call, raw, call_count=len(response.tool_calls))
                        result, failed, decision = outcome.value, outcome.is_error, outcome.decision
                    except (ValueError, LookupError, RecursionError) as exc:
                        failed = True
                        issue = issue or RunIssue(code='tool_json' if isinstance(exc, json.JSONDecodeError) else 'plan_validation' if call.name == 'submit_rp_plan' else 'tool_arguments',
                            stage='tools', message='工具参数不是完整JSON。' if isinstance(exc, json.JSONDecodeError) else '计划校验未通过。' if call.name == 'submit_rp_plan' else '工具参数或读取条件不满足。',
                            step=number, step_id=step_id, tool=call.name[:128], call_id=call.call_id[:256],
                            repair_attempt=repairs, fields=field_issues(exc))
                        if isinstance(exc, ValidationError):
                            result = {'error': [{'field': list(item['loc']), 'message': item['msg']} for item in exc.errors(include_input=False)]}
                        else:
                            result = {'error': str(exc)[:500] if not isinstance(exc, RecursionError) else '参数嵌套过深。'}
                        if call.name == 'submit_rp_plan':
                            # 不自动修补业务值；只让模型按真实错误重交，限制修复时重新岔题。
                            result['repair_instruction'] = JSON_PLAN_REPAIR if json_plan else PLAN_VALIDATION_REPAIR
                    if failed:
                        issue = issue or RunIssue(code='tool_execution', stage='tools', message='外部工具返回失败；请查看本步工具结果。',
                            step=number, step_id=step_id, tool=call.name[:128], call_id=call.call_id[:256], repair_attempt=repairs)
                        projection['validation_issues'].append(issue.model_dump())
                    value = ToolResult(call.call_id, call.name, encoded(result), is_error=failed)
                    messages.append(value)
                    results.append(asdict(value))
                tools_complete = True
            finally:
                if response.tool_calls:
                    projection['tool_execution_status'] = 'completed' if tools_complete else 'interrupted'
                    projection['tools_completed'] = len(results)
                    self.store.finish_step(step_id, projection, results, snapshot.api_key)
            if decision is not None:
                return decision
            if any(value['is_error'] for value in results):
                repairs += 1
                if mixed_submission or invalid_selection or any(value['name'] == 'submit_rp_plan' and value['is_error'] for value in results):
                    repair_submission = True
            if not response.tool_calls:
                repairs += 1
                repair_submission = True
                messages.append(ModelMessage('user', [TextContent(MISSING_PLAN_REPAIR)]))
            if repairs > 1:
                raise RunError('计划修复一次后仍不合格，输入及诊断已保留。', code='repair_exhausted', stage='tools', step=number, step_id=step_id, repair_attempt=repairs - 1)
        raise RunError('模型步骤达到上限，未提交计划。', code='step_limit', stage='tools', step=number, step_id=step_id, repair_attempt=repairs)
