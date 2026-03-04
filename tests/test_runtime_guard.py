from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json

from core.runtime_guard import RuntimeAlreadyRunning, acquire_lock


def test_stale_lock_is_replaced():
    with TemporaryDirectory() as temp_dir:
        lock_dir = Path(temp_dir)
        lock_path = lock_dir / "telegram.lock"
        lock_path.write_text(
            json.dumps(
                {
                    "pid": 999999,
                    "started_at": "2000-01-01T00:00:00+00:00",
                    "argv": "old",
                    "cwd": "old",
                }
            ),
            encoding="utf-8",
        )

        with patch("core.runtime_guard.os.kill", side_effect=ProcessLookupError):
            with acquire_lock("telegram", lock_dir=lock_dir):
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                assert payload["pid"] > 0
                assert payload["argv"]


def test_active_lock_raises():
    with TemporaryDirectory() as temp_dir:
        lock_dir = Path(temp_dir)
        lock_path = lock_dir / "telegram.lock"
        lock_path.write_text(
            json.dumps(
                {
                    "pid": 1234,
                    "started_at": "2000-01-01T00:00:00+00:00",
                    "argv": "existing",
                    "cwd": "existing",
                }
            ),
            encoding="utf-8",
        )

        with patch("core.runtime_guard.os.kill", return_value=None):
            try:
                with acquire_lock("telegram", lock_dir=lock_dir):
                    raise AssertionError("should not acquire active lock")
            except RuntimeAlreadyRunning as exc:
                assert exc.pid == 1234
