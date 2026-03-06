from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools.report_reader import find_latest_report


BASE_DIR = Path(__file__).resolve().parent.parent
MAIN_PATH = BASE_DIR / "main.py"


def run_pipeline_once(dev_mode: bool = False, timeout_s: int = 1800) -> dict[str, Any]:
    cmd = [sys.executable, str(MAIN_PATH)]
    if dev_mode:
        cmd.append("--dev")
    else:
        cmd.append("--once")
    timeout_s = max(60, int(timeout_s))
    started = time.perf_counter()
    env = os.environ.copy()
    env["TREND_TELEGRAM_AGENT"] = "0"
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        latest = find_latest_report()
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "elapsed_ms": elapsed_ms,
            "stdout_tail": (proc.stdout or "")[-1200:],
            "stderr_tail": (proc.stderr or "")[-1200:],
            "report_path": str(latest) if latest else None,
        }
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "returncode": None,
            "elapsed_ms": elapsed_ms,
            "stdout_tail": ((exc.stdout or "") if isinstance(exc.stdout, str) else "")[-1200:],
            "stderr_tail": ((exc.stderr or "") if isinstance(exc.stderr, str) else "")[-1200:],
            "report_path": None,
            "error": f"Pipeline timed out after {timeout_s}s",
        }
