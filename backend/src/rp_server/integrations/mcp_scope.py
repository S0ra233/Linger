"""每轮 RP 独占 SDK 上下文；同一任务进入、调用并关闭，未知请求不重放。"""
import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
import logging
from time import time

from jsonschema import Draft202012Validator

from ..roleplay.store import StaleRun, encoded
from .mcp_client import McpError, canonical, discover, open_session, render_result

logger = logging.getLogger('rp_server.mcp')


class McpRunScope:
    def __init__(self, service, instance, run_id):
        self.service, self.instance, self.run_id = service, instance, run_id
        self.tools = service.capture(instance['id'])
        self.sessions, self.blocked = {}, set()
        self.stack = AsyncExitStack()

    @property
    def schemas(self):
        return [item['schema'] for item in self.tools.values()]

    async def __aenter__(self):
        await self.stack.__aenter__()
        snapshot = [{'alias': alias, 'server_id': item['server']['id'],
                     'server_revision': item['server']['revision'],
                     'catalog_revision': item['server']['catalog_revision'], 'name': item['tool']['name']}
                    for alias, item in self.tools.items()]
        with self.service.database.transaction() as tx:
            tx.execute('UPDATE rp_runs SET mcp_json=? WHERE id=?', (encoded(snapshot), self.run_id))
        return self

    async def __aexit__(self, kind, value, traceback):
        try:
            await self.stack.__aexit__(kind, value, traceback)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 关闭失败不改写远端已返回的结果，也不触发重新执行。
            logger.warning('MCP 连接关闭异常类型 %s', type(exc).__name__)
        return False

    def fresh(self, item):
        if not self.service.store.valid(self.instance['id'], self.instance['input_version'], self.run_id):
            raise StaleRun()
        self.service.allowed(self.instance['id'], item)

    def finish(self, identifier, status, result=None, error=None):
        with self.service.database.transaction() as tx:
            tx.execute('UPDATE mcp_calls SET status=?,result_json=?,error=?,finished_at=? WHERE id=?',
                       (status, encoded(result) if result is not None else None, error, time(), identifier))
        self.service.events.publish('mcp')

    async def call(self, alias, arguments, call_id):
        item = self.tools[alias]
        server_id, config = item['server']['id'], item['config']
        self.fresh(item)
        if server_id in self.blocked:
            raise McpError('此连接本轮已有未知结果或目录变化，未再次调用。')
        # 参数只接受已完整解析的 JSON；错误不包含参数值和服务端任意正文。
        try:
            canonical(arguments)
            error = next(Draft202012Validator(item['tool']['inputSchema']).iter_errors(arguments), None)
            if error is not None:
                raise McpError('MCP 参数不符合当前工具 Schema，请检查字段类型与必填项。')
        except McpError:
            raise
        except Exception as exc:
            raise McpError('MCP 参数或内部 Schema 引用无法校验，未执行。') from exc
        with self.service.database.transaction() as tx:
            identifier = tx.execute('''INSERT INTO mcp_calls(run_id,instance_id,server_id,tool_name,call_id,status,created_at)
                VALUES (?,?,?,?,?,'connecting',?)''',
                (self.run_id, self.instance['id'], server_id, item['tool']['name'], call_id, time())).lastrowid
        sent = False
        try:
            # 限制同时在网请求，不把槽位锁在整个连接上，避免多连接互相等待。
            async with self.service.slots:
                self.fresh(item)
                if server_id not in self.sessions:
                    secret = self.service.credentials.read(item['server']['credential_ref']) if item['server']['credential_ref'] else ''
                    async with asyncio.timeout(config['connect_timeout']):
                        session = await self.stack.enter_async_context(open_session(config, secret, lambda: self.blocked.add(server_id)))
                        live = await discover(session, secret)
                    self.sessions[server_id] = (session, secret, {tool['name']: tool for tool in live})
                session, secret, live = self.sessions[server_id]
                if server_id in self.blocked or canonical(live.get(item['tool']['name'])) != canonical(item['tool']):
                    self.blocked.add(server_id)
                    raise McpError('远端工具目录已变化，请管理员重新同步并授权。')
                self.fresh(item)
                with self.service.database.transaction() as tx:
                    tx.execute("UPDATE mcp_calls SET status='running' WHERE id=?", (identifier,))
                sent = True
                async with asyncio.timeout(config['call_timeout']):
                    raw = await session.call_tool(item['tool']['name'], arguments,
                        read_timeout_seconds=timedelta(seconds=config['call_timeout']))
                result, failed = render_result(raw, secret)
            self.finish(identifier, 'failed' if failed else 'succeeded', result)
            return result, failed
        except (asyncio.CancelledError, StaleRun):
            self.blocked.add(server_id)
            # 已确认的成功不因随后出现的新输入被改为未知；仅中断进行中的调用。
            row = self.service.database.one('SELECT status FROM mcp_calls WHERE id=?', (identifier,))
            if row['status'] in {'connecting', 'running'}:
                self.finish(identifier, 'unknown' if sent else 'cancelled', error='运行中断；远端可能仍在处理，未自动重试。' if sent else '调用前运行已停止。')
            raise
        except Exception as exc:
            self.blocked.add(server_id)
            message = str(exc) if isinstance(exc, McpError) and not sent else f'MCP 请求未完成（{type(exc).__name__}）；未自动重试。'
            self.finish(identifier, 'unknown' if sent else 'failed', error=message)
            return {'error': message, 'result_unknown': sent}, True
