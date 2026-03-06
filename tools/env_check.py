import os
import sys


REQUIRED_ENV = [
    "TREND_OPENAI_API_KEY",
    "TREND_CHAT_PROVIDER",
    "TREND_SUMMARY_PROVIDER",
    "TREND_OPENAI_CHAT_MODEL",
    "TREND_OPENAI_SUMMARY_MODEL",
]


def check_env():
    missing = []
    for key in REQUIRED_ENV:
        val = os.getenv(key)
        if not val:
            missing.append(key)

    if missing:
        _write_line("")
        _write_line("❌ Environment configuration error")
        _write_line("Missing variables:")
        for m in missing:
            _write_line(f"  - {m}")
        _write_line("")
        _write_line("Fix your .env file and restart.")
        _write_line("")
        sys.exit(1)

    _write_line("✓ Environment configuration OK")


def _write_line(text: str) -> None:
    data = f"{text}\n".encode("utf-8", errors="replace")
    try:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    except Exception:
        print(text.encode("ascii", errors="replace").decode("ascii"))
