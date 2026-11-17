"""Dialog to add or edit an alert (a room rule or the server's global mention rule).
It only collects and validates; the alerts tab stores the result."""

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mk96_app_pc_rocket_tool.models import AppConfig, RepeatMode, RoomInfo, RoomType, Rule, RuleType
from mk96_app_pc_rocket_tool.sound_library import display_name
from mk96_app_pc_rocket_tool.ui.common import (
    SoundPickerDialog,
    Stepper,
    children,
    play_button,
    show_error,
)

ROOM_GROUPS = (
    (RoomType.CHANNEL, "Canales"),
    (RoomType.PRIVATE, "Privados"),
    (RoomType.DIRECT, "Mensajes directos"),
)
REPEAT_LABELS = {
    RepeatMode.ONCE: "Solo una vez",
    RepeatMode.N_TIMES: "N veces",
    RepeatMode.UNTIL_READ: "Hasta que lo leas",
}
ROOM_ID_ROLE = Qt.ItemDataRole.UserRole
DEFAULT_REPEAT_COUNT = 3


def validate_alert(
    rule_type: RuleType, has_room: bool, sound_id: str | None, duplicate_room: bool
) -> tuple[str, str] | None:
    """(title, message) of the first problem that prevents saving, or None."""
    if rule_type is RuleType.ROOM:
        if not has_room:
            return (
                "Falta elegir una sala",
                "Usa el buscador o «Ver todas...» y elige una sala o persona de la lista: "
                "escribir el nombre a mano no basta.",
            )
        if duplicate_room:
            return (
                "Ya existe una alerta para esta sala",
                "Edítala desde la lista de alertas en vez de crear otra.",
            )
    if not sound_id:
        return "Falta el sonido", "Usa «Elegir sonido...» antes de guardar la alerta."
    return None


def repeat_values(mode: RepeatMode, count: int, every: int) -> tuple[RepeatMode, int | None, int]:
    """(repeat_mode, repeat_count, repeat_every_polls) as they are stored."""
    return (
        mode,
        (count if mode is RepeatMode.N_TIMES else None),
        (every if mode is not RepeatMode.ONCE else 1),
    )


@dataclass(frozen=True, slots=True)
class AlertFormResult:
    sound_id: str
    repeat_mode: RepeatMode
    repeat_count: int | None
    repeat_every_polls: int
    mention_all: bool
    room: RoomInfo | None  # only for new room alerts


class RoomPickerDialog(QDialog):
    """Every known room grouped by type, with a live filter. Makes no request."""

    def __init__(self, parent: QWidget, rooms: list[RoomInfo]):
        super().__init__(parent)
        self.setWindowTitle("Elegir sala o persona")
        self.resize(460, 520)
        self._rooms = {room.room_id: room for room in rooms}
        self.chosen: RoomInfo | None = None

        self._search = QLineEdit(placeholderText="Buscar sala o persona...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemDoubleClicked.connect(lambda item, _c: self._accept_item(item))
        for room_type, label in ROOM_GROUPS:
            members = sorted(
                (r for r in rooms if r.type == room_type), key=lambda r: r.name.lower()
            )
            if not members:
                continue
            group = QTreeWidgetItem([label])
            group.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            self._tree.addTopLevelItem(group)
            for room in members:
                item = QTreeWidgetItem([room.name])
                item.setData(0, ROOM_ID_ROLE, room.room_id)
                item.setToolTip(0, room.room_id)
                group.addChild(item)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Seleccionar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(lambda: self._accept_item(self._tree.currentItem()))
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._search)
        layout.addWidget(self._tree)
        layout.addWidget(buttons)

    def _apply_filter(self, text: str) -> None:
        query = text.strip().lower()
        for group in children(self._tree.invisibleRootItem()):
            shown = 0
            for child in children(group):
                haystack = f"{child.text(0)} {child.data(0, ROOM_ID_ROLE)}".lower()
                child.setHidden(bool(query) and query not in haystack)
                shown += not child.isHidden()
            group.setHidden(shown == 0)
            group.setExpanded(bool(query))

    def _accept_item(self, item: QTreeWidgetItem | None) -> None:
        room_id = item.data(0, ROOM_ID_ROLE) if item is not None else None
        if room_id:
            self.chosen = self._rooms[room_id]
            self.accept()


class AlertDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        config: AppConfig,
        server_id: str,
        known_rooms: list[RoomInfo],
        preview: Callable[[str], None],
        rule: Rule | None = None,
        rule_type: RuleType = RuleType.ROOM,
    ):
        """rule=None creates an alert of `rule_type`; with a rule it edits it (type and room
        are fixed then)."""
        super().__init__(parent)
        self._config = config
        self._server_id = server_id
        self._rooms = known_rooms
        self._preview = preview
        self._rule = rule
        self._type = rule.type if rule else rule_type
        self._sound_id = rule.sound_id if rule else None
        self._room: RoomInfo | None = None
        self.result_data: AlertFormResult | None = None
        if rule:
            self.setWindowTitle("Editar alerta")
        else:
            self.setWindowTitle(
                "Nueva alerta" if self._type is RuleType.ROOM else "Configurar mención global"
            )
        self.setMinimumWidth(520)

        form = QFormLayout()
        if self._type is RuleType.ROOM:
            form.addRow("Sala o persona:", self._build_room_row())
        sound_row = QHBoxLayout()
        self._sound_label = QLabel(display_name(self._sound_id) if self._sound_id else "(ninguno)")
        choose = QPushButton("Elegir sonido...")
        choose.clicked.connect(self._choose_sound)
        self._preview_button = play_button("Probar")
        self._preview_button.setEnabled(self._sound_id is not None)
        self._preview_button.clicked.connect(
            lambda: self._sound_id and self._preview(self._sound_id)
        )
        sound_row.addWidget(self._sound_label, 1)
        sound_row.addWidget(choose)
        sound_row.addWidget(self._preview_button)
        form.addRow("Sonido:", sound_row)
        form.addRow(self._repeat_label(), self._build_repeat_controls(rule))

        self._mention_all = QCheckBox("Avisar también con @all y @here")
        self._mention_all.setChecked(bool(rule and rule.mention_all))
        if self._type is RuleType.MENTION:
            form.addRow("", self._mention_all)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    # -- Room ---------------------------------------------------------------------------------

    def _build_room_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        if self._rule is not None:
            room_id = self._rule.room_id or ""
            layout.addWidget(QLabel(self._config.room_names.get(room_id, room_id)))
            return row
        self._room_search = QLineEdit(placeholderText="Escribe para buscar...")
        self._by_description = {room.description: room for room in self._rooms}
        completer = QCompleter(QStringListModel(sorted(self._by_description)), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(12)
        completer.activated.connect(self._on_room_completed)
        self._room_search.setCompleter(completer)
        self._room_search.textEdited.connect(self._on_room_text_edited)
        browse = QPushButton("Ver todas...")
        browse.clicked.connect(self._browse_rooms)
        layout.addWidget(self._room_search, 1)
        layout.addWidget(browse)
        return row

    def _on_room_text_edited(self, text: str) -> None:
        if self._room is not None and text != self._room.description:
            self._room = None  # typed text alone never counts as a chosen room

    def _on_room_completed(self, description: str) -> None:
        self._room = self._by_description.get(description)

    def _browse_rooms(self) -> None:
        picker = RoomPickerDialog(self, self._rooms)
        if picker.exec() and picker.chosen is not None:
            self._room = picker.chosen
            self._room_search.setText(picker.chosen.description)

    # -- Sound and repetition ------------------------------------------------------------------

    def _choose_sound(self) -> None:
        picker = SoundPickerDialog(self, self._sound_id, self._preview)
        if picker.exec() and picker.chosen_sound_id:
            self._sound_id = picker.chosen_sound_id
            self._sound_label.setText(display_name(self._sound_id))
            self._preview_button.setEnabled(True)

    @staticmethod
    def _repeat_label() -> QLabel:
        label = QLabel("Repetir sonido: ⓘ")
        label.setToolTip(
            "El sonido se repite mientras el mensaje siga sin leer en Rocket.Chat (en cualquier "
            "dispositivo), con la frecuencia elegida. En cuanto lo leas, deja de repetirse."
        )
        return label

    def _build_repeat_controls(self, rule: Rule | None) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        self._repeat_mode = QComboBox()
        for mode, label in REPEAT_LABELS.items():
            self._repeat_mode.addItem(label, mode.value)
        current = rule.repeat_mode if rule else RepeatMode.ONCE
        self._repeat_mode.setCurrentIndex(self._repeat_mode.findData(current.value))
        self._repeat_count = Stepper(
            1, 99, (rule.repeat_count if rule else None) or DEFAULT_REPEAT_COUNT
        )
        self._repeat_every = Stepper(1, 20, rule.repeat_every_polls if rule else 1)
        count_label = QLabel("Número de veces:")
        every_label = QLabel("Cada cuántas comprobaciones:")
        # One grid so both steppers sit in the same column whatever their label width.
        details = QGridLayout()
        details.setContentsMargins(0, 4, 0, 0)
        details.setHorizontalSpacing(10)
        details.addWidget(count_label, 0, 0)
        details.addWidget(self._repeat_count, 0, 1)
        details.addWidget(every_label, 1, 0)
        details.addWidget(self._repeat_every, 1, 1)
        details.setColumnStretch(2, 1)
        self._count_row = (count_label, self._repeat_count)
        self._every_row = (every_label, self._repeat_every)
        layout.addWidget(self._repeat_mode)
        layout.addLayout(details)
        self._repeat_mode.currentIndexChanged.connect(self._sync_repeat_rows)
        self._sync_repeat_rows()
        return box

    def _selected_repeat_mode(self) -> RepeatMode:
        return RepeatMode(self._repeat_mode.currentData())

    def _sync_repeat_rows(self) -> None:
        mode = self._selected_repeat_mode()
        for widget in self._count_row:
            widget.setVisible(mode is RepeatMode.N_TIMES)
        for widget in self._every_row:
            widget.setVisible(mode is not RepeatMode.ONCE)
        self.adjustSize()

    # -- Result -------------------------------------------------------------------------------

    def _try_accept(self) -> None:
        is_new_room_alert = self._rule is None and self._type is RuleType.ROOM
        duplicate = (
            is_new_room_alert
            and self._room is not None
            and self._config.find_room_rule(self._server_id, self._room.room_id) is not None
        )
        has_room = self._rule is not None or self._room is not None
        error = validate_alert(self._type, has_room, self._sound_id, duplicate)
        if error is not None:
            show_error(self, *error)
            return
        mode, count, every = repeat_values(
            self._selected_repeat_mode(), self._repeat_count.value(), self._repeat_every.value()
        )
        self.result_data = AlertFormResult(
            sound_id=self._sound_id or "",
            repeat_mode=mode,
            repeat_count=count,
            repeat_every_polls=every,
            mention_all=self._type is RuleType.MENTION and self._mention_all.isChecked(),
            room=self._room if is_new_room_alert else None,
        )
        self.accept()
