import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from core.health import heartbeat_summary, record_error, record_poll_ok, record_voice
from tools.tg_message import split_for_telegram


class TelegramConflictError(RuntimeError):
    pass


def _telegram_api_call(token: str, method: str, data: dict | None = None, timeout: int = 30) -> dict:
    encoded = None
    if data is not None:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        description = ""
        try:
            result = json.loads(payload)
            if isinstance(result, dict):
                description = str(result.get("description") or "").strip()
        except Exception:
            description = payload.strip()
        if exc.code == 409 and "Conflict: terminated by other getUpdates request" in description:
            raise TelegramConflictError(description) from exc
        raise RuntimeError(f"Telegram API call failed: {method} HTTP {exc.code}: {description or 'unknown error'}") from exc
    result = json.loads(payload)
    if not isinstance(result, dict) or not result.get("ok"):
        description = str(result.get("description") or "").strip() if isinstance(result, dict) else ""
        if "Conflict: terminated by other getUpdates request" in description:
            raise TelegramConflictError(description)
        raise RuntimeError(f"Telegram API call failed: {method}: {description or 'unknown error'}")
    return result


def send_telegram_message(
    token: str,
    chat_id: int,
    text: str,
    timeout: int = 30,
    max_chars: int | None = None,
    logger=None,
) -> None:
    payload = str(text or "").strip()
    if not payload:
        return
    parts = split_for_telegram(payload, max_chars=max_chars or 2800) if max_chars else [payload]
    if logger is not None:
        logger.info("TG send split parts=%d chars=%d", len(parts), len(payload))
    for part in parts:
        _telegram_api_call(
            token,
            "sendMessage",
            data={
                "chat_id": str(chat_id),
                "text": part,
            },
            timeout=timeout,
        )


def _message_text(message: dict) -> str:
    text = message.get("text")
    if isinstance(text, str):
        return text
    caption = message.get("caption")
    if isinstance(caption, str):
        return caption
    return ""


def _poll_once(token: str, message_handler, offset: int | None, logger=None, poll_timeout: int = 25) -> int | None:
    params = {
        "timeout": str(poll_timeout),
        "allowed_updates": json.dumps(["message"]),
    }
    if offset is not None:
        params["offset"] = str(offset)

    response = _telegram_api_call(
        token,
        "getUpdates",
        data=params,
        timeout=poll_timeout + 10,
    )
    updates = response.get("result", [])
    if not isinstance(updates, list):
        updates = []

    latest_update_id = None
    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            latest_update_id = update_id
            offset = update_id + 1

        message = update.get("message")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict):
            continue
        if chat.get("type") != "private":
            continue
        chat_id = chat.get("id")
        has_text = isinstance(message.get("text"), str)
        has_voice = isinstance(message.get("voice"), dict)
        has_audio = isinstance(message.get("audio"), dict)
        if not isinstance(chat_id, int) or not (has_text or has_voice or has_audio):
            continue
        if has_voice or has_audio:
            record_voice()
        text = _message_text(message)

        try:
            reply = message_handler(chat_id, text, message, token)
        except Exception:
            if logger is not None:
                logger.exception("Telegram message handler failed")
            record_error()
            reply = "Request failed."

        if isinstance(reply, str) and reply.strip():
            try:
                send_telegram_message(token, chat_id, reply.strip(), logger=logger)
            except Exception:
                if logger is not None:
                    logger.exception("Telegram sendMessage failed")
                record_error()

    record_poll_ok(update_id=latest_update_id)
    return offset


def run_telegram_forever(
    token: str,
    message_handler,
    logger=None,
    poll_timeout: int = 25,
    idle_sleep: float = 1.0,
    heartbeat_interval: float = 60.0,
) -> None:
    offset = None
    backoffs = [2.0, 5.0, 10.0, 60.0]
    backoff_index = 0
    last_heartbeat = 0.0

    while True:
        now = time.time()
        if logger is not None and now - last_heartbeat >= heartbeat_interval:
            logger.info(heartbeat_summary(now=now))
            last_heartbeat = now

        try:
            offset = _poll_once(
                token=token,
                message_handler=message_handler,
                offset=offset,
                logger=logger,
                poll_timeout=poll_timeout,
            )
            backoff_index = 0
            time.sleep(max(idle_sleep, 0.2))
        except KeyboardInterrupt:
            raise
        except TelegramConflictError as exc:
            if logger is not None:
                logger.error("Telegram polling conflict: %s", exc)
            raise
        except Exception:
            record_error()
            delay = backoffs[min(backoff_index, len(backoffs) - 1)]
            if logger is not None:
                logger.exception("Telegram polling error; retrying in %.0fs", delay)
            time.sleep(delay)
            if backoff_index < len(backoffs) - 1:
                backoff_index += 1


def start_telegram_polling(token: str, message_handler, logger=None, poll_timeout: int = 25, idle_sleep: float = 1.0) -> threading.Thread:
    thread = threading.Thread(
        target=run_telegram_forever,
        kwargs={
            "token": token,
            "message_handler": message_handler,
            "logger": logger,
            "poll_timeout": poll_timeout,
            "idle_sleep": idle_sleep,
        },
        name="telegram-poll",
        daemon=True,
    )
    thread.start()
    return thread
