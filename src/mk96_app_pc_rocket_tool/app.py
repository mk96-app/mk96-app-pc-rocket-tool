"""Entry point and controller: loads config and credentials, runs one Poller per connected
Rocket.Chat server, turns matching messages into sound + toast + tray entry, runs the meeting
reminders (independent of Rocket.Chat) and owns the windows.

Network calls never run on the GUI thread: the pollers use their own worker thread and every
other request goes through workers.run_in_thread.
"""

import ctypes
import logging
import logging.handlers
import sys
from dataclasses import dataclass, replace
from datetime import datetime

from PySide6.QtCore import QEvent, QMessageLogContext, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from mk96_app_pc_rocket_tool import (
    APP_ID,
    APP_NAME,
    __version__,
    paths,
    secret_store,
    single_instance,
)
from mk96_app_pc_rocket_tool.config import ConfigError, load_config, save_config
from mk96_app_pc_rocket_tool.meeting_scheduler import MeetingScheduler
from mk96_app_pc_rocket_tool.meetings import (
    DEFAULT_MEETING_SOUND_ID,
    DueReminder,
    upcoming_meetings,
)
from mk96_app_pc_rocket_tool.models import AppConfig, ChatEvent, ConnectionState, RuleType
from mk96_app_pc_rocket_tool.notifier import meeting_reminder_text, special_message_text
from mk96_app_pc_rocket_tool.poller import Poller
from mk96_app_pc_rocket_tool.quiet_hours import (
    SilenceOption,
    has_manual_silence,
    is_quiet,
    quiet_status_text,
    silence_until,
)
from mk96_app_pc_rocket_tool.rest_client import (
    PRESENCE_LABELS,
    Presence,
    RestApiError,
    RestClient,
    is_insecure_url,
)
from mk96_app_pc_rocket_tool.rules import match_rule, rooms_to_mark_read
from mk96_app_pc_rocket_tool.secret_store import SecretStoreUnavailable
from mk96_app_pc_rocket_tool.sound_player import SoundPlaybackError, SoundPlayer
from mk96_app_pc_rocket_tool.tray import (
    ConnectionSummary,
    PendingMeetingAlert,
    RecentNotification,
    Tray,
    TrayActions,
    UpcomingMeeting,
    build_status_lines,
)
from mk96_app_pc_rocket_tool.ui.alerts_panel import AlertActions
from mk96_app_pc_rocket_tool.ui.chat_window import ChatWindow
from mk96_app_pc_rocket_tool.ui.common import open_folder, open_web_link
from mk96_app_pc_rocket_tool.ui.meetings_panel import MeetingActions
from mk96_app_pc_rocket_tool.ui.servers_panel import ServerActions
from mk96_app_pc_rocket_tool.ui.settings_window import SettingsPage, SettingsWindow
from mk96_app_pc_rocket_tool.workers import init_gui_dispatcher, run_in_thread

logger = logging.getLogger(__name__)

UPCOMING_MEETINGS_IN_TRAY = 3
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 3


@dataclass(slots=True)
class Connection:
    client: RestClient
    poller: Poller
    connected: bool = True
    username: str | None = None
    error: str | None = None


