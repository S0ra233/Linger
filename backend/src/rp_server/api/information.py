"""管理员管理资料与记忆；模型只经绑定作用域读取，不开放任意文件入口。"""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import ValidationError

from ..information.models import (DocumentInput, LibraryInput, MemoryInput, RevisionInput, AdminSearchInput,
                                  IndexInput, MemoryProtectionInput, MemoryProposalReview, MemoryKindsInput, MemoryBatchForget)
from ..information.evidence_models import EvidenceStatusInput, ConversationWindowInput
from ..information.adjustment_models import ProposalReview, AdjustmentRollback
from ..security import Session
from .response_models import MemoryProposalPageResponse
from .management import current_session, write_session
from .roleplay import invoke


router = APIRouter(prefix='/api/v1/information')
EvidenceKind = Literal['input', 'sent', 'life', 'world_event', 'observation']


@router.get('/instances/{instance_id}/conversation-window')
async def conversation_window(instance_id: str, request: Request, kind: Literal['input', 'sent'],
                              id: int = Query(ge=1, le=9223372036854775807),
                              page: int | None = Query(default=None, ge=1, le=1000000),
                              anchor_input: int | None = Query(default=None, ge=0, le=9223372036854775807),
                              anchor_sent: int | None = Query(default=None, ge=0, le=9223372036854775807),
                              _: Session = Depends(current_session)):
    query = ConversationWindowInput(kind=kind, id=id, token_budget=8000)
    try:
        return service(request).evidence.conversations.read(instance_id, query, paged=True,
            page=page, anchor_input=anchor_input, anchor_sent=anchor_sent)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/instances/{instance_id}/evidence')
async def evidence(instance_id: str, request: Request, kind: EvidenceKind,
                   before: int | None = Query(default=None, ge=1), _: Session = Depends(current_session)):
    return invoke(service(request).evidence, 'catalog', instance_id, kind, before)


@router.get('/instances/{instance_id}/scene')
async def scene(instance_id: str, request: Request, _: Session = Depends(current_session)):
    invoke(service(request).store, 'instance', instance_id)
    return service(request).evidence.scene(instance_id)


@router.get('/instances/{instance_id}/evidence/{kind}/{source_id}')
async def evidence_detail(instance_id: str, kind: EvidenceKind, source_id: int, request: Request,
                          _: Session = Depends(current_session)):
    invoke(service(request).store, 'instance', instance_id)
    return invoke(service(request).evidence, 'read', instance_id, kind, source_id)


def service(request):
    return request.app.state.runtime.information


@router.put('/instances/{instance_id}/evidence/{kind}/{source_id}/status')
async def evidence_status(instance_id: str, kind: EvidenceKind, source_id: int, body: EvidenceStatusInput,
                          request: Request, _: Session = Depends(write_session)):
    return invoke(service(request).evidence, 'set_status', instance_id, kind, source_id, body)


@router.get('/instances/{instance_id}/character-adjustments')
async def adjustments(instance_id: str, request: Request, before: int | None = Query(default=None, ge=1), _: Session = Depends(current_session)):
    return invoke(service(request).store.adjustments, 'detail', instance_id, before)


@router.post('/instances/{instance_id}/character-adjustments/{proposal_id}/review')
async def review_adjustment(instance_id: str, proposal_id: int, body: ProposalReview, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request).store.adjustments, 'review', instance_id, proposal_id, body)


@router.post('/instances/{instance_id}/character-adjustments/rollback')
async def rollback_adjustment(instance_id: str, body: AdjustmentRollback, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request).store.adjustments, 'rollback', instance_id, body)


@router.get('/libraries')
async def libraries(request: Request, _: Session = Depends(current_session)):
    return service(request).libraries()


@router.post('/libraries', status_code=201)
async def create_library(body: LibraryInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'save_library', body)


@router.put('/libraries/{library_id}')
async def update_library(library_id: str, body: LibraryInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'save_library', body, library_id)


@router.get('/libraries/{library_id}/documents')
async def documents(library_id: str, request: Request, before: str | None = Query(default=None, pattern=r'^[0-9a-f]{32}$'), _: Session = Depends(current_session)):
    return invoke(service(request), 'documents', library_id, before)


@router.post('/libraries/{library_id}/documents', status_code=201)
async def create_document(library_id: str, body: DocumentInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'save_document', library_id, body)


@router.post('/libraries/{library_id}/documents/import', status_code=201)
async def import_document(library_id: str, request: Request, filename: str = Query(min_length=1, max_length=200),
                          encoding: Literal['utf-8-sig', 'utf-16', 'gb18030'] = 'utf-8-sig', _: Session = Depends(write_session)):
    invoke(service(request), 'library', library_id)
    if not filename.lower().endswith(('.txt', '.md', '.markdown')):
        raise HTTPException(400, '资料导入只接收 TXT 或 Markdown。')
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 512000:
            raise HTTPException(413, '单篇资料不能超过 512000 字节。')
        body.extend(chunk)
    try:
        text = bytes(body).decode(encoding)
    except UnicodeError as exc:
        raise HTTPException(400, '文本解码失败，请选择正确编码。') from exc
    try:
        document = DocumentInput(title=filename, text=text)
    except ValidationError as exc:
        raise HTTPException(400, [{'message': item['msg'], 'location': list(item['loc'])} for item in exc.errors(include_input=False)]) from exc
    return invoke(service(request), 'save_document', library_id, document, None, bytes(body), encoding)


