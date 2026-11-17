"""Non-sensitive settings in config.yaml: servers, sound rules, room-name cache, meetings and
Do Not Disturb. Credentials (URL, user id, token) live in secret_store, keyed by server id.

The file is user-editable, so parsing is lenient: unknown enum values fall back to defaults
and malformed meeting exceptions are dropped instead of failing the whole load.
"""

import logging
import os
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from mk96_app_pc_rocket_tool.models import (
    AppConfig,
    Meeting,
    MeetingRepeat,
    OccurrenceState,
    QuietSettings,
    RepeatMode,
    RoomType,
    Rule,
    RuleType,
    Server,
    Weekday,
    new_id,
)
from mk96_app_pc_rocket_tool.quiet_hours import INDEFINITE, parse_hhmm

logger = logging.getLogger(__name__)

DATETIME_FORMAT = "%Y-%m-%dT%H:%M"
DATE_FORMAT = "%Y-%m-%d"


class ConfigError(Exception):
    """config.yaml exists but cannot be read as a configuration."""


def _enum[E: StrEnum](enum_type: type[E], value: Any, default: E) -> E:
    try:
        return enum_type(value)
    except ValueError:
        return default


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(second=0, microsecond=0)
    return datetime.strptime(str(value), DATETIME_FORMAT)


def _parse_date(value: Any) -> datetime | None:
    """PyYAML turns '2026-12-31' into a date object; accept both forms."""
    if value is None:
        return None
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return datetime.strptime(str(value), DATE_FORMAT)


def _parse_exceptions(raw: Any) -> dict[datetime, OccurrenceState]:
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, state in raw.items():
        try:
            result[_parse_datetime(key)] = OccurrenceState(state)
        except ValueError:
            logger.warning("Dropping malformed meeting exception %r: %r", key, state)
    return result


def parse_server(raw: dict[str, Any]) -> Server:
    return Server(
        id=raw.get("id") or new_id(),
        name=str(raw["name"]),
        active=bool(raw.get("active", False)),
        poll_interval_s=int(raw.get("poll_interval_s", 30)),
    )


def parse_rule(raw: dict[str, Any]) -> Rule:
    room_type = raw.get("room_type")
    return Rule(
        id=raw.get("id") or new_id(),
        server_id=str(raw["server_id"]),
        type=RuleType(raw["type"]),
        sound_id=str(raw["sound_id"]),
        room_id=raw.get("room_id"),
        room_type=_enum(RoomType, room_type, RoomType.CHANNEL) if room_type else None,
        label=str(raw.get("label") or ""),
        active=bool(raw.get("active", True)),
        mention_all=bool(raw.get("mention_all", False)),
        repeat_mode=_enum(RepeatMode, raw.get("repeat_mode"), RepeatMode.ONCE),
        repeat_count=raw.get("repeat_count"),
        repeat_every_polls=int(raw.get("repeat_every_polls") or 1),
    )


def parse_meeting(raw: dict[str, Any]) -> Meeting:
    return Meeting(
        id=raw.get("id") or new_id(),
        title=str(raw["title"]),
        start=_parse_datetime(raw["start"]),
        duration_min=int(raw.get("duration_min", 30)),
        repeat=_enum(MeetingRepeat, raw.get("repeat"), MeetingRepeat.NONE),
        repeat_days=tuple(Weekday(int(day)) for day in raw.get("repeat_days") or ()),
        repeat_until=_parse_date(raw.get("repeat_until")),
        remind_before_min=int(raw.get("remind_before_min", 0)),
        link=raw.get("link") or None,
        notes=str(raw.get("notes") or ""),
        sound_id=raw.get("sound_id") or None,
        active=bool(raw.get("active", True)),
        exceptions=_parse_exceptions(raw.get("exceptions")),
    )


def parse_quiet(raw: dict[str, Any] | None) -> QuietSettings:
    raw = raw or {}
    defaults = QuietSettings()
    until = raw.get("silenced_until")
    if until != INDEFINITE and until is not None:
        try:
            until = (
                _parse_datetime(until)
                if isinstance(until, datetime)
                else datetime.fromisoformat(str(until))
            )
        except ValueError:
            until = None
    start, end = str(raw.get("schedule_start")), str(raw.get("schedule_end"))
    return QuietSettings(
        silenced_until=until,
        schedule_enabled=bool(raw.get("schedule_enabled", False)),
        schedule_start=start if parse_hhmm(start) else defaults.schedule_start,
        schedule_end=end if parse_hhmm(end) else defaults.schedule_end,
    )


def parse_config(text: str | None) -> AppConfig:
    try:
        data = yaml.safe_load(text) if text else None
        data = data or {}
        return AppConfig(
            servers=[parse_server(s) for s in data.get("servers") or []],
            rules=[parse_rule(r) for r in data.get("rules") or []],
            room_names={str(k): str(v) for k, v in (data.get("room_names") or {}).items()},
            meetings=[parse_meeting(m) for m in data.get("meetings") or []],
            quiet=parse_quiet(data.get("quiet")),
        )
    except (yaml.YAMLError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ConfigError(f"config.yaml no es válido: {exc}") from exc


def _dump_meeting(meeting: Meeting) -> dict[str, Any]:
    return {
        "id": meeting.id,
        "title": meeting.title,
        "start": meeting.start.strftime(DATETIME_FORMAT),
        "duration_min": meeting.duration_min,
        "repeat": meeting.repeat.value,
        "repeat_days": [int(day) for day in meeting.repeat_days],
        "repeat_until": meeting.repeat_until.strftime(DATE_FORMAT)
        if meeting.repeat_until
        else None,
        "remind_before_min": meeting.remind_before_min,
        "link": meeting.link,
        "notes": meeting.notes,
        "sound_id": meeting.sound_id,
        "active": meeting.active,
        "exceptions": {k.strftime(DATETIME_FORMAT): v.value for k, v in meeting.exceptions.items()},
    }


def _dump_quiet(quiet: QuietSettings) -> dict[str, Any]:
    until = quiet.silenced_until
    return {
        "silenced_until": until.isoformat(timespec="seconds")
        if isinstance(until, datetime)
        else until,
        "schedule_enabled": quiet.schedule_enabled,
        "schedule_start": quiet.schedule_start,
        "schedule_end": quiet.schedule_end,
    }


def serialize_config(config: AppConfig) -> str:
    data = {
        "servers": [
            {"id": s.id, "name": s.name, "active": s.active, "poll_interval_s": s.poll_interval_s}
            for s in config.servers
        ],
        "rules": [
            {
                "id": r.id,
                "server_id": r.server_id,
                "type": r.type.value,
                "sound_id": r.sound_id,
                "room_id": r.room_id,
                "room_type": r.room_type.value if r.room_type else None,
                "label": r.label,
                "active": r.active,
                "mention_all": r.mention_all,
                "repeat_mode": r.repeat_mode.value,
                "repeat_count": r.repeat_count,
                "repeat_every_polls": r.repeat_every_polls,
            }
            for r in config.rules
        ],
        "room_names": dict(config.room_names),
        "meetings": [_dump_meeting(m) for m in config.meetings],
        "quiet": _dump_quiet(config.quiet),
    }
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    return parse_config(path.read_text(encoding="utf-8"))


def save_config(config: AppConfig, path: Path) -> None:
    """Writes to a temporary file and swaps it in, so a crash mid-write never leaves a
    truncated config.yaml behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".yaml.tmp")
    temporary.write_text(serialize_config(config), encoding="utf-8")
    os.replace(temporary, path)
