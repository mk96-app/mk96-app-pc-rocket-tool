from datetime import datetime

import pytest

from mk96_app_pc_rocket_tool.config import (
    ConfigError,
    load_config,
    parse_config,
    save_config,
    serialize_config,
)
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
)


def _full_config() -> AppConfig:
    return AppConfig(
        servers=[Server("s1", "Trabajo", active=True, poll_interval_s=15)],
        rules=[
            Rule(
                "r1",
                "s1",
                RuleType.ROOM,
                "digital-audio/tone1",
                room_id="abc",
                room_type=RoomType.DIRECT,
                label="Ana",
                repeat_mode=RepeatMode.N_TIMES,
                repeat_count=3,
                repeat_every_polls=2,
            ),
            Rule("m1", "s1", RuleType.MENTION, "bell", label="Mención", mention_all=True),
        ],
        room_names={"abc": "Ana"},
        meetings=[
            Meeting(
                "x",
                "Daily",
                datetime(2026, 10, 12, 9, 30),
                repeat=MeetingRepeat.WEEKLY,
                repeat_days=(Weekday.MONDAY, Weekday.FRIDAY),
                repeat_until=datetime(2026, 12, 31),
                remind_before_min=5,
                link="https://meet.example/x",
                notes="n",
                exceptions={datetime(2026, 10, 16, 9, 30): OccurrenceState.DELETED},
            ),
        ],
        quiet=QuietSettings(datetime(2026, 10, 13, 13, 0), True, "23:00", "07:30"),
    )


def test_parse_config_empty_or_missing_text_gives_defaults():
    assert parse_config("") == AppConfig()
    assert parse_config(None) == AppConfig()


def test_serialize_then_parse_roundtrips_every_field():
    config = _full_config()
    assert parse_config(serialize_config(config)) == config


def test_parse_config_fills_defaults_and_generates_ids():
    text = """
servers:
  - name: Trabajo
rules:
  - server_id: s1
    type: room
    room_id: abc
    sound_id: cat
meetings:
  - title: Sync
    start: "2026-08-20T10:00"
    repeat_until: 2026-12-31
"""
    config = parse_config(text)
    assert config.servers[0].id and config.servers[0].poll_interval_s == 30
    assert config.servers[0].active is False
    rule = config.rules[0]
    assert rule.id and rule.active and rule.repeat_mode is RepeatMode.ONCE and rule.label == ""
    assert config.meetings[0].repeat_until == datetime(2026, 12, 31)
    assert config.quiet == QuietSettings()


def test_parse_config_tolerates_hand_edited_typos():
    text = """
rules:
  - {server_id: s1, type: mention, sound_id: x, repeat_mode: forever}
meetings:
  - title: Sync
    start: "2026-08-20T10:00"
    repeat: weekyl
    exceptions: {"2026-08-27T10:00": deleted, "junk": deleted, "2026-09-03T10:00": other}
quiet: {schedule_start: "99:99", silenced_until: "garbage"}
"""
    config = parse_config(text)
    assert config.rules[0].repeat_mode is RepeatMode.ONCE
    assert config.meetings[0].repeat is MeetingRepeat.NONE
    assert config.meetings[0].exceptions == {datetime(2026, 8, 27, 10): OccurrenceState.DELETED}
    assert config.quiet.schedule_start == "22:00" and config.quiet.silenced_until is None


def test_parse_config_rejects_structurally_broken_files():
    with pytest.raises(ConfigError, match="no es válido"):
        parse_config("rules:\n  - {type: room}\n")


def test_serialized_config_never_contains_credentials():
    text = serialize_config(_full_config())
    assert "token" not in text.lower() and "user_id" not in text


def test_save_then_load_from_disk(tmp_path):
    path = tmp_path / "sub" / "config.yaml"
    save_config(_full_config(), path)
    assert load_config(path) == _full_config()
    assert load_config(tmp_path / "missing.yaml") == AppConfig()


def test_app_config_queries_and_removal():
    config = _full_config()
    assert config.server("s1").name == "Trabajo"  # type: ignore[union-attr]
    assert config.find_room_rule("s1", "abc").id == "r1"  # type: ignore[union-attr]
    assert config.find_room_rule("s1", "zzz") is None
    assert config.find_room_rule("s2", "abc") is None
    config.remove_server("s1")
    assert config.servers == [] and config.rules == []
