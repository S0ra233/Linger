"""备份只复制数据库快照中已发布的不可变附件；主密钥始终另行保管。"""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, time
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from cryptography.fernet import Fernet, InvalidToken

from .. import __version__
from .credentials import KEY_CHECK
from .database import SCHEMA_VERSION


FORMAT = 'rp-backup-v1'
# 此格式的恢复清理逻辑支持从v10起的连续版本；旧库在正常启动时逐级迁移。
MIN_RESTORE_SCHEMA_VERSION = 10
MAX_BYTES = 20 * 1024**3
MAX_FILES = 200000
MAX_MANIFEST = 32 * 1024**2
ARCHIVE_NAME = re.compile(r'rp-backup-\d{8}T\d{6}Z-[a-f0-9]{32}\.zip')
FILE_NAME = re.compile(r'(attachments/[a-f0-9]{32}\.(source|png|json)|imports/[a-f0-9]{32}\.source|rp\.sqlite3)')
logger = logging.getLogger('rp_server.backups')


class BackupStopped(ValueError):
    pass


def required_files(connection) -> list[str]:
    """先读快照再取文件。现有写入都是先完整发布文件、后提交数据库引用。"""
    result = ['rp.sqlite3']
    for (identifier,) in connection.execute('SELECT DISTINCT attachment_id FROM inbound_images WHERE attachment_id IS NOT NULL'):
        if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
            raise ValueError('快照中的附件编号无效。')
        result.extend(f'attachments/{identifier}{suffix}' for suffix in ('.source', '.png', '.json'))
    for (identifier,) in connection.execute('SELECT id FROM material_imports'):
        if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
            raise ValueError('快照中的导入编号无效。')
        result.append(f'imports/{identifier}.source')
    if len(result) > MAX_FILES:
        raise ValueError('备份文件超过 20 万个，需另行安排分卷备份。')
    return result


def safe_source(root: Path, name: str) -> Path:
    candidate = root / name
    if not FILE_NAME.fullmatch(name) or candidate.is_symlink() or not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError('备份文件路径越界或使用了符号链接。')
    if not candidate.is_file():
        raise ValueError('快照引用的附件或导入原文缺失，备份未完成。')
    return candidate


def build_backup(data_dir: Path, stop: Event) -> dict:
    if (data_dir / '.restore-incomplete').exists():
        raise ValueError('数据目录恢复尚未完成，不能创建备份。')
    directory = data_dir / 'backups'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.resolve().is_relative_to(data_dir.resolve()):
        raise ValueError('备份目录不能指向数据目录以外。')
    name = f"rp-backup-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}.zip"
    started, deadline = time(), monotonic() + 3600

    def check(*_):
        if stop.is_set():
            raise BackupStopped('服务正在退出，未完成的备份已中止。')
        if monotonic() > deadline:
            raise BackupStopped('备份超过一小时，未发布未完成文件。')

    # 工作目录在同一卷，最终只发布完整 ZIP；不遍历 secrets 或旧备份。
    with TemporaryDirectory(prefix='.building-', dir=directory) as temporary:
        work = Path(temporary)
        os.chmod(work, 0o700)
        snapshot = work / 'rp.sqlite3'
        source = safe_source(data_dir, 'rp.sqlite3')
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as origin:
            with closing(sqlite3.connect(snapshot)) as target:
                origin.backup(target, pages=256, progress=check, sleep=0.05)
                schema = target.execute('PRAGMA user_version').fetchone()[0]
                if schema != SCHEMA_VERSION:
                    raise ValueError('请使用与已启动服务相同的数据库版本创建备份。')
                paths = required_files(target)
        captured_at = time()
        archive, entries, total = work / name, [], 0
        with ZipFile(archive, 'x', compression=ZIP_DEFLATED, compresslevel=3) as output:
            for path in paths:
                check()
                source = snapshot if path == 'rp.sqlite3' else safe_source(data_dir, path)
                digest, size = hashlib.sha256(), 0
                with source.open('rb') as reader, output.open(path, 'w', force_zip64=True) as writer:
                    while chunk := reader.read(1024 * 1024):
                        check()
                        size += len(chunk)
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError('备份原始内容超过 20 GiB，未发布备份。')
                        digest.update(chunk)
                        writer.write(chunk)
                entries.append({'path': path, 'bytes': size, 'sha256': digest.hexdigest()})
            manifest = {'format': FORMAT, 'application_version': __version__, 'schema_version': schema,
                        'started_at': started, 'snapshot_at': captured_at, 'finished_at': time(),
                        'master_key_included': False, 'files': entries}
            content = json.dumps(manifest, ensure_ascii=False).encode('utf-8')
            if len(content) > MAX_MANIFEST:
                raise ValueError('备份清单超过大小限制。')
            output.writestr('manifest.json', content)
        check()
        os.chmod(archive, 0o600)
        with archive.open('rb+') as ready:
            os.fsync(ready.fileno())
        archive.rename(directory / name)
    return {'name': name, 'snapshot_at': captured_at, 'finished_at': time(), 'file_count': len(entries),
            'bytes': (directory / name).stat().st_size, 'source_bytes': total}


