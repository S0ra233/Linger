"""独立策略与绑定管理；所有身份由管理员路由和服务端绑定决定。"""
from fastapi import APIRouter, Depends, Query, Request

from ..roleplay.context_strategies import StrategyBindingInput, StrategyInput
from ..security import Session
from .management import current_session, write_session
from .roleplay import invoke


router = APIRouter(prefix='/api/v1/context-strategies')


def service(request):
    return request.app.state.runtime.context.strategies


@router.get('')
def catalog(request: Request, _: Session = Depends(current_session)):
    return invoke(service(request), 'catalog')


@router.post('', status_code=201)
def create(body: StrategyInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'save', body)


@router.get('/bindings')
def bindings(request: Request, _: Session = Depends(current_session)):
    return invoke(service(request), 'bindings')


@router.put('/bindings')
def bind(body: StrategyBindingInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'bind', body)


@router.get('/bindings/{instance_id}')
def binding(instance_id: str, request: Request, _: Session = Depends(current_session)):
    return invoke(service(request), 'binding', instance_id)


@router.get('/{strategy_id}')
def detail(strategy_id: str, request: Request, revision: int | None = Query(default=None, ge=1), _: Session = Depends(current_session)):
    return invoke(service(request), 'get', strategy_id, revision)


@router.put('/{strategy_id}')
def update(strategy_id: str, body: StrategyInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'save', body, strategy_id)
