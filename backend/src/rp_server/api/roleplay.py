"""受保护的资料、QQ 接入和输入记录管理；不提供 Web 聊天入口。"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..information.characters import CharacterInput
from ..integrations.qq_accounts import AccountInput
from ..roleplay.inputs import BindingInput
from ..roleplay.conversations import ConversationDelete
from ..roleplay.archives import ArchiveInput
from ..security import Session
from ..storage.configuration import RevisionConflict
from .management import current_session, write_session


router = APIRouter(prefix='/api/v1/roleplay')
gateway_router = APIRouter()


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=0)


def invoke(service, method: str, *arguments):
    try:
        return getattr(service, method)(*arguments)
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def character_names(rows: list[dict]):
    for row in rows:
        row['character_name'] = json.loads(row.pop('character_config'))['name'] if row.get('character_config') else None
        row.pop('character_config', None)
    return rows


@router.get('/characters')
async def characters(request: Request, _: Session = Depends(current_session)):
    return request.app.state.runtime.characters.list()


@router.post('/characters', status_code=201)
async def create_character(body: CharacterInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.characters, 'save', body)


@router.put('/characters/{character_id}')
async def update_character(character_id: str, body: CharacterInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.characters, 'save', body, character_id)


@router.get('/qq/accounts')
async def accounts(request: Request, _: Session = Depends(current_session)):
    runtime = request.app.state.runtime
    return {'items': [{**row, 'connection': runtime.qq.state(row['id'])} for row in runtime.qq_accounts.list()],
            'images': runtime.inbound_images.summary()}


@router.post('/qq/accounts', status_code=201)
async def create_account(body: AccountInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.qq_accounts, 'save', body)


@router.put('/qq/accounts/{account_id}')
async def update_account(account_id: str, body: AccountInput, request: Request, _: Session = Depends(write_session)):
    runtime = request.app.state.runtime
    result = invoke(runtime.qq_accounts, 'save', body, account_id)
    # 禁用、换令牌后旧连接不再有权入库；NapCat 用新配置重新连接。
    await runtime.qq.disconnect(account_id)
    return result


@router.post('/qq/accounts/{account_id}/disconnect', status_code=204)
async def disconnect(account_id: str, request: Request, _: Session = Depends(write_session)):
    await request.app.state.runtime.qq.disconnect(account_id)


@router.get('/qq/accounts/{account_id}/contacts')
async def contacts(account_id: str, request: Request, _: Session = Depends(current_session)):
    return character_names(request.app.state.runtime.inputs.contacts(account_id))


@router.put('/qq/accounts/{account_id}/contacts/{peer_id}')
async def bind(account_id: str, peer_id: str, body: BindingInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.inputs, 'bind', account_id, peer_id, body)


@router.get('/instances')
async def instances(request: Request, _: Session = Depends(current_session)):
    return character_names(request.app.state.runtime.inputs.instances())


@router.post('/instances/{instance_id}/sync-character')
async def sync_character(instance_id: str, body: RevisionInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.inputs, 'sync_character', instance_id, body.revision)


@router.get('/instances/{instance_id}/messages')
async def messages(instance_id: str, request: Request, before: int | None = Query(default=None, ge=1),
                   _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.inputs, 'messages', instance_id, before)


@router.get('/instances/{instance_id}/images/{image_id}')
async def image(instance_id: str, image_id: str, request: Request, _: Session = Depends(current_session)):
    path = invoke(request.app.state.runtime.inbound_images, 'image_path', instance_id, image_id)
    return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.get('/instances/{instance_id}/conversation')
async def conversation(instance_id: str, request: Request, page: int = Query(default=1, ge=1, le=1000000),
                       page_size: int = Query(default=30), anchor_input: int | None = Query(default=None, ge=0, le=9223372036854775807),
                       anchor_sent: int | None = Query(default=None, ge=0, le=9223372036854775807), _: Session = Depends(current_session)):
    if page_size not in {20, 30, 50}:
        raise HTTPException(400, '每页轮数须为20、30或50。')
    return invoke(request.app.state.runtime.inputs, 'conversation', instance_id, page, page_size, anchor_input, anchor_sent)


@router.post('/instances/{instance_id}/images/{image_id}/retry')
async def retry_image(instance_id: str, image_id: str, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.inbound_images, 'retry', instance_id, image_id)


@router.get('/instances/{instance_id}/conversation/delete-preview')
async def delete_preview(instance_id: str, request: Request, rounds: int = Query(ge=1, le=1000000),
                         _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.rp_store.conversations, 'preview', instance_id, rounds)


@router.post('/instances/{instance_id}/conversation/delete-recent')
async def delete_recent(instance_id: str, body: ConversationDelete, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.rp_store.conversations, 'remove', instance_id, body)


@router.get('/instances/{instance_id}/conversation/clear-preview')
async def clear_preview(instance_id: str, request: Request, _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.rp_store.archives, 'preview', instance_id)


@router.post('/instances/{instance_id}/conversation/clear')
async def clear_conversation(instance_id: str, body: ArchiveInput, request: Request, _: Session = Depends(write_session)):
    return invoke(request.app.state.runtime.rp_store.archives, 'clear', instance_id, body)


@router.get('/conversation-archives')
async def archives(request: Request, page: int = Query(default=1, ge=1, le=1000000),
                   instance_id: str | None = Query(default=None, pattern=r'^[a-f0-9]{32}$'),
                   q: str = Query(default='', max_length=100), _: Session = Depends(current_session)):
    return invoke(request.app.state.runtime.rp_store.archives, 'list', page, instance_id, q)


@router.get('/conversation-archives/{archive_id}')
async def archive_detail(archive_id: str, request: Request, page: int = Query(default=1, ge=1, le=1000000),
                         page_size: int = Query(default=30), _: Session = Depends(current_session)):
    if page_size not in {20, 30, 50}:
        raise HTTPException(400, '每页轮数须为20、30或50。')
    return invoke(request.app.state.runtime.rp_store.archives, 'read', archive_id, page, page_size)


@gateway_router.websocket('/onebot/v11/{account_id}/')
@gateway_router.websocket('/onebot/v11/{account_id}')
async def onebot(websocket: WebSocket, account_id: str):
    runtime = getattr(websocket.app.state, 'runtime', None)
    if runtime is None:
        await websocket.close(code=1013)
        return
    await runtime.qq.serve(websocket, account_id)
