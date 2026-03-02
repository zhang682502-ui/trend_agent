from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("trend_agent")


def default_ops_memory(agent_id: str = "TrendAgent-Local-01") -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "agent_id": agent_id,
        "created_at": now,
        "updated_at": now,
        "totals": {
            "runs": 0,
            "successes": 0,
            "failures": 0,
            "items_new": 0,
            "items_duplicates": 0,
        },
        "streaks": {
            "success": 0,
            "failure": 0,
        },
        "last_run": {
            "id": None,
            "state": None,
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "feeds_ok": 0,
            "feeds_failed": 0,
            "items_new": 0,
            "items_duplicates": 0,
            "error": None,
        },
        "health": {
            "state": "unknown",
            "reason": "No runs recorded yet",
            "updated_at": None,
            "feed_health": {},
        },
    }


def load_ops_memory(path: Path) -> dict:
    if not path.exists():
        logger.info("ops memory missing, creating defaults: %s", path)
        return default_ops_memory()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("failed to read ops memory: %s", path, exc_info=True)
        return default_ops_memory()
    if not isinstance(data, dict):
        return default_ops_memory()

    merged = default_ops_memory(str(data.get("agent_id") or "TrendAgent-Local-01"))
    for key in ("agent_id", "created_at", "updated_at"):
        if isinstance(data.get(key), str):
            merged[key] = data[key]
    for key in ("totals", "streaks", "last_run", "health"):
        if isinstance(data.get(key), dict):
            merged[key].update(data[key])
    if not isinstance(merged.get("health", {}).get("feed_health"), dict):
        merged["health"]["feed_health"] = {}
    return merged


def save_ops_memory_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    logger.info("ops memory saved: %s", path)


def summarize_health(run_state: str, feeds_failed: int, failure_streak: int) -> tuple[str, str]:
    if run_state == "FAILED" or failure_streak >= 3:
        return "failed", "Run failed or repeated failures reached threshold"
    if feeds_failed > 0:
        return "degraded", "Run succeeded with partial feed failures"
    return "healthy", "Run succeeded without feed failures"


def update_ops_after_run(memory: dict, run: dict, feed_failures: dict[str, dict]) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    state = str(run.get("state") or "")

    totals = memory.setdefault("totals", {})
    totals["runs"] = int(totals.get("runs", 0) or 0) + 1
    if state == "SUCCESS":
        totals["successes"] = int(totals.get("successes", 0) or 0) + 1
    if state == "FAILED":
        totals["failures"] = int(totals.get("failures", 0) or 0) + 1
    totals["items_new"] = int(totals.get("items_new", 0) or 0) + int(run.get("items_new", 0) or 0)
    totals["items_duplicates"] = int(totals.get("items_duplicates", 0) or 0) + int(run.get("items_duplicates", 0) or 0)

    streaks = memory.setdefault("streaks", {})
    success_streak = int(streaks.get("success", 0) or 0)
    failure_streak = int(streaks.get("failure", 0) or 0)
    if state == "SUCCESS":
        success_streak += 1
        failure_streak = 0
    elif state == "FAILED":
        failure_streak += 1
        success_streak = 0
    streaks["success"] = success_streak
    streaks["failure"] = failure_streak

    memory["last_run"] = {
        "id": run.get("id"),
        "state": state or None,
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "duration_seconds": run.get("duration_seconds"),
        "feeds_ok": int(run.get("feeds_ok", 0) or 0),
        "feeds_failed": int(run.get("feeds_failed", 0) or 0),
        "items_new": int(run.get("items_new", 0) or 0),
        "items_duplicates": int(run.get("items_duplicates", 0) or 0),
        "error": run.get("error"),
    }

    health = memory.setdefault("health", {})
    feed_health = health.setdefault("feed_health", {})
    for feed_key, payload in (feed_failures or {}).items():
        entry = feed_health.setdefault(feed_key, {})
        entry["failures_7d"] = int(entry.get("failures_7d", 0) or 0) + int(payload.get("count", 0) or 0)
        entry["last_failed_at"] = payload.get("last_failed_at")
        entry["last_reason"] = payload.get("last_reason")

    health_state, reason = summarize_health(state, int(run.get("feeds_failed", 0) or 0), failure_streak)
    health["state"] = health_state
    health["reason"] = reason
    health["updated_at"] = now

    memory["updated_at"] = now
    if not memory.get("created_at"):
        memory["created_at"] = now
    return memory

