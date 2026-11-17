from dataclasses import replace
from datetime import date, datetime

import pytest

from mk96_app_pc_rocket_tool.meetings import (
    LANES_PER_DAY,
    TIMELINE_ROWS,
    BlockRows,
    DueReminder,
    OccurrenceStatus,
    ReminderMoment,
    assign_lanes,
    block_rows,
    day_blocks,
    default_start_on_day,
    due_meetings,
    edited_meeting,
    format_day_label,
    format_duration,
    format_month_label,
    format_week_label,
    lane_columns,
    meeting_tooltip,
    next_occurrence,
    now_position,
    occurrence_status,
    occurrences_in_range,
    round_up_to_next_half_hour,
    selection_range,
    startup_due_meetings,
    timeline_row,
    upcoming_meetings,
    validate_meeting,
    visible_occurrences,
    visible_pills,
    week_bounds,
    with_exception,
)
from mk96_app_pc_rocket_tool.models import Meeting, MeetingRepeat, OccurrenceState, Weekday


def _meeting(start=datetime(2026, 8, 20, 10, 0), **overrides) -> Meeting:
    return Meeting(id=overrides.pop("id", "m1"), title="Sync", start=start, **overrides)


def _daily(**overrides) -> Meeting:
    return _meeting(datetime(2026, 10, 12, 9, 30), repeat=MeetingRepeat.DAILY, **overrides)


# -- next_occurrence -------------------------------------------------------------------------


def test_next_occurrence_single_meeting_future_and_past():
    meeting = _meeting(datetime(2026, 8, 20, 10, 0))
    assert next_occurrence(meeting, datetime(2026, 8, 18, 9)) == datetime(2026, 8, 20, 10)
    assert next_occurrence(meeting, datetime(2026, 8, 21, 9)) is None


def test_next_occurrence_inactive_meeting_returns_none():
    assert next_occurrence(_meeting(active=False), datetime(2026, 8, 18, 9)) is None


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 18, 9, 0), datetime(2026, 8, 18, 10, 0)),
        (datetime(2026, 8, 18, 11, 0), datetime(2026, 8, 19, 10, 0)),
    ],
)
def test_next_occurrence_daily_rolls_to_tomorrow_after_the_time(now, expected):
    meeting = _meeting(datetime(2026, 8, 10, 10, 0), repeat=MeetingRepeat.DAILY)
    assert next_occurrence(meeting, now) == expected


def test_next_occurrence_daily_respects_series_end():
    meeting = _meeting(
        datetime(2026, 8, 10, 10, 0), repeat=MeetingRepeat.DAILY, repeat_until=datetime(2026, 8, 17)
    )
    assert next_occurrence(meeting, datetime(2026, 8, 18, 9)) is None


def test_next_occurrence_weekly_finds_next_chosen_weekday():
    meeting = _meeting(
        datetime(2026, 8, 3, 10, 0),
        repeat=MeetingRepeat.WEEKLY,
        repeat_days=(Weekday.MONDAY, Weekday.THURSDAY),
    )
    assert next_occurrence(meeting, datetime(2026, 8, 18, 9)) == datetime(2026, 8, 20, 10)
    assert next_occurrence(meeting, datetime(2026, 8, 20, 11)) == datetime(2026, 8, 24, 10)


@pytest.mark.parametrize(
    ("start", "now", "expected"),
    [
        (datetime(2026, 6, 15, 9), datetime(2026, 8, 18, 9), datetime(2026, 9, 15, 9)),
        (datetime(2026, 1, 31, 9), datetime(2026, 1, 31, 10), datetime(2026, 3, 31, 9)),
        (datetime(2018, 3, 15, 10), datetime(2026, 8, 1, 9), datetime(2026, 8, 15, 10)),
        (datetime(2018, 1, 31, 10), datetime(2026, 2, 1, 9), datetime(2026, 3, 31, 10)),
    ],
)
def test_next_occurrence_monthly_skips_months_without_that_day(start, now, expected):
    meeting = _meeting(start, repeat=MeetingRepeat.MONTHLY)
    assert next_occurrence(meeting, now) == expected


def test_next_occurrence_monthly_old_series_respects_its_end():
    meeting = _meeting(
        datetime(2018, 3, 15, 10), repeat=MeetingRepeat.MONTHLY, repeat_until=datetime(2020, 1, 1)
    )
    assert next_occurrence(meeting, datetime(2026, 8, 1, 9)) is None


