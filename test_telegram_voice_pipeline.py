import os

import main


def _fake_voice_message(duration: int = 6) -> dict:
    return {"voice": {"file_id": "fake-file-id", "duration": duration}}


def run_smoke() -> None:
    chat_id = 123456
    token = "fake-token"

    original_transcribe = main.transcribe_telegram_media
    try:
        os.environ["TREND_LLM_CHAT"] = "0"

        main.transcribe_telegram_media = lambda **kwargs: "status"
        command_reply = main.handle_telegram_message(chat_id, "", _fake_voice_message(), token)
        print(f"VOICE_COMMAND_REPLY: {command_reply}")

        main.transcribe_telegram_media = lambda **kwargs: "hello can we chat"
        chat_reply = main.handle_telegram_message(chat_id, "", _fake_voice_message(), token)
        print(f"VOICE_CHAT_REPLY: {chat_reply}")
    finally:
        main.transcribe_telegram_media = original_transcribe


if __name__ == "__main__":
    run_smoke()
