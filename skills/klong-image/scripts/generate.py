#!/usr/bin/env python3
"""Generate an image through the Klong OpenAI-compatible or Gemini API."""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import BinaryIO


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
USER_AGENT = "klong-image-skill/0.1"
SERIAL_MODELS = {"gpt-image-2-codex", "gpt-image-2-vip"}


class TransientError(RuntimeError):
    """A temporary upstream failure that may succeed when retried."""


def read_limited(stream: BinaryIO, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    content = stream.read(limit + 1)
    if len(content) > limit:
        raise RuntimeError(f"Response exceeded the {limit // (1024 * 1024)} MiB safety limit")
    return content


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


def request_json(url: str, headers: dict[str, str], payload: dict | None, timeout: int) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **headers}, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(read_limited(response).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(1001).decode("utf-8", errors="replace")
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
        return base64.b64decode(item["b64_json"]), "image/png"
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
    payload = {
        "contents": [{"role": "user", "parts": [{"text": args.prompt}]}],
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
    parser.add_argument("--output", default="generated.png", help="Output image path.")
    parser.add_argument("--size", help="OpenAI-compatible size, for example 1024x1024.")
    parser.add_argument("--base-url", default=os.environ.get("KLONG_BASE_URL", "https://api.klong.lat"))
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--count", type=int, default=1, help="Number of images to generate (1-100).")
    parser.add_argument("--concurrency", type=int, default=1, help="Parallel requests (at least 1; effective maximum is --count).")
    parser.add_argument("--retries", type=int, default=2, help="Retries after a transient failure (0-5).")
    parser.add_argument("--retry-delay", type=float, default=3, help="Initial retry delay in seconds (0-60).")
    parser.add_argument("--check", action="store_true", help="Check model access without generating an image.")
    parser.add_argument("--list-models", action="store_true", help="List model IDs visible to the current key.")
    args = parser.parse_args()
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
    if not args.list_models and not args.model:
        args.model = DEFAULT_OPENAI_MODEL
    if not args.list_models and not args.check and not args.prompt:
        parser.error("--prompt is required unless --check or --list-models is used")
    if args.protocol == "auto":
        args.protocol = "gemini" if args.model in GEMINI_MODELS or (args.model or "").startswith("gemini-") else "openai"
    if args.model in SERIAL_MODELS and args.concurrency != 1:
        parser.error(f"{args.model} only supports --concurrency 1")
    if args.protocol == "gemini" and args.size:
        parser.error("--size is only supported for OpenAI-compatible models")
    return args


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("KLONG_API_KEY")
    if not api_key:
        print("KLONG_API_KEY is not set", file=sys.stderr)
        return 2
    base_url = args.base_url.rstrip("/")
    try:
        if args.list_models:
            print(json.dumps({"models": list_models(base_url, api_key, args.timeout)}, ensure_ascii=False))
            return 0
        if args.check:
            check_model(base_url, api_key, args.model, args.protocol, args.timeout)
            return 0
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        def generate_one(index: int) -> dict:
            for attempt in range(args.retries + 1):
                try:
                    image_bytes, mime_type = (
                        generate_gemini(base_url, api_key, args)
                        if args.protocol == "gemini"
                        else generate_openai(base_url, api_key, args)
                    )
                    break
                except (TransientError, TimeoutError) as exc:
                    if attempt >= args.retries:
                        raise RuntimeError(f"Image {index} failed after {attempt + 1} attempts: {exc}") from exc
                    delay = args.retry_delay * (2**attempt)
                    time.sleep(delay + random.uniform(0, min(1, delay * 0.1)))
            if not image_bytes:
                raise RuntimeError("The API returned an empty image")
            validate_image(image_bytes)
            item_output = output if args.count == 1 else output.with_name(f"{output.stem}-{index:03d}{output.suffix}")
            item_output.write_bytes(image_bytes)
            return {"index": index, "bytes": len(image_bytes), "mime_type": mime_type, "output": str(item_output)}

        results = []
        failures = []
        with ThreadPoolExecutor(max_workers=min(args.concurrency, args.count)) as executor:
            futures = {executor.submit(generate_one, index): index for index in range(1, args.count + 1)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results.append(future.result())
                except (RuntimeError, ValueError, OSError) as exc:
                    failures.append({"index": index, "error": str(exc)})
        results.sort(key=lambda item: item["index"])
        failures.sort(key=lambda item: item["index"])
        print(json.dumps({
            "model": args.model,
            "protocol": args.protocol,
            "requested": args.count,
            "succeeded": len(results),
            "failed": len(failures),
            "images": results,
            "failures": failures,
        }, ensure_ascii=False))
        return 1 if failures else 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
