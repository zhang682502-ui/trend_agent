from __future__ import annotations

import time
from pathlib import Path


def cleanup_logs(log_dir: Path | str = "logs", *, older_than_days: int = 7) -> int:
    base = Path(log_dir)
    if not base.exists() or not base.is_dir():
        return 0

    cutoff_ts = time.time() - max(1, int(older_than_days)) * 24 * 60 * 60
    removed = 0
    for path in base.iterdir():
        if not path.is_file() or path.suffix.lower() != ".log":
            continue
        try:
            if path.stat().st_mtime < cutoff_ts:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed
