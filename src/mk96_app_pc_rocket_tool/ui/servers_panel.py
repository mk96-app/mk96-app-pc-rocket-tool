"""Servers tab: saved Rocket.Chat servers with a status dot, the connection form of the
selected one (name, URL, user id, token, poll interval; test, save, connect/disconnect,
delete) and the "start with Windows" switch. Several servers can be connected at once."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mk96_app_pc_rocket_tool import autostart, secret_store
from mk96_app_pc_rocket_tool.models import AppConfig, ConnectionState, Server, new_id
from mk96_app_pc_rocket_tool.rest_client import (
    RestApiError,
    check_connection,
    describe_connection_error,
    validate_server_url,
)
from mk96_app_pc_rocket_tool.secret_store import SecretStoreUnavailable, ServerCredentials
from mk96_app_pc_rocket_tool.ui.common import confirm_destructive, show_error
from mk96_app_pc_rocket_tool.workers import run_in_thread

logger = logging.getLogger(__name__)

POLL_INTERVALS_S = (15, 30, 60)
DEFAULT_POLL_INTERVAL_S = 30
SERVER_ID_ROLE = Qt.ItemDataRole.UserRole


class Tone:
    """Colors of status texts; readable on light and dark themes."""

    OK = "#2ea043"
    ERROR = "#e5534b"
    MUTED = "#8b949e"


STATUS_DOT_COLORS = {
    ConnectionState.CONNECTED: Tone.OK,
    ConnectionState.DISCONNECTED: Tone.MUTED,
    ConnectionState.ERROR: Tone.ERROR,
}


def server_status_text(state: ConnectionState | None, detail: str | None) -> tuple[str, str | None]:
    """(text, color) of a server's connection status. `detail` is the username when
    connected, or the error message when failing."""
    if state is ConnectionState.CONNECTED:
        return (f"Conexión abierta: @{detail}", Tone.OK) if detail else ("Conectando...", None)
    if state is ConnectionState.ERROR:
        return detail or "Sin conexión.", Tone.ERROR
    if state is ConnectionState.DISCONNECTED:
        return "Conexión cerrada", Tone.MUTED
    return "", None


def probe_ok_text(username: str) -> str:
    return f"Conexión correcta: @{username}"


def delete_confirmation_text(server_name: str, rule_count: int) -> tuple[str, str]:
    """(title, body) of the delete-server confirmation."""
    if rule_count == 0:
        rules = ""
    elif rule_count == 1:
        rules = "su regla de alerta y "
    else:
        rules = f"sus {rule_count} reglas de alerta y "
    return (
        f"¿Eliminar el servidor «{server_name}»?",
        f"Se borrarán {rules}las credenciales guardadas. No se puede deshacer.",
    )


def missing_fields(url: str, user_id: str, token: str) -> list[str]:
    return [
        name
        for name, value in (("la URL", url), ("el User Id", user_id), ("el token", token))
        if not value
    ]


def _dot_icon(color: str) -> QIcon:
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(1, 1, 10, 10)
    painter.end()
    return QIcon(pixmap)


def _set_colored(label: QLabel, text: str, color: str | None) -> None:
    label.setText(text)
    label.setStyleSheet(f"color: {color};" if color else "")


@dataclass(frozen=True, slots=True)
class ServerActions:
    config_changed: Callable[[], None]
    connect: Callable[[str], None]
    disconnect: Callable[[str], None]
    delete_credentials: Callable[[str], None]


class ServersPanel(QWidget):
    def __init__(self, config: AppConfig, actions: ServerActions):
        super().__init__()
        self._config = config
        self._actions = actions
        self._states: dict[str, ConnectionState] = {}
        self._details: dict[str, str | None] = {}
        self._selected_id: str | None = None
        self._probe_generation = 0  # stale test results (form changed meanwhile) are dropped

        self._list = QListWidget()
        self._list.setIconSize(QSize(12, 12))
        self._list.currentItemChanged.connect(self._on_list_selection)
        add_button = QPushButton("+ Añadir servidor")
        add_button.clicked.connect(lambda: self._select(None))
        self._autostart = QCheckBox("Iniciar con Windows")
        self._autostart.setToolTip(
            "Arranca la aplicación al iniciar sesión en Windows, para que el icono de la "
            "bandeja esté siempre. No cambia qué servidores se conectan."
        )
        self._autostart.setChecked(autostart.is_enabled())
        self._autostart.toggled.connect(self._on_autostart_toggled)

        left = QVBoxLayout()
        left.addWidget(QLabel("Servidores guardados"))
        left.addWidget(self._list, 1)
        left.addWidget(add_button)
        left.addSpacing(8)
        left.addWidget(self._autostart)

        self._form = self._build_form()
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)

        layout = QHBoxLayout(self)
        layout.addLayout(left, 2)
        layout.addWidget(separator)
        layout.addWidget(self._form, 3)

        self._refresh_list()
        self._select(config.servers[0].id if config.servers else None)

    # -- Status pushed by the controller ------------------------------------------------------

    def set_server_status(self, server_id: str, state: ConnectionState, detail: str | None) -> None:
        self._states[server_id] = state
        self._details[server_id] = detail
        self._refresh_list()
        if server_id == self._selected_id:
            self._render_status()

    def config_replaced(self) -> None:
        """The server list changed elsewhere."""
        self._refresh_list()
        if self._selected_id is not None and self._config.server(self._selected_id) is None:
            self._select(None)

    # -- List ---------------------------------------------------------------------------------

    def _refresh_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for server in self._config.servers:
            state = self._states.get(server.id, ConnectionState.DISCONNECTED)
            item = QListWidgetItem(_dot_icon(STATUS_DOT_COLORS[state]), server.name)
            item.setData(SERVER_ID_ROLE, server.id)
            self._list.addItem(item)
            if server.id == self._selected_id:
                self._list.setCurrentItem(item)
        self._list.blockSignals(False)

    def _on_list_selection(self, current: QListWidgetItem | None, _previous: object) -> None:
        if current is not None:
            self._select(current.data(SERVER_ID_ROLE))

    def _on_autostart_toggled(self, enabled: bool) -> None:
        try:
            autostart.set_enabled(enabled)
        except OSError as exc:
            logger.exception("Could not change autostart")
            show_error(self, "No se pudo cambiar el inicio con Windows", str(exc))
            self._autostart.blockSignals(True)
            self._autostart.setChecked(not enabled)
            self._autostart.blockSignals(False)

    # -- Form ---------------------------------------------------------------------------------

    def _build_form(self) -> QWidget:
        form = QWidget()
        self._form_title = QLabel()
        font = self._form_title.font()
        font.setPointSizeF(font.pointSizeF() * 1.2)
        font.setBold(True)
        self._form_title.setFont(font)

        self._toggle_button = QPushButton()
        self._toggle_button.clicked.connect(self._on_toggle_connection)
        self._status_label = QLabel(wordWrap=True)
        self._status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._status_row = QWidget()
        status_layout = QHBoxLayout(self._status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.addWidget(self._toggle_button)
        status_layout.addWidget(self._status_label, 1)

        self._name = QLineEdit(placeholderText="Trabajo, Personal...")
        self._url = QLineEdit(placeholderText="https://chat.miempresa.com")
        self._user_id = QLineEdit(placeholderText="Mi cuenta > Tokens de acceso personal")
        self._token = QLineEdit(placeholderText="Personal Access Token")
        self._token.setEchoMode(QLineEdit.EchoMode.Password)
        show_token = QPushButton("Mostrar")
        show_token.setCheckable(True)
        show_token.toggled.connect(self._on_show_token)
        self._show_token = show_token
        token_row = QHBoxLayout()
        token_row.addWidget(self._token, 1)
        token_row.addWidget(show_token)
        self._interval = QComboBox()
        for seconds in POLL_INTERVALS_S:
            self._interval.addItem(f"{seconds} segundos", seconds)

        fields = QFormLayout()
        fields.addRow("Nombre:", self._name)
        fields.addRow("URL del servidor:", self._url)
        fields.addRow("User Id:", self._user_id)
        fields.addRow("Token personal:", token_row)
        fields.addRow("Comprobar cada:", self._interval)

        self._test_button = QPushButton("Probar conexión")
        self._test_button.setToolTip(
            "Comprueba la URL, el User Id y el token escritos, sin guardar nada."
        )
        self._test_button.clicked.connect(self._test_connection)
        self._test_result = QLabel(wordWrap=True)
        self._test_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        test_row = QHBoxLayout()
        test_row.addWidget(self._test_button)
        test_row.addWidget(self._test_result, 1)
        for edit in (self._url, self._user_id, self._token):
            # An old ✔ must not survive editing what was tested.
            edit.textEdited.connect(self._clear_test_result)

        self._save_button = QPushButton("Guardar")
        self._save_button.setDefault(True)
        self._save_button.clicked.connect(self._save)
        self._delete_button = QPushButton("Eliminar servidor")
        self._delete_button.clicked.connect(self._confirm_delete)
        actions_row = QHBoxLayout()
        actions_row.addWidget(self._save_button)
        actions_row.addStretch(1)
        actions_row.addWidget(self._delete_button)

        layout = QVBoxLayout(form)
        layout.addWidget(self._form_title)
        layout.addWidget(self._status_row)
        layout.addLayout(fields)
        layout.addLayout(test_row)
        layout.addStretch(1)
        layout.addLayout(actions_row)
        form.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return form

    def _select(self, server_id: str | None) -> None:
        self._selected_id = server_id
        self._probe_generation += 1
        server = self._config.server(server_id) if server_id else None
        credentials = None
        if server is not None:
            try:
                credentials = secret_store.load_server_credentials(server.id)
            except SecretStoreUnavailable as exc:
                show_error(self, "No se pudo leer la credencial guardada", str(exc))

        self._form_title.setText(server.name if server else "Nuevo servidor")
        self._status_row.setVisible(server is not None)
        self._delete_button.setVisible(server is not None)
        self._name.setText(server.name if server else "")
        self._url.setText(credentials.server_url if credentials else "")
        self._user_id.setText(credentials.user_id if credentials else "")
        self._token.setText(credentials.token if credentials else "")
        self._show_token.setChecked(False)
        interval = server.poll_interval_s if server else DEFAULT_POLL_INTERVAL_S
        self._interval.setCurrentIndex(max(0, self._interval.findData(interval)))
        self._clear_test_result()
        self._render_status()
        self._refresh_list()
        self._name.setFocus()

    def _render_status(self) -> None:
        if self._selected_id is None:
            return
        state = self._states.get(self._selected_id, ConnectionState.DISCONNECTED)
        text, color = server_status_text(state, self._details.get(self._selected_id))
        _set_colored(self._status_label, text, color)
        self._toggle_button.setText(
            "Desconectar" if state is ConnectionState.CONNECTED else "Conectar"
        )

    def _on_toggle_connection(self) -> None:
        if self._selected_id is None:
            return
        if self._states.get(self._selected_id) is ConnectionState.CONNECTED:
            self._actions.disconnect(self._selected_id)
        else:
            self._actions.connect(self._selected_id)

    def _on_show_token(self, shown: bool) -> None:
        self._token.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)
        self._show_token.setText("Ocultar" if shown else "Mostrar")

    def _clear_test_result(self) -> None:
        _set_colored(self._test_result, "", None)

    def _form_values(self) -> tuple[str, str, str, str]:
        return (
            self._name.text().strip(),
            self._url.text().strip(),
            self._user_id.text().strip(),
            self._token.text().strip(),
        )

    def _test_connection(self) -> None:
        _name, url, user_id, token = self._form_values()
        missing = missing_fields(url, user_id, token)
        if missing:
            _set_colored(self._test_result, f"Falta rellenar {', '.join(missing)}.", Tone.ERROR)
            return
        url_error = validate_server_url(url)
        if url_error:
            _set_colored(self._test_result, url_error, Tone.ERROR)
            return

        self._test_button.setEnabled(False)
        _set_colored(self._test_result, "Probando...", None)
        self._probe_generation += 1
        generation = self._probe_generation

        def done(username: str | None, error: BaseException | None) -> None:
            self._test_button.setEnabled(True)
            if generation != self._probe_generation:
                return  # another server was selected meanwhile
            if isinstance(error, RestApiError):
                _set_colored(self._test_result, describe_connection_error(error), Tone.ERROR)
            elif error is not None:
                logger.error("Unexpected error testing a connection", exc_info=error)
                _set_colored(self._test_result, f"Error inesperado: {error}", Tone.ERROR)
            else:
                _set_colored(self._test_result, probe_ok_text(username or ""), Tone.OK)

        run_in_thread(lambda: check_connection(url, user_id, token), done)

    def _save(self) -> None:
        name, url, user_id, token = self._form_values()
        missing = ([] if name else ["el nombre"]) + missing_fields(url, user_id, token)
        if missing:
            _set_colored(self._test_result, f"Falta rellenar {', '.join(missing)}.", Tone.ERROR)
            return
        url_error = validate_server_url(url)
        if url_error:
            _set_colored(self._test_result, url_error, Tone.ERROR)
            return

        interval = int(self._interval.currentData())
        existing = self._config.server(self._selected_id) if self._selected_id else None
        server = (
            replace(existing, name=name, poll_interval_s=interval)
            if existing
            else Server(new_id(), name, active=False, poll_interval_s=interval)
        )
        try:
            secret_store.save_server_credentials(server.id, ServerCredentials(url, user_id, token))
        except SecretStoreUnavailable as exc:
            show_error(self, "No se pudo guardar la credencial", str(exc))
            return

        if existing:
            self._config.replace_server(server)
        else:
            self._config.servers.append(server)
        self._actions.config_changed()
        self._select(server.id)
        _set_colored(self._test_result, "Guardado.", Tone.OK)

    def _confirm_delete(self) -> None:
        server = self._config.server(self._selected_id) if self._selected_id else None
        if server is None:
            return
        title, body = delete_confirmation_text(
            server.name, len(self._config.rules_for_server(server.id))
        )
        if not confirm_destructive(self, title, body, "Eliminar"):
            return
        self._actions.disconnect(server.id)
        self._actions.delete_credentials(server.id)
        self._config.remove_server(server.id)
        self._states.pop(server.id, None)
        self._actions.config_changed()
        self._select(self._config.servers[0].id if self._config.servers else None)
