from pathlib import Path
import json


BASE_DIR = Path(__file__).resolve().parent
SECRET_PATH = BASE_DIR / "secret.json"
REQUIRED_SECRET_KEYS = (
    "telegram_bot_token",
    "discord_webhook_url",
    "gmail_app_password",
)
_SECRETS_CACHE: dict | None = None


class SecretConfigError(RuntimeError):
    pass


def load_secrets(required_keys: tuple[str, ...] = REQUIRED_SECRET_KEYS) -> dict:
    global _SECRETS_CACHE
    if _SECRETS_CACHE is None:
        if not SECRET_PATH.exists():
            raise SecretConfigError(
                f"Missing secret file: {SECRET_PATH}. Copy config/secret.example.json to config/secret.json and fill in the required values."
            )
        try:
            data = json.loads(SECRET_PATH.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise SecretConfigError(f"Malformed JSON in {SECRET_PATH}: {exc}") from exc
        except OSError as exc:
            raise SecretConfigError(f"Could not read {SECRET_PATH}: {exc}") from exc
        if not isinstance(data, dict):
            raise SecretConfigError(f"{SECRET_PATH} must contain a top-level JSON object.")
        _SECRETS_CACHE = data

    missing: list[str] = []
    for key in required_keys:
        value = _SECRETS_CACHE.get(key)
        if value is None or not str(value).strip():
            missing.append(key)
    if missing:
        raise SecretConfigError(f"config/secret.json missing required keys: {', '.join(missing)}")
    return _SECRETS_CACHE
