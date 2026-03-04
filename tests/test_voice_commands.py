from main import parse_voice_command


def test_parse_voice_command_ping():
    assert parse_voice_command("ping", None) == "/ping"


def test_parse_voice_command_ping_upper():
    assert parse_voice_command("PING", None) == "/ping"


def test_parse_voice_command_status():
    assert parse_voice_command("status", None) == "/status"


def test_parse_voice_command_highlights():
    assert parse_voice_command("highlights", None) == "/highlights"


def test_parse_voice_command_summary_alias():
    assert parse_voice_command("summary", None) == "/highlights"


def test_parse_voice_command_help():
    assert parse_voice_command("help", None) == "/help"


def test_parse_voice_command_ping_with_punctuation():
    assert parse_voice_command("ping.", None) == "/ping"


def test_parse_voice_command_please_ping_is_not_executed():
    assert parse_voice_command("please ping", None) is None


def test_parse_voice_command_longer_sentence_is_not_executed():
    assert parse_voice_command("my status is fine", None) is None


def test_parse_voice_command_slash_ping_fallback():
    assert parse_voice_command("slash ping", None) == "/ping"


def test_parse_voice_command_cmd_ping_fallback():
    assert parse_voice_command("cmd ping", None) == "/ping"


def test_parse_voice_command_duration_guard_blocks_auto_execution():
    assert parse_voice_command("ping", 4.0) is None
