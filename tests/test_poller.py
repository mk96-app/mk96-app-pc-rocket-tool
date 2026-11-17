import pytest

from mk96_app_pc_rocket_tool.models import HistoryMessage, RepeatMode
from mk96_app_pc_rocket_tool.poller import (
    MAX_BACKOFF_TICKS,
    PendingReminder,
    Poller,
    PollOutcome,
    backoff_ticks,
    is_message_read,
    should_fetch_room,
    should_repeat,
)
from mk96_app_pc_rocket_tool.rest_client import AuthError, RestApiError

T0 = "2026-01-01T00:00:00.000Z"


class _FakeApi:
    def __init__(self):
        self.subs = []
        self.history = {}
        self.fail_subs = None
        self.fail_history = set()

    def list_subscriptions(self, updated_since=None):
        if self.fail_subs is not None:
            raise self.fail_subs
        return self.subs

    def get_own_username(self):
        return "yo"

    def room_history_since(self, room_id, room_type, oldest):
        if room_id in self.fail_history:
            raise RestApiError("boom")
        return self.history.get(room_id, [])


def _poller(api, rules):
    log = {"events": [], "status": [], "read": [], "auth": 0}

    def on_auth():
        log["auth"] += 1

    poller = Poller(
        api,
        30,
        lambda: rules,
        log["events"].append,
        log["status"].append,
        log["read"].append,
        on_auth,
    )
    return poller, log


def _tick(poller, rules):
    """One full poll: network work, then delivery on the 'GUI thread'."""
    poller._on_poll_done(poller._poll_once([r for r in rules if r.active]), None)


def _msg(ts, sender="ana", mentions=()):
    return HistoryMessage(sender, "hola", ts, tuple(mentions))


def test_is_message_read_compares_against_last_seen():
    assert is_message_read("2026-08-14T10:00:00.000Z", "2026-08-14T10:00:00.000Z") is True
    assert is_message_read("2026-08-14T10:01:00.000Z", "2026-08-14T10:00:00.000Z") is False
    assert is_message_read("2026-08-14T10:01:00.000Z", None) is False
    assert is_message_read(None, "2026-08-14T10:00:00.000Z") is False


def test_should_fetch_room_only_for_ruled_rooms_unless_mention_rule(room_rule):
    rules = [room_rule("room1")]
    assert should_fetch_room("room1", rules, has_active_mention_rule=False) is True
    assert should_fetch_room("room2", rules, has_active_mention_rule=False) is False
    assert should_fetch_room("room9", rules, has_active_mention_rule=True) is True


@pytest.mark.parametrize(
    ("mode", "count", "every", "sounded", "tick", "expected"),
    [
        (RepeatMode.ONCE, None, 1, 1, 5, False),
        (RepeatMode.UNTIL_READ, None, 2, 1, 1, False),
        (RepeatMode.UNTIL_READ, None, 2, 1, 2, True),
        (RepeatMode.N_TIMES, 2, 1, 2, 5, False),
        (RepeatMode.N_TIMES, 3, 1, 1, 1, True),
    ],
)
def test_should_repeat(room_rule, mode, count, every, sounded, tick, expected):
    rule = room_rule(repeat_mode=mode, repeat_count=count, repeat_every_polls=every)
    pending = PendingReminder(None, rule.id, T0, sounded, 0)  # type: ignore[arg-type]
    assert should_repeat(pending, rule, tick) is expected


def test_backoff_ticks_grows_and_is_capped():
    assert [backoff_ticks(n) for n in (0, 1, 2, 3, 4)] == [0, 0, 1, 3, 7]
    assert backoff_ticks(50) == MAX_BACKOFF_TICKS


def test_poller_baseline_is_silent_then_new_unread_message_sounds(room_rule):
    api = _FakeApi()
    api.subs = [{"rid": "r1", "t": "p", "ls": T0}]
    rules = [room_rule("r1")]
    poller, log = _poller(api, rules)
    _tick(poller, rules)
    assert log["events"] == [] and log["status"] == [True]

    api.history["r1"] = [_msg("2026-01-01T00:05:00.000Z")]
    _tick(poller, rules)
    assert [(e.room_id, e.room_type) for e in log["events"]] == [("r1", "p")]


