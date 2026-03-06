from __future__ import annotations

import core.llm_controller as controller
import main
import sys


def fake_run_context_chat_llm(prompt: str, *, text: str, timeout_s: int) -> str:
    if controller.CJK_RE.search(text):
        return "你好，当然可以。我们现在就随便聊聊。"
    return "Hi, of course. We can chat for a bit."


def main_smoke() -> None:
    controller._run_context_chat_llm = fake_run_context_chat_llm  # type: ignore[assignment]

    zh_reply = main._handle_telegram_text(91001, "你好，我们聊聊吧", source="text")
    en_reply = main._handle_telegram_text(91002, "Hi, can we chat a bit?", source="text")

    sys.stdout.buffer.write(f"ZH: {zh_reply}\n".encode("utf-8", errors="replace"))
    sys.stdout.buffer.write(f"EN: {en_reply}\n".encode("utf-8", errors="replace"))

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
