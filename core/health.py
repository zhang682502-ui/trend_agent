from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time


@dataclass
class _HealthState:
    started_at: float = field(default_factory=time.time)
    last_poll_ok_at: float | None = None
    last_update_id: int | None = None
    last_voice_at: float | None = None
    last_command: str | None = None
    last_command_at: float | None = None
    last_report_trigger_at: float | None = None
    error_timestamps: deque[float] = field(default_factory=deque)


_STATE = _HealthState()
_LOCK = threading.Lock()


def reset_health_state(now: float | None = None) -> None:
    with _LOCK:
        _STATE.started_at = now if now is not None else time.time()
        _STATE.last_poll_ok_at = None
        _STATE.last_update_id = None
        _STATE.last_voice_at = None
        _STATE.last_command = None
        _STATE.last_command_at = None
        _STATE.last_report_trigger_at = None
        _STATE.error_timestamps.clear()


def _prune_errors(now: float) -> None:
    cutoff = now - 3600
    while _STATE.error_timestamps and _STATE.error_timestamps[0] < cutoff:
        _STATE.error_timestamps.popleft()


def record_poll_ok(update_id: int | None = None, now: float | None = None) -> None:
    ts = now if now is not None else time.time()
    with _LOCK:
        _STATE.last_poll_ok_at = ts
        if update_id is not None:
            _STATE.last_update_id = int(update_id)


def record_voice(now: float | None = None) -> None:
    ts = now if now is not None else time.time()
    with _LOCK:
        _STATE.last_voice_at = ts


def record_command(command: str, now: float | None = None) -> None:
    ts = now if now is not None else time.time()
    with _LOCK:
        _STATE.last_command = command
        _STATE.last_command_at = ts


def record_report_trigger(now: float | None = None) -> None:
    ts = now if now is not None else time.time()
    with _LOCK:
        _STATE.last_report_trigger_at = ts


def record_error(now: float | None = None) -> None:
    ts = now if now is not None else time.time()
    with _LOCK:
        _STATE.error_timestamps.append(ts)
        _prune_errors(ts)


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_age(ts: float | None, now: float) -> str:
    if ts is None:
        return "never"
    delta = max(0, int(now - ts))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    return f"{delta // 3600}h ago"


def health_snapshot(now: float | None = None) -> dict:
    ts = now if now is not None else time.time()
    with _LOCK:
        _prune_errors(ts)
        return {
            "started_at": _STATE.started_at,
            "uptime_seconds": max(0, int(ts - _STATE.started_at)),
            "last_poll_ok_at": _STATE.last_poll_ok_at,
            "last_update_id": _STATE.last_update_id,
            "last_voice_at": _STATE.last_voice_at,
            "last_command": _STATE.last_command,
            "last_command_at": _STATE.last_command_at,
            "last_report_trigger_at": _STATE.last_report_trigger_at,
            "error_count_last_hour": len(_STATE.error_timestamps),
        }


def format_health_text(now: float | None = None) -> str:
    ts = now if now is not None else time.time()
    snap = health_snapshot(now=ts)
    last_command = snap["last_command"]
    last_command_text = f"{last_command} ({_format_age(snap['last_command_at'], ts)})" if last_command else "never"
    return "\n".join(
        [
            "Health: OK",
            f"Uptime: {_format_duration(snap['uptime_seconds'])}",
            f"Last poll ok: {_format_age(snap['last_poll_ok_at'], ts)}",
            f"Last update_id: {snap['last_update_id'] if snap['last_update_id'] is not None else 'none'}",
            f"Last voice: {_format_age(snap['last_voice_at'], ts)}",
            f"Last command: {last_command_text}",
            f"Last report trigger: {_format_age(snap['last_report_trigger_at'], ts)}",
            f"Errors (1h): {snap['error_count_last_hour']}",
        ]
    )


def heartbeat_summary(now: float | None = None) -> str:
    ts = now if now is not None else time.time()
    snap = health_snapshot(now=ts)
    return (
        f"TG heartbeat: uptime={_format_duration(snap['uptime_seconds'])} "
        f"last_poll_ok={_format_age(snap['last_poll_ok_at'], ts)} "
        f"last_update_id={snap['last_update_id'] if snap['last_update_id'] is not None else 'none'} "
        f"errors_1h={snap['error_count_last_hour']}"
    )