# -- exceptions --------------------------------------------------------------------------------


def test_next_occurrence_skips_deleted_and_inactive_occurrences():
    meeting = with_exception(
        with_exception(_daily(), datetime(2026, 10, 13, 9, 30), OccurrenceState.DELETED),
        datetime(2026, 10, 14, 9, 30),
        OccurrenceState.INACTIVE,
    )
    assert next_occurrence(meeting, datetime(2026, 10, 13)) == datetime(2026, 10, 15, 9, 30)


def test_next_occurrence_ends_when_every_occurrence_is_excepted():
    meeting = _daily(
        repeat_until=datetime(2026, 10, 13),
        exceptions={
            datetime(2026, 10, 12, 9, 30): OccurrenceState.DELETED,
            datetime(2026, 10, 13, 9, 30): OccurrenceState.DELETED,
        },
    )
    assert next_occurrence(meeting, datetime(2026, 10, 1)) is None


def test_visible_occurrences_show_inactive_but_not_deleted():
    meeting = with_exception(
        with_exception(_daily(), datetime(2026, 10, 13, 9, 30), OccurrenceState.INACTIVE),
        datetime(2026, 10, 14, 9, 30),
        OccurrenceState.DELETED,
    )
    start, end = datetime(2026, 10, 12), datetime(2026, 10, 15)
    assert visible_occurrences(meeting, start, end) == [
        datetime(2026, 10, 12, 9, 30),
        datetime(2026, 10, 13, 9, 30),
    ]
    assert occurrences_in_range(meeting, start, end) == [datetime(2026, 10, 12, 9, 30)]
    assert occurrence_status(meeting, datetime(2026, 10, 13, 9, 30)) is OccurrenceStatus.INACTIVE
    assert occurrence_status(meeting, datetime(2026, 10, 12, 9, 30)) is OccurrenceStatus.ACTIVE


def test_inactive_series_is_drawn_dimmed_but_never_reminds():
    meeting = _daily(active=False)
    assert len(visible_occurrences(meeting, datetime(2026, 10, 12), datetime(2026, 10, 15))) == 3
    assert occurrence_status(meeting, datetime(2026, 10, 13, 9, 30)) is OccurrenceStatus.INACTIVE
    assert next_occurrence(meeting, datetime(2026, 10, 12)) is None


def test_with_exception_none_unmarks_and_never_mutates_the_original():
    meeting = _daily()
    marked = with_exception(meeting, datetime(2026, 10, 13, 9, 30), OccurrenceState.DELETED)
    assert meeting.exceptions == {}
    assert with_exception(marked, datetime(2026, 10, 13, 9, 30), None).exceptions == {}


def test_edited_meeting_keeps_exceptions_unless_the_series_changes():
    original = with_exception(_daily(), datetime(2026, 10, 13, 9, 30), OccurrenceState.DELETED)
    renamed = edited_meeting(original, replace(_daily(), title="Otro"))
    assert renamed.exceptions == {datetime(2026, 10, 13, 9, 30): OccurrenceState.DELETED}
    moved = edited_meeting(original, replace(_daily(), start=datetime(2026, 10, 12, 10)))
    assert moved.exceptions == {}
    assert edited_meeting(None, _daily()).exceptions == {}


# -- reminders ---------------------------------------------------------------------------------


def test_due_meetings_reminds_in_advance_and_again_at_start():
    meeting = _meeting(datetime(2026, 8, 18, 10), remind_before_min=10)
    occurrence = datetime(2026, 8, 18, 10)
    assert due_meetings([meeting], datetime(2026, 8, 18, 9, 50), datetime(2026, 8, 18, 9, 49)) == [
        DueReminder(meeting, occurrence, ReminderMoment.ADVANCE)
    ]
    assert due_meetings([meeting], datetime(2026, 8, 18, 10), datetime(2026, 8, 18, 9, 59, 30)) == [
        DueReminder(meeting, occurrence, ReminderMoment.START)
    ]


def test_due_meetings_without_advance_reminds_once_and_never_twice_across_ticks():
    meeting = _meeting(datetime(2026, 8, 18, 10))
    first = due_meetings([meeting], datetime(2026, 8, 18, 10), datetime(2026, 8, 18, 9, 59, 30))
    second = due_meetings([meeting], datetime(2026, 8, 18, 10, 0, 30), datetime(2026, 8, 18, 10))
    assert first == [DueReminder(meeting, datetime(2026, 8, 18, 10), ReminderMoment.START)]
    assert second == []