class Application:
    def __init__(self, config: AppConfig, app_icon: QIcon):
        self._config = config
        self._app_icon = app_icon
        self._connections: dict[str, Connection] = {}
        self._room_origin: dict[str, tuple[str, str]] = {}  # room_id -> (server_id, room_type)
        self._settings: SettingsWindow | None = None
        self._chats: dict[str, ChatWindow] = {}

        self._tray = Tray(
            TrayActions(
                open_settings=self._open_settings_page,
                open_chat=self._open_chat,
                open_link=open_web_link,
                toggle_rule=self._toggle_rule,
                mark_read=self._ask_mark_read,
                open_sounds_folder=lambda: open_folder(paths.user_sounds_dir()),
                set_presence=self._set_presence,
                set_quiet=self._set_quiet,
                quit=QApplication.quit,
            )
        )
        self._sounds = SoundPlayer(lambda message: self._notify_error("Sonido", message))
        self._scheduler = MeetingScheduler(
            lambda: self._config.meetings, self._on_meeting_due, self._on_scheduler_tick
        )
        self._scheduler.start()
        self._on_scheduler_tick()

        active = [server for server in config.servers if server.active]
        for server in active:
            self._connect(server.id)
        self._refresh_tray_rules()
        if not config.servers:
            self._open_settings_page(None)  # first run: nothing works until a server exists

    # -- Helpers -------------------------------------------------------------------------------

    def _save(self) -> None:
        try:
            save_config(self._config, paths.config_path())
        except OSError as exc:
            logger.exception("Could not save the configuration")
            self._notify_error("No se pudo guardar la configuración", str(exc))

    def _notify_error(self, title: str, body: str) -> None:
        self._tray.notify(title, body, is_error=True)

    def _server_name(self, server_id: str) -> str:
        server = self._config.server(server_id)
        return server.name if server else server_id

    def _play(self, sound_id: str) -> None:
        try:
            self._sounds.play(sound_id)
        except SoundPlaybackError as exc:
            self._notify_error("No se pudo reproducir el sonido", str(exc))

    # -- Connections ---------------------------------------------------------------------------

    def _connect(self, server_id: str) -> None:
        server = self._config.server(server_id)
        if server is None:
            return
        try:
            credentials = secret_store.load_server_credentials(server_id)
        except SecretStoreUnavailable as exc:
            self._notify_error("Credenciales no disponibles", str(exc))
            return
        if credentials is None:
            self._notify_error(
                "Falta la credencial", f"{server.name} no tiene credenciales guardadas."
            )
            return

        previous = self._connections.pop(server_id, None)
        if previous is not None:
            previous.poller.stop()
        if is_insecure_url(credentials.server_url):
            self._tray.notify(
                "Conexión sin cifrar",
                f"{server.name} usa http://: el token viajaría en claro. "
                "Usa https:// si el servidor lo permite.",
            )

        if not server.active:
            self._config.replace_server(replace(server, active=True))
            self._save()
        client = RestClient(credentials.server_url, credentials.user_id, credentials.token)
        poller = Poller(
            client,
            server.poll_interval_s,
            lambda: self._config.rules_for_server(server_id),
            lambda event: self._on_event(server_id, event),
            lambda connected: self._on_connection_changed(server_id, connected),
            self._tray.remove_recent_notification,
            lambda: self._on_auth_error(server_id),
        )
        self._connections[server_id] = Connection(client, poller)
        logger.info("Connecting to server %s", server_id)
        poller.start()
        self._refresh_known_rooms(server_id)
        self._push_status(server_id, ConnectionState.CONNECTED, None)
        self._update_tray_connection()
        self._refresh_tray_rules()

    def _disconnect(self, server_id: str) -> None:
        connection = self._connections.pop(server_id, None)
        if connection is not None:
            connection.poller.stop()
            logger.info("Disconnected from server %s", server_id)
        server = self._config.server(server_id)
        if server is not None and server.active:
            self._config.replace_server(replace(server, active=False))
            self._save()
        self._push_status(server_id, ConnectionState.DISCONNECTED, None)
        self._update_tray_connection()
        self._refresh_tray_rules()

    def _delete_credentials(self, server_id: str) -> None:
        try:
            secret_store.clear_server_credentials(server_id)
        except SecretStoreUnavailable as exc:
            self._notify_error("No se pudo borrar la credencial", str(exc))

    def _on_connection_changed(self, server_id: str, connected: bool) -> None:
        connection = self._connections.get(server_id)
        if connection is None:
            return
        connection.connected = connected
        connection.username = connection.poller.username if connected else None
        connection.error = None if connected else connection.poller.last_error
        state = ConnectionState.CONNECTED if connected else ConnectionState.ERROR
        self._push_status(server_id, state, connection.username if connected else connection.error)
        self._update_tray_connection()
        if not connected:
            logger.warning("Lost connection with server %s: %s", server_id, connection.error)
            self._notify_error(
                f"Sin conexión con {self._server_name(server_id)}",
                "Se reintentará automáticamente.",
            )

    def _on_auth_error(self, server_id: str) -> None:
        connection = self._connections.get(server_id)
        error = connection.poller.last_error if connection else None
        if connection is not None:
            connection.connected, connection.username, connection.error = False, None, error
        logger.warning("Server %s rejected the credentials", server_id)
        self._push_status(server_id, ConnectionState.ERROR, error)
        self._update_tray_connection()
        self._notify_error(
            f"Token no válido en {self._server_name(server_id)}",
            "El servidor rechazó las credenciales (quizá se revocó el token). "
            "Actualízalas en Configuración y pulsa Conectar.",
        )

    def _push_status(self, server_id: str, state: ConnectionState, detail: str | None) -> None:
        if self._settings is not None:
            self._settings.set_server_status(server_id, state, detail)

    def _update_tray_connection(self) -> None:
        summaries = [
            ConnectionSummary(self._server_name(sid), c.connected, c.username, c.error)
            for sid, c in self._connections.items()
        ]
        any_connected = any(c.connected for c in self._connections.values())
        self._tray.set_connection_status(any_connected, build_status_lines(summaries))

    def _refresh_known_rooms(self, server_id: str) -> None:
        connection = self._connections.get(server_id)
        if connection is None:
            return

        def done(rooms: object, error: BaseException | None) -> None:
            if error is not None:
                logger.warning("Could not list rooms of server %s: %s", server_id, error)
                self._notify_error("No se pudieron listar las salas", str(error))
            elif self._settings is not None and isinstance(rooms, list):
                self._settings.set_known_rooms(server_id, rooms)

        run_in_thread(connection.client.list_rooms, done)

    # -- Rules and events ------------------------------------------------------------------------

    def _refresh_tray_rules(self) -> None:
        several = len(self._connections) > 1
        rules = []
        for server_id in self._connections:
            for rule in self._config.rules_for_server(server_id):
                label = rule.label or rule.room_id or "Mención"
                if several:
                    label = f"{self._server_name(server_id)} · {label}"
                rules.append(replace(rule, label=label))
        self._tray.set_rules(rules)

    def _toggle_rule(self, rule_id: str, active: bool) -> None:
        for rule in self._config.rules:
            if rule.id == rule_id:
                self._config.replace_rule(replace(rule, active=active))
        self._config_changed()

    def _on_event(self, server_id: str, event: ChatEvent) -> None:
        rule = match_rule(event, self._config.rules_for_server(server_id))
        if rule is None:
            return
        self._room_origin[event.room_id] = (server_id, event.room_type)
        label = (
            rule.label
            if rule.type is RuleType.ROOM and rule.label
            else (event.sender or "Rocket.Chat")
        )
        if rule.type is RuleType.MENTION:
            room_name = self._config.room_names.get(event.room_id)
            label = f"Mención de {event.sender}" + (f" en {room_name}" if room_name else "")
        logger.info("Alert for room %s on server %s (rule %s)", event.room_id, server_id, rule.id)
        # While silenced the chat still shows up in the tray, without sound or toast.
        if not is_quiet(self._config.quiet, datetime.now()):
            self._play(rule.sound_id)
            title, body = special_message_text(label, event.sender, event.text)
            self._tray.notify(title, body, room_id=event.room_id)
        self._tray.add_recent_notification(RecentNotification(label, event.room_id))

    def _ask_mark_read(self) -> None:
        box = QMessageBox(
            QMessageBox.Icon.Question,
            "Marcar como leído",
            "¿Qué chats quieres marcar como leídos?\n\n"
            "Todos: todas las salas con mensajes sin leer.\n"
            "No configurados: solo las que no tienen una alerta de sala activa.",
        )
        everything = box.addButton("Todos", QMessageBox.ButtonRole.AcceptRole)
        unconfigured = box.addButton("No configurados", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setWindowIcon(self._app_icon)
        box.exec()
        if box.clickedButton() is everything:
            self._mark_rooms_read(only_unconfigured=False)
        elif box.clickedButton() is unconfigured:
            self._mark_rooms_read(only_unconfigured=True)

    def _mark_rooms_read(self, only_unconfigured: bool) -> None:
        jobs = [
            (connection.client, self._config.rules_for_server(server_id))
            for server_id, connection in self._connections.items()
            if connection.connected
        ]
        if not jobs:
            return

        def work() -> tuple[int, int, list[str]]:
            marked, total, errors = 0, 0, []
            for client, rules in jobs:
                try:
                    room_ids = rooms_to_mark_read(
                        client.list_subscriptions(), rules, only_unconfigured
                    )
                except RestApiError as exc:
                    errors.append(f"No se pudieron leer los chats: {exc}")
                    continue
                total += len(room_ids)
                for room_id in room_ids:
                    try:
                        client.mark_room_read(room_id)
                        marked += 1
                    except RestApiError as exc:
                        errors.append(str(exc))
            return marked, total, errors

        def done(result: tuple[int, int, list[str]] | None, error: BaseException | None) -> None:
            if error is not None or result is None:
                logger.error("Mark as read failed", exc_info=error)
                self._notify_error("Error al marcar leídos", str(error))
                return
            marked, total, errors = result
            if errors:
                self._notify_error(
                    "Error al marcar leídos", f"{marked}/{total} marcados. Error: {errors[0]}"
                )
            else:
                self._tray.notify(
                    "Chats leídos", f"{marked} chat(s) marcados." if marked else "Nada sin leer."
                )

        run_in_thread(work, done)

    def _set_presence(self, presence: Presence) -> None:
        clients = [c.client for c in self._connections.values() if c.connected]
        if not clients:
            return

        def work() -> list[str]:
            errors = []
            for client in clients:
                try:
                    client.set_presence(presence)
                except RestApiError as exc:
                    errors.append(str(exc))
            return errors

        def done(errors: list[str] | None, error: BaseException | None) -> None:
            problems = [str(error)] if error is not None else (errors or [])
            if problems:
                self._notify_error("Error al cambiar el estado", problems[0])
            else:
                self._tray.notify("Estado", PRESENCE_LABELS[presence])

        run_in_thread(work, done)

    # -- Do Not Disturb ------------------------------------------------------------------------

    def _set_quiet(self, option: SilenceOption) -> None:
        quiet = self._config.quiet
        self._config.quiet = replace(quiet, silenced_until=silence_until(datetime.now(), option))
        self._save()
        self._refresh_quiet()

    def _refresh_quiet(self) -> None:
        now = datetime.now()
        self._tray.set_quiet(
            quiet_status_text(self._config.quiet, now), has_manual_silence(self._config.quiet, now)
        )

    # -- Meetings ---------------------------------------------------------------------------------

    def _on_meeting_due(self, reminder: DueReminder) -> None:
        meeting = reminder.meeting
        logger.info("Meeting reminder %s (%s)", meeting.id, reminder.moment)
        self._play(meeting.sound_id or DEFAULT_MEETING_SOUND_ID)
        title, body = meeting_reminder_text(
            meeting.title, f"{reminder.occurrence:%H:%M}", meeting.link, reminder.moment
        )
        self._tray.notify(title, body)
        self._tray.add_pending_meeting(PendingMeetingAlert(meeting.id, meeting.title, meeting.link))

    def _on_scheduler_tick(self) -> None:
        self._refresh_upcoming()
        self._refresh_quiet()
        self._tray.refresh_icon()

    def _refresh_upcoming(self) -> None:
        upcoming = upcoming_meetings(
            self._config.meetings, datetime.now(), UPCOMING_MEETINGS_IN_TRAY
        )
        self._tray.set_upcoming_meetings(
            [
                UpcomingMeeting(m.id, m.title, f"{occurrence:%d/%m %H:%M}")
                for occurrence, m in upcoming
            ]
        )

    # -- Windows -------------------------------------------------------------------------------

    def _config_changed(self) -> None:
        self._save()
        self._refresh_tray_rules()
        self._refresh_quiet()
        self._refresh_upcoming()
        if self._settings is not None:
            self._settings.config_replaced()

    def _panel_config_changed(self) -> None:
        """A settings panel changed the shared config itself: save and refresh the tray only
        (the panel already shows the change)."""
        self._save()
        self._refresh_tray_rules()
        self._refresh_quiet()
        self._refresh_upcoming()

    def _servers_changed(self) -> None:
        self._panel_config_changed()
        if self._settings is not None:
            self._settings.alerts.config_replaced()

    def open_settings(self) -> None:
        self._open_settings_page(None)

    def _open_settings_page(self, page: str | None) -> None:
        if self._settings is None:
            self._settings = SettingsWindow(
                self._config,
                self._app_icon,
                ServerActions(
                    self._servers_changed, self._connect, self._disconnect, self._delete_credentials
                ),
                AlertActions(
                    self._panel_config_changed,
                    lambda: self._open_settings_page(SettingsPage.SERVERS),
                    self._play,
                ),
                MeetingActions(self._panel_config_changed, self._play),
            )
            self._settings.destroyed.connect(self._on_settings_closed)
            for server_id, connection in self._connections.items():
                state = ConnectionState.CONNECTED if connection.connected else ConnectionState.ERROR
                self._settings.set_server_status(
                    server_id, state, connection.username or connection.error
                )
                self._refresh_known_rooms(server_id)
        if page is not None:
            self._settings.show_page(SettingsPage(page))
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    def _on_settings_closed(self) -> None:
        self._settings = None

    def _open_chat(self, room_id: str) -> None:
        existing = self._chats.get(room_id)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        origin = self._room_origin.get(room_id)
        if origin is None:
            rule = next(
                (
                    r
                    for r in self._config.rules
                    if r.room_id == room_id and r.server_id in self._connections
                ),
                None,
            )
            if rule is not None:
                origin = (rule.server_id, rule.room_type or "c")
        if origin is None or origin[0] not in self._connections:
            self._notify_error("No se puede abrir el chat", "Su servidor no está conectado.")
            return
        server_id, room_type = origin
        title = self._config.room_names.get(room_id, room_id)
        window = ChatWindow(
            self._connections[server_id].client, room_id, room_type, title, self._notify_error
        )
        window.setWindowIcon(self._app_icon)
        window.destroyed.connect(lambda: self._chats.pop(room_id, None))
        self._chats[room_id] = window
        window.show()

    def shutdown(self) -> None:
        for connection in self._connections.values():
            connection.poller.stop()
        self._scheduler.stop()
        self._sounds.shutdown()
        self._tray.hide()
        logger.info("Stopped")


def _configure_logging() -> None:
    log_dir = paths.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            log_dir / "app.log", maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
    ]
    if sys.stderr is not None:  # None in the windowed (no console) build
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )


