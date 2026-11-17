"""Builds the real windows and dialogs with sample data and drives them a little, so Qt code
paths that the type checker cannot see (signals, layouts, painting) run at least once.
User data, credentials and the registry are never touched: APPDATA points at tmp_path and the
credential store is replaced by a dict."""

from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QIcon
from PySide6.QtTest import QTest

from mk96_app_pc_rocket_tool import secret_store, workers
from mk96_app_pc_rocket_tool.meetings import round_up_to_next_half_hour
from mk96_app_pc_rocket_tool.models import (
    AppConfig,
    ConnectionState,
    Meeting,
    MeetingRepeat,
    RoomInfo,
    Rule,
    RuleType,
    Server,
    Weekday,
)
from mk96_app_pc_rocket_tool.secret_store import ServerCredentials
from mk96_app_pc_rocket_tool.ui import alert_dialog
from mk96_app_pc_rocket_tool.ui.alert_dialog import AlertDialog
from mk96_app_pc_rocket_tool.ui.alerts_panel import AlertActions
from mk96_app_pc_rocket_tool.ui.calendar_views import CalendarCallbacks, MonthView, TimelineView
from mk96_app_pc_rocket_tool.ui.common import SoundPickerDialog
from mk96_app_pc_rocket_tool.ui.meeting_dialog import MeetingDialog, MeetingDialogOutcome
from mk96_app_pc_rocket_tool.ui.meetings_panel import MeetingActions
from mk96_app_pc_rocket_tool.ui.servers_panel import ServerActions
from mk96_app_pc_rocket_tool.ui.settings_window import SettingsPage, SettingsWindow

NOW = datetime.now().replace(second=0, microsecond=0)


