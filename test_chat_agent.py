from __future__ import annotations

import core.llm_controller as controller
import main
import sys
import os
from pathlib import Path


OPENAI_CALLS: list[tuple[str, str, int]] = []


def fake_run_openai_chat(*, model: str, messages: list[dict[str, str]], timeout_s: int) -> str:
    system_text = messages[0]["content"]
    user_text = messages[-1]["content"]
    if "summarize report content" in system_text.lower():
        OPENAI_CALLS.append(("summary", model, timeout_s))
        return '{"executive_summary":"Cloud summary smoke.","trends":["Trend A"],"highlights":["Highlight A"],"questions":[]}'
    OPENAI_CALLS.append(("chat", model, timeout_s))
    if controller.CJK_RE.search(user_text):
        return "你好，当然可以。我们现在就随便聊聊。"
    if "thanks" in user_text.lower():
        return "You're welcome. We can chat."
    return "Hi, of course. We can chat for a bit."


def main_smoke() -> None:
    os.environ["TREND_CHAT_PROVIDER"] = "openai"
    os.environ["TREND_SUMMARY_PROVIDER"] = "openai"
    controller._run_openai_chat = fake_run_openai_chat  # type: ignore[assignment]
    main.controller_summarize_report_text = controller.summarize_report_text  # type: ignore[assignment]
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

    chat_calls = [call for call in OPENAI_CALLS if call[0] == "chat"]
    summary_calls = [call for call in OPENAI_CALLS if call[0] == "summary"]
    assert len(chat_calls) >= 3, f"expected chat cloud calls, got {OPENAI_CALLS}"
    assert summary_calls, f"summary path did not use cloud provider: {OPENAI_CALLS}"
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
