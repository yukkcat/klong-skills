"""Shared secure connection storage for the CLI and Prompt Studio."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse


SKILL_DIR = Path(__file__).resolve().parent.parent
DOTENV_KEYS = {
    "KLONG_API_KEY",
    "KLONG_BASE_URL",
    "KLONG_DEFAULT_MODEL",
    "KLONG_OUTPUT_DIR",
    "KLONG_STUDIO_HOME",
}


def _dotenv_value(raw: str) -> str:
    value = raw.strip()
    quote = ""
    escaped = False
    for index, character in enumerate(value):
        if quote:
            if quote == '"' and character == "\\" and not escaped:
                escaped = True
                continue
            if character == quote and not escaped:
                quote = ""
            escaped = False
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "#" and (index == 0 or value[index - 1].isspace()):
            value = value[:index].rstrip()
            break
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        if value[0] == "'":
            return value[1:-1]
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, str) else value[1:-1]
        except ValueError:
            return value[1:-1]
    return value


def load_dotenv(
    paths: list[Path] | None = None,
    environ: dict[str, str] | None = None,
) -> list[Path]:
    """Load supported KLONG_* values without overriding the process environment."""
    environment = os.environ if environ is None else environ
    if paths is None:
        candidates = [Path.cwd() / ".env", SKILL_DIR / ".env"]
        repository_root = SKILL_DIR.parents[1]
        if (repository_root / ".env.example").is_file():
            candidates.append(repository_root / ".env")
    else:
        candidates = paths
    loaded: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        path = candidate.expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            continue
        loaded.append(path)
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if key in DOTENV_KEYS and key not in environment:
                environment[key] = _dotenv_value(raw_value)
    return loaded


load_dotenv()

DEFAULT_BASE_URL = "https://api.klong.lat"
DEFAULT_MODEL = "gpt-image-2"
ENVIRONMENT_CONNECTION_ID = "environment"
CACHE_DIR = Path(os.environ.get("KLONG_STUDIO_HOME", Path.home() / ".klong-image")).expanduser()
SETTINGS_PATH = CACHE_DIR / "settings.json"


def resolve_output_directory(
    settings_path: Path = SETTINGS_PATH,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    explicit: str | Path | None = None,
) -> dict[str, object]:
    """Resolve command, environment, saved, and default output locations in priority order."""
    environment = os.environ if environ is None else environ
    working_directory = (cwd or Path.cwd()).expanduser().resolve()
    default_path = (working_directory / "outputs" / "prompt-studio").resolve()

    raw_path: object
    if str(explicit or "").strip():
        raw_path, source, locked = explicit, "command", True
    elif str(environment.get("KLONG_OUTPUT_DIR", "")).strip():
        raw_path, source, locked = environment["KLONG_OUTPUT_DIR"], "environment", True
    else:
        data = _load_settings(settings_path)
        saved = str(data.get("output_dir", "")).strip()
        if saved:
            raw_path, source, locked = saved, "saved", False
        else:
            raw_path, source, locked = default_path, "default", False

    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = working_directory / path
    return {
        "path": path.resolve(),
        "default_path": default_path,
        "source": source,
        "locked": locked,
    }


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def validate_base_url(value: object) -> str:
    url = clean(value or DEFAULT_BASE_URL).rstrip("/")
    parsed = urlparse(url)
    is_local_http = parsed.scheme == "http" and (parsed.hostname or "").lower() in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    if not parsed.hostname or (parsed.scheme != "https" and not is_local_http):
        raise ValueError("API 地址必须使用 HTTPS；本机 localhost 可使用 HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API 地址不能包含账号、查询参数或片段")
    return url


def protect_windows_secret(value: str) -> str:
    if os.name != "nt":
        raise OSError("secure persistent storage is unavailable")
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    raw = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    input_blob = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "小恐龙图像工作台",
        None,
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.data)
    return base64.b64encode(encrypted).decode("ascii")


def unprotect_windows_secret(value: str) -> str:
    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    raw = base64.b64decode(value, validate=True)
    buffer = ctypes.create_string_buffer(raw)
    input_blob = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        decrypted = ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.data)
    return decrypted.decode("utf-8")


def _load_settings(settings_path: Path) -> dict:
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _stored_connections(data: dict) -> list[dict]:
    raw_connections = data.get("connections")
    if isinstance(raw_connections, list):
        return [item for item in raw_connections if isinstance(item, dict)]
    legacy_fields = {"base_url", "default_model", "api_key_dpapi"}
    if not legacy_fields.intersection(data):
        return []
    return [{
        "id": "default",
        "name": "默认连接",
        "base_url": data.get("base_url") or DEFAULT_BASE_URL,
        "default_model": data.get("default_model") or DEFAULT_MODEL,
        "api_key_dpapi": data.get("api_key_dpapi", ""),
    }]


def environment_connection_available(environ: Mapping[str, str] | None = None) -> bool:
    environment = os.environ if environ is None else environ
    return bool(
        str(environment.get("KLONG_API_KEY", "")).strip()
        or str(environment.get("KLONG_BASE_URL", "")).strip()
    )


def active_connection_id(
    data: dict,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the connection selected by both Prompt Studio and direct CLI calls."""
    environment = os.environ if environ is None else environ
    connections = _stored_connections(data)
    selected_id = clean(data.get("active_connection_id"))
    if selected_id == ENVIRONMENT_CONNECTION_ID and environment_connection_available(environment):
        return selected_id
    if selected_id and any(clean(item.get("id")) == selected_id for item in connections):
        return selected_id
    if environment_connection_available(environment):
        return ENVIRONMENT_CONNECTION_ID
    return clean(connections[0].get("id")) if connections else ""