def test_due_meetings_ignores_inactive_and_not_yet_due_meetings():
    inactive = _meeting(datetime(2026, 8, 18, 10), active=False)
    later = _meeting(datetime(2026, 8, 18, 10), id="m2", remind_before_min=10)
    now, last_check = datetime(2026, 8, 18, 9, 30), datetime(2026, 8, 18, 9)
    assert due_meetings([inactive, later], now, last_check) == []


def test_due_meetings_skips_inactive_occurrence_of_a_series():
    meeting = with_exception(_daily(), datetime(2026, 10, 13, 9, 30), OccurrenceState.INACTIVE)
    assert (
        due_meetings([meeting], datetime(2026, 10, 13, 9, 31), datetime(2026, 10, 13, 9, 29)) == []
    )


@pytest.mark.parametrize(
    ("now", "remind_before", "expected_moment"),
    [
        (datetime(2026, 8, 18, 9, 55), 10, ReminderMoment.ADVANCE),
        (datetime(2026, 8, 18, 10, 0), 0, ReminderMoment.START),
        (datetime(2026, 8, 18, 9, 30), 10, None),
        (datetime(2026, 8, 18, 10, 30), 10, None),
    ],
)
def test_startup_due_meetings_recovers_only_open_reminder_windows(
    now, remind_before, expected_moment
):
    meeting = _meeting(datetime(2026, 8, 18, 10), remind_before_min=remind_before)
    expected = (
        []
        if expected_moment is None
        else [DueReminder(meeting, datetime(2026, 8, 18, 10), expected_moment)]
    )
    assert startup_due_meetings([meeting], now) == expected


def test_upcoming_meetings_are_sorted_and_limited():
    soon = _meeting(datetime(2026, 8, 18, 11), id="a")
    later = _meeting(datetime(2026, 8, 19, 9), id="b")
    past = _meeting(datetime(2026, 8, 17, 9), id="c")
    result = upcoming_meetings([later, past, soon], datetime(2026, 8, 18, 10), limit=1)
    assert result == [(datetime(2026, 8, 18, 11), soon)]


# -- form and labels ---------------------------------------------------------------------------


def test_validate_meeting_requires_title_and_weekly_days():
    assert validate_meeting(" ", MeetingRepeat.NONE, [])[0] == "Falta el título"  # type: ignore[index]
    assert validate_meeting("x", MeetingRepeat.WEEKLY, [])[0] == "Faltan días"  # type: ignore[index]
    assert validate_meeting("x", MeetingRepeat.WEEKLY, [Weekday.MONDAY]) is None


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 8, 20, 10, 17), datetime(2026, 8, 20, 10, 30)),
        (datetime(2026, 8, 20, 10, 35), datetime(2026, 8, 20, 11, 0)),
        (datetime(2026, 8, 20, 10, 30), datetime(2026, 8, 20, 11, 0)),
        (datetime(2026, 8, 20, 23, 45), datetime(2026, 8, 21, 0, 0)),
    ],
)
def test_round_up_to_next_half_hour_always_advances(moment, expected):
    assert round_up_to_next_half_hour(moment) == expected


def test_default_start_on_day_is_next_half_hour_today_and_9am_otherwise():
    now = datetime(2026, 10, 13, 15, 10)
    assert default_start_on_day(date(2026, 10, 13), now) == datetime(2026, 10, 13, 15, 30)
    assert default_start_on_day(date(2026, 10, 20), now) == datetime(2026, 10, 20, 9, 0)


def test_week_bounds_returns_monday_and_next_monday():
    assert week_bounds(date(2024, 1, 3)) == (date(2024, 1, 1), date(2024, 1, 8))


@pytest.mark.parametrize(
    ("monday", "expected"),
    [
        (date(2024, 1, 1), "1 - 7 de enero de 2024"),
        (date(2024, 1, 29), "29 ene - 4 feb 2024"),
        (date(2024, 12, 30), "30 dic 2024 - 5 ene 2025"),
    ],
)
def test_format_week_label_handles_month_and_year_changes(monday, expected):
    assert format_week_label(monday) == expected


