import pytest

from mk96_app_pc_rocket_tool.models import ChatEvent
from mk96_app_pc_rocket_tool.rules import match_rule, rooms_to_mark_read


def _event(room_id: str = "room1", mentioned: bool = False, mentioned_all: bool = False):
    return ChatEvent(room_id, "c", "ana", "hola", mentioned, mentioned_all)


def test_match_rule_without_rules_returns_none():
    assert match_rule(_event(), []) is None


def test_match_rule_room_rule_matches_by_room_id(room_rule):
    rule = room_rule("room1")
    assert match_rule(_event("room1"), [rule]) == rule


def test_match_rule_room_rule_ignores_other_rooms(room_rule):
    assert match_rule(_event("room2"), [room_rule("room1")]) is None


@pytest.mark.parametrize("kind", ["room", "mention"])
def test_match_rule_inactive_rule_is_ignored(room_rule, mention_rule, kind):
    rule = room_rule("room1", active=False) if kind == "room" else mention_rule(active=False)
    assert match_rule(_event("room1", mentioned=True), [rule]) is None


def test_match_rule_mention_rule_needs_a_mention(mention_rule):
    rule = mention_rule()
    assert match_rule(_event(mentioned=True), [rule]) == rule
    assert match_rule(_event(mentioned=False), [rule]) is None


def test_match_rule_room_rule_wins_over_mention_rule(room_rule, mention_rule):
    room, mention = room_rule("room1"), mention_rule()
    assert match_rule(_event("room1", mentioned=True), [mention, room]) == room


def test_match_rule_falls_back_to_mention_rule_in_unconfigured_room(room_rule, mention_rule):
    mention = mention_rule()
    assert match_rule(_event("room9", mentioned=True), [room_rule("room1"), mention]) == mention


def test_match_rule_at_all_needs_the_rule_to_opt_in(mention_rule):
    event = _event(mentioned_all=True)
    assert match_rule(event, [mention_rule()]) is None
    opted_in = mention_rule(mention_all=True)
    assert match_rule(event, [opted_in]) == opted_in


def _sub(rid, unread=0, alert=False):
    return {"rid": rid, "unread": unread, "alert": alert}


def test_rooms_to_mark_read_all_picks_unread_or_alerted_rooms(room_rule):
    subs = [_sub("a", unread=3), _sub("b"), _sub("c", alert=True), _sub("d", unread=1)]
    assert rooms_to_mark_read(subs, [room_rule("d")], only_unconfigured=False) == ["a", "c", "d"]


def test_rooms_to_mark_read_unconfigured_skips_active_room_rules_only(room_rule, mention_rule):
    subs = [_sub("a", unread=1), _sub("b", unread=1), _sub("c", unread=1)]
    rules = [room_rule("a"), room_rule("b", id="r2", active=False), mention_rule()]
    assert rooms_to_mark_read(subs, rules, only_unconfigured=True) == ["b", "c"]


def test_rooms_to_mark_read_skips_subscriptions_without_rid():
    assert rooms_to_mark_read([{"unread": 2}], [], only_unconfigured=False) == []
