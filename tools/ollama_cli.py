from __future__ import annotations

import subprocess
import re


ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


def _clean_output(text: str) -> str:
    cleaned = ANSI_RE.sub("", text or "")
    cleaned = cleaned.replace("\r", "\n")
    cleaned = "".join(ch for ch in cleaned if (ch == "\n" or ch == "\t" or ord(ch) >= 32))
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return "\n".join(lines).strip()


def run_ollama(model: str, prompt: str, timeout_s: int = 25) -> str:
    payload = f"{prompt.rstrip()}\n/bye\n"
    try:
        result = subprocess.run(
            ["ollama", "run", model],
            input=payload,
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_s)),
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Ollama CLI timed out after {timeout_s}s") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("Ollama CLI not found in PATH") from exc
    except Exception as exc:
        raise RuntimeError(f"Ollama CLI failed: {type(exc).__name__}: {exc}") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or f"ollama run failed with exit code {result.returncode}")

    return _clean_output(result.stdout)
