"""模型管理接口；连接检查异步完成，实际模型请求只能由管理员操作触发。"""
from fastapi import APIRouter, Depends, HTTPException, Request

from ..llm.configuration import AssignmentsInput, CheckInput, ConnectionInput, PROTOCOL_LABELS, RevisionInput
from ..llm.service import CheckLimit, MissingModel
from ..llm.reasoning import reasoning_catalog
from ..llm.cache_maintenance import CachePolicies
from ..llm.explicit_cache import CacheCreate, CacheOperation
from .roleplay import invoke
from ..security import Session
from ..storage.configuration import RevisionConflict
from .management import current_session, write_session


router = APIRouter(prefix='/api/v1/models')


@router.get('/explicit-caches')
async def explicit_caches(request: Request, _: Session = Depends(current_session)):
    return request.app.state.runtime.rp_store.explicit_cache.catalog()


async def cache_operation(service, method, *args):
    try:
        return await getattr(service, method)(*args)
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, LookupError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/explicit-caches', status_code=201)
async def explicit_create(body: CacheCreate, request: Request, _: Session = Depends(write_session)):
    return await cache_operation(request.app.state.runtime.rp_store.explicit_cache, 'create', body)


@router.post('/explicit-caches/{identifier}/operation')
async def explicit_operate(identifier: str, body: CacheOperation, request: Request, _: Session = Depends(write_session)):
    return await cache_operation(request.app.state.runtime.rp_store.explicit_cache, 'operate', identifier, body)


@router.get('/capabilities')
async def capabilities(_: Session = Depends(current_session)):
    return reasoning_catalog()


@router.get('/cache')
async def cache_catalog(request: Request, _: Session = Depends(current_session)):
    return request.app.state.runtime.rp_store.cache.catalog()


@router.put('/cache')
async def cache_settings(body: CachePolicies, request: Request, _: Session = Depends(write_session)):
    try:
        return request.app.state.runtime.rp_store.cache.save(body)
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, LookupError) as exc:
        raise HTTPException(400, str(exc)) from exc


def invoke(request: Request, method: str, *arguments):
    try:
        return getattr(request.app.state.runtime.models, method)(*arguments)
    except MissingModel as exc:
        raise HTTPException(404, str(exc)) from exc
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except CheckLimit as exc:
        raise HTTPException(429, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/catalog')
async def catalog(request: Request, archived: bool = False, _: Session = Depends(current_session)):
    return {'connections': invoke(request, 'list_connections', archived),
            'assignments': invoke(request, 'assignments'), 'protocols': PROTOCOL_LABELS}


@router.post('/connections', status_code=201)
async def create(body: ConnectionInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request, 'save', body)


@router.put('/connections/{connection_id}')
async def update(connection_id: str, body: ConnectionInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request, 'save', body, connection_id)


@router.post('/connections/{connection_id}/archive')
async def archive(connection_id: str, body: RevisionInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request, 'set_archived', connection_id, body.revision, True)


@router.post('/connections/{connection_id}/restore')
async def restore(connection_id: str, body: RevisionInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request, 'set_archived', connection_id, body.revision, False)


@router.put('/assignments')
async def assignments(body: AssignmentsInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request, 'save_assignments', body)


@router.get('/checks')
async def checks(request: Request, connection_id: str | None = None, _: Session = Depends(current_session)):
    return invoke(request, 'checks', connection_id)


@router.post('/connections/{connection_id}/checks', status_code=202)
async def start_check(connection_id: str, body: CheckInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request, 'start_check', connection_id, body)


@router.post('/checks/{check_id}/cancel')
async def cancel_check(check_id: str, request: Request, _: Session = Depends(write_session)):
    return invoke(request, 'cancel_check', check_id)
