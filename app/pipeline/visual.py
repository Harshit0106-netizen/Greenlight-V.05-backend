"""Image checks. Deterministic and local: pixels never leave the server and no AI is involved.

What is checked: that the file is really the image type it claims to be, its size, whether alt text is
descriptive, and whether a large share of the image is a colour outside the approved palette.
What is NOT checked: text inside images, logos, faces or whether the picture is appropriate. The UI says so.
"""
from __future__ import annotations

import re
from collections import Counter
from io import BytesIO

ALLOWED_TYPES = {"image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp", "image/gif": "gif"}
MAX_IMAGE_BYTES = 2 * 1024 * 1024  # keeps one upload inside common serverless request limits
MAX_ASSETS_PER_REVIEW = 4
VAGUE_ALT = {"image", "photo", "picture", "img", "graphic", "logo", "banner", "screenshot", "icon"}
_FILE_LIKE = re.compile(r"\.(?:png|jpe?g|gif|webp)$|^img[_-]?\d+|^dsc[_-]?\d+", re.I)
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


def alt_problem(alt: str | None) -> str | None:
    """None when the alt text is fine, otherwise a short reason."""
    a = " ".join((alt or "").split())
    if not a:
        return "This image has no alt text."
    if a.lower() in VAGUE_ALT or _FILE_LIKE.search(a):
        return "The alt text is a filename or a generic word, so it does not describe the image."
    if len(a) < 5:
        return "The alt text is too short to describe the image."
    return None


def sniff_image(data: bytes) -> dict:
    """Read format and size straight from the file header. Raises ValueError for anything else."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return {"format": "png", "width": int.from_bytes(data[16:20], "big"), "height": int.from_bytes(data[20:24], "big")}
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return {"format": "gif", "width": int.from_bytes(data[6:8], "little"), "height": int.from_bytes(data[8:10], "little")}
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
                i += 2 if marker != 0xFF else 1
                continue
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return {"format": "jpeg", "height": int.from_bytes(data[i + 5:i + 7], "big"), "width": int.from_bytes(data[i + 7:i + 9], "big")}
            i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
        raise ValueError("Could not read the JPEG dimensions.")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        kind = data[12:16]
        if kind == b"VP8 ":
            return {"format": "webp", "width": int.from_bytes(data[26:28], "little") & 0x3FFF, "height": int.from_bytes(data[28:30], "little") & 0x3FFF}
        if kind == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return {"format": "webp", "width": (bits & 0x3FFF) + 1, "height": ((bits >> 14) & 0x3FFF) + 1}
        if kind == b"VP8X":
            return {"format": "webp", "width": 1 + int.from_bytes(data[24:27], "little"), "height": 1 + int.from_bytes(data[27:30], "little")}
    raise ValueError("The file is not a PNG, JPEG, WebP or GIF image.")


# ---- palette ---------------------------------------------------------------------------------------
def approved_palette(texts: list[str]) -> set[str]:
    return {h.lower() for t in texts for h in _HEX.findall(t or "")}


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def dominant_colours(pixels, min_share: float = 0.06) -> list[tuple[str, float]]:
    """Bucket pixels to 4 bits per channel and return (hex, share) for the common buckets, biggest first.
    The same method runs in the frontend demo backend, so both sides agree."""
    counts: Counter = Counter()
    total = 0
    for r, g, b in pixels:
        counts[(r >> 4, g >> 4, b >> 4)] += 1
        total += 1
    if not total:
        return []
    out = [("#%02x%02x%02x" % (k[0] * 16 + 8, k[1] * 16 + 8, k[2] * 16 + 8), n / total) for k, n in counts.items() if n / total >= min_share]
    return sorted(out, key=lambda c: -c[1])


def palette_issue(dominant: list[tuple[str, float]], approved: set[str], tolerance: int = 70,
                  min_total_share: float = 0.15) -> dict | None:
    """Flag when chromatic colours outside the approved palette cover a large part of the image.
    Near-greys (white, black, neutral backgrounds) are ignored; so are colours close to an approved one."""
    if not approved:
        return None
    pal = [_rgb(a) for a in approved]
    off, total = [], 0.0
    for hx, share in dominant:
        r, g, b = _rgb(hx)
        if max(r, g, b) - min(r, g, b) < 30:
            continue
        if any(((r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2) ** 0.5 <= tolerance for pr, pg, pb in pal):
            continue
        off.append((hx, share))
        total += share
    if total < min_total_share:
        return None
    return {"colour": off[0][0], "share": round(total, 2), "severity": "medium" if total >= 0.35 else "low"}


def image_pixels(data: bytes, size: int = 48):
    """Pixels of a small copy of the image, or None when Pillow is not installed or cannot read the file."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(BytesIO(data)) as im:
            im = im.convert("RGB").resize((size, size))
            return list(im.getdata())
    except Exception:  # noqa: BLE001 - a corrupt image must not break the review
        return None


def analyze(data: bytes, approved: set[str]) -> dict:
    info = sniff_image(data)
    pixels = image_pixels(data) if approved else None
    info["palette_checked"] = pixels is not None
    info["palette_issue"] = palette_issue(dominant_colours(pixels), approved) if pixels else None
    return info