def test_poller_ignores_own_and_already_read_messages(room_rule):
    api = _FakeApi()
    api.subs = [{"rid": "r1", "t": "c", "ls": "2026-01-01T00:10:00.000Z"}]
    rules = [room_rule("r1")]
    poller, log = _poller(api, rules)
    _tick(poller, rules)
    api.history["r1"] = [
        _msg("2026-01-01T00:05:00.000Z"),
        _msg("2026-01-01T00:20:00.000Z", sender="yo"),
    ]
    _tick(poller, rules)
    assert log["events"] == []


def test_poller_at_all_sounds_only_for_rules_that_opt_in(mention_rule):
    api = _FakeApi()
    api.subs = [{"rid": "r1", "t": "c", "ls": T0}]
    plain = [mention_rule()]
    poller, log = _poller(api, plain)
    _tick(poller, plain)
    api.history["r1"] = [_msg("2026-01-01T00:05:00.000Z", mentions=["all"])]
    _tick(poller, plain)
    assert log["events"] == []

    opted_in = [mention_rule(mention_all=True)]
    api.history["r1"] = [_msg("2026-01-01T00:06:00.000Z", mentions=["here"])]
    _tick(poller, opted_in)
    assert len(log["events"]) == 1 and log["events"][0].mentioned_all is True


def test_poller_repeats_until_the_room_is_read_then_reports_it(room_rule):
    api = _FakeApi()
    api.subs = [{"rid": "r1", "t": "c", "ls": T0}]
    rules = [room_rule("r1", repeat_mode=RepeatMode.UNTIL_READ)]
    poller, log = _poller(api, rules)
    _tick(poller, rules)
    api.history["r1"] = [_msg("2026-01-01T00:05:00.000Z")]
    _tick(poller, rules)
    api.history, api.subs = {}, []
    _tick(poller, rules)
    assert len(log["events"]) == 2

    api.subs = [{"rid": "r1", "t": "c", "ls": "2026-01-01T00:10:00.000Z"}]
    _tick(poller, rules)
    api.subs = []
    _tick(poller, rules)
    assert len(log["events"]) == 2 and log["read"] == ["r1"]


def test_poller_network_failure_backs_off_and_reports_once_then_recovers():
    api = _FakeApi()
    api.fail_subs = RestApiError("Connection refused")
    poller, log = _poller(api, [])
    for _ in range(3):
        _tick(poller, [])
    assert log["status"] == [False] and poller._skip_ticks == backoff_ticks(3)
    assert "Connection refused" in (poller.last_error or "")

    api.fail_subs = None
    _tick(poller, [])
    assert log["status"] == [False, True] and poller.last_error is None and poller.username == "yo"


def test_poller_auth_error_stops_with_specific_warning_only():
    api = _FakeApi()
    api.fail_subs = AuthError("401", status=401)
    poller, log = _poller(api, [])
    _tick(poller, [])
    assert log["auth"] == 1 and log["status"] == [] and poller._stopped
    assert "rechazadas" in (poller.last_error or "")


def test_poller_retries_a_room_whose_history_failed_without_new_activity(room_rule):
    api = _FakeApi()
    api.subs = [{"rid": "r1", "t": "c", "ls": T0}]
    rules = [room_rule("r1")]
    poller, log = _poller(api, rules)
    _tick(poller, rules)
    api.fail_history = {"r1"}
    _tick(poller, rules)
    api.fail_history, api.subs = set(), []
    api.history["r1"] = [_msg("2026-01-01T00:05:00.000Z")]
    _tick(poller, rules)
    assert len(log["events"]) == 1


def test_poller_ignores_results_after_stop_and_counts_unexpected_errors():
    poller, log = _poller(_FakeApi(), [])
    outcome = poller._poll_once([])
    poller.stop()
    poller._on_poll_done(outcome, None)
    assert log["status"] == []

    fresh, fresh_log = _poller(_FakeApi(), [])
    fresh._on_poll_done(PollOutcome(), RuntimeError("bug"))
    assert fresh_log["status"] == [False] and fresh._failures == 1
