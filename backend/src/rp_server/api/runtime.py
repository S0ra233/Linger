"""运行与投递管理；管理员操作不是外部聊天接口。"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..integrations.onebot import message_id as normalize_message_id
from ..roleplay.decisions import SettingsUpdate
from ..security import Session
from .management import current_session, write_session
from .roleplay import character_names, invoke
from .response_models import RunDetailResponse


router = APIRouter(prefix='/api/v1/roleplay/runtime')


class ResolveInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    result: Literal['succeeded', 'failed']
    message_id: str | None = Field(default=None, max_length=21)


@router.get('')
async def overview(request: Request, _: Session = Depends(current_session)):
    runtime = request.app.state.runtime
    return {'settings': runtime.rp_store.settings(), 'summary': runtime.roleplay.summary(),
            'instances': character_names(runtime.inputs.instances())}


@router.put('/settings')
async def settings(body: SettingsUpdate, request: Request, _: Session = Depends(write_session)):
    runtime = request.app.state.runtime
    if body.enabled:
        try:
            snapshot = runtime.models.for_purpose('narrator')
            if snapshot.model.tool_input is not True:
                raise ValueError('请先为主叙事模型明确启用工具能力。')
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return invoke(runtime.rp_store, 'save_settings', body)


@router.post('/instances/{instance_id}/{action}')
async def control(instance_id: str, action: Literal['pause', 'resume', 'retry'], request: Request,
                  _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.rp_store, 'control', instance_id, action)


@router.get('/instances/{instance_id}/state')
async def state(instance_id: str, request: Request, _: Session = Depends(current_session)):
    invoke(request.app.state.runtime.rp_store, 'instance', instance_id)
    return request.app.state.runtime.rp_store.state(instance_id)


@router.get('/runs')
async def runs(request: Request, before: int | None = Query(default=None, ge=1),
               instance_id: str | None = Query(default=None, min_length=1, max_length=80),
               _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.rp_store, 'list_runs', before, instance_id)


@router.get('/runs/{run_id}', response_model=RunDetailResponse)
async def detail(run_id: int, request: Request, _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.rp_store, 'run_detail', run_id)


@router.get('/deliveries')
async def deliveries(request: Request, instance_id: str | None = Query(default=None, min_length=1, max_length=80),
                     _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.rp_store, 'deliveries', instance_id)


@router.post('/deliveries/{outbox_id}/cancel')
async def cancel(outbox_id: str, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.rp_store, 'cancel_delivery', outbox_id)


@router.post('/deliveries/{outbox_id}/resolve')
async def resolve(outbox_id: str, body: ResolveInput, request: Request, _: Session = Depends(write_session)):
    try:
        actual_id = normalize_message_id(body.message_id) if body.result == 'succeeded' and body.message_id else None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return invoke(request.app.state.runtime.rp_store, 'resolve_unknown', outbox_id, body.result, actual_id)