@pytest.fixture
def isolated(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    store: dict[str, ServerCredentials] = {}
    monkeypatch.setattr(secret_store, "load_server_credentials", store.get)
    monkeypatch.setattr(secret_store, "save_server_credentials", store.__setitem__)
    workers.init_gui_dispatcher()
    return store


def _config() -> AppConfig:
    today_10 = NOW.replace(hour=10, minute=0)
    return AppConfig(
        servers=[Server("s1", "Trabajo", active=True), Server("s2", "Personal")],
        rules=[
            Rule("r1", "s1", RuleType.ROOM, "digital-audio/tone1", room_id="g", label="General"),
            Rule("m1", "s1", RuleType.MENTION, "interface-sounds/bong_001", mention_all=True),
        ],
        room_names={"g": "General"},
        meetings=[
            Meeting("a", "Daily", today_10, repeat=MeetingRepeat.DAILY),
            Meeting("b", "Overlap", today_10 + timedelta(minutes=15), duration_min=45),
            Meeting("c", "Weekly", today_10 + timedelta(hours=4), repeat=MeetingRepeat.WEEKLY,
                    repeat_days=(Weekday.MONDAY, Weekday.WEDNESDAY), active=False),
        ],
    )  # fmt: skip


def _callbacks(log: list[str]) -> CalendarCallbacks:
    return CalendarCallbacks(
        on_meeting_menu=lambda m, o, p: log.append(f"menu:{m.id}"),
        on_meeting_edit=lambda m: log.append(f"edit:{m.id}"),
        on_empty_menu=lambda start, duration, p: log.append(f"empty:{start:%H:%M}:{duration}"),
        on_show_day=lambda day: log.append(f"day:{day}"),
    )


def test_settings_window_builds_every_tab_and_reflects_status(isolated):
    config = _config()
    isolated["s1"] = ServerCredentials("https://chat.example.com", "uid", "tok")
    saved: list[str] = []
    window = SettingsWindow(
        config,
        QIcon(),
        ServerActions(lambda: saved.append("servers"), saved.append, saved.append, saved.append),
        AlertActions(lambda: saved.append("alerts"), lambda: None, lambda _s: None),
        MeetingActions(lambda: saved.append("meetings"), lambda _s: None),
    )
    window.show()
    window.set_server_status("s1", ConnectionState.CONNECTED, "jane")
    window.set_known_rooms("s1", [RoomInfo("g", "General", "c"), RoomInfo("d", "ana", "d")])
    for page in SettingsPage:
        window.show_page(page)
        QTest.qWait(30)
        assert not window.grab().isNull()
    assert window.servers._url.text() == "https://chat.example.com"
    assert window.servers._status_label.text() == "Conexión abierta: @jane"
    window.close()


def test_servers_panel_saves_a_new_server_with_its_credentials(isolated):
    config = AppConfig()
    saved: list[str] = []
    window = SettingsWindow(
        config,
        QIcon(),
        ServerActions(lambda: saved.append("saved"), saved.append, saved.append, saved.append),
        AlertActions(lambda: None, lambda: None, lambda _s: None),
        MeetingActions(lambda: None, lambda _s: None),
    )
    panel = window.servers
    panel._save()
    assert config.servers == [] and "Falta" in panel._test_result.text()
    QTest.keyClicks(panel._name, "Trabajo")
    QTest.keyClicks(panel._url, "https://chat.example.com")
    QTest.keyClicks(panel._user_id, "uid")
    QTest.keyClicks(panel._token, "tok")
    panel._save()
    assert [s.name for s in config.servers] == ["Trabajo"] and saved == ["saved"]
    assert isolated[config.servers[0].id] == ServerCredentials(
        "https://chat.example.com", "uid", "tok"
    )
    window.close()


def test_timeline_and_month_views_paint_and_map_clicks(isolated):
    config, log = _config(), []
    monday = (NOW - timedelta(days=NOW.weekday())).date()
    week = TimelineView(
        [monday + timedelta(days=i) for i in range(7)], config.meetings, _callbacks(log)
    )
    week.resize(900, 600)
    week.show()
    assert not week.grab().isNull()
    grid = week._grid
    today_index = NOW.weekday()
    day_width = (grid.width() - 56) / 7
    x_today = int(56 + today_index * day_width + day_width / 4)  # first lane (overlaps split)
    # 10:00 is row 6 (07:00 + 6 * 30 min): the Daily block sits there.
    QTest.mouseClick(grid, Qt.MouseButton.RightButton, pos=QPoint(x_today, 6 * 24 + 5))
    QTest.mouseDClick(grid, Qt.MouseButton.LeftButton, pos=QPoint(x_today, 6 * 24 + 5))
    QTest.mouseClick(grid, Qt.MouseButton.RightButton, pos=QPoint(x_today, 26 * 24 + 5))
    assert log[:2] == ["menu:a", "edit:a"]
    assert log[2] == "empty:20:00:30"

    month = MonthView(NOW.year, NOW.month, config.meetings, _callbacks(log))
    month.resize(900, 700)
    month.show()
    assert not month.grab().isNull()
    week.close()
    month.close()


def test_meeting_dialog_collects_a_weekly_meeting(isolated):
    start = round_up_to_next_half_hour(NOW)
    dialog = MeetingDialog(None, lambda _s: None, None, start, 60)  # type: ignore[arg-type]
    QTest.keyClicks(dialog._title, "Planning")
    dialog._repeat.setCurrentIndex(dialog._repeat.findData(MeetingRepeat.WEEKLY.value))
    dialog._remind.setValue(10)
    dialog._save()
    assert dialog.outcome is MeetingDialogOutcome.SAVE
    meeting = dialog.result_meeting
    assert meeting is not None and meeting.title == "Planning" and meeting.start == start
    assert meeting.duration_min == 60 and meeting.remind_before_min == 10
    assert meeting.repeat_days == (Weekday(start.weekday()),)  # preselected from the date


def test_alert_dialog_and_pickers_build_and_collect(isolated, monkeypatch):
    errors: list[str] = []
    monkeypatch.setattr(
        alert_dialog, "show_error", lambda _parent, title, _body: errors.append(title)
    )
    config = _config()
    rooms = [RoomInfo("g", "General", "c"), RoomInfo("d", "ana", "d")]
    edit = AlertDialog(None, config, "s1", rooms, lambda _s: None, config.rules[1])  # type: ignore[arg-type]
    edit._try_accept()
    assert edit.result_data is not None and edit.result_data.mention_all is True

    new = AlertDialog(None, config, "s1", rooms, lambda _s: None)  # type: ignore[arg-type]
    new._sound_id = "digital-audio/tone1"
    QTest.keyClicks(new._room_search, "General")  # typed text alone is not a chosen room
    new._try_accept()
    assert new.result_data is None and errors == ["Falta elegir una sala"]
    new._on_room_completed(rooms[0].description)
    new._try_accept()
    assert errors[-1] == "Ya existe una alerta para esta sala"  # r1 already covers "g"
    new._on_room_completed(rooms[1].description)
    new._try_accept()
    assert new.result_data is not None and new.result_data.room == rooms[1]

    picker = SoundPickerDialog(None, "digital-audio/tone1", lambda _s: None)
    assert picker._tree.currentItem() is not None
    picker._search.setText("tone1")
    assert picker._tree.topLevelItemCount() == 6


def test_month_view_right_click_on_empty_day_proposes_a_start(isolated):
    log: list[str] = []
    month = MonthView(NOW.year, NOW.month, [], _callbacks(log))
    month.resize(700, 560)
    month.show()
    QTest.mouseClick(month, Qt.MouseButton.RightButton, pos=QPoint(350, 300))
    assert log and log[0].startswith("empty:") and log[0].endswith(":None")
    month.close()
