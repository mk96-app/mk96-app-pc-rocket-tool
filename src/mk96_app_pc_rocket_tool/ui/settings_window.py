"""Settings window: Servers, Alerts and Meetings tabs. Changes apply at once (no OK/Apply);
"Cerrar" and Esc just close it. Size and position are remembered between openings."""

from enum import StrEnum

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import QDialogButtonBox, QTabWidget, QVBoxLayout, QWidget

from mk96_app_pc_rocket_tool import APP_ID, APP_NAME
from mk96_app_pc_rocket_tool.models import AppConfig, ConnectionState, RoomInfo
from mk96_app_pc_rocket_tool.ui.alerts_panel import AlertActions, AlertsPanel
from mk96_app_pc_rocket_tool.ui.meetings_panel import MeetingActions, MeetingsPanel
from mk96_app_pc_rocket_tool.ui.servers_panel import ServerActions, ServersPanel

DEFAULT_SIZE = (1000, 780)
SCREEN_MARGIN = 60
GEOMETRY_KEY = "settings_window/geometry"


class SettingsPage(StrEnum):
    SERVERS = "servers"
    ALERTS = "alerts"
    MEETINGS = "meetings"


class SettingsWindow(QWidget):
    def __init__(
        self,
        config: AppConfig,
        icon: QIcon,
        server_actions: ServerActions,
        alert_actions: AlertActions,
        meeting_actions: MeetingActions,
    ):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"Configuración - {APP_NAME}")
        self.setWindowIcon(icon)
        self.servers = ServersPanel(config, server_actions)
        self.alerts = AlertsPanel(config, alert_actions)
        self.meetings = MeetingsPanel(config, meeting_actions)
        self._tabs = QTabWidget()
        self._pages = {
            SettingsPage.SERVERS: self.servers,
            SettingsPage.ALERTS: self.alerts,
            SettingsPage.MEETINGS: self.meetings,
        }
        for page, title in zip(
            self._pages.values(), ("Servidores", "Alertas", "Reuniones"), strict=True
        ):
            self._tabs.addTab(page, title)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("Cerrar")
        close.rejected.connect(self.close)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs, 1)
        layout.addWidget(close)
        self._restore_geometry()

    def show_page(self, page: SettingsPage) -> None:
        self._tabs.setCurrentWidget(self._pages[page])

    def set_server_status(self, server_id: str, state: ConnectionState, detail: str | None) -> None:
        self.servers.set_server_status(server_id, state, detail)
        self.alerts.set_server_status(server_id, state)

    def set_known_rooms(self, server_id: str, rooms: list[RoomInfo]) -> None:
        self.alerts.set_known_rooms(server_id, rooms)

    def config_replaced(self) -> None:
        self.servers.config_replaced()
        self.alerts.config_replaced()

    def _restore_geometry(self) -> None:
        geometry = QSettings("mk96", APP_ID).value(GEOMETRY_KEY)
        # restoreGeometry() keeps the window on a monitor that still exists.
        if geometry is not None and self.restoreGeometry(geometry):
            return
        screen = QGuiApplication.primaryScreen().availableGeometry()
        width = min(DEFAULT_SIZE[0], screen.width() - SCREEN_MARGIN)
        height = min(DEFAULT_SIZE[1], screen.height() - SCREEN_MARGIN)
        self.resize(width, height)
        self.move(screen.center().x() - width // 2, screen.center().y() - height // 2)

    def closeEvent(self, event: QCloseEvent) -> None:
        QSettings("mk96", APP_ID).setValue(GEOMETRY_KEY, self.saveGeometry())
        super().closeEvent(event)
