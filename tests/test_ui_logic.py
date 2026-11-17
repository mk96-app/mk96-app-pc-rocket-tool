from datetime import date

import pytest

from mk96_app_pc_rocket_tool.models import ConnectionState, HistoryMessage, RepeatMode, RuleType
from mk96_app_pc_rocket_tool.tray import (
    MAX_RECENT_ITEMS,
    ConnectionSummary,
    PendingMeetingAlert,
    RecentNotification,
    add_pending_meeting,
    add_to_recent,
    build_status_lines,
    remove_from_recent,
    remove_pending_meeting,
    tooltip_text,
)
from mk96_app_pc_rocket_tool.ui.alert_dialog import repeat_values, validate_alert
from mk96_app_pc_rocket_tool.ui.alerts_panel import describe_rule
from mk96_app_pc_rocket_tool.ui.chat_window import history_html, trailing_new_start
from mk96_app_pc_rocket_tool.ui.meetings_panel import CalendarMode, shift_reference
from mk96_app_pc_rocket_tool.ui.servers_panel import (
    Tone,
    delete_confirmation_text,
    missing_fields,
    probe_ok_text,
    server_status_text,
)

# -- Servers tab --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "detail", "expected"),
    [
        (ConnectionState.CONNECTED, "jane", ("Conexión abierta: @jane", Tone.OK)),
        (ConnectionState.CONNECTED, None, ("Conectando...", None)),
        (ConnectionState.ERROR, "Credenciales rechazadas", ("Credenciales rechazadas", Tone.ERROR)),
        (ConnectionState.ERROR, None, ("Sin conexión.", Tone.ERROR)),
        (ConnectionState.DISCONNECTED, None, ("Conexión cerrada", Tone.MUTED)),
        (None, None, ("", None)),
    ],
)
def test_server_status_text(state, detail, expected):
    assert server_status_text(state, detail) == expected


def test_probe_and_missing_field_messages():
    assert probe_ok_text("jane") == "Conexión correcta: @jane"
    assert missing_fields("", "u", "") == ["la URL", "el token"]


def test_delete_confirmation_mentions_rules_and_credentials():
    title, body = delete_confirmation_text("Trabajo", 3)
    assert "Trabajo" in title and "3 reglas" in body and "credenciales" in body
    assert "su regla de alerta" in delete_confirmation_text("A", 1)[1]
    assert "regla" not in delete_confirmation_text("A", 0)[1]


# -- Alerts tab ---------------------------------------------------------------------------------


def test_validate_alert_requires_a_chosen_room_without_duplicates_and_a_sound():
    room_error = validate_alert(RuleType.ROOM, False, "s", False)
    assert room_error is not None and "sala" in room_error[0]
    duplicate = validate_alert(RuleType.ROOM, True, "s", True)
    assert duplicate is not None and "Ya existe" in duplicate[0]
    no_sound = validate_alert(RuleType.MENTION, False, None, False)
    assert no_sound is not None and "sonido" in no_sound[0]
    assert validate_alert(RuleType.ROOM, True, "s", False) is None
    assert validate_alert(RuleType.MENTION, False, "s", False) is None


def test_repeat_values_keep_only_what_each_mode_uses():
    assert repeat_values(RepeatMode.ONCE, 5, 7) == (RepeatMode.ONCE, None, 1)
    assert repeat_values(RepeatMode.N_TIMES, 5, 7) == (RepeatMode.N_TIMES, 5, 7)
    assert repeat_values(RepeatMode.UNTIL_READ, 5, 7) == (RepeatMode.UNTIL_READ, None, 7)


def test_describe_rule_uses_room_name_cache_and_sound_name(room_rule, mention_rule):
    names = {"room1": "General"}
    assert (
        describe_rule(room_rule("room1", sound_id="digital-audio/tone1"), names)
        == "General → tone1"
    )
    assert (
        describe_rule(mention_rule(mention_all=True), names)
        == "Mención global (+ @all/@here) → bell"
    )


# -- Chat window --------------------------------------------------------------------------------


def _msg(sender, text="hola"):
    return HistoryMessage(sender, text, None)


