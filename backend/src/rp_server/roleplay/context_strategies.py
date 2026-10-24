"""独立上下文策略库与对话绑定；不需要模型连接，也不创建运行任务。"""
import json
from time import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..llm.preset_models import ContextPolicy, new_preset_content
from ..storage.configuration import RevisionConflict
from .store import encoded


# 提示词与厂商能力不属于策略；旧模型预设的字段继续兼容读取。
STRATEGY_FIELDS = (
    'strategy', 'history_trigger_turns', 'history_trigger_tokens', 'history_target_tokens', 'retain_turns',
    'trim_on_material_change', 'context_window', 'context_bytes', 'output_reserve', 'safety_margin', 'trigger_ratio',
    'state_injection', 'state_refresh_turns',
    'worldbook_budget_tokens', 'memory_budget_tokens', 'knowledge_budget_tokens', 'retrieval_mode', 'retrieval_top_k',
    'retrieval_budget_tokens', 'retrieval_target_tokens', 'allow_information_tools', 'retain_retrieval_snapshot',
    'summary_target_tokens', 'summary_max_tokens', 'summary_timeout', 'summary_max_batches', 'pending_input_limit',
)


def strategy_defaults(mode='cost'):
    value = new_preset_content().context.model_dump()
    value.update(strategy=mode, trim_on_material_change=mode == 'cost')
    return {key: value[key] for key in STRATEGY_FIELDS}


def stored_rules(raw):
    # 旧固定版本缺少新字段时仅补读取默认，不回写或推进它的版本。
    return {'state_injection': 'off', 'state_refresh_turns': 20, **json.loads(raw)}


def validate_rules(value):
    if set(value) - set(STRATEGY_FIELDS):
        raise ValueError('策略包含模型提示词或未知字段。')
    if value.get('strategy', 'cost') not in {'cost', 'continuity'}:
        raise ValueError('独立策略仅支持成本优先或对话连续性优先。')
    policy = ContextPolicy.model_validate({**strategy_defaults(), **value})
    return {key: getattr(policy, key) for key in STRATEGY_FIELDS}


class StrategyInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=80)
    revision: int = Field(default=0, ge=0)
    config: dict = Field(default_factory=dict)


