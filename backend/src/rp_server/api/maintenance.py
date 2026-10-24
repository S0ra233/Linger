"""维护接口只提供受保护的备份；恢复在停服后的独立命令中进行。"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .management import current_session, write_session
from ..security import Session
from ..storage.backups import archive_path


router = APIRouter(prefix='/api/v1/maintenance')


@router.get('')
async def overview(request: Request, _: Session = Depends(current_session)):
    try:
        return await asyncio.to_thread(request.app.state.runtime.backups.overview)
    except (OSError, ValueError, LookupError) as exc:
        raise HTTPException(503, '维护信息暂不可读，请检查数据目录权限并刷新。') from exc


@router.post('/backups', status_code=202)
async def create_backup(request: Request, _: Session = Depends(write_session)):
    try:
        return request.app.state.runtime.backups.start()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/backups/{name}/download')
async def download(name: str, request: Request, _: Session = Depends(current_session)):
    try:
        path = archive_path(request.app.state.settings.data_dir, name)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type='application/zip', filename=name)
