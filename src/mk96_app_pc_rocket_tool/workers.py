"""Runs blocking work (REST calls) off the GUI thread and hands the result back on it.
Widgets are never touched from a worker thread."""

import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot

logger = logging.getLogger(__name__)

DoneCallback = Callable[[Any, BaseException | None], None]


class _GuiThreadDispatcher(QObject):
    """Lives on the GUI thread; a queued signal makes `deliver` run there."""

    _call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, function: Callable[[], None]) -> None:
        function()

    def deliver(self, function: Callable[[], None]) -> None:
        self._call.emit(function)


_dispatcher: _GuiThreadDispatcher | None = None


def init_gui_dispatcher() -> None:
    """Must be called once from the GUI thread before any run_in_thread."""
    global _dispatcher
    _dispatcher = _GuiThreadDispatcher()


def run_in_thread(work: Callable[[], Any], on_done: DoneCallback) -> None:
    """Calls work() in a daemon thread, then on_done(result, error) on the GUI thread
    (error is the exception if work failed, and result is None then)."""
    dispatcher = _dispatcher
    if dispatcher is None:
        raise RuntimeError("init_gui_dispatcher() was not called")

    def runner() -> None:
        try:
            result, error = work(), None
        except Exception as exc:  # handed to on_done, which decides what to show
            result, error = None, exc
        dispatcher.deliver(lambda: on_done(result, error))

    threading.Thread(target=runner, daemon=True).start()
