from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sys


BASE_DIR = Path(__file__).resolve().parent.parent
LOCK_DIR = BASE_DIR / "data" / "locks"


class RuntimeAlreadyRunning(RuntimeError):
    def __init__(self, name: str, pid: int):
        super().__init__(f"{name} already running (pid={pid})")
        self.name = name
        self.pid = pid


def _lock_path(name: str, lock_dir: Path | None = None) -> Path:
    return (lock_dir or LOCK_DIR) / f"{name}.lock"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _read_lock(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@contextmanager
def acquire_lock(name: str, lock_dir: Path | None = None):
    path = _lock_path(name, lock_dir=lock_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = _read_lock(path)
        existing_pid = int(existing.get("pid", 0) or 0)
        if _process_exists(existing_pid):
            raise RuntimeAlreadyRunning(name, existing_pid)

    payload = {
        "pid": os.getpid(),
        "started_at": _utc_now(),
        "argv": " ".join(sys.argv),
        "cwd": str(Path.cwd()),
    }
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)

    try:
        yield path
    finally:
        if not path.exists():
            return
        current = _read_lock(path)
        if int(current.get("pid", 0) or 0) == payload["pid"] and str(current.get("started_at") or "") == payload["started_at"]:
            try:
                path.unlink()
            except OSError:
                pass
