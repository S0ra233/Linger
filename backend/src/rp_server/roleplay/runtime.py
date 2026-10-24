"""会话协调与固定投递程序；轮询数据库本身不会调用模型。"""
import asyncio
import logging
import json
from time import time, monotonic
from pydantic import ValidationError

from .decisions import RuntimeSettings
from .engine import RpEngine, RunError
from .store import RpStore, StaleRun
from .run_diagnostics import RunProgress, field_issues


logger = logging.getLogger('rp_server.roleplay')


class RpRuntime:
    def __init__(self, store: RpStore, models, gateway, context):
        self.store, self.gateway = store, gateway
        self.engine = RpEngine(store, models, context)
        self.generations: dict[str, tuple[int, asyncio.Task, int]] = {}
        self.deliveries: dict[str, tuple[str, asyncio.Task]] = {}
        self.task: asyncio.Task | None = None
        self._drain_waiter: asyncio.Future | None = None
        self.closing = False
        self.last_error: str | None = None
        gateway.on_receipt = store.receipt

    def start(self):
        self.store.recover()
        self.task = asyncio.create_task(self._coordinate(), name='rp-coordinator')

    def summary(self):
        return {'generating': len(self.generations), 'sending': len(self.deliveries), 'last_error': self.last_error}

    async def _generate(self, run_id: int, instance: dict, through: int, settings: RuntimeSettings):
        accepted = False
        progress = RunProgress()
        deadline = asyncio.timeout(settings.run_timeout)
        try:
            async with deadline:
                decision = await self.engine.run(run_id, instance, through, settings, progress=progress)
            progress.enter('commit')
            self.store.commit(run_id, decision, settings)
            accepted = True
        except asyncio.CancelledError:
            reason = '运行已取消；未确认送达的输入仍保留。'
            self.store.finish_run(run_id, 'cancelled', reason, failure=progress.issue('cancelled', reason))
            raise
        except StaleRun:
            reason = '输入、绑定或运行限制已变化，旧计划未提交。'
            self.store.finish_run(run_id, 'cancelled', reason, failure=progress.issue('stale_run', reason))
        except TimeoutError:
            reason = '本轮超过总时限；未自动重新调用。' if deadline.expired() else '当前阶段超时；未自动重新调用。'
            self.store.finish_run(run_id, 'failed', reason, failure=progress.issue('timeout', reason))
        except RunError as exc:
            issue = exc.issue
            if issue.stage == progress.stage:
                issue = issue.model_copy(update={'stage_elapsed_ms': progress.elapsed_ms()})
            self.store.finish_run(run_id, 'failed', str(exc), failure=issue)
        except ValueError as exc:
            # 提交阶段只有本地业务校验，不把Provider／凭据读取阶段的原始异常透传。
            if progress.stage == 'commit':
                reason = '；'.join(item['msg'] for item in exc.errors(include_input=False)[:5]) if isinstance(exc, ValidationError) else str(exc)
                self.store.finish_run(run_id, 'failed', '计划提交校验未通过：' + reason[:1000],
                    failure=progress.issue('commit_validation', reason, fields=field_issues(exc)))
            else:
                logger.error('RP 运行 %s 失败，异常类型 %s', run_id, type(exc).__name__)
                reason = f'运行未完成（{type(exc).__name__}），请查看配置与步骤。'
                self.store.finish_run(run_id, 'failed', reason, failure=progress.issue('internal', reason))
        except Exception as exc:
            # 厂商异常可能携带请求与凭据，不直接存异常文本。
            logger.error('RP 运行 %s 失败，异常类型 %s', run_id, type(exc).__name__)
            reason = f'运行未完成（{type(exc).__name__}），请查看配置与步骤。'
            self.store.finish_run(run_id, 'failed', reason, failure=progress.issue('internal', reason))
        finally:
            if self.store.cache: self.store.cache.finish_business(run_id, accepted)
            if self.store.explicit_cache: self.store.explicit_cache.finish_business(run_id, accepted)

    async def _deliver(self, item: dict, settings: RuntimeSettings):
        started = False

        def claim():
            nonlocal started
            if item.get('parts_json') and self.store.media:
                for part in json.loads(item['parts_json']):
                    if part['type'] != 'asset': continue
                    asset = self.store.database.one('SELECT instance_id,revision,archived FROM media_assets WHERE id=?', (part['asset_id'],))
                    if not asset or asset['instance_id'] != item['instance_id'] or asset['revision'] != part['revision'] or asset['archived']:
                        raise ValueError('素材授权或版本已变化。')
            started = self.store.claim(item['id']) is not None
            return started

        try:
            parts = json.loads(item.get('parts_json') or '[]')
            content = await self.store.media.segments(item['instance_id'], parts) if parts else item['text']
            result = await self.gateway.send_private(item['account_id'], item['peer_id'], content, item['id'], settings.receipt_timeout, claim)
            if result['status'] != 'not_started':
                self.store.receipt(item['account_id'], item['id'], result)
        except asyncio.CancelledError:
            if started:
                self.store.receipt(item['account_id'], item['id'], {'status': 'unknown', 'error': '投递任务取消时结果尚未确认，不自动重发。'})
            raise
        except Exception as exc:
            logger.error('投递 %s 异常，类型 %s', item['id'], type(exc).__name__)
            if started:
                self.store.receipt(item['account_id'], item['id'], {'status': 'unknown', 'error': '投递中断，结果未知，不自动重发。'})
            else:
                with self.store.database.transaction() as tx:
                    tx.execute("UPDATE rp_outbox SET status='failed',finished_at=?,error='素材准备或授权校验失败，未发送。' WHERE id=? AND status='pending'", (time(), item['id']))
                    self.store.plans.sync(tx)
                self.store.events.publish('runtime')

    async def _coordinate(self):
        try:
            while not self.closing:
                raw = self.store.settings()
                settings = RuntimeSettings.model_validate({key: value for key, value in raw.items() if key != 'revision'})
                for instance_id, (version, task, run_id) in list(self.generations.items()):
                    if task.done():
                        # 显式取异常，避免任务在数据库故障时无声消失。
                        if not task.cancelled() and task.exception():
                            raise task.exception()
                        del self.generations[instance_id]
                    elif not settings.enabled or not self.store.valid(instance_id, version, run_id):
                        if not task.cancelling(): task.cancel()
                for key, (_, task) in list(self.deliveries.items()):
                    if task.done():
                        if not task.cancelled() and task.exception():
                            raise task.exception()
                        del self.deliveries[key]
                self.store.sweep()
                if self.store.extensions:
                    self.store.extensions.poll()
                if self.store.cache:
                    self.store.cache.poll(self, settings)
                if settings.enabled:
                    connected = [key for key, connection in self.gateway.connections.items() if connection.accepting]
                    for candidate in self.store.candidates(connected):
                        if len(self.generations) + (len(self.store.cache.tasks) if self.store.cache else 0) >= settings.concurrency:
                            break
                        if candidate['id'] in self.generations or time() < candidate['last_input'] + settings.debounce_seconds:
                            continue
                        if not self.gateway.state(self.store.instance(candidate['id'])['account_id'])['connected']:
                            continue
                        started = self.store.begin(candidate, settings)
                        if started:
                            run_id, instance = started
                            task = asyncio.create_task(self._generate(run_id, instance, candidate['input_through'], settings), name=f'rp-run-{run_id}')
                            self.generations[instance['id']] = (instance['input_version'], task, run_id)
                    busy = {instance_id for instance_id, _ in self.deliveries.values()}
                    for item in self.store.pending(connected):
                        if len(self.deliveries) >= settings.concurrency:
                            break
                        if item['instance_id'] in busy or not self.gateway.state(item['account_id'])['connected']:
                            continue
                        task = asyncio.create_task(self._deliver(item, settings), name=f"rp-delivery-{item['id']}")
                        self.deliveries[item['id']] = (item['instance_id'], task)
                        busy.add(item['instance_id'])
                await asyncio.sleep(.25)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = '运行协调已停止，请检查日志并重启服务。'
            logger.error('RP 协调器停止，异常类型 %s', type(exc).__name__)
            try:
                await self._cancel_active()
            finally:
                await self._close_support()
                self.store.events.publish('runtime')

    async def _cancel_active(self):
        # 协调停止后不能遗留等待网关锁的投递；已认领投递仍按未知结果收束，不重发。
        if self._drain_waiter is None:
            tasks = [task for _, task, _ in self.generations.values()] + [task for _, task in self.deliveries.values()]
            for task in tasks:
                if not task.done() and not task.cancelling(): task.cancel()
            self._drain_waiter = asyncio.gather(*tasks, return_exceptions=True)
        # close() 可能在异常收束期间取消协调器；不把第二次取消传播到正在关闭的 MCP／回执。
        await asyncio.shield(self._drain_waiter)
        self.generations.clear()
        self.deliveries.clear()

    async def close(self):
        self.closing = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        try:
            await self._cancel_active()
        finally:
            await self._close_support()

    async def _close_support(self):
        resources = [resource for resource in (self.store.cache, self.store.extensions, self.store.explicit_cache) if resource]
        results = await asyncio.gather(*(resource.close() for resource in resources), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
