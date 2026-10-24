"""MCP 连接、显式目录同步和生活线授权；读取管理页面不会建立外部连接。"""
import asyncio
from contextlib import asynccontextmanager
import json
import logging
from time import time
from uuid import uuid4

from ..llm.messages import ToolSchema
from ..roleplay.store import encoded, invalidate
from ..storage.configuration import RevisionConflict
from .mcp_client import McpError, canonical, discover, open_session, tool_alias

logger = logging.getLogger('rp_server.mcp')


class McpService:
    def __init__(self, store, credentials):
        self.store, self.database, self.events = store, store.database, store.events
        self.credentials = credentials
        self.syncs, self.runs = {}, set()
        self.slots = asyncio.Semaphore(2)
        self.closing = False
        # SDK 日志可能带任意服务端内容；统一记录经过裁剪的本地诊断。
        sdk_log = logging.getLogger('mcp')
        self.sdk_log_state = (sdk_log.level, sdk_log.propagate)
        self.sdk_log_handler = logging.NullHandler()
        sdk_log.addHandler(self.sdk_log_handler); sdk_log.propagate = False
        sdk_log.setLevel(logging.CRITICAL + 1)
        # SDK 的协议校验分支也直接调用 root logging，不能仅设置 mcp 子日志级别。
        self.sdk_root_filter = lambda record: '/mcp/' not in record.pathname.replace('\\', '/')
        logging.getLogger().addFilter(self.sdk_root_filter)
        with self.database.transaction() as tx:
            tx.execute("UPDATE mcp_syncs SET status='interrupted',finished_at=?,error='服务中断，未自动同步。' WHERE status IN ('queued','running')", (time(),))
            tx.execute("UPDATE mcp_calls SET status='unknown',finished_at=?,error='服务中断，远端结果未知，未自动重试。' WHERE status IN ('connecting','running')", (time(),))

    def row(self, identifier):
        row = self.database.one('SELECT * FROM mcp_servers WHERE id=?', (identifier,))
        if row is None:
            raise LookupError('MCP 连接不存在。')
        return row

    @staticmethod
    def public(row):
        return {**json.loads(row['config_json']), 'id': row['id'], 'revision': row['revision'],
                'key_configured': row['credential_ref'] is not None, 'catalog_revision': row['catalog_revision'],
                'tools': json.loads(row['tools_json']), 'discovered_at': row['discovered_at'], 'last_error': row['last_error']}

    def catalog(self):
        return {'servers': [self.public(row) for row in self.database.all('SELECT * FROM mcp_servers ORDER BY created_at')],
                'syncs': self.database.all("SELECT * FROM mcp_syncs ORDER BY status IN ('queued','running') DESC,created_at DESC LIMIT 20"),
                'active_runs': len(self.runs)}

    def audit(self, tx, action, details):
        tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)', (time(), action, encoded(details)))

    def revoke_server(self, tx, identifier, reason):
        for row in tx.execute('SELECT * FROM mcp_permissions').fetchall():
            tools = json.loads(row['tools_json'])
            remaining = [tool for tool in tools if tool['server_id'] != identifier]
            if len(remaining) != len(tools):
                tx.execute('UPDATE mcp_permissions SET tools_json=?,revision=revision+1,updated_at=? WHERE instance_id=?', (encoded(remaining), time(), row['instance_id']))
                invalidate(tx, row['instance_id'], reason)

    def save(self, body, identifier=None):
        old = self.row(identifier) if identifier else None
        previous = json.loads(old['config_json']) if old else None
        if old and old['credential_ref'] and body.key_action == 'keep' and (previous['url'], previous['auth_type']) != (body.url, body.auth_type):
            raise ValueError('地址或认证方式改变后，请重新填写或明确清除密钥。')
        data = body.model_dump(exclude={'revision', 'token', 'key_action'})
        changed = old is None or body.key_action != 'keep' or previous != data
        target, now = identifier or uuid4().hex, time()
        ciphertext = self.credentials.encrypt(body.token) if body.key_action == 'replace' else None
        reference = uuid4().hex if ciphertext else None if body.key_action == 'clear' else old['credential_ref'] if old else None
        with self.database.transaction() as tx:
            current = tx.execute('SELECT revision FROM mcp_servers WHERE id=?', (target,)).fetchone()
            if (current['revision'] if current else 0) != body.revision:
                raise RevisionConflict('MCP 配置已更新，请重新载入。')
            if not changed:
                return self.public(old)
            if not old and tx.execute('SELECT count(*) FROM mcp_servers').fetchone()[0] >= 32:
                raise ValueError('最多保存 32 个 MCP 连接。')
            if ciphertext:
                tx.execute('INSERT INTO encrypted_secrets VALUES (?,?,?,?)', (reference, ciphertext, now, now))
            if changed and old:
                self.revoke_server(tx, target, 'MCP 配置已更改，旧授权与待发送计划已撤销。')
            tx.execute('''INSERT INTO mcp_servers(id,config_json,credential_ref,revision,created_at,updated_at) VALUES (?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json,credential_ref=excluded.credential_ref,
                revision=excluded.revision,updated_at=excluded.updated_at''', (target, encoded(data), reference, body.revision + 1, now, now))
            if changed:
                tx.execute("UPDATE mcp_servers SET tools_json='[]',catalog_revision=catalog_revision+1,discovered_at=NULL,last_error=NULL WHERE id=?", (target,))
            self.audit(tx, 'mcp.saved', {'id': target, 'revision': body.revision + 1})
        self.events.publish('mcp')
        return self.public(self.row(target))

    def start_sync(self, identifier, revision):
        if self.closing or identifier in self.syncs or len(self.syncs) >= 2:
            raise ValueError('目录同步进行中，请稍后或取消。')
        row = self.row(identifier)
        if row['revision'] != revision:
            raise RevisionConflict('MCP 配置已变化，请刷新后同步。')
        config = json.loads(row['config_json'])
        if not config['enabled']:
            raise ValueError('请先启用此连接。')
        target = uuid4().hex
        with self.database.transaction() as tx:
            tx.execute("INSERT INTO mcp_syncs(id,server_id,server_revision,status,created_at) VALUES (?,?,?,'queued',?)", (target, identifier, revision, time()))
        task = asyncio.create_task(self._sync(target, row), name=f'mcp-discover-{identifier}')
        self.syncs[identifier] = (target, task)
        task.add_done_callback(lambda done: self.sync_done(identifier, done))
        self.events.publish('mcp')
        return {'id': target, 'status': 'queued'}

    def sync_done(self, identifier, task):
        self.syncs.pop(identifier, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error('MCP 同步任务异常类型 %s', type(task.exception()).__name__)

    async def _sync(self, sync_id, row):
        config = json.loads(row['config_json'])
        try:
            with self.database.transaction() as tx:
                tx.execute("UPDATE mcp_syncs SET status='running' WHERE id=? AND status='queued'", (sync_id,))
            secret = self.credentials.read(row['credential_ref']) if row['credential_ref'] else ''
            changed_during_sync = asyncio.Event()
            async with asyncio.timeout(config['connect_timeout']):
                async with open_session(config, secret, changed_during_sync.set) as session:
                    tools = await discover(session, secret)
                    if changed_during_sync.is_set():
                        raise McpError('同步期间远端目录变化，未采用；请重新同步。')
            with self.database.transaction() as tx:
                current = tx.execute('SELECT revision,tools_json,catalog_revision FROM mcp_servers WHERE id=?', (row['id'],)).fetchone()
                status = tx.execute('SELECT status FROM mcp_syncs WHERE id=?', (sync_id,)).fetchone()['status']
                if status != 'running' or current['revision'] != row['revision']:
                    raise RevisionConflict('同步期间配置已变化，目录未采用。')
                changed = canonical(json.loads(current['tools_json'])) != canonical(tools)
                if changed:
                    self.revoke_server(tx, row['id'], 'MCP 工具目录变化，旧授权已撤销，请重新选择。')
                tx.execute('UPDATE mcp_servers SET tools_json=?,catalog_revision=catalog_revision+?,discovered_at=?,last_error=NULL WHERE id=?', (encoded(tools), int(changed), time(), row['id']))
                tx.execute("UPDATE mcp_syncs SET status='succeeded',tool_count=?,finished_at=? WHERE id=?", (len(tools), time(), sync_id))
                self.audit(tx, 'mcp.discovered', {'server_id': row['id'], 'tools': len(tools), 'changed': changed})
        except asyncio.CancelledError:
            self.finish_sync(sync_id, 'interrupted' if self.closing else 'cancelled', '同步已停止；未自动重试。')
            raise
        except Exception as exc:
            message = str(exc) if isinstance(exc, McpError) else f'连接或目录同步未完成（{type(exc).__name__}）；未自动重试。'
            self.finish_sync(sync_id, 'failed', message)
        finally:
            self.events.publish('mcp')

    def finish_sync(self, identifier, status, error):
        with self.database.transaction() as tx:
            row = tx.execute('SELECT * FROM mcp_syncs WHERE id=?', (identifier,)).fetchone()
            if row and row['status'] in {'queued', 'running'}:
                tx.execute('UPDATE mcp_syncs SET status=?,error=?,finished_at=? WHERE id=?', (status, error, time(), identifier))
                tx.execute('UPDATE mcp_servers SET last_error=? WHERE id=? AND revision=?', (error, row['server_id'], row['server_revision']))

    def cancel_sync(self, identifier):
        row = self.database.one('SELECT * FROM mcp_syncs WHERE id=?', (identifier,))
        if row is None:
            raise LookupError('同步记录不存在。')
        entry = self.syncs.get(row['server_id'])
        if entry and entry[0] == identifier:
            entry[1].cancel()
        self.finish_sync(identifier, 'cancelled', '管理员取消同步，未自动重试。')
        self.events.publish('mcp')
        return {'status': 'cancel_requested'}

    def permissions(self, instance_id):
        self.store.instance(instance_id)
        row = self.database.one('SELECT * FROM mcp_permissions WHERE instance_id=?', (instance_id,))
        return {'revision': row['revision'] if row else 0, 'tools': json.loads(row['tools_json']) if row else []}

    def save_permissions(self, instance_id, body):
        self.store.instance(instance_id)
        keys = [(tool.server_id, tool.name) for tool in body.tools]
        if len(keys) != len(set(keys)):
            raise ValueError('工具选择重复。')
        with self.database.transaction() as tx:
            old = self.permissions(instance_id)
            if old['revision'] != body.revision:
                raise RevisionConflict('此生活线的 MCP 授权已更新，请重新载入。')
            for tool in body.tools:
                server = self.row(tool.server_id)
                config = json.loads(server['config_json'])
                actual = next((item for item in json.loads(server['tools_json']) if item['name'] == tool.name), None)
                if not config['enabled'] or server['catalog_revision'] != tool.catalog_revision or not actual or not actual['eligible']:
                    raise RevisionConflict('工具目录、连接状态或可用范围已变化，请重新选择。')
            tools = [tool.model_dump() for tool in body.tools]
            if old['tools'] == tools:
                return old
            tx.execute('INSERT INTO mcp_permissions VALUES (?,?,?,?) ON CONFLICT(instance_id) DO UPDATE SET tools_json=excluded.tools_json,revision=excluded.revision,updated_at=excluded.updated_at', (instance_id, encoded(tools), body.revision + 1, time()))
            invalidate(tx, instance_id, '此生活线的 MCP 授权已变化，旧运行和待发送计划已取消。')
            self.audit(tx, 'mcp.permissions_saved', {'instance_id': instance_id, 'count': len(tools)})
        self.events.publish('mcp', instance_ids=(instance_id,))
        return self.permissions(instance_id)

    def capture(self, instance_id):
        result = {}
        for grant in self.permissions(instance_id)['tools']:
            row = self.row(grant['server_id'])
            config = json.loads(row['config_json'])
            tool = next((item for item in json.loads(row['tools_json']) if item['name'] == grant['name']), None)
            if config['enabled'] and row['catalog_revision'] == grant['catalog_revision'] and tool and tool['eligible']:
                alias = tool_alias(row['id'], tool['name'])
                result[alias] = {'server': row, 'config': config, 'tool': tool,
                    'schema': ToolSchema(alias, f"外部只读查询 · {config['name']} · {tool['name']}\n{tool['description'] or ''}", tool['inputSchema'])}
        return result

    def allowed(self, instance_id, item):
        row = self.row(item['server']['id'])
        if row['revision'] != item['server']['revision'] or row['catalog_revision'] != item['server']['catalog_revision'] or not json.loads(row['config_json'])['enabled']:
            raise McpError('MCP 配置或工具目录变化，未执行。')
        grant = {'server_id': row['id'], 'name': item['tool']['name'], 'catalog_revision': row['catalog_revision']}
        if grant not in self.permissions(instance_id)['tools']:
            raise McpError('当前生活线已无此工具授权，未执行。')

    def calls(self, instance_id=None):
        if instance_id:
            self.store.instance(instance_id)
        return self.database.all('SELECT * FROM mcp_calls' + (' WHERE instance_id=?' if instance_id else '') + ' ORDER BY id DESC LIMIT 50', (instance_id,) if instance_id else ())

    @asynccontextmanager
    async def scope(self, instance, run_id):
        from .mcp_scope import McpRunScope
        if self.closing:
            raise McpError('MCP 正在关闭。')
        task = asyncio.current_task(); self.runs.add(task)
        try:
            async with McpRunScope(self, instance, run_id) as scope:
                yield scope
        finally:
            self.runs.discard(task)

    async def close(self):
        self.closing = True
        sync_ids = [value[0] for value in self.syncs.values()]
        tasks = [value[1] for value in self.syncs.values()] + list(self.runs)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for identifier in sync_ids:
            self.finish_sync(identifier, 'interrupted', '服务关闭；未自动同步。')
        sdk_log = logging.getLogger('mcp')
        sdk_log.setLevel(self.sdk_log_state[0]); sdk_log.propagate = self.sdk_log_state[1]
        sdk_log.removeHandler(self.sdk_log_handler)
        logging.getLogger().removeFilter(self.sdk_root_filter)
