import sys

from tools.local_llm import route_model, summarize_article


def _run_sample(title: str, content: str, timeout_s: int = 120) -> None:
    print(f"\n=== {title} ===")
    lang, diff, model = route_model(title=title, content=content, url="")
    print(f"Model: {model} (lang={lang}, diff={diff})")
    try:
        summary = summarize_article(
            title=title,
            content=content,
            max_bullets=4,
            timeout_s=timeout_s,
            content_chars=5000,
        )
        print(summary)
    except Exception as exc:
        print(f"Summary failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    en_title = "US inflation cools as energy prices decline"
    en_text = (
        "Consumer prices rose more slowly this month after a decline in fuel costs. "
        "Core inflation remained sticky in services, while food inflation eased. "
        "Analysts expect the central bank to keep rates steady and watch labor-market data."
    )

    zh_title = "\u4e2d\u56fd\u5236\u9020\u4e1aPMI\u56de\u5347\uff0c\u51fa\u53e3\u8ba2\u5355\u6539\u5584"
    zh_text = (
        "\u6700\u65b0\u6570\u636e\u663e\u793a\uff0c\u5236\u9020\u4e1aPMI\u8f83\u4e0a\u6708\u56de\u5347\u3002"
        "\u90e8\u5206\u884c\u4e1a\u51fa\u53e3\u8ba2\u5355\u6539\u5584\uff0c\u4f01\u4e1a\u8865\u5e93\u610f\u613f\u589e\u5f3a\u3002"
        "\u4e0d\u8fc7\uff0c\u5916\u90e8\u9700\u6c42\u4ecd\u6709\u4e0d\u786e\u5b9a\u6027\uff0c\u4f01\u4e1a\u5229\u6da6\u4fee\u590d\u4ecd\u9700\u65f6\u95f4\u3002"
    )

    hard_title = "Analyze the causal mechanisms and trade-offs in US inflation dynamics and forecast 12-month scenarios."
    hard_text = (hard_title + " " + "technical architecture policy research mechanism causal trade-off forecast optimize " * 80)

    print(f"EN route: {route_model(en_title, en_text, '')[2]}")
    print(f"ZH route: {route_model(zh_title, zh_text, '')[2]}")
    print(f"HARD route: {route_model(hard_title, hard_text, '')[2]}")

    _run_sample(en_title, en_text, timeout_s=120)
    _run_sample(zh_title, zh_text, timeout_s=180)
    _run_sample(hard_title, hard_text, timeout_s=180)
