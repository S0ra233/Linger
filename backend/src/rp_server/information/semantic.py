"""可选语义检索与显式建索引；不因轮询、导入或配置模型而自动产生付费请求。"""
import asyncio
from dataclasses import replace
import heapq
import math
import logging
import struct
from time import time
from uuid import uuid4

from ..llm.embedding import EmbeddingClient, EmbeddingError
from ..llm.usage import AttemptUsage
from ..roleplay.store import encoded
from ..storage.configuration import RevisionConflict
from .service import JOIN, digest
from .validity import active_memory_sql

logger = logging.getLogger('rp_server.information')


class SemanticInformation:
    def __init__(self, information, models):
        self.info, self.models = information, models
        self.database, self.events = information.database, information.events
        self.jobs = {}
        self.queries = set()
        self.slots = asyncio.Semaphore(2)
        self.closing = False
        # 重启只标识中断，不能把已可能计费的请求自动再发一遍。
        with self.database.transaction() as tx:
            tx.execute("UPDATE embedding_jobs SET status='interrupted',finished_at=?,error='服务中断；已保存向量保留，未自动续建。' WHERE status IN ('queued','running')", (time(),))
            tx.execute("UPDATE embedding_calls SET status='interrupted',finished_at=?,error='请求结果未知；未自动重发。' WHERE status='requesting'", (time(),))

    def current(self):
        snapshot = self.models.for_purpose('embedding')
        options = snapshot.embedding_options()
        # 相同维度不代表相同语义空间；连接／模型条目、协议地址和用途版本都参与隔离。
        space = digest(f'{snapshot.connection_id}:{snapshot.model.id}:{options.fingerprint()}')
        return snapshot, options, space

    def capture(self, mode):
        if mode == 'keyword':
            return None
        try:
            return self.current()
        except ValueError as exc:
            # 同一 RP 运行保留用途选择；运行中修改连接不偷偷改变请求目的地。
            return str(exc)

    def ensure_space(self, snapshot, options, space):
        data = {'connection_id': snapshot.connection_id, 'model_id': snapshot.model.id,
                'connection_name': snapshot.connection.name, 'model': options.model,
                'protocol': options.protocol, 'requested_dimensions': options.dimensions}
        with self.database.transaction() as tx:
            tx.execute('INSERT OR IGNORE INTO embedding_spaces VALUES (?,?,NULL,?)', (space, encoded(data), time()))

    def target_scope(self, kind, identifier):
        if kind == 'library':
            library = self.info.library(identifier)
            if not library['enabled']:
                raise ValueError('资料库已停用，请先启用。')
            return 'd.library_id=? AND d.revision=c.revision AND d.enabled=1 AND l.enabled=1', (identifier,)
        self.info.store.instance(identifier)
        return 'm.instance_id=? AND m.revision=c.revision AND ' + active_memory_sql(), (identifier, time())

    def coverage(self, space, condition, arguments):
        return self.database.one('SELECT count(*) AS chunks,count(v.chunk_id) AS indexed' + JOIN +
            ' LEFT JOIN information_vectors v ON v.chunk_id=c.id AND v.space_id=? WHERE ' + condition,
            (space, *arguments))

    def status(self, kind=None, identifier=None):
        current, message = None, None
        try:
            snapshot, options, space = self.current()
            stored = self.database.one('SELECT dimensions FROM embedding_spaces WHERE id=?', (space,))
            current = {'space_id': space, 'connection_name': snapshot.connection.name, 'model': options.model,
                       'connection_revision': snapshot.revision, 'dimensions': stored['dimensions'] if stored else None}
            if kind and identifier:
                condition, arguments = self.target_scope(kind, identifier)
                current['coverage'] = self.coverage(space, condition, arguments)
        except (ValueError, LookupError) as exc:
            message = str(exc)
        return {'current': current, 'message': message,
                'jobs': self.database.all('SELECT * FROM embedding_jobs ORDER BY created_at DESC LIMIT 20'),
                'calls': self.database.all('SELECT * FROM embedding_calls ORDER BY id DESC LIMIT 30')}

    def start_job(self, body):
        if self.closing or any(not task.done() for task in self.jobs.values()):
            raise ValueError('已有建索引任务进行中，请等待或取消。')
        self.target_scope(body.scope_kind, body.scope_id)
        snapshot, options, space = self.current()
        if space != body.space_id or snapshot.revision != body.connection_revision:
            raise RevisionConflict('Embedding 配置已变化，请刷新后重新确认建立索引。')
        self.ensure_space(snapshot, options, space)
        identifier = uuid4().hex
        with self.database.transaction() as tx:
            tx.execute('''INSERT INTO embedding_jobs(id,space_id,connection_revision,scope_kind,scope_id,status,
                max_chunks,batch_size,created_at) VALUES (?,?,?,?,?,'queued',?,?,?)''',
                (identifier, space, snapshot.revision, body.scope_kind, body.scope_id, body.max_chunks, body.batch_size, time()))
            self.info.audit(tx, 'embedding.index_requested', {'id': identifier, 'scope_kind': body.scope_kind, 'scope_id': body.scope_id})
        task = asyncio.create_task(self._build(identifier, body, options, space), name=f'index-{identifier}')
        self.jobs[identifier] = task
        task.add_done_callback(lambda done: self.job_done(identifier, done))
        # 索引和用量进度不改既有请求快照；真正资料编辑由Information发布依赖变化。
        self.events.publish('information', instance_ids=())
        return self.database.one('SELECT * FROM embedding_jobs WHERE id=?', (identifier,))

    def job_done(self, identifier, task):
        self.jobs.pop(identifier, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error('索引任务 %s 异常类型 %s', identifier, type(task.exception()).__name__)

    def cancel_job(self, identifier):
        row = self.database.one('SELECT * FROM embedding_jobs WHERE id=?', (identifier,))
        if row is None:
            raise LookupError('索引任务不存在。')
        task = self.jobs.get(identifier)
        if task and not task.done():
            task.cancel()
        with self.database.transaction() as tx:
            tx.execute("UPDATE embedding_jobs SET status='cancelled',finished_at=?,error='管理员取消；已保存向量保留。' WHERE id=? AND status IN ('queued','running')", (time(), identifier))
        self.events.publish('information', instance_ids=())
        return {'status': 'cancel_requested' if task else 'cancelled' if row['status'] in {'queued', 'running'} else row['status']}

    async def embed(self, options, space, texts, purpose, *, job_id=None, instance_id=None, run_id=None):
        if self.closing:
            raise ValueError('服务正在关闭。')
        async with self.slots:
            with self.database.transaction() as tx:
                call_id = tx.execute('''INSERT INTO embedding_calls(space_id,job_id,instance_id,run_id,purpose,status,created_at)
                    VALUES (?,?,?,?,?,'requesting',?)''', (space, job_id, instance_id, run_id, purpose, time())).lastrowid
            try:
                def save_attempts(value):
                    with self.database.transaction() as tx:
                        tx.execute('UPDATE embedding_calls SET usage_json=? WHERE id=?', (encoded({'attempts': value}), call_id))
                attempts = AttemptUsage(save_attempts)
                attempts.begin()
                # 借用统一客户端，显式限制单次等待；未知结果不重试，不保存查询正文和密钥。
                vectors, usage = await EmbeddingClient(replace(options, timeout_seconds=min(options.timeout_seconds, 60)),
                    client=self.models.client).embed(texts, purpose=purpose)
            except asyncio.CancelledError:
                self.finish_call(call_id, 'cancelled', None, '已取消；远端是否计费未知。')
                raise
            except Exception as exc:
                message = str(exc) if isinstance(exc, EmbeddingError) else f'Embedding 未完成（{type(exc).__name__}）。'
                self.finish_call(call_id, 'failed', None, message)
                raise ValueError(message) from exc
            self.finish_call(call_id, 'succeeded', usage or None, None)
            return vectors

    def finish_call(self, identifier, status, usage, error):
        with self.database.transaction() as tx:
            previous = tx.execute('SELECT usage_json FROM embedding_calls WHERE id=?', (identifier,)).fetchone()
            saved = json.loads(previous['usage_json']) if previous and previous['usage_json'] else {}
            saved['usage'] = usage
            tx.execute('UPDATE embedding_calls SET status=?,usage_json=?,error=?,finished_at=? WHERE id=?',
                (status, encoded(saved), error, time(), identifier))
        self.events.publish('information', instance_ids=())

    async def _build(self, identifier, body, options, space):
        processed = saved = skipped = 0
        try:
            with self.database.transaction() as tx:
                tx.execute("UPDATE embedding_jobs SET status='running' WHERE id=? AND status='queued'", (identifier,))
            async with asyncio.timeout(1800):
                while processed < body.max_chunks:
                    condition, arguments = self.target_scope(body.scope_kind, body.scope_id)
                    rows = self.database.all('SELECT c.id,c.text' + JOIN + ' LEFT JOIN information_vectors v ON v.chunk_id=c.id AND v.space_id=?'
                        + ' WHERE v.chunk_id IS NULL AND ' + condition + ' ORDER BY c.id LIMIT ?',
                        (space, *arguments, min(body.batch_size, body.max_chunks - processed)))
                    if not rows:
                        break
                    vectors = await self.embed(options, space, [row['text'] for row in rows], 'document', job_id=identifier)
                    # 网络返回后重新验证资料版本、启用状态和期限，陈旧结果不覆盖当前资料。
                    condition, arguments = self.target_scope(body.scope_kind, body.scope_id)
                    dimension = len(vectors[0])
                    with self.database.transaction() as tx:
                        job = tx.execute('SELECT status FROM embedding_jobs WHERE id=?', (identifier,)).fetchone()
                        if job['status'] != 'running':
                            raise asyncio.CancelledError()
                        previous = tx.execute('SELECT dimensions FROM embedding_spaces WHERE id=?', (space,)).fetchone()['dimensions']
                        if previous is not None and previous != dimension:
                            raise ValueError('同一语义空间返回了不同维度；旧索引保留，请核对模型并显式配置维度。')
                        tx.execute('UPDATE embedding_spaces SET dimensions=? WHERE id=?', (dimension, space))
                        for row, vector in zip(rows, vectors, strict=True):
                            current = tx.execute('SELECT c.id' + JOIN + ' WHERE c.id=? AND ' + condition, (row['id'], *arguments)).fetchone()
                            if current:
                                count = tx.execute('INSERT OR IGNORE INTO information_vectors VALUES (?,?,?,?)',
                                    (space, row['id'], dimension, struct.pack(f'<{dimension}f', *vector))).rowcount
                                saved += count
                            else:
                                skipped += 1
                        processed += len(rows)
                        tx.execute('UPDATE embedding_jobs SET processed=?,saved=?,skipped=? WHERE id=?', (processed, saved, skipped, identifier))
                    self.events.publish('information', instance_ids=())
                    await asyncio.sleep(0)
            self.finish_job(identifier, 'succeeded')
        except asyncio.CancelledError:
            self.finish_job(identifier, 'interrupted' if self.closing else 'cancelled', '已中断；已保存向量保留，当前远端用量可能未知。')
            raise
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ValueError, LookupError)) else f'索引未完成（{type(exc).__name__}）。'
            self.finish_job(identifier, 'failed', message)

    def finish_job(self, identifier, status, error=None):
        with self.database.transaction() as tx:
            tx.execute('UPDATE embedding_jobs SET status=?,error=?,finished_at=? WHERE id=? AND status IN (\'queued\',\'running\')', (status, error, time(), identifier))
        self.events.publish('information', instance_ids=())

    def scoped(self, instance_id, category):
        condition, arguments = self.info.scope(instance_id)
        if category == 'memory':
            condition += ' AND c.memory_id IS NOT NULL'
        elif category == 'knowledge':
            condition += ' AND c.document_id IS NOT NULL'
        return condition, arguments

    async def rank(self, instance_id, vector, space, top_k, category):
        condition, arguments = self.scoped(instance_id, category)
        counts = self.coverage(space, condition, arguments)
        ranked, cursor, scanned, consumed = [], '', 0, 0
        complete = True
        # 分批读取向量，限制向量数和总字节；不把上万条高维向量一次读入内存。
        while True:
            condition, arguments = self.scoped(instance_id, category)
            rows = self.database.all('SELECT c.id,v.dimensions,v.vector' + JOIN +
                ' JOIN information_vectors v ON v.chunk_id=c.id AND v.space_id=? WHERE c.id>? AND ' + condition +
                ' ORDER BY c.id LIMIT 32', (space, cursor, *arguments))
            if not rows:
                break
            for row in rows:
                if scanned >= 4000 or consumed + len(row['vector']) > 32 * 1024 * 1024:
                    complete = False
                    break
                if row['dimensions'] != len(vector) or len(row['vector']) != 4 * len(vector):
                    raise ValueError('查询与索引维度不一致，未混用语义空间。')
                values = struct.unpack(f'<{len(vector)}f', row['vector'])
                if any(not math.isfinite(value) for value in values):
                    raise ValueError('索引含无效向量，请核对数据。')
                score = sum(left * right for left, right in zip(vector, values, strict=True))
                item = (score, row['id'])
                if len(ranked) < top_k:
                    heapq.heappush(ranked, item)
                elif item > ranked[0]:
                    heapq.heapreplace(ranked, item)
                scanned += 1; consumed += len(row['vector']); cursor = row['id']
            if not complete:
                break
            await asyncio.sleep(0)
        results = [{'chunk_id': key, 'score': round(score, 6)} for score, key in sorted(ranked, reverse=True)]
        return {'results': results, 'scan_complete': complete,
                'coverage': {**counts, 'index_complete': counts['chunks'] == counts['indexed'], 'scanned_vectors': scanned,
                             'vector_scan_limit': 4000, 'byte_scan_limit': 32 * 1024 * 1024}, 'score_basis': 'cosine; not_probability'}

    def fresh_results(self, instance_id, items):
        if not items:
            return []
        condition, arguments = self.info.scope(instance_id)
        keys = [item['chunk_id'] for item in items]
        rows = self.database.all('SELECT c.*,m.kind,m.expires_at,m.locked,m.valid_from,d.title,d.library_id' + JOIN + ' WHERE c.id IN ('
            + ','.join('?' for _ in keys) + ') AND ' + condition, (*keys, *arguments))
        rows = {row['id']: row for row in rows}
        return [self.info.result(rows[item['chunk_id']], item.get('score')) for item in items if item['chunk_id'] in rows]

    async def search(self, instance_id, query, top_k=6, category='all', mode='keyword', *, automatic=False, per_category=False, selection=None, run_id=None):
        self.info.store.instance(instance_id)
        if mode == 'keyword':
            return self.info.search(instance_id, query, top_k, category, automatic=automatic)
        if not query.strip():
            return {'results': [], 'scan_complete': True, 'mode': mode, 'score_basis': 'empty_query'}
        categories = ['memory', 'knowledge'] if per_category else [category]
        task = asyncio.current_task()
        self.queries.add(task)
        try:
            try:
                if isinstance(selection, str):
                    raise ValueError(selection)
                snapshot, options, space = selection if selection is not None else self.current()
                condition, arguments = self.scoped(instance_id, category)
                coverage = self.coverage(space, condition, arguments)
                if not coverage['indexed']:
                    raise ValueError('当前模型在此授权范围内没有有效向量索引，请先建立索引。')
                self.ensure_space(snapshot, options, space)
                vectors = await self.embed(options, space, [query], 'query', instance_id=instance_id, run_id=run_id)
                batches = []
                for target in categories:
                    semantic = await self.rank(instance_id, vectors[0], space, top_k, target)
                    if mode == 'hybrid':
                        keyword = self.info.search(instance_id, query, top_k, target, automatic=automatic)
                        scores = {}
                        for items in [keyword['results'], semantic['results']]:
                            for index, item in enumerate(items, 1):
                                scores[item['chunk_id']] = scores.get(item['chunk_id'], 0) + 1 / (60 + index)
                        results = [{'chunk_id': key, 'score': round(scores[key], 8)} for key in sorted(scores, key=lambda key: (scores[key], key), reverse=True)[:top_k]]
                        semantic = {**semantic, 'results': results, 'scan_complete': semantic['scan_complete'] and keyword['scan_complete'],
                                    'query_terms_truncated': keyword.get('query_terms_truncated', False),
                                    'score_basis': 'RRF(k=60); not_probability'}
                    batches.append(semantic)
                return {'results': self.fresh_results(instance_id, [item for batch in batches for item in batch['results']]), 'mode': mode, 'space_id': space,
                        'scan_complete': all(batch['scan_complete'] for batch in batches),
                        'query_terms_truncated': any(batch.get('query_terms_truncated', False) for batch in batches),
                        'coverage': [batch['coverage'] for batch in batches], 'score_basis': batches[0]['score_basis'],
                        'embedding_snapshot': {'connection_id': snapshot.connection_id, 'model_id': snapshot.model.id, 'revision': snapshot.revision}}
            except ValueError as exc:
                if mode != 'hybrid':
                    raise
                batches = [self.info.search(instance_id, query, top_k, target, automatic=automatic) for target in categories]
                return {'results': self.fresh_results(instance_id, [item for batch in batches for item in batch['results']]), 'mode': mode,
                        'scan_complete': all(batch['scan_complete'] for batch in batches),
                        'query_terms_truncated': any(batch.get('query_terms_truncated', False) for batch in batches),
                        'fallback': str(exc), 'score_basis': 'BM25; semantic_unavailable; not_probability'}
        finally:
            self.queries.discard(task)

    async def close(self):
        self.closing = True
        identifiers = list(self.jobs)
        tasks = list(self.jobs.values()) + list(self.queries)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # 在首次执行协程前就取消的任务也要明确留下中断状态。
        for identifier in identifiers:
            self.finish_job(identifier, 'interrupted', '服务关闭；未自动续建。')