def archive_path(data_dir: Path, name: str) -> Path:
    if not ARCHIVE_NAME.fullmatch(name):
        raise LookupError('备份编号无效。')
    root = data_dir / 'backups'
    path = root / name
    if root.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(data_dir.resolve()) or not path.is_file():
        raise LookupError('备份文件不存在。')
    return path


class Backups:
    """线程拥有自己的 SQLite 只读连接；退出先通知线程并等待，再关闭应用资源。"""
    def __init__(self, database, events, settings):
        self.database, self.events, self.settings = database, events, settings
        self.task = None
        self.stop = Event()
        self.job = None
        self.closed = False

    def overview(self):
        directory = self.settings.data_dir / 'backups'
        if directory.is_symlink():
            raise ValueError('备份目录不能使用符号链接。')
        paths = [] if not directory.exists() else sorted(
            (p.name for p in directory.iterdir() if ARCHIVE_NAME.fullmatch(p.name)), reverse=True)
        items = []
        for name in paths[:50]:
            path = archive_path(self.settings.data_dir, name)
            info = path.stat()
            items.append({'name': name, 'bytes': info.st_size, 'created_at': info.st_mtime})
        disk = shutil.disk_usage(self.settings.data_dir)
        return {'job': dict(self.job) if self.job else None, 'items': items, 'has_more': len(paths) > 50,
                'disk': {'total': disk.total, 'free': disk.free}, 'schema_version': SCHEMA_VERSION,
                'master_key_included': False, 'restore_online': False,
                'database_bytes': self.settings.database_path.stat().st_size,
                'key_source': 'environment' if os.environ.get('RP_MASTER_KEY') else 'file'}

    def start(self):
        if self.closed or self.task is not None and not self.task.done():
            raise ValueError('已有备份正在执行或服务正在退出，请稍后刷新。')
        self.stop.clear()
        self.job = {'id': uuid4().hex, 'status': 'running', 'started_at': time(), 'result': None, 'error': None}
        self.task = asyncio.create_task(self._run())
        self.events.publish('maintenance')
        return dict(self.job)

    async def _run(self):
        try:
            result = await asyncio.to_thread(build_backup, self.settings.data_dir, self.stop)
            self.job.update(status='succeeded', result=result, finished_at=time())
        except Exception as exc:
            # 文件系统异常可能包含私人路径；接口只给出分类，不回显原异常。
            self.job.update(status='interrupted' if isinstance(exc, BackupStopped) else 'failed', finished_at=time(),
                            error=str(exc) if isinstance(exc, ValueError) else '备份失败，请检查磁盘空间、目录权限及文件完整性。')
        try:
            with self.database.transaction() as tx:
                tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                           (time(), 'backup.' + self.job['status'], json.dumps({'job_id': self.job['id'],
                            'name': (self.job['result'] or {}).get('name')})))
        except sqlite3.Error:
            # 压缩包已发布时不能反称备份失败；审计不可写单独保留日志。
            self.job['warning'] = '备份任务已结束，但管理审计未写入；请检查数据库可写性。'
            logger.error('备份任务 %s 结束，但审计未写入。', self.job['id'])
        self.events.publish('maintenance')

    async def close(self):
        self.closed = True
        self.stop.set()
        if self.task is not None:
            # 不 cancel to_thread 的外层任务，否则线程会在数据库／进程锁释放后继续工作。
            await self.task


