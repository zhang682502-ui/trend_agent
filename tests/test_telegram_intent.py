from main import parse_telegram_intent


def test_parse_telegram_intent_keyword_status_is_command():
    kind, command, reason = parse_telegram_intent("can you share status please", source="voice", duration_sec=8.0)
    assert kind == "command"
    assert command == "/status"
    assert reason == "keyword:status"


def test_parse_telegram_intent_keyword_report_is_command():
    kind, command, reason = parse_telegram_intent("please run report now", source="voice", duration_sec=10.0)
    assert kind == "command"
    assert command == "/report"
    assert reason.startswith("keyword:")


def test_parse_telegram_intent_chat_fallback():
    kind, command, reason = parse_telegram_intent("hello can we chat", source="voice", duration_sec=6.0)
    assert kind == "chat"
    assert command is None
    assert reason == "fallback_chat"
