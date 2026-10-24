"""预设保存后同步模型引用；历史不可变，模型本地修改独立保留。"""
import json
from time import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .preset_models import PresetContent, new_preset_content
from .default_prompts import DEFAULT_PRESET_ID, DEFAULT_PRESET_NAME
from ..storage.configuration import RevisionConflict


class PresetInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=80)
    config: PresetContent
    revision: int = Field(default=0, ge=0)
    source: dict | None = None


class Presets:
    def __init__(self, database, events):
        self.database, self.events = database, events

    def list(self):
        return self.database.all('SELECT id,name,revision,updated_at FROM model_presets ORDER BY updated_at DESC')

    def ensure_default(self):
        """启动时只补一份可编辑基线；已有版本、用户修改和模型绑定均不触碰。"""
        with self.database.transaction() as tx:
            if tx.execute('SELECT 1 FROM model_presets WHERE id=?', (DEFAULT_PRESET_ID,)).fetchone():
                return
            if tx.execute('SELECT count(*) FROM model_presets').fetchone()[0] >= 200:
                return
            now, config = time(), new_preset_content().model_dump_json()
            tx.execute('INSERT INTO model_presets VALUES (?,?,1,?,NULL,?,?)',
                (DEFAULT_PRESET_ID, DEFAULT_PRESET_NAME, config, now, now))
            tx.execute('INSERT INTO model_preset_versions VALUES (?,1,?,?,NULL,?)',
                (DEFAULT_PRESET_ID, DEFAULT_PRESET_NAME, config, now))
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                (now, 'preset.builtin_added', json.dumps({'id': DEFAULT_PRESET_ID})))

    def get(self, preset_id, revision=None):
        row = self.database.one('SELECT * FROM model_presets WHERE id=?', (preset_id,)) if revision is None else self.database.one(
            'SELECT preset_id AS id,name,revision,config_json,source_json,created_at AS updated_at FROM model_preset_versions WHERE preset_id=? AND revision=?', (preset_id, revision))
        if not row:
            raise LookupError('预设版本不存在。')
        return {'id': row['id'], 'name': row['name'], 'revision': row['revision'], 'updated_at': row['updated_at'],
                'config': PresetContent.model_validate_json(row['config_json']).model_dump(),
                'source': json.loads(row['source_json']) if row['source_json'] else None}

    def save(self, body: PresetInput, preset_id=None):
        if not body.name.strip():
            raise ValueError('预设名称不能为空。')
        source = json.dumps(body.source, ensure_ascii=False, allow_nan=False) if body.source else None
        if source and len(source.encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('预设导入来源超过 2 MiB。')
        target, now = preset_id or uuid4().hex, time()
        with self.database.transaction() as tx:
            row = tx.execute('SELECT * FROM model_presets WHERE id=?', (target,)).fetchone()
            if preset_id and not row:
                raise LookupError('预设不存在。')
            revision = row['revision'] if row else 0
            if body.revision != revision:
                raise RevisionConflict('预设已修改，请重新载入。')
            if not row and tx.execute('SELECT count(*) FROM model_presets').fetchone()[0] >= 200:
                raise ValueError('预设最多 200 个。')
            source = source or (row['source_json'] if row else None)
            config = body.config.model_dump_json()
            unchanged = row and row['name'] == body.name.strip() and source == row['source_json'] and (
                body.config == PresetContent.model_validate_json(row['config_json']))
            saved_revision = revision if unchanged else revision + 1
            if not unchanged:
                tx.execute('''INSERT INTO model_presets VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,revision=excluded.revision,config_json=excluded.config_json,
                    source_json=excluded.source_json,updated_at=excluded.updated_at''',
                    (target, body.name.strip(), saved_revision, config, source, now, now))
                tx.execute('INSERT INTO model_preset_versions VALUES (?,?,?,?,?,?)', (target, saved_revision, body.name.strip(), config, source, now))
            sync = self._sync_bindings(tx, target, saved_revision, now) if row else {'connections': 0, 'models': 0, 'pending': []}
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (now, 'preset.saved', json.dumps({'id': target, 'revision': saved_revision, 'sync': sync}, ensure_ascii=False)))
        self.events.publish('models')
        return {**self.get(target), 'sync': sync}

    def _sync_bindings(self, tx, preset_id, revision, now):
        """同事务更新引用和连接修订；不兼容的连接保留原配置并交由管理员调整。"""
        # configuration 引用预设类型，在保存入口延后导入，避免模块循环。
        from .configuration import ConnectionInput
        from .reasoning import resolve_effort

        result = {'connections': 0, 'models': 0, 'pending': []}
        for row in tx.execute('SELECT id,config_json FROM model_connections').fetchall():
            data = json.loads(row['config_json'])
            affected = [model for model in data['models'] if (model.get('profile') or {}).get('preset_id') == preset_id
                        and model['profile'].get('revision') != revision]
            if not affected or data['protocol'].startswith('embedding_'):
                continue
            for model in affected:
                model['profile']['revision'] = revision
            try:
                connection = ConnectionInput.model_validate(data)
                for model in connection.models:
                    if not any(item.get('id') == model.id for item in affected):
                        continue
                    effective, _ = self.materialize(model)
                    config = connection.model_config_for(effective)
                    resolve_effort(config.reasoning, effective.reasoning_effort,
                                   temperature=effective.temperature, max_output_tokens=effective.max_output_tokens)
                    if model.context_window is not None and effective.max_output_tokens >= model.context_window:
                        raise ValueError('最大输出必须小于模型能力窗口。')
                    stored = next(item for item in affected if item['id'] == model.id)
                    stored['max_output_tokens'] = effective.max_output_tokens
                    stored['temperature'] = effective.temperature
            except ValueError:
                result['pending'].append({'connection_id': row['id'], 'name': data['name'],
                    'reason': '新预设与模型参数或本地修改不兼容，请在模型设置中同步并调整。'})
                continue
            tx.execute('UPDATE model_connections SET config_json=?,revision=revision+1,updated_at=? WHERE id=?',
                       (json.dumps(data, ensure_ascii=False), now, row['id']))
            result['connections'] += 1
            result['models'] += len(affected)
        return result

    def resolve(self, model):
        binding = model.profile
        if binding and binding.preset_id:
            try:
                base = self.get(binding.preset_id, binding.revision)['config']
            except LookupError as exc:
                raise ValueError('模型所选预设版本不存在，请重新选择。') from exc
        else:
            base = PresetContent().model_dump()
        overrides = binding.overrides if binding else {'max_output_tokens': model.max_output_tokens, 'temperature': model.temperature}
        value = {**base, **overrides, 'context': {**base['context'], **overrides.get('context', {})}}
        return PresetContent.model_validate(value)

    def materialize(self, model):
        config = self.resolve(model)
        effort = model.reasoning_effort if config.reasoning_effort == 'inherit' else config.reasoning_effort
        return model.model_copy(update={'max_output_tokens': config.max_output_tokens, 'temperature': config.temperature, 'reasoning_effort': effort}), config