class BindingTarget(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    instance_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    revision: int = Field(ge=0)
    overrides: dict = Field(default_factory=dict)


class StrategyBindingInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    strategy_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    strategy_revision: int | None = Field(default=None, ge=1)
    targets: list[BindingTarget] = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def reference(self):
        if (self.strategy_id is None) != (self.strategy_revision is None):
            raise ValueError('策略与版本须同时选择。')
        if len({item.instance_id for item in self.targets}) != len(self.targets):
            raise ValueError('同一对话不能重复绑定。')
        return self


class ContextStrategies:
    def __init__(self, store):
        self.store, self.database, self.events = store, store.database, store.events

    def list(self):
        return self.database.all('''SELECT s.id,s.name,s.revision,s.config_json,s.updated_at,
            (SELECT count(*) FROM context_bindings b WHERE b.strategy_id=s.id) AS bound_count
            FROM context_strategies s ORDER BY s.updated_at DESC,s.id''')

    def catalog(self):
        return {'items': [{key: value for key, value in row.items() if key != 'config_json'} | {'config': stored_rules(row['config_json'])} for row in self.list()],
                'defaults': {'cost': strategy_defaults(), 'continuity': strategy_defaults('continuity')}}

    def get(self, identifier, revision=None):
        row = self.database.one('SELECT id,name,revision,config_json,updated_at FROM context_strategies WHERE id=?', (identifier,)) if revision is None else self.database.one(
            'SELECT strategy_id AS id,name,revision,config_json,created_at AS updated_at FROM context_strategy_versions WHERE strategy_id=? AND revision=?', (identifier, revision))
        if not row:
            raise LookupError('上下文策略版本不存在。')
        return {key: value for key, value in row.items() if key != 'config_json'} | {'config': stored_rules(row['config_json'])}

    def save(self, body, identifier=None):
        if not body.name.strip():
            raise ValueError('策略名称不能为空。')
        config = encoded(validate_rules(body.config))
        target, now = identifier or uuid4().hex, time()
        with self.database.transaction() as tx:
            old = tx.execute('SELECT revision FROM context_strategies WHERE id=?', (target,)).fetchone()
            if identifier and not old:
                raise LookupError('策略不存在。')
            if (old['revision'] if old else 0) != body.revision:
                raise RevisionConflict('策略已修改，请重新载入。')
            if not old and tx.execute('SELECT count(*) FROM context_strategies').fetchone()[0] >= 200:
                raise ValueError('策略最多200个。')
            revision = body.revision + 1
            tx.execute('''INSERT INTO context_strategies VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,revision=excluded.revision,config_json=excluded.config_json,updated_at=excluded.updated_at''',
                (target, body.name.strip(), revision, config, now, now))
            tx.execute('INSERT INTO context_strategy_versions VALUES (?,?,?,?,?,?)', (target, revision, body.name.strip(), config, now, 'saved'))
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                (now, 'context.strategy_saved', encoded({'id': target, 'revision': revision})))
        # 策略保存产生新版本，已绑定对话仍使用原固定版本。
        self.events.publish('context', instance_ids=())
        return self.get(target)

    def binding(self, instance_id):
        self.store.instance(instance_id)
        row = self.database.one('SELECT * FROM context_bindings WHERE instance_id=?', (instance_id,))
        value = {'instance_id': instance_id, 'strategy_id': None, 'strategy_revision': None, 'revision': 0, 'overrides': {}, 'strategy': None}
        if row:
            value.update({key: row[key] for key in ('strategy_id', 'strategy_revision', 'revision')}, overrides=json.loads(row['overrides_json']))
            if row['strategy_id']:
                value['strategy'] = self.get(row['strategy_id'], row['strategy_revision'])
                value['latest_revision'] = self.get(row['strategy_id'])['revision']
        return value

    def bindings(self):
        rows = self.database.all('''SELECT r.id,c.display_name,c.nickname,r.peer_id,a.name AS account_name,v.config_json AS character_config,
            b.strategy_id,b.strategy_revision,coalesce(b.revision,0) AS binding_revision,b.overrides_json,sv.name AS strategy_name,s.revision AS latest_revision
            FROM role_instances r JOIN qq_accounts a ON a.id=r.account_id JOIN contacts c ON c.account_id=r.account_id AND c.peer_id=r.peer_id
            JOIN character_versions v ON v.character_id=r.character_id AND v.revision=r.character_revision
            LEFT JOIN context_bindings b ON b.instance_id=r.id LEFT JOIN context_strategies s ON s.id=b.strategy_id
            LEFT JOIN context_strategy_versions sv ON sv.strategy_id=b.strategy_id AND sv.revision=b.strategy_revision
            ORDER BY r.created_at DESC,r.id''')
        for row in rows:
            row['character_name'] = json.loads(row.pop('character_config'))['name']
            row['overridden'] = bool(json.loads(row.pop('overrides_json') or '{}'))
        return rows

    def bind(self, body):
        base = self.get(body.strategy_id, body.strategy_revision)['config'] if body.strategy_id else None
        for target in body.targets:
            self.store.instance(target.instance_id)
            if set(target.overrides) - set(STRATEGY_FIELDS):
                raise ValueError('对话覆盖包含未知策略字段。')
            if base:
                validate_rules({**base, **target.overrides})
            elif target.overrides:
                raise ValueError('解除绑定时不能携带策略覆盖。')
        with self.database.transaction() as tx:
            for target in body.targets:
                current = tx.execute('SELECT revision FROM context_bindings WHERE instance_id=?', (target.instance_id,)).fetchone()
                if (current['revision'] if current else 0) != target.revision:
                    raise RevisionConflict('所选对话绑定已变化，请重新载入。')
                # 解除绑定也保留修订行，避免删除／重建造成旧草稿修订号复用。
                tx.execute('''INSERT INTO context_bindings VALUES (?,?,?,?,?,?) ON CONFLICT(instance_id) DO UPDATE SET
                    strategy_id=excluded.strategy_id,strategy_revision=excluded.strategy_revision,
                    overrides_json=excluded.overrides_json,revision=excluded.revision,updated_at=excluded.updated_at''',
                    (target.instance_id, body.strategy_id, body.strategy_revision, encoded(target.overrides), target.revision + 1, time()))
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                (time(), 'context.strategy_bound', encoded({'strategy_id': body.strategy_id, 'revision': body.strategy_revision,
                 'instances': [item.instance_id for item in body.targets]})))
        self.events.publish('context', instance_ids=tuple(target.instance_id for target in body.targets))
        return {'status': 'saved', 'count': len(body.targets)}

    def resolve(self, instance_id, model_policy):
        binding = self.binding(instance_id)
        if binding['strategy']:
            base = {**model_policy.model_dump(), **binding['strategy']['config']}
            # 策略未指定窗口时沿用模型默认值；默认值不是接口能力上限。
            if base['context_window'] is None:
                base['context_window'] = model_policy.context_window
            config = {**base, **binding['overrides']}
            if config['context_window'] is None:
                config['context_window'] = base['context_window']
            policy = ContextPolicy.model_validate({**model_policy.model_dump(), **config})
            return {'revision': binding['revision'], 'overrides': binding['overrides'],
                    'base': base, 'effective': policy.model_dump(), 'binding': binding}
        row = self.database.one('SELECT overrides_json,revision FROM context_worksets WHERE instance_id=?', (instance_id,))
        overrides = json.loads(row['overrides_json']) if row else {}
        config = {**model_policy.model_dump(), **overrides}
        if config['context_window'] is None:
            config['context_window'] = model_policy.context_window
        return {'revision': row['revision'] if row else 0, 'overrides': overrides, 'base': model_policy.model_dump(),
                'effective': ContextPolicy.model_validate(config).model_dump(), 'binding': binding}
