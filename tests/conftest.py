import gc
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from mk96_app_pc_rocket_tool.models import Rule, RuleType

_QT_APP: QApplication | None = None


@pytest.fixture(scope="session")
def qt_app() -> Iterator[QApplication]:
    """One QApplication for the whole run, kept in a module global so it outlives every Qt
    object the tests create. At the end every window is closed and deleted while the
    application still exists: widgets left for interpreter shutdown crash the process."""
    global _QT_APP
    existing = QApplication.instance()
    _QT_APP = existing if isinstance(existing, QApplication) else QApplication([])
    yield _QT_APP
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    gc.collect()


@pytest.fixture
def room_rule() -> Callable[..., Rule]:
    def build(room_id: str = "room1", **overrides: Any) -> Rule:
        values: dict[str, Any] = {
            "id": "r1",
            "server_id": "s1",
            "type": RuleType.ROOM,
            "sound_id": "cat",
            "room_id": room_id,
            "label": "Test rule",
        }
        return Rule(**{**values, **overrides})

    return build


@pytest.fixture
def mention_rule() -> Callable[..., Rule]:
    def build(**overrides: Any) -> Rule:
        values: dict[str, Any] = {
            "id": "m1",
            "server_id": "s1",
            "type": RuleType.MENTION,
            "sound_id": "bell",
            "label": "Mención",
        }
        return Rule(**{**values, **overrides})

    return build
