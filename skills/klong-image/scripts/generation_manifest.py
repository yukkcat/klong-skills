"""Build and persist generation manifests shared by the CLI and Prompt Studio."""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,80}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_multiline(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def validate_job_id(value: object) -> str:
    job_id = clean(value)
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("job id must contain only letters, numbers, underscores, or hyphens")
    return job_id


def _manifest_images(output_dir: Path, result: dict[str, Any]) -> list[dict[str, Any]]:
    images = []
    for raw_image in result.get("images", []):
        if not isinstance(raw_image, dict):
            continue
        image = dict(raw_image)
        try:
            path = Path(str(image.get("output"))).expanduser().resolve()
            relative = path.relative_to(output_dir).as_posix()
        except (OSError, ValueError):
            continue
        images.append({
            **{key: value for key, value in image.items() if key not in {"output", "url", "id"}},
            "path": relative,
        })
    return images


def build_generation_manifest(
    output_dir: Path,
    job: dict[str, Any],
    payload: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    job_id = validate_job_id(job.get("id"))
    images = _manifest_images(output_dir, result)
    status = clean(job.get("status")) or "completed"
    completed_at = job.get("completed_at", "")
    if not completed_at and status in {"completed", "failed"}:
        completed_at = now_iso()
    return {
        "job_id": job_id,
        "name": clean(job.get("name")),
        "status": status,
        "created_at": job.get("created_at") or now_iso(),
        "updated_at": job.get("updated_at") or completed_at or job.get("started_at") or job.get("created_at") or now_iso(),
        "started_at": job.get("started_at", ""),
        "completed_at": completed_at,
        "prompt": clean_multiline(payload.get("prompt")),
        "model": clean(job.get("model")),
        "connection_id": clean(job.get("connection_id")),
        "connection_name": clean(job.get("connection_name")),
        "protocol": clean(result.get("protocol") or payload.get("protocol")),
        "mode": clean(result.get("mode") or payload.get("mode")),
        "size": clean(payload.get("size")),
        "count": int(job.get("count", 1) or 1),
        "requested": int(result.get("requested", job.get("count", 1)) or 1),
        "concurrency": int(job.get("concurrency", 1) or 1),
        "succeeded": int(result.get("succeeded", len(images)) or 0),
        "failed": int(result.get("failed", 0) or 0),
        "duration_seconds": result.get("duration_seconds"),
        "failures": result.get("failures", []),
        "progress": [str(line) for line in job.get("progress", [])][-80:],
        "error": clean_multiline(job.get("error"))[:1000],
        "batches": result.get("batches", []),
        "current_batch": result.get("current_batch"),
        "images": images,
    }


def write_generation_manifest(output_dir: Path, manifest: dict[str, Any]) -> Path:
    output_dir = output_dir.expanduser().resolve()
    job_id = validate_job_id(manifest.get("job_id"))
    metadata_dir = output_dir / ".klong" / "jobs"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    target = metadata_dir / f"{job_id}.json"
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def record_generation_manifest(
    output_dir: Path,
    job: dict[str, Any],
    payload: dict[str, Any],
    result: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    manifest = build_generation_manifest(output_dir, job, payload, result)
    return manifest, write_generation_manifest(output_dir, manifest)
