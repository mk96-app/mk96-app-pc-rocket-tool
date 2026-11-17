from datetime import datetime, time

import pytest

from mk96_app_pc_rocket_tool.models import QuietSettings
from mk96_app_pc_rocket_tool.quiet_hours import (
    INDEFINITE,
    SilenceOption,
    has_manual_silence,
    in_schedule,
    is_quiet,
    parse_hhmm,
    quiet_status_text,
    silence_until,
)

NOW = datetime(2026, 10, 13, 12, 0)


def test_parse_hhmm_accepts_valid_and_rejects_invalid_times():
    assert parse_hhmm("08:30") == time(8, 30)
    assert parse_hhmm("25:00") is None
    assert parse_hhmm("abc") is None
    assert parse_hhmm(None) is None


@pytest.mark.parametrize(
    ("start", "end", "now", "expected"),
    [
        (time(9), time(17), time(12), True),
        (time(9), time(17), time(17), False),
        (time(22), time(8), time(23), True),
        (time(22), time(8), time(3), True),
        (time(22), time(8), time(12), False),
        (time(8), time(8), time(8), False),
    ],
)
def test_in_schedule_handles_same_day_and_midnight_ranges(start, end, now, expected):
    assert in_schedule(start, end, now) is expected


def test_is_quiet_default_settings_are_not_quiet():
    assert is_quiet(QuietSettings(), NOW) is False


def test_is_quiet_manual_silence_only_while_it_lasts():
    assert is_quiet(QuietSettings(silenced_until=datetime(2026, 10, 13, 13)), NOW) is True
    assert is_quiet(QuietSettings(silenced_until=datetime(2026, 10, 13, 11)), NOW) is False
    assert is_quiet(QuietSettings(silenced_until=INDEFINITE), NOW) is True


def test_is_quiet_schedule_applies_only_when_enabled():
    settings = QuietSettings(schedule_start="11:00", schedule_end="13:00")
    assert is_quiet(settings, NOW) is False
    enabled = QuietSettings(schedule_enabled=True, schedule_start="11:00", schedule_end="13:00")
    assert is_quiet(enabled, NOW) is True


def test_silence_until_maps_each_menu_option():
    assert silence_until(NOW, SilenceOption.ONE_HOUR) == datetime(2026, 10, 13, 13)
    assert silence_until(NOW, SilenceOption.FOUR_HOURS) == datetime(2026, 10, 13, 16)
    assert silence_until(NOW, SilenceOption.INDEFINITE) == INDEFINITE
    assert silence_until(NOW, SilenceOption.OFF) is None


def test_quiet_status_text_describes_the_current_silence():
    assert quiet_status_text(QuietSettings(), NOW) is None
    manual = QuietSettings(silenced_until=datetime(2026, 10, 13, 13))
    assert "13:00" in (quiet_status_text(manual, NOW) or "")
    indefinite = QuietSettings(silenced_until=INDEFINITE)
    assert "desactives" in (quiet_status_text(indefinite, NOW) or "")
    scheduled = QuietSettings(None, True, "11:00", "13:00")
    assert "Horario silencioso" in (quiet_status_text(scheduled, NOW) or "")


def test_has_manual_silence_ignores_the_schedule():
    assert has_manual_silence(QuietSettings(None, True, "11:00", "13:00"), NOW) is False
    assert has_manual_silence(QuietSettings(silenced_until=INDEFINITE), NOW) is True
