from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
CONTEXT_PATH = DATA_DIR / "tg_context.json"
PENDING_PATH = DATA_DIR / "tg_pending.json"
_LOCK = threading.Lock()


def _load_map(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_map(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def get_context(chat_id: int, path: Path = CONTEXT_PATH) -> dict[str, Any] | None:
    data = _load_map(path)
    value = data.get(str(chat_id))
    return value if isinstance(value, dict) else None


def save_context(chat_id: int, context: dict[str, Any], path: Path = CONTEXT_PATH) -> None:
    if not isinstance(context, dict):
        return
    with _LOCK:
        data = _load_map(path)
        payload = dict(context)
        payload["updated_at"] = int(time.time())
        data[str(chat_id)] = payload
        _save_map(path, data)


def clear_context(chat_id: int, path: Path = CONTEXT_PATH) -> None:
    with _LOCK:
        data = _load_map(path)
        data.pop(str(chat_id), None)
        _save_map(path, data)


def get_pending_plan(chat_id: int, path: Path = PENDING_PATH) -> dict[str, Any] | None:
    data = _load_map(path)
    value = data.get(str(chat_id))
    return value if isinstance(value, dict) else None


def save_pending_plan(chat_id: int, pending: dict[str, Any], path: Path = PENDING_PATH) -> None:
    if not isinstance(pending, dict):
        return
    with _LOCK:
        data = _load_map(path)
        payload = dict(pending)
        payload["updated_at"] = int(time.time())
        data[str(chat_id)] = payload
        _save_map(path, data)


def clear_pending_plan(chat_id: int, path: Path = PENDING_PATH) -> None:
    with _LOCK:
        data = _load_map(path)
        data.pop(str(chat_id), None)
        _save_map(path, data)
