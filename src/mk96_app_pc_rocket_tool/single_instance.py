"""One running copy per user session.

A named mutex lets the installer detect the running app (Inno Setup `AppMutex`, keep
MUTEX_NAME in sync with packaging/installer.iss). A local socket lets a second launch (e.g.
from the Start menu) wake the running copy, which then opens its settings window.
"""

import ctypes
import logging
from collections.abc import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from mk96_app_pc_rocket_tool import APP_ID

logger = logging.getLogger(__name__)

MUTEX_NAME = APP_ID
SERVER_NAME = f"{APP_ID}-activate"
CONNECT_TIMEOUT_MS = 500


def hold_installer_mutex() -> int:
    """Creates the named mutex and returns its handle; it lives as long as the process."""
    handle: int = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        logger.warning("CreateMutexW failed with error %s", ctypes.GetLastError())
    return handle


def wake_running_instance(server_name: str = SERVER_NAME) -> bool:
    """True if another copy is running (and was asked to show itself)."""
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
        return False
    socket.write(b"activate")
    socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
    socket.disconnectFromServer()
    return True


def listen_for_activation(
    on_activate: Callable[[], None], server_name: str = SERVER_NAME
) -> QLocalServer:
    """Starts the wake-up channel; keep the returned server alive."""
    QLocalServer.removeServer(server_name)  # stale name left by a crashed copy
    server = QLocalServer()
    if not server.listen(server_name):
        logger.warning("Activation channel unavailable: %s", server.errorString())

    def on_connection() -> None:
        while (connection := server.nextPendingConnection()) is not None:
            connection.disconnected.connect(connection.deleteLater)
            on_activate()

    server.newConnection.connect(on_connection)
    return server
