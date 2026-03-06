import os
import sys


REQUIRED_ENV = [
    "TREND_OPENAI_API_KEY",
    "TREND_CHAT_PROVIDER",
    "TREND_SUMMARY_PROVIDER",
    "TREND_OPENAI_CHAT_MODEL",
    "TREND_OPENAI_SUMMARY_MODEL",
]
CLOUD_PROVIDER_ALIASES = {"openai", "chatgpt"}

# Controller routing is cloud-first by default. TREND_CONTROLLER_PROVIDER is optional;
# if unset it defaults to "openai". TREND_OPENAI_CONTROLLER_MODEL is also optional and
# falls back to TREND_OPENAI_MODEL, then "gpt-4o-mini".


def check_env():
    missing = []
    invalid = []
    for key in REQUIRED_ENV:
        val = os.getenv(key)
        if not val:
            missing.append(key)

    for key in ("TREND_CHAT_PROVIDER", "TREND_SUMMARY_PROVIDER"):
        provider = _normalized_provider(os.getenv(key))
        if provider and provider not in CLOUD_PROVIDER_ALIASES:
            invalid.append(f"{key} must be 'openai' for the cloud-first architecture")

    controller_provider = _normalized_provider(os.getenv("TREND_CONTROLLER_PROVIDER"))
    if controller_provider and controller_provider not in CLOUD_PROVIDER_ALIASES:
        invalid.append("TREND_CONTROLLER_PROVIDER must be 'openai' if set")

    if missing or invalid:
        _write_line("")
        _write_line("Environment configuration error")
        if missing:
            _write_line("Missing variables:")
            for item in missing:
                _write_line(f"  - {item}")
        if invalid:
            _write_line("Invalid configuration:")
            for item in invalid:
                _write_line(f"  - {item}")
        _write_line("")
        _write_line("Fix your .env file and restart.")
        _write_line("")
        sys.exit(1)

    _write_line("Environment configuration OK")


def _normalized_provider(raw: str | None) -> str:
    if raw is None:
        return ""
    return str(raw).strip().lower()


def _write_line(text: str) -> None:
    data = f"{text}\n".encode("utf-8", errors="replace")
    try:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    except Exception:
        print(text.encode("ascii", errors="replace").decode("ascii"))
