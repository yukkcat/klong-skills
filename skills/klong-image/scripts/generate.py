#!/usr/bin/env python3
"""Generate an image through the 小恐龙 OpenAI-compatible or Gemini API."""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import secrets
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from connection_store import resolve_cli_connection, resolve_output_directory, validate_base_url
from generation_manifest import now_iso, record_generation_manifest, validate_job_id
from image_sizes import constrain_image_size


OPENAI_MODELS = {
    "gpt-image-2",
    "gpt-image-2-c",
    "gpt-image-2-codex",
    "gpt-image-2-vip",
}
GEMINI_MODELS = {
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image-preview",
}
DEFAULT_OPENAI_MODEL = "gpt-image-2"
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_INPUT_BYTES = 20 * 1024 * 1024
USER_AGENT = "klong-image-skill/0.1"
SERIAL_MODELS = {"gpt-image-2-codex", "gpt-image-2-vip"}
DEFAULT_OUTPUT_DIR = Path(resolve_output_directory()["path"])


class TransientError(RuntimeError):
    """A temporary upstream failure that may succeed when retried."""


class ImageTaskError(RuntimeError):
    def __init__(self, message: str, attempts: int, duration_seconds: float):
        super().__init__(message)
        self.attempts = attempts
        self.duration_seconds = duration_seconds


