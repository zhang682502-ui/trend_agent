from __future__ import annotations

import json
from pathlib import Path


def load_cache(path: str | Path) -> dict:
    cache_path = Path(path)
    if not cache_path.exists():
        return {}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def save_cache(path: str | Path, payload: dict) -> None:
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(f"{cache_path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(cache_path)
