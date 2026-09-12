"""Qwen3-VL image-token arithmetic (pure Python, shared by the converters and the trainer)."""

from __future__ import annotations

import math

PATCH_SIZE = 16
MERGE_SIZE = 2
TOKEN_PIXELS = PATCH_SIZE * MERGE_SIZE  # 32 px per token side after 2x2 merge
DEFAULT_MIN_PIXELS = 65536
DEFAULT_MAX_PIXELS = 1048576


def smart_resize(height: int, width: int, *, factor: int = TOKEN_PIXELS, min_pixels: int = DEFAULT_MIN_PIXELS, max_pixels: int = DEFAULT_MAX_PIXELS) -> tuple[int, int]:
    """Mirror of the Qwen2-VL/Qwen3-VL image processor resize rule (multiples of ``factor``)."""
    if max(height, width) / min(height, width) > 200:
        raise ValueError("absolute aspect ratio must be smaller than 200")
    h_bar = max(factor, round(height / factor) * factor)
    w_bar = max(factor, round(width / factor) * factor)
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = max(factor, math.floor(height / beta / factor) * factor)
        w_bar = max(factor, math.floor(width / beta / factor) * factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = math.ceil(height * beta / factor) * factor
        w_bar = math.ceil(width * beta / factor) * factor
    return h_bar, w_bar


def image_token_count(width: int, height: int, *, min_pixels: int = DEFAULT_MIN_PIXELS, max_pixels: int = DEFAULT_MAX_PIXELS, factor: int = TOKEN_PIXELS) -> int:
    h_bar, w_bar = smart_resize(height, width, factor=factor, min_pixels=min_pixels, max_pixels=max_pixels)
    return (h_bar // factor) * (w_bar // factor)


def jpeg_size(path) -> tuple[int, int]:
    """Read JPEG/PNG dimensions from the header without Pillow."""
    import struct

    with open(path, "rb") as handle:
        head = handle.read(26)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            width, height = struct.unpack(">II", head[16:24])
            return int(width), int(height)
        if head[:2] != b"\xff\xd8":
            raise ValueError(f"unsupported image format: {path}")
        handle.seek(2)
        while True:
            marker = handle.read(2)
            if len(marker) < 2 or marker[0] != 0xFF:
                raise ValueError(f"malformed JPEG: {path}")
            if marker[1] in {0xC0, 0xC1, 0xC2}:
                handle.read(3)
                height, width = struct.unpack(">HH", handle.read(4))
                return int(width), int(height)
            (length,) = struct.unpack(">H", handle.read(2))
            handle.seek(length - 2, 1)
