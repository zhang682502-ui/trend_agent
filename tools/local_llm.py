from __future__ import annotations

import re

from tools.ollama_cli import run_ollama

CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")


def detect_language(text: str) -> str:
    cjk_count = len(CJK_RE.findall(text or ""))
    return "zh" if cjk_count >= 20 else "en"


def pick_model(text: str) -> str:
    return "qwen2" if detect_language(text) == "zh" else "llama3"


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
) -> str:
    if bullets is not None:
        max_bullets = bullets
    max_bullets = max(1, min(int(max_bullets), 8))
    timeout_s = max(1, int(timeout_s))
    content_chars = max(500, int(content_chars))
    trimmed_content = (content or "").strip()[:content_chars]
    model = pick_model(f"{title}\n{trimmed_content[:1200]}")
    prompt = _build_prompt(title=title or "(no title)", content=trimmed_content, max_bullets=max_bullets)
    raw_text = run_ollama(model=model, prompt=prompt, timeout_s=timeout_s)
    return _normalize_bullets(raw_text, max_bullets=max_bullets)
