"""Tray icon and its menu: connection status, recent special notifications, meeting reminders,
quick toggles for the alerts, tools, presence, Do Not Disturb and quit.

Windows conventions: left click opens Settings, right click shows the menu, the tooltip shows
name + status, and the icon is a monochrome symbol matched to the taskbar theme, with small
overlays for "silenced" and "disconnected" instead of colors or animation.
"""

import winreg
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import QByteArray, QLineF, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from mk96_app_pc_rocket_tool import APP_NAME, paths
from mk96_app_pc_rocket_tool.models import Rule
from mk96_app_pc_rocket_tool.quiet_hours import SilenceOption
from mk96_app_pc_rocket_tool.rest_client import PRESENCE_LABELS, Presence

MAX_RECENT_ITEMS = 10
TOOLTIP_MAX_CHARS = 127  # Windows cuts tray tooltips at 127 characters
ICON_SIZES = (16, 20, 24, 32, 48)
_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


@dataclass(frozen=True, slots=True)
class RecentNotification:
    label: str
    room_id: str


@dataclass(frozen=True, slots=True)
class PendingMeetingAlert:
    meeting_id: str
    title: str
    link: str | None


@dataclass(frozen=True, slots=True)
class UpcomingMeeting:
    meeting_id: str
    title: str
    time_text: str


@dataclass(frozen=True, slots=True)
class ConnectionSummary:
    server_name: str
    connected: bool
    username: str | None
    error: str | None


def add_to_recent(
    recent: list[RecentNotification], entry: RecentNotification, max_items: int = MAX_RECENT_ITEMS
) -> list[RecentNotification]:
    """Puts the room's notification first. A room never takes two entries: a newer one
    replaces it and moves to the top."""
    others = [e for e in recent if e.room_id != entry.room_id]
    return [entry, *others][:max_items]


def remove_from_recent(recent: list[RecentNotification], room_id: str) -> list[RecentNotification]:
    return [e for e in recent if e.room_id != room_id]


def add_pending_meeting(
    pending: list[PendingMeetingAlert],
    entry: PendingMeetingAlert,
    max_items: int = MAX_RECENT_ITEMS,
) -> list[PendingMeetingAlert]:
    """Same as add_to_recent, per meeting, capped so undismissed alerts cannot pile up."""
    others = [e for e in pending if e.meeting_id != entry.meeting_id]
    return [entry, *others][:max_items]


def remove_pending_meeting(
    pending: list[PendingMeetingAlert], meeting_id: str
) -> list[PendingMeetingAlert]:
    return [e for e in pending if e.meeting_id != meeting_id]


def build_status_lines(connections: list[ConnectionSummary]) -> list[str]:
    """One status line per server. With several servers the server name is added."""
    if not connections:
        return ["Desconectado"]
    several = len(connections) > 1
    lines = []
    for conn in connections:
        if not conn.connected:
            lines.append(f"{conn.server_name}: {conn.error or 'sin conexión'}")
        elif conn.username:
            suffix = f" · {conn.server_name}" if several else ""
            lines.append(f"Conectado: @{conn.username}{suffix}")
        else:
            lines.append(f"Conectando a {conn.server_name}...")
    return lines


def tooltip_text(status_lines: list[str], quiet_text: str | None) -> str:
    text = "\n".join([APP_NAME, *status_lines, *([quiet_text] if quiet_text else [])])
    return text if len(text) <= TOOLTIP_MAX_CHARS else text[: TOOLTIP_MAX_CHARS - 1] + "…"


