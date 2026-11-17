"""Meeting reminders timer, independent of Rocket.Chat (works with no server connected).

What is due is decided by meetings.due_meetings (pure); this only drives it with a QTimer.
On start it also recovers reminders whose advance window opened before the app started.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QTimer

from mk96_app_pc_rocket_tool.meetings import DueReminder, due_meetings, startup_due_meetings
from mk96_app_pc_rocket_tool.models import Meeting

logger = logging.getLogger(__name__)

CHECK_INTERVAL_S = 30


class MeetingScheduler:
    def __init__(
        self,
        get_meetings: Callable[[], list[Meeting]],
        on_due: Callable[[DueReminder], None],
        on_tick: Callable[[], None],
    ):
        """get_meetings() is read on every tick so edits apply at once. on_tick() runs after
        each check to refresh derived UI (upcoming meetings, Do Not Disturb status)."""
        self._get_meetings = get_meetings
        self._on_due = on_due
        self._on_tick = on_tick
        self._last_check = datetime.now()
        self._timer = QTimer()
        self._timer.setInterval(CHECK_INTERVAL_S * 1000)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._last_check = datetime.now()
        self._fire(startup_due_meetings(self._get_meetings(), self._last_check))
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _fire(self, reminders: list[DueReminder]) -> None:
        for reminder in reminders:
            # One broken reminder must not stop the others nor the timer.
            try:
                self._on_due(reminder)
            except Exception:
                logger.exception("Reminder for meeting %s failed", reminder.meeting.id)

    def _tick(self) -> None:
        now = datetime.now()
        self._fire(due_meetings(self._get_meetings(), now, self._last_check))
        self._last_check = now
        self._on_tick()
