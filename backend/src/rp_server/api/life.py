"""生活记录与结构化投递管理；到期发送不额外请求模型。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..roleplay.life_models import LifePolicyInput, LifeSettingsInput
from ..roleplay.decisions import DeliveryChange
from ..security import Session
from .management import current_session, write_session
from .roleplay import invoke
from ..roleplay.planning_models import ExtensionPolicy, ScheduleUpdate, WorldEventDraft, ObservationDraft
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix='/api/v1/roleplay/life')


class ExtensionCancel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int | None = Field(default=None, ge=1)


@router.get('/instances/{instance_id}/materials')
async def materials(instance_id: str, request: Request, _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.rp_store.extensions, 'catalog', instance_id)


@router.put('/instances/{instance_id}/materials/policy')
async def materials_policy(instance_id: str, body: ExtensionPolicy, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.rp_store.extensions, 'save_policy', instance_id, body)


@router.put('/instances/{instance_id}/materials/schedule')
async def schedule(instance_id: str, body: ScheduleUpdate, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.rp_store.extensions.schedules, 'save', instance_id, body)


@router.post('/instances/{instance_id}/materials/events', status_code=201)
async def add_event(instance_id: str, body: WorldEventDraft, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.rp_store.extensions, 'add', instance_id, 'event', body)


@router.post('/instances/{instance_id}/materials/observations', status_code=201)
async def add_observation(instance_id: str, body: ObservationDraft, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.rp_store.extensions, 'add', instance_id, 'observation', body)


@router.post('/instances/{instance_id}/materials/{kind}/{identifier}/cancel')
async def cancel_material(instance_id: str, kind: str, identifier: int, body: ExtensionCancel, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.rp_store.extensions, 'cancel', instance_id, kind, identifier, body.revision)


@router.get('')
async def overview(request: Request, _: Session = Depends(current_session)):
    runtime = request.app.state.runtime
    return {'settings': runtime.life.settings(), 'runtime_enabled': runtime.rp_store.settings()['enabled'],
            'policies': runtime.database.all('SELECT instance_id,enabled,revision FROM life_policies'),
            'active_runs': runtime.database.one("SELECT count(*) AS n FROM rp_runs WHERE trigger_kind='life' AND status='running'")['n']}


@router.put('/settings')
async def settings(body: LifeSettingsInput, request: Request, _: Session = Depends(write_session)):
    runtime = request.app.state.runtime
    if body.enabled:
        # 与自动回复一样，只检查已保存能力，不发模型请求。
        snapshot = invoke(runtime.models, 'for_purpose', 'narrator')
        if not snapshot.model.tool_input:
            raise HTTPException(400, '请先为主叙事模型明确启用工具能力。')
    return invoke(runtime.life, 'save_settings', body)


@router.get('/instances/{instance_id}')
async def instance(instance_id: str, request: Request, _: Session = Depends(current_session)):
    runtime = request.app.state.runtime
    line = invoke(runtime.rp_store, 'instance', instance_id)
    return {'policy': runtime.life.policy(instance_id), 'context': runtime.life.context(line)}


@router.put('/instances/{instance_id}/policy')
async def policy(instance_id: str, body: LifePolicyInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.life, 'save_policy', instance_id, body)


@router.get('/plans')
async def plans(request: Request, instance_id: str | None = Query(default=None, min_length=1, max_length=80),
                _: Session = Depends(current_session)):
    store = request.app.state.runtime.rp_store
    deliveries = invoke(store.plans, 'list', instance_id, 201)
    clause, args = (' AND instance_id=?', (instance_id,)) if instance_id else ('', ())
    followups = store.database.all("SELECT id,run_id,instance_id,text,due_at,status,anchor_at,after_seconds,error FROM rp_outbox WHERE plan_kind='followup' AND status IN ('pending','sending','unknown')" + clause + ' ORDER BY due_at LIMIT 201', args)
    return {'deliveries': deliveries[:200], 'followups': followups[:200], 'truncated': len(deliveries)>200 or len(followups)>200}


@router.post('/instances/{instance_id}/deliveries')
async def manage_delivery(instance_id: str, body: DeliveryChange, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.rp_store.plans, 'manage', instance_id, body)


@router.get('/recycle')
async def recycle(request: Request, instance_id: str | None = Query(default=None, min_length=1, max_length=80),
                  page: int = Query(default=1, ge=1, le=1000000), _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.rp_store.plans, 'recycle', instance_id, page)


@router.get('/recycle/{run_id}')
async def recycle_detail(run_id: int, request: Request, _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.rp_store.plans, 'recycle_detail', run_id)


@router.get('/intents')
async def intents(request: Request, instance_id: str | None = Query(default=None, min_length=1, max_length=80),
                  _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.life, 'queue', instance_id)


@router.get('/timeline')
async def timeline(request: Request, instance_id: str | None = Query(default=None, min_length=1, max_length=80),
                   before: int | None = Query(default=None, ge=1), _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.life, 'timeline', instance_id, before)
