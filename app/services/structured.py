"""Structured content input: turn a CMS-style list of blocks into the plain text the checks read.

Each block keeps an id, so a finding can be traced back to the block it came from (best effort: the mapping is
only trusted while the draft still has one paragraph per block).
"""
from __future__ import annotations

MAX_BLOCKS = 500


class StructuredError(ValueError):
    pass


def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def flatten(blocks: list[dict]) -> tuple[str, list[dict]]:
    """Returns (text, block_map). block_map lists only the blocks that produced text, in order:
    [{"id", "type", "start", "end"}]. Blocks that point at an uploaded image produce no text (the image is
    checked as an asset), but still count towards the block ids returned in `asset_blocks` by the caller."""
    if not blocks:
        raise StructuredError("blocks: add at least one block.")
    if len(blocks) > MAX_BLOCKS:
        raise StructuredError(f"blocks: at most {MAX_BLOCKS} blocks are allowed.")
    parts: list[tuple[str, str, str]] = []
    for i, b in enumerate(blocks):
        bid = _s(b.get("id")) or f"b{i + 1}"
        t = b.get("type")
        if t == "heading":
            text = _s(b.get("text"))
            level = b.get("level") if isinstance(b.get("level"), int) and 1 <= b.get("level") <= 6 else 2
            line = f"{'#' * level} {text}" if text else ""
        elif t == "paragraph":
            line = _s(b.get("text"))
        elif t == "quote":
            text = _s(b.get("text"))
            line = f"> {text}" if text else ""
        elif t == "list":
            items = [_s(x) for x in (b.get("items") or []) if _s(x)]
            line = "\n".join(f"- {x}" for x in items)
        elif t in ("link", "button"):
            label, url = _s(b.get("label")) or _s(b.get("text")), _s(b.get("url"))
            line = f"[{label}]({url})" if label and url else label
        elif t == "image":
            if b.get("asset_index") is not None:
                line = ""  # checked as an uploaded asset
            else:
                url = _s(b.get("url"))
                if not url:
                    raise StructuredError(f"blocks[{i}]: an image block needs a url or an asset_index.")
                line = f"![{_s(b.get('alt'))}]({url})"
        else:
            raise StructuredError(f"blocks[{i}].type: unknown block type {t!r}.")
        if line:
            parts.append((bid, t, line))
    if not parts:
        raise StructuredError("blocks: the blocks contain no text to review.")
    text, block_map, pos = "", [], 0
    for n, (bid, t, line) in enumerate(parts):
        if n:
            text += "\n\n"
            pos += 2
        block_map.append({"id": bid, "type": t, "start": pos, "end": pos + len(line)})
        text += line
        pos += len(line)
    return text, block_map


def locate_block(content: str, block_ids: list[str], offset: int) -> str | None:
    """Which block a character offset falls in, or None when the draft no longer lines up with the blocks."""
    segments = content.split("\n\n")
    if len(segments) != len(block_ids):
        return None
    pos = 0
    for bid, seg in zip(block_ids, segments):
        if pos <= offset <= pos + len(seg):
            return bid
        pos += len(seg) + 2
    return None
