"""Texts of the desktop notifications (Windows toasts shown through the tray icon)."""

from mk96_app_pc_rocket_tool.meetings import ReminderMoment

MAX_BODY_CHARS = 200  # Windows truncates toasts anyway; keep the useful part


def special_message_text(label: str, sender: str | None, text: str) -> tuple[str, str]:
    """(title, body) for a message that matched a rule."""
    title = label or sender or "Rocket.Chat"
    body = f"{sender}: {text}" if sender else text
    if len(body) > MAX_BODY_CHARS:
        body = body[: MAX_BODY_CHARS - 1] + "…"
    return title, body


def meeting_reminder_text(
    title: str, time_text: str, link: str | None, moment: ReminderMoment
) -> tuple[str, str]:
    """(title, body) for a meeting reminder, in advance or at its start."""
    prefix = "Reunión en breve" if moment is ReminderMoment.ADVANCE else "Reunión ahora"
    body = f"A las {time_text}"
    if link:
        body += f"\n{link}"
    return f"{prefix}: {title}", body
