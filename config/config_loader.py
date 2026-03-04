from pathlib import Path
import json


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DEFAULT_CONFIG = {
    "telegram_stay_alive": True,
    "telegram_voice_model": "small",
    "telegram_voice_runtime": None,
    "open_browser": True,
}


class ConfigError(RuntimeError):
    pass


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise ConfigError(f"Missing config file: {CONFIG_PATH}")
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Malformed JSON in {CONFIG_PATH}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {CONFIG_PATH}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_PATH} must contain a top-level JSON object.")
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def save_config(data: dict) -> None:
    if not isinstance(data, dict):
        raise ConfigError("Config data must be a dictionary.")
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CONFIG_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(CONFIG_PATH)
    except OSError as exc:
        raise ConfigError(f"Could not write {CONFIG_PATH}: {exc}") from exc
