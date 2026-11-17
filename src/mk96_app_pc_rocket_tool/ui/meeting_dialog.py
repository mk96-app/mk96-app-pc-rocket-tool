"""Dialog to create or edit a meeting. It only collects and validates; the meetings tab saves
or deletes. Dates and times use Qt's own editors, so they are always valid."""

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from mk96_app_pc_rocket_tool.meetings import (
    DEFAULT_MEETING_SOUND_ID,
    WEEKDAY_LETTERS,
    round_up_to_next_half_hour,
    validate_meeting,
)
from mk96_app_pc_rocket_tool.models import Meeting, MeetingRepeat, Weekday, new_id
from mk96_app_pc_rocket_tool.sound_library import display_name
from mk96_app_pc_rocket_tool.ui.common import (
    SoundPickerDialog,
    confirm_destructive,
    open_web_link,
    play_button,
    show_error,
)
from mk96_app_pc_rocket_tool.ui.servers_panel import Tone

DATE_FORMAT = "dd/MM/yyyy"
TIME_FORMAT = "HH:mm"
DEFAULT_DURATION_MIN = 30
REPEAT_LABELS = {
    MeetingRepeat.NONE: "No se repite",
    MeetingRepeat.DAILY: "Cada día",
    MeetingRepeat.WEEKLY: "Cada semana",
    MeetingRepeat.MONTHLY: "Cada mes (mismo día del mes)",
}
WEEKDAY_TOOLTIPS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


class MeetingDialogOutcome(StrEnum):
    SAVE = "save"
    DELETE = "delete"


def _to_qdatetime(moment: datetime) -> tuple[QDate, QTime]:
    return QDate(moment.year, moment.month, moment.day), QTime(moment.hour, moment.minute)


def _to_datetime(qdate: QDate, qtime: QTime) -> datetime:
    return datetime(qdate.year(), qdate.month(), qdate.day(), qtime.hour(), qtime.minute())


def _date_edit() -> QDateEdit:
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    edit.setDisplayFormat(DATE_FORMAT)
    return edit


class MeetingDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        preview: Callable[[str], None],
        meeting: Meeting | None = None,
        start: datetime | None = None,
        duration_min: int | None = None,
    ):
        """meeting=None creates one starting at `start` (default: next half hour) lasting
        `duration_min`; with a meeting it edits it and offers Delete."""
        super().__init__(parent)
        self.setWindowTitle("Editar reunión" if meeting else "Nueva reunión")
        self.setMinimumWidth(560)
        self._meeting = meeting
        self._preview = preview
        self._sound_id = meeting.sound_id if meeting else None
        self.outcome: MeetingDialogOutcome | None = None
        self.result_meeting: Meeting | None = None

        layout = QVBoxLayout(self)
        if meeting and meeting.is_series:
            note = QLabel("Esta reunión se repite: los cambios afectan a toda la serie.")
            note.setStyleSheet(f"color: {Tone.MUTED};")
            layout.addWidget(note)
        layout.addWidget(self._build_main())
        layout.addWidget(self._build_when())
        layout.addWidget(self._build_repeat())
        layout.addWidget(self._build_reminder())
        layout.addWidget(self._build_details())
        layout.addWidget(self._build_buttons())
        self._load(start, duration_min)
        self._sync_repeat_rows()
        self._title.setFocus()

    # -- Sections -----------------------------------------------------------------------------

    def _build_main(self) -> QGroupBox:
        self._title = QLineEdit()
        self._active = QCheckBox("Activa (avisa a su hora)")
        form = QFormLayout()
        form.addRow("Título:", self._title)
        form.addRow("", self._active)
        group = QGroupBox("Reunión")
        group.setLayout(form)
        return group

    def _build_when(self) -> QGroupBox:
        self._date = _date_edit()
        self._time = QTimeEdit()
        self._time.setDisplayFormat(TIME_FORMAT)
        self._duration = QSpinBox(minimum=5, maximum=480, singleStep=5, suffix=" min")
        row = QHBoxLayout()
        row.addWidget(self._date)
        row.addWidget(QLabel("a las"))
        row.addWidget(self._time)
        row.addStretch(1)
        form = QFormLayout()
        form.addRow("Fecha:", row)
        form.addRow("Duración:", self._duration)
        group = QGroupBox("Cuándo")
        group.setLayout(form)
        return group

    def _build_repeat(self) -> QGroupBox:
        self._repeat = QComboBox()
        for mode, label in REPEAT_LABELS.items():
            self._repeat.addItem(label, mode.value)
        self._repeat.currentIndexChanged.connect(self._sync_repeat_rows)
        self._day_checks: dict[Weekday, QCheckBox] = {}
        self._days_row = QWidget()
        days_layout = QHBoxLayout(self._days_row)
        days_layout.setContentsMargins(0, 0, 0, 0)
        for weekday in Weekday:
            check = QCheckBox(WEEKDAY_LETTERS[weekday])
            check.setToolTip(WEEKDAY_TOOLTIPS[weekday])
            days_layout.addWidget(check)
            self._day_checks[weekday] = check
        days_layout.addStretch(1)
        self._has_end = QCheckBox("Hasta el")
        self._end_date = _date_edit()
        self._has_end.toggled.connect(self._end_date.setEnabled)
        self._end_row = QWidget()
        end_layout = QHBoxLayout(self._end_row)
        end_layout.setContentsMargins(0, 0, 0, 0)
        end_layout.addWidget(self._has_end)
        end_layout.addWidget(self._end_date)
        end_layout.addStretch(1)
        self._form_repeat = QFormLayout()
        self._form_repeat.addRow("Repetir:", self._repeat)
        self._form_repeat.addRow("Días:", self._days_row)
        self._form_repeat.addRow("Fin de la serie:", self._end_row)
        group = QGroupBox("Repetición")
        group.setLayout(self._form_repeat)
        return group

    def _build_reminder(self) -> QGroupBox:
        self._remind = QSpinBox(minimum=0, maximum=180, singleStep=5, suffix=" min")
        self._remind.setSpecialValueText("Solo a la hora")
        self._sound_label = QLabel()
        choose = QPushButton("Elegir sonido...")
        choose.clicked.connect(self._choose_sound)
        preview = play_button("Probar")
        preview.clicked.connect(self._preview_sound)
        sound_row = QHBoxLayout()
        sound_row.addWidget(self._sound_label, 1)
        sound_row.addWidget(choose)
        sound_row.addWidget(preview)
        form = QFormLayout()
        form.addRow("Avisar antes:", self._remind)
        form.addRow("Sonido:", sound_row)
        group = QGroupBox("Aviso y sonido")
        group.setLayout(form)
        return group

    def _build_details(self) -> QGroupBox:
        self._link = QLineEdit(placeholderText="https://... (opcional)")
        open_link = QPushButton("Abrir")
        open_link.clicked.connect(
            lambda: self._link.text().strip() and open_web_link(self._link.text())
        )
        link_row = QHBoxLayout()
        link_row.addWidget(self._link, 1)
        link_row.addWidget(open_link)
        self._notes = QLineEdit()
        form = QFormLayout()
        form.addRow("Enlace:", link_row)
        form.addRow("Notas:", self._notes)
        group = QGroupBox("Detalles")
        group.setLayout(form)
        return group

    def _build_buttons(self) -> QWidget:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        if self._meeting is not None:
            # Destructive action kept apart, on the left, away from Save.
            delete = QPushButton("Borrar reunión")
            delete.clicked.connect(self._delete)
            layout.addWidget(delete)
        layout.addStretch(1)
        layout.addWidget(buttons)
        return row

    # -- Load / sync ----------------------------------------------------------------------------

    def _load(self, start: datetime | None, duration_min: int | None) -> None:
        meeting = self._meeting
        moment = meeting.start if meeting else (start or round_up_to_next_half_hour(datetime.now()))
        qdate, qtime = _to_qdatetime(moment)
        self._date.setDate(qdate)
        self._time.setTime(qtime)
        until = meeting.repeat_until if meeting else None
        self._has_end.setChecked(until is not None)
        self._end_date.setEnabled(until is not None)
        self._end_date.setDate(_to_qdatetime(until)[0] if until else qdate.addMonths(3))
        if meeting is None:
            self._duration.setValue(duration_min or DEFAULT_DURATION_MIN)
            self._active.setChecked(True)
            self._repeat.setCurrentIndex(self._repeat.findData(MeetingRepeat.NONE.value))
        else:
            self._title.setText(meeting.title)
            self._active.setChecked(meeting.active)
            self._duration.setValue(meeting.duration_min)
            self._repeat.setCurrentIndex(self._repeat.findData(meeting.repeat.value))
            for weekday, check in self._day_checks.items():
                check.setChecked(weekday in meeting.repeat_days)
            self._remind.setValue(meeting.remind_before_min)
            self._link.setText(meeting.link or "")
            self._notes.setText(meeting.notes)
        self._update_sound_label()

    def _selected_repeat(self) -> MeetingRepeat:
        return MeetingRepeat(self._repeat.currentData())

    def _sync_repeat_rows(self) -> None:
        repeat = self._selected_repeat()
        self._form_repeat.setRowVisible(self._days_row, repeat is MeetingRepeat.WEEKLY)
        self._form_repeat.setRowVisible(self._end_row, repeat is not MeetingRepeat.NONE)
        if repeat is MeetingRepeat.WEEKLY and not any(
            c.isChecked() for c in self._day_checks.values()
        ):
            self._day_checks[Weekday(self._date.date().dayOfWeek() - 1)].setChecked(True)

    # -- Sound ----------------------------------------------------------------------------------

    def _update_sound_label(self) -> None:
        self._sound_label.setText(
            display_name(self._sound_id) if self._sound_id else "(sonido por defecto)"
        )

    def _choose_sound(self) -> None:
        picker = SoundPickerDialog(self, self._sound_id, self._preview)
        if picker.exec() and picker.chosen_sound_id:
            self._sound_id = picker.chosen_sound_id
            self._update_sound_label()

    def _preview_sound(self) -> None:
        self._preview(self._sound_id or DEFAULT_MEETING_SOUND_ID)

    # -- Result ---------------------------------------------------------------------------------

    def _collect(self) -> Meeting | None:
        repeat = self._selected_repeat()
        days = tuple(day for day, check in self._day_checks.items() if check.isChecked())
        error = validate_meeting(self._title.text(), repeat, days)
        if error is not None:
            show_error(self, *error)
            return None
        start = _to_datetime(self._date.date(), self._time.time())
        until = None
        if repeat is not MeetingRepeat.NONE and self._has_end.isChecked():
            until = _to_datetime(self._end_date.date(), QTime(0, 0))
            if until.date() < start.date():
                show_error(
                    self, "Fin de la serie inválido", "La serie no puede terminar antes de empezar."
                )
                return None
        return Meeting(
            id=self._meeting.id if self._meeting else new_id(),
            title=self._title.text().strip(),
            start=start.replace(second=0, microsecond=0),
            duration_min=self._duration.value(),
            repeat=repeat,
            repeat_days=days if repeat is MeetingRepeat.WEEKLY else (),
            repeat_until=until,
            remind_before_min=self._remind.value(),
            link=self._link.text().strip() or None,
            notes=self._notes.text().strip(),
            sound_id=self._sound_id,
            active=self._active.isChecked(),
        )

    def _save(self) -> None:
        meeting = self._collect()
        if meeting is not None:
            self.outcome, self.result_meeting = MeetingDialogOutcome.SAVE, meeting
            self.accept()

    def _delete(self) -> None:
        if self._meeting is None:
            return
        if confirm_destructive(
            self,
            f"¿Borrar la reunión «{self._meeting.title}»?",
            "Esta acción no se puede deshacer.",
            "Borrar",
        ):
            self.outcome = MeetingDialogOutcome.DELETE
            self.accept()