class ProgressReporter:
    def __init__(self, enabled: bool, total: int, interval: int = 30):
        self.enabled = enabled
        self.total = total
        self.interval = interval
        self.started_at = time.monotonic()
        self.active = set()
        self.succeeded = 0
        self.failed = 0
        self.messages = []
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None

    def emit(self, message: str) -> None:
        with self.lock:
            self.messages = (self.messages + [message])[-80:]
        if self.enabled:
            print(message, file=sys.stderr, flush=True)

    def start(self, model: str, protocol: str, mode: str, concurrency: int, timeout: int) -> None:
        self.emit(
            f"[start] model={model} protocol={protocol} mode={mode} "
            f"requests={self.total} concurrency={min(concurrency, self.total)} timeout={timeout}s"
        )
        if self.enabled:
            self.thread = threading.Thread(target=self._heartbeat, daemon=True)
            self.thread.start()

    def _heartbeat(self) -> None:
        while not self.stop_event.wait(self.interval):
            with self.lock:
                active = len(self.active)
                succeeded = self.succeeded
                failed = self.failed
                completed = succeeded + failed
            elapsed = time.monotonic() - self.started_at
            self.emit(
                f"[waiting] elapsed={elapsed:.1f}s active={active} "
                f"completed={completed}/{self.total} succeeded={succeeded} failed={failed}"
            )

    def request_started(self, index: int) -> None:
        with self.lock:
            self.active.add(index)
        self.emit(f"[request {index}/{self.total}] started")

    def retry(self, index: int, attempt: int, delay: float, error: Exception) -> None:
        self.emit(
            f"[request {index}/{self.total}] retry={attempt} delay={delay:.1f}s reason={error}"
        )

    def request_completed(self, index: int, duration: float, size: int, width: int | None, height: int | None) -> None:
        with self.lock:
            self.active.discard(index)
            self.succeeded += 1
        dimensions = f"{width}x{height}" if width and height else "unknown"
        self.emit(
            f"[request {index}/{self.total}] completed duration={duration:.1f}s "
            f"size={size / (1024 * 1024):.2f}MiB dimensions={dimensions}"
        )

    def request_failed(self, index: int, duration: float, error: Exception) -> None:
        with self.lock:
            self.active.discard(index)
            self.failed += 1
        self.emit(f"[request {index}/{self.total}] failed duration={duration:.1f}s reason={error}")

    def complete(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join()
        elapsed = time.monotonic() - self.started_at
        self.emit(
            f"[complete] duration={elapsed:.1f}s requested={self.total} "
            f"succeeded={self.succeeded} failed={self.failed}"
        )


def read_limited(stream: BinaryIO, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    content = stream.read(limit + 1)
    if len(content) > limit:
        raise RuntimeError(f"Response exceeded the {limit // (1024 * 1024)} MiB safety limit")
    return content


def decode_response_text(content: bytes, headers: object | None = None) -> str:
    """Decode API text while tolerating Chinese upstreams that omit or mislabel charset."""
    if not content:
        return ""
    encodings: list[str] = []
    if headers is not None:
        try:
            charset = headers.get_content_charset()  # type: ignore[attr-defined]
        except (AttributeError, LookupError, TypeError):
            charset = None
        if not charset:
            try:
                content_type = str(headers.get("Content-Type", ""))  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                content_type = ""
            match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type, flags=re.IGNORECASE)
            charset = match.group(1) if match else None
        if charset:
            encodings.append(str(charset).strip())
    encodings.extend(("utf-8-sig", "gb18030"))

    unique_encodings = list(dict.fromkeys(encoding.lower() for encoding in encodings if encoding))
    for encoding in unique_encodings:
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    decoded_candidates = []
    for encoding in unique_encodings or ["utf-8"]:
        try:
            decoded = content.decode(encoding, errors="replace")
        except LookupError:
            continue
        decoded_candidates.append(decoded)
    return min(decoded_candidates, key=lambda value: value.count("\ufffd"), default=content.decode("utf-8", errors="replace"))


def validate_image(content: bytes) -> None:
    signatures = (
        b"\x89PNG\r\n\x1a\n",
        b"\xff\xd8\xff",
        b"GIF87a",
        b"GIF89a",
    )
    is_webp = len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    if not is_webp and not any(content.startswith(signature) for signature in signatures):
        raise RuntimeError("The API response is not a supported PNG, JPEG, GIF, or WebP image")


def output_image_mime(content: bytes, fallback: str = "") -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    normalized = str(fallback or "").split(";", 1)[0].strip().lower()
    return normalized if normalized.startswith("image/") else "application/octet-stream"


def image_extension(mime_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(str(mime_type).split(";", 1)[0].lower(), ".png")


def input_image_mime(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    raise RuntimeError("Input image must be PNG, JPEG, or WebP")


def image_dimensions(content: bytes) -> tuple[int | None, int | None]:
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24:
        return struct.unpack(">II", content[16:24])
    if content.startswith((b"GIF87a", b"GIF89a")) and len(content) >= 10:
        return struct.unpack("<HH", content[6:10])
    if content.startswith(b"\xff\xd8"):
        offset = 2
        start_of_frame = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while offset + 9 <= len(content):
            if content[offset] != 0xFF:
                offset += 1
                continue
            marker = content[offset + 1]
            if marker in start_of_frame:
                height, width = struct.unpack(">HH", content[offset + 5:offset + 9])
                return width, height
            if marker in {0xD8, 0xD9}:
                offset += 2
                continue
            if offset + 4 > len(content):
                break
            segment_length = struct.unpack(">H", content[offset + 2:offset + 4])[0]
            if segment_length < 2:
                break
            offset += 2 + segment_length
    if len(content) >= 30 and content[:4] == b"RIFF" and content[8:16] == b"WEBPVP8X":
        width = int.from_bytes(content[24:27], "little") + 1
        height = int.from_bytes(content[27:30], "little") + 1
        return width, height
    return None, None


def multipart_body(fields: dict[str, str], filename: str, content: bytes, mime_type: str) -> tuple[bytes, str]:
    boundary = f"klong-{secrets.token_hex(16)}"
    chunks = []
    for name, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    safe_filename = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
    chunks.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{safe_filename}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8")
    )
    chunks.extend((content, b"\r\n", f"--{boundary}--\r\n".encode("ascii")))
    return b"".join(chunks), boundary


def request_multipart(url: str, api_key: str, body: bytes, boundary: str, timeout: int) -> dict:
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(decode_response_text(read_limited(response), response.headers))
    except urllib.error.HTTPError as exc:
        detail = decode_response_text(exc.read(4096), exc.headers)
        error = f"HTTP {exc.code}: {detail[:1000]}"
        if exc.code == 429 or 500 <= exc.code <= 599:
            raise TransientError(error) from exc
        raise RuntimeError(error) from exc
    except urllib.error.URLError as exc:
        raise TransientError(f"Network error: {exc.reason}") from exc


def request_json(url: str, headers: dict[str, str], payload: dict | None, timeout: int) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **headers}, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(decode_response_text(read_limited(response), response.headers))
    except urllib.error.HTTPError as exc:
        detail = decode_response_text(exc.read(4096), exc.headers)
        error = f"HTTP {exc.code}: {detail[:1000]}"
        if exc.code == 429 or 500 <= exc.code <= 599:
            raise TransientError(error) from exc
        raise RuntimeError(error) from exc
    except urllib.error.URLError as exc:
        raise TransientError(f"Network error: {exc.reason}") from exc


def list_models(base_url: str, api_key: str, timeout: int) -> list[str]:
    result = request_json(
        f"{base_url}/v1/models",
        {"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        None,
        timeout,
    )
    return sorted(item["id"] for item in result.get("data", []) if isinstance(item, dict) and item.get("id"))


def check_model(base_url: str, api_key: str, model: str, protocol: str, timeout: int) -> None:
    available = set(list_models(base_url, api_key, timeout))
    if model not in available:
        raise RuntimeError(f"Model is not available to this key: {model}")
    print(json.dumps({"available": True, "model": model, "protocol": protocol}, ensure_ascii=False))


def generate_openai(base_url: str, api_key: str, args: argparse.Namespace) -> tuple[bytes, str]:
    if args.input_image_bytes is not None:
        fields = {"model": args.model, "prompt": args.prompt, "n": "1"}
        if args.size:
            fields["size"] = args.size
        body, boundary = multipart_body(
            fields, args.input_image_path.name, args.input_image_bytes, args.input_image_mime
        )
        result = request_multipart(f"{base_url}/v1/images/edits", api_key, body, boundary, args.timeout)
    else:
        payload = {"model": args.model, "prompt": args.prompt, "n": 1}
        if args.size:
            payload["size"] = args.size
        result = request_json(
            f"{base_url}/v1/images/generations",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload,
            args.timeout,
        )
    items = result.get("data") or []
    if not items:
        raise RuntimeError("OpenAI-compatible response did not contain data[0]")
    item = items[0]
    if item.get("b64_json"):
        content = base64.b64decode(item["b64_json"])
        return content, output_image_mime(content, "image/png")
    if item.get("url"):
        image_url = item["url"]
        if not isinstance(image_url, str) or not image_url.lower().startswith(("https://", "http://")):
            raise RuntimeError("The API returned an unsupported image URL")
        request = urllib.request.Request(image_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                return read_limited(response), response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or 500 <= exc.code <= 599:
                raise TransientError(f"Image download returned HTTP {exc.code}") from exc
            raise RuntimeError(f"Image download returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise TransientError(f"Image download failed: {exc.reason}") from exc
    raise RuntimeError("OpenAI-compatible response contained neither b64_json nor url")


def generate_gemini(base_url: str, api_key: str, args: argparse.Namespace) -> tuple[bytes, str]:
    request_parts = [{"text": args.prompt}]
    if args.input_image_bytes is not None:
        request_parts.append({
            "inlineData": {
                "mimeType": args.input_image_mime,
                "data": base64.b64encode(args.input_image_bytes).decode("ascii"),
            }
        })
    payload = {
        "contents": [{"role": "user", "parts": request_parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    result = request_json(
        f"{base_url}/v1beta/models/{args.model}:generateContent",
        {"x-goog-api-key": api_key, "Content-Type": "application/json"},
        payload,
        args.timeout,
    )
    candidates = result.get("candidates") or []
    parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            return base64.b64decode(inline["data"]), inline.get("mimeType") or inline.get("mime_type") or "image/png"
    raise RuntimeError("Gemini response did not contain inline image data")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help=f"Model ID. Defaults to {DEFAULT_OPENAI_MODEL}.")
    parser.add_argument("--protocol", choices=("auto", "openai", "gemini"), default="auto")
    parser.add_argument("--prompt", help="Image prompt. Required unless --check or --list-models is used.")
    parser.add_argument(
        "--output",
        help="Output image path. The suffix is adjusted to the actual PNG, JPEG, WebP, or GIF response format.",
    )
    parser.add_argument("--name", help="Task name shown in local studio history. Defaults to the output filename.")
    parser.add_argument("--job-id", help="Stable task ID used by local studio history.")
    parser.add_argument(
        "--gallery-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Shared local gallery directory used for task history metadata.",
    )
    parser.add_argument("--no-history", action="store_true", help="Do not write local studio task history metadata.")
    parser.add_argument("--connection-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--connection-name", default="", help=argparse.SUPPRESS)
    parser.add_argument("--input-image", help="PNG, JPEG, or WebP source image for image-to-image editing.")
    parser.add_argument("--size", help="OpenAI-compatible size, for example 1024x1024.")
    parser.add_argument("--base-url", help="Override the API base URL from the active connection.")
    parser.add_argument("--timeout", type=int, help="Request timeout in seconds. Defaults to 420 for 4K models, otherwise 360.")
    parser.add_argument("--count", type=int, default=1, help="Number of images to generate (1-100).")
    parser.add_argument("--concurrency", type=int, default=1, help="Parallel requests (at least 1; effective maximum is --count).")
    parser.add_argument("--retries", type=int, default=2, help="Retries after a transient failure (0-5).")
    parser.add_argument("--retry-delay", type=float, default=3, help="Initial retry delay in seconds (0-60).")
    parser.add_argument("--check", action="store_true", help="Check model access without generating an image.")
    parser.add_argument("--list-models", action="store_true", help="List model IDs visible to the current key.")
    parser.add_argument("--no-progress", action="store_true", help="Suppress human-readable progress on stderr.")
    args = parser.parse_args()
    if not args.output:
        filename = datetime.now().strftime("generated-%Y%m%d-%H%M%S-%f.png")
        args.output = str(DEFAULT_OUTPUT_DIR / filename)
    if args.list_models and (args.check or args.prompt):
        parser.error("--list-models cannot be combined with --check or --prompt")
    if not 1 <= args.count <= 100:
        parser.error("--count must be between 1 and 100")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if not 0 <= args.retries <= 5:
        parser.error("--retries must be between 0 and 5")
    if not 0 <= args.retry_delay <= 60:
        parser.error("--retry-delay must be between 0 and 60")
    if not args.list_models and not args.check and not args.prompt:
        parser.error("--prompt is required unless --check or --list-models is used")
    if args.job_id:
        try:
            args.job_id = validate_job_id(args.job_id)
        except ValueError as exc:
            parser.error(str(exc))
    args.input_image_path = None
    args.input_image_bytes = None
    args.input_image_mime = None
    if args.input_image:
        path = Path(args.input_image).expanduser().resolve()
        if not path.is_file():
            parser.error(f"--input-image does not exist or is not a file: {path}")
        if path.stat().st_size > MAX_INPUT_BYTES:
            parser.error(f"--input-image must not exceed {MAX_INPUT_BYTES // (1024 * 1024)} MiB")
        try:
            content = path.read_bytes()
            mime_type = input_image_mime(content)
        except (OSError, RuntimeError) as exc:
            parser.error(str(exc))
        args.input_image_path = path
        args.input_image_bytes = content
        args.input_image_mime = mime_type
    return args


def configure_runtime(args: argparse.Namespace, connection: dict[str, str]) -> None:
    args.model = args.model or connection.get("default_model") or DEFAULT_OPENAI_MODEL
    args.base_url = validate_base_url(args.base_url or connection.get("base_url"))
    args.connection_id = args.connection_id or connection.get("id") or ""
    args.connection_name = args.connection_name or connection.get("name") or ""
    if args.protocol == "auto":
        args.protocol = "gemini" if args.model in GEMINI_MODELS or args.model.startswith("gemini-") else "openai"
    if args.timeout is None:
        args.timeout = 420 if "4k" in args.model.lower() else 360
    if args.model in SERIAL_MODELS and args.concurrency != 1:
        raise ValueError(f"{args.model} only supports --concurrency 1")
    if args.protocol == "gemini" and args.size:
        raise ValueError("--size is only supported for OpenAI-compatible models")
    args.requested_size = args.size or ""
    if args.size:
        args.size, args.size_limited = constrain_image_size(args.size)
    else:
        args.size_limited = False


def main() -> int:
    args = parse_args()
    try:
        connection = resolve_cli_connection(
            process_connection_id=args.connection_id,
            process_connection_name=args.connection_name,
        )
        configure_runtime(args, connection)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    api_key = connection["api_key"]
    base_url = args.base_url.rstrip("/")
    persist_history = None
    progress = None
    job_context = None
    try:
        if args.list_models:
            print(json.dumps({"models": list_models(base_url, api_key, args.timeout)}, ensure_ascii=False))
            return 0
        if args.check:
            check_model(base_url, api_key, args.model, args.protocol, args.timeout)
            return 0
        output = Path(args.output).expanduser().resolve()
        gallery_dir = Path(args.gallery_dir).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        mode = "image-to-image" if args.input_image_bytes is not None else "text-to-image"
        job_id = args.job_id or f"codex-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
        job_name = str(args.name or output.stem).strip()[:120] or output.stem
        created_at = now_iso()
        started_at = now_iso()
        progress = ProgressReporter(not args.no_progress, args.count)
        progress.start(args.model, args.protocol, mode, args.concurrency, args.timeout)
        if args.size_limited:
            progress.emit(f"[size] adjusted requested={args.requested_size} actual={args.size} upstream_limit=true")
        history_manifest = None
        history_enabled = not args.no_history
        if history_enabled:
            try:
                output.relative_to(gallery_dir)
            except ValueError:
                history_enabled = False
                progress.emit(f"[history] skipped because output is outside gallery directory: {gallery_dir}")

        manifest_payload = {
            "prompt": args.prompt,
            "protocol": args.protocol,
            "mode": mode,
            "size": args.size or "",
        }

        job_context = {
            "id": job_id,
            "name": job_name,
            "created_at": created_at,
            "started_at": started_at,
            "prompt": args.prompt,
            "model": args.model,
            "connection_id": args.connection_id,
            "connection_name": args.connection_name,
            "protocol": args.protocol,
            "size": args.size or "",
            "count": args.count,
            "concurrency": min(args.concurrency, args.count),
        }

        def persist_history(status: str, result: dict, error: str = "", completed_at: str = "") -> None:
            nonlocal history_manifest
            if not history_enabled:
                return
            job = {
                **job_context,
                "status": status,
                "completed_at": completed_at,
                "progress": list(progress.messages),
                "error": error,
            }
            try:
                _, target = record_generation_manifest(gallery_dir, job, manifest_payload, result)
                history_manifest = str(target)
            except (OSError, TypeError, ValueError) as exc:
                progress.emit(f"[history] failed to write task metadata: {exc}")

        persist_history("running", {})

        def generate_one(index: int) -> dict:
            request_started_at = time.monotonic()
            attempts = 0
            progress.request_started(index)
            try:
                for attempt in range(args.retries + 1):
                    attempts = attempt + 1
                    try:
                        image_bytes, mime_type = (
                            generate_gemini(base_url, api_key, args)
                            if args.protocol == "gemini"
                            else generate_openai(base_url, api_key, args)
                        )
                        break
                    except (TransientError, TimeoutError) as exc:
                        if attempt >= args.retries:
                            raise RuntimeError(
                                f"Image {index} failed after {attempt + 1} attempts: {exc}"
                            ) from exc
                        delay = args.retry_delay * (2**attempt)
                        progress.retry(index, attempt + 2, delay, exc)
                        time.sleep(delay + random.uniform(0, min(1, delay * 0.1)))
                if not image_bytes:
                    raise RuntimeError("The API returned an empty image")
                validate_image(image_bytes)
                mime_type = output_image_mime(image_bytes, mime_type)
                width, height = image_dimensions(image_bytes)
                item_base = output if args.count == 1 else output.with_name(f"{output.stem}-{index:03d}{output.suffix}")
                item_output = item_base.with_suffix(image_extension(mime_type))
                item_output.write_bytes(image_bytes)
                duration = time.monotonic() - request_started_at
                progress.request_completed(index, duration, len(image_bytes), width, height)
                return {
                    "index": index,
                    "bytes": len(image_bytes),
                    "mime_type": mime_type,
                    "width": width,
                    "height": height,
                    "attempts": attempts,
                    "duration_seconds": round(duration, 3),
                    "output": str(item_output),
                }
            except (RuntimeError, ValueError, OSError) as exc:
                duration = time.monotonic() - request_started_at
                progress.request_failed(index, duration, exc)
                raise ImageTaskError(str(exc), attempts, duration) from exc

        results = []
        failures = []
        with ThreadPoolExecutor(max_workers=min(args.concurrency, args.count)) as executor:
            futures = {executor.submit(generate_one, index): index for index in range(1, args.count + 1)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results.append(future.result())
                except ImageTaskError as exc:
                    failures.append({
                        "index": index,
                        "attempts": exc.attempts,
                        "duration_seconds": round(exc.duration_seconds, 3),
                        "error": str(exc),
                    })
        results.sort(key=lambda item: item["index"])
        failures.sort(key=lambda item: item["index"])
        progress.complete()
        total_duration = time.monotonic() - progress.started_at
        completed_at = now_iso()
        status = "failed" if failures else "completed"
        result = {
            "model": args.model,
            "protocol": args.protocol,
            "mode": mode,
            "requested": args.count,
            "concurrency": min(args.concurrency, args.count),
            "succeeded": len(results),
            "failed": len(failures),
            "duration_seconds": round(total_duration, 3),
            "images": results,
            "failures": failures,
        }
        error = "; ".join(str(item.get("error") or "") for item in failures if item.get("error"))
        persist_history(status, result, error, completed_at)
        print(json.dumps({
            **job_context,
            "status": status,
            "completed_at": completed_at,
            "progress": list(progress.messages),
            "error": error,
            "history_manifest": history_manifest,
            "result": result,
        }, ensure_ascii=False))
        return 1 if failures else 0
    except (RuntimeError, ValueError, OSError) as exc:
        if persist_history is not None and progress is not None and job_context is not None:
            completed_at = now_iso()
            duration = round(max(0.0, time.monotonic() - progress.started_at), 3)
            result = {
                "model": job_context["model"],
                "protocol": job_context["protocol"],
                "mode": "image-to-image" if args.input_image_bytes is not None else "text-to-image",
                "requested": job_context["count"],
                "concurrency": job_context["concurrency"],
                "succeeded": 0,
                "failed": job_context["count"],
                "duration_seconds": duration,
                "images": [],
                "failures": [{"error": str(exc)}],
            }
            persist_history("failed", result, str(exc), completed_at)
            print(json.dumps({
                **job_context,
                "status": "failed",
                "completed_at": completed_at,
                "progress": list(progress.messages),
                "error": str(exc),
                "history_manifest": history_manifest,
                "result": result,
            }, ensure_ascii=False))
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
