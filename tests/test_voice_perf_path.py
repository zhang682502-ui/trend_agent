from core.voice import _should_use_short_command_fast_path


def test_short_voice_command_fast_path_enabled_for_three_seconds_or_less():
    assert _should_use_short_command_fast_path(3.0) is True
    assert _should_use_short_command_fast_path(1.2) is True


def test_short_voice_command_fast_path_disabled_for_longer_or_missing_duration():
    assert _should_use_short_command_fast_path(3.1) is False
    assert _should_use_short_command_fast_path(None) is False
