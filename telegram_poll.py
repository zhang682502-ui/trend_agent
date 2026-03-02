import json
import threading
import time
import urllib.parse
import urllib.request


def _telegram_api_call(token: str, method: str, data: dict | None = None, timeout: int = 30) -> dict:
    encoded = None
    if data is not None:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    result = json.loads(payload)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"Telegram API call failed: {method}")
    return result


def send_telegram_message(token: str, chat_id: int, text: str, timeout: int = 30) -> None:
    _telegram_api_call(
        token,
        "sendMessage",
        data={
            "chat_id": str(chat_id),
            "text": text,
        },
        timeout=timeout,
    )


def _poll_loop(token: str, message_handler, logger=None, poll_timeout: int = 25, idle_sleep: float = 1.0) -> None:
    offset = None
    while True:
        try:
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

            for update in updates:
                if not isinstance(update, dict):
                    continue
                update_id = update.get("update_id")
                if isinstance(update_id, int):
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
                text = message.get("text")
                if not isinstance(chat_id, int) or not isinstance(text, str):
                    continue

                try:
                    reply = message_handler(chat_id, text)
                except Exception:
                    if logger is not None:
                        logger.exception("Telegram message handler failed")
                    reply = "Request failed."

                if isinstance(reply, str) and reply.strip():
                    try:
                        send_telegram_message(token, chat_id, reply.strip())
                    except Exception:
                        if logger is not None:
                            logger.exception("Telegram sendMessage failed")
        except Exception:
            if logger is not None:
                logger.exception("Telegram polling loop error")
            time.sleep(max(idle_sleep, 1.0))
            continue

        time.sleep(max(idle_sleep, 0.2))


def start_telegram_polling(token: str, message_handler, logger=None, poll_timeout: int = 25, idle_sleep: float = 1.0) -> threading.Thread:
    thread = threading.Thread(
        target=_poll_loop,
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
