from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import importlib.metadata
import importlib.util
import json
import logging
import math
import os
import subprocess
import sys
import tempfile
import wave

from config.config_loader import ConfigError, load_config, save_config


logger = logging.getLogger(__name__)

DEFAULT_VOICE_SETTINGS = {
    "device": "cpu",
    "compute_type": "int8",
    "beam_size": 5,
    "batch_size": 4,
    "num_workers": 1,
}

CUDA_COMPUTE_PREFERENCE = ("float16", "int8_float16", "int8")
PROBE_TIMEOUT_SECONDS = 90
BENCHMARK_TIMEOUT_FLOOR_SECONDS = 120

SUBPROCESS_TRANSCRIBE_SCRIPT = """
import json
import sys
import time

payload = {
    "success": False,
    "error": "unknown error",
}

try:
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    sample_wav = sys.argv[1]
    model_size = sys.argv[2]
    device = sys.argv[3]
    compute_type = sys.argv[4]
    beam_size = int(sys.argv[5])
    batch_size = int(sys.argv[6])
    num_workers = int(sys.argv[7])

    started = time.perf_counter()
    model = WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
        num_workers=num_workers,
    )
    pipeline = BatchedInferencePipeline(model=model)
    segments, info = pipeline.transcribe(
        sample_wav,
        beam_size=beam_size,
        batch_size=batch_size,
        vad_filter=True,
        without_timestamps=True,
        condition_on_previous_text=False,
    )
    text = " ".join(segment.text.strip() for segment in segments if getattr(segment, "text", "").strip()).strip()
    payload = {
        "success": True,
        "text": text,
        "language": getattr(info, "language", None),
        "duration_seconds": float(getattr(info, "duration", 0.0) or 0.0),
        "wall_time_seconds": time.perf_counter() - started,
    }
except Exception as exc:
    payload = {
        "success": False,
        "error": str(exc),
    }

print(json.dumps(payload, ensure_ascii=True))
"""


class VoiceTuningError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _write_probe_wav(path: Path, duration_seconds: float = 0.8, sample_rate: int = 16000) -> None:
    frame_count = max(1, int(duration_seconds * sample_rate))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        silence = (b"\x00\x00") * frame_count
        handle.writeframes(silence)


def _wav_duration_seconds(path: str | Path) -> float:
    with wave.open(str(path), "rb") as handle:
        frame_rate = handle.getframerate()
        frames = handle.getnframes()
    if frame_rate <= 0:
        return 0.0
    return frames / float(frame_rate)


def _normalize_transcript(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _torch_info() -> dict:
    if importlib.util.find_spec("torch") is None:
        return {"installed": False}

    try:
        import torch
    except Exception as exc:
        return {
            "installed": True,
            "import_error": str(exc),
        }

    info = {
        "installed": True,
        "version": getattr(torch, "__version__", None),
    }
    try:
        info["cuda_available"] = bool(torch.cuda.is_available())
        info["cuda_device_count"] = int(torch.cuda.device_count()) if info["cuda_available"] else 0
        if info["cuda_available"]:
            info["cuda_devices"] = [torch.cuda.get_device_name(index) for index in range(info["cuda_device_count"])]
    except Exception as exc:
        info["cuda_error"] = str(exc)
    return info


def _subprocess_timeout_seconds(sample_wav: str, floor_seconds: int = BENCHMARK_TIMEOUT_FLOOR_SECONDS) -> int:
    duration = _wav_duration_seconds(sample_wav)
    scaled = int(duration * 25) + 30
    return max(floor_seconds, scaled)


def _run_subprocess_candidate(
    sample_wav: str,
    model_size: str,
    candidate: dict,
    timeout_seconds: int,
) -> dict:
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                SUBPROCESS_TRANSCRIBE_SCRIPT,
                sample_wav,
                model_size,
                str(candidate.get("device", "cpu")),
                str(candidate.get("compute_type", "int8")),
                str(int(candidate.get("beam_size", 1) or 1)),
                str(int(candidate.get("batch_size", 1) or 1)),
                str(int(candidate.get("num_workers", 1) or 1)),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"timed out after {timeout_seconds}s",
        }

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if stdout:
        last_line = stdout.splitlines()[-1]
        try:
            payload = json.loads(last_line)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    if stderr:
        return {
            "success": False,
            "error": stderr.splitlines()[-1],
        }
    return {
        "success": False,
        "error": f"subprocess failed with exit code {completed.returncode}",
    }


