"""QQ 素材按生活线授权；原件不可变，模型只取得编号与说明。"""
import asyncio
import base64
from io import BytesIO
import json
from time import time
from typing import Literal
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..storage.configuration import RevisionConflict


MAX_MEDIA_BYTES = 8 * 1024 * 1024


class MediaInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    instance_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default='', max_length=500)
    kind: Literal['sticker', 'image', 'market', 'face'] = 'sticker'
    source_message_id: int | None = Field(default=None, ge=1)
    source_part: int = Field(default=0, ge=0, le=32)
    face_id: str | None = Field(default=None, pattern=r'^\d{1,6}$')

    @model_validator(mode='after')
    def name_valid(self):
        if not self.name.strip(): raise ValueError('素材名称不能为空。')
        if self.kind == 'face' and self.source_message_id is not None:
            raise ValueError('内置表情使用确认的编号，不接受来源消息引用。')
        return self


class MediaUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default='', max_length=500)
    archived: bool = False


def prepare_media(source):
    """检查动画总解码规模，发送保留原件；预览单独生成，不替代 GIF。"""
    if not source or len(source) > MAX_MEDIA_BYTES: raise ValueError('素材须为 1 字节至 8 MiB。')
    try:
        with Image.open(BytesIO(source)) as image:
            mime = {'GIF': 'image/gif', 'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}.get(image.format)
            if mime is None: raise ValueError('仅支持实际为 PNG、JPEG、GIF、WebP 的图片。')
            frames = getattr(image, 'n_frames', 1)
            if frames > 120 or image.width * image.height > 24_000_000:
                raise ValueError('素材超过 120 帧或单帧 2400 万像素。')
            pixels = 0
            for index in range(frames):
                image.seek(index)
                pixels += image.width * image.height
                if pixels > 96_000_000: raise ValueError('动画总解码规模超过限制。')
                image.load()
            image.seek(0)
            preview = image.convert('RGBA')
            preview.thumbnail((320, 320))
            with BytesIO() as output:
                preview.save(output, 'PNG')
                content = output.getvalue()
            preview.close()
            return mime, content
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('图片格式损坏或解码规模过大。') from exc


class MediaAssets:
    def __init__(self, store, credentials, images):
        self.store, self.database, self.events = store, store.database, store.events
        self.credentials, self.images = credentials, images

    def list(self, instance_id, archived=False):
        self.store.instance(instance_id)
        return self.database.all('''SELECT id,instance_id,kind,name,description,mime,revision,archived,
            length(source) AS bytes,source_message_id,source_part,created_at FROM media_assets WHERE instance_id=? AND (? OR archived=0)
            ORDER BY created_at DESC LIMIT 200''', (instance_id, int(archived)))

    def context(self, instance_id):
        return [{**{key: row[key] for key in ('id', 'kind', 'name', 'revision')}, 'description': row['description'][:120]}
                for row in self.list(instance_id)[:16]]

    async def upload(self, body, source):
        if body.kind not in {'sticker', 'image'}: raise ValueError('上传文件请选择表情包或普通图片。')
        mime, preview = await asyncio.to_thread(prepare_media, source)
        return self._create(body, mime=mime, source=source, preview=preview)

    async def import_received(self, body):
        self.store.instance(body.instance_id)
        if body.kind == 'face':
            if body.face_id is None: raise ValueError('请选择已确认可用的 QQ 表情编号。')
            return self._create(body, native={'id': body.face_id})
        row = self.database.one('SELECT * FROM inbound_messages WHERE id=? AND instance_id=? AND deleted_at IS NULL', (body.source_message_id, body.instance_id))
        if not row: raise ValueError('来源消息不属于当前生活线。')
        parts = json.loads(row['parts_json'])
        if body.source_part >= len(parts): raise ValueError('来源消息片段不存在。')
        part = parts[body.source_part]
        if body.kind == 'market':
            native = part.get('market')
            if not native or not all(isinstance(native.get(key), str) and native[key] for key in ('emoji_id', 'emoji_package_id', 'key')):
                raise ValueError('此消息没有完整的 QQ 原生表情参数，不能登记为可发送贴纸。')
            image = self.database.one("SELECT attachment_id FROM inbound_images WHERE message_id=? AND part_index=? AND status='ready'", (row['id'], body.source_part))
            preview = None
            if image and image['attachment_id']:
                def read_preview():
                    with self.images._path(image['attachment_id'], '.png').open('rb') as reader:
                        return prepare_media(reader.read(MAX_MEDIA_BYTES + 1))[1]
                preview = await asyncio.to_thread(read_preview)
            return self._create(body, native=native, preview=preview, mime='image/png' if preview else None)
        image = self.database.one('SELECT attachment_id FROM inbound_images WHERE message_id=? AND part_index=? AND status=\'ready\'',
                                  (row['id'], body.source_part))
        if not image or not image['attachment_id']: raise ValueError('来源图片尚未接收完成。')
        path = self.images._path(image['attachment_id'], '.source')
        def read():
            with path.open('rb') as reader: return reader.read(MAX_MEDIA_BYTES + 1)
        return await self.upload(body, await asyncio.to_thread(read))

    def _create(self, body, *, mime=None, source=None, preview=None, native=None):
        self.store.instance(body.instance_id)
        identifier, secret = uuid4().hex, uuid4().hex if native else None
        encrypted = self.credentials.encrypt(json.dumps(native)) if native else None
        with self.database.transaction() as tx:
            if tx.execute('SELECT count(*) FROM media_assets WHERE instance_id=?', (body.instance_id,)).fetchone()[0] >= 200:
                raise ValueError('每条生活线最多保留 200 个素材。')
            if secret:
                tx.execute('INSERT INTO encrypted_secrets VALUES (?,?,?,?)', (secret, encrypted, time(), time()))
            tx.execute('''INSERT INTO media_assets(id,instance_id,kind,name,description,mime,source,preview,native_json,source_message_id,source_part,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (identifier, body.instance_id, body.kind, body.name.strip(), body.description,
                                                    mime, source, preview, secret, body.source_message_id, body.source_part, time()))
            self._audit(tx, 'media.created', identifier)
        self.events.publish('media', instance_ids=(body.instance_id,))
        self._invalidate_cache(body.instance_id)
        return next(item for item in self.list(body.instance_id) if item['id'] == identifier)

    def _audit(self, tx, action, identifier):
        tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                   (time(), action, json.dumps({'asset_id': identifier})))

    def _invalidate_cache(self, instance_id):
        if self.store.cache:
            self.store.cache.forget_instance(instance_id)

    def update(self, identifier, body):
        if not body.name.strip(): raise ValueError('素材名称不能为空。')
        with self.database.transaction() as tx:
            result = tx.execute('UPDATE media_assets SET name=?,description=?,archived=?,revision=revision+1 WHERE id=? AND revision=?',
                                (body.name.strip(), body.description, int(body.archived), identifier, body.revision))
            if not result.rowcount: raise RevisionConflict('素材已更新或不存在，请刷新。')
            self._audit(tx, 'media.updated', identifier)
        row = self.database.one('SELECT instance_id FROM media_assets WHERE id=?', (identifier,))
        self.events.publish('media', instance_ids=(row['instance_id'],))
        self._invalidate_cache(row['instance_id'])

    def validate_parts(self, instance_id, parts):
        result, total = [], 0
        for part in parts:
            if part.type == 'text':
                result.append({'type': 'text', 'text': part.text})
                continue
            row = self.database.one('SELECT id,instance_id,kind,name,revision,archived,length(source) AS bytes FROM media_assets WHERE id=?', (part.asset_id,))
            if not row or row['instance_id'] != instance_id or row['archived'] or row['revision'] != part.revision:
                raise ValueError('素材不属于当前生活线、已停用或版本已变化。')
            total += row['bytes'] or 0
            if total > MAX_MEDIA_BYTES: raise ValueError('单个气泡素材合计不能超过 8 MiB，请拆分气泡。')
            if row['kind'] == 'market' and len(parts) != 1:
                raise ValueError('QQ 原生表情包须单独一泡，不与文字或其他素材混排。')
            result.append({'type': 'asset', 'asset_id': row['id'], 'revision': row['revision'], 'name': row['name'], 'kind': row['kind']})
        return result

    async def segments(self, instance_id, parts):
        result, total = [], 0
        for part in parts:
            if part['type'] == 'text':
                result.append({'type': 'text', 'data': {'text': part['text']}})
                continue
            row = self.database.one('SELECT * FROM media_assets WHERE id=? AND instance_id=?', (part['asset_id'], instance_id))
            if not row or row['archived'] or row['revision'] != part['revision']:
                raise ValueError('待发素材已更新或停用。')
            if row['kind'] in {'face', 'market'}:
                native = json.loads(self.credentials.read(row['native_json']))
                result.append({'type': 'face' if row['kind'] == 'face' else 'mface', 'data': native})
            else:
                total += len(row['source'])
                if total > MAX_MEDIA_BYTES: raise ValueError('单个气泡图片合计超过 8 MiB。')
                data = await asyncio.to_thread(base64.b64encode, row['source'])
                item = {'file': 'base64://' + data.decode('ascii'), 'summary': row['name']}
                if row['kind'] == 'sticker': item['sub_type'] = 1
                result.append({'type': 'image', 'data': item})
        return result