@router.get('/libraries/{library_id}/documents/{document_id}')
async def document(library_id: str, document_id: str, request: Request, _: Session = Depends(current_session)):
    result = invoke(service(request), 'document', library_id, document_id)
    return {**result, 'versions': service(request).database.all('SELECT revision,title,digest,encoding,created_at FROM knowledge_versions WHERE document_id=? ORDER BY revision DESC LIMIT 20', (document_id,))}


@router.put('/libraries/{library_id}/documents/{document_id}')
async def update_document(library_id: str, document_id: str, body: DocumentInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'save_document', library_id, body, document_id)


@router.get('/libraries/{library_id}/documents/{document_id}/original')
async def original(library_id: str, document_id: str, request: Request, revision: int | None = Query(default=None, ge=1), _: Session = Depends(current_session)):
    row = invoke(service(request), 'document', library_id, document_id)
    source = service(request).database.one('SELECT source_bytes FROM knowledge_versions WHERE document_id=? AND revision=?', (document_id, revision or row['revision']))
    if source is None:
        raise HTTPException(404, '资料版本不存在。')
    return Response(bytes(source['source_bytes']), media_type='application/octet-stream', headers={'Content-Disposition': 'attachment; filename="knowledge.txt"'})


@router.get('/instances/{instance_id}/memories')
async def memories(instance_id: str, request: Request, before: str | None = Query(default=None, pattern=r'^[0-9a-f]{32}$'), _: Session = Depends(current_session)):
    return invoke(service(request), 'memories', instance_id, before)


@router.get('/instances/{instance_id}/memory-proposals', response_model=MemoryProposalPageResponse)
async def memory_proposals(instance_id: str, request: Request,
                           before: str | None = Query(default=None, pattern=r'^[0-9a-f]{32}$'),
                           _: Session = Depends(current_session)):
    return invoke(service(request).governance, 'proposals', instance_id, before)


@router.post('/instances/{instance_id}/memory-proposals/{proposal_id}/review')
async def review_memory_proposal(instance_id: str, proposal_id: str, body: MemoryProposalReview,
                                request: Request, _: Session = Depends(write_session)):
    return invoke(service(request).governance, 'review', instance_id, proposal_id, body)


@router.put('/instances/{instance_id}/memories/{memory_id}/protection')
async def memory_protection(instance_id: str, memory_id: str, body: MemoryProtectionInput,
                            request: Request, _: Session = Depends(write_session)):
    return invoke(service(request).governance, 'protect', instance_id, memory_id, body)


@router.post('/instances/{instance_id}/memories', status_code=201)
async def create_memory(instance_id: str, body: MemoryInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'save_memory', instance_id, body)


@router.get('/instances/{instance_id}/memories/{memory_id}')
async def memory(instance_id: str, memory_id: str, request: Request, _: Session = Depends(current_session)):
    return invoke(service(request), 'memory_detail', instance_id, memory_id)


@router.put('/instances/{instance_id}/memories/{memory_id}')
async def update_memory(instance_id: str, memory_id: str, body: MemoryInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'save_memory', instance_id, body, memory_id)


@router.post('/instances/{instance_id}/memories/{memory_id}/forget')
async def forget(instance_id: str, memory_id: str, body: RevisionInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'forget', instance_id, memory_id, body.revision)


@router.post('/instances/{instance_id}/memory-forget-preview')
async def memory_forget_preview(instance_id: str, body: MemoryKindsInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'forget_preview', instance_id, body.kinds)


@router.post('/instances/{instance_id}/memory-forget-batch')
async def memory_forget_batch(instance_id: str, body: MemoryBatchForget, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request), 'forget_batch', instance_id, body)


@router.post('/instances/{instance_id}/search')
async def search(instance_id: str, body: AdminSearchInput, request: Request, _: Session = Depends(write_session)):
    try:
        result = await service(request).semantic.search(instance_id, body.query, body.top_k, body.category, body.mode)
        result['results'] = service(request).evidence.conversations.attach_anchors(instance_id, result['results'])
        return result
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/indexes')
async def indexes(request: Request, scope_kind: Literal['library', 'memory'] | None = None,
                  scope_id: str | None = Query(default=None, pattern=r'^[0-9a-f]{32}$'), _: Session = Depends(current_session)):
    if (scope_kind is None) != (scope_id is None):
        raise HTTPException(400, '请同时选择索引类型和范围。')
    return service(request).semantic.status(scope_kind, scope_id)


@router.post('/indexes/jobs', status_code=202)
async def build_index(body: IndexInput, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request).semantic, 'start_job', body)


@router.post('/indexes/jobs/{job_id}/cancel')
async def cancel_index(job_id: str, request: Request, _: Session = Depends(write_session)):
    return invoke(service(request).semantic, 'cancel_job', job_id)
