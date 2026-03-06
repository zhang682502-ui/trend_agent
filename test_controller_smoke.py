import json
import sys

from core.llm_controller import decide_and_respond
from tools.tg_message import split_for_telegram, strip_redundant_closings


SAMPLES = [
    "帮我把最新 report 通读一遍给我总结",
    "把今天的 report 跑一下",
    "我们聊聊今天最重要的两条",
]


def smoke_split_behavior() -> None:
    long_exec = ("关键变化是政策预期与风险偏好同步上移。 " * 260).strip()
    summary_text = (
        "Summary:\n"
        f"{long_exec}\n\n"
        "Highlights:\n"
        "- 市场关注通胀粘性与政策路径。\n"
        "- 资金在防御和成长之间轮动。\n\n"
        "你想先聊哪一条？\n"
        "如果你愿意我可以继续展开。"
    )
    cleaned = strip_redundant_closings(summary_text)
    parts = split_for_telegram(cleaned, max_chars=2800)
    followup = "想先聊哪一条？你可以说“第1条/第2条”，或直接问你关心的点。"
    send_queue = parts + [followup]
    followup_count = sum(1 for item in send_queue if item == followup)

    print("=" * 80)
    print("SPLIT_SMOKE")
    print(f"cleaned_chars={len(cleaned)}")
    print(f"parts={len(parts)} part_sizes={[len(p) for p in parts]}")
    print(f"followup_count={followup_count}")
    assert len(parts) >= 2
    assert followup_count == 1


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    chat_id = 10001
    for text in SAMPLES:
        reply, plan = decide_and_respond(text, chat_id=chat_id, meta={})
        print("=" * 80)
        print(f"INPUT: {text}")
        print(f"REPLY: {reply}")
        print("PLAN:")
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        assert isinstance(plan, dict)
        assert isinstance(plan.get("actions"), list)
        assert plan.get("intent") in {"CHAT", "FULL_REPORT_SUMMARY", "RUN_PIPELINE", "CLARIFY"}

    smoke_split_behavior()


if __name__ == "__main__":
    main()
