"""Do Not Disturb: manual temporary silence (from the tray) and a recurring quiet schedule.

Only chat alerts are silenced: no sound and no notification, but the chat still shows up in
the tray menu. Meeting reminders are never silenced.
"""

from datetime import datetime, time, timedelta
from enum import StrEnum

from mk96_app_pc_rocket_tool.models import QuietSettings

INDEFINITE = "indefinite"


class SilenceOption(StrEnum):
    ONE_HOUR = "1h"
    FOUR_HOURS = "4h"
    INDEFINITE = "indefinite"
    OFF = "off"


_DURATIONS = {
    SilenceOption.ONE_HOUR: timedelta(hours=1),
    SilenceOption.FOUR_HOURS: timedelta(hours=4),
}


def parse_hhmm(text: object) -> time | None:
    try:
        hours, minutes = str(text).split(":")
        return time(int(hours), int(minutes))
    except (ValueError, TypeError):
        return None


def in_schedule(start: time, end: time, now: time) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


def _manual_until(settings: QuietSettings, now: datetime) -> datetime | str | None:
    """None if no manual silence is running; INDEFINITE or the moment it ends."""
    value = settings.silenced_until
    if value == INDEFINITE:
        return INDEFINITE
    if isinstance(value, datetime) and value > now:
        return value
    return None


def is_quiet(settings: QuietSettings, now: datetime) -> bool:
    if _manual_until(settings, now) is not None:
        return True
    if not settings.schedule_enabled:
        return False
    start, end = parse_hhmm(settings.schedule_start), parse_hhmm(settings.schedule_end)
    return start is not None and end is not None and in_schedule(start, end, now.time())


def silence_until(now: datetime, option: SilenceOption) -> datetime | str | None:
    """New `silenced_until` value for a tray menu option."""
    if option is SilenceOption.INDEFINITE:
        return INDEFINITE
    if option in _DURATIONS:
        return (now + _DURATIONS[option]).replace(microsecond=0)
    return None


def quiet_status_text(settings: QuietSettings, now: datetime) -> str | None:
    """Text for the tray, or None when nothing is silenced right now."""
    manual = _manual_until(settings, now)
    if manual == INDEFINITE:
        return "No molestar (hasta que lo desactives)"
    if isinstance(manual, datetime):
        return f"No molestar hasta las {manual:%H:%M}"
    if is_quiet(settings, now):
        return f"Horario silencioso (hasta las {settings.schedule_end})"
    return None


def has_manual_silence(settings: QuietSettings, now: datetime) -> bool:
    return _manual_until(settings, now) is not None
