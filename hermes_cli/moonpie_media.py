"""Validation and normalization for MoonPie image message parts."""

from __future__ import annotations

import base64
import binascii
import struct
from typing import Any


MAX_IMAGE_COUNT = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8192
MAX_IMAGE_PIXELS = 40_000_000

_ALLOWED_MEDIA_TYPES = frozenset({"image/jpeg", "image/png"})
_JPEG_START_OF_FRAME = frozenset({
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
})


def build_moonpie_message_content(text: Any, images: Any) -> str | list[dict[str, Any]]:
    """Return plain text or validated OpenAI-style multimodal content parts."""
    if not isinstance(text, str):
        raise ValueError("content must be a string")
    if images in (None, []):
        return text
    if not isinstance(images, list):
        raise ValueError("images must be an array")
    if len(images) > MAX_IMAGE_COUNT:
        raise ValueError(f"at most {MAX_IMAGE_COUNT} images are allowed")

    total_bytes = 0
    image_parts: list[dict[str, Any]] = []
    for image in images:
        if not isinstance(image, dict):
            raise ValueError("each image must be an object")

        name = image.get("name")
        media_type = image.get("media_type")
        encoded = image.get("data")
        if not isinstance(name, str) or not name or len(name) > 255:
            raise ValueError("image name is invalid")
        if "/" in name or "\\" in name:
            raise ValueError("image name must not contain a path")
        if media_type not in _ALLOWED_MEDIA_TYPES:
            raise ValueError("image media type is not supported")
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("image data is required")
        if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
            raise ValueError("image exceeds the per-image size limit")

        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image data is not valid base64") from exc
        if len(decoded) > MAX_IMAGE_BYTES:
            raise ValueError("image exceeds the per-image size limit")

        total_bytes += len(decoded)
        if total_bytes > MAX_TOTAL_IMAGE_BYTES:
            raise ValueError("images exceed the total size limit")

        width, height = _image_dimensions(decoded, media_type)
        if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
            raise ValueError("image dimensions exceed the limit")
        if width * height > MAX_IMAGE_PIXELS:
            raise ValueError("image pixel count exceeds the limit")

        canonical_data = base64.b64encode(decoded).decode("ascii")
        image_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{canonical_data}"},
        })

    prompt = text.strip() or "Analyze the attached image or images."
    return [{"type": "text", "text": prompt}, *image_parts]


def _image_dimensions(data: bytes, media_type: str) -> tuple[int, int]:
    if media_type == "image/png":
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise ValueError("image data does not match its media type")
        width, height = struct.unpack(">II", data[16:24])
    else:
        width, height = _jpeg_dimensions(data)

    if width <= 0 or height <= 0:
        raise ValueError("image dimensions are invalid")
    return width, height


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise ValueError("image data does not match its media type")

    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            raise ValueError("JPEG marker is invalid")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            break
        segment_length = int.from_bytes(data[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            raise ValueError("JPEG segment is invalid")
        if marker in _JPEG_START_OF_FRAME:
            if segment_length < 7:
                raise ValueError("JPEG dimensions are invalid")
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            return width, height
        offset += segment_length

    raise ValueError("JPEG dimensions are missing")
