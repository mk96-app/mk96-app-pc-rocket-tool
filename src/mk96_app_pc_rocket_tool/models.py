"""Domain data: servers, sound rules, meetings, Do Not Disturb settings and the whole config.

Value objects are frozen; change them with `dataclasses.replace`. `AppConfig` is the only
mutable container and is what gets saved to config.yaml (credentials never live here).
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, StrEnum


def new_id() -> str:
    return str(uuid.uuid4())


class RuleType(StrEnum):
    ROOM = "room"  # one specific channel, private group or direct message
    MENTION = "mention"  # any room where the user is mentioned


class RepeatMode(StrEnum):
    ONCE = "once"
    N_TIMES = "n_times"
    UNTIL_READ = "until_read"


class RoomType(StrEnum):
    """Rocket.Chat room type codes, as the API returns them."""

    CHANNEL = "c"
    PRIVATE = "p"
    DIRECT = "d"


class ConnectionState(StrEnum):
    CONNECTED = "connected"  # polling (username known once the first poll answers)
    DISCONNECTED = "disconnected"  # stopped by the user
    ERROR = "error"  # polling but failing, or stopped by a rejected token


class MeetingRepeat(StrEnum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class OccurrenceState(StrEnum):
    """Mark for a single occurrence of a recurring meeting."""

    DELETED = "deleted"  # hidden and silent
    INACTIVE = "inactive"  # still drawn (dimmed) but silent


class Weekday(IntEnum):
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


@dataclass(frozen=True, slots=True)
class Server:
    id: str
    name: str
    active: bool = False  # reconnects on startup
    poll_interval_s: int = 30


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    server_id: str
    type: RuleType
    sound_id: str
    room_id: str | None = None
    room_type: RoomType | None = None
    label: str = ""
    active: bool = True
    mention_all: bool = False  # mention rules: also @all / @here
    repeat_mode: RepeatMode = RepeatMode.ONCE
    repeat_count: int | None = None  # only for N_TIMES
    repeat_every_polls: int = 1


@dataclass(frozen=True, slots=True)
class Meeting:
    id: str
    title: str
    start: datetime  # naive local time, minute precision
    duration_min: int = 30
    repeat: MeetingRepeat = MeetingRepeat.NONE
    repeat_days: tuple[Weekday, ...] = ()  # only for WEEKLY
    repeat_until: datetime | None = None  # last day (inclusive) of the series, at 00:00
    remind_before_min: int = 0
    link: str | None = None
    notes: str = ""
    sound_id: str | None = None  # None: default meeting sound
    active: bool = True
    exceptions: dict[datetime, OccurrenceState] = field(default_factory=dict)

    @property
    def is_series(self) -> bool:
        return self.repeat is not MeetingRepeat.NONE


@dataclass(frozen=True, slots=True)
class QuietSettings:
    """Do Not Disturb. `silenced_until`: None, INDEFINITE, or the end of a manual silence."""

    silenced_until: datetime | str | None = None
    schedule_enabled: bool = False
    schedule_start: str = "22:00"  # HH:MM; start > end means the range crosses midnight
    schedule_end: str = "08:00"


@dataclass(slots=True)
class AppConfig:
    servers: list[Server] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    room_names: dict[str, str] = field(default_factory=dict)  # room_id -> display name cache
    meetings: list[Meeting] = field(default_factory=list)
    quiet: QuietSettings = field(default_factory=QuietSettings)

    def server(self, server_id: str) -> Server | None:
        return next((s for s in self.servers if s.id == server_id), None)

    def rules_for_server(self, server_id: str) -> list[Rule]:
        return [rule for rule in self.rules if rule.server_id == server_id]

    def find_room_rule(self, server_id: str, room_id: str) -> Rule | None:
        """Existing room rule for that room, so two sounds never point at the same room."""
        return next(
            (
                rule
                for rule in self.rules
                if rule.server_id == server_id
                and rule.type is RuleType.ROOM
                and rule.room_id == room_id
            ),
            None,
        )

    def replace_server(self, server: Server) -> None:
        self.servers = [server if s.id == server.id else s for s in self.servers]

    def replace_rule(self, rule: Rule) -> None:
        self.rules = [rule if r.id == rule.id else r for r in self.rules]

    def upsert_meeting(self, meeting: Meeting) -> None:
        self.meetings = [m for m in self.meetings if m.id != meeting.id] + [meeting]

    def remove_meeting(self, meeting_id: str) -> None:
        self.meetings = [m for m in self.meetings if m.id != meeting_id]

    def remove_server(self, server_id: str) -> None:
        """Drops the server and its rules (credentials are the caller's job)."""
        self.servers = [s for s in self.servers if s.id != server_id]
        self.rules = [r for r in self.rules if r.server_id != server_id]


@dataclass(frozen=True, slots=True)
class RoomInfo:
    room_id: str
    name: str
    type: str | None

    @property
    def description(self) -> str:
        """'Type · name', what the room search shows and matches against."""
        type_label = ROOM_TYPE_LABELS.get(self.type or "", self.type or "?")
        return f"{type_label} · {self.name}"


ROOM_TYPE_LABELS: dict[str, str] = {
    RoomType.DIRECT: "DM",
    RoomType.CHANNEL: "Canal",
    RoomType.PRIVATE: "Privado",
}


@dataclass(frozen=True, slots=True)
class HistoryMessage:
    sender: str | None
    text: str
    ts: str | None  # ISO 8601 string, as Rocket.Chat sends it (sortable as text)
    mentions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatEvent:
    """A new message that may deserve a sound."""

    room_id: str
    room_type: str
    sender: str | None
    text: str
    mentioned: bool = False
    mentioned_all: bool = False  # @all or @here
