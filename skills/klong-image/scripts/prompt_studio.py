#!/usr/bin/env python3
"""Run the local 小恐龙 prompt browser and image generation studio."""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
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
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
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
from image_sizes import constrain_image_size

GEMINI_BASE_SIZES = {
    "1:1": (1024, 1024), "2:3": (1024, 1536), "3:2": (1536, 1024),
    "3:4": (1024, 1365), "4:3": (1365, 1024), "5:4": (1152, 928), "4:5": (928, 1152),
    "9:16": (1080, 1920), "16:9": (1920, 1080), "21:9": (1584, 672),
}
GEMINI_RATIOS = set(GEMINI_BASE_SIZES)
GEMINI_IMAGE_SIZES = {"1K", "2K", "4K"}
GEMINI_SIZE_PRESETS = {}
for _ratio, (_width, _height) in GEMINI_BASE_SIZES.items():
    for _tier, _multiplier in (("1K", 1), ("2K", 2), ("4K", 4)):
        _value, _ = constrain_image_size(f"{_width * _multiplier}x{_height * _multiplier}")
        # Old manifests only stored the constrained pixel value. Some 2K and 4K
        # presets collide at the upstream limit, so prefer the earlier/lower tier.
        GEMINI_SIZE_PRESETS.setdefault(_value, (_ratio, _tier))


