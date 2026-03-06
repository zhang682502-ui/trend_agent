from __future__ import annotations

import json
import os
from unittest.mock import patch

from core.llm_controller import chat_with_context, decide_and_respond


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": self._content,
                    }
                }
            ]
        }


def _fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
    messages = (json or {}).get("messages") or []
    prompt = "\n".join(str(message.get("content") or "") for message in messages if isinstance(message, dict))
    if "Return ONLY valid JSON" in prompt:
        payload = {
            "reply": "I will summarize the latest report.",
            "plan": {
                "intent": "FULL_REPORT_SUMMARY",
                "actions": [
                    {"tool": "get_latest_report", "args": {}},
                    {"tool": "summarize_report_full", "args": {"topic_hint": None}},
                ],
                "needs_confirmation": False,
                "confirmation_prompt": None,
                "store_context": True,
                "context_id": "123",
            },
        }
        return _FakeResponse(json_module.dumps(payload))
    return _FakeResponse("Here are the top points from the current report context.")


json_module = json


def main() -> None:
    os.environ["TREND_LLM_PROVIDER"] = "openai"
    os.environ["TREND_OPENAI_API_KEY"] = "test-key"
    os.environ["TREND_OPENAI_MODEL"] = "gpt-4o-mini"

    with patch("core.llm_controller.requests.post", side_effect=_fake_post):
        reply, plan = decide_and_respond("Summarize the latest report", chat_id=123, meta={})
        print("controller_reply:", reply)
        print("controller_plan:", json.dumps(plan, ensure_ascii=False))

        context_reply = chat_with_context(
            "What are the key points?",
            context={"executive_summary": "OpenAI and Google updates dominate today's report."},
            meta={},
        )
        print("context_reply:", context_reply)


if __name__ == "__main__":
    main()
