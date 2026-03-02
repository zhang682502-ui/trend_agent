from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("trend_agent")

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


DEFAULT_PREFS = {
    "agent_id": "TrendAgent-Local-01",
    "timezone": "America/New_York",
    "language": "en",
    "max_items_per_section": 3,
    "dedupe_window_days": 7,
    "topic_weights": {
        "technology": 1.0,
        "politics": 1.0,
        "finance": 1.0,
        "economic": 1.0,
        "science": 1.0,
        "culture": 1.0,
        "aviation": 1.0,
    },
    "allow_sources": [],
    "deny_sources": [],
    "allow_domains": [],
    "deny_domains": [],
    "interaction": {
        "enable_query_mode": True,
        "default_query_days": 7,
    },
}


def _to_yaml_text(data: dict) -> str:
    if yaml is None:
        lines = [
            f"agent_id: {data['agent_id']}",
            f"timezone: {data['timezone']}",
            f"language: {data['language']}",
            f"max_items_per_section: {data['max_items_per_section']}",
            f"dedupe_window_days: {data['dedupe_window_days']}",
            "topic_weights:",
        ]
        for k, v in data.get("topic_weights", {}).items():
            lines.append(f"  {k}: {v}")
        lines.extend(
            [
                "allow_sources: []",
                "deny_sources: []",
                "allow_domains: []",
                "deny_domains: []",
                "interaction:",
                f"  enable_query_mode: {str(data.get('interaction', {}).get('enable_query_mode', True)).lower()}",
                f"  default_query_days: {int(data.get('interaction', {}).get('default_query_days', 7))}",
            ]
        )
        return "\n".join(lines) + "\n"
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def merge_prefs_with_defaults(raw: dict) -> dict:
    merged = dict(DEFAULT_PREFS)
    for key, value in raw.items():
        if key in {"topic_weights", "interaction"} and isinstance(value, dict):
            base = dict(merged.get(key, {}))
            base.update(value)
            merged[key] = base
        else:
            merged[key] = value
    return merged


def ensure_default_prefs(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_to_yaml_text(DEFAULT_PREFS), encoding="utf-8")
    logger.info("created default prefs: %s", path)


def load_prefs(path: Path) -> dict:
    ensure_default_prefs(path)
    text = path.read_text(encoding="utf-8")
    if yaml is None:
        logger.warning("PyYAML not installed; using defaults + minimal overrides from prefs text")
        return dict(DEFAULT_PREFS)
    try:
        raw = yaml.safe_load(text) or {}
    except Exception:
        logger.warning("failed to parse prefs yaml, fallback defaults: %s", path, exc_info=True)
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return merge_prefs_with_defaults(raw)

