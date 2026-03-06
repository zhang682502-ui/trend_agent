from __future__ import annotations

import json
import os
import re
from typing import Any

from providers.provider_factory import get_provider

CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")


def detect_language(text: str) -> str:
    forced = str(os.getenv("TREND_REPORT_SUMMARY_LANG") or os.getenv("TREND_LLM_FORCE_LANG") or "").strip().lower()
    if forced in {"zh", "en"}:
        return forced
    content = text or ""
    if not content.strip():
        return "en"
    cjk_count = len(CJK_RE.findall(content))
    return "zh" if cjk_count >= 20 else "en"


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return max(minimum, int(default))
    try:
        return max(minimum, int(raw))
    except Exception:
        return max(minimum, int(default))


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        if isinstance(value, dict):
            return value
    except Exception:
        return None
    return None


def _split_report_items(report_text: str) -> list[str]:
    lines = (report_text or "").splitlines()
    items: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("Title:") and current:
            items.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        items.append("\n".join(current).strip())
    items = [item for item in items if item]
    if not items and (report_text or "").strip():
        return [(report_text or "").strip()]
    return items


def _chunk(items: list[str], size: int) -> list[list[str]]:
    size = max(1, int(size))
    return [items[i : i + size] for i in range(0, len(items), size)]


def _to_list(value: Any, limit: int = 8) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        parts = [part.strip("-* \t") for part in value.splitlines()]
        items = [part for part in parts if part]
    else:
        items = []
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def _parse_report_item(item_text: str) -> dict[str, str]:
    fields = {"title": "", "source": "", "link": "", "content": ""}
    current_key = ""
    content_lines: list[str] = []
    for raw_line in (item_text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("Title:"):
            fields["title"] = line.split(":", 1)[1].strip()
            current_key = "title"
            continue
        if line.startswith("Source:"):
            fields["source"] = line.split(":", 1)[1].strip()
            current_key = "source"
            continue
        if line.startswith("Link:"):
            fields["link"] = line.split(":", 1)[1].strip()
            current_key = "link"
            continue
        if line.startswith("Content:"):
            body = line.split(":", 1)[1].strip()
            if body:
                content_lines.append(body)
            current_key = "content"
            continue
        if current_key == "content" and line:
            content_lines.append(line)
    fields["content"] = " ".join(content_lines).strip()
    return fields


def _looks_like_raw_report_text(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return False
    if normalized.count("Title:") >= 1 or normalized.count("Source:") >= 1 or normalized.count("Link:") >= 1:
        return True
    url_count = len(re.findall(r"https?://", normalized))
    return url_count >= 2 and len(normalized) < 800


def _fallback_chunk_summary(chunk_text: str) -> dict[str, Any]:
    items = [_parse_report_item(item) for item in _split_report_items(chunk_text)]
    titles = [item["title"] for item in items if item.get("title")]
    highlights: list[str] = []
    for item in items:
        title = item.get("title", "").strip()
        if not title:
            continue
        source = item.get("source", "").strip()
        link = item.get("link", "").strip()
        if source:
            label = f"{source}: {title}"
        else:
            label = title
        if link:
            label = f"{label} ({link})"
        highlights.append(label)
        if len(highlights) >= 4:
            break

    lang = detect_language(chunk_text)
    if titles:
        if lang == "zh":
            memo = "本批重点包括：" + "；".join(titles[:3]) + "。"
        else:
            memo = "Key items in this batch: " + "; ".join(titles[:3]) + "."
    else:
        compact = re.sub(r"\s+", " ", chunk_text).strip()
        memo = compact[:260] or ("未找到可总结内容。" if lang == "zh" else "No clear summary content found.")

    return {
        "memo": memo,
        "trends": titles[:4],
        "highlights": highlights[:4] or titles[:4],
        "questions": [],
    }


def _pick_model(text: str, fast_mode: bool = False) -> str:
    lang = detect_language(text)
    if lang == "zh":
        return str(os.getenv("TREND_LLM_ZH_MODEL", "qwen2") or "qwen2").strip() or "qwen2"
    if fast_mode:
        return str(os.getenv("TREND_TG_SUMMARY_FAST_MODEL", os.getenv("TREND_LLM_EN_MODEL", "llama3")) or "llama3").strip() or "llama3"
    return str(os.getenv("TREND_LLM_EN_MODEL", "llama3") or "llama3").strip() or "llama3"


def _chunk_memo(chunk_text: str, topic_hint: str | None, timeout_s: int, fast_mode: bool = False) -> dict[str, Any]:
    model = _pick_model(chunk_text, fast_mode=fast_mode)
    provider = get_provider("ollama", model)
    hint = str(topic_hint or "").strip() or "none"
    prompt = (
        "Summarize this report chunk.\n"
        "Return strict JSON only:\n"
        '{"memo":"...","trends":["..."],"highlights":["..."],"questions":["..."]}\n'
        "Each list max 4 items.\n"
        f"Topic hint: {hint}\n\n"
        f"Chunk:\n{chunk_text[:12000]}\n"
    )
    raw = provider.chat(prompt, timeout_s=timeout_s)
    parsed = _extract_json_object(raw) or {}
    fallback = _fallback_chunk_summary(chunk_text)
    memo = str(parsed.get("memo") or "").strip()
    if not memo or _looks_like_raw_report_text(memo):
        memo = str(fallback.get("memo") or "").strip()
    return {
        "memo": memo,
        "trends": _to_list(parsed.get("trends"), limit=4) or _to_list(fallback.get("trends"), limit=4),
        "highlights": _to_list(parsed.get("highlights"), limit=4) or _to_list(fallback.get("highlights"), limit=4),
        "questions": _to_list(parsed.get("questions"), limit=4),
    }


def _final_summary(chunk_memos: list[dict[str, Any]], topic_hint: str | None, timeout_s: int, fast_mode: bool = False) -> dict[str, Any]:
    merged = json.dumps(chunk_memos, ensure_ascii=False)[:24000]
    reason_model = _pick_model(merged, fast_mode=fast_mode) if fast_mode else (
        str(os.getenv("TREND_LLM_REASON_MODEL", "deepseek-r1:7b") or "deepseek-r1:7b").strip() or "deepseek-r1:7b"
    )
    provider = get_provider("ollama", reason_model)
    hint = str(topic_hint or "").strip() or "none"
    prompt = (
        "You are generating an executive summary from chunk memos.\n"
        "Return strict JSON only:\n"
        '{"executive_summary":"...","trends":["..."],"highlights":["..."],"questions":["..."]}\n'
        "Rules: concise, factual, max 6 items per list.\n"
        f"Topic hint: {hint}\n\n"
        f"Chunk memos:\n{merged}\n"
    )
    raw = provider.summarize(prompt, timeout_s=timeout_s)
    parsed = _extract_json_object(raw) or {}
    return {
        "executive_summary": str(parsed.get("executive_summary") or "").strip(),
        "trends": _to_list(parsed.get("trends"), limit=6),
        "highlights": _to_list(parsed.get("highlights"), limit=6),
        "questions": _to_list(parsed.get("questions"), limit=6),
    }


def summarize_report_full(
    report_text: str,
    topic_hint: str | None = None,
    chunk_size: int = 10,
    chunk_timeout_s: int = 35,
    final_timeout_s: int = 90,
    fast_mode: bool | None = None,
) -> dict[str, Any]:
    items = _split_report_items(report_text)
    if not items:
        return {
            "executive_summary": "No report content was found.",
            "trends": [],
            "highlights": [],
            "questions": [],
        }

    if fast_mode is None:
        fast_mode = _env_flag("TREND_TG_SUMMARY_FAST", True)

    if fast_mode:
        max_items = _env_int("TREND_TG_SUMMARY_MAX_ITEMS", 12, minimum=3)
        items = items[:max_items]
        chunk_size = _env_int("TREND_TG_SUMMARY_CHUNK_SIZE", max_items, minimum=2)
        chunk_timeout_s = _env_int("TREND_TG_SUMMARY_CHUNK_TIMEOUT_S", 20, minimum=5)
        final_timeout_s = _env_int("TREND_TG_SUMMARY_FINAL_TIMEOUT_S", 25, minimum=10)
    else:
        chunk_timeout_s = _env_int("TREND_TG_SUMMARY_CHUNK_TIMEOUT_S", chunk_timeout_s, minimum=5)
        final_timeout_s = _env_int("TREND_TG_SUMMARY_FINAL_TIMEOUT_S", final_timeout_s, minimum=10)

    chunks = _chunk(items, chunk_size)

    chunk_memos: list[dict[str, Any]] = []
    aggregate_trends: list[str] = []
    aggregate_highlights: list[str] = []
    aggregate_questions: list[str] = []

    for group in chunks:
        text = "\n\n".join(group)[:16000]
        try:
            memo = _chunk_memo(text, topic_hint=topic_hint, timeout_s=chunk_timeout_s, fast_mode=fast_mode)
        except Exception:
            memo = _fallback_chunk_summary(text)
        chunk_memos.append(memo)
        aggregate_trends.extend(memo.get("trends", []))
        aggregate_highlights.extend(memo.get("highlights", []))
        aggregate_questions.extend(memo.get("questions", []))

    if fast_mode and len(chunk_memos) == 1:
        memo = chunk_memos[0]
        executive_summary = str(memo.get("memo") or "").strip()
        if not executive_summary or _looks_like_raw_report_text(executive_summary):
            executive_summary = str(_fallback_chunk_summary("\n\n".join(items)).get("memo") or "").strip()
        executive_summary = executive_summary or "Summary unavailable."
        return {
            "executive_summary": executive_summary,
            "trends": _to_list(memo.get("trends"), limit=6),
            "highlights": _to_list(memo.get("highlights"), limit=6),
            "questions": _to_list(memo.get("questions"), limit=6),
        }

    try:
        final = _final_summary(chunk_memos, topic_hint=topic_hint, timeout_s=final_timeout_s, fast_mode=fast_mode)
    except Exception:
        joined_memos = " ".join(str(m.get("memo") or "") for m in chunk_memos).strip()
        final = {
            "executive_summary": joined_memos[:700] or "Summary generation failed.",
            "trends": _to_list(aggregate_trends, limit=6),
            "highlights": _to_list(aggregate_highlights, limit=6),
            "questions": _to_list(aggregate_questions, limit=6),
        }

    if not final.get("executive_summary"):
        final["executive_summary"] = " ".join(str(m.get("memo") or "") for m in chunk_memos).strip()[:700] or "Summary unavailable."
    final["trends"] = _to_list(final.get("trends"), limit=6) or _to_list(aggregate_trends, limit=6)
    final["highlights"] = _to_list(final.get("highlights"), limit=6) or _to_list(aggregate_highlights, limit=6)
    final["questions"] = _to_list(final.get("questions"), limit=6) or _to_list(aggregate_questions, limit=6)
    return final
