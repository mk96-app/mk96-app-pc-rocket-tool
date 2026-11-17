"""Periodic polling of the Rocket.Chat REST API, one Poller per connected server.

Each tick: `subscriptions.get` in delta mode finds rooms with activity, then the history
of the rooms some rule cares about yields the new messages. A message counts as read when
its `ts` is not after the subscription's `ls` (last seen), which any real Rocket.Chat client
updates when the room is opened, so reading it anywhere stops the sound.

The module-level functions are pure; Poller orchestrates them with a QTimer and a worker
thread. Only one poll is in flight at a time, so its internal state is touched by one thread.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from PySide6.QtCore import QTimer

from mk96_app_pc_rocket_tool.models import ChatEvent, HistoryMessage, RepeatMode, Rule, RuleType
from mk96_app_pc_rocket_tool.rest_client import AuthError, RestApiError, describe_connection_error
from mk96_app_pc_rocket_tool.rules import match_rule
from mk96_app_pc_rocket_tool.workers import run_in_thread

logger = logging.getLogger(__name__)

# After network failures ticks are skipped (exponential backoff) so a downed server is not
# hammered every 15 s; with the default 30 s interval the cap is about 5 minutes.
MAX_BACKOFF_TICKS = 10
# Usernames Rocket.Chat uses for @all and @here in a message's mention list.
MENTION_ALL_NAMES = frozenset({"all", "here"})


class RocketChatApi(Protocol):
    def list_subscriptions(self, updated_since: str | None = None) -> list[dict[str, Any]]: ...
    def get_own_username(self) -> str | None: ...
    def room_history_since(
        self, room_id: str, room_type: str, oldest: str | None
    ) -> list[HistoryMessage]: ...


def is_message_read(message_ts: str | None, last_seen_ts: str | None) -> bool:
    if not message_ts or not last_seen_ts:
        return False
    return message_ts <= last_seen_ts


def should_fetch_room(
    room_id: str, active_rules: list[Rule], has_active_mention_rule: bool
) -> bool:
    """History is only worth fetching for rooms with a room rule, or any room when a mention
    rule is active (a mention can happen anywhere)."""
    if has_active_mention_rule:
        return True
    return any(r.type is RuleType.ROOM and r.room_id == room_id for r in active_rules)


def backoff_ticks(failures: int) -> int:
    """Ticks to skip after `failures` consecutive failures: 0, 0, 1, 3, 7... up to the cap."""
    if failures <= 0:
        return 0
    return min((1 << (failures - 1)) - 1, MAX_BACKOFF_TICKS)


@dataclass(slots=True)
class PendingReminder:
    """An unread message whose rule asks to keep sounding."""

    event: ChatEvent
    rule_id: str
    message_ts: str | None
    sound_count: int
    last_sound_tick: int


def should_repeat(pending: PendingReminder, rule: Rule, current_tick: int) -> bool:
    if rule.repeat_mode is RepeatMode.ONCE:
        return False
    if rule.repeat_mode is RepeatMode.N_TIMES and pending.sound_count >= (rule.repeat_count or 1):
        return False
    return current_tick - pending.last_sound_tick >= rule.repeat_every_polls


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(slots=True)
class PollOutcome:
    connected: bool = False
    auth_error: bool = False
    error: str | None = None
    username: str | None = None
    events: list[ChatEvent] = field(default_factory=list)
    rooms_read: list[str] = field(default_factory=list)


class Poller:
    """Callbacks always run on the GUI thread: on_event(ChatEvent), on_status_changed(bool),
    on_room_read(room_id) and on_auth_error()."""

    def __init__(
        self,
        api: RocketChatApi,
        poll_interval_s: int,
        get_rules: Callable[[], list[Rule]],
        on_event: Callable[[ChatEvent], None],
        on_status_changed: Callable[[bool], None],
        on_room_read: Callable[[str], None],
        on_auth_error: Callable[[], None],
    ):
        self._api = api
        self._poll_interval_s = poll_interval_s
        self._get_rules = get_rules
        self._on_event = on_event
        self._on_status_changed = on_status_changed
        self._on_room_read = on_room_read
        self._on_auth_error = on_auth_error

        self._tick = 0
        self._cursors: dict[str, str] = {}  # room_id -> ts of the last processed message
        self._pending: dict[str, PendingReminder] = {}
        # room_id -> ts of the last notified message, repeating or not; only used to report
        # when the room gets read (on_room_read clears it from the tray).
        self._notified: dict[str, str | None] = {}
        # Rooms whose history failed: the subscriptions delta would not bring them back
        # until there is new activity, so they are retried on their own.
        self._retry: dict[str, dict[str, Any]] = {}
        self._last_poll_iso: str | None = None
        self._baseline_done = False
        self._timer: QTimer | None = None
        self._connected: bool | None = None  # None: unknown yet, avoids a report every tick
        self._busy = False
        self._stopped = False
        self._failures = 0
        self._skip_ticks = 0
        self.username: str | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        self._stopped = False
        self._timer = QTimer()
        self._timer.setInterval(self._poll_interval_s * 1000)
        self._timer.timeout.connect(self._tick_poll)
        self._timer.start()
        self._tick_poll()

    def stop(self) -> None:
        self._stopped = True
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _set_connected(self, connected: bool) -> None:
        # Only on real transitions: the settings window redraws on each status report.
        if connected != self._connected:
            self._connected = connected
            self._on_status_changed(connected)

    def _tick_poll(self) -> None:
        if self._stopped or self._busy:
            return
        if self._skip_ticks > 0:
            self._skip_ticks -= 1
            return
        self._busy = True
        # Rules are read here, on the GUI thread, never from the worker.
        active_rules = [rule for rule in self._get_rules() if rule.active]
        run_in_thread(lambda: self._poll_once(active_rules), self._on_poll_done)

    def _on_poll_done(self, outcome: PollOutcome | None, error: BaseException | None) -> None:
        self._busy = False
        if self._stopped:
            return
        if error is not None or outcome is None:
            logger.error("Unexpected polling failure", exc_info=error)
            outcome = PollOutcome(error=f"Error inesperado: {error}")

        if not outcome.connected:
            self.last_error = outcome.error
            if outcome.auth_error:
                # Only the specific token warning, not the generic "no connection" one.
                self.stop()
                self._connected = False
                self._on_auth_error()
                return
            self._failures += 1
            self._skip_ticks = backoff_ticks(self._failures)
            self._set_connected(False)
            return

        self._failures = 0
        self.username = outcome.username
        self.last_error = None
        self._set_connected(True)
        for room_id in outcome.rooms_read:
            self._on_room_read(room_id)
        for event in outcome.events:
            self._on_event(event)

    def _poll_once(self, active_rules: list[Rule]) -> PollOutcome:
        """Runs on the worker thread; calls no callback, returns what to do."""
        outcome = PollOutcome()
        # Taken BEFORE asking: a change during the request shows up in the next delta.
        poll_started = _now_iso()
        try:
            subscriptions = self._api.list_subscriptions(self._last_poll_iso)
            own_username = self._api.get_own_username()
        except AuthError as exc:
            outcome.auth_error = True
            outcome.error = describe_connection_error(exc)
            return outcome
        except RestApiError as exc:
            outcome.error = describe_connection_error(exc)
            return outcome

        outcome.connected = True
        outcome.username = own_username
        self._last_poll_iso = poll_started
        self._tick += 1

        if not self._baseline_done:
            # First poll: everything already there is history, nothing sounds.
            for sub in subscriptions:
                if sub.get("rid"):
                    self._cursors[sub["rid"]] = sub.get("ls") or poll_started
            self._baseline_done = True
            return outcome

        by_room = {sub["rid"]: sub for sub in subscriptions if sub.get("rid")}
        for room_id, sub in self._retry.items():
            by_room.setdefault(room_id, sub)
        self._retry = {}

        has_mention_rule = any(rule.type is RuleType.MENTION for rule in active_rules)
        for room_id, sub in by_room.items():
            self._clear_if_read(room_id, sub, outcome)
            if should_fetch_room(room_id, active_rules, has_mention_rule):
                self._process_room(room_id, sub, active_rules, own_username, outcome)

        self._fire_due_repeats(active_rules, outcome)
        return outcome

    def _clear_if_read(self, room_id: str, sub: dict[str, Any], outcome: PollOutcome) -> None:
        pending = self._pending.get(room_id)
        if pending is not None and is_message_read(pending.message_ts, sub.get("ls")):
            del self._pending[room_id]
        if room_id in self._notified and is_message_read(self._notified[room_id], sub.get("ls")):
            del self._notified[room_id]
            outcome.rooms_read.append(room_id)

    def _process_room(
        self,
        room_id: str,
        sub: dict[str, Any],
        active_rules: list[Rule],
        own_username: str | None,
        outcome: PollOutcome,
    ) -> None:
        room_type = sub.get("t") or "c"
        try:
            messages = self._api.room_history_since(room_id, room_type, self._cursors.get(room_id))
        except RestApiError as exc:
            logger.warning("History of room %s failed, retrying next tick: %s", room_id, exc)
            self._retry[room_id] = sub
            return

        for message in messages:
            if message.ts:
                self._cursors[room_id] = message.ts
            if message.sender == own_username:
                continue
            event = ChatEvent(
                room_id=room_id,
                room_type=room_type,
                sender=message.sender,
                text=message.text,
                mentioned=own_username in message.mentions,
                mentioned_all=bool(MENTION_ALL_NAMES.intersection(message.mentions)),
            )
            rule = match_rule(event, active_rules)
            if rule is None or is_message_read(message.ts, sub.get("ls")):
                continue
            outcome.events.append(event)
            self._notified[room_id] = message.ts
            if rule.repeat_mode is not RepeatMode.ONCE:
                self._pending[room_id] = PendingReminder(event, rule.id, message.ts, 1, self._tick)

    def _fire_due_repeats(self, active_rules: list[Rule], outcome: PollOutcome) -> None:
        rules_by_id = {rule.id: rule for rule in active_rules}
        for room_id, pending in list(self._pending.items()):
            rule = rules_by_id.get(pending.rule_id)
            if rule is None:  # rule removed or silenced meanwhile
                del self._pending[room_id]
                continue
            if should_repeat(pending, rule, self._tick):
                outcome.events.append(pending.event)
                pending.sound_count += 1
                pending.last_sound_tick = self._tick
