"""持久图片任务：输入先入库，图片下载和解码均不占用事务。"""
import asyncio
import json
import logging
from time import time
from urllib.parse import urljoin, urlsplit

import httpx

from ..events import EventBus
from ..llm.messages import ImageContent
from ..storage.database import Database
from .images import MAX_SOURCE_BYTES, MAX_REQUEST_BYTES
from .store import ImageStore


logger = logging.getLogger('rp_server.attachments')


class ImageReceiveError(ValueError):
    """可公开的固定诊断，不包含原始地址与网关正文。"""


def checked_image_url(value: str) -> str:
    """只向固定 QQ 图片域名请求，不允许消息指定内网、磁盘或任意第三方站点。"""
    parsed = urlsplit(value)
    host = (parsed.hostname or '').lower().rstrip('.')
    allowed = (host == 'multimedia.nt.qq.com.cn'
               or any(host == suffix or host.endswith('.' + suffix) for suffix in ('qpic.cn', 'qq.com')))
    if (parsed.scheme not in {'https', 'http'} or not allowed or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in {None, 80, 443}):
        raise ImageReceiveError('图片地址须来自 qpic.cn、qq.com 或 multimedia.nt.qq.com.cn，且使用 HTTP(S)。')
    return value


class InboundImages:
    def __init__(self, database: Database, events: EventBus, client: httpx.AsyncClient, images: ImageStore):
        self.database, self.events, self.client, self.images = database, events, client, images
        self.wakeup = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.closing = False
        self.last_error: str | None = None

    def start(self):
        # 上次进程可能在网络或解码中退出；只恢复附件读取，不重放聊天或调用模型。
        with self.database.transaction() as tx:
            tx.execute("UPDATE inbound_images SET status='pending',updated_at=? WHERE status='fetching'", (time(),))
        self.tasks = [asyncio.create_task(self._worker(), name=f'qq-images-{index}') for index in range(2)]
        self.wakeup.set()

    def wake(self):
        self.wakeup.set()

    def summary(self):
        counts = {row['status']: row['count'] for row in self.database.all(
            'SELECT status,count(*) AS count FROM inbound_images GROUP BY status')}
        return {'counts': counts, 'last_error': self.last_error}

    def retry(self, instance_id: str, image_id: str):
        with self.database.transaction() as tx:
            row = tx.execute('''SELECT i.status,i.source_url FROM inbound_images i JOIN inbound_messages m ON m.id=i.message_id
                WHERE i.id=? AND m.instance_id=? AND m.deleted_at IS NULL''', (image_id, instance_id)).fetchone()
            if not row:
                raise LookupError('图片不存在。')
            if row['status'] != 'failed':
                raise ValueError('只能重新接收失败的图片。')
            if not row['source_url']:
                raise ValueError('网关未提供图片 URL，无法重新接收。')
            tx.execute("UPDATE inbound_images SET status='pending',error=NULL,updated_at=? WHERE id=?", (time(), image_id))
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (time(), 'image.retry', json.dumps({'id': image_id})))
        self.wake()
        self.events.publish('messages')
        return {'status': 'pending'}

    def image_path(self, instance_id: str, image_id: str):
        row = self.database.one('''SELECT i.attachment_id FROM inbound_images i
            JOIN inbound_messages m ON m.id=i.message_id WHERE i.id=? AND m.instance_id=? AND m.deleted_at IS NULL AND i.status='ready' ''',
            (image_id, instance_id))
        if not row:
            raise LookupError('图片尚未接收成功或不属于这条生活线。')
        path = self.images.image_path(ImageContent(row['attachment_id']))
        if not path.is_file():
            raise LookupError('图片文件缺失，请检查附件备份。')
        return path

    def _claim(self):
        with self.database.transaction() as tx:
            row = tx.execute("SELECT i.* FROM inbound_images i JOIN inbound_messages m ON m.id=i.message_id WHERE i.status='pending' AND m.deleted_at IS NULL ORDER BY i.updated_at,i.id LIMIT 1").fetchone()
            if not row:
                return None
            tx.execute("UPDATE inbound_images SET status='fetching',updated_at=? WHERE id=?", (time(), row['id']))
            return dict(row)

    async def _download(self, source: str) -> bytes:
        url = checked_image_url(source)
        for _ in range(4):
            async with self.client.stream('GET', url, timeout=httpx.Timeout(30, connect=10),
                                          headers={'Accept': 'image/*', 'Accept-Encoding': 'identity'}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    url = checked_image_url(urljoin(url, response.headers.get('location', '')))
                    continue
                if response.status_code != 200:
                    raise ImageReceiveError(f'图片下载返回 HTTP {response.status_code}，链接可能已过期。')
                length = response.headers.get('content-length')
                if length and int(length) > MAX_SOURCE_BYTES:
                    raise ImageReceiveError('原图超过 20 MiB。')
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > MAX_SOURCE_BYTES:
                        raise ImageReceiveError('原图超过 20 MiB。')
                    content.extend(chunk)
                return bytes(content)
        raise ImageReceiveError('图片重定向次数过多。')

    def _finish(self, row: dict, attachment=None, error: str | None = None):
        with self.database.transaction() as tx:
            if attachment:
                total = tx.execute("SELECT COALESCE(sum(byte_size),0) FROM inbound_images WHERE message_id=? AND status='ready'",
                                   (row['message_id'],)).fetchone()[0]
                if total + attachment.byte_size > MAX_REQUEST_BYTES:
                    attachment, error = None, '同一消息处理后的图片合计超过 20 MiB。'
            tx.execute('''UPDATE inbound_images SET status=?,attachment_id=?,byte_size=?,error=?,updated_at=?
                WHERE id=? AND status='fetching' ''',
                ('ready' if attachment else 'failed', attachment.attachment_id if attachment else None,
                 attachment.byte_size if attachment else None, error, time(), row['id']))
        self.events.publish('messages')

    async def _worker(self):
        row = None
        try:
            while not self.closing:
                self.wakeup.clear()
                row = self._claim()
                if row is None:
                    await self.wakeup.wait()
                    continue
                try:
                    # 总限时同时约束重定向、慢速分块与排队连接。
                    async with asyncio.timeout(45):
                        source = await self._download(row['source_url'])
                        attachment = await self.images.import_bytes(source)
                    self._finish(row, attachment)
                except (httpx.HTTPError, TimeoutError):
                    self._finish(row, error='图片传输失败或超时，可重新接收。')
                except ImageReceiveError as exc:
                    self._finish(row, error=str(exc))
                except ValueError:
                    self._finish(row, error='图片地址、格式或大小不符合要求；详情见接入说明。')
                except OSError:
                    self._finish(row, error='附件写入失败，请检查数据目录空间与权限。')
                row = None
        except asyncio.CancelledError:
            # 关闭时保留可恢复状态；网络被取消，磁盘线程可能完成独立文件，但未发布数据库引用。
            raise
        except Exception as exc:
            self.last_error = '图片工作器已停止，请检查日志并重启服务。'
            logger.error('图片工作器异常，类型 %s', type(exc).__name__)
            self.events.publish('qq')

    async def close(self):
        self.closing = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        # 进程锁仍在，本进程任务均退出后才退回 pending。
        with self.database.transaction() as tx:
            tx.execute("UPDATE inbound_images SET status='pending',updated_at=? WHERE status='fetching'", (time(),))
