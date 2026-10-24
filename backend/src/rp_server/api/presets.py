"""预设保存与导入只由管理员操作；导入先返回预览，不自动应用连接。"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..llm.presets import PresetInput
from ..llm.preset_models import PresetContent, new_preset_content
from ..llm.preset_imports import import_preset
from ..security import Session
from .management import current_session, write_session
from .roleplay import invoke


router = APIRouter(prefix='/api/v1/models/presets')


@router.get('')
async def catalog(request: Request, _: Session = Depends(current_session)):
    return {'items': request.app.state.runtime.models.presets.list(), 'defaults': new_preset_content().model_dump(),
            'legacy_defaults': PresetContent().model_dump()}


@router.post('/import-preview')
async def preview(request: Request, filename: str = Query(min_length=1, max_length=200), _: Session = Depends(write_session)):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 2 * 1024 * 1024:
            raise HTTPException(413, '预设文件不能超过 2 MiB。')
        data.extend(chunk)
    try:
        return await asyncio.to_thread(import_preset, bytes(data), filename)
    except ValidationError as exc:
        raise HTTPException(400, [{'location': list(item['loc']), 'message': item['msg']}
                                  for item in exc.errors(include_input=False)]) from exc
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else '预设字段类型或嵌套无效。') from exc


@router.post('', status_code=201)
async def create(body: PresetInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.models.presets, 'save', body)


@router.get('/{preset_id}')
async def detail(preset_id: str, request: Request, revision: int | None = Query(default=None, ge=1), _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.models.presets, 'get', preset_id, revision)


@router.get('/{preset_id}/export')
async def export(preset_id: str, request: Request, revision: int | None = Query(default=None, ge=1), _: Session = Depends(current_session)):
    row = invoke(request.app.state.runtime.models.presets, 'get', preset_id, revision)
    return JSONResponse({'format': 'rp_preset_v1', 'name': row['name'], 'config': row['config']},
                        headers={'Content-Disposition': 'attachment; filename="rp-preset.json"'})


@router.put('/{preset_id}')
async def update(preset_id: str, body: PresetInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.models.presets, 'save', body, preset_id)
