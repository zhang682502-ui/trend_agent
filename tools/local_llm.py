from __future__ import annotations

import logging
import re

from tools.ollama_cli import run_ollama

CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
logger = logging.getLogger("trend_agent")

EN_DIFFICULTY_KEYWORDS = (
    "why",
    "how",
    "analyze",
    "evaluate",
    "trade-off",
    "mechanism",
    "causal",
    "forecast",
    "what if",
    "derive",
    "prove",
    "optimize",
    "policy",
    "research",
    "paper",
    "technical",
    "architecture",
)
ZH_DIFFICULTY_KEYWORDS = (
    "为什么",
    "如何",
    "分析",
    "推演",
    "机制",
    "因果",
    "预测",
    "权衡",
    "证明",
    "优化",
    "政策",
    "研究",
    "论文",
    "技术",
    "架构",
)

HARD_THRESHOLD = 2
REASON_MODEL = "deepseek-r1:7b"
ZH_MODEL = "qwen2"
EN_MODEL = "llama3"


def configure_routing(
    *,
    hard_threshold: int = 2,
    reason_model: str = "deepseek-r1:7b",
    zh_model: str = "qwen2",
    en_model: str = "llama3",
) -> None:
    global HARD_THRESHOLD, REASON_MODEL, ZH_MODEL, EN_MODEL
    HARD_THRESHOLD = max(0, int(hard_threshold))
    REASON_MODEL = str(reason_model or "deepseek-r1:7b").strip() or "deepseek-r1:7b"
    ZH_MODEL = str(zh_model or "qwen2").strip() or "qwen2"
    EN_MODEL = str(en_model or "llama3").strip() or "llama3"


def detect_language(text: str) -> str:
    cjk_count = len(CJK_RE.findall(text or ""))
    return "zh" if cjk_count >= 20 else "en"


def score_difficulty(title: str, content: str, url: str = "") -> int:
    score = 0
    haystack = f"{title}\n{content}\n{url}"
    lowered = haystack.lower()
    if any(keyword in lowered for keyword in EN_DIFFICULTY_KEYWORDS):
        score += 1
    if any(keyword in haystack for keyword in ZH_DIFFICULTY_KEYWORDS):
        score += 1
    content_len = len(content or "")
    if content_len > 1800:
        score += 1
    if content_len > 4000:
        score += 1
    return score


def route_model(title: str, content: str, url: str = "") -> tuple[str, int, str]:
    lang = detect_language(f"{title}\n{content}")
    difficulty = score_difficulty(title=title, content=content, url=url)
    if difficulty >= HARD_THRESHOLD:
        return lang, difficulty, REASON_MODEL
    if lang == "zh":
        return lang, difficulty, ZH_MODEL
    return lang, difficulty, EN_MODEL


def pick_model(text: str) -> str:
    lang = detect_language(text)
    return ZH_MODEL if lang == "zh" else EN_MODEL


def _build_prompt(title: str, content: str, max_bullets: int) -> str:
    return (
        "You summarize a news article.\n"
        "Return bullet points only.\n"
        f"Maximum bullets: {max_bullets}.\n"
        "No preamble. No headings. No labels. No extra text.\n\n"
        f"Title: {title.strip()}\n\n"
        "Content:\n"
        f"{content.strip()}\n"
    )


def _normalize_bullets(text: str, max_bullets: int) -> str:
    bullets: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^(?:[-*]+|\d+[.)])\s*", "", line).strip()
        line = re.sub(r"^[\u2022\u2023\u25E6\u2043\u2219]+\s*", "", line).strip()
        if not line:
            continue
        bullets.append(f"- {line}")
        if len(bullets) >= max_bullets:
            break
    if not bullets:
        compact = re.sub(r"\s+", " ", text or "").strip()
        if compact:
            bullets = [f"- {compact[:320]}"]
        else:
            bullets = ["- Summary unavailable."]
    return "\n".join(bullets[:max_bullets])


def summarize_article(
    title: str,
    content: str,
    max_bullets: int = 4,
    timeout_s: int = 25,
    content_chars: int = 5000,
    bullets: int | None = None,
    url: str = "",
) -> str:
    if bullets is not None:
        max_bullets = bullets
    max_bullets = max(1, min(int(max_bullets), 8))
    timeout_s = max(1, int(timeout_s))
    content_chars = max(500, int(content_chars))
    trimmed_content = (content or "").strip()[:content_chars]
    lang, difficulty, model = route_model(title=title, content=trimmed_content, url=url)
    logger.info("LLM route: lang=%s diff=%s -> %s", lang, difficulty, model)
    prompt = _build_prompt(title=title or "(no title)", content=trimmed_content, max_bullets=max_bullets)
    raw_text = run_ollama(model=model, prompt=prompt, timeout_s=timeout_s)
    return _normalize_bullets(raw_text, max_bullets=max_bullets)
