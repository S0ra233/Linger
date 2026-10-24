"""本轮工具的具体处理；循环、错误反馈、取消检查与正式提交仍由Engine负责。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING

from ..information.models import SearchInput, ReadInput
from ..information.evidence_models import EvidenceReadInput, EvidenceListInput, ConversationWindowInput
from ..llm.messages import ToolCall
from .life_models import LifeReadInput

if TYPE_CHECKING:
    from ..llm.preset_models import ContextPolicy
    from .decisions import RpDecision


@dataclass(frozen=True)
class ToolScope:
    """由服务器绑定的本轮范围；工具参数不能替换联系人或输入边界。"""
    instance: dict
    through: int
    run_id: int
    policy: ContextPolicy
    retrieval: Any
    run_clock: dict


@dataclass(frozen=True)
class ToolOutcome:
    """尚未编码的真实结果；decision仅表示通过校验，不代表已提交或发送。"""
    value: Any
    is_error: bool = False
    decision: RpDecision | None = None


class RpToolDispatcher:
    def __init__(self, store, context, scope: ToolScope, remote, *, clock: Callable[[], dict],
                 validate_plan: Callable[[dict], RpDecision]):
        self.store, self.context, self.scope, self.remote = store, context, scope, remote
        self.clock, self.validate_plan = clock, validate_plan
        # 只登记现有内置处理，不动态增删模型可见工具或推测授权。
        self._readers = {
            'rp_read_state': self._read_state,
            'rp_list_media': self._list_media,
            'rp_search_information': self._search_information,
            'rp_read_information': self._read_information,
            'rp_read_conversation': self._read_conversation,
            'rp_read_evidence': self._read_evidence,
            'rp_list_evidence': self._list_evidence,
            'rp_read_life_events': self._read_life_events,
        }

    async def execute(self, call: ToolCall, raw: dict, *, call_count: int) -> ToolOutcome:
        """只接收循环已解析的参数；不捕获取消、不修复、不写入最终决策。"""
        if call.name == 'submit_rp_plan':
            if call_count != 1:
                raise ValueError('提交计划必须独占一次工具调用。')
            decision = self.validate_plan(raw)
            return ToolOutcome({'status': 'validated', 'committed': False}, decision=decision)
        reader = self._readers.get(call.name)
        if reader is not None:
            return ToolOutcome(await reader(raw))
        if call.name in self.remote.tools:
            result, failed = await self.remote.call(call.name, raw, call.call_id)
            return ToolOutcome(result, is_error=failed)
        raise ValueError('本轮未开放这个工具。')

    async def _read_state(self, raw):
        if raw:
            raise ValueError('rp_read_state 不接收参数。')
        scope, instance_id = self.scope, self.scope.instance['id']
        scene = self.context.information.evidence.scene(instance_id)
        return {**self.clock(), 'state': self.store.state(instance_id)['state'],
                'deliveries': self.store.plans.context(instance_id),
                'scene': scene if scene['status'] == 'active' else {'status': scene['status']},
                'life': {'trigger_kind': scope.run_clock['life']['trigger_kind'],
                         'context': self.store.life.context(self.store.instance(instance_id), scope.through, scope.run_id),
                         'triggers': scope.run_clock['life'].get('triggers', [])}}

    async def _list_media(self, raw):
        if set(raw) - {'query'} or not isinstance(raw.get('query', ''), str) or len(raw.get('query', '')) > 100:
            raise ValueError('素材查询仅接收不超过 100 字符的 query。')
        query = raw.get('query', '').casefold()
        return [item for item in self.store.media.list(self.scope.instance['id'])
                if not query or query in (item['name'] + item['description']).casefold()][:20]

    async def _search_information(self, raw):
        scope = self.scope
        if not scope.policy.allow_information_tools:
            raise ValueError('当前对话未授权记忆与知识检索。')
        query = SearchInput.model_validate(raw)
        result = await self.context.information.semantic.search(scope.instance['id'], query.query,
            min(query.top_k, scope.policy.retrieval_top_k), query.category, scope.policy.retrieval_mode,
            selection=scope.retrieval, run_id=scope.run_id)
        self.context.layered.remember_tool(scope.run_id, scope.instance, scope.policy, result['results'])
        result['results'] = self.context.information.evidence.conversations.attach_anchors(
            scope.instance['id'], result['results'], through=scope.through, run_id=scope.run_id)
        return result

    async def _read_information(self, raw):
        scope = self.scope
        if not scope.policy.allow_information_tools:
            raise ValueError('当前对话未授权记忆与知识检索。')
        query = ReadInput.model_validate(raw)
        result = self.context.information.read(scope.instance['id'], query.chunk_id)
        self.context.layered.remember_tool(scope.run_id, scope.instance, scope.policy, [result])
        return self.context.information.evidence.conversations.attach_anchors(
            scope.instance['id'], [result], through=scope.through, run_id=scope.run_id)[0]

    async def _read_conversation(self, raw):
        scope = self.scope
        if not scope.policy.allow_information_tools:
            raise ValueError('当前对话未授权记忆与知识检索。')
        query = ConversationWindowInput.model_validate(raw)
        return self.context.information.evidence.conversations.read(
            scope.instance['id'], query, through=scope.through, run_id=scope.run_id)

    async def _read_evidence(self, raw):
        query = EvidenceReadInput.model_validate(raw)
        result = self.context.information.evidence.read(self.scope.instance['id'], query.kind, query.id, self.scope.through)
        if result['validity']['invalid']:
            result['text'] = '[来源已失效，原始内容不提供给模型]'
            result.pop('state_changes', None)
        result['truncated'] = len(result['text']) > 8000
        result['text'] = result['text'][:8000]
        return result

    async def _list_evidence(self, raw):
        query = EvidenceListInput.model_validate(raw)
        return self.context.information.evidence.catalog(
            self.scope.instance['id'], query.kind, query.before, through=self.scope.through)

    async def _read_life_events(self, raw):
        query = LifeReadInput.model_validate(raw)
        return self.store.life.read_history(self.scope.instance['id'], query.before, query.limit)