def read_manifest(archive: ZipFile) -> tuple[dict, dict]:
    files = archive.infolist()
    if len(files) > MAX_FILES + 1 or len({item.filename for item in files}) != len(files):
        raise ValueError('备份包含重复文件或文件过多。')
    by_name = {item.filename: item for item in files}
    info = by_name.get('manifest.json')
    if (info is None or info.file_size > MAX_MANIFEST or info.flag_bits & 1
            or info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}):
        raise ValueError('备份清单缺失或过大。')
    try:
        manifest = json.loads(archive.read(info))
        if (manifest['format'] != FORMAT or type(manifest['schema_version']) is not int
                or not MIN_RESTORE_SCHEMA_VERSION <= manifest['schema_version'] <= SCHEMA_VERSION or manifest['master_key_included'] is not False):
            raise ValueError('备份格式或数据库版本不兼容，请使用对应的 RP 版本。')
        entries = manifest['files']
        if not isinstance(entries, list) or not entries or len(entries) > MAX_FILES:
            raise ValueError('备份文件清单无效。')
        checked, total = {}, 0
        for entry in entries:
            name, size, digest = entry['path'], entry['bytes'], entry['sha256']
            if (not isinstance(name, str) or not FILE_NAME.fullmatch(name) or name in checked
                    or type(size) is not int or size < 0 or not isinstance(digest, str)
                    or re.fullmatch('[a-f0-9]{64}', digest) is None):
                raise ValueError('备份文件条目无效。')
            item = by_name.get(name)
            if (item is None or item.is_dir() or item.file_size != size or item.flag_bits & 1
                    or item.compress_type not in {ZIP_STORED, ZIP_DEFLATED}):
                raise ValueError('备份缺少文件、大小不符或使用了不支持的加密。')
            total += size
            checked[name] = entry
        if total > MAX_BYTES or set(by_name) != set(checked) | {'manifest.json'} or 'rp.sqlite3' not in checked:
            raise ValueError('备份内容越界、不完整或超过 20 GiB。')
        return manifest, checked
    except (KeyError, TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError('备份清单无效。') from exc


def prepare_restored_database(path: Path, key: bytes, names: set[str], expected_version=SCHEMA_VERSION):
    """仅处理暂存副本，不迁移、不运行恢复队列；未知回执保持未知。"""
    try:
        cipher = Fernet(key)
        with closing(sqlite3.connect(path)) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != expected_version:
                raise ValueError('备份数据库版本与清单不符。')
            if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError('备份数据库损坏。')
            row = db.execute("SELECT value FROM metadata WHERE key='master_key_check'").fetchone()
            if row is None or cipher.decrypt(row[0].encode('ascii')) != KEY_CHECK:
                raise ValueError('原主密钥与备份不匹配。')
            if set(required_files(db)) != names:
                raise ValueError('数据库引用与备份文件清单不一致。')
            if db.execute('SELECT 1 FROM administrator WHERE id=1').fetchone() is None:
                raise ValueError('备份没有管理员资料。')
            now, reason = time(), '从备份恢复，旧执行已停止；请人工核对。'
            # 即使快照在发送前拍下，也不能据此证明后来没发送：pending 也一并取消。
            with db:
                db.execute('DELETE FROM login_sessions')
                for config in ('rp_runtime', 'life'):
                    row = db.execute('SELECT value_json FROM configurations WHERE key=?', (config,)).fetchone()
                    if row:
                        value = json.loads(row[0])
                        value['enabled'] = False
                        db.execute('UPDATE configurations SET value_json=?,revision=revision+1,updated_at=? WHERE key=?',
                                   (json.dumps(value, ensure_ascii=False), now, config))
                db.execute('UPDATE life_policies SET enabled=0,revision=revision+1,updated_at=?', (now,))
                cache = db.execute("SELECT value_json FROM configurations WHERE key='cache_keepalive'").fetchone()
                if cache:
                    policies = json.loads(cache[0])
                    if not isinstance(policies, list) or any(not isinstance(item, dict) for item in policies):
                        raise ValueError('备份的保活规则格式无效。')
                    for item in policies: item['enabled'] = False
                    db.execute("UPDATE configurations SET value_json=?,revision=revision+1,updated_at=? WHERE key='cache_keepalive'",
                               (json.dumps(policies), now))
                if expected_version >= 11:
                    db.execute("UPDATE cache_calls SET status='unknown',finished_at=?,error=? WHERE status='running'", (now, reason))
                if expected_version >= 15:
                    db.execute("UPDATE observations SET status=CASE WHEN status='fetching' THEN 'unknown' ELSE 'cancelled' END,error=? WHERE status IN ('pending','fetching')", (reason,))
                    db.execute("UPDATE world_events SET status='expired',revision=revision+1,updated_at=? WHERE status='pending'", (now,))
                    for instance_id, payload in db.execute('SELECT instance_id,config_json FROM life_extensions').fetchall():
                        config = json.loads(payload)
                        for flag in ('schedules_enabled','events_enabled','observations_enabled'):
                            config[flag] = False
                        db.execute('UPDATE life_extensions SET config_json=?,revision=revision+1,updated_at=? WHERE instance_id=?', (json.dumps(config,ensure_ascii=False), now, instance_id))
                    db.execute("UPDATE explicit_caches SET status='disabled',revision=revision+1,updated_at=?,error='从备份恢复；不自动复用或延长远端对象。'", (now,))
                    db.execute("UPDATE explicit_cache_operations SET status='unknown',finished_at=?,error=? WHERE status='running'", (now, reason))
                db.execute('UPDATE role_instances SET paused=1,input_version=input_version+1')
                db.execute("UPDATE rp_outbox SET status='cancelled',error=?,finished_at=? WHERE status='pending'", (reason, now))
                db.execute("UPDATE rp_outbox SET status='unknown',error=?,finished_at=? WHERE status='sending'", (reason, now))
                if expected_version >= 21:
                    db.execute("UPDATE rp_deliveries SET status='held',revision=revision+1,updated_at=? WHERE status='pending'", (now,))
                    db.execute("UPDATE rp_deliveries SET status='unknown',revision=revision+1,updated_at=? WHERE status='sending'", (now,))
                if expected_version >= 23:
                    db.execute('UPDATE rp_deliveries SET resume_input_version=NULL WHERE resume_input_version IS NOT NULL')
                db.execute("UPDATE rp_runs SET status='unknown',error=?,finished_at=? WHERE status='running'", (reason, now))
                if expected_version >= 13:
                    db.execute("UPDATE life_intents SET resolution='interrupted',outcome_reason=? WHERE status IN ('pending','running')", (reason,))
                db.execute("UPDATE life_intents SET status='interrupted',error=?,revision=revision+1,updated_at=? WHERE status IN ('pending','running')", (reason, now))
                db.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                           (now, 'backup.restored_offline', '{"automatic_running":false,"instances_paused":true}'))
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('PRAGMA journal_mode=DELETE')
    except (InvalidToken, UnicodeError) as exc:
        raise ValueError('原主密钥与备份不匹配。') from exc


