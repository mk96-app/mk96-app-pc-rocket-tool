"""Rule engine: which distinctive sound (if any) a new Rocket.Chat message deserves.

A matching room rule wins over the mention rule, so a key contact's own sound is never
covered by the generic mention sound and nothing sounds twice.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from mk96_app_pc_rocket_tool.models import ChatEvent, Rule, RuleType


def match_rule(event: ChatEvent, rules: Iterable[Rule]) -> Rule | None:
    active_rules = [rule for rule in rules if rule.active]

    for rule in active_rules:
        if rule.type is RuleType.ROOM and rule.room_id == event.room_id:
            return rule

    for rule in active_rules:
        if rule.type is not RuleType.MENTION:
            continue
        if event.mentioned or (event.mentioned_all and rule.mention_all):
            return rule

    return None


def rooms_to_mark_read(
    subscriptions: Iterable[Mapping[str, Any]], rules: Iterable[Rule], only_unconfigured: bool
) -> list[str]:
    """Room ids with unread messages. With `only_unconfigured`, rooms with an active room
    rule are skipped; a silenced rule does not count as configured (same as match_rule)."""
    configured = {rule.room_id for rule in rules if rule.type is RuleType.ROOM and rule.active}
    return [
        sub["rid"]
        for sub in subscriptions
        if sub.get("rid")
        and (sub.get("unread", 0) > 0 or sub.get("alert"))
        and not (only_unconfigured and sub["rid"] in configured)
    ]
