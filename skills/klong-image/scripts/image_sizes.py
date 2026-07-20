from __future__ import annotations

import math
import re


MAX_IMAGE_EDGE = 3840
MAX_IMAGE_PIXELS = 8_294_400
SIZE_PATTERN = re.compile(r"^(\d+)\s*[xX\u00d7]\s*(\d+)$")


def constrain_image_dimensions(width: int, height: int) -> tuple[int, int, bool]:
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive integers")
    scale = min(
        1.0,
        MAX_IMAGE_EDGE / max(width, height),
        math.sqrt(MAX_IMAGE_PIXELS / (width * height)),
    )
    constrained_width = max(1, math.floor(width * scale))
    constrained_height = max(1, math.floor(height * scale))
    return constrained_width, constrained_height, scale < 1.0


def constrain_image_size(value: str) -> tuple[str, bool]:
    normalized = str(value or "").strip()
    if not normalized:
        return "", False
    match = SIZE_PATTERN.fullmatch(normalized)
    if not match:
        raise ValueError("--size must use WIDTHxHEIGHT, for example 1024x1024")
    width, height, limited = constrain_image_dimensions(int(match.group(1)), int(match.group(2)))
    return f"{width}x{height}", limited
