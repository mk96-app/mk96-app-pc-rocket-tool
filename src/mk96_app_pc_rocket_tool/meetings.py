"""Local meetings calendar: recurrence, reminders, single-occurrence exceptions and the
geometry of the Day/Week grid. Pure functions; timers and drawing live elsewhere.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from mk96_app_pc_rocket_tool.models import Meeting, MeetingRepeat, OccurrenceState, Weekday

MONTH_NAMES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
WEEKDAY_NAMES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
WEEKDAY_SHORT_NAMES = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
WEEKDAY_LETTERS = ("L", "M", "X", "J", "V", "S", "D")

# Bundled CC0 sound used when a meeting has no sound of its own.
DEFAULT_MEETING_SOUND_ID = "interface-sounds/confirmation_001"

# Upper bound for the monthly search: only needed to skip months without that day (31st).
# The search starts at max(start, now), so it never walks whole years.
MAX_MONTHS_SEARCHED = 12

TIMELINE_START_HOUR = 7
TIMELINE_END_HOUR = 21
TIMELINE_SLOT_MIN = 30
TIMELINE_ROWS = (TIMELINE_END_HOUR - TIMELINE_START_HOUR) * 60 // TIMELINE_SLOT_MIN

# Sub-columns reserved per day in the Day/Week grid to place overlapping meetings.
LANES_PER_DAY = 4


class ReminderMoment(StrEnum):
    ADVANCE = "advance"  # remind_before_min before the start
    START = "start"  # the exact start time


class OccurrenceStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class DueReminder:
    meeting: Meeting
    occurrence: datetime
    moment: ReminderMoment


# -- Recurrence ----------------------------------------------------------------------------


def next_occurrence(meeting: Meeting, now: datetime) -> datetime | None:
    """Next start at `now` or later, skipping occurrences marked in `meeting.exceptions`.
    None when the meeting is inactive or the series is over.

    Reminders, the tray and the calendar all go through here, so exceptions are applied only
    here. The loop always ends: each turn skips a different key of a finite dict."""
    cursor = now
    while True:
        occurrence = _next_ignoring_exceptions(meeting, cursor)
        if occurrence is None or occurrence not in meeting.exceptions:
            return occurrence
        cursor = occurrence + timedelta(minutes=1)


def _past_end(candidate: date, meeting: Meeting) -> bool:
    return meeting.repeat_until is not None and candidate > meeting.repeat_until.date()


def _next_ignoring_exceptions(meeting: Meeting, now: datetime) -> datetime | None:
    if not meeting.active:
        return None
    start = meeting.start
    match meeting.repeat:
        case MeetingRepeat.NONE:
            return start if start >= now else None
        case MeetingRepeat.DAILY:
            return _next_daily(meeting, now)
        case MeetingRepeat.WEEKLY:
            return _next_weekly(meeting, now)
        case MeetingRepeat.MONTHLY:
            return _next_monthly(meeting, now)


def _next_daily(meeting: Meeting, now: datetime) -> datetime | None:
    candidate = datetime.combine(max(meeting.start.date(), now.date()), meeting.start.time())
    if candidate < now:
        candidate += timedelta(days=1)
    return None if _past_end(candidate.date(), meeting) else candidate


def _next_weekly(meeting: Meeting, now: datetime) -> datetime | None:
    first_day = max(meeting.start.date(), now.date())
    for offset in range(8):
        day = first_day + timedelta(days=offset)
        if Weekday(day.weekday()) not in meeting.repeat_days:
            continue
        candidate = datetime.combine(day, meeting.start.time())
        if candidate < now:
            continue
        return None if _past_end(day, meeting) else candidate
    return None


def _next_monthly(meeting: Meeting, now: datetime) -> datetime | None:
    # Start straight at max(start, now): a monthly meeting created years ago still has
    # occurrences today, and walking month by month from its start would take decades.
    begin = max(meeting.start, now)
    year, month = begin.year, begin.month
    for _ in range(MAX_MONTHS_SEARCHED):
        try:
            day = date(year, month, meeting.start.day)
        except ValueError:
            day = None  # this month has no such day: skip it
        if day is not None:
            candidate = datetime.combine(day, meeting.start.time())
            if candidate >= meeting.start and candidate >= now:
                return None if _past_end(day, meeting) else candidate
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return None


def occurrences_in_range(meeting: Meeting, start: datetime, end: datetime) -> list[datetime]:
    """Every occurrence in [start, end), reusing next_occurrence."""
    result = []
    cursor = start
    while (occurrence := next_occurrence(meeting, cursor)) is not None and occurrence < end:
        result.append(occurrence)
        cursor = occurrence + timedelta(minutes=1)
    return result


def visible_occurrences(meeting: Meeting, start: datetime, end: datetime) -> list[datetime]:
    """Occurrences to DRAW in [start, end): inactive ones are included (dimmed, so they can be
    reactivated) and deleted ones are not."""
    deleted_only: dict[datetime, OccurrenceState] = {
        key: state for key, state in meeting.exceptions.items() if state is OccurrenceState.DELETED
    }
    return occurrences_in_range(replace(meeting, active=True, exceptions=deleted_only), start, end)


def occurrence_status(meeting: Meeting, occurrence: datetime) -> OccurrenceStatus:
    if not meeting.active or meeting.exceptions.get(occurrence) is OccurrenceState.INACTIVE:
        return OccurrenceStatus.INACTIVE
    return OccurrenceStatus.ACTIVE


def with_exception(
    meeting: Meeting, occurrence: datetime, state: OccurrenceState | None
) -> Meeting:
    """Copy of `meeting` with that occurrence marked, or unmarked when `state` is None."""
    exceptions = dict(meeting.exceptions)
    if state is None:
        exceptions.pop(occurrence, None)
    else:
        exceptions[occurrence] = state
    return replace(meeting, exceptions=exceptions)


def edited_meeting(original: Meeting | None, edited: Meeting) -> Meeting:
    """`edited` (fresh from the form) keeping `original`'s exceptions, unless when the series
    happens changed: then the stored keys no longer match real occurrences."""
    if original is None:
        return edited
    series_changed = (original.start, original.repeat, original.repeat_days) != (
        edited.start,
        edited.repeat,
        edited.repeat_days,
    )
    return replace(edited, exceptions={} if series_changed else dict(original.exceptions))


# -- Reminders ------------------------------------------------------------------------------


def _reminder_instants(
    meeting: Meeting, occurrence: datetime
) -> list[tuple[ReminderMoment, datetime]]:
    instants = []
    if meeting.remind_before_min > 0:
        advance = occurrence - timedelta(minutes=meeting.remind_before_min)
        instants.append((ReminderMoment.ADVANCE, advance))
    instants.append((ReminderMoment.START, occurrence))
    return instants


def due_meetings(
    meetings: Iterable[Meeting], now: datetime, last_check: datetime
) -> list[DueReminder]:
    """Reminders whose instant falls in (last_check, now], so consecutive ticks never repeat
    one. With remind_before_min > 0 each occurrence reminds twice: in advance and at start."""
    result = []
    for meeting in meetings:
        occurrence = next_occurrence(meeting, last_check)
        if occurrence is None:
            continue
        for moment, instant in _reminder_instants(meeting, occurrence):
            if last_check < instant <= now:
                result.append(DueReminder(meeting, occurrence, moment))
    return result


def startup_due_meetings(meetings: Iterable[Meeting], now: datetime) -> list[DueReminder]:
    """Reminders whose advance window opened before the app started but whose meeting has not
    happened yet: opening the laptop at 09:55 for a 10:00 meeting with a 10 min reminder
    still reminds. Meant to be called exactly once, when the scheduler starts."""
    result = []
    for meeting in meetings:
        occurrence = next_occurrence(meeting, now)
        if occurrence is None:
            continue
        if occurrence - timedelta(minutes=meeting.remind_before_min) <= now:
            moment = (
                ReminderMoment.ADVANCE if meeting.remind_before_min > 0 else ReminderMoment.START
            )
            result.append(DueReminder(meeting, occurrence, moment))
    return result


def upcoming_meetings(
    meetings: Iterable[Meeting], now: datetime, limit: int
) -> list[tuple[datetime, Meeting]]:
    """The next `limit` (occurrence, meeting) pairs, soonest first."""
    pairs = [
        (occurrence, meeting)
        for meeting in meetings
        if (occurrence := next_occurrence(meeting, now)) is not None
    ]
    pairs.sort(key=lambda pair: pair[0])
    return pairs[:limit]


# -- Form -------------------------------------------------------------------------------------


def validate_meeting(
    title: str, repeat: MeetingRepeat, repeat_days: Sequence[Weekday]
) -> tuple[str, str] | None:
    """(title, message) of the first problem that prevents saving, or None."""
    if not title.strip():
        return "Falta el título", "Escribe un título para la reunión."
    if repeat is MeetingRepeat.WEEKLY and not repeat_days:
        return "Faltan días", "Elige al menos un día de la semana para la repetición semanal."
    return None


def round_up_to_next_half_hour(moment: datetime) -> datetime:
    """Next :00/:30 mark strictly after `moment` (10:30:00 sharp becomes 11:00)."""
    base = moment.replace(minute=0, second=0, microsecond=0)
    return base + timedelta(minutes=(moment.minute // 30 + 1) * 30)


def default_start_on_day(day: date, now: datetime) -> datetime:
    """Start proposed for a new meeting on a day without a time (Month view): the next half
    hour if it is today, 09:00 otherwise."""
    if day == now.date():
        return round_up_to_next_half_hour(now)
    return datetime.combine(day, datetime.min.time()).replace(hour=9)


# -- Labels -----------------------------------------------------------------------------------


def week_bounds(day: date) -> tuple[date, date]:
    """(Monday, next Monday) of the week that contains `day`."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=7)