_QT_LOG_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}


def _forward_qt_message(kind: QtMsgType, _context: QMessageLogContext, message: str) -> None:
    """Qt's own warnings (missing plugins, multimedia backend errors) go to the log file; the
    windowed exe has no console where they would otherwise show up."""
    logging.getLogger("qt").log(_QT_LOG_LEVELS.get(kind, logging.WARNING), "%s", message)


def _load_app_icon() -> QIcon:
    return QIcon(str(paths.APP_ICON_SVG))


def main() -> int:
    _configure_logging()
    qInstallMessageHandler(_forward_qt_message)
    # Own taskbar/notification identity instead of "python.exe".
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"mk96.{APP_ID}")
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setApplicationVersion(__version__)
    qt_app.setQuitOnLastWindowClosed(False)  # it lives in the tray
    icon = _load_app_icon()
    qt_app.setWindowIcon(icon)

    if single_instance.wake_running_instance():
        logger.info("Already running: asked the running copy to show itself")
        return 0
    mutex = single_instance.hold_installer_mutex()
    init_gui_dispatcher()

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, APP_NAME, "No se encontró el área de notificación de Windows.")
        return 1
    try:
        config = load_config(paths.config_path())
    except ConfigError as exc:
        logger.exception("Invalid configuration file")
        QMessageBox.critical(
            None, APP_NAME, f"{exc}\n\nRevisa o mueve el archivo:\n{paths.config_path()}"
        )
        return 1

    logger.info("Starting %s %s", APP_NAME, __version__)
    application = Application(config, icon)
    activation = single_instance.listen_for_activation(application.open_settings)
    qt_app.aboutToQuit.connect(application.shutdown)
    exit_code = qt_app.exec()
    activation.close()
    # Delete every window while QApplication still exists: Qt objects left for interpreter
    # shutdown crash the process on exit (seen in the UI tests).
    for widget in QApplication.topLevelWidgets():
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del application, activation, mutex
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
