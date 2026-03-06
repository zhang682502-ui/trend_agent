from __future__ import annotations

import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
REPORT_DIR = BASE_DIR / "report"
HTML_REPORT_DIR = BASE_DIR / "report_html"


def find_latest_report(prefer_markdown: bool = True) -> Path | None:
    md_candidates = sorted(REPORT_DIR.glob("trend_report_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    html_candidates = sorted(HTML_REPORT_DIR.glob("trend_report_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)

    ordered = md_candidates + html_candidates if prefer_markdown else html_candidates + md_candidates
    for path in ordered:
        if path.exists() and path.is_file():
            return path
    return None


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(h1|h2|h3|li|p|summary|div)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def load_report_text(path: Path, max_chars: int = 250_000) -> str:
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    if str(path).lower().endswith(".html"):
        content = _html_to_text(content)
    if max_chars > 0:
        content = content[:max_chars]
    return content
