"""Checks of a post's images before git sync commits it: width and alt text.

The width is read from the file header (PNG, JPEG, GIF, WebP, AVIF), with no
image library: Hugin never converts images, it only looks at them. A
format it cannot read is skipped, never reported. Alt text is checked on
Markdown images of the body: an empty one, or one that is only a file name,
tells the reader nothing.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from hugin import images

HEADER_BYTES = 256 * 1024  # AVIF keeps its size box near the start

_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)")
_FILE_NAME = re.compile(r"^[\w.-]+\.(avif|webp|jpe?g|png|gif|svg|heic|tiff?|bmp)$", re.I)


def image_width(path: Path) -> int | None:
    """Pixel width of a PNG, JPEG, GIF, WebP or AVIF file; None when unknown."""
    try:
        with open(path, "rb") as f:
            head = f.read(HEADER_BYTES)
    except OSError:
        return None
    try:
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return struct.unpack(">I", head[16:20])[0]
        if head[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<H", head[6:8])[0]
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return _webp_width(head)
        if head[:2] == b"\xff\xd8":
            return _jpeg_width(head)
        if head[4:8] == b"ftyp" and (b"avif" in head[8:32] or b"avis" in head[8:32]):
            return _avif_width(head)
    except (struct.error, IndexError):
        return None
    return None


def _webp_width(head: bytes) -> int | None:
    chunk = head[12:16]
    if chunk == b"VP8 ":
        return struct.unpack("<H", head[26:28])[0] & 0x3FFF
    if chunk == b"VP8L":
        bits = struct.unpack("<I", head[21:25])[0]
        return (bits & 0x3FFF) + 1
    if chunk == b"VP8X":
        return int.from_bytes(head[24:27], "little") + 1
    return None


def _jpeg_width(head: bytes) -> int | None:
    i = 2
    while i + 9 < len(head):
        if head[i] != 0xFF:
            i += 1
            continue
        marker = head[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", head[i + 2:i + 4])[0]
        # Start of frame (baseline, progressive…), not DHT/JPG/DAC
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return struct.unpack(">H", head[i + 7:i + 9])[0]
        i += 2 + length
    return None


def _avif_width(head: bytes) -> int | None:
    """Widest `ispe` (image spatial extents) box: the image, not its thumbnail."""
    widths = []
    start = 0
    while (at := head.find(b"ispe", start)) != -1:
        # box type, then version/flags (4 bytes), width, height
        widths.append(struct.unpack(">I", head[at + 8:at + 12])[0])
        start = at + 4
    return max(widths) if widths else None


def _local_file(ref: str, static_root: Path, base_url: str) -> Path | None:
    site = images.site_path(ref, base_url)
    return static_root / site.lstrip("/") if site else None


def problems(
    metadata: dict, body: str, static_root: Path, base_url: str = "", min_width: int = 0,
) -> list[str]:
    """What is wrong with the post's images, one line each."""
    found = []
    if min_width > 0:
        for ref in dict.fromkeys(images.post_refs(metadata, body)):
            path = _local_file(ref, static_root, base_url)
            width = image_width(path) if path is not None and path.is_file() else None
            if width is not None and width < min_width:
                found.append(f"{ref}: {width} px wide (at least {min_width})")
    for alt, ref in _MD_IMAGE.findall(body):
        alt = alt.strip()
        if not alt:
            found.append(f"{ref}: no alt text")
        elif _FILE_NAME.match(alt):
            found.append(f"{ref}: alt text is a file name ({alt})")
    return found
