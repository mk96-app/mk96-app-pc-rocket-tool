"""Alerts tab: the alerts of the server picked at the top (global mention card + room alerts)
and the Do Not Disturb quiet schedule. Adding room alerts needs the server connected, since
the room list comes from it."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from mk96_app_pc_rocket_tool.models import (
    AppConfig,
    ConnectionState,
    RoomInfo,
    RoomType,
    Rule,
    RuleType,
    new_id,
)
from mk96_app_pc_rocket_tool.sound_library import display_name
from mk96_app_pc_rocket_tool.ui.alert_dialog import AlertDialog
from mk96_app_pc_rocket_tool.ui.common import play_button
from mk96_app_pc_rocket_tool.ui.servers_panel import Tone

TIME_FORMAT = "HH:mm"


def describe_rule(rule: Rule, room_names: dict[str, str]) -> str:
    sound = display_name(rule.sound_id)
    if rule.type is RuleType.MENTION:
        extra = " (+ @all/@here)" if rule.mention_all else ""
        return f"Mención global{extra} → {sound}"
    room_id = rule.room_id or ""
    return f"{room_names.get(room_id, rule.label or room_id)} → {sound}"


def _time_edit(hhmm: str) -> QTimeEdit:
    edit = QTimeEdit(QTime.fromString(hhmm, TIME_FORMAT))
    edit.setDisplayFormat(TIME_FORMAT)
    return edit


@dataclass(frozen=True, slots=True)
class AlertActions:
    config_changed: Callable[[], None]
    open_servers: Callable[[], None]
    preview: Callable[[str], None]


class AlertsPanel(QWidget):
    def __init__(self, config: AppConfig, actions: AlertActions):
        super().__init__()
        self._config = config
        self._actions = actions
        self._rooms_by_server: dict[str, list[RoomInfo]] = {}
        self._states: dict[str, ConnectionState] = {}
        self._selected_id: str | None = None

        self._server_combo = QComboBox()
        self._server_combo.currentIndexChanged.connect(self._on_server_changed)
        top = QHBoxLayout()
        top.addWidget(QLabel("Servidor:"))
        top.addWidget(self._server_combo, 1)

        # Only the alert list scrolls; Do Not Disturb stays fixed below it.
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._content, 1)
        layout.addWidget(self._build_quiet_section())
        self._refresh_server_combo()

    # -- Pushed by the controller -------------------------------------------------------------

    def set_server_status(self, server_id: str, state: ConnectionState) -> None:
        was_connected = self._states.get(server_id) is ConnectionState.CONNECTED
        self._states[server_id] = state
        self._refresh_server_combo()
        if server_id == self._selected_id and was_connected != (state is ConnectionState.CONNECTED):
            self._show_server(server_id)

    def set_known_rooms(self, server_id: str, rooms: list[RoomInfo]) -> None:
        self._rooms_by_server[server_id] = rooms

    def config_replaced(self) -> None:
        """Rules or servers changed elsewhere (e.g. a toggle in the tray menu)."""
        self._refresh_server_combo()
        self._show_server(self._selected_id)

    # -- Server selector ----------------------------------------------------------------------

    def _refresh_server_combo(self) -> None:
        self._server_combo.blockSignals(True)
        self._server_combo.clear()
        for server in self._config.servers:
            connected = self._states.get(server.id) is ConnectionState.CONNECTED
            self._server_combo.addItem(
                server.name if connected else f"{server.name} (desconectado)", server.id
            )
        ids = [s.id for s in self._config.servers]
        keep = self._selected_id if self._selected_id in ids else (ids[0] if ids else None)
        if keep is not None:
            self._server_combo.setCurrentIndex(ids.index(keep))
        self._server_combo.blockSignals(False)
        if keep != self._selected_id or not self._content_layout.count():
            self._show_server(keep)

    def _on_server_changed(self, index: int) -> None:
        self._show_server(self._server_combo.itemData(index) if index >= 0 else None)

    def _show_server(self, server_id: str | None) -> None:
        self._selected_id = server_id
        while (item := self._content_layout.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        if server_id is None:
            self._content_layout.addWidget(self._no_servers_hint())
        else:
            self._content_layout.addWidget(self._rules_section(server_id))

    def _no_servers_hint(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.addWidget(
            QLabel(
                "Todavía no hay ningún servidor. Añade uno para poder crear alertas.", wordWrap=True
            )
        )
        button = QPushButton("Ir a Servidores")
        button.clicked.connect(self._actions.open_servers)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return box

    # -- Rules --------------------------------------------------------------------------------

    def _rules_section(self, server_id: str) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        rules = self._config.rules_for_server(server_id)

        layout.addWidget(self._heading("Mención global"))
        mention = next((r for r in rules if r.type is RuleType.MENTION), None)
        card = QFrame()
        card.setObjectName("mentionCard")
        card.setStyleSheet(
            "#mentionCard { background: rgba(0, 120, 212, 0.12);"
            " border: 1px solid rgba(0, 120, 212, 0.5); border-radius: 6px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(4, 4, 4, 4)
        if mention is not None:
            card_layout.addWidget(self._rule_row(server_id, mention))
        else:
            empty = QHBoxLayout()
            empty.addWidget(QLabel("Sin configurar"), 1)
            configure = QPushButton("Configurar")
            configure.clicked.connect(lambda: self._open_dialog(server_id, None, RuleType.MENTION))
            empty.addWidget(configure)
            card_layout.addLayout(empty)
        layout.addWidget(card)

        layout.addWidget(self._heading("Alertas de sala"))
        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(0, 0, 0, 0)
        room_rules = [r for r in rules if r.type is RuleType.ROOM]
        for rule in room_rules:
            list_layout.addWidget(self._rule_row(server_id, rule))
        if not room_rules:
            placeholder = QLabel("Este servidor todavía no tiene alertas de sala.")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(f"color: {Tone.MUTED}; padding: 24px;")
            list_layout.addWidget(placeholder)
        list_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(list_widget)
        layout.addWidget(scroll, 1)

        connected = self._states.get(server_id) is ConnectionState.CONNECTED
        add_row = QHBoxLayout()
        add_button = QPushButton("+ Añadir alerta de sala")
        add_button.setEnabled(connected)
        add_button.clicked.connect(lambda: self._open_dialog(server_id, None, RuleType.ROOM))
        add_row.addWidget(add_button)
        if not connected:
            hint = QLabel("Conecta este servidor para poder crear alertas de sala.")
            hint.setStyleSheet(f"color: {Tone.MUTED};")
            add_row.addWidget(hint)
        add_row.addStretch(1)
        layout.addLayout(add_row)
        return box

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    def _rule_row(self, server_id: str, rule: Rule) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 2, 6, 2)
        active = QCheckBox()
        active.setChecked(rule.active)
        active.setToolTip("Activar o silenciar esta alerta")
        active.toggled.connect(lambda checked: self._set_active(rule.id, checked))
        text = describe_rule(rule, self._config.room_names)
        label = QLabel(text if rule.active else f"{text} (silenciada)")
        label.setToolTip(text)
        if not rule.active:
            label.setStyleSheet(f"color: {Tone.MUTED};")
        preview = play_button()
        preview.setToolTip("Probar este sonido")
        preview.setFixedWidth(32)
        preview.clicked.connect(lambda: self._actions.preview(rule.sound_id))
        edit = QPushButton("Editar")
        edit.clicked.connect(lambda: self._open_dialog(server_id, rule, rule.type))
        remove = QPushButton("Quitar")
        remove.clicked.connect(lambda: self._remove(rule.id))
        layout.addWidget(active)
        layout.addWidget(label, 1)
        layout.addWidget(preview)
        layout.addWidget(edit)
        layout.addWidget(remove)
        return row

    def _set_active(self, rule_id: str, active: bool) -> None:
        rule = next(r for r in self._config.rules if r.id == rule_id)
        self._config.replace_rule(replace(rule, active=active))
        self._actions.config_changed()
        self._show_server(self._selected_id)

    def _remove(self, rule_id: str) -> None:
        self._config.rules = [r for r in self._config.rules if r.id != rule_id]
        self._actions.config_changed()
        self._show_server(self._selected_id)

    def _open_dialog(self, server_id: str, rule: Rule | None, rule_type: RuleType) -> None:
        dialog = AlertDialog(
            self,
            self._config,
            server_id,
            self._rooms_by_server.get(server_id, []),
            self._actions.preview,
            rule,
            rule_type,
        )
        if not dialog.exec() or dialog.result_data is None:
            return
        data = dialog.result_data
        if rule is not None:
            edited = rule
        elif rule_type is RuleType.MENTION:
            edited = Rule(new_id(), server_id, RuleType.MENTION, data.sound_id, label="Mención")
        elif data.room is not None:
            room = data.room
            known_type = room.type in RoomType._value2member_map_
            edited = Rule(
                new_id(),
                server_id,
                RuleType.ROOM,
                data.sound_id,
                room_id=room.room_id,
                room_type=RoomType(room.type) if known_type and room.type else None,
                label=room.name,
            )
            self._config.room_names[room.room_id] = room.name
        else:
            return
        edited = replace(
            edited,
            sound_id=data.sound_id,
            repeat_mode=data.repeat_mode,
            repeat_count=data.repeat_count,
            repeat_every_polls=data.repeat_every_polls,
            mention_all=data.mention_all,
        )
        if rule is not None:
            self._config.replace_rule(edited)
        else:
            self._config.rules.append(edited)
        self._actions.config_changed()
        self._show_server(server_id)

    # -- Quiet schedule -----------------------------------------------------------------------

    def _build_quiet_section(self) -> QGroupBox:
        quiet = self._config.quiet
        group = QGroupBox("No molestar")
        self._schedule_check = QCheckBox("Horario silencioso")
        self._schedule_check.setToolTip(
            "Sin sonido ni notificación de chats dentro de este horario (siguen apareciendo en "
            "la bandeja). Los recordatorios de reuniones no se silencian. Para silenciar a mano, "
            "usa «No molestar» en el menú de la bandeja."
        )
        self._schedule_check.setChecked(quiet.schedule_enabled)
        self._start = _time_edit(quiet.schedule_start)
        self._end = _time_edit(quiet.schedule_end)
        row = QHBoxLayout()
        row.addWidget(self._schedule_check)
        row.addSpacing(16)
        row.addWidget(QLabel("Desde"))
        row.addWidget(self._start)
        row.addWidget(QLabel("hasta"))
        row.addWidget(self._end)
        row.addStretch(1)
        group.setLayout(row)
        self._sync_quiet_enabled()
        self._schedule_check.toggled.connect(self._save_quiet)
        self._start.timeChanged.connect(self._save_quiet)
        self._end.timeChanged.connect(self._save_quiet)
        return group

    def _sync_quiet_enabled(self) -> None:
        enabled = self._schedule_check.isChecked()
        self._start.setEnabled(enabled)
        self._end.setEnabled(enabled)

    def _save_quiet(self) -> None:
        self._sync_quiet_enabled()
        self._config.quiet = replace(
            self._config.quiet,
            schedule_enabled=self._schedule_check.isChecked(),
            schedule_start=self._start.time().toString(TIME_FORMAT),
            schedule_end=self._end.time().toString(TIME_FORMAT),
        )
        self._actions.config_changed()
