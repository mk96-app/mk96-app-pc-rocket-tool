"""Meetings tab: Day/Week/Month calendar with navigation and "+ Nueva reunión". Meetings are
local and not tied to any Rocket.Chat server. Menus run their action after they close, and
series ask whether a change applies to one occurrence or to the whole series."""

import calendar
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from mk96_app_pc_rocket_tool.meetings import (
    OccurrenceStatus,
    edited_meeting,
    format_day_label,
    format_duration,
    format_month_label,
    format_week_label,
    occurrence_status,
    week_bounds,
    with_exception,
)
from mk96_app_pc_rocket_tool.models import AppConfig, Meeting, OccurrenceState
from mk96_app_pc_rocket_tool.ui.calendar_views import CalendarCallbacks, MonthView, TimelineView
from mk96_app_pc_rocket_tool.ui.common import confirm_destructive, standard_icon
from mk96_app_pc_rocket_tool.ui.meeting_dialog import MeetingDialog, MeetingDialogOutcome

NOW_LINE_REFRESH_MS = 20_000


class CalendarMode(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class Scope(StrEnum):
    THIS_ONE = "this_one"
    SERIES = "series"


@dataclass(frozen=True, slots=True)
class MeetingActions:
    config_changed: Callable[[], None]
    preview: Callable[[str], None]


def shift_reference(reference: date, mode: CalendarMode, direction: int) -> date:
    """Moves the shown period one step back (-1) or forward (+1)."""
    if mode is CalendarMode.DAY:
        return reference + timedelta(days=direction)
    if mode is CalendarMode.WEEK:
        return reference + timedelta(weeks=direction)
    month_index = reference.year * 12 + reference.month - 1 + direction
    year, month = divmod(month_index, 12)
    month += 1
    return date(year, month, min(reference.day, calendar.monthrange(year, month)[1]))


class MeetingsPanel(QWidget):
    def __init__(self, config: AppConfig, actions: MeetingActions):
        super().__init__()
        self._config = config
        self._actions = actions
        self._mode = CalendarMode.WEEK
        self._reference = date.today()
        self._view: TimelineView | MonthView | None = None
        self._callbacks = CalendarCallbacks(
            on_meeting_menu=self._meeting_menu,
            on_meeting_edit=lambda meeting: self._open_dialog(meeting),
            on_empty_menu=self._empty_menu,
            on_show_day=self._show_day,
        )

        self._mode_buttons = QButtonGroup(self)
        self._buttons: dict[CalendarMode, QPushButton] = {}
        modes = QHBoxLayout()
        for mode, label in (
            (CalendarMode.DAY, "Día"),
            (CalendarMode.WEEK, "Semana"),
            (CalendarMode.MONTH, "Mes"),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(mode is self._mode)
            button.clicked.connect(lambda _checked, m=mode: self._set_mode(m))
            self._mode_buttons.addButton(button)
            self._buttons[mode] = button
            modes.addWidget(button)
        modes.addStretch(1)
        new_button = QPushButton("+ Nueva reunión")
        new_button.clicked.connect(lambda: self._open_dialog(None))
        modes.addWidget(new_button)

        previous = QPushButton()
        previous.setIcon(standard_icon(previous, QStyle.StandardPixmap.SP_ArrowLeft))
        previous.setToolTip("Anterior")
        previous.clicked.connect(lambda: self._navigate(-1))
        following = QPushButton()
        following.setIcon(standard_icon(following, QStyle.StandardPixmap.SP_ArrowRight))
        following.setToolTip("Siguiente")
        following.clicked.connect(lambda: self._navigate(1))
        today = QPushButton("Hoy")
        today.clicked.connect(self._go_today)
        self._period = QLabel()
        font = self._period.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.15)
        self._period.setFont(font)
        navigation = QHBoxLayout()
        navigation.addWidget(previous)
        navigation.addWidget(following)
        navigation.addWidget(today)
        navigation.addSpacing(12)
        navigation.addWidget(self._period, 1)

        self._container = QVBoxLayout()
        layout = QVBoxLayout(self)
        layout.addLayout(modes)
        layout.addLayout(navigation)
        layout.addLayout(self._container, 1)

        # Moves the red "now" line without rebuilding the view.
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(NOW_LINE_REFRESH_MS)
        self._tick_timer.timeout.connect(lambda: self._view and self._view.tick())
        self._tick_timer.start()
        self.refresh()

    # -- Navigation ---------------------------------------------------------------------------

    def _set_mode(self, mode: CalendarMode) -> None:
        self._mode = mode
        self._buttons[mode].setChecked(True)
        self.refresh()

    def _navigate(self, direction: int) -> None:
        self._reference = shift_reference(self._reference, self._mode, direction)
        self.refresh()

    def _go_today(self) -> None:
        self._reference = date.today()
        self.refresh()

    def _show_day(self, day: date) -> None:
        self._reference = day
        self._set_mode(CalendarMode.DAY)

    def refresh(self) -> None:
        if self._view is not None:
            self._container.removeWidget(self._view)
            self._view.deleteLater()
        meetings = list(self._config.meetings)
        if self._mode is CalendarMode.DAY:
            self._period.setText(format_day_label(self._reference))
            view: TimelineView | MonthView = TimelineView(
                [self._reference], meetings, self._callbacks
            )
        elif self._mode is CalendarMode.WEEK:
            monday, _next = week_bounds(self._reference)
            self._period.setText(format_week_label(monday))
            view = TimelineView(
                [monday + timedelta(days=i) for i in range(7)], meetings, self._callbacks
            )
        else:
            self._period.setText(format_month_label(self._reference.year, self._reference.month))
            view = MonthView(self._reference.year, self._reference.month, meetings, self._callbacks)
        self._view = view
        self._container.addWidget(view)
        # The scroll range is only known once the view has its size.
        QTimer.singleShot(0, view.scroll_to_anchor)

    # -- Menus --------------------------------------------------------------------------------

    @staticmethod
    def _menu_with_title(title: str) -> QMenu:
        menu = QMenu()
        header = menu.addAction(title)
        header.setEnabled(False)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        menu.addSeparator()
        return menu

    def _empty_menu(self, start: datetime, duration: int | None, global_pos: QPoint) -> None:
        if duration is None:
            title = f"{start:%d/%m/%Y}"
            label = f"+ Añadir reunión el {start:%d/%m/%Y} a las {start:%H:%M}"
        else:
            end = start + timedelta(minutes=duration)
            title = f"{start:%d/%m/%Y}"
            label = f"+ Añadir reunión de {start:%H:%M} a {end:%H:%M} ({format_duration(duration)})"
        menu = self._menu_with_title(title)
        add = menu.addAction(label)
        if menu.exec(global_pos) is add:
            self._open_dialog(None, start, duration)

    def _meeting_menu(self, meeting: Meeting, occurrence: datetime, global_pos: QPoint) -> None:
        active = occurrence_status(meeting, occurrence) is OccurrenceStatus.ACTIVE
        if active:
            toggle_label = "Desactivar"
        else:
            toggle_label = (
                "Activar toda la serie" if meeting.is_series and not meeting.active else "Activar"
            )
        menu = self._menu_with_title(f"{occurrence:%H:%M} {meeting.title}")
        edit = menu.addAction("Editar...")
        toggle = menu.addAction(toggle_label)
        delete = menu.addAction("Eliminar")
        chosen = menu.exec(global_pos)
        if chosen is edit:
            self._open_dialog(meeting)
        elif chosen is toggle:
            self._toggle(meeting, occurrence)
        elif chosen is delete:
            self._delete(meeting, occurrence)

    # -- Changes ------------------------------------------------------------------------------

    def _save(self, meeting: Meeting) -> None:
        self._config.upsert_meeting(meeting)
        self._actions.config_changed()
        self.refresh()

    def _remove(self, meeting: Meeting) -> None:
        self._config.remove_meeting(meeting.id)
        self._actions.config_changed()
        self.refresh()

    def _open_dialog(
        self, meeting: Meeting | None, start: datetime | None = None, duration: int | None = None
    ) -> None:
        dialog = MeetingDialog(self, self._actions.preview, meeting, start, duration)
        if not dialog.exec():
            return
        if dialog.outcome is MeetingDialogOutcome.DELETE and meeting is not None:
            self._remove(meeting)
        elif dialog.result_meeting is not None:
            saved = edited_meeting(meeting, dialog.result_meeting)
            self._reference = saved.start.date()
            self._save(saved)

    def _ask_scope(self, verb: str, meeting: Meeting, occurrence: datetime) -> Scope | None:
        box = QMessageBox(
            QMessageBox.Icon.Question,
            f"«{meeting.title}» se repite",
            f"¿Quieres {verb} solo la del {occurrence:%d/%m/%Y} a las {occurrence:%H:%M} "
            "o toda la serie?",
            parent=self,
        )
        this_one = box.addButton("Solo esta", QMessageBox.ButtonRole.AcceptRole)
        series = box.addButton("Toda la serie", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is this_one:
            return Scope.THIS_ONE
        return Scope.SERIES if clicked is series else None

    def _delete(self, meeting: Meeting, occurrence: datetime) -> None:
        if meeting.is_series:
            scope = self._ask_scope("eliminar", meeting, occurrence)
            if scope is Scope.THIS_ONE:
                self._save(with_exception(meeting, occurrence, OccurrenceState.DELETED))
            elif scope is Scope.SERIES:
                self._remove(meeting)
            return
        if confirm_destructive(
            self,
            f"¿Eliminar la reunión «{meeting.title}»?",
            "Esta acción no se puede deshacer.",
            "Eliminar",
        ):
            self._remove(meeting)

    def _toggle(self, meeting: Meeting, occurrence: datetime) -> None:
        if occurrence_status(meeting, occurrence) is OccurrenceStatus.ACTIVE:
            if not meeting.is_series:
                self._save(replace(meeting, active=False))
                return
            scope = self._ask_scope("desactivar", meeting, occurrence)
            if scope is Scope.THIS_ONE:
                self._save(with_exception(meeting, occurrence, OccurrenceState.INACTIVE))
            elif scope is Scope.SERIES:
                self._save(replace(meeting, active=False))
        elif not meeting.active:
            self._save(replace(meeting, active=True))  # reactivating is always the whole meeting
        else:
            self._save(with_exception(meeting, occurrence, None))

    def config_replaced(self) -> None:
        self.refresh()
