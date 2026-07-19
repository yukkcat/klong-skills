#!/usr/bin/env python3
"""Run the local 小恐龙 prompt browser and image generation studio."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from connection_store import (
    CACHE_DIR,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    ENVIRONMENT_CONNECTION_ID,
    SETTINGS_PATH,
    active_connection_id,
    environment_connection_available,
    protect_windows_secret,
    resolve_output_directory,
    unprotect_windows_secret,
    validate_base_url,
)
from generation_manifest import record_generation_manifest


MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_BODY_BYTES = 30 * 1024 * 1024
MAX_PREVIEW_BYTES = 12 * 1024 * 1024
MAX_GALLERY_BATCH = 10_000
SOURCE_TIMEOUT = 12
SYNC_WORKERS = 4
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "prompt-studio"
GENERATE_SCRIPT = Path(__file__).resolve().parent / "generate.py"
CACHE_PATH = CACHE_DIR / "prompt-library.json"
PREVIEW_CACHE_DIR = CACHE_DIR / "previews"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}
SOURCES = [
    {"id": "banana-prompt-quicker", "name": "Banana Prompt Quicker", "adapter": "json", "url": "https://glidea.github.io/banana-prompt-quicker/prompts.json", "homepage": "https://glidea.github.io/banana-prompt-quicker/"},
    {"id": "awesome-gpt-image", "name": "Awesome GPT Image", "adapter": "markdown", "url": "https://raw.githubusercontent.com/ZeroLu/awesome-gpt-image/main/README.zh-CN.md", "homepage": "https://github.com/ZeroLu/awesome-gpt-image"},
    {"id": "awesome-gpt4o-image-prompts", "name": "Awesome GPT-4o Image Prompts", "adapter": "html", "url": "https://raw.githubusercontent.com/ImgEdify/Awesome-GPT4o-Image-Prompts/main/Prompts.html", "homepage": "https://github.com/ImgEdify/Awesome-GPT4o-Image-Prompts"},
    {"id": "youmind-gpt-image-2", "name": "YouMind GPT Image 2", "adapter": "markdown", "url": "https://raw.githubusercontent.com/YouMind-OpenLab/awesome-gpt-image-2/main/README_zh.md", "homepage": "https://github.com/YouMind-OpenLab/awesome-gpt-image-2"},
    {"id": "youmind-nano-banana-pro", "name": "YouMind Nano Banana Pro", "adapter": "markdown", "url": "https://raw.githubusercontent.com/YouMind-OpenLab/awesome-nano-banana-pro-prompts/main/README_zh.md", "homepage": "https://github.com/YouMind-OpenLab/awesome-nano-banana-pro-prompts"},
    {"id": "davidwu-gpt-image2-prompts", "name": "DavidWu GPT Image 2 Prompts", "adapter": "json", "url": "https://raw.githubusercontent.com/davidwuw0811-boop/awesome-gpt-image2-prompts/main/prompts.json", "homepage": "https://github.com/davidwuw0811-boop/awesome-gpt-image2-prompts"},
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_multiline(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def validate_output_directory(value: object) -> Path:
    raw_path = str(value or "").strip()
    if not raw_path:
        raise ValueError("请选择或填写图库位置")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise ValueError("图库位置必须使用绝对路径")
    path = path.resolve()
    if path == Path(path.anchor):
        raise ValueError("不要把整个磁盘根目录设为图库位置")
    path.mkdir(parents=True, exist_ok=True)
    probe = None
    try:
        handle = tempfile.NamedTemporaryFile(prefix=".klong-write-", dir=path, delete=False)
        probe = Path(handle.name)
        handle.close()
    except OSError as exc:
        raise ValueError(f"图库位置不可写入: {exc}") from exc
    finally:
        if probe:
            probe.unlink(missing_ok=True)
    return path


def choose_output_directory(initial: Path) -> str:
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory(
                initialdir=str(initial),
                mustexist=False,
                title="选择小恐龙图库位置",
            )
        finally:
            root.destroy()
    except Exception as exc:
        raise ValueError("当前系统无法打开目录选择器，请直接填写绝对路径") from exc
    return str(Path(selected).resolve()) if selected else ""


def open_output_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise ValueError(f"无法打开图库位置: {exc}") from exc


def item_category(item: dict[str, Any]) -> str:
    return " / ".join(value for value in (clean(item.get("category")), clean(item.get("sub_category"))) if value)


def validate_public_https_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("only public HTTPS URLs are allowed")
    addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    if not addresses:
        raise ValueError("preview host did not resolve")
    for resolved_address in addresses:
        address = ipaddress.ip_address(resolved_address)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved:
            raise ValueError("private preview addresses are not allowed")
    return value


class PublicHttpsRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request | None:
        validate_public_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def image_mime(payload: bytes, fallback: str = "") -> str:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    if len(payload) >= 12 and payload[4:8] == b"ftyp" and payload[8:12] in {b"avif", b"avis"}:
        return "image/avif"
    return fallback if fallback.startswith("image/") else "application/octet-stream"


def strip_markup(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"!\[[^\]]*]\([^)]+\)", "", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", text)
    text = re.sub(r"[`*_~>#]+", "", text)
    return clean(text)


def image_urls(text: str) -> list[str]:
    found = re.findall(r"!\[[^\]]*]\(([^)\s]+)", text)
    found += re.findall(r"<img\b[^>]*\bsrc=[\"']([^\"']+)", text, flags=re.I)
    urls = list(dict.fromkeys(html.unescape(item).strip() for item in found if item.strip()))
    return [url for url in urls if not is_decorative_image(url)]


def is_decorative_image(value: str) -> bool:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    return (
        hostname in {"img.shields.io", "awesome.re"}
        or "badge.svg" in path
        or "/actions/workflows/" in path
    )


def parse_json(payload: bytes) -> list[dict[str, Any]]:
    data = json.loads(payload.decode("utf-8-sig"))
    if isinstance(data, dict):
        data = next((data[key] for key in ("items", "prompts", "data") if isinstance(data.get(key), list)), [])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def parse_markdown(payload: bytes) -> list[dict[str, Any]]:
    lines = payload.decode("utf-8-sig", errors="replace").splitlines()
    sections: list[tuple[str, str, str]] = []
    category, title, start = "", "", -1
    for index, line in enumerate(lines):
        match = re.match(r"^(#{2,3})\s+(.+?)\s*$", line)
        if not match:
            continue
        level, heading = match.groups()
        if title and start >= 0:
            sections.append((category, title, "\n".join(lines[start:index])))
            title, start = "", -1
        heading = re.sub(r"^(?:No\.)?\s*\d+\s*[:：.、-]*\s*", "", strip_markup(heading), flags=re.I)
        if level == "##":
            category = heading
        else:
            title, start = heading, index
    if title and start >= 0:
        sections.append((category, title, "\n".join(lines[start:])))
    result = []
    fence = re.compile(r"```[\w-]*\s*\n(.*?)(?:\n```|$)", re.S)
    for order, (category, title, section) in enumerate(sections):
        blocks = [clean_multiline(match) for match in fence.findall(section)]
        prompt = next((block for block in blocks if len(block) >= 10), "")
        if prompt:
            urls = image_urls(section)
            result.append({"id": f"md-{order + 1}", "title": title, "prompt": prompt, "category": category, "preview": urls[0] if urls else "", "sort_order": order})
    return result


def parse_html(payload: bytes) -> list[dict[str, Any]]:
    text = payload.decode("utf-8-sig", errors="replace")
    blocks = re.findall(r"<(?:article|section)\b[^>]*>(.*?)</(?:article|section)>", text, re.S | re.I)
    if not blocks:
        blocks = re.findall(r"<div\b[^>]*class=[\"'][^\"']*(?:prompt|card|item)[^\"']*[\"'][^>]*>(.*?)</div>", text, re.S | re.I)
    result = []
    for order, block in enumerate(blocks):
        heading = re.search(r"<h[1-4]\b[^>]*>(.*?)</h[1-4]>", block, re.S | re.I)
        paragraphs = [clean_multiline(strip_markup(item)) for item in re.findall(r"<p\b[^>]*>(.*?)</p>", block, re.S | re.I)]
        paragraphs = [item for item in paragraphs if item]
        title = strip_markup(heading.group(1)) if heading else ""
        prompt = max(paragraphs, key=len) if paragraphs else ""
        urls = image_urls(block)
        if title and prompt:
            result.append({"id": f"html-{order + 1}", "title": title, "prompt": prompt, "preview": urls[0] if urls else "", "sort_order": order})
    return result


def normalize_item(raw: dict[str, Any], source: dict[str, str], index: int) -> dict[str, Any] | None:
    title = clean(raw.get("title") or raw.get("title_cn") or raw.get("title_zh") or raw.get("title_en") or raw.get("name"))
    prompt = clean_multiline(raw.get("prompt") or raw.get("content") or raw.get("text") or raw.get("positive_prompt"))
    if not title or not prompt:
        return None
    preview = raw.get("preview") or raw.get("preview_url") or raw.get("image") or raw.get("image_url") or ""
    preview = urljoin(source["url"], str(preview)) if preview else ""
    item_id = clean(raw.get("id")) or hashlib.sha1(f"{title}\n{prompt}".encode()).hexdigest()[:16]
    mode = clean(raw.get("image_mode") or raw.get("mode")).lower()
    return {
        "id": f"{source['id']}:{item_id}", "title": title[:160], "description": strip_markup(str(raw.get("description") or raw.get("description_cn") or raw.get("summary") or ""))[:500],
        "prompt": prompt, "category": clean(raw.get("category_cn") or raw.get("category_zh") or raw.get("category"))[:80],
        "sub_category": clean(raw.get("sub_category") or raw.get("subcategory"))[:80], "preview": preview[:1200],
        "author": clean(raw.get("author"))[:120], "source_id": source["id"], "source_name": source["name"], "source_homepage": source["homepage"],
        "image_mode": "edit" if mode in {"edit", "image-to-image", "i2i"} else "generate", "image_model": clean(raw.get("image_model") or raw.get("model"))[:80],
        "image_size": clean(raw.get("image_size") or raw.get("size"))[:40], "image_count": raw.get("image_count") or raw.get("n") or 1, "sort_order": raw.get("sort_order", index),
    }


class Library:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.items: list[dict[str, Any]] = []
        self.sources = [{**source, "status": "waiting", "count": 0, "error": "", "synced_at": ""} for source in SOURCES]
        self.syncing = False
        self.synced_at = ""
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            self.items = data.get("items", [])
            cached = {item["id"]: item for item in data.get("sources", [])}
            self.sources = [{**source, **cached.get(source["id"], {})} for source in SOURCES]
            self.synced_at = data.get("synced_at", "")
        except (OSError, ValueError, KeyError):
            pass

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"sources": self.sources, "syncing": self.syncing, "synced_at": self.synced_at, "prompt_count": len(self.items)}

    def page(self, offset: int, limit: int, keyword: str = "", source_id: str = "", category: str = "") -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 60:
            raise ValueError("offset must be non-negative and limit must be 1-60")
        keyword = clean(keyword)[:200].casefold()
        source_id = clean(source_id)
        category = clean(category)
        valid_source_ids = {source["id"] for source in SOURCES}
        if source_id and source_id not in valid_source_ids:
            raise ValueError("unknown prompt source")

        with self.lock:
            source_items = [item for item in self.items if not source_id or item.get("source_id") == source_id]
            categories = sorted({label for item in source_items if (label := item_category(item))}, key=str.casefold)
            filtered = [
                item for item in source_items
                if (not category or item_category(item) == category)
                and (
                    not keyword
                    or keyword in " ".join(
                        clean(item.get(field)) for field in ("title", "description", "prompt", "category", "sub_category", "author", "source_name")
                    ).casefold()
                )
            ]
            total = len(filtered)
            items = [dict(item) for item in filtered[offset:offset + limit]]
        return {"items": items, "total": total, "offset": offset, "limit": limit, "has_more": offset + len(items) < total, "categories": categories}

    def get(self, item_id: str) -> dict[str, Any] | None:
        with self.lock:
            return next((dict(item) for item in self.items if item.get("id") == item_id), None)

    def fetch(self, source: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
        parsed = urlparse(source["url"])
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("only HTTPS prompt sources are allowed")
        try:
            address = ipaddress.ip_address(parsed.hostname)
            if address.is_private or address.is_loopback or address.is_link_local:
                raise ValueError("private prompt source addresses are not allowed")
        except ValueError as exc:
            if "does not appear" not in str(exc):
                raise
        request = Request(source["url"], headers={"User-Agent": "klong-prompt-studio/1.0", "Accept": "application/json,text/markdown,text/html,text/plain"})
        with urlopen(request, timeout=SOURCE_TIMEOUT) as response:
            payload = response.read(MAX_SOURCE_BYTES + 1)
        if len(payload) > MAX_SOURCE_BYTES:
            raise ValueError("prompt source exceeds 4 MiB")
        adapter = source["adapter"]
        raw_items = parse_json(payload) if adapter == "json" else parse_markdown(payload) if adapter == "markdown" else parse_html(payload)
        items = [item for index, raw in enumerate(raw_items) if (item := normalize_item(raw, source, index))]
        if not items:
            raise ValueError("source returned no usable prompts")
        return items, adapter

    def sync(self, source_id: str = "") -> None:
        with self.lock:
            if self.syncing:
                return
            self.syncing = True
            selected_sources = [source for source in SOURCES if not source_id or source["id"] == source_id]
            if not selected_sources:
                self.syncing = False
                return
            for source in self.sources:
                if source["id"] not in {item["id"] for item in selected_sources}:
                    continue
                source.update(status="syncing", error="")
        old_by_source: dict[str, list[dict[str, Any]]] = {}
        for item in self.items:
            old_by_source.setdefault(item.get("source_id", ""), []).append(item)
        fetched: dict[str, list[dict[str, Any]]] = dict(old_by_source)
        with ThreadPoolExecutor(max_workers=SYNC_WORKERS) as executor:
            futures = {executor.submit(self.fetch, source): source for source in selected_sources}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    items, _ = future.result()
                    fetched[source["id"]] = items
                    status, error = "ready", ""
                except (OSError, ValueError, HTTPError, URLError, json.JSONDecodeError) as exc:
                    fetched[source["id"]] = old_by_source.get(source["id"], [])
                    status, error = ("cached" if fetched[source["id"]] else "error"), str(exc)[:300]
                with self.lock:
                    target = next(item for item in self.sources if item["id"] == source["id"])
                    target.update(status=status, error=error, count=len(fetched[source["id"]]), synced_at=now_iso() if status == "ready" else target.get("synced_at", ""))
        combined, seen = [], set()
        for source in SOURCES:
            for item in fetched.get(source["id"], []):
                fingerprint = hashlib.sha1(clean(item.get("prompt")).lower().encode()).hexdigest()
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    combined.append(item)
        with self.lock:
            if combined:
                self.items = combined
            self.synced_at = now_iso()
            self.syncing = False
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps({"items": self.items, "sources": self.sources, "synced_at": self.synced_at}, ensure_ascii=False), encoding="utf-8")


class Settings:
    ENVIRONMENT_ID = ENVIRONMENT_CONNECTION_ID

    def __init__(self, settings_path: Path = SETTINGS_PATH) -> None:
        self.lock = threading.RLock()
        self.settings_path = settings_path
        self.data: dict[str, Any] = {}
        self.session_api_keys: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self.data = payload
        except (OSError, ValueError):
            self.data = {}
        self._migrate_legacy()

    def _migrate_legacy(self) -> None:
        if isinstance(self.data.get("connections"), list):
            return
        legacy_fields = {
            "base_url",
            "default_model",
            "api_key_dpapi",
            "models",
            "models_synced_at",
        }
        connection = None
        if legacy_fields.intersection(self.data):
            connection = {
                "id": "default",
                "name": "默认连接",
                "base_url": self.data.get("base_url") or DEFAULT_BASE_URL,
                "default_model": self.data.get("default_model") or DEFAULT_MODEL,
                "models": self.data.get("models") if isinstance(self.data.get("models"), list) else [],
                "models_synced_at": self.data.get("models_synced_at", ""),
            }
            if self.data.get("api_key_dpapi"):
                connection["api_key_dpapi"] = self.data["api_key_dpapi"]
        self.data["schema_version"] = 2
        self.data["connections"] = [connection] if connection else []
        if connection:
            self.data["active_connection_id"] = (
                self.ENVIRONMENT_ID if self._environment_available() else connection["id"]
            )

    def persist(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_name(f".{self.settings_path.name}.{secrets.token_hex(4)}.tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
            temporary.replace(self.settings_path)
        finally:
            temporary.unlink(missing_ok=True)

    def set_output_dir(self, path: Path | None) -> None:
        with self.lock:
            if path is None:
                self.data.pop("output_dir", None)
            else:
                self.data["output_dir"] = str(path.resolve())
            self.persist()

    @staticmethod
    def _connection_id(value: object) -> str:
        connection_id = clean(value)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", connection_id):
            raise ValueError("invalid connection id")
        return connection_id

    @staticmethod
    def _connection_name(value: object) -> str:
        name = clean(value)
        if not name or len(name) > 60:
            raise ValueError("连接名称不能为空且不能超过 60 个字符")
        return name

    @staticmethod
    def _models(value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("models must be an array")
        return list(dict.fromkeys(clean(item) for item in value if clean(item)))[:300]

    def _stored_connections(self) -> list[dict[str, Any]]:
        connections = self.data.get("connections", [])
        return [item for item in connections if isinstance(item, dict)] if isinstance(connections, list) else []

    def _stored_connection(self, connection_id: str) -> dict[str, Any] | None:
        return next((item for item in self._stored_connections() if clean(item.get("id")) == connection_id), None)

    def _environment_available(self) -> bool:
        return environment_connection_available()

    def _environment_connection(self) -> dict[str, Any] | None:
        if not self._environment_available():
            return None
        key = os.environ.get("KLONG_API_KEY", "").strip()
        return {
            "id": self.ENVIRONMENT_ID,
            "name": "环境变量",
            "base_url": validate_base_url(os.environ.get("KLONG_BASE_URL") or DEFAULT_BASE_URL),
            "default_model": clean(os.environ.get("KLONG_DEFAULT_MODEL") or DEFAULT_MODEL)[:120],
            "models": self._models(self.data.get("environment_models", [])),
            "models_synced_at": clean(self.data.get("environment_models_synced_at")),
            "key_configured": bool(key),
            "key_source": "environment",
            "key_hint": f"••••{key[-4:]}" if key else "",
            "persistent_secret_storage": True,
            "readonly": True,
        }

    def _stored_api_key(self, connection: dict[str, Any]) -> str:
        connection_id = clean(connection.get("id"))
        session_key = self.session_api_keys.get(connection_id, "")
        if session_key:
            return session_key
        encrypted = clean(connection.get("api_key_dpapi"))
        if encrypted:
            try:
                return unprotect_windows_secret(encrypted).strip()
            except (OSError, ValueError):
                return ""
        return ""

    def _stored_snapshot(self, connection: dict[str, Any]) -> dict[str, Any]:
        key = self._stored_api_key(connection)
        return {
            "id": clean(connection.get("id")),
            "name": clean(connection.get("name")) or "未命名连接",
            "base_url": validate_base_url(connection.get("base_url") or DEFAULT_BASE_URL),
            "default_model": clean(connection.get("default_model") or DEFAULT_MODEL)[:120],
            "models": self._models(connection.get("models", [])),
            "models_synced_at": clean(connection.get("models_synced_at")),
            "key_configured": bool(key),
            "key_source": "secure_storage" if connection.get("api_key_dpapi") and key else "session" if key else "none",
            "key_hint": f"••••{key[-4:]}" if key else "",
            "persistent_secret_storage": os.name == "nt",
            "readonly": False,
        }

    def _active_connection_id(self) -> str:
        return active_connection_id(self.data)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            connections = []
            environment = self._environment_connection()
            if environment:
                connections.append(environment)
            connections.extend(self._stored_snapshot(item) for item in self._stored_connections())
            active_id = self._active_connection_id()
            active = next((item for item in connections if item["id"] == active_id), None)
        fallback = {
            "id": "",
            "name": "未配置",
            "base_url": DEFAULT_BASE_URL,
            "default_model": DEFAULT_MODEL,
            "key_configured": False,
            "key_source": "none",
            "key_hint": "",
            "persistent_secret_storage": os.name == "nt",
            "models": [],
            "models_synced_at": "",
            "readonly": False,
        }
        active = active or fallback
        return {
            "schema_version": 2,
            "active_connection_id": active_id,
            "active_connection": active,
            "connections": connections,
            # Compatibility fields for older Prompt Studio clients.
            "base_url": active["base_url"],
            "default_model": active["default_model"],
            "key_configured": active["key_configured"],
            "key_source": active["key_source"],
            "key_hint": active["key_hint"],
            "persistent_secret_storage": active["persistent_secret_storage"],
            "models": active["models"],
        }

    def _write_connection(self, connection: dict[str, Any], payload: dict[str, Any]) -> None:
        connection_id = self._connection_id(connection.get("id"))
        connection["name"] = self._connection_name(payload.get("name") or connection.get("name"))
        connection["base_url"] = validate_base_url(payload.get("base_url") or connection.get("base_url") or DEFAULT_BASE_URL)
        default_model = clean(payload.get("default_model") or connection.get("default_model") or DEFAULT_MODEL)
        if not default_model or len(default_model) > 120:
            raise ValueError("默认模型不能为空且不能超过 120 个字符")
        connection["default_model"] = default_model
        if "models" in payload:
            connection["models"] = self._models(payload.get("models"))
        else:
            connection["models"] = self._models(connection.get("models", []))
        api_key = str(payload.get("api_key") or "").strip()
        clear_api_key = bool(payload.get("clear_api_key"))
        if clear_api_key:
            connection.pop("api_key_dpapi", None)
            self.session_api_keys.pop(connection_id, None)
        elif api_key:
            if os.name == "nt":
                connection["api_key_dpapi"] = protect_windows_secret(api_key)
                self.session_api_keys.pop(connection_id, None)
            else:
                connection.pop("api_key_dpapi", None)
                self.session_api_keys[connection_id] = api_key

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            connection_id = secrets.token_hex(6)
            connection = {"id": connection_id, "name": payload.get("name") or "新连接"}
            self._write_connection(connection, payload)
            connections = self._stored_connections()
            connections.append(connection)
            self.data["connections"] = connections
            self.data["schema_version"] = 2
            if payload.get("activate", True) or not self._active_connection_id():
                self.data["active_connection_id"] = connection_id
            self.persist()
        return self.snapshot()

    def update(self, connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        connection_id = self._connection_id(connection_id)
        if connection_id == self.ENVIRONMENT_ID:
            raise ValueError("环境变量连接为只读配置")
        with self.lock:
            connection = self._stored_connection(connection_id)
            if not connection:
                raise ValueError("连接配置不存在")
            self._write_connection(connection, payload)
            self.persist()
        return self.snapshot()

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        connection_id = clean(payload.get("connection_id")) or self._active_connection_id()
        if connection_id and connection_id != self.ENVIRONMENT_ID and self._stored_connection(connection_id):
            return self.update(connection_id, payload)
        return self.create(payload)

    def activate(self, connection_id: str) -> dict[str, Any]:
        connection_id = self._connection_id(connection_id)
        if connection_id == self.ENVIRONMENT_ID:
            if not self._environment_available():
                raise ValueError("环境变量连接当前不可用")
        elif not self._stored_connection(connection_id):
            raise ValueError("连接配置不存在")
        with self.lock:
            self.data["active_connection_id"] = connection_id
            self.persist()
        return self.snapshot()

    def delete(self, connection_id: str) -> dict[str, Any]:
        connection_id = self._connection_id(connection_id)
        if connection_id == self.ENVIRONMENT_ID:
            raise ValueError("环境变量连接不能删除")
        with self.lock:
            connections = self._stored_connections()
            if not any(clean(item.get("id")) == connection_id for item in connections):
                raise ValueError("连接配置不存在")
            was_active = clean(self.data.get("active_connection_id")) == connection_id
            self.data["connections"] = [item for item in connections if clean(item.get("id")) != connection_id]
            self.session_api_keys.pop(connection_id, None)
            if was_active:
                self.data["active_connection_id"] = (
                    self.ENVIRONMENT_ID if self._environment_available()
                    else clean(self.data["connections"][0].get("id")) if self.data["connections"]
                    else ""
                )
            self.persist()
        return self.snapshot()

    def resolve(self, connection_id: object = "") -> dict[str, str]:
        requested_id = clean(connection_id) or self._active_connection_id()
        if not requested_id:
            raise ValueError("请先添加连接配置")
        if requested_id == self.ENVIRONMENT_ID:
            environment = self._environment_connection()
            if not environment:
                raise ValueError("环境变量连接当前不可用")
            api_key = os.environ.get("KLONG_API_KEY", "").strip()
            connection = environment
        else:
            stored = self._stored_connection(self._connection_id(requested_id))
            if not stored:
                raise ValueError("连接配置不存在")
            api_key = self._stored_api_key(stored)
            connection = self._stored_snapshot(stored)
        if not api_key:
            raise ValueError("请先为当前连接填写 API Key")
        return {
            "id": requested_id,
            "name": str(connection["name"]),
            "api_key": api_key,
            "base_url": str(connection["base_url"]),
            "default_model": str(connection["default_model"]),
        }

    def api_key(self, connection_id: object = "") -> str:
        try:
            return self.resolve(connection_id)["api_key"]
        except ValueError:
            return ""

    def base_url(self, connection_id: object = "") -> str:
        try:
            return self.resolve(connection_id)["base_url"]
        except ValueError:
            return DEFAULT_BASE_URL

    def default_model(self, connection_id: object = "") -> str:
        requested_id = clean(connection_id) or self._active_connection_id()
        snapshot = self.snapshot()
        connection = next((item for item in snapshot["connections"] if item["id"] == requested_id), None)
        return str((connection or snapshot["active_connection"])["default_model"])

    def test(self, payload: dict[str, Any]) -> dict[str, Any]:
        connection_id = clean(payload.get("connection_id"))
        current: dict[str, str] = {}
        if connection_id:
            try:
                current = self.resolve(connection_id)
            except ValueError:
                current = {}
        base_url = validate_base_url(payload.get("base_url") or current.get("base_url") or DEFAULT_BASE_URL)
        api_key = str(payload.get("api_key") or "").strip() or current.get("api_key", "")
        if not api_key:
            raise ValueError("请先填写 API Key")
        environment = os.environ.copy()
        environment["KLONG_API_KEY"] = api_key
        command = [sys.executable, str(GENERATE_SCRIPT), "--list-models", "--base-url", base_url, "--timeout", "30", "--no-progress"]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, timeout=35, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("连接测试超时") from exc
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "连接测试失败").strip()
            raise ValueError(message[-500:])
        try:
            models = [clean(item) for item in json.loads(completed.stdout.strip().splitlines()[-1]).get("models", []) if clean(item)]
        except (ValueError, IndexError, AttributeError) as exc:
            raise ValueError("模型接口返回了无法识别的数据") from exc
        models = list(dict.fromkeys(models))[:300]
        with self.lock:
            if connection_id == self.ENVIRONMENT_ID and self._environment_available():
                self.data["environment_models"] = models
                self.data["environment_models_synced_at"] = now_iso()
                self.persist()
            elif connection_id:
                connection = self._stored_connection(connection_id)
                if connection:
                    connection["models"] = models
                    connection["models_synced_at"] = now_iso()
                    self.persist()
        return {"ok": True, "models": models, "model_count": len(models)}


class Gallery:
    SORTS = {"created_desc", "created_asc", "name_asc", "name_desc", "size_asc", "size_desc"}

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir.resolve()
        self.private_dir = self.output_dir / ".klong"
        self.metadata_dir = self.private_dir / "jobs"
        self.lock = threading.RLock()

    def image_id(self, path: Path) -> str:
        relative = path.resolve().relative_to(self.output_dir).as_posix()
        return base64.urlsafe_b64encode(relative.encode("utf-8")).decode("ascii").rstrip("=")

    def resolve_image(self, image_id: str) -> Path:
        try:
            padding = "=" * (-len(image_id) % 4)
            relative_text = base64.urlsafe_b64decode(image_id + padding).decode("utf-8")
            relative = PurePosixPath(relative_text)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("invalid image id") from exc
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid image path")
        target = (self.output_dir / Path(*relative.parts)).resolve()
        if (
            self.output_dir not in target.parents
            or self.private_dir == target
            or self.private_dir in target.parents
            or target.suffix.lower() not in IMAGE_EXTENSIONS
            or not target.is_file()
        ):
            raise ValueError("image not found")
        return target

    def _metadata(self) -> dict[str, dict[str, Any]]:
        mapped: dict[str, dict[str, Any]] = {}
        if not self.metadata_dir.is_dir():
            return mapped
        manifests = sorted(self.metadata_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for manifest in manifests:
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                for image in data.get("images", []):
                    relative = clean(image.get("path"))
                    if relative and relative not in mapped:
                        mapped[relative] = {**data, **image}
            except (OSError, ValueError, AttributeError):
                continue
        return mapped

    def _active_records(self, keyword: str = "") -> list[dict[str, Any]]:
        keyword = clean(keyword)[:200].casefold()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        metadata = self._metadata()
        records = []
        for path in self.output_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS or self.private_dir in path.parents:
                continue
            try:
                stat = path.stat()
                relative = path.relative_to(self.output_dir).as_posix()
            except OSError:
                continue
            detail = metadata.get(relative, {})
            haystack = " ".join((path.name, clean(detail.get("prompt")), clean(detail.get("model")))).casefold()
            if keyword and keyword not in haystack:
                continue
            image_id = self.image_id(path)
            records.append({
                "id": image_id,
                "name": path.name,
                "relative_path": relative,
                "bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "url": f"/api/gallery/image?id={image_id}",
                "prompt": clean_multiline(detail.get("prompt")),
                "model": clean(detail.get("model")),
                "protocol": clean(detail.get("protocol")),
                "mode": clean(detail.get("mode")),
                "width": detail.get("width"),
                "height": detail.get("height"),
                "duration_seconds": detail.get("duration_seconds"),
                "job_id": clean(detail.get("job_id")),
                "_path": path,
                "_sort_time": stat.st_mtime,
            })
        return records

    def storage_stats(self) -> dict[str, int]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        image_count = 0
        total_bytes = 0
        for path in self.output_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS or self.private_dir in path.parents:
                continue
            try:
                total_bytes += path.stat().st_size
                image_count += 1
            except OSError:
                continue
        return {"image_count": image_count, "total_bytes": total_bytes}

    @staticmethod
    def _sort_records(records: list[dict[str, Any]], sort: str) -> None:
        if sort.startswith("name_"):
            key = lambda item: (item["name"].casefold(), item["id"])
        elif sort.startswith("size_"):
            key = lambda item: (item["bytes"], item["name"].casefold())
        else:
            key = lambda item: (item["_sort_time"], item["name"].casefold())
        records.sort(key=key, reverse=sort.endswith("_desc"))

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items() if not key.startswith("_")}

    def page(self, offset: int, limit: int, keyword: str = "", sort: str = "created_desc") -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 60:
            raise ValueError("offset must be non-negative and limit must be 1-60")
        sort = clean(sort) or "created_desc"
        if sort not in self.SORTS:
            raise ValueError("invalid gallery sort")
        with self.lock:
            records = self._active_records(keyword)
            self._sort_records(records, sort)
            total = len(records)
            if total and offset >= total:
                offset = ((total - 1) // limit) * limit
            items = [self._public(item) for item in records[offset:offset + limit]]
            page = offset // limit + 1
            page_count = max(1, (total + limit - 1) // limit)
            return {
                "items": items,
                "total": total,
                "offset": offset,
                "limit": limit,
                "page": page,
                "page_count": page_count,
                "has_previous": page > 1,
                "has_more": offset + len(items) < total,
                "sort": sort,
            }

    def _selection(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        scope = clean(payload.get("scope")) or "ids"
        if scope not in {"ids", "query"}:
            raise ValueError("invalid gallery selection scope")
        records = self._active_records(clean(payload.get("keyword")) if scope == "query" else "")
        by_id = {item["id"]: item for item in records}
        raw_excluded = payload.get("exclude_ids", [])
        raw_ids = payload.get("ids", [])
        if not isinstance(raw_excluded, list) or not isinstance(raw_ids, list):
            raise ValueError("gallery ids must be arrays")
        excluded = {clean(value) for value in raw_excluded if clean(value)}
        if scope == "query":
            selected = [item for item in records if item["id"] not in excluded]
        else:
            ids = [clean(value) for value in raw_ids if clean(value)]
            selected = [by_id[image_id] for image_id in dict.fromkeys(ids) if image_id in by_id]
        if not selected:
            raise ValueError("no gallery items selected")
        if len(selected) > MAX_GALLERY_BATCH:
            raise ValueError(f"gallery action is limited to {MAX_GALLERY_BATCH} items")
        return selected

    def action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = clean(payload.get("action"))
        if action != "delete":
            raise ValueError("invalid gallery action")
        with self.lock:
            records = self._selection(payload)
            affected = 0
            failures = []
            for record in records:
                try:
                    record["_path"].unlink()
                    affected += 1
                except OSError as exc:
                    failures.append({"id": record["id"], "error": str(exc)[:200]})
            return {"action": action, "affected": affected, "failed": len(failures), "failures": failures}

    def archive(self, payload: dict[str, Any]) -> tuple[Path, int]:
        with self.lock:
            records = self._selection(payload)
            self.private_dir.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                prefix="gallery-archive-",
                suffix=".zip",
                dir=self.private_dir,
                delete=False,
            )
            archive_path = Path(handle.name)
            handle.close()
            names: set[str] = set()
            try:
                with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                    for record in records:
                        candidate = PurePosixPath(record["relative_path"] or record["name"]).as_posix().lstrip("/")
                        if not candidate or ".." in PurePosixPath(candidate).parts:
                            candidate = record["name"]
                        arcname = candidate
                        sequence = 2
                        while arcname.casefold() in names:
                            path = PurePosixPath(candidate)
                            arcname = (path.parent / f"{path.stem}-{sequence}{path.suffix}").as_posix()
                            sequence += 1
                        names.add(arcname.casefold())
                        archive.write(record["_path"], arcname)
                return archive_path, len(records)
            except Exception:
                archive_path.unlink(missing_ok=True)
                raise

    def record_job(self, job: dict[str, Any], payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(result)
        images = []
        for raw_image in result.get("images", []):
            image = dict(raw_image)
            try:
                path = Path(str(image.get("output"))).resolve()
                path.relative_to(self.output_dir)
                image_id = self.image_id(path)
            except (OSError, ValueError):
                images.append(image)
                continue
            image.update(id=image_id, url=f"/api/gallery/image?id={image_id}")
            images.append(image)
        decorated["images"] = images
        with self.lock:
            record_generation_manifest(self.output_dir, job, payload, result)
        return decorated

    def _job_from_manifest(self, data: dict[str, Any]) -> dict[str, Any] | None:
        job_id = clean(data.get("job_id"))
        if not job_id:
            return None
        images = []
        for raw_image in data.get("images", []):
            if not isinstance(raw_image, dict):
                continue
            image = dict(raw_image)
            relative = clean(image.pop("path", ""))
            try:
                path = (self.output_dir / Path(*PurePosixPath(relative).parts)).resolve()
                if (
                    self.output_dir not in path.parents
                    or self.private_dir in path.parents
                    or path.suffix.lower() not in IMAGE_EXTENSIONS
                    or not path.is_file()
                ):
                    raise ValueError("image not found")
                image_id = self.image_id(path)
                image.update(output=str(path), id=image_id, url=f"/api/gallery/image?id={image_id}")
            except (OSError, ValueError):
                image["output"] = str(self.output_dir / Path(*PurePosixPath(relative).parts)) if relative else ""
            images.append(image)
        duration = data.get("duration_seconds")
        if duration is None:
            try:
                started = datetime.fromisoformat(str(data.get("started_at") or data.get("created_at")))
                completed = datetime.fromisoformat(str(data.get("completed_at")))
                duration = round(max(0.0, (completed - started).total_seconds()), 3)
            except (TypeError, ValueError):
                duration = 0
        result = {
            "protocol": clean(data.get("protocol")),
            "mode": clean(data.get("mode")),
            "succeeded": int(data.get("succeeded", len(images)) or 0),
            "failed": int(data.get("failed", 0) or 0),
            "duration_seconds": duration,
            "images": images,
            "failures": data.get("failures", []),
        }
        return {
            "id": job_id,
            "name": clean(data.get("name")) or f"历史任务 {job_id[:6]}",
            "status": clean(data.get("status")) or "completed",
            "created_at": data.get("created_at", ""),
            "started_at": data.get("started_at", ""),
            "completed_at": data.get("completed_at", ""),
            "prompt": clean_multiline(data.get("prompt")),
            "model": clean(data.get("model")),
            "connection_id": clean(data.get("connection_id")),
            "connection_name": clean(data.get("connection_name")),
            "protocol": clean(data.get("protocol")),
            "size": clean(data.get("size")),
            "count": int(data.get("count", max(1, len(images))) or 1),
            "concurrency": int(data.get("concurrency", 1) or 1),
            "progress": [str(line) for line in data.get("progress", [])][-80:],
            "result": result,
            "error": clean_multiline(data.get("error"))[:1000],
        }

    def historical_jobs(self) -> list[dict[str, Any]]:
        if not self.metadata_dir.is_dir():
            return []
        jobs = []
        with self.lock:
            manifests = list(self.metadata_dir.glob("*.json"))
            manifests.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            for manifest in manifests:
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    job = self._job_from_manifest(data)
                    if job:
                        jobs.append(job)
                except (OSError, ValueError, TypeError, AttributeError):
                    continue
        return jobs

    def historical_job(self, job_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
            return None
        target = self.metadata_dir / f"{job_id}.json"
        try:
            with self.lock:
                data = json.loads(target.read_text(encoding="utf-8"))
            return self._job_from_manifest(data)
        except (OSError, ValueError, TypeError, AttributeError):
            return None


class Jobs:
    def __init__(self, gallery: Gallery, settings: Settings) -> None:
        self.gallery = gallery
        self.output_dir = gallery.output_dir
        self.settings = settings
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}

    def has_active_jobs(self) -> bool:
        with self.lock:
            return any(job.get("status") in {"queued", "running"} for job in self.jobs.values())

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        connection = self.settings.resolve(payload.get("connection_id"))
        prompt = clean_multiline(payload.get("prompt"))
        if not prompt or len(prompt) > 100_000:
            raise ValueError("prompt is required and must not exceed 100,000 characters")
        count, concurrency = int(payload.get("count", 1)), int(payload.get("concurrency", 1))
        if not 1 <= count <= 100 or not 1 <= concurrency <= count:
            raise ValueError("count must be 1-100 and concurrency must be between 1 and count")
        model = clean(payload.get("model") or "gpt-image-2")
        if model in {"gpt-image-2-codex", "gpt-image-2-vip"} and concurrency != 1:
            raise ValueError(f"{model} only supports concurrency 1")
        job_id = secrets.token_hex(8)
        job = {
            "id": job_id,
            "name": clean(payload.get("filename")) or f"创作任务 {job_id[:6]}",
            "status": "queued",
            "created_at": now_iso(),
            "prompt": prompt,
            "model": model,
            "connection_id": connection["id"],
            "connection_name": connection["name"],
            "protocol": clean(payload.get("protocol")),
            "size": clean(payload.get("size")),
            "count": count,
            "concurrency": concurrency,
            "progress": [],
            "result": None,
            "error": "",
        }
        with self.lock:
            self.jobs[job_id] = job
        threading.Thread(target=self.run, args=(job_id, payload, connection), daemon=True).start()
        return dict(job)

    def run(self, job_id: str, payload: dict[str, Any], connection: dict[str, str]) -> None:
        job = self.jobs[job_id]
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", clean(payload.get("filename") or f"image-{job_id}"))[:80].strip(".-") or f"image-{job_id}"
        output = self.output_dir / f"{stem}.png"
        command = [
            sys.executable,
            str(GENERATE_SCRIPT),
            "--model", job["model"],
            "--prompt", str(payload["prompt"]),
            "--output", str(output),
            "--count", str(job["count"]),
            "--concurrency", str(job["concurrency"]),
            "--job-id", job["id"],
            "--name", job["name"],
            "--gallery-dir", str(self.output_dir),
            "--connection-id", job["connection_id"],
            "--connection-name", job["connection_name"],
        ]
        protocol, size = clean(payload.get("protocol")), clean(payload.get("size"))
        if protocol in {"openai", "gemini"}:
            command += ["--protocol", protocol]
        if size and protocol != "gemini" and not job["model"].startswith("gemini-"):
            command += ["--size", size]
        temp_path = None
        image_data = payload.get("input_image")
        if image_data:
            try:
                header, encoded = str(image_data).split(",", 1)
                suffix = ".png" if "png" in header else ".webp" if "webp" in header else ".jpg"
                content = base64.b64decode(encoded, validate=True)
                if len(content) > 20 * 1024 * 1024:
                    raise ValueError("input image exceeds 20 MiB")
                handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                handle.write(content); handle.close(); temp_path = handle.name
                command += ["--input-image", temp_path]
            except (ValueError, TypeError) as exc:
                job.update(status="failed", completed_at=now_iso(), error=f"Invalid input image: {exc}")
                job["result"] = self.gallery.record_job(job, payload, {})
                return
        job.update(status="running", started_at=now_iso())
        try:
            environment = os.environ.copy()
            environment["KLONG_API_KEY"] = connection["api_key"]
            environment["KLONG_BASE_URL"] = connection["base_url"]
            environment["KLONG_DEFAULT_MODEL"] = connection["default_model"]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=environment)
            assert process.stderr is not None
            for line in process.stderr:
                with self.lock:
                    job["progress"] = (job["progress"] + [line.strip()])[-80:]
            stdout = process.stdout.read() if process.stdout else ""
            return_code = process.wait()
            try:
                output_payload = json.loads(stdout.strip().splitlines()[-1])
                result = output_payload.get("result", output_payload)
                if not isinstance(result, dict):
                    result = None
            except (ValueError, IndexError):
                result = None
            job["completed_at"] = now_iso()
            result_error = ""
            if result and result.get("failures"):
                result_error = "; ".join(clean(item.get("error")) for item in result["failures"] if clean(item.get("error")))
            job.update(status="completed" if return_code == 0 else "failed", result=result or {}, error="" if return_code == 0 else (result_error or stdout.strip() or "Generation failed")[:1000])
            job["result"] = self.gallery.record_job(job, payload, job["result"])
        except OSError as exc:
            job.update(status="failed", completed_at=now_iso(), error=str(exc))
            try:
                job["result"] = self.gallery.record_job(job, payload, {})
            except OSError:
                job["result"] = {}
        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            current = dict(self.jobs[job_id]) if job_id in self.jobs else None
        return current or self.gallery.historical_job(job_id)

    @staticmethod
    def _summary(job: dict[str, Any]) -> dict[str, Any]:
        result = job.get("result") or {}
        images = result.get("images") or []
        thumbnail_url = next((clean(image.get("url")) for image in images if clean(image.get("url"))), "")
        return {
            "id": job.get("id", ""),
            "name": job.get("name", ""),
            "status": job.get("status", ""),
            "created_at": job.get("created_at", ""),
            "completed_at": job.get("completed_at", ""),
            "model": job.get("model", ""),
            "connection_id": job.get("connection_id", ""),
            "connection_name": job.get("connection_name", ""),
            "count": job.get("count", 1),
            "concurrency": job.get("concurrency", 1),
            "succeeded": result.get("succeeded", 0),
            "failed": result.get("failed", 0),
            "duration_seconds": result.get("duration_seconds", 0),
            "thumbnail_url": thumbnail_url,
        }

    def history(self, limit: int = 50) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("history limit must be 1-100")
        merged = {job["id"]: job for job in self.gallery.historical_jobs()}
        with self.lock:
            merged.update({job_id: dict(job) for job_id, job in self.jobs.items()})
        jobs = sorted(merged.values(), key=lambda job: str(job.get("created_at", "")), reverse=True)
        return {"items": [self._summary(job) for job in jobs[:limit]], "total": len(jobs)}


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        library: Library,
        settings: Settings,
        gallery: Gallery,
        jobs: Jobs,
        token: str,
        storage_state: dict[str, object],
    ):
        super().__init__(address, Handler)
        self.library, self.settings, self.gallery, self.jobs, self.token = library, settings, gallery, jobs, token
        self.storage_lock = threading.RLock()
        self.storage_source = str(storage_state["source"])
        self.storage_locked = bool(storage_state["locked"])
        self.default_output_dir = Path(storage_state["default_path"]).resolve()

    def storage_snapshot(self) -> dict[str, Any]:
        stats = self.gallery.storage_stats()
        return {
            "output_dir": str(self.gallery.output_dir),
            "default_output_dir": str(self.default_output_dir),
            "source": self.storage_source,
            "locked": self.storage_locked,
            **stats,
        }

    def create_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.storage_lock:
            return self.jobs.create(payload)

    def change_output_dir(self, value: object = None, reset: bool = False) -> dict[str, Any]:
        with self.storage_lock:
            if self.storage_locked:
                label = "KLONG_OUTPUT_DIR" if self.storage_source == "environment" else "--output-dir"
                raise ValueError(f"图库位置由 {label} 控制，请修改启动配置后重启")
            if self.jobs.has_active_jobs():
                raise ValueError("有任务正在生成，完成后才能切换图库位置")
            target = validate_output_directory(self.default_output_dir if reset else value)
            self.settings.set_output_dir(None if reset else target)
            self.gallery = Gallery(target)
            self.jobs = Jobs(self.gallery, self.settings)
            self.storage_source = "default" if reset else "saved"
            return self.storage_snapshot()


class Handler(BaseHTTPRequestHandler):
    server: AppServer

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[studio] {self.address_string()} {fmt % args}", file=sys.stderr)

    def allowed_host(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def json_response(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff"); self.end_headers(); self.wfile.write(body)

    def preview_response(self, item_id: str) -> None:
        item = self.server.library.get(item_id)
        if not item:
            self.send_error(HTTPStatus.NOT_FOUND, "prompt not found")
            return
        preview_url = clean(item.get("preview"))
        if not preview_url:
            self.send_error(HTTPStatus.NOT_FOUND, "prompt has no preview")
            return

        cache_key = hashlib.sha256(f"{item_id}\n{preview_url}".encode()).hexdigest()
        cache_path = PREVIEW_CACHE_DIR / f"{cache_key}.img"
        try:
            if cache_path.is_file():
                payload = cache_path.read_bytes()
            else:
                validate_public_https_url(preview_url)
                request = Request(
                    preview_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
                        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif;q=0.9,*/*;q=0.1",
                    },
                )
                opener = build_opener(PublicHttpsRedirectHandler())
                with opener.open(request, timeout=SOURCE_TIMEOUT) as response:
                    validate_public_https_url(response.geturl())
                    fallback_mime = response.headers.get_content_type()
                    payload = response.read(MAX_PREVIEW_BYTES + 1)
                if len(payload) > MAX_PREVIEW_BYTES:
                    raise ValueError("preview image exceeds 12 MiB")
                if image_mime(payload, fallback_mime) == "application/octet-stream":
                    raise ValueError("preview response is not a supported image")
                PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                temporary_path = PREVIEW_CACHE_DIR / f".{cache_key}.{secrets.token_hex(4)}.tmp"
                temporary_path.write_bytes(payload)
                try:
                    temporary_path.replace(cache_path)
                finally:
                    temporary_path.unlink(missing_ok=True)

            mime = image_mime(payload)
            if mime == "application/octet-stream":
                raise ValueError("cached preview is not a supported image")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "public, max-age=86400, immutable")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, ValueError, HTTPError, URLError, socket.gaierror) as exc:
            self.send_error(HTTPStatus.BAD_GATEWAY, str(exc)[:200])

    def gallery_image_response(self, image_id: str, download: bool = False) -> None:
        try:
            target = self.server.gallery.resolve_image(image_id)
            size = target.stat().st_size
            with target.open("rb") as handle:
                mime = image_mime(handle.read(64), mimetypes.guess_type(target.name)[0] or "")
                if mime == "application/octet-stream":
                    raise ValueError("unsupported image format")
                handle.seek(0)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "private, max-age=3600")
                self.send_header("X-Content-Type-Options", "nosniff")
                if download:
                    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", target.name) or "image"
                    self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
                self.end_headers()
                while chunk := handle.read(1024 * 1024):
                    self.wfile.write(chunk)
        except (OSError, ValueError) as exc:
            self.send_error(HTTPStatus.NOT_FOUND, str(exc)[:200])

    def gallery_archive_response(self, payload: dict[str, Any]) -> None:
        archive_path: Path | None = None
        try:
            archive_path, count = self.server.gallery.archive(payload)
            size = archive_path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="klong-gallery-{count}.zip"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with archive_path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    self.wfile.write(chunk)
        finally:
            if archive_path:
                archive_path.unlink(missing_ok=True)

    def do_GET(self) -> None:
        if not self.allowed_host():
            self.send_error(HTTPStatus.FORBIDDEN); return
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT); self.end_headers(); return
        if parsed_path.path == "/api/library":
            self.json_response(self.server.library.snapshot()); return
        if parsed_path.path == "/api/settings":
            self.json_response(self.server.settings.snapshot()); return
        if parsed_path.path == "/api/storage":
            self.json_response(self.server.storage_snapshot()); return
        if parsed_path.path == "/api/prompts":
            query = parse_qs(parsed_path.query)
            try:
                offset = int(query.get("offset", ["0"])[0])
                limit = int(query.get("limit", ["24"])[0])
                self.json_response(self.server.library.page(
                    offset,
                    limit,
                    query.get("keyword", [""])[0],
                    query.get("source", [""])[0],
                    query.get("category", [""])[0],
                ))
            except (TypeError, ValueError) as exc:
                self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed_path.path == "/api/preview":
            item_id = clean(parse_qs(parsed_path.query).get("id", [""])[0])
            if not item_id:
                self.send_error(HTTPStatus.BAD_REQUEST, "missing prompt id"); return
            self.preview_response(item_id); return
        if parsed_path.path == "/api/gallery":
            query = parse_qs(parsed_path.query)
            try:
                self.json_response(self.server.gallery.page(
                    int(query.get("offset", ["0"])[0]),
                    int(query.get("limit", ["24"])[0]),
                    query.get("keyword", [""])[0],
                    query.get("sort", ["created_desc"])[0],
                ))
            except (TypeError, ValueError) as exc:
                self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed_path.path == "/api/gallery/image":
            query = parse_qs(parsed_path.query)
            image_id = clean(query.get("id", [""])[0])
            if not image_id:
                self.send_error(HTTPStatus.BAD_REQUEST, "missing image id"); return
            self.gallery_image_response(
                image_id,
                query.get("download", ["0"])[0] == "1",
            ); return
        if parsed_path.path == "/api/jobs":
            query = parse_qs(parsed_path.query)
            try:
                self.json_response(self.server.jobs.history(int(query.get("limit", ["50"])[0])))
            except (TypeError, ValueError) as exc:
                self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed_path.path.startswith("/api/jobs/"):
            job = self.server.jobs.get(parsed_path.path.rsplit("/", 1)[-1]); self.json_response(job or {"error": "job not found"}, 200 if job else 404); return
        path = "index.html" if parsed_path.path in {"/", "/index.html"} else parsed_path.path.lstrip("/")
        target = (ASSET_DIR / path).resolve()
        if ASSET_DIR.resolve() not in target.parents and target != ASSET_DIR.resolve():
            self.send_error(HTTPStatus.FORBIDDEN); return
        try:
            body = target.read_bytes()
            if target.name == "index.html":
                body = body.replace(b"__KLONG_TOKEN__", self.server.token.encode())
            self.send_response(200); self.send_header("Content-Type", (mimetypes.guess_type(target.name)[0] or "application/octet-stream") + ("; charset=utf-8" if target.suffix in {".html", ".css", ".js"} else "")); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff"); self.end_headers(); self.wfile.write(body)
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self.allowed_host() or not secrets.compare_digest(self.headers.get("X-Klong-Token", ""), self.server.token):
            self.send_error(HTTPStatus.FORBIDDEN); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                raise ValueError("request body too large")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            path = urlparse(self.path).path
            if path == "/api/refresh":
                source_id = clean(payload.get("source_id"))
                threading.Thread(target=self.server.library.sync, args=(source_id,), daemon=True).start(); self.json_response({"accepted": True, "source_id": source_id}); return
            if path == "/api/settings":
                self.json_response(self.server.settings.save(payload)); return
            if path == "/api/settings/test":
                self.json_response(self.server.settings.test(payload)); return
            if path == "/api/storage":
                action = clean(payload.get("action")) or "set"
                if action == "set":
                    self.json_response(self.server.change_output_dir(payload.get("output_dir"))); return
                if action == "reset":
                    self.json_response(self.server.change_output_dir(reset=True)); return
                if action == "pick":
                    selected = choose_output_directory(self.server.gallery.output_dir)
                    self.json_response({**self.server.storage_snapshot(), "selected_path": selected}); return
                if action == "open":
                    open_output_directory(self.server.gallery.output_dir)
                    self.json_response({**self.server.storage_snapshot(), "opened": True}); return
                raise ValueError("unknown storage action")
            if path == "/api/connections":
                self.json_response(self.server.settings.create(payload), HTTPStatus.CREATED); return
            connection_match = re.fullmatch(r"/api/connections/([A-Za-z0-9_-]{1,80})(?:/(activate|delete))?", path)
            if connection_match:
                connection_id, action = connection_match.groups()
                if action == "activate":
                    self.json_response(self.server.settings.activate(connection_id)); return
                if action == "delete":
                    self.json_response(self.server.settings.delete(connection_id)); return
                self.json_response(self.server.settings.update(connection_id, payload)); return
            if path == "/api/jobs":
                self.json_response(self.server.create_job(payload), 202); return
            if path == "/api/gallery/action":
                self.json_response(self.server.gallery.action(payload)); return
            if path == "/api/gallery/archive":
                self.gallery_archive_response(payload); return
            self.json_response({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.json_response({"error": str(exc)}, 400)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Gallery and generation directory. Overrides KLONG_OUTPUT_DIR and the saved Studio location.",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Refresh all prompt sources even when a cache exists.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    library = Library()
    settings = Settings()
    storage_state = resolve_output_directory(settings.settings_path, explicit=args.output_dir)
    gallery = Gallery(Path(storage_state["path"]))
    jobs = Jobs(gallery, settings)
    server = AppServer(
        (args.host, args.port),
        library,
        settings,
        gallery,
        jobs,
        secrets.token_urlsafe(24),
        storage_state,
    )
    if not library.items or args.refresh:
        threading.Thread(target=library.sync, daemon=True).start()
    url = f"http://{args.host}:{server.server_address[1]}"
    print(f"小恐龙图像工作台: {url}")
    print(f"Prompt cache: {CACHE_PATH}")
    print(f"Generated images: {jobs.output_dir}")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭小恐龙图像工作台。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
