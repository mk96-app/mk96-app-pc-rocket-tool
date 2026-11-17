"""Adapters against the real Windows services they wrap (Credential Manager, registry, audio,
local sockets). Each test cleans up what it creates."""

import time
import winreg
from collections.abc import Callable
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from PySide6.QtCore import QCoreApplication

from mk96_app_pc_rocket_tool import autostart, secret_store, single_instance, workers
from mk96_app_pc_rocket_tool.paths import BUNDLED_SOUNDS_DIR
from mk96_app_pc_rocket_tool.secret_store import SecretStoreUnavailable, ServerCredentials
from mk96_app_pc_rocket_tool.sound_player import SoundPlaybackError, SoundPlayer

TEST_RUN_KEY = r"Software\mk96-app-pc-rocket-tool-tests\Run"
CREDENTIALS = ServerCredentials("https://chat.example.com", "uid-1", "secret-token")


def _process_events_until(condition: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


# -- Credential Manager ----------------------------------------------------------------------


@pytest.fixture
def test_server_id():
    server_id = "pytest-server-id"
    yield server_id
    secret_store.clear_server_credentials(server_id)


def test_credentials_roundtrip_through_credential_manager(test_server_id):
    assert secret_store.load_server_credentials(test_server_id) is None
    secret_store.save_server_credentials(test_server_id, CREDENTIALS)
    assert secret_store.load_server_credentials(test_server_id) == CREDENTIALS
    secret_store.clear_server_credentials(test_server_id)
    assert secret_store.load_server_credentials(test_server_id) is None
    secret_store.clear_server_credentials(test_server_id)  # clearing twice is fine


def test_credentials_repr_never_shows_the_token():
    assert "secret-token" not in repr(CREDENTIALS)


def test_credentials_refuse_any_backend_other_than_credential_manager(monkeypatch):
    class _PlainTextBackend(KeyringBackend):
        priority = 1  # type: ignore[assignment]

        def get_password(self, service, username):
            return None

        def set_password(self, service, username, password):
            raise AssertionError("must never be called")

        def delete_password(self, service, username):
            return None

    monkeypatch.setattr(keyring, "get_keyring", lambda: _PlainTextBackend())
    with pytest.raises(SecretStoreUnavailable, match="no está disponible"):
        secret_store.save_server_credentials("x", CREDENTIALS)


def test_damaged_stored_credential_is_reported(test_server_id):
    keyring.get_keyring().set_password(secret_store.SERVICE_NAME, test_server_id, "not json")
    with pytest.raises(SecretStoreUnavailable, match="dañada"):
        secret_store.load_server_credentials(test_server_id)


# -- Autostart (registry) ----------------------------------------------------------------------


@pytest.fixture
def run_key():
    yield TEST_RUN_KEY
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_RUN_KEY)
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_RUN_KEY.rsplit("\\", 1)[0])


def test_autostart_toggles_the_run_value(run_key):
    assert autostart.is_enabled(run_key) is False
    autostart.set_enabled(True, run_key)
    assert autostart.is_enabled(run_key) is True
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as key:
        value, _kind = winreg.QueryValueEx(key, autostart.RUN_VALUE_NAME)
    assert value == autostart.current_launch_command()
    autostart.set_enabled(False, run_key)
    autostart.set_enabled(False, run_key)
    assert autostart.is_enabled(run_key) is False


def test_launch_command_for_installed_exe_and_for_source_runs():
    exe = Path(r"C:\Apps\Rocket\mk96-app-pc-rocket-tool.exe")
    assert autostart.launch_command(True, exe) == f'"{exe}"'
    python = Path(r"C:\venv\Scripts\python.exe")
    assert autostart.launch_command(False, python) == (
        r'"C:\venv\Scripts\pythonw.exe" -m mk96_app_pc_rocket_tool'
    )


# -- Audio --------------------------------------------------------------------------------------


CLICK = BUNDLED_SOUNDS_DIR / "interface-sounds" / "click_001.ogg"


@pytest.fixture(scope="module")
def sound_errors() -> list[str]:
    return []


@pytest.fixture(scope="module")
def sound_player(qt_app, sound_errors):
    # One player for the whole module, as in the app, shut down the same way the app does.
    player = SoundPlayer(sound_errors.append)
    yield player
    player.shutdown()


def test_sound_player_plays_a_bundled_ogg_to_the_end(sound_player, sound_errors):
    sound_player.play_file(CLICK)
    assert not sound_player.is_idle()
    assert _process_events_until(sound_player.is_idle)
    assert sound_errors == []


def test_sound_player_overlaps_up_to_the_pool_size_and_skips_the_rest(sound_player):
    for _ in range(6):
        sound_player.play_file(CLICK)
    assert len(sound_player._players) == len(sound_player._busy) == 4
    assert _process_events_until(sound_player.is_idle)


def test_sound_player_reports_unknown_sound_ids(sound_player):
    with pytest.raises(SoundPlaybackError, match="no-such-sound"):
        sound_player.play("no-such-sound")


def test_sound_player_reports_undecodable_files(sound_player, sound_errors, tmp_path):
    broken = tmp_path / "broken.ogg"
    broken.write_bytes(b"this is not audio")
    sound_player.play_file(broken)
    assert _process_events_until(lambda: any("broken.ogg" in e for e in sound_errors))
    sound_player.play_file(CLICK)  # the pool still works after a failure
    assert _process_events_until(sound_player.is_idle)


# -- Threads and single instance ----------------------------------------------------------------


def test_run_in_thread_delivers_result_or_error_on_the_gui_thread(qt_app):
    import threading

    workers.init_gui_dispatcher()
    results: list[tuple[object, object, bool]] = []

    def record(result, error):
        results.append((result, error, threading.current_thread() is threading.main_thread()))

    workers.run_in_thread(lambda: 42, record)
    workers.run_in_thread(lambda: 1 / 0, record)
    assert _process_events_until(lambda: len(results) == 2)
    by_result = {r[0]: r for r in results}
    assert by_result[42][1] is None and by_result[42][2] is True
    assert isinstance(by_result[None][1], ZeroDivisionError) and by_result[None][2] is True


def test_second_launch_wakes_the_running_instance(qt_app):
    # Own channel name, so a real copy of the app open on this PC does not interfere.
    channel = f"{single_instance.SERVER_NAME}-pytest"
    assert single_instance.wake_running_instance(channel) is False
    activations: list[bool] = []
    server = single_instance.listen_for_activation(lambda: activations.append(True), channel)
    try:
        assert single_instance.wake_running_instance(channel) is True
        assert _process_events_until(lambda: activations == [True])
    finally:
        server.close()
