"""初始化与启动入口；维护操作需要先停止正在使用同一数据目录的服务。"""
import argparse
from getpass import getpass
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from threading import Event
from time import time
from zipfile import BadZipFile

from cryptography.fernet import Fernet

from .settings import ServerSettings
from .security import hash_password
from .storage.credentials import Credentials
from .storage.database import Database
from .storage.process_lock import ProcessLock
from .storage.backups import build_backup, restore_backup


def read_password() -> str:
    password = getpass("设置管理员密码（12—128 字符，不回显）：")
    if getpass("再次输入密码：") != password:
        raise ValueError("两次密码不一致，未保存。")
    return hash_password(password)


def initialize(settings: ServerSettings, username: str):
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", username) is None:
        raise ValueError("管理员名需要 1—80 个英文字母、数字或 _ . -。")
    with ProcessLock(settings.data_dir):
        database = Database(settings.database_path, create=True)
        try:
            if database.one("SELECT id FROM administrator WHERE id=1"):
                raise ValueError("管理员已经存在，不会覆盖；忘记密码请停止服务后使用 reset-password。")
            password_hash = read_password()
            if not os.environ.get("RP_MASTER_KEY") and not settings.master_key_file.exists():
                if database.one("SELECT value FROM metadata WHERE key='master_key_check'"):
                    raise ValueError("已有密钥校验记录，必须恢复原主密钥，不能新建替代。")
                settings.master_key_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor = os.open(settings.master_key_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(Fernet.generate_key() + b"\n")
                    output.flush()
                    os.fsync(output.fileno())
            credentials = Credentials(database, settings)
            credentials.check_key(initialize=True)
            now = time()
            with database.transaction() as connection:
                connection.execute("INSERT INTO administrator VALUES (1,?,?,?,?)", (username, password_hash, now, now))
                connection.execute("INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)",
                                   (now, "system.initialized", json.dumps({"username": username})))
        finally:
            database.close()
    print(f"初始化完成，管理员：{username}。密码和主密钥不会在终端输出。")
    print("请分别保管数据库与服务器主密钥；不要将 data 提交或公开分享。")


def reset_password(settings: ServerSettings):
    with ProcessLock(settings.data_dir):
        database = Database(settings.database_path)
        try:
            Credentials(database, settings).check_key()
            administrator = database.one("SELECT username FROM administrator WHERE id=1")
            if administrator is None:
                raise ValueError("尚无管理员，请使用 init。")
            candidate = read_password()
            with database.transaction() as connection:
                connection.execute("UPDATE administrator SET password_hash=?,password_changed_at=? WHERE id=1", (candidate, time()))
                connection.execute("DELETE FROM login_sessions")
                connection.execute("INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)",
                                   (time(), "auth.password_reset_offline", "{}"))
        finally:
            database.close()
    print("密码已更新，所有浏览器会话已失效。主密钥与业务配置未改变。")


def main():
    parser = argparse.ArgumentParser(description="独立 RP 管理服务")
    parser.add_argument("--data-dir", type=Path, help="独立数据目录；省略时使用 RP_DATA_DIR 或项目 data")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="交互式初始化单管理员与服务器主密钥")
    init.add_argument("--username", default="admin")
    commands.add_parser("reset-password", help="停止服务后交互式重置密码并撤销所有登录")
    commands.add_parser('backup', help='停止服务后创建数据库、附件与导入原文备份；不包含主密钥')
    restore = commands.add_parser('restore', help='停止服务后恢复到不存在的新目录；不覆盖原数据')
    restore.add_argument('archive', type=Path)
    restore.add_argument('--target', type=Path, required=True)
    restore.add_argument('--key-file', type=Path, help='原主密钥文件；也可提供 RP_MASTER_KEY')
    serve = commands.add_parser("serve", help="启动单进程服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=18765)
    arguments = parser.parse_args()
    try:
        settings = ServerSettings.from_env(arguments.data_dir)
        if arguments.command == "init":
            initialize(settings, arguments.username)
        elif arguments.command == "reset-password":
            reset_password(settings)
        elif arguments.command == 'backup':
            with ProcessLock(settings.data_dir):
                result = build_backup(settings.data_dir, Event())
            print(f"备份完成：{settings.data_dir / 'backups' / result['name']}")
            print('备份不包含主密钥；请将原主密钥另行保管。')
        elif arguments.command == 'restore':
            with ProcessLock(settings.data_dir):
                restore_backup(arguments.archive, arguments.target, arguments.key_file)
            print(f'恢复完成：{arguments.target.expanduser().absolute()}')
            print('所有登录已撤销，自动运行与后台生活已关闭，生活线已暂停，旧待发计划已取消。')
            print('请核对实际 QQ 回执，再显式切换 RP_DATA_DIR；不会启动或覆盖原服务。')
        else:
            import uvicorn
            from .app import create_app
            logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
            uvicorn.run(create_app(settings), host=arguments.host, port=arguments.port,
                        workers=1, proxy_headers=False, timeout_graceful_shutdown=5, log_level="info",
                        ws_max_size=1024 * 1024, ws_max_queue=8)
    except (BadZipFile, sqlite3.DatabaseError):
        parser.exit(1, '无法完成：备份压缩包或数据库内容损坏，请保留原数据。\n')
    except (ValueError, OSError) as exc:
        # 控制台只输出维护命令的可读原因；不打印密码或主密钥内容。
        parser.exit(1, f"无法完成：{exc}\n")
    except (KeyboardInterrupt, EOFError):
        parser.exit(1, "\n操作已取消。\n")


if __name__ == "__main__":
    main()
