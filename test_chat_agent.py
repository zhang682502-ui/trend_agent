from __future__ import annotations

import core.llm_controller as controller
import main
import sys
from pathlib import Path


CHAT_CALLS: list[tuple[str, int]] = []
SUMMARY_CALLS: list[str] = []


def fake_run_context_chat_llm(prompt: str, *, text: str, timeout_s: int) -> str:
    CHAT_CALLS.append((text, timeout_s))
    if controller.CJK_RE.search(text):
        return "你好，当然可以。我们现在就随便聊聊。"
    if "thanks" in text.lower():
        return "You're welcome. We can chat."
    return "Hi, of course. We can chat for a bit."


def fake_summarize_report_text(report_text: str, *, topic_hint: str | None = None) -> dict[str, object]:
    SUMMARY_CALLS.append(report_text)
    return {
        "executive_summary": "Local summary smoke.",
        "trends": ["Trend A"],
        "highlights": ["Highlight A"],
        "questions": [],
    }


def main_smoke() -> None:
    controller._run_context_chat_llm = fake_run_context_chat_llm  # type: ignore[assignment]
    main.controller_summarize_report_text = fake_summarize_report_text  # type: ignore[assignment]
    main.find_latest_report = lambda: Path("dummy_report.md")  # type: ignore[assignment]
    main.load_report_text = lambda _path: "Title: A\nSource: Test\nLink: https://example.com\nContent: Example."  # type: ignore[assignment]
    main._start_telegram_report_thread = lambda _chat_id: True  # type: ignore[assignment]

    zh_reply = main._handle_telegram_text(91001, "你好，我们聊聊吧", source="text")
    en_reply = main._handle_telegram_text(91002, "Hi, can we chat a bit?", source="text")
    thanks_reply = main._handle_telegram_text(91003, "thanks", source="text")
    news_reply = main._handle_telegram_text(91004, "today's news", source="text")
    status_reply = main._handle_telegram_text(91005, "/status", source="text")
    report_reply = main._handle_telegram_text(91006, "/report", source="text")

    sys.stdout.buffer.write(f"ZH: {zh_reply}\n".encode("utf-8", errors="replace"))
    sys.stdout.buffer.write(f"EN: {en_reply}\n".encode("utf-8", errors="replace"))
    sys.stdout.buffer.write(f"THANKS: {thanks_reply}\n".encode("utf-8", errors="replace"))
    sys.stdout.buffer.write(f"NEWS: {news_reply}\n".encode("utf-8", errors="replace"))
    sys.stdout.buffer.write(f"STATUS: {status_reply}\n".encode("utf-8", errors="replace"))
    sys.stdout.buffer.write(f"REPORT: {report_reply}\n".encode("utf-8", errors="replace"))

    assert len(CHAT_CALLS) >= 3, f"expected chat calls, got {CHAT_CALLS}"
    assert CHAT_CALLS[0][0] == "你好，我们聊聊吧"
    assert CHAT_CALLS[1][0] == "Hi, can we chat a bit?"
    assert CHAT_CALLS[2][0] == "thanks"
    assert SUMMARY_CALLS, "summary path did not run"
    assert status_reply == "TrendAgent alive ?"
    assert report_reply == "OK, running report."

    banned = (
        "I will continue with the current context.",
        "我会继续结合当前上下文处理。",
    )
    for value in (zh_reply, en_reply):
        for token in banned:
            if token in value:
                raise SystemExit(f"placeholder detected: {token}")


if __name__ == "__main__":
    main_smoke()