def format_day_label(day: date) -> str:
    return (
        f"{WEEKDAY_NAMES[day.weekday()]}, {day.day} de {MONTH_NAMES[day.month - 1]} de {day.year}"
    )


def format_week_label(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday.day} - {sunday.day} de {MONTH_NAMES[monday.month - 1]} de {monday.year}"
    first, last = MONTH_NAMES[monday.month - 1][:3], MONTH_NAMES[sunday.month - 1][:3]
    if monday.year == sunday.year:
        return f"{monday.day} {first} - {sunday.day} {last} {monday.year}"
    return f"{monday.day} {first} {monday.year} - {sunday.day} {last} {sunday.year}"


def format_month_label(year: int, month: int) -> str:
    return f"{MONTH_NAMES[month - 1]} de {year}"


def format_duration(minutes: int) -> str:
    """30 -> "30 min", 60 -> "1 h", 90 -> "1 h 30 min"."""
    hours, rest = divmod(minutes, 60)
    if hours == 0:
        return f"{rest} min"
    return f"{hours} h" if rest == 0 else f"{hours} h {rest} min"


def meeting_tooltip(meeting: Meeting, occurrence: datetime) -> str:
    """Full title + time range (and notes) for a calendar block."""
    end = occurrence + timedelta(minutes=meeting.duration_min)
    text = f"{meeting.title}\n{occurrence:%H:%M} - {end:%H:%M} ({meeting.duration_min} min)"
    if occurrence_status(meeting, occurrence) is OccurrenceStatus.INACTIVE:
        text += "\n(inactiva)"
    if meeting.notes:
        text += f"\n{meeting.notes}"
    return text