@pytest.mark.parametrize(
    ("senders", "own", "expected"),
    [
        ([], "yo", 0),
        (["otro", "yo"], "yo", 2),
        (["yo", "otro", "otro"], "yo", 1),
        (["otro", "otro"], "yo", 0),
        (["otro", "otro"], None, 2),
    ],
)
def test_trailing_new_start_highlights_the_final_run_from_others(senders, own, expected):
    assert trailing_new_start([_msg(s) for s in senders], own) == expected


def test_history_html_escapes_text_and_marks_new_messages():
    html = history_html([_msg("yo", "<b>hi</b>"), _msg("ana", "x & y")], "yo")
    assert "&lt;b&gt;hi&lt;/b&gt;" in html and "x &amp; y" in html
    assert html.count("background-color") == 1


# -- Tray ---------------------------------------------------------------------------------------


def test_recent_notifications_move_to_front_without_duplicates_and_are_capped():
    recent = add_to_recent([], RecentNotification("Ana", "r1"))
    recent = add_to_recent(recent, RecentNotification("Bea", "r2"))
    recent = add_to_recent(recent, RecentNotification("Ana (2)", "r1"))
    assert recent == [RecentNotification("Ana (2)", "r1"), RecentNotification("Bea", "r2")]
    assert remove_from_recent(recent, "r1") == [RecentNotification("Bea", "r2")]
    full = [RecentNotification(f"u{i}", f"r{i}") for i in range(MAX_RECENT_ITEMS)]
    assert len(add_to_recent(full, RecentNotification("new", "new"))) == MAX_RECENT_ITEMS


def test_pending_meeting_alerts_replace_same_meeting_and_are_capped():
    pending = add_pending_meeting([], PendingMeetingAlert("m1", "Sync", None))
    pending = add_pending_meeting(pending, PendingMeetingAlert("m2", "Standup", "https://x"))
    pending = add_pending_meeting(pending, PendingMeetingAlert("m1", "Sync (movida)", None))
    assert [p.title for p in pending] == ["Sync (movida)", "Standup"]
    assert remove_pending_meeting(pending, "m1") == [
        PendingMeetingAlert("m2", "Standup", "https://x")
    ]
    full = [PendingMeetingAlert(f"m{i}", "x", None) for i in range(MAX_RECENT_ITEMS)]
    assert len(add_pending_meeting(full, PendingMeetingAlert("new", "y", None))) == MAX_RECENT_ITEMS


def _conn(name="Trabajo", connected=True, username=None, error=None):
    return ConnectionSummary(name, connected, username, error)


@pytest.mark.parametrize(
    ("connections", "expected"),
    [
        ([], ["Desconectado"]),
        ([_conn(username="jane")], ["Conectado: @jane"]),
        (
            [_conn("A", username="ana"), _conn("B", username="bea")],
            ["Conectado: @ana · A", "Conectado: @bea · B"],
        ),
        ([_conn()], ["Conectando a Trabajo..."]),
        (
            [_conn(connected=False, error="Credenciales rechazadas")],
            ["Trabajo: Credenciales rechazadas"],
        ),
        ([_conn(connected=False)], ["Trabajo: sin conexión"]),
    ],
)
def test_build_status_lines(connections, expected):
    assert build_status_lines(connections) == expected


def test_tooltip_has_name_status_and_fits_the_windows_limit():
    assert tooltip_text(["Conectado: @jane"], None) == "MK96 PC Rocket Tool\nConectado: @jane"
    assert len(tooltip_text(["x" * 300], "No molestar")) <= 127


# -- Meetings tab -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "direction", "expected"),
    [
        (CalendarMode.DAY, 1, date(2026, 1, 31)),
        (CalendarMode.WEEK, -1, date(2026, 1, 23)),
        (CalendarMode.MONTH, 1, date(2026, 2, 28)),
        (CalendarMode.MONTH, -1, date(2025, 12, 30)),
    ],
)
def test_shift_reference_moves_one_period_and_clamps_the_day(mode, direction, expected):
    assert shift_reference(date(2026, 1, 30), mode, direction) == expected