REGISTRY_MANIFEST_MAX_BYTES = 128 * 1024
REGISTRY_PAYLOAD_MAX_BYTES = 8 * 1024 * 1024
REGISTRY_TIMEOUT = 30
MAX_INPUT_IMAGES = 5
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_BODY_BYTES = MAX_INPUT_IMAGES * (((MAX_INPUT_BYTES + 2) // 3) * 4) + 2 * 1024 * 1024
MAX_PREVIEW_BYTES = 12 * 1024 * 1024
MAX_GALLERY_BATCH = 10_000
SOURCE_TIMEOUT = 12
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "prompt-studio"
GENERATE_SCRIPT = Path(__file__).resolve().parent / "generate.py"
CACHE_PATH = CACHE_DIR / "prompt-library.json"
PREVIEW_CACHE_DIR = CACHE_DIR / "previews"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}
# The registry is generated and validated by https://github.com/yukkcat/image-prompts.
# Keep the source metadata here as a fallback for an empty/offline cache; a successful
# registry sync replaces it with the metadata from manifest.json.
REGISTRY_BASE_URL = "https://raw.githubusercontent.com/yukkcat/image-prompts/main/dist"
SOURCES = [
    {"id": "banana-prompt-quicker", "name": "Banana Prompt Quicker", "adapter": "json", "url": "https://glidea.github.io/banana-prompt-quicker/prompts.json", "homepage": "https://glidea.github.io/banana-prompt-quicker/", "registry_path": "sources/banana-prompt-quicker.json"},
    {"id": "davidwu-gpt-image2-prompts", "name": "DavidWu GPT Image 2 Prompts", "adapter": "json", "url": "https://raw.githubusercontent.com/davidwuw0811-boop/awesome-gpt-image2-prompts/main/prompts.json", "homepage": "https://github.com/davidwuw0811-boop/awesome-gpt-image2-prompts", "registry_path": "sources/davidwu-gpt-image2-prompts.json"},
    {"id": "freestylefly-gpt-image-2", "name": "Freestylefly GPT Image 2", "adapter": "json", "url": "https://raw.githubusercontent.com/freestylefly/awesome-gpt-image-2/main/data/cases.json", "homepage": "https://github.com/freestylefly/awesome-gpt-image-2", "registry_path": "sources/freestylefly-gpt-image-2.json"},
    {"id": "awesome-gpt-image", "name": "Awesome GPT Image", "adapter": "markdown", "url": "https://raw.githubusercontent.com/ZeroLu/awesome-gpt-image/main/README.zh-CN.md", "homepage": "https://github.com/ZeroLu/awesome-gpt-image", "registry_path": "sources/awesome-gpt-image.json"},
    {"id": "awesome-gpt4o-image-prompts", "name": "Awesome GPT-4o Image Prompts", "adapter": "markdown", "url": "https://raw.githubusercontent.com/ImgEdify/Awesome-GPT4o-Image-Prompts/main/README.zh-CN.md", "homepage": "https://github.com/ImgEdify/Awesome-GPT4o-Image-Prompts", "registry_path": "sources/awesome-gpt4o-image-prompts.json"},
    {"id": "youmind-gpt-image-2", "name": "YouMind GPT Image 2", "adapter": "markdown", "url": "https://raw.githubusercontent.com/YouMind-OpenLab/awesome-gpt-image-2/main/README_zh.md", "homepage": "https://github.com/YouMind-OpenLab/awesome-gpt-image-2", "registry_path": "sources/youmind-gpt-image-2.json"},
    {"id": "youmind-nano-banana-pro", "name": "YouMind Nano Banana Pro", "adapter": "markdown", "url": "https://raw.githubusercontent.com/YouMind-OpenLab/awesome-nano-banana-pro-prompts/main/README_zh.md", "homepage": "https://github.com/YouMind-OpenLab/awesome-nano-banana-pro-prompts", "registry_path": "sources/youmind-nano-banana-pro.json"},
]

REGISTRY_ITEM_FIELDS = {
    "id",
    "sourceId",
    "title",
    "prompt",
    "description",
    "coverUrl",
    "referenceImageUrls",
    "tags",
    "author",
    "sourceUrl",
    "createdAt",
    "imageMode",
    "imageModel",
    "imageSize",
    "imageCount",
}
REGISTRY_REQUIRED_ITEM_FIELDS = REGISTRY_ITEM_FIELDS - {"imageSize", "imageCount"}
REGISTRY_MANIFEST_FIELDS = {
    "schemaVersion",
    "generatedAt",
    "registryHash",
    "total",
    "promptsPath",
    "sources",
}
REGISTRY_SOURCE_FIELDS = {"id", "name", "homepage", "upstreamUrl", "count", "path", "sha256"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_multiline(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def normalize_input_images(payload: dict[str, Any]) -> list[str]:
    if "input_images" in payload:
        values = payload.get("input_images")
        if not isinstance(values, list):
            raise ValueError("input_images must be an array")
    else:
        legacy = payload.get("input_image")
        values = [legacy] if legacy else []
    if len(values) > MAX_INPUT_IMAGES:
        raise ValueError(f"input_images must contain at most {MAX_INPUT_IMAGES} images")
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("every input image must be a non-empty data URL")
    return values


def job_output_stem(value: object, job_id: str) -> str:
    fallback = f"image-{job_id}"
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", clean(value) or fallback)[:64].strip(".-") or fallback
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "", job_id)[:8] or secrets.token_hex(4)
    return f"{base}-{suffix}"


def _nonnegative_int(value: object, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def continuation_result(job: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize an existing job so a new generation batch can be appended safely."""
    if not job:
        return {
            "requested": 0,
            "succeeded": 0,
            "failed": 0,
            "duration_seconds": 0,
            "images": [],
            "failures": [],
            "batches": [],
        }, []

    raw_result = job.get("result") if isinstance(job.get("result"), dict) else {}
    result = copy.deepcopy(raw_result)
    images = [dict(image) for image in result.get("images", []) if isinstance(image, dict)]
    failures = [dict(failure) for failure in result.get("failures", []) if isinstance(failure, dict)]
    requested = _nonnegative_int(result.get("requested"), _nonnegative_int(job.get("count"), len(images) + len(failures)))
    legacy_batch_id = clean(job.get("batch_id")) or f"{clean(job.get('id')) or 'legacy'}-initial"
    for position, image in enumerate(images, 1):
        image.setdefault("index", position)
        image.setdefault("batch_index", position)
        image.setdefault("batch_id", legacy_batch_id)
        image.setdefault("prompt", clean_multiline(job.get("prompt")))
        image.setdefault("model", clean(job.get("model")))
        image.setdefault("size", clean(job.get("size")))
        image.setdefault("mode", clean(result.get("mode") or job.get("mode")))
        image.setdefault("protocol", clean(result.get("protocol") or job.get("protocol")))
        image.setdefault("connection_id", clean(job.get("connection_id")))
        image.setdefault("connection_name", clean(job.get("connection_name")))
        image.setdefault("created_at", job.get("completed_at") or job.get("created_at", ""))

    batches = [dict(batch) for batch in result.get("batches", []) if isinstance(batch, dict)]
    if not batches and (requested or images or failures):
        batches = [{
            "id": legacy_batch_id,
            "status": clean(job.get("status")) or "completed",
            "created_at": job.get("created_at", ""),
            "completed_at": job.get("completed_at", ""),
            "prompt": clean_multiline(job.get("prompt")),
            "model": clean(job.get("model")),
            "size": clean(job.get("size")),
            "mode": clean(result.get("mode") or job.get("mode")),
            "protocol": clean(result.get("protocol") or job.get("protocol")),
            "connection_id": clean(job.get("connection_id")),
            "connection_name": clean(job.get("connection_name")),
            "count": requested,
            "concurrency": _nonnegative_int(job.get("concurrency"), 1),
            "succeeded": _nonnegative_int(result.get("succeeded"), len(images)),
            "failed": _nonnegative_int(result.get("failed"), len(failures)),
            "duration_seconds": result.get("duration_seconds", 0),
        }]

    result.update({
        "requested": requested,
        "succeeded": _nonnegative_int(result.get("succeeded"), len(images)),
        "failed": _nonnegative_int(result.get("failed"), len(failures)),
        "duration_seconds": result.get("duration_seconds", 0) or 0,
        "images": images,
        "failures": failures,
        "batches": batches,
    })
    result.pop("current_batch", None)
    return result, batches


def merge_generation_batch(
    previous_result: dict[str, Any],
    previous_batches: list[dict[str, Any]],
    batch: dict[str, Any],
    batch_result: dict[str, Any] | None,
    status: str,
) -> dict[str, Any]:
    """Append one batch while preserving image-level generation metadata."""
    current = copy.deepcopy(batch_result) if isinstance(batch_result, dict) else {}
    previous_images = [dict(image) for image in previous_result.get("images", []) if isinstance(image, dict)]
    previous_failures = [dict(failure) for failure in previous_result.get("failures", []) if isinstance(failure, dict)]
    offset = max((_nonnegative_int(image.get("index")) for image in previous_images), default=0)

    current_images = []
    for position, raw_image in enumerate(current.get("images", []), 1):
        if not isinstance(raw_image, dict):
            continue
        image = dict(raw_image)
        local_index = _nonnegative_int(image.get("index"), position) or position
        image.update({
            "index": offset + position,
            "batch_index": local_index,
            "batch_id": batch["id"],
            "prompt": batch["prompt"],
            "model": batch["model"],
            "size": batch["size"],
            "mode": current.get("mode") or batch.get("mode", ""),
            "protocol": current.get("protocol") or batch.get("protocol", ""),
            "connection_id": batch.get("connection_id", ""),
            "connection_name": batch.get("connection_name", ""),
            "created_at": image.get("created_at") or batch.get("completed_at") or batch.get("created_at", ""),
        })
        current_images.append(image)

    current_failures = []
    for position, raw_failure in enumerate(current.get("failures", []), 1):
        if not isinstance(raw_failure, dict):
            continue
        failure = dict(raw_failure)
        failure.setdefault("index", position)
        failure["batch_id"] = batch["id"]
        current_failures.append(failure)

    current_succeeded = _nonnegative_int(current.get("succeeded"), len(current_images))
    current_failed = _nonnegative_int(current.get("failed"), len(current_failures))
    batch_summary = {
        **batch,
        "status": status,
        "completed_at": batch.get("completed_at", ""),
        "protocol": current.get("protocol") or batch.get("protocol", ""),
        "mode": current.get("mode") or batch.get("mode", ""),
        "succeeded": current_succeeded,
        "failed": current_failed,
        "duration_seconds": current.get("duration_seconds", 0) or 0,
    }
    batches = [copy.deepcopy(item) for item in previous_batches] + [batch_summary]
    return {
        "protocol": current.get("protocol") or batch.get("protocol") or previous_result.get("protocol", ""),
        "mode": current.get("mode") or batch.get("mode") or previous_result.get("mode", ""),
        "model": batch["model"],
        "requested": _nonnegative_int(previous_result.get("requested")) + _nonnegative_int(batch.get("count")),
        "concurrency": batch.get("concurrency", 1),
        "succeeded": _nonnegative_int(previous_result.get("succeeded"), len(previous_images)) + current_succeeded,
        "failed": _nonnegative_int(previous_result.get("failed"), len(previous_failures)) + current_failed,
        "duration_seconds": round(float(previous_result.get("duration_seconds", 0) or 0) + float(current.get("duration_seconds", 0) or 0), 3),
        "images": previous_images + current_images,
        "failures": previous_failures + current_failures,
        "batches": batches,
        "current_batch": batch_summary,
    }


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


def registry_path(value: object) -> str:
    """Return a safe path relative to the published registry directory."""
    path = str(value or "").replace("\\", "/").lstrip("/")
    if not path or ".." in path.split("/") or not re.fullmatch(r"[A-Za-z0-9._/-]+", path):
        raise ValueError("invalid prompt registry path")
    return path


def cached_source_definition(raw: object, fallback: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Normalize a source definition stored by an older or newer cache."""
    if not isinstance(raw, dict):
        return None
    fallback = fallback or {}
    source_id = clean(raw.get("id") or fallback.get("id"))
    if not re.fullmatch(r"[a-z0-9-]+", source_id):
        return None
    name = clean(raw.get("name") or fallback.get("name"))
    homepage = clean(raw.get("homepage") or fallback.get("homepage"))
    upstream_url = clean(
        raw.get("url")
        or raw.get("upstream_url")
        or raw.get("upstreamUrl")
        or fallback.get("url")
        or fallback.get("upstream_url")
    )
    if not name or not homepage.startswith(("http://", "https://")) or not upstream_url.startswith(("http://", "https://")):
        return None
    result = {
        **fallback,
        "id": source_id,
        "name": name[:160],
        "adapter": clean(raw.get("adapter"))
        or ("registry" if raw.get("path") or raw.get("sha256") else clean(fallback.get("adapter")))
        or "registry",
        "url": upstream_url,
        "homepage": homepage,
    }
    path = raw.get("registry_path") or raw.get("path") or fallback.get("registry_path") or fallback.get("path")
    if path:
        try:
            result["registry_path"] = registry_path(path)
        except ValueError:
            return None
    if "count" in raw:
        try:
            result["count"] = max(0, int(raw["count"]))
        except (TypeError, ValueError):
            result["count"] = 0
    if "sha256" in raw and re.fullmatch(r"[a-fA-F0-9]{64}", clean(raw.get("sha256"))):
        result["sha256"] = clean(raw.get("sha256")).lower()
    return result


def registry_url(path: object) -> str:
    return f"{REGISTRY_BASE_URL.rstrip('/')}/{registry_path(path)}"


def registry_http_url(value: object, field: str, *, allow_empty: bool = False) -> str:
    """Validate one absolute HTTP(S) URL from the published registry."""
    if not isinstance(value, str):
        raise ValueError(f"prompt registry {field} must be a string")
    value = value.strip()
    if not value and allow_empty:
        return ""
    parsed = urlparse(value)
    if (
        not value
        or any(char.isspace() for char in value)
        or parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
    ):
        raise ValueError(f"prompt registry {field} must be an absolute HTTP(S) URL")
    return value


def registry_source(raw: object) -> dict[str, Any]:
    """Normalize one source entry from image-prompts/dist/manifest.json."""
    if not isinstance(raw, dict):
        raise ValueError("prompt registry source must be an object")
    if set(raw) != REGISTRY_SOURCE_FIELDS:
        raise ValueError("prompt registry source fields do not match schema version 1")
    source_id = clean(raw.get("id"))
    if not re.fullmatch(r"[a-z0-9-]+", source_id):
        raise ValueError(f"invalid prompt registry source id: {source_id[:80]}")
    name = clean(raw.get("name"))
    homepage = registry_http_url(raw.get("homepage"), "source homepage")
    upstream_url = registry_http_url(raw.get("upstreamUrl"), "source upstreamUrl")
    path = registry_path(raw.get("path"))
    count = raw.get("count")
    if isinstance(count, bool) or not isinstance(count, int):
        raise ValueError(f"invalid prompt registry source count: {source_id}")
    digest = clean(raw.get("sha256")).lower()
    if not name:
        raise ValueError(f"prompt registry source is missing a name: {source_id}")
    if count < 0 or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError(f"invalid prompt registry source metadata: {source_id}")
    return {
        "id": source_id,
        "name": name[:160],
        "adapter": "registry",
        "url": upstream_url,
        "homepage": homepage,
        "registry_path": path,
        "count": count,
        "sha256": digest,
    }


def registry_source_snapshot(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Serialize normalized source metadata in the cache's registry section."""
    return [
        {
            "id": source["id"],
            "name": source["name"],
            "homepage": source["homepage"],
            "upstreamUrl": source["url"],
            "count": source.get("count", 0),
            "path": source.get("registry_path", ""),
            "sha256": source.get("sha256", ""),
        }
        for source in sources
    ]


def parse_registry_manifest(payload: bytes) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("prompt registry manifest is not valid JSON") from exc
    if not isinstance(data, dict) or isinstance(data.get("schemaVersion"), bool) or data.get("schemaVersion") != 1:
        raise ValueError("unsupported prompt registry schema")
    if set(data) != REGISTRY_MANIFEST_FIELDS:
        raise ValueError("prompt registry manifest fields do not match schema version 1")
    revision = clean(data.get("registryHash")).lower()
    generated_at = clean(data.get("generatedAt"))
    prompts_path = registry_path(data.get("promptsPath"))
    raw_sources = data.get("sources")
    if not re.fullmatch(r"[a-f0-9]{64}", revision) or not generated_at:
        raise ValueError("prompt registry manifest is missing a valid revision")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("prompt registry manifest has no sources")
    sources = [registry_source(raw) for raw in raw_sources]
    source_ids = [source["id"] for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("prompt registry manifest contains duplicate sources")
    total = data.get("total")
    if isinstance(total, bool) or not isinstance(total, int):
        raise ValueError("prompt registry manifest has an invalid total")
    if total < 0 or total != sum(source["count"] for source in sources):
        raise ValueError("prompt registry manifest total does not match source counts")
    return {
        "revision": revision,
        "generated_at": generated_at,
        "prompts_path": prompts_path,
        "total": total,
    }, sources, raw_sources


def registry_created_at(value: object) -> str:
    """Normalize and validate the registry's date/date-time contract."""
    if not isinstance(value, str):
        raise ValueError("prompt registry createdAt must be a string")
    value = clean(value)
    if not value:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("prompt registry createdAt has an invalid date") from exc
        return value
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
        raise ValueError("prompt registry createdAt must be a date or RFC 3339 date-time")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError("prompt registry createdAt has an invalid date-time") from exc
    if parsed.tzinfo is None:
        raise ValueError("prompt registry createdAt must include an explicit offset")
    return value


def normalize_registry_item(raw: object, source: dict[str, Any], index: int) -> dict[str, Any]:
    """Validate one schema-v1 registry record and map it to Prompt Studio fields.

    The registry is already normalized upstream, so this path deliberately does
    not apply the permissive aliases used by the legacy source adapters. That
    keeps a malformed or unexpectedly extended payload from silently changing
    the prompt library.
    """
    if not isinstance(raw, dict):
        raise ValueError("prompt registry item must be an object")
    keys = set(raw)
    unknown = keys - REGISTRY_ITEM_FIELDS
    missing = REGISTRY_REQUIRED_ITEM_FIELDS - keys
    if unknown:
        raise ValueError(f"prompt registry item contains unknown fields: {', '.join(sorted(map(str, unknown)))}")
    if missing:
        raise ValueError(f"prompt registry item is missing fields: {', '.join(sorted(missing))}")

    source_id = raw["sourceId"]
    item_id = raw["id"]
    if not isinstance(source_id, str) or source_id != source["id"]:
        raise ValueError("prompt registry item has an invalid sourceId")
    if not isinstance(item_id, str) or not re.fullmatch(rf"{re.escape(source_id)}:[a-f0-9]{{16}}", item_id):
        raise ValueError(f"prompt registry item has an invalid id/source pair: {str(item_id)[:120]}")

    scalar_fields = (
        "title",
        "prompt",
        "description",
        "author",
        "createdAt",
        "imageMode",
        "imageModel",
        "coverUrl",
        "sourceUrl",
    )
    for field in scalar_fields:
        if not isinstance(raw[field], str):
            raise ValueError(f"prompt registry item field {field} must be a string")
    title = clean(raw["title"])
    prompt = clean_multiline(raw["prompt"])
    description = clean_multiline(raw["description"])
    if not title or not prompt:
        raise ValueError("prompt registry item title and prompt cannot be empty")

    cover_url = registry_http_url(raw["coverUrl"], "coverUrl", allow_empty=True)
    source_url = registry_http_url(raw["sourceUrl"], "sourceUrl")
    raw_references = raw["referenceImageUrls"]
    if not isinstance(raw_references, list):
        raise ValueError("prompt registry referenceImageUrls must be an array")
    references: list[str] = []
    for value in raw_references:
        reference = registry_http_url(value, "referenceImageUrls[]")
        if reference in references:
            raise ValueError("prompt registry referenceImageUrls must be unique")
        references.append(reference)

    raw_tags = raw["tags"]
    if not isinstance(raw_tags, list):
        raise ValueError("prompt registry tags must be an array")
    tags: list[str] = []
    for value in raw_tags:
        if not isinstance(value, str):
            raise ValueError("prompt registry tags must contain strings")
        tag = clean(value)
        if not tag:
            raise ValueError("prompt registry tags cannot contain empty values")
        if tag in tags:
            raise ValueError("prompt registry tags must be unique")
        tags.append(tag)

    created_at = registry_created_at(raw["createdAt"])
    image_mode = clean(raw["imageMode"])
    if image_mode not in {"", "generate", "edit"}:
        raise ValueError("prompt registry imageMode is invalid")
    image_model = clean(raw["imageModel"])

    image_size: str | None = None
    if "imageSize" in raw:
        if not isinstance(raw["imageSize"], str):
            raise ValueError("prompt registry imageSize must be a string")
        image_size = clean(raw["imageSize"])
        if not image_size:
            raise ValueError("prompt registry imageSize cannot be empty when present")

    image_count: int | None = None
    if "imageCount" in raw:
        value = raw["imageCount"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("prompt registry imageCount must be a positive integer")
        image_count = value

    # Registry tags are the only category signal in the public contract. Keep
    # the first two labels in the legacy category fields for existing filters.
    result: dict[str, Any] = {
        "id": item_id,
        "title": title[:160],
        "description": description[:500],
        "prompt": prompt,
        "category": tags[0][:80] if tags else "",
        "sub_category": tags[1][:80] if len(tags) > 1 else "",
        "preview": cover_url[:1200],
        "author": clean(raw["author"])[:120],
        "source_id": source["id"],
        "source_name": source["name"],
        "source_homepage": source["homepage"],
        "source_url": source_url[:1200],
        "reference_image_urls": references[:12],
        "tags": tags[:24],
        # An empty imageMode is meaningful: it means the upstream source did
        # not specify whether the prompt generates or edits an image.
        "image_mode": image_mode,
        "image_model": image_model[:80],
        "created_at": created_at,
        "sort_order": index,
    }
    if image_size is not None:
        result["image_size"] = image_size[:40]
    if image_count is not None:
        result["image_count"] = image_count
    return result


class Library:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.items: list[dict[str, Any]] = []
        self.sources = [{**source, "status": "waiting", "count": 0, "error": "", "synced_at": ""} for source in SOURCES]
        self.syncing = False
        self.synced_at = ""
        self.registry_revision = ""
        self.registry_generated_at = ""
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            raw_items = data.get("items", [])
            self.items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
            cached_sources = data.get("sources", [])
            if not isinstance(cached_sources, list):
                cached_sources = []
            cached = {
                clean(item.get("id")): item
                for item in cached_sources
                if isinstance(item, dict) and clean(item.get("id"))
            }
            registry = data.get("registry") if isinstance(data.get("registry"), dict) else {}
            registry_sources = registry.get("sources", []) if isinstance(registry.get("sources"), list) else []
            fallback_by_id = {source["id"]: source for source in SOURCES}
            definitions: dict[str, dict[str, Any]] = {}
            # Start with the older top-level records for migration and let the
            # registry snapshot win last so stale cache metadata cannot replace
            # the names, URLs, or paths declared by the manifest.
            for raw in [*cached_sources, *registry_sources]:
                source_id = clean(raw.get("id")) if isinstance(raw, dict) else ""
                definition = cached_source_definition(raw, fallback_by_id.get(source_id))
                if definition:
                    definitions[source_id] = definition
            for source in SOURCES:
                definitions.setdefault(source["id"], dict(source))
            self.sources = []
            for source_id, source in definitions.items():
                cached_state = cached.get(source_id, {})
                state: dict[str, Any] = {
                    "status": "waiting",
                    "count": 0,
                    "error": "",
                    "synced_at": "",
                    "fetch_ms": 0,
                }
                if isinstance(cached_state.get("status"), str):
                    state["status"] = clean(cached_state.get("status")) or "waiting"
                try:
                    state["count"] = max(0, int(cached_state.get("count", 0)))
                except (TypeError, ValueError):
                    state["count"] = 0
                state["error"] = clean(cached_state.get("error"))[:300]
                state["synced_at"] = clean(cached_state.get("synced_at"))
                try:
                    state["fetch_ms"] = max(0, int(cached_state.get("fetch_ms", 0)))
                except (TypeError, ValueError):
                    state["fetch_ms"] = 0
                self.sources.append({
                    **source,
                    **state,
                })
            self.synced_at = data.get("synced_at", "")
            self.registry_revision = clean(registry.get("revision"))
            self.registry_generated_at = clean(registry.get("generated_at"))
        except (OSError, ValueError, KeyError):
            pass

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "sources": self.sources,
                "syncing": self.syncing,
                "synced_at": self.synced_at,
                "prompt_count": len(self.items),
                "registry_revision": self.registry_revision,
                "registry_generated_at": self.registry_generated_at,
            }

    def needs_registry_sync(self) -> bool:
        """Whether the cache lacks a valid registry snapshot marker."""
        with self.lock:
            return (
                not re.fullmatch(r"[a-f0-9]{64}", self.registry_revision.lower())
                or not self.sources
            )

    def _cache_payload(self) -> dict[str, Any]:
        return {
            "items": self.items,
            "sources": self.sources,
            "synced_at": self.synced_at,
            "registry": {
                "url": REGISTRY_BASE_URL,
                "revision": self.registry_revision,
                "generated_at": self.registry_generated_at,
                "sources": registry_source_snapshot(self.sources),
            },
        }

    def _write_cache(self) -> None:
        """Persist the current library without ever exposing a partial JSON file."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = CACHE_PATH.with_name(f".{CACHE_PATH.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(self._cache_payload(), handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(CACHE_PATH)
        finally:
            temporary.unlink(missing_ok=True)

    def page(self, offset: int, limit: int, keyword: str = "", source_id: str = "", category: str = "") -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 60:
            raise ValueError("offset must be non-negative and limit must be 1-60")
        keyword = clean(keyword)[:200].casefold()
        source_id = clean(source_id)
        category = clean(category)
        valid_source_ids = {source["id"] for source in self.sources}
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

    @staticmethod
    def download(url: str, max_bytes: int, *, timeout: int = SOURCE_TIMEOUT) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("only HTTPS prompt sources are allowed")
        try:
            address = ipaddress.ip_address(parsed.hostname)
            if address.is_private or address.is_loopback or address.is_link_local:
                raise ValueError("private prompt source addresses are not allowed")
        except ValueError as exc:
            if "does not appear" not in str(exc):
                raise
        request = Request(
            url,
            headers={
                "User-Agent": "klong-prompt-studio/2.0",
                "Accept": "application/json,text/markdown,text/html,text/plain",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared_length = int(content_length)
                except (TypeError, ValueError) as exc:
                    raise ValueError("prompt source returned an invalid content length") from exc
                if declared_length > max_bytes:
                    raise ValueError(f"prompt source exceeds {max_bytes} bytes")
            payload = response.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ValueError(f"prompt source exceeds {max_bytes} bytes")
        return payload

    def fetch_registry(self) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """Fetch and validate the atomic image-prompts registry snapshot."""
        manifest_payload = self.download(
            registry_url("manifest.json"),
            REGISTRY_MANIFEST_MAX_BYTES,
            timeout=REGISTRY_TIMEOUT,
        )
        manifest, sources, raw_sources = parse_registry_manifest(manifest_payload)
        payload = self.download(
            registry_url(manifest["prompts_path"]),
            REGISTRY_PAYLOAD_MAX_BYTES,
            timeout=REGISTRY_TIMEOUT,
        )
        expected_hash = hashlib.sha256(
            json.dumps(raw_sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + payload
        ).hexdigest()
        if expected_hash != manifest["revision"]:
            raise ValueError("prompt registry manifest and payload hashes do not match")
        try:
            raw_items = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("prompt registry payload is not valid JSON") from exc
        if not isinstance(raw_items, list) or len(raw_items) != manifest["total"]:
            raise ValueError("prompt registry payload count does not match the manifest")
        sources_by_id = {source["id"]: source for source in sources}
        grouped: dict[str, list[dict[str, Any]]] = {source_id: [] for source_id in sources_by_id}
        seen_ids: set[str] = set()
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                raise ValueError("prompt registry payload contains a non-object item")
            source_id = raw.get("sourceId") if isinstance(raw.get("sourceId"), str) else ""
            source = sources_by_id.get(source_id)
            if source is None:
                raise ValueError(f"prompt registry item references unknown source: {source_id[:80]}")
            item = normalize_registry_item(raw, source, len(grouped[source_id]))
            if item["id"] in seen_ids:
                raise ValueError(f"prompt registry item id is duplicated: {item['id'][:120]}")
            seen_ids.add(item["id"])
            grouped[source_id].append(item)
        for source in sources:
            if len(grouped[source["id"]]) != source["count"]:
                raise ValueError(f"prompt registry source count does not match: {source['id']}")
        return manifest, sources, grouped

    def sync(self, source_id: str = "") -> None:
        with self.lock:
            if self.syncing:
                return
            self.syncing = True
            known_source_ids = {source["id"] for source in self.sources}
            if source_id and source_id not in known_source_ids:
                self.syncing = False
                return
            # The upstream registry is one atomic snapshot. A source-scoped UI
            # refresh therefore updates every known source once the new
            # manifest succeeds, and reports an all-source failure otherwise.
            selected_ids = known_source_ids
            for source in self.sources:
                if source["id"] not in selected_ids:
                    continue
                source.update(status="syncing", error="")
            old_by_source: dict[str, list[dict[str, Any]]] = {}
            for item in self.items:
                old_by_source.setdefault(item.get("source_id", ""), []).append(item)

        def combine(source_order: list[dict[str, Any]], fetched: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
            combined, seen = [], set()
            for source in source_order:
                for item in fetched.get(source["id"], []):
                    # Registry records have stable IDs. Keep records from different
                    # sources even when their prompt text happens to be identical.
                    item_id = clean(item.get("id"))
                    if item_id in seen:
                        continue
                    seen.add(item_id)
                    combined.append(item)
            return combined

        try:
            started = time.monotonic()
            manifest, registry_sources, grouped = self.fetch_registry()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            synced_at = now_iso()
            with self.lock:
                # The registry is an atomic snapshot. Even a source-scoped refresh
                # replaces all records so the revision and cached items cannot drift
                # apart when another source changed in the same publication.
                fetched = grouped
                merged_sources = []
                for source in registry_sources:
                    items = fetched.get(source["id"], [])
                    merged_sources.append({
                        **source,
                        "status": "ready",
                        "count": len(items),
                        "error": "",
                        "synced_at": synced_at,
                        "fetch_ms": elapsed_ms,
                    })
                combined = combine(merged_sources, fetched)
                self.items = combined
                self.sources = merged_sources
                self.registry_revision = manifest["revision"]
                self.registry_generated_at = manifest["generated_at"]
                self.synced_at = synced_at
                self.syncing = False
                self._write_cache()
        except Exception as exc:
            error = str(exc)[:300] or exc.__class__.__name__
            with self.lock:
                for source in self.sources:
                    if source["id"] not in selected_ids:
                        continue
                    cached_items = old_by_source.get(source["id"], [])
                    source.update(
                        status="cached" if cached_items else "error",
                        count=len(cached_items),
                        error=error,
                    )
                self.synced_at = now_iso()
                self.syncing = False
                self._write_cache()


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
        environment["KLONG_BASE_URL"] = base_url
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        command = [
            sys.executable,
            str(GENERATE_SCRIPT),
            "--list-models",
            "--base-url", base_url,
            "--connection-id", connection_id or "connection-test",
            "--connection-name", clean(payload.get("name")) or "连接测试",
            "--timeout", "30",
            "--no-progress",
        ]
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
                "size": clean(detail.get("size")),
                "connection_id": clean(detail.get("connection_id")),
                "connection_name": clean(detail.get("connection_name")),
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
            "model": clean(data.get("model")),
            "requested": int(data.get("requested", data.get("count", max(1, len(images)))) or 1),
            "succeeded": int(data.get("succeeded", len(images)) or 0),
            "failed": int(data.get("failed", 0) or 0),
            "duration_seconds": duration,
            "images": images,
            "failures": data.get("failures", []),
            "batches": data.get("batches", []),
            "current_batch": data.get("current_batch"),
        }
        return {
            "id": job_id,
            "name": clean(data.get("name")) or f"历史任务 {job_id[:6]}",
            "status": clean(data.get("status")) or "completed",
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at") or data.get("completed_at") or data.get("created_at", ""),
            "started_at": data.get("started_at", ""),
            "completed_at": data.get("completed_at", ""),
            "prompt": clean_multiline(data.get("prompt")),
            "model": clean(data.get("model")),
            "connection_id": clean(data.get("connection_id")),
            "connection_name": clean(data.get("connection_name")),
            "protocol": clean(data.get("protocol")),
            "size": clean(data.get("size")),
            "aspect_ratio": clean(data.get("aspect_ratio")),
            "image_size": clean(data.get("image_size")).upper(),
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
                    if data.get("history_hidden") is True:
                        continue
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
            if data.get("history_hidden") is True:
                return None
            return self._job_from_manifest(data)
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def hide_job_history(self, job_id: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
            raise ValueError("invalid job id")
        target = self.metadata_dir / f"{job_id}.json"
        with self.lock:
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return False
            if not isinstance(data, dict):
                raise ValueError("invalid job manifest")
            if data.get("history_hidden") is True:
                return True
            data["history_hidden"] = True
            temporary = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        return True


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

        continue_job_id = clean(payload.get("continue_job_id"))
        if continue_job_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", continue_job_id):
            raise ValueError("invalid task id")
        with self.lock:
            active = self.jobs.get(continue_job_id) if continue_job_id else None
            if active and active.get("status") in {"queued", "running"}:
                raise ValueError("当前任务仍在生成，请完成后再追加")
        previous_job = self.get(continue_job_id) if continue_job_id else None
        if continue_job_id and not previous_job:
            raise ValueError("要继续的任务不存在")

        input_images = normalize_input_images(payload)
        payload = {**payload, "input_images": input_images}
        payload.pop("input_image", None)
        previous_result, previous_batches = continuation_result(previous_job)
        job_id = continue_job_id or secrets.token_hex(8)
        batch_id = secrets.token_hex(8)
        created_at = now_iso()
        mode = "image-to-image" if input_images else "text-to-image"
        size = clean(payload.get("size"))
        requested_protocol = clean(payload.get("protocol"))
        model_family = model.lower()
        is_gemini = requested_protocol == "gemini" or model_family.startswith("gemini-")
        exact_model = model_family.startswith("gpt-image-") and model_family.endswith("-exact")
        if size and not exact_model and not model_family.startswith("nano-banana") and not is_gemini:
            size, _ = constrain_image_size(size)
        aspect_ratio = clean(payload.get("aspect_ratio"))
        image_size = clean(payload.get("image_size")).upper()
        if aspect_ratio.lower() == "auto":
            aspect_ratio = ""
        if image_size.lower() == "auto":
            image_size = ""
        if is_gemini:
            if aspect_ratio and aspect_ratio not in GEMINI_RATIOS:
                raise ValueError(f"Gemini does not support aspect_ratio={aspect_ratio}")
            if image_size and image_size not in GEMINI_IMAGE_SIZES:
                raise ValueError(f"Gemini does not support image_size={image_size}")
            preset = GEMINI_SIZE_PRESETS.get(size)
            if preset:
                aspect_ratio = aspect_ratio or preset[0]
                image_size = image_size or preset[1]
        else:
            aspect_ratio = ""
            image_size = ""
        payload = {
            **payload,
            "size": size,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
        }
        batch = {
            "id": batch_id,
            "status": "queued",
            "created_at": created_at,
            "completed_at": "",
            "prompt": prompt,
            "model": model,
            "size": size,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "mode": mode,
            "protocol": clean(payload.get("protocol")),
            "connection_id": connection["id"],
            "connection_name": connection["name"],
            "count": count,
            "concurrency": concurrency,
        }
        queued_result = merge_generation_batch(previous_result, previous_batches, batch, {}, "queued")
        job = {
            "id": job_id,
            "batch_id": batch_id,
            "name": clean(previous_job.get("name")) if previous_job else clean(payload.get("filename")) or f"创作任务 {job_id[:6]}",
            "status": "queued",
            "created_at": previous_job.get("created_at") if previous_job else created_at,
            "updated_at": created_at,
            "completed_at": "",
            "prompt": prompt,
            "model": model,
            "connection_id": connection["id"],
            "connection_name": connection["name"],
            "protocol": clean(payload.get("protocol")),
            "mode": mode,
            "size": size,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "count": count,
            "concurrency": concurrency,
            "progress": [],
            "result": queued_result,
            "error": "",
        }
        with self.lock:
            self.jobs[job_id] = job
        threading.Thread(
            target=self.run,
            args=(job_id, payload, connection, previous_result, previous_batches, batch),
            daemon=True,
        ).start()
        return copy.deepcopy(job)

    def run(
        self,
        job_id: str,
        payload: dict[str, Any],
        connection: dict[str, str],
        previous_result: dict[str, Any],
        previous_batches: list[dict[str, Any]],
        batch: dict[str, Any],
    ) -> None:
        job = self.jobs[job_id]
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stem = job_output_stem(payload.get("filename"), batch["id"])
        output = self.output_dir / f"{stem}.png"
        command = [
            sys.executable,
            str(GENERATE_SCRIPT),
            "--model", job["model"],
            "--prompt", str(payload["prompt"]),
            "--output", str(output),
            "--count", str(batch["count"]),
            "--concurrency", str(batch["concurrency"]),
            "--job-id", batch["id"],
            "--name", job["name"],
            "--gallery-dir", str(self.output_dir),
            "--connection-id", job["connection_id"],
            "--connection-name", job["connection_name"],
            "--no-history",
        ]
        protocol, size = clean(payload.get("protocol")), clean(payload.get("size"))
        quality = clean(payload.get("quality"))
        if protocol in {"openai", "gemini"}:
            command += ["--protocol", protocol]
        if protocol == "gemini" or job["model"].lower().startswith("gemini-"):
            aspect_ratio = clean(payload.get("aspect_ratio"))
            image_size = clean(payload.get("image_size")).upper()
            if aspect_ratio:
                command += ["--aspect-ratio", aspect_ratio]
            if image_size:
                command += ["--image-size", image_size]
        elif size:
            command += ["--size", size]
        if quality:
            command += ["--quality", quality]

        def finish(batch_result: dict[str, Any], status: str, error: str = "") -> None:
            completed_at = now_iso()
            completed_batch = {**batch, "status": status, "completed_at": completed_at}
            merged = merge_generation_batch(previous_result, previous_batches, completed_batch, batch_result, status)
            job.update(
                status=status,
                updated_at=completed_at,
                completed_at=completed_at,
                result=merged,
                error=error[:1000],
            )
            job["result"] = self.gallery.record_job(job, payload, merged)

        temp_paths: list[str] = []
        try:
            for index, image_data in enumerate(payload.get("input_images") or [], start=1):
                header, encoded = image_data.split(",", 1)
                matched = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64", header, re.IGNORECASE)
                if not matched:
                    raise ValueError(f"reference image {index} must be a PNG, JPEG, or WebP data URL")
                mime_type = matched.group(1).lower()
                content = base64.b64decode(encoded, validate=True)
                if not content or len(content) > MAX_INPUT_BYTES:
                    raise ValueError(f"reference image {index} must be non-empty and not exceed 20 MiB")
                if image_mime(content) != mime_type:
                    raise ValueError(f"reference image {index} content does not match its MIME type")
                suffix = ".png" if mime_type == "image/png" else ".webp" if mime_type == "image/webp" else ".jpg"
                handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                temp_paths.append(handle.name)
                try:
                    handle.write(content)
                finally:
                    handle.close()
                command += ["--input-image", handle.name]
        except (binascii.Error, OSError, TypeError, ValueError) as exc:
            for temp_path in temp_paths:
                Path(temp_path).unlink(missing_ok=True)
            message = f"Invalid input image: {exc}"
            finish({"failed": batch["count"], "failures": [{"error": message}]}, "failed", message)
            return

        started_at = now_iso()
        job.update(status="running", started_at=started_at, updated_at=started_at)
        running_result = copy.deepcopy(job["result"])
        if running_result.get("batches"):
            running_result["batches"][-1]["status"] = "running"
        if running_result.get("current_batch"):
            running_result["current_batch"]["status"] = "running"
        job["result"] = running_result
        try:
            environment = os.environ.copy()
            environment["KLONG_API_KEY"] = connection["api_key"]
            environment["KLONG_BASE_URL"] = connection["base_url"]
            environment["KLONG_DEFAULT_MODEL"] = connection["default_model"]
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["PYTHONUTF8"] = "1"
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
            result_error = ""
            if result and result.get("failures"):
                result_error = "; ".join(clean(item.get("error")) for item in result["failures"] if clean(item.get("error")))
            if result is None:
                result = {"failed": batch["count"], "failures": [{"error": stdout.strip() or "Generation failed"}]}
            finish(
                result,
                "completed" if return_code == 0 else "failed",
                "" if return_code == 0 else (result_error or stdout.strip() or "Generation failed"),
            )
        except OSError as exc:
            try:
                finish({"failed": batch["count"], "failures": [{"error": str(exc)}]}, "failed", str(exc))
            except OSError:
                job.update(status="failed", updated_at=now_iso(), completed_at=now_iso(), error=str(exc))
        finally:
            for temp_path in temp_paths:
                Path(temp_path).unlink(missing_ok=True)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            current = copy.deepcopy(self.jobs[job_id]) if job_id in self.jobs else None
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
            "updated_at": job.get("updated_at") or job.get("completed_at") or job.get("created_at", ""),
            "completed_at": job.get("completed_at", ""),
            "model": job.get("model", ""),
            "connection_id": job.get("connection_id", ""),
            "connection_name": job.get("connection_name", ""),
            "count": result.get("requested", job.get("count", 1)),
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
            merged.update({job_id: copy.deepcopy(job) for job_id, job in self.jobs.items()})
        jobs = sorted(merged.values(), key=lambda job: str(job.get("updated_at") or job.get("created_at", "")), reverse=True)
        return {"items": [self._summary(job) for job in jobs[:limit]], "total": len(jobs)}

    def delete_history(self, job_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
            raise ValueError("invalid job id")
        with self.lock:
            current = self.jobs.get(job_id)
            if current and current.get("status") in {"queued", "running"}:
                raise ValueError("生成中的任务不能删除")
            removed = self.jobs.pop(job_id, None) is not None
        hidden = self.gallery.hide_job_history(job_id)
        if not removed and not hidden:
            raise ValueError("任务不存在")
        return {"id": job_id, "deleted": True, "images_preserved": True}


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
            job_delete_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9_-]{1,80})/delete", path)
            if job_delete_match:
                self.json_response(self.server.jobs.delete_history(job_delete_match.group(1))); return
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
    if not library.items or args.refresh or library.needs_registry_sync():
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
