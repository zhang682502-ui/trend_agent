from __future__ import annotations

import re


SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\!\?。！？])\s+")

_CLOSING_PATTERNS = [
    re.compile(r"^(如果你愿意.*|你想先聊哪一条.*|需要我继续吗.*|要我继续吗.*|还想聊哪一条.*)$"),
    re.compile(r"^(想先聊哪一条.*|你可以说.?第?\d+条.*)$"),
    re.compile(r"^(if you (want|would like).*$)", re.IGNORECASE),
    re.compile(r"^(would you like.*$)", re.IGNORECASE),
    re.compile(r"^(do you want me to continue.*$)", re.IGNORECASE),
    re.compile(r"^(shall i continue.*$)", re.IGNORECASE),
    re.compile(r"^(which point should we dig into first.*$)", re.IGNORECASE),
]


def _normalize_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _hard_cut(text: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= max_chars:
            parts.append(remaining)
            break
        cut_at = remaining.rfind(" ", 0, max_chars)
        if cut_at < max_chars // 2:
            cut_at = max_chars
        part = remaining[:cut_at].strip()
        if not part:
            part = remaining[:max_chars].strip()
            cut_at = max_chars
        parts.append(part)
        remaining = remaining[cut_at:].lstrip()
    return parts


def _pack_units(units: list[str], max_chars: int, joiner: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for unit in units:
        piece = unit.strip()
        if not piece:
            continue
        if not current:
            current = piece
            continue
        candidate = f"{current}{joiner}{piece}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def _split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    para = paragraph.strip()
    if not para:
        return []
    if len(para) <= max_chars:
        return [para]

    lines = para.split("\n")
    if len(lines) > 1 and all(len(line) <= max_chars for line in lines):
        return _pack_units(lines, max_chars, "\n")

    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(para) if s.strip()]
    if len(sentences) > 1 and all(len(sentence) <= max_chars for sentence in sentences):
        return _pack_units(sentences, max_chars, " ")

    return _hard_cut(para, max_chars)


def split_for_telegram(text: str, max_chars: int = 2800) -> list[str]:
    max_chars = max(500, int(max_chars))
    normalized = _normalize_text(text)
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    paragraphs = [part.strip() for part in re.split(r"\n{2,}", normalized) if part.strip()]
    units: list[str] = []
    for para in paragraphs:
        units.extend(_split_long_paragraph(para, max_chars=max_chars))

    if not units:
        return _hard_cut(normalized, max_chars=max_chars)
    return _pack_units(units, max_chars=max_chars, joiner="\n\n")


def _is_closing_line(line: str) -> bool:
    candidate = line.strip()
    if not candidate or len(candidate) > 180:
        return False
    return any(pattern.match(candidate) for pattern in _CLOSING_PATTERNS)


def strip_redundant_closings(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    lines = normalized.split("\n")

    removed = 0
    while lines and removed < 3:
        tail = lines[-1].strip()
        if not tail:
            lines.pop()
            continue
        if _is_closing_line(tail):
            lines.pop()
            removed += 1
            while lines and not lines[-1].strip():
                lines.pop()
            continue
        break
    return "\n".join(lines).strip()
