"""只管理本线素材；上传和选入不发送 QQ、不调用模型。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from ..attachments.media import MAX_MEDIA_BYTES, MediaInput, MediaUpdate
from ..security import Session
from .management import current_session, write_session
from .roleplay import invoke

router = APIRouter(prefix='/api/v1/media')


@router.get('')
async def catalog(request: Request, instance_id: str, archived: bool = False, _: Session = Depends(current_session)):
    runtime = request.app.state.runtime
    return {'items': invoke(runtime.rp_store.media, 'list', instance_id, archived),
            'instances': runtime.inputs.instances()}


@router.post('/upload', status_code=201)
async def upload(request: Request, instance_id: str, name: str = Query(min_length=1, max_length=80),
                 description: str = Query(default='', max_length=500), kind: str = 'sticker', _: Session = Depends(write_session)):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_MEDIA_BYTES: raise HTTPException(413, '素材不能超过 8 MiB。')
        body.extend(chunk)
    try:
        return await request.app.state.runtime.rp_store.media.upload(MediaInput(instance_id=instance_id, name=name, description=description, kind=kind), bytes(body))
    except (ValueError, LookupError) as exc:
        raise HTTPException(400, '素材参数无效。' if not isinstance(exc, ValueError) else str(exc)) from exc


@router.post('/import', status_code=201)
async def import_received(body: MediaInput, request: Request, _: Session = Depends(write_session)):
    try:
        return await request.app.state.runtime.rp_store.media.import_received(body)
    except (ValueError, LookupError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put('/{asset_id}', status_code=204)
async def update(asset_id: str, body: MediaUpdate, request: Request, _: Session = Depends(write_session)):
    invoke(request.app.state.runtime.rp_store.media, 'update', asset_id, body)


@router.get('/{asset_id}/preview')
async def preview(asset_id: str, request: Request, _: Session = Depends(current_session)):
    row = request.app.state.runtime.database.one('SELECT preview FROM media_assets WHERE id=?', (asset_id,))
    if not row or row['preview'] is None: raise HTTPException(404, '此素材没有本地预览。')
    return Response(content=row['preview'], media_type='image/png')