def resolve_cli_connection(
    settings_path: Path = SETTINGS_PATH,
    environ: Mapping[str, str] | None = None,
    process_connection_id: object = "",
    process_connection_name: object = "",
) -> dict[str, str]:
    """Resolve the connection selected in Prompt Studio's shared settings."""
    environment = os.environ if environ is None else environ
    snapshot_id = clean(process_connection_id)
    if snapshot_id:
        process_key = str(environment.get("KLONG_API_KEY", "")).strip()
        if not process_key:
            raise ValueError("网页任务连接快照缺少 API Key")
        return {
            "id": snapshot_id[:80],
            "name": clean(process_connection_name)[:60] or "任务连接",
            "api_key": process_key,
            "base_url": validate_base_url(environment.get("KLONG_BASE_URL") or DEFAULT_BASE_URL),
            "default_model": clean(environment.get("KLONG_DEFAULT_MODEL") or DEFAULT_MODEL)[:120],
            "source": "process_snapshot",
        }

    data = _load_settings(settings_path)
    connections = _stored_connections(data)
    selected_id = active_connection_id(data, environment)

    if selected_id == ENVIRONMENT_CONNECTION_ID:
        environment_key = str(environment.get("KLONG_API_KEY", "")).strip()
        if not environment_key:
            raise ValueError("环境变量连接缺少 KLONG_API_KEY")
        return {
            "id": ENVIRONMENT_CONNECTION_ID,
            "name": "环境变量",
            "api_key": environment_key,
            "base_url": validate_base_url(environment.get("KLONG_BASE_URL") or DEFAULT_BASE_URL),
            "default_model": clean(environment.get("KLONG_DEFAULT_MODEL") or DEFAULT_MODEL)[:120],
            "source": "environment",
        }

    active = next((item for item in connections if clean(item.get("id")) == selected_id), None)
    if not active:
        raise ValueError("未找到 API Key，请先在小恐龙图像工作台中添加连接")

    encrypted = clean(active.get("api_key_dpapi"))
    if not encrypted:
        raise ValueError("当前连接没有可供 Codex 读取的持久化 API Key")
    try:
        api_key = unprotect_windows_secret(encrypted).strip()
    except (OSError, ValueError) as exc:
        raise ValueError("无法解密当前连接的 API Key，请在工作台中重新保存") from exc
    if not api_key:
        raise ValueError("当前系统无法读取网页连接，请设置 KLONG_API_KEY")

    return {
        "id": clean(active.get("id")) or "default",
        "name": clean(active.get("name")) or "默认连接",
        "api_key": api_key,
        "base_url": validate_base_url(active.get("base_url") or DEFAULT_BASE_URL),
        "default_model": clean(active.get("default_model") or DEFAULT_MODEL)[:120],
        "source": "secure_storage",
    }
