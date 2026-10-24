"""管理员用量报表只读取本地记录，价格修改不触发模型调用。"""
from fastapi import APIRouter, Depends, Query, Request

from ..llm.accounting import PricesInput, UsageReports
from ..security import Session
from .management import current_session, write_session
from .roleplay import invoke


router = APIRouter(prefix='/api/v1/usage')


def service(request):
    runtime = request.app.state.runtime
    return UsageReports(runtime.database, runtime.events)


@router.get('/prices')
def prices(request: Request, _: Session = Depends(current_session)):
    reports = service(request)
    return {'prices': reports.prices(), 'catalog': reports.catalog()}


@router.put('/prices')
def save_prices(body: PricesInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'save_prices', body)


@router.get('')
def report(request: Request, start: float = Query(ge=0, le=32503680000), end: float = Query(ge=0, le=32503680000),
           source: str | None = Query(default=None, max_length=40),
           connection_id: str | None = Query(default=None, min_length=1, max_length=80),
           model_id: str | None = Query(default=None, min_length=1, max_length=80),
           page: int = Query(default=1, ge=1, le=200), instance_id: str | None = Query(default=None, pattern=r'^[0-9a-f]{32}$'),
           _: Session = Depends(current_session)):
    if instance_id:
        invoke(request.app.state.runtime.rp_store, 'instance', instance_id)
    return invoke(service(request), 'report', start, end, source, connection_id, model_id, page, instance_id)


@router.get('/cache/instances/{instance_id}')
def recent_cache(instance_id: str, request: Request, limit: int = Query(default=20, ge=1, le=100), _: Session = Depends(current_session)):
    runtime = request.app.state.runtime
    invoke(runtime.rp_store, 'instance', instance_id)
    binding = runtime.models.assignments()['narrator']
    return invoke(service(request), 'recent_cache', instance_id, binding, limit)