def detect_devices() -> dict:
    report = {
        "detected_at": _utc_now(),
        "ctranslate2_version": _distribution_version("ctranslate2"),
        "faster_whisper_version": _distribution_version("faster-whisper"),
        "torch": _torch_info(),
        "cuda_detected": False,
        "cuda_device_count": 0,
        "cuda_usable": False,
        "cuda_usable_compute_types": [],
        "cuda_probe_results": [],
        "cpu_usable": False,
        "cpu_probe_results": [],
    }

    try:
        import ctranslate2
    except Exception as exc:
        report["ctranslate2_error"] = str(exc)
        return report

    try:
        cuda_count = int(ctranslate2.get_cuda_device_count())
    except Exception as exc:
        report["cuda_probe_error"] = str(exc)
        cuda_count = 0

    report["cuda_device_count"] = cuda_count
    report["cuda_detected"] = cuda_count > 0

    with tempfile.TemporaryDirectory(prefix="voice-probe-") as temp_dir:
        probe_wav = Path(temp_dir) / "probe.wav"
        _write_probe_wav(probe_wav)

        cpu_probe = _run_subprocess_candidate(
            sample_wav=str(probe_wav),
            model_size="tiny",
            candidate={
                "device": "cpu",
                "compute_type": "int8",
                "beam_size": 1,
                "batch_size": 1,
                "num_workers": 1,
            },
            timeout_seconds=PROBE_TIMEOUT_SECONDS,
        )
        cpu_probe["device"] = "cpu"
        cpu_probe["compute_type"] = "int8"
        cpu_probe["usable"] = bool(cpu_probe.get("success"))
        report["cpu_probe_results"].append(cpu_probe)
        report["cpu_usable"] = bool(cpu_probe.get("usable"))

        if report["cuda_detected"]:
            for compute_type in CUDA_COMPUTE_PREFERENCE:
                result = _run_subprocess_candidate(
                    sample_wav=str(probe_wav),
                    model_size="tiny",
                    candidate={
                        "device": "cuda",
                        "compute_type": compute_type,
                        "beam_size": 1,
                        "batch_size": 1,
                        "num_workers": 1,
                    },
                    timeout_seconds=PROBE_TIMEOUT_SECONDS,
                )
                result["device"] = "cuda"
                result["compute_type"] = compute_type
                result["usable"] = bool(result.get("success"))
                report["cuda_probe_results"].append(result)
                if result.get("usable"):
                    report["cuda_usable_compute_types"].append(compute_type)

    report["cuda_usable"] = bool(report["cuda_usable_compute_types"])
    return report


def _cpu_worker_candidates() -> list[int]:
    cpu_count = max(1, int(os.cpu_count() or 1))
    candidates = [1]
    if cpu_count >= 4:
        candidates.append(min(4, cpu_count))
    elif cpu_count >= 2:
        candidates.append(2)
    return sorted(set(candidates))


