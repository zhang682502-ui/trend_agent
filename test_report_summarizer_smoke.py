import json
import sys

import tools.report_summarizer as rs


def _fake_run_ollama(model: str, prompt: str, timeout_s: int = 25) -> str:
    if '"executive_summary"' in prompt:
        return json.dumps(
            {
                "executive_summary": "Executive summary smoke result.",
                "trends": ["Trend A"],
                "highlights": ["Highlight A"],
                "questions": ["Question A"],
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "memo": "Chunk memo smoke result.",
            "trends": ["Trend chunk"],
            "highlights": ["Highlight chunk"],
            "questions": ["Question chunk"],
        },
        ensure_ascii=False,
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    rs.run_ollama = _fake_run_ollama

    en_text = "This is a short English sample about macro trends and policy."
    zh_text = "这是一个中文测试样本文本，用于检查语言检测是否能够识别中文字符数量并返回正确结果。"

    print(f"lang(en): {rs.detect_language(en_text)}")
    print(f"lang(zh): {rs.detect_language(zh_text)}")

    sample_report = (
        "Title: Sample Item One\n"
        "Source: Test\n"
        "Link: https://example.com/a\n"
        "Content: This is content A.\n\n"
        "Title: Sample Item Two\n"
        "Source: Test\n"
        "Link: https://example.com/b\n"
        "Content: This is content B.\n"
    )
    result = rs.summarize_report_full(sample_report, topic_hint="smoke")
    print("summary keys:", sorted(result.keys()))
    print("executive_summary:", result.get("executive_summary"))


if __name__ == "__main__":
    main()
