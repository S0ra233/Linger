"""同一数据目录只能由一个服务或维护命令持有。"""
import os
from pathlib import Path


class ProcessLock:
    def __init__(self, directory: Path):
        self.path = directory / ".server.lock"
        self._file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(0, os.SEEK_END)
                if not self._file.tell():
                    self._file.write(b"\0")
                    self._file.flush()
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            self._file = None
            raise ValueError("数据目录正在被其他进程使用；请停止原服务，不要开启多个 worker。") from exc
        return self

    def __exit__(self, *_):
        if self._file is not None:
            # 关闭文件句柄会释放系统锁；锁文件保留，不通过删除判断进程状态。
            self._file.close()
            self._file = None
