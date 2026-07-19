#!/usr/bin/env python3
"""Export the local Prompt Studio cache as the browser build snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_CACHE = Path.home() / ".klong-image" / "prompt-library.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "web" / "public" / "prompt-library.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_CACHE, help="Prompt Studio cache JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Browser snapshot JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(args.input.expanduser().read_text(encoding="utf-8"))
    items = payload.get("items")
    sources = payload.get("sources")
    if not isinstance(items, list) or not items:
        raise ValueError("prompt cache contains no items")
    if not isinstance(sources, list) or not sources:
        raise ValueError("prompt cache contains no sources")
    snapshot = {
        "items": items,
        "sources": sources,
        "synced_at": str(payload.get("synced_at") or ""),
    }
    output = args.output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"output": str(output.resolve()), "prompts": len(items), "sources": len(sources)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
