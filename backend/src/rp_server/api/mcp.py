"""MCP 管理接口；同步是显式操作，读取页面不发外部请求。"""
from fastapi import APIRouter, Depends, Query, Request

from ..integrations.mcp_models import McpInput, PermissionInput, RevisionInput
from ..security import Session
from .management import current_session, write_session
from .roleplay import invoke

router = APIRouter(prefix='/api/v1/mcp')


@router.get('')
async def catalog(request: Request, _: Session = Depends(current_session)):
    return request.app.state.runtime.mcp.catalog()


@router.post('/servers', status_code=201)
async def create(body: McpInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.mcp, 'save', body)


@router.put('/servers/{server_id}')
async def update(server_id: str, body: McpInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.mcp, 'save', body, server_id)


@router.post('/servers/{server_id}/sync', status_code=202)
async def sync(server_id: str, body: RevisionInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.mcp, 'start_sync', server_id, body.revision)


@router.post('/syncs/{sync_id}/cancel')
async def cancel(sync_id: str, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.mcp, 'cancel_sync', sync_id)


@router.get('/instances/{instance_id}/permissions')
async def permissions(instance_id: str, request: Request, _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.mcp, 'permissions', instance_id)


@router.put('/instances/{instance_id}/permissions')
async def save_permissions(instance_id: str, body: PermissionInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.mcp, 'save_permissions', instance_id, body)


@router.get('/calls')
async def calls(request: Request, instance_id: str | None = Query(default=None, min_length=1, max_length=80),
                _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.mcp, 'calls', instance_id)
