"""进程配置只决定资源位置与安全边界，业务配置由数据库维护。"""
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit


def parse_origin(value: str) -> str:
    address = urlsplit(value.strip())
    if (address.scheme not in {"http", "https"} or not address.hostname
            or address.username or address.password or address.path not in {"", "/"}
            or address.query or address.fragment):
        raise ValueError("管理端来源必须是只含协议、主机和端口的 HTTP(S) 地址。")
    # 与浏览器序列化保持一致：省略默认端口，IPv6 地址保留方括号。
    port = address.port
    host = address.hostname.lower().encode("idna").decode("ascii")
    if ":" in host:
        host = f"[{host}]"
    suffix = f":{port}" if port and (address.scheme, port) not in {("http", 80), ("https", 443)} else ""
    return f"{address.scheme}://{host}{suffix}"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    if value.lower() not in {"true", "false", "1", "0"}:
        raise ValueError(f"{name} 只接受 true、false、1 或 0。")
    return value.lower() in {"true", "1"}


@dataclass(frozen=True)
class ServerSettings:
    data_dir: Path
    master_key_file: Path
    static_dir: Path
    public_origin: str
    allowed_origins: frozenset[str]
    cookie_secure: bool
    session_seconds: int = 43200
    session_idle_seconds: int = 7200
    cookie_name: str = "rp_session"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "rp.sqlite3"

    @classmethod
    def from_env(cls, data_dir: Path | None = None) -> "ServerSettings":
        root = Path(__file__).resolve().parents[3]
        data = (data_dir or Path(os.environ.get("RP_DATA_DIR", root / "data"))).expanduser().resolve()
        origin = parse_origin(os.environ.get("RP_PUBLIC_URL", "http://127.0.0.1:18765"))
        origins = {origin}
        origins.update(parse_origin(item) for item in os.environ.get("RP_ALLOWED_ORIGINS", "").split(",") if item.strip())
        secure = env_bool("RP_COOKIE_SECURE", origin.startswith("https://"))
        if origin.startswith("https://") and not secure:
            raise ValueError("HTTPS 管理端必须启用 RP_COOKIE_SECURE。")
        static_default = root / "web" / "dist"
        packaged_static = Path(__file__).parent / "static"
        if packaged_static.exists():
            static_default = packaged_static
        return cls(
            data_dir=data,
            master_key_file=Path(os.environ.get("RP_MASTER_KEY_FILE", data / "secrets" / "master.key")).expanduser().resolve(),
            static_dir=Path(os.environ.get("RP_STATIC_DIR", static_default)).expanduser().resolve(),
            public_origin=origin,
            allowed_origins=frozenset(origins),
            cookie_secure=secure,
        )
