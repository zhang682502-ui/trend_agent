from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import warnings
import wave

import requests
from core.voice_tuner import VoiceTuningError, transcribe_with_saved_or_benchmarked_settings


os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
warnings.filterwarnings("ignore", category=UserWarning)

BASE_DIR = Path(__file__).resolve().parent.parent
VOICE_TMP_DIR = BASE_DIR / "data" / "tmp"
FFMPEG_REFRESH_COMMAND = "$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"
SHORT_COMMAND_MAX_DURATION_SECONDS = 3.0
FAST_WHISPER_MODEL_NAME = "tiny.en"
_FAST_WHISPER_MODEL = None
_FAST_WHISPER_PIPELINE = None


class VoiceTranscriptionError(RuntimeError):
    pass


def _telegram_api_call(token: str, method: str, data: dict | None = None, timeout: int = 30) -> dict:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data or {},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise VoiceTranscriptionError(f"Telegram API call failed: {method}")
    return payload


def _download_file(url: str, destination: Path, timeout: int = 60) -> None:
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if chunk:
                    handle.write(chunk)


def _convert_to_wav(source_path: Path, wav_path: Path) -> None:
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(wav_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise VoiceTranscriptionError(
            f"ffmpeg not found. Refresh PATH in this PowerShell with: {FFMPEG_REFRESH_COMMAND}"
        ) from exc

    if result.returncode == 0:
        return

    stderr = (result.stderr or "").strip()
    detail = stderr.splitlines()[-1] if stderr else "unknown ffmpeg error"
    raise VoiceTranscriptionError(f"ffmpeg conversion failed: {detail}")


def _extract_media_payload(message: dict) -> tuple[str, dict]:
    voice_payload = message.get("voice")
    if isinstance(voice_payload, dict):
        return "voice", voice_payload
    audio_payload = message.get("audio")
    if isinstance(audio_payload, dict):
        return "audio", audio_payload
    raise VoiceTranscriptionError("Telegram message did not contain a voice or audio attachment.")


def _should_use_short_command_fast_path(duration_sec: float | None) -> bool:
    return duration_sec is not None and duration_sec <= SHORT_COMMAND_MAX_DURATION_SECONDS


def _write_silent_wav(path: Path, duration_seconds: float = 0.1, sample_rate: int = 16000) -> None:
    frame_count = max(1, int(duration_seconds * sample_rate))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes((b"\x00\x00") * frame_count)


def preload_fast_voice_model(logger: logging.Logger | None = None) -> None:
    global _FAST_WHISPER_MODEL
    global _FAST_WHISPER_PIPELINE

    if _FAST_WHISPER_MODEL is not None and _FAST_WHISPER_PIPELINE is not None:
        return

    try:
        from faster_whisper import BatchedInferencePipeline, WhisperModel
    except ImportError as exc:
        raise VoiceTranscriptionError(
            "faster-whisper is not installed in the active environment. Install requirements.txt into the venv."
        ) from exc

    _FAST_WHISPER_MODEL = WhisperModel(
        FAST_WHISPER_MODEL_NAME,
        device="cpu",
        compute_type="int8",
        num_workers=1,
    )
    _FAST_WHISPER_PIPELINE = BatchedInferencePipeline(model=_FAST_WHISPER_MODEL)
    if logger is not None:
        logger.info("Voice fast-path model tiny.en preloaded")

    with tempfile.TemporaryDirectory(prefix="voice-warmup-") as temp_dir:
        warmup_wav = Path(temp_dir) / "warmup.wav"
        _write_silent_wav(warmup_wav)
        _FAST_WHISPER_PIPELINE.transcribe(
            str(warmup_wav),
            language="en",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            batch_size=1,
            vad_filter=True,
            without_timestamps=True,
        )

    if logger is not None:
        logger.info("Voice model warm-up complete")


def _fast_whisper_pipeline():
    if _FAST_WHISPER_PIPELINE is None:
        preload_fast_voice_model()
    return _FAST_WHISPER_PIPELINE


@lru_cache(maxsize=2)
def _load_short_command_model(model_name: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VoiceTranscriptionError(
            "faster-whisper is not installed in the active environment. Install requirements.txt into the venv."
        ) from exc

    return WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        num_workers=1,
    )


def _transcribe_short_command_audio(wav_path: Path) -> dict:
    try:
        pipeline = _fast_whisper_pipeline()
        segments, info = pipeline.transcribe(
            str(wav_path),
            language="en",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            batch_size=1,
            vad_filter=True,
            without_timestamps=True,
        )
        text = " ".join(segment.text.strip() for segment in segments if getattr(segment, "text", "").strip()).strip()
        return {
            "text": text,
            "language": getattr(info, "language", "en"),
            "duration_seconds": getattr(info, "duration", None),
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": 1,
            "batch_size": 1,
            "num_workers": 1,
            "settings_source": "short_command_fast_path",
            "model_name": FAST_WHISPER_MODEL_NAME,
        }
    except Exception:
        last_error: Exception | None = None
        try:
            from faster_whisper import BatchedInferencePipeline
        except ImportError as exc:
            raise VoiceTranscriptionError(
                "faster-whisper is not installed in the active environment. Install requirements.txt into the venv."
            ) from exc

        for model_name in ("tiny.en", "tiny"):
            try:
                model = _load_short_command_model(model_name)
                pipeline = BatchedInferencePipeline(model=model)
                segments, info = pipeline.transcribe(
                    str(wav_path),
                    language="en",
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    batch_size=1,
                    vad_filter=True,
                    without_timestamps=True,
                )
                text = " ".join(segment.text.strip() for segment in segments if getattr(segment, "text", "").strip()).strip()
                return {
                    "text": text,
                    "language": getattr(info, "language", "en"),
                    "duration_seconds": getattr(info, "duration", None),
                    "device": "cpu",
                    "compute_type": "int8",
                    "beam_size": 1,
                    "batch_size": 1,
                    "num_workers": 1,
                    "settings_source": "short_command_fast_path",
                    "model_name": model_name,
                }
            except Exception as exc:
                last_error = exc

        raise VoiceTranscriptionError(f"Short voice command transcription failed: {last_error}")


def transcribe_telegram_media(
    token: str,
    message: dict,
    logger: logging.Logger | None = None,
    model_size: str = "small",
    timeout: int = 60,
) -> str:
    if not token:
        raise VoiceTranscriptionError("Telegram bot token is required for voice transcription.")

    media_kind, media_payload = _extract_media_payload(message)
    file_id = str(media_payload.get("file_id") or "").strip()
    if not file_id:
        raise VoiceTranscriptionError("Telegram media payload is missing file_id.")

    temp_dir = VOICE_TMP_DIR / f"tg-{media_kind}-{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.perf_counter()
    duration_hint = int(media_payload.get("duration", 0) or 0)

    try:
        if logger is not None:
            logger.info(
                "TG voice download start kind=%s file_id=%s duration_hint=%ss",
                media_kind,
                file_id,
                duration_hint,
            )

        file_result = _telegram_api_call(token, "getFile", data={"file_id": file_id}, timeout=timeout).get("result", {})
        if not isinstance(file_result, dict):
            raise VoiceTranscriptionError("Telegram getFile returned an unexpected payload.")

        file_path = str(file_result.get("file_path") or "").strip()
        if not file_path:
            raise VoiceTranscriptionError("Telegram getFile did not return a file_path.")

        source_suffix = Path(file_path).suffix or (".ogg" if media_kind == "voice" else ".bin")
        source_path = temp_dir / f"input{source_suffix}"
        wav_path = temp_dir / "input.wav"

        download_started_at = time.perf_counter()
        _download_file(f"https://api.telegram.org/file/bot{token}/{file_path}", source_path, timeout=timeout)
        if logger is not None:
            logger.info(
                "TG voice download complete path=%s elapsed=%.2fs",
                source_path,
                time.perf_counter() - download_started_at,
            )

        convert_started_at = time.perf_counter()
        _convert_to_wav(source_path, wav_path)
        if logger is not None:
            logger.info(
                "TG voice converted wav=%s elapsed=%.2fs",
                wav_path,
                time.perf_counter() - convert_started_at,
            )

        transcribe_started_at = time.perf_counter()
        if _should_use_short_command_fast_path(float(duration_hint) if duration_hint else None):
            runtime_result = _transcribe_short_command_audio(wav_path)
        else:
            runtime_result = transcribe_with_saved_or_benchmarked_settings(
                sample_wav=str(wav_path),
                model_size=model_size,
                logger_override=logger,
            )
        text = str(runtime_result.get("text") or "").strip()
        if not text:
            raise VoiceTranscriptionError("No speech detected in the audio.")

        if logger is not None:
            detected_duration = runtime_result.get("duration_seconds")
            detected_language = runtime_result.get("language")
            logger.info(
                (
                    "TG voice transcribed kind=%s model=%s device=%s compute=%s beam=%s "
                    "batch=%s workers=%s source=%s language=%s duration=%s elapsed=%.2fs"
                ),
                media_kind,
                runtime_result.get("model_name") or model_size,
                runtime_result.get("device"),
                runtime_result.get("compute_type"),
                runtime_result.get("beam_size"),
                runtime_result.get("batch_size"),
                runtime_result.get("num_workers"),
                runtime_result.get("settings_source"),
                detected_language,
                detected_duration,
                time.perf_counter() - transcribe_started_at,
            )
            logger.info(
                "TG voice pipeline complete kind=%s total_elapsed=%.2fs",
                media_kind,
                time.perf_counter() - started_at,
            )
        return text
    except requests.RequestException as exc:
        raise VoiceTranscriptionError(f"Telegram download failed: {exc}") from exc
    except subprocess.SubprocessError as exc:
        raise VoiceTranscriptionError(f"Audio conversion failed: {exc}") from exc
    except VoiceTuningError as exc:
        raise VoiceTranscriptionError(str(exc)) from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
