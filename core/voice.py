from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import logging
import shutil
import subprocess
import time
import uuid

import requests


BASE_DIR = Path(__file__).resolve().parent.parent
VOICE_TMP_DIR = BASE_DIR / "data" / "tmp"
FFMPEG_REFRESH_COMMAND = "$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"


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


@lru_cache(maxsize=4)
def _load_model(model_size: str, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VoiceTranscriptionError(
            "faster-whisper is not installed in the active environment. Install requirements.txt into the venv."
        ) from exc

    return WhisperModel(model_size, device=device, compute_type=compute_type)


def _extract_media_payload(message: dict) -> tuple[str, dict]:
    voice_payload = message.get("voice")
    if isinstance(voice_payload, dict):
        return "voice", voice_payload
    audio_payload = message.get("audio")
    if isinstance(audio_payload, dict):
        return "audio", audio_payload
    raise VoiceTranscriptionError("Telegram message did not contain a voice or audio attachment.")


def transcribe_telegram_media(
    token: str,
    message: dict,
    logger: logging.Logger | None = None,
    model_size: str = "small",
    device: str = "auto",
    compute_type: str = "int8",
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

        _download_file(f"https://api.telegram.org/file/bot{token}/{file_path}", source_path, timeout=timeout)
        if logger is not None:
            logger.info("TG voice download complete path=%s", source_path)

        _convert_to_wav(source_path, wav_path)
        if logger is not None:
            logger.info("TG voice converted wav=%s", wav_path)

        model = _load_model(model_size=model_size, device=device, compute_type=compute_type)
        segments, info = model.transcribe(str(wav_path), vad_filter=True)
        parts = [segment.text.strip() for segment in segments if getattr(segment, "text", "").strip()]
        text = " ".join(parts).strip()
        if not text:
            raise VoiceTranscriptionError("No speech detected in the audio.")

        if logger is not None:
            detected_duration = getattr(info, "duration", None)
            detected_language = getattr(info, "language", None)
            logger.info(
                "TG voice transcribed kind=%s model=%s language=%s duration=%s elapsed=%.2fs",
                media_kind,
                model_size,
                detected_language,
                detected_duration,
                time.perf_counter() - started_at,
            )
        return text
    except requests.RequestException as exc:
        raise VoiceTranscriptionError(f"Telegram download failed: {exc}") from exc
    except subprocess.SubprocessError as exc:
        raise VoiceTranscriptionError(f"Audio conversion failed: {exc}") from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