# -- Day/Week grid geometry --------------------------------------------------------------------


def timeline_row(moment: datetime) -> tuple[int, bool]:
    """(row, clipped) of `moment` in the 07:00-21:00 grid of TIMELINE_SLOT_MIN rows. `row` is
    in [0, TIMELINE_ROWS]; `clipped` is True when `moment` falls outside the window."""
    day_start = moment.replace(hour=TIMELINE_START_HOUR, minute=0, second=0, microsecond=0)
    day_end = moment.replace(hour=TIMELINE_END_HOUR, minute=0, second=0, microsecond=0)
    if moment <= day_start:
        return 0, moment < day_start
    if moment >= day_end:
        return TIMELINE_ROWS, moment > day_end
    minutes = (moment - day_start).total_seconds() / 60
    return int(minutes // TIMELINE_SLOT_MIN), False


@dataclass(frozen=True, slots=True)
class BlockRows:
    first_row: int
    row_count: int
    clipped_top: bool
    clipped_bottom: bool


def block_rows(start: datetime, duration_min: int) -> BlockRows:
    """Rows a meeting block covers: ceil(duration / slot), at least one, clipped to the grid."""
    first_row, clipped_top = timeline_row(start)
    first_row = min(first_row, TIMELINE_ROWS - 1)
    wanted = max(1, math.ceil(duration_min / TIMELINE_SLOT_MIN))
    available = max(1, TIMELINE_ROWS - first_row)
    return BlockRows(first_row, min(wanted, available), clipped_top, wanted > available)


def assign_lanes(intervals: Sequence[tuple[Any, Any, Any]]) -> list[tuple[Any, int, int]]:
    """For each (start, end, item), the lane to draw it in so overlapping intervals sit side by
    side. Returns [(item, lane, lane_count)] in the SAME order as `intervals`; lane_count is
    the peak concurrency of the cluster of transitively overlapping intervals the item
    belongs to, so unrelated clusters do not widen each other.

    Sort by start, merge into clusters using the running max end (classic interval merge),
    then give each interval the first lane already free at its start (greedy sweep)."""
    result: list[tuple[Any, int, int]] = [(None, 0, 0)] * len(intervals)

    def resolve(cluster: list[int]) -> None:
        lane_ends: list[Any] = []
        lanes = {}
        for index in cluster:
            start, end, _item = intervals[index]
            lane = next((i for i, busy_until in enumerate(lane_ends) if busy_until <= start), None)
            if lane is None:
                lane = len(lane_ends)
                lane_ends.append(end)
            else:
                lane_ends[lane] = end
            lanes[index] = lane
        for index in cluster:
            result[index] = (intervals[index][2], lanes[index], len(lane_ends))

    cluster: list[int] = []
    cluster_end = None
    for index in sorted(range(len(intervals)), key=lambda i: intervals[i][0]):
        start, end, _item = intervals[index]
        if cluster and start < cluster_end:
            cluster.append(index)
            cluster_end = max(cluster_end, end)
        else:
            if cluster:
                resolve(cluster)
            cluster, cluster_end = [index], end
    if cluster:
        resolve(cluster)
    return result


def lane_columns(lane: int, lane_count: int) -> tuple[int, int]:
    """(first sub-column, width in sub-columns) inside the LANES_PER_DAY of a day. With more
    overlapping meetings than lanes, the extra ones reuse the last column (accepted)."""
    width = max(1, LANES_PER_DAY // min(lane_count, LANES_PER_DAY))
    return min(lane, LANES_PER_DAY - 1) * width, width


@dataclass(frozen=True, slots=True)
class PlacedBlock:
    meeting: Meeting
    occurrence: datetime
    rows: BlockRows
    first_column: int  # sub-column inside the day, 0..LANES_PER_DAY-1
    column_span: int


def day_blocks(meetings: Iterable[Meeting], day: date) -> list[PlacedBlock]:
    """Blocks to draw on `day`. Lanes are assigned on the quantized grid rows, not on clock
    time: 10:00-10:15 and 10:15-10:30 do not overlap on the clock but share one 30-min row,
    so assigning lanes on clock time would draw both in the same cell."""
    day_start = datetime.combine(day, datetime.min.time())
    intervals = []
    for meeting in meetings:
        for occurrence in visible_occurrences(meeting, day_start, day_start + timedelta(days=1)):
            rows = block_rows(occurrence, meeting.duration_min)
            intervals.append(
                (rows.first_row, rows.first_row + rows.row_count, (meeting, occurrence, rows))
            )
    placed = []
    for (meeting, occurrence, rows), lane, lane_count in assign_lanes(intervals):
        first_column, span = lane_columns(lane, lane_count)
        placed.append(PlacedBlock(meeting, occurrence, rows, first_column, span))
    return placed


def day_occurrences(meetings: Iterable[Meeting], day: date) -> list[tuple[datetime, Meeting]]:
    """Visible occurrences on `day`, in time order (Month view)."""
    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    pairs = [
        (occurrence, meeting)
        for meeting in meetings
        for occurrence in visible_occurrences(meeting, day_start, day_end)
    ]
    return sorted(pairs, key=lambda pair: pair[0])


def visible_pills(total: int, maximum: int = 3) -> tuple[int, int]:
    """(shown, hidden) meetings of one day in the Month view."""
    return (total, 0) if total <= maximum else (maximum, total - maximum)


def selection_range(day: date, row_a: int, row_b: int) -> tuple[datetime, int]:
    """(start, duration_min) of the grid rows between `row_a` and `row_b`, both included."""
    first, last = sorted((row_a, row_b))
    start = datetime.combine(day, datetime.min.time()) + timedelta(
        hours=TIMELINE_START_HOUR, minutes=first * TIMELINE_SLOT_MIN
    )
    return start, (last - first + 1) * TIMELINE_SLOT_MIN


def now_position(now: datetime) -> float | None:
    """`now` as a fractional row of the grid (row 3.5 = halfway through row 3), so the "now"
    line is exact to the minute. None outside the time window."""
    minutes = (now.hour - TIMELINE_START_HOUR) * 60 + now.minute + now.second / 60
    if minutes < 0 or minutes >= TIMELINE_ROWS * TIMELINE_SLOT_MIN:
        return None
    return minutes / TIMELINE_SLOT_MIN