def test_format_day_month_and_duration_labels():
    assert format_day_label(date(2024, 1, 1)) == "lunes, 1 de enero de 2024"
    assert format_month_label(2026, 8) == "agosto de 2026"
    assert [format_duration(m) for m in (30, 60, 90)] == ["30 min", "1 h", "1 h 30 min"]


def test_meeting_tooltip_has_title_range_inactive_mark_and_notes():
    meeting = _meeting(datetime(2026, 10, 13, 9, 30), active=False, notes="sala 2")
    text = meeting_tooltip(meeting, datetime(2026, 10, 13, 9, 30))
    assert "Sync" in text and "09:30 - 10:00" in text and "inactiva" in text and "sala 2" in text


# -- grid geometry -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 8, 20, 7, 0), (0, False)),
        (datetime(2026, 8, 20, 6, 0), (0, True)),
        (datetime(2026, 8, 20, 22, 0), (TIMELINE_ROWS, True)),
        (datetime(2026, 8, 20, 21, 0), (TIMELINE_ROWS, False)),
        (datetime(2026, 8, 20, 10, 17), (6, False)),
    ],
)
def test_timeline_row_floors_and_clips_to_the_window(moment, expected):
    assert timeline_row(moment) == expected


@pytest.mark.parametrize(
    ("start", "duration", "expected"),
    [
        (datetime(2026, 8, 20, 10, 0), 45, BlockRows(6, 2, False, False)),
        (datetime(2026, 8, 20, 20, 30), 60, BlockRows(27, 1, False, True)),
        (datetime(2026, 8, 20, 22, 0), 30, BlockRows(27, 1, True, False)),
    ],
)
def test_block_rows_rounds_up_and_clips(start, duration, expected):
    assert block_rows(start, duration) == expected


def test_assign_lanes_separates_overlaps_and_keeps_clusters_independent():
    intervals = [
        (9.0, 9.5, "alone"),
        (10.0, 11.0, "a"),
        (10.25, 10.75, "b"),
        (10.5, 11.5, "c"),
    ]
    lanes = {item: (lane, count) for item, lane, count in assign_lanes(intervals)}
    assert lanes["alone"] == (0, 1)
    assert {lanes["a"][0], lanes["b"][0], lanes["c"][0]} == {0, 1, 2}
    assert lanes["a"][1] == lanes["b"][1] == lanes["c"][1] == 3


def test_assign_lanes_chain_overlap_reuses_a_freed_lane():
    intervals = [(10, 11, "a"), (10.5, 11.5, "b"), (11.25, 12, "c")]
    assert [count for _item, _lane, count in assign_lanes(intervals)] == [2, 2, 2]
    assert assign_lanes([]) == []


@pytest.mark.parametrize(
    ("lane", "count", "expected"),
    [(0, 1, (0, 4)), (1, 2, (2, 2)), (2, 3, (2, 1)), (4, 5, (3, 1))],
)
def test_lane_columns_split_the_day_width(lane, count, expected):
    assert lane_columns(lane, count) == expected


def test_day_blocks_puts_back_to_back_short_meetings_in_separate_lanes():
    first = _meeting(datetime(2026, 8, 20, 10, 0), id="a", duration_min=15)
    second = _meeting(datetime(2026, 8, 20, 10, 15), id="b", duration_min=15)
    blocks = day_blocks([first, second], date(2026, 8, 20))
    assert sorted(b.first_column for b in blocks) == [0, LANES_PER_DAY // 2]


def test_visible_pills_caps_at_three():
    assert visible_pills(3) == (3, 0)
    assert visible_pills(7) == (3, 4)


def test_selection_range_is_inclusive_and_order_independent():
    assert selection_range(date(2026, 10, 13), 5, 2) == (datetime(2026, 10, 13, 8, 0), 120)
    assert selection_range(date(2026, 10, 13), 0, 0) == (datetime(2026, 10, 13, 7, 0), 30)


def test_now_position_is_exact_to_the_minute_inside_the_window():
    assert now_position(datetime(2026, 10, 13, 7, 0)) == 0.0
    assert now_position(datetime(2026, 10, 13, 15, 45)) == 17.5
    assert now_position(datetime(2026, 10, 13, 6, 59)) is None
    assert now_position(datetime(2026, 10, 13, 21, 0)) is None
