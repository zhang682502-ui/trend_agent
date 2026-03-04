from core.health import format_health_text, heartbeat_summary, record_command, record_error, record_poll_ok, record_report_trigger, record_voice, reset_health_state


def test_health_format_is_stable():
    reset_health_state(now=0)
    record_poll_ok(update_id=123456, now=10)
    record_voice(now=20)
    record_command("/report", now=30)
    record_report_trigger(now=40)
    record_error(now=50)

    text = format_health_text(now=70)

    assert "Health: OK" in text
    assert "Uptime: 00:01:10" in text
    assert "Last poll ok: 1m ago" in text
    assert "Last update_id: 123456" in text
    assert "Last voice: 50s ago" in text
    assert "Last command: /report (40s ago)" in text
    assert "Last report trigger: 30s ago" in text
    assert "Errors (1h): 1" in text


def test_heartbeat_summary_is_stable():
    reset_health_state(now=0)
    record_poll_ok(update_id=77, now=15)
    record_error(now=20)

    summary = heartbeat_summary(now=75)

    assert summary == "TG heartbeat: uptime=00:01:15 last_poll_ok=1m ago last_update_id=77 errors_1h=1"
