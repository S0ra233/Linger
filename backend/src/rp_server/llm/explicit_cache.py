"""Gemini 显式缓存只缓存已成功业务的固定系统／工具前缀；资源管理不生成角色消息。"""
import asyncio
from copy import deepcopy
from datetime import datetime
import json
import re
from time import time
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field

from .cache_maintenance import model_fingerprint, scoped_snapshot
from ..roleplay.store import encoded
from ..storage.configuration import RevisionConflict


class CacheCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    instance_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    connection_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    model_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    ttl_seconds: int = Field(default=600, ge=60, le=604800)


class CacheOperation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=1)
    action: str = Field(pattern=r'^(refresh|extend|delete|disable)$')
    ttl_seconds: int = Field(default=600, ge=60, le=604800)


def prefix(payload):
    return {key: deepcopy(payload[key]) for key in ('systemInstruction', 'tools', 'toolConfig') if key in payload}


class ExplicitCaches:
    def __init__(self, store, models):
        self.store, self.models, self.database = store, models, store.database
        self.baselines, self.candidates, self.tasks = {}, {}, set()
        self.closing = False
        with self.database.transaction() as tx:
            tx.execute("UPDATE explicit_caches SET status='unknown',revision=revision+1,error='进程中断，资源结果未知；未重新创建。' WHERE status IN ('creating','extending','deleting','refreshing')")
            tx.execute("UPDATE explicit_cache_operations SET status='unknown',finished_at=?,error='进程中断，未重试。' WHERE status='running'", (time(),))

    @staticmethod
    def observes(snapshot):
        return snapshot.connection.protocol == 'gemini' and snapshot.connection.options.gemini_explicit_cache_confirmed and bool(snapshot.connection.options.gemini_explicit_cache_url)

    def capture(self, run_id, instance, snapshot, payload):
        if not self.observes(snapshot):
            return
        value = prefix(payload)
        if value and len(encoded(value).encode('utf-8')) <= 512 * 1024:
            self.candidates[run_id] = (instance['id'], instance['input_version'], snapshot, value)

    def finish_business(self, run_id, accepted):
        value = self.candidates.pop(run_id, None)
        if value and accepted:
            self.baselines[(value[0], value[2].connection_id, value[2].model.id)] = value

    def apply(self, instance_id, snapshot, payload):
        if not self.observes(snapshot):
            return payload
        row = self.database.one("SELECT * FROM explicit_caches WHERE instance_id=? AND connection_id=? AND model_id=? AND fingerprint=? AND status='active' AND expires_at>? ORDER BY created_at DESC LIMIT 1",
                                (instance_id, snapshot.connection_id, snapshot.model.id, model_fingerprint(snapshot), time() + 5))
        if not row or json.loads(row['prefix_json']) != prefix(payload):
            return payload
        value = deepcopy(payload)
        for key in ('systemInstruction', 'tools', 'toolConfig'):
            value.pop(key, None)
        value['cachedContent'] = row['name']
        return value

    def selection_locked(self, instance_id, snapshot):
        """显式缓存的toolConfig不可变；修复不能为强制选择而绕过该资源。"""
        return self.observes(snapshot) and bool(self.database.one(
            "SELECT id FROM explicit_caches WHERE instance_id=? AND connection_id=? AND model_id=? AND fingerprint=? AND status='active' AND expires_at>? LIMIT 1",
            (instance_id, snapshot.connection_id, snapshot.model.id, model_fingerprint(snapshot), time() + 5)))

    def catalog(self):
        rows = self.database.all('SELECT id,instance_id,connection_id,model_id,model,name,expires_at,status,revision,token_count,created_at,updated_at,error FROM explicit_caches ORDER BY created_at DESC LIMIT 100')
        for row in rows:
            if row['status'] == 'active' and row['expires_at'] <= time():
                row['status'] = 'expired'
        return {'items': rows, 'baselines': [{'instance_id': key[0], 'connection_id': key[1], 'model_id': key[2], 'bytes': len(encoded(value[3]).encode('utf-8'))} for key, value in self.baselines.items()],
                'operations': self.database.all('SELECT * FROM explicit_cache_operations ORDER BY id DESC LIMIT 50')}

    async def _http(self, snapshot, method, suffix='', body=None):
        url = snapshot.connection.options.gemini_explicit_cache_url.rstrip('/') + suffix
        async with asyncio.timeout(40):
            async with self.models.client_for(snapshot).stream(method, url, headers={'x-goog-api-key': snapshot.api_key}, json=body,
                                                 timeout=min(30, snapshot.connection.timeout_seconds)) as response:
                if not 200 <= response.status_code < 300:
                    raise ValueError(f'缓存资源接口返回 HTTP {response.status_code}；未自动重试。')
                if method == 'DELETE':
                    return {}
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=32768):
                    if len(content) + len(chunk) > 2 * 1024 * 1024:
                        raise ValueError('缓存资源响应超过 2 MiB。')
                    content.extend(chunk)
                try:
                    data = json.loads(content)
                except (ValueError, RecursionError) as exc:
                    raise ValueError('缓存资源接口返回了无效 JSON。') from exc
                if not isinstance(data, dict):
                    raise ValueError('缓存资源响应必须为对象。')
                return data

    async def create(self, body):
        if self.closing:
            raise ValueError('服务正在退出。')
        self.store.instance(body.instance_id)
        snapshot = scoped_snapshot(self.store, self.models.snapshot(body.connection_id, body.model_id), body.instance_id)
        value = self.baselines.get((body.instance_id, body.connection_id, body.model_id))
        if not self.observes(snapshot) or not value or model_fingerprint(value[2]) != model_fingerprint(snapshot):
            raise ValueError('请先确认本连接支持显式缓存，并完成一轮有效 Gemini 业务以取得当前固定前缀。')
        if value[1] != self.store.instance(body.instance_id)['input_version']:
            raise ValueError('生活线输入／资料已变化，请等待新的有效业务前缀。')
        identifier, now = uuid4().hex, time()
        with self.database.transaction() as tx:
            if tx.execute("SELECT 1 FROM explicit_caches WHERE instance_id=? AND connection_id=? AND model_id=? AND status NOT IN ('deleted','expired','disabled') AND (expires_at IS NULL OR expires_at>?)", (body.instance_id, body.connection_id, body.model_id, now)).fetchone():
                raise ValueError('已有未结束或结果未知的缓存对象，请先查询、停用或删除。')
            if tx.execute('SELECT count(*) FROM explicit_caches WHERE instance_id=?', (body.instance_id,)).fetchone()[0] >= 200:
                raise ValueError('本线缓存对象记录已达 200 条，暂不新增。')
            tx.execute("INSERT INTO explicit_caches(id,instance_id,connection_id,model_id,model,fingerprint,prefix_json,status,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'creating',1,?,?)",
                       (identifier, body.instance_id, body.connection_id, body.model_id, snapshot.model.model, model_fingerprint(snapshot), encoded(value[3]), now, now))
        return await self._perform(identifier, snapshot, 'create', 1, body.ttl_seconds)

    async def operate(self, identifier, body):
        row = self.database.one('SELECT * FROM explicit_caches WHERE id=?', (identifier,))
        if not row:
            raise LookupError('缓存对象不存在。')
        if row['revision'] != body.revision or row['status'] in {'creating','extending','deleting','refreshing'}:
            raise RevisionConflict('缓存对象已变化或正在操作。')
        if body.action == 'disable':
            with self.database.transaction() as tx:
                tx.execute("UPDATE explicit_caches SET status='disabled',revision=revision+1,updated_at=?,error='本地停用；远端对象按原有效期到期，可能继续产生存储费用。' WHERE id=? AND revision=?", (time(), identifier, body.revision))
            self.store.events.publish('models')
            return self.catalog()
        snapshot = scoped_snapshot(self.store, self.models.snapshot(row['connection_id'], row['model_id']), row['instance_id'])
        if not self.observes(snapshot) or model_fingerprint(snapshot) != row['fingerprint']:
            raise ValueError('连接或模型配置已变化；旧对象只能本地停用，请通过原供应商管理剩余远端资源。')
        return await self._perform(identifier, snapshot, body.action, body.revision, body.ttl_seconds)

    async def _perform(self, identifier, snapshot, action, revision, ttl):
        if self.closing:
            raise ValueError('服务正在退出。')
        task = asyncio.current_task()
        self.tasks.add(task)
        status, error, began = 'unknown', None, False
        try:
            with self.database.transaction() as tx:
                row = self.database.one('SELECT * FROM explicit_caches WHERE id=?', (identifier,))
                if row['revision'] != revision:
                    raise RevisionConflict('缓存对象已变化。')
                state = {'create':'creating','extend':'extending','delete':'deleting','refresh':'refreshing'}[action]
                tx.execute('UPDATE explicit_caches SET status=?,revision=revision+1,updated_at=? WHERE id=?', (state, time(), identifier))
                operation = tx.execute("INSERT INTO explicit_cache_operations(cache_id,operation,status,created_at) VALUES (?,?,'running',?)", (identifier, action, time())).lastrowid
            began = True
            suffix = '/' + row['name'].split('/', 1)[1] if row['name'] else ''
            if action == 'create':
                data = await self._http(snapshot, 'POST', body={'model': 'models/' + snapshot.model.model.removeprefix('models/'), 'displayName': 'RP-' + identifier, 'ttl': f'{ttl}s', **json.loads(row['prefix_json'])})
            elif not row['name']:
                # 未知创建只查询资源目录，不重复创建或假称不存在。
                data = None
                page = ''
                from urllib.parse import urlencode
                for _ in range(5):
                    listing = await self._http(snapshot, 'GET', '?' + urlencode({'pageSize':100, **({'pageToken':page} if page else {})}))
                    data = next((item for item in listing.get('cachedContents', []) if item.get('displayName') == 'RP-' + identifier), None)
                    page = listing.get('nextPageToken')
                    if data or not page:
                        break
                if not data:
                    raise ValueError('本次查询未找到目标对象，仍不能证明此前创建未成功。')
                if action != 'refresh':
                    raise ValueError('请先查询取得资源编号，再延长或删除。')
            elif action == 'delete':
                await self._http(snapshot, 'DELETE', suffix)
                data, status = {}, 'deleted'
            else:
                data = await self._http(snapshot, 'PATCH' if action == 'extend' else 'GET', suffix + ('?updateMask=ttl' if action == 'extend' else ''), {'ttl': f'{ttl}s'} if action == 'extend' else None)
            if status != 'deleted':
                name = data.get('name')
                if not isinstance(name, str) or re.fullmatch(r'cachedContents/[A-Za-z0-9_-]+', name) is None:
                    raise ValueError('缓存资源缺少有效编号，结果仍未知。')
                if row['name'] and name != row['name']:
                    raise ValueError('查询返回的缓存对象与目标不一致，未采用。')
                expiry = datetime.fromisoformat(data['expireTime'].replace('Z', '+00:00'))
                if expiry.tzinfo is None:
                    raise ValueError('缓存失效时间缺少时区。')
                tokens = data.get('usageMetadata', {}).get('totalTokenCount')
                with self.database.transaction() as tx:
                    tx.execute('UPDATE explicit_caches SET name=?,expires_at=?,token_count=? WHERE id=?', (name, expiry.timestamp(), tokens if type(tokens) is int and tokens >= 0 else None, identifier))
                status = 'active' if expiry.timestamp() > time() else 'expired'
                if action == 'refresh' and row['status'] == 'disabled':
                    status = 'disabled'
        except asyncio.CancelledError:
            error = '资源操作中断，远端结果未知；未自动重试。'
            raise
        except Exception as exc:
            error = str(exc) if isinstance(exc, (ValueError, RevisionConflict)) and not isinstance(exc, UnicodeError) else f'缓存资源操作未完成（{type(exc).__name__}）。'
            if snapshot.api_key:
                error = error.replace(snapshot.api_key, '[redacted]')
            if not began:
                raise
        finally:
            self.tasks.discard(task)
            if began:
                with self.database.transaction() as tx:
                    tx.execute('UPDATE explicit_caches SET status=?,revision=revision+1,updated_at=?,error=? WHERE id=?', (status, time(), error, identifier))
                    tx.execute('UPDATE explicit_cache_operations SET status=?,finished_at=?,error=? WHERE id=?', (status, time(), error, operation))
                    self.store.information.audit(tx, 'cache.resource_operation', {'id': identifier, 'action': action, 'status': status})
                self.store.events.publish('models')
        return self.catalog()

    async def close(self):
        self.closing = True
        tasks = list(self.tasks)
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