def taskbar_is_light() -> bool:
    """Windows can theme the taskbar apart from apps (SystemUsesLightTheme)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "SystemUsesLightTheme")
    except OSError:
        return False  # the default Windows 11 taskbar is dark
    return bool(value)


def _render_icon(color: QColor, quiet: bool, connected: bool) -> QIcon:
    svg = paths.TRAY_ICON_SVG.read_bytes().replace(b"#ffffff", color.name().encode())
    renderer = QSvgRenderer(QByteArray(svg))
    icon = QIcon()
    for size in ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not connected:
            painter.setOpacity(0.45)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.setOpacity(1.0)
        if quiet:
            # Small "no sound" badge in the bottom-right corner.
            badge = size * 0.5
            rect = QRectF(size - badge, size - badge, badge, badge)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#d13438"))
            painter.drawEllipse(rect)
            painter.setPen(QPen(QColor("white"), max(1.0, size / 12)))
            inset = badge * 0.28
            middle = rect.center().y()
            painter.drawLine(QLineF(rect.left() + inset, middle, rect.right() - inset, middle))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


@dataclass(frozen=True, slots=True)
class TrayActions:
    open_settings: Callable[[str | None], None]  # page name or None
    open_chat: Callable[[str], None]
    open_link: Callable[[str], object]
    toggle_rule: Callable[[str, bool], None]
    mark_read: Callable[[], None]
    open_sounds_folder: Callable[[], None]
    set_presence: Callable[[Presence], None]
    set_quiet: Callable[[SilenceOption], None]
    quit: Callable[[], None]


class Tray:
    def __init__(self, actions: TrayActions):
        self._actions = actions
        self._connected = False
        self._status_lines = ["Desconectado"]
        self._quiet_text: str | None = None
        self._quiet_manual = False
        self._recent: list[RecentNotification] = []
        self._rules: list[Rule] = []
        self._pending_meetings: list[PendingMeetingAlert] = []
        self._upcoming: list[UpcomingMeeting] = []
        self._icon_key: tuple[bool, bool, bool] | None = None

        self._menu = QMenu()
        self._icon = QSystemTrayIcon()
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)
        self._icon.messageClicked.connect(self._on_message_clicked)
        self._last_message_room: str | None = None
        self._rebuild()
        self._icon.show()

    @property
    def icon(self) -> QSystemTrayIcon:
        return self._icon

    # -- State setters (rebuild only on real changes) --------------------------------------

    def set_connection_status(self, connected: bool, lines: list[str]) -> None:
        if (connected, lines) != (self._connected, self._status_lines):
            self._connected, self._status_lines = connected, lines
            self._rebuild()

    def set_quiet(self, text: str | None, manual: bool) -> None:
        if (text, manual) != (self._quiet_text, self._quiet_manual):
            self._quiet_text, self._quiet_manual = text, manual
            self._rebuild()

    def set_rules(self, rules: list[Rule]) -> None:
        self._rules = rules
        self._rebuild()

    def set_upcoming_meetings(self, upcoming: list[UpcomingMeeting]) -> None:
        if upcoming != self._upcoming:
            self._upcoming = upcoming
            self._rebuild()

    def add_recent_notification(self, entry: RecentNotification) -> None:
        self._recent = add_to_recent(self._recent, entry)
        self._rebuild()

    def remove_recent_notification(self, room_id: str) -> None:
        if any(e.room_id == room_id for e in self._recent):
            self._recent = remove_from_recent(self._recent, room_id)
            self._rebuild()

    def add_pending_meeting(self, entry: PendingMeetingAlert) -> None:
        self._pending_meetings = add_pending_meeting(self._pending_meetings, entry)
        self._rebuild()

    def refresh_icon(self) -> None:
        """Re-reads the taskbar theme (called periodically: Windows sends no Qt signal)."""
        self._update_icon()

    # -- Notifications -----------------------------------------------------------------------

    def notify(
        self, title: str, body: str, *, room_id: str | None = None, is_error: bool = False
    ) -> None:
        """Windows toast through the tray icon. Clicking a chat toast opens that chat."""
        self._last_message_room = room_id
        kind = (
            QSystemTrayIcon.MessageIcon.Critical
            if is_error
            else QSystemTrayIcon.MessageIcon.Information
        )
        self._icon.showMessage(title, body, kind)

    def _on_message_clicked(self) -> None:
        if self._last_message_room is not None:
            self._open_recent(self._last_message_room)

    # -- Menu -----------------------------------------------------------------------------------

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason is QSystemTrayIcon.ActivationReason.Trigger:
            self._actions.open_settings(None)

    def _open_recent(self, room_id: str) -> None:
        # Opening the chat counts as having seen it.
        self.remove_recent_notification(room_id)
        self._actions.open_chat(room_id)

    def _dismiss_meeting(self, meeting_id: str) -> None:
        self._pending_meetings = remove_pending_meeting(self._pending_meetings, meeting_id)
        self._rebuild()

    def _update_icon(self) -> None:
        light = taskbar_is_light()
        key = (light, self._quiet_text is not None, self._connected)
        if key != self._icon_key:
            self._icon_key = key
            color = QColor("#1f1f1f") if light else QColor("#ffffff")
            self._icon.setIcon(_render_icon(color, quiet=key[1], connected=key[2]))
        self._icon.setToolTip(tooltip_text(self._status_lines, self._quiet_text))

    @staticmethod
    def _info(menu: QMenu, text: str, bold: bool = False) -> None:
        action = menu.addAction(text)
        action.setEnabled(False)
        if bold:
            font = QFont(action.font())
            font.setBold(True)
            action.setFont(font)

    def _rebuild(self) -> None:
        self._update_icon()
        menu = self._menu
        menu.clear()
        menu.addAction("Abrir configuración", lambda: self._actions.open_settings(None))
        menu.setDefaultAction(menu.actions()[0])
        menu.addSeparator()

        for line in self._status_lines:
            self._info(menu, line)
        if self._quiet_text:
            self._info(menu, f"🔕 {self._quiet_text}")
        menu.addSeparator()

        if self._pending_meetings:
            for meeting in self._pending_meetings:
                self._info(menu, f"🔔 {meeting.title}", bold=True)
                if meeting.link:
                    menu.addAction(
                        "    Abrir enlace", partial(self._actions.open_link, meeting.link)
                    )
                menu.addAction("    Descartar", partial(self._dismiss_meeting, meeting.meeting_id))
            menu.addSeparator()

        if self._recent:
            for entry in self._recent:
                menu.addAction(entry.label, partial(self._open_recent, entry.room_id))
        else:
            self._info(menu, "Sin notificaciones especiales todavía")
        menu.addSeparator()

        self._add_alerts_menu(menu)
        self._add_meetings_menu(menu)
        menu.addSeparator()
        self._add_tools_menu(menu)
        self._add_presence_menu(menu)
        self._add_quiet_menu(menu)
        menu.addSeparator()
        menu.addAction("Salir", self._actions.quit)

    def _add_alerts_menu(self, menu: QMenu) -> None:
        submenu = menu.addMenu("Alertas")
        for rule in self._rules:
            action = QAction(rule.label or rule.room_id or "Mención", submenu)
            action.setCheckable(True)
            action.setChecked(rule.active)
            action.toggled.connect(
                lambda checked, rule_id=rule.id: self._actions.toggle_rule(rule_id, checked)
            )
            submenu.addAction(action)
        if self._rules:
            submenu.addSeparator()
        submenu.addAction("Configurar alertas...", lambda: self._actions.open_settings("alerts"))

    def _add_meetings_menu(self, menu: QMenu) -> None:
        submenu = menu.addMenu("Reuniones")
        for meeting in self._upcoming:
            self._info(submenu, f"{meeting.time_text} - {meeting.title}")
        if not self._upcoming:
            self._info(submenu, "Sin reuniones próximas")
        submenu.addSeparator()
        submenu.addAction(
            "Configurar reuniones...", lambda: self._actions.open_settings("meetings")
        )

    def _add_tools_menu(self, menu: QMenu) -> None:
        submenu = menu.addMenu("Herramientas")
        mark_read = submenu.addAction("Marcar como leído...", self._actions.mark_read)
        mark_read.setEnabled(self._connected)  # without a server there is nothing to mark
        submenu.addAction("Carpeta de sonidos propios", self._actions.open_sounds_folder)

    def _add_presence_menu(self, menu: QMenu) -> None:
        submenu = menu.addMenu("Estado")
        submenu.setEnabled(self._connected)
        for presence in Presence:
            submenu.addAction(
                PRESENCE_LABELS[presence], partial(self._actions.set_presence, presence)
            )

    def _add_quiet_menu(self, menu: QMenu) -> None:
        submenu = menu.addMenu("No molestar")
        for option, label in (
            (SilenceOption.ONE_HOUR, "Durante 1 hora"),
            (SilenceOption.FOUR_HOURS, "Durante 4 horas"),
            (SilenceOption.INDEFINITE, "Hasta que lo desactive"),
        ):
            submenu.addAction(label, partial(self._actions.set_quiet, option))
        submenu.addSeparator()
        off = submenu.addAction("Desactivar", lambda: self._actions.set_quiet(SilenceOption.OFF))
        off.setEnabled(self._quiet_manual)

    def hide(self) -> None:
        self._icon.hide()