def restore_backup(source: Path, target: Path, key_file: Path | None):
    source, target = source.expanduser().resolve(), target.expanduser().absolute()
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError('恢复目标必须是不存在的新目录，且父目录已经存在；不覆盖现有数据。')
    env_key = os.environ.get('RP_MASTER_KEY')
    if not env_key and key_file is None:
        raise ValueError('恢复需要 --key-file 指向原主密钥，或通过 RP_MASTER_KEY 提供原密钥。')
    key = env_key.encode('ascii') if env_key else key_file.expanduser().read_bytes().strip()
    # 所有内容先在独立暂存目录中核对，目标目录不接收不完整数据库。
    with TemporaryDirectory(prefix='.rp-restore-', dir=target.parent) as temporary:
        work = Path(temporary)
        os.chmod(work, 0o700)
        with ZipFile(source) as archive:
            manifest, entries = read_manifest(archive)
            for name, entry in entries.items():
                output = work / name
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                digest, size = hashlib.sha256(), 0
                with archive.open(name) as reader, output.open('xb') as writer:
                    os.chmod(output, 0o600)
                    while chunk := reader.read(1024 * 1024):
                        size += len(chunk)
                        if size > entry['bytes']:
                            raise ValueError('备份解压大小超过清单。')
                        digest.update(chunk)
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                if size != entry['bytes'] or digest.hexdigest() != entry['sha256']:
                    raise ValueError('备份文件校验不符，恢复未发布。')
        prepare_restored_database(work / 'rp.sqlite3', key, set(entries), manifest['schema_version'])
        if not env_key:
            secret = work / 'secrets'
            secret.mkdir(mode=0o700)
            with (secret / 'master.key').open('xb') as writer:
                os.chmod(secret / 'master.key', 0o600)
                writer.write(key + b'\n')
                writer.flush()
                os.fsync(writer.fileno())
        # 独占创建保留位置，不利用 rename 覆盖并发创建的目录；数据库最后发布。
        target.mkdir(mode=0o700)
        marker = target / '.restore-incomplete'
        marker.write_text('恢复尚未完成，不要启动此目录。', encoding='utf-8')
        for child in sorted(work.iterdir(), key=lambda item: item.name == 'rp.sqlite3'):
            child.rename(target / child.name)
        marker.unlink()
