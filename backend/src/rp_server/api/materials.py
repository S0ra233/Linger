"""导入预览、版本资料与只读上下文检查；不提供手动聊天入口。"""
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..information.imports import MAX_IMPORT_BYTES
from ..information.worldbooks import WorldbookInput
from ..roleplay.decisions import RuntimeSettings
from ..roleplay.plan_contract import describe_contract
from ..llm.preset_models import new_preset_content
from ..security import Session
from .management import current_session, write_session
from .roleplay import invoke
from .response_models import ContextPreviewResponse


router = APIRouter(prefix='/api/v1/roleplay')


class ContextRulesInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=0)
    overrides: dict = Field(default_factory=dict)


@router.get('/context/instances/{instance_id}/rules')
async def context_rules(instance_id: str, request: Request, _: Session = Depends(current_session)):
    runtime = request.app.state.runtime
    try:
        base = runtime.models.for_purpose('narrator').preset_config.context if runtime.models.assignments()['narrator'] else new_preset_content().context
        return invoke(runtime.context.layered, 'settings', instance_id, base)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put('/context/instances/{instance_id}/rules')
async def save_context_rules(instance_id: str, body: ContextRulesInput, request: Request, _: Session = Depends(write_session)):
    runtime = request.app.state.runtime
    try:
        base = runtime.models.for_purpose('narrator').preset_config.context if runtime.models.assignments()['narrator'] else new_preset_content().context
        return invoke(runtime.context.layered, 'save_settings', instance_id, base, body)
    except ValidationError as exc:
        raise HTTPException(400, [{'location': list(item['loc']), 'message': item['msg']} for item in exc.errors(include_input=False)]) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/worldbooks')
async def worldbooks(request: Request, _: Session = Depends(current_session)):
    return request.app.state.runtime.worldbooks.list()


@router.get('/worldbooks/{book_id}')
async def worldbook(book_id: str, request: Request, revision: int | None = Query(default=None, ge=1),
                    _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.worldbooks, 'get', book_id, revision)


@router.post('/worldbooks', status_code=201)
async def create_worldbook(body: WorldbookInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.worldbooks, 'save', body)


@router.put('/worldbooks/{book_id}')
async def update_worldbook(book_id: str, body: WorldbookInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.worldbooks, 'save', body, book_id)


@router.post('/imports/preview')
async def preview_import(request: Request, kind: Literal['character', 'worldbook'],
                         filename: str = Query(min_length=1, max_length=200), _: Session = Depends(write_session)):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_IMPORT_BYTES:
            raise HTTPException(413, '导入文件不能超过 12 MiB。')
        body.extend(chunk)
    try:
        # 只有解析进入线程；恢复到应用任务后才写入，关闭时不会留下线程占用数据库。
        return await request.app.state.runtime.material_imports.preview(bytes(body), kind, filename)
    except ValidationError as exc:
        raise HTTPException(400, [{'location': list(item['loc']), 'message': item['msg']}
                                  for item in exc.errors(include_input=False)]) from exc
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else '导入字段类型、数值或嵌套层数无效。'
        raise HTTPException(400, message) from exc


@router.get('/imports/{import_id}')
async def import_detail(import_id: str, request: Request, _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.material_imports, 'get', import_id)


@router.get('/imports/{import_id}/original')
async def import_original(import_id: str, request: Request, _: Session = Depends(current_session)):
    service = request.app.state.runtime.material_imports
    record = invoke(service, 'get', import_id)
    path = invoke(service, 'source_path', import_id)
    return FileResponse(path, media_type='application/octet-stream', filename='character.png' if record['format'] == 'png' else 'material.json')


@router.post('/imports/{import_id}/commit')
async def commit_import(import_id: str, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.material_imports, 'commit', import_id)


@router.get('/characters/{character_id}/source')
async def character_source(character_id: str, request: Request, revision: int = Query(ge=1),
                           _: Session = Depends(current_session)):
    runtime = request.app.state.runtime
    row = runtime.database.one('SELECT import_id FROM character_sources WHERE character_id=? AND revision=?', (character_id, revision))
    return invoke(runtime.material_imports, 'get', row['import_id']) if row else None


@router.api_route('/context/settings', methods=['GET', 'PUT'])
async def retired_context_settings(_: Session = Depends(current_session)):
    raise HTTPException(410, '上下文与提示词已迁移到模型配置及预设页面；旧全局设置不再用于新运行。')


@router.get('/context/instances/{instance_id}/preview', response_model=ContextPreviewResponse)
async def context_preview(instance_id: str, request: Request, run_type: Literal['input', 'life'] = 'input',
                          _: Session = Depends(current_session)):
    runtime = request.app.state.runtime
    instance = invoke(runtime.rp_store, 'instance', instance_id)
    through = runtime.database.one('SELECT max(id) AS id FROM inbound_messages WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL', (instance_id,))['id'] or 0
    if run_type == 'life':
        # 后台预览不把尚未处理输入当作已处理历史，也不认领到期意图。
        through = instance['handled_input_id']
    try:
        snapshot = runtime.models.for_purpose('narrator')
        snapshot = runtime.context.layered.apply(instance_id, snapshot)
        settings = RuntimeSettings.model_validate({key: value for key, value in runtime.rp_store.settings().items() if key != 'revision'})
        clock = runtime.roleplay.engine.clock()
        life_context = runtime.life.context(instance, through)
        triggers = [item for item in life_context['intents'] if item['status'] == 'pending'
                    and item['due_at'] <= clock['server_time'] < item['expires_at']] if run_type == 'life' else []
        tools, instructions, schema = runtime.roleplay.engine.contract(instance_id, snapshot.preset_config.context,
            [item['schema'] for item in runtime.mcp.capture(instance_id).values()], config=snapshot.config())
        _, _, rendered, report, _, _ = runtime.context.inspect(instance, through, settings, snapshot,
            tools, instructions, {**clock, 'life': {'trigger_kind': run_type, 'triggers': triggers,
                'context': life_context, 'request': '只读预览，不安排或执行生活唤醒。'}}, plan_schema=schema)
        report['plan_contract'] = describe_contract(tools, schema=schema, output=report['plan_output'])
        return {'report': report, 'messages': [asdict(message) for message in rendered[0]]}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/context/instances/{instance_id}/summaries')
async def summaries(instance_id: str, request: Request, _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.context, 'summaries', instance_id)


@router.post('/context/instances/{instance_id}/clear-summary')
async def clear_summary(instance_id: str, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.context, 'clear_summary', instance_id)
