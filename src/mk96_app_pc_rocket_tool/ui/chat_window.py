"""Window of one room: read-only recent history plus a one-line reply.
No attachments, threads or typing indicator: for that, open Rocket.Chat itself."""

import html
import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from mk96_app_pc_rocket_tool.models import HistoryMessage
from mk96_app_pc_rocket_tool.rest_client import RestClient
from mk96_app_pc_rocket_tool.workers import run_in_thread

logger = logging.getLogger(__name__)

# Fewer than the API default: the window is meant to be read at a glance.
HISTORY_MESSAGE_COUNT = 10
NEW_MESSAGE_STYLE = "background-color:#fff3b0; color:#3a3000;"


def trailing_new_start(messages: list[HistoryMessage], own_username: str | None) -> int:
    """Index from which messages are "new" and get highlighted: the final run of messages
    not written by you. len(messages) when there is nothing to highlight (the last one is
    yours, or the own username is unknown)."""
    if own_username is None:
        return len(messages)
    index = len(messages)
    while index > 0 and messages[index - 1].sender != own_username:
        index -= 1
    return index


def history_html(messages: list[HistoryMessage], own_username: str | None) -> str:
    new_start = trailing_new_start(messages, own_username)
    blocks = []
    for index, message in enumerate(messages):
        sender = html.escape(message.sender or "?")
        text = html.escape(message.text).replace("\n", "<br>")
        style = f' style="{NEW_MESSAGE_STYLE}"' if index >= new_start else ""
        blocks.append(f"<p{style}><b>{sender}</b><br>{text}</p>")
    return "".join(blocks) or "<p><i>Sin mensajes.</i></p>"


class ChatWindow(QWidget):
    def __init__(
        self,
        client: RestClient,
        room_id: str,
        room_type: str,
        title: str,
        on_error: Callable[[str, str], None],
    ):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.resize(440, 500)
        self._client = client
        self._room_id = room_id
        self._room_type = room_type
        self._on_error = on_error
        self._closed = False  # thread results can arrive after the window closed

        self._history = QTextBrowser()
        self._history.setOpenExternalLinks(False)
        self._reply = QLineEdit(placeholderText="Escribe una respuesta...")
        self._reply.returnPressed.connect(self._send)
        send = QPushButton("Enviar")
        send.clicked.connect(self._send)
        reply_row = QHBoxLayout()
        reply_row.addWidget(self._reply, 1)
        reply_row.addWidget(send)
        layout = QVBoxLayout(self)
        layout.addWidget(self._history, 1)
        layout.addLayout(reply_row)
        self._load_history()
        self._reply.setFocus()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # Qt override
        self._closed = True
        super().closeEvent(event)

    def _load_history(self) -> None:
        def work() -> tuple[list[HistoryMessage], str | None]:
            messages = self._client.room_history(
                self._room_id, self._room_type, count=HISTORY_MESSAGE_COUNT
            )
            return messages, self._client.get_own_username()

        def done(
            result: tuple[list[HistoryMessage], str | None] | None, error: BaseException | None
        ) -> None:
            if self._closed:
                return
            if error is not None or result is None:
                self._on_error("No se pudo cargar el historial", str(error))
                return
            self._history.setHtml(history_html(*result))
            self._history.moveCursor(QTextCursor.MoveOperation.End)

        run_in_thread(work, done)

    def _send(self) -> None:
        text = self._reply.text().strip()
        if not text:
            return
        self._reply.setEnabled(False)

        def done(_result: object, error: BaseException | None) -> None:
            if self._closed:
                return
            self._reply.setEnabled(True)
            if error is not None:
                self._on_error("No se pudo enviar el mensaje", str(error))
                return
            self._reply.clear()
            self._reply.setFocus()
            self._load_history()

        run_in_thread(lambda: self._client.send_message(self._room_id, text), done)