def build_candidate_settings(detect_report: dict) -> list[dict]:
    candidates: list[dict] = []

    cpu_workers = _cpu_worker_candidates()
    tuned_cpu_workers = cpu_workers[-1]
    candidates.append(
        {
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": 5,
            "batch_size": 1,
            "num_workers": 1,
        }
    )
    candidates.append(
        {
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": 5,
            "batch_size": 4,
            "num_workers": tuned_cpu_workers,
        }
    )
    candidates.append(
        {
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": 1,
            "batch_size": 4,
            "num_workers": tuned_cpu_workers,
        }
    )

    if detect_report.get("cuda_usable"):
        usable_compute_types = list(detect_report.get("cuda_usable_compute_types") or [])
        if usable_compute_types:
            preferred_compute_type = usable_compute_types[0]
            candidates.append(
                {
                    "device": "cuda",
                    "compute_type": preferred_compute_type,
                    "beam_size": 5,
                    "batch_size": 8,
                    "num_workers": 1,
                }
            )
            candidates.append(
                {
                    "device": "cuda",
                    "compute_type": preferred_compute_type,
                    "beam_size": 5,
                    "batch_size": 16,
                    "num_workers": 1,
                }
            )
            candidates.append(
                {
                    "device": "cuda",
                    "compute_type": preferred_compute_type,
                    "beam_size": 1,
                    "batch_size": 8,
                    "num_workers": 1,
                }
            )
        if len(usable_compute_types) > 1:
            candidates.append(
                {
                    "device": "cuda",
                    "compute_type": usable_compute_types[1],
                    "beam_size": 5,
                    "batch_size": 8,
                    "num_workers": 1,
                }
            )

    deduped: list[dict] = []
    seen: set[tuple] = set()
    for candidate in candidates:
        key = (
            candidate["device"],
            candidate["compute_type"],
            int(candidate["beam_size"]),
            int(candidate["batch_size"]),
            int(candidate["num_workers"]),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    if not deduped:
        deduped.append(dict(DEFAULT_VOICE_SETTINGS))
    return deduped


def _run_candidate(sample_wav: str, model_size: str, candidate: dict) -> dict:
    started = datetime.now(timezone.utc)
    timeout_seconds = _subprocess_timeout_seconds(sample_wav)
    payload = _run_subprocess_candidate(sample_wav, model_size, candidate, timeout_seconds=timeout_seconds)

    if not payload.get("success"):
        raise VoiceTuningError(str(payload.get("error") or "unknown transcription failure"))

    text = str(payload.get("text") or "").strip()
    duration_seconds = float(payload.get("duration_seconds") or 0.0) or _wav_duration_seconds(sample_wav)
    wall_time = float(payload.get("wall_time_seconds") or 0.0)
    return {
        "started_at": started.isoformat(timespec="seconds"),
        "device": str(candidate["device"]),
        "compute_type": str(candidate["compute_type"]),
        "beam_size": int(candidate.get("beam_size", 5) or 5),
        "batch_size": int(candidate.get("batch_size", 1) or 1),
        "num_workers": int(candidate.get("num_workers", 1) or 1),
        "success": True,
        "text": text,
        "normalized_text": _normalize_transcript(text),
        "text_length": len(text),
        "language": payload.get("language"),
        "duration_seconds": duration_seconds,
        "wall_time_seconds": wall_time,
        "realtime_factor": (wall_time / duration_seconds) if duration_seconds > 0 else None,
    }


def _pick_best_result(results: list[dict]) -> dict | None:
    successful = [result for result in results if result.get("success")]
    if not successful:
        return None

    nonempty = [result for result in successful if result.get("normalized_text")]
    if nonempty:
        counts = Counter(result["normalized_text"] for result in nonempty)
        highest_count = max(counts.values())
        consensus_texts = {text for text, count in counts.items() if count == highest_count}
        shortlisted = [result for result in nonempty if result["normalized_text"] in consensus_texts]
    else:
        shortlisted = successful

    def score(result: dict) -> tuple:
        return (
            0 if result.get("normalized_text") else 1,
            0 if int(result.get("beam_size", 5) or 5) >= 5 else 1,
            float(result.get("wall_time_seconds", math.inf)),
        )

    return min(shortlisted, key=score)


def benchmark_whisper(sample_wav: str, model_size: str, candidates: list) -> dict:
    sample_path = Path(sample_wav)
    if not sample_path.exists():
        raise VoiceTuningError(f"Benchmark sample not found: {sample_wav}")

    results: list[dict] = []
    for candidate in candidates:
        try:
            result = _run_candidate(str(sample_path), model_size, candidate)
        except Exception as exc:
            result = {
                "device": str(candidate.get("device", "")),
                "compute_type": str(candidate.get("compute_type", "")),
                "beam_size": int(candidate.get("beam_size", 5) or 5),
                "batch_size": int(candidate.get("batch_size", 1) or 1),
                "num_workers": int(candidate.get("num_workers", 1) or 1),
                "success": False,
                "error": str(exc),
            }
        results.append(result)

    best = _pick_best_result(results)
    return {
        "benchmarked_at": _utc_now(),
        "sample_wav": str(sample_path),
        "model_size": model_size,
        "results": results,
        "selected": best,
    }


def _runtime_settings_from_config(config: dict, model_size: str) -> dict | None:
    runtime = config.get("telegram_voice_runtime")
    if not isinstance(runtime, dict):
        return None
    selected = runtime.get("selected")
    if not isinstance(selected, dict):
        return None
    if str(selected.get("model_size") or model_size) != model_size:
        return None
    return selected


def _persist_runtime(model_size: str, detect_report: dict, benchmark_report: dict, selected: dict, source: str) -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        logger.warning("Voice tuner could not load config for persistence: %s", exc)
        return

    selected_to_save = {
        "model_size": model_size,
        "device": str(selected["device"]),
        "compute_type": str(selected["compute_type"]),
        "beam_size": int(selected["beam_size"]),
        "batch_size": int(selected["batch_size"]),
        "num_workers": int(selected["num_workers"]),
        "language": selected.get("language"),
        "duration_seconds": selected.get("duration_seconds"),
        "wall_time_seconds": selected.get("wall_time_seconds"),
        "realtime_factor": selected.get("realtime_factor"),
        "selected_at": _utc_now(),
        "source": source,
    }
    runtime_payload = {
        "model_size": model_size,
        "selected": selected_to_save,
        "backend": {
            "detected_at": detect_report.get("detected_at"),
            "ctranslate2_version": detect_report.get("ctranslate2_version"),
            "faster_whisper_version": detect_report.get("faster_whisper_version"),
            "cuda_detected": detect_report.get("cuda_detected"),
            "cuda_device_count": detect_report.get("cuda_device_count"),
            "cuda_usable": detect_report.get("cuda_usable"),
            "cuda_usable_compute_types": detect_report.get("cuda_usable_compute_types"),
            "torch": detect_report.get("torch"),
        },
        "benchmark": {
            "benchmarked_at": benchmark_report.get("benchmarked_at"),
            "results": benchmark_report.get("results"),
        },
    }
    config["telegram_voice_runtime"] = runtime_payload

    try:
        save_config(config)
    except ConfigError as exc:
        logger.warning("Voice tuner could not persist config: %s", exc)


def transcribe_with_saved_or_benchmarked_settings(
    sample_wav: str,
    model_size: str,
    logger_override: logging.Logger | None = None,
    force_rebenchmark: bool = False,
) -> dict:
    active_logger = logger_override or logger
    config: dict = {}
    try:
        config = load_config()
    except ConfigError as exc:
        active_logger.warning("Voice tuner could not load config; continuing with defaults: %s", exc)

    saved = None if force_rebenchmark else _runtime_settings_from_config(config, model_size)
    if saved is not None:
        try:
            result = _run_candidate(sample_wav, model_size, saved)
            result["settings_source"] = "saved"
            return result
        except Exception as exc:
            active_logger.warning(
                "Saved voice runtime failed; re-benchmarking on %s/%s: %s",
                saved.get("device"),
                saved.get("compute_type"),
                exc,
            )

    detect_report = detect_devices()
    candidates = build_candidate_settings(detect_report)
    benchmark_report = benchmark_whisper(sample_wav, model_size, candidates)
    selected = benchmark_report.get("selected")
    if not isinstance(selected, dict):
        raise VoiceTuningError("No usable faster-whisper settings were found on this machine.")

    selected["model_size"] = model_size
    selected["settings_source"] = "benchmarked"
    _persist_runtime(model_size, detect_report, benchmark_report, selected, source="benchmark")
    return selected
