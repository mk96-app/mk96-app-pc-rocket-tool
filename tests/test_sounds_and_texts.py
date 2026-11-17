import pytest

from mk96_app_pc_rocket_tool.meetings import ReminderMoment
from mk96_app_pc_rocket_tool.notifier import meeting_reminder_text, special_message_text
from mk96_app_pc_rocket_tool.paths import BUNDLED_SOUNDS_DIR
from mk96_app_pc_rocket_tool.sound_library import (
    USER_CATEGORY,
    SoundEntry,
    add_user_sound,
    display_name,
    list_bundled_sounds,
    list_user_sounds,
    resolve_sound_path,
)


def test_resolve_sound_path_prefers_user_sounds_and_ignores_missing_dirs(tmp_path):
    user, bundled = tmp_path / "user", tmp_path / "bundled"
    (bundled / "cat").mkdir(parents=True)
    (bundled / "cat" / "meow.ogg").write_bytes(b"b")
    assert resolve_sound_path("cat/meow", user, bundled) == bundled / "cat" / "meow.ogg"
    user.mkdir()
    (user / "dog.wav").write_bytes(b"u")
    (bundled / "dog.mp3").write_bytes(b"b")
    assert resolve_sound_path("dog", user, bundled) == user / "dog.wav"
    assert resolve_sound_path("ghost", user, bundled) is None


def test_list_bundled_sounds_groups_by_category_and_skips_non_audio(tmp_path):
    (tmp_path / "digital-audio").mkdir()
    (tmp_path / "digital-audio" / "tone1.ogg").write_bytes(b"x")
    (tmp_path / "digital-audio" / "License.txt").write_text("cc0")
    assert list_bundled_sounds(tmp_path) == [
        SoundEntry("digital-audio/tone1", "Digital", "tone1", False)
    ]
    assert list_bundled_sounds(tmp_path / "missing") == []


def test_bundled_palette_ships_all_501_sounds_and_the_default_meeting_sound():
    sounds = list_bundled_sounds(BUNDLED_SOUNDS_DIR)
    assert len(sounds) == 501
    assert "interface-sounds/confirmation_001" in {s.sound_id for s in sounds}


def test_user_sounds_are_flat_and_added_by_copy(tmp_path):
    source = tmp_path / "mi sonido.mp3"
    source.write_bytes(b"x")
    user_dir = tmp_path / "user"
    assert add_user_sound(source, user_dir) == "mi sonido"
    assert source.exists()
    assert list_user_sounds(user_dir) == [SoundEntry("mi sonido", USER_CATEGORY, "mi sonido", True)]
    with pytest.raises(ValueError, match="no soportado"):
        add_user_sound(tmp_path / "notes.txt", user_dir)


def test_display_name_strips_the_category():
    assert display_name("digital-audio/tone1") == "tone1"
    assert display_name("mi-sonido") == "mi-sonido"


def test_special_message_text_uses_label_and_sender_and_truncates():
    assert special_message_text("Ana", "ana", "hola") == ("Ana", "ana: hola")
    assert special_message_text("", None, "hola") == ("Rocket.Chat", "hola")
    _title, body = special_message_text("x", "ana", "a" * 500)
    assert len(body) == 200 and body.endswith("…")


def test_meeting_reminder_text_differs_for_advance_and_start():
    title, body = meeting_reminder_text("Daily", "09:30", "https://meet/x", ReminderMoment.ADVANCE)
    assert title == "Reunión en breve: Daily" and body == "A las 09:30\nhttps://meet/x"
    title, body = meeting_reminder_text("Daily", "09:30", None, ReminderMoment.START)
    assert title == "Reunión ahora: Daily" and body == "A las 09:30"
