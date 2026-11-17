"""Plays sounds with QtMultimedia (FFmpeg backend: .ogg, .wav, .mp3, .flac).

Playback is asynchronous so the GUI never waits for a sound to finish. A small pool of players
lets alerts that arrive together overlap instead of cutting each other off; beyond the pool
size, extra sounds are skipped. Players are reused, never destroyed: deleting a QMediaPlayer
right after it finished crashed the process with an access violation inside Qt's FFmpeg
backend (PySide6 6.12, reproduced in the tests).
"""

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from mk96_app_pc_rocket_tool import paths
from mk96_app_pc_rocket_tool.sound_library import resolve_sound_path

logger = logging.getLogger(__name__)

MAX_SIMULTANEOUS_SOUNDS = 4


class SoundPlaybackError(Exception):
    """The sound could not be found or played."""


class SoundPlayer(QObject):
    def __init__(self, on_error: Callable[[str], None]):
        """on_error(message) reports failures that only show up while decoding."""
        super().__init__()
        self._on_error = on_error
        self._players: list[QMediaPlayer] = []
        # Tracked by hand: playbackState() still says "stopped" until the media has loaded.
        self._busy: set[QMediaPlayer] = set()

    def play(self, sound_id: str) -> None:
        """Starts playing and returns at once. Raises SoundPlaybackError if no file exists."""
        path = resolve_sound_path(sound_id, paths.user_sounds_dir(), paths.BUNDLED_SOUNDS_DIR)
        if path is None:
            raise SoundPlaybackError(f"No se encontró ningún archivo de audio para «{sound_id}».")
        self.play_file(path)

    def play_file(self, path: Path) -> None:
        player = self._free_player()
        if player is None:
            logger.info(
                "Skipping %s: %d sounds already playing", path.name, MAX_SIMULTANEOUS_SOUNDS
            )
            return
        self._busy.add(player)
        player.setSource(QUrl.fromLocalFile(str(path)))
        player.play()

    def is_idle(self) -> bool:
        return not self._busy

    def shutdown(self) -> None:
        """Stops and releases every player while the Qt application still exists; players
        left for interpreter shutdown can block the process from exiting."""
        for player in self._players:
            player.stop()
            player.setSource(QUrl())
        self._busy.clear()

    def _free_player(self) -> QMediaPlayer | None:
        """A player that is not playing, or None when the pool is full and all are busy.
        A busy player is never restarted: stop() + setSource() on a player that is still
        loading deadlocked Qt's FFmpeg backend (reproduced in the tests)."""
        for player in self._players:
            if player not in self._busy:
                return player
        if len(self._players) < MAX_SIMULTANEOUS_SOUNDS:
            return self._new_player()
        return None

    def _new_player(self) -> QMediaPlayer:
        player = QMediaPlayer(self)
        player.setAudioOutput(QAudioOutput(player))

        def on_status(status: QMediaPlayer.MediaStatus) -> None:
            if status in (
                QMediaPlayer.MediaStatus.EndOfMedia,
                QMediaPlayer.MediaStatus.InvalidMedia,
            ):
                self._busy.discard(player)

        def on_error(_error: QMediaPlayer.Error, message: str) -> None:
            self._busy.discard(player)
            source = player.source().toLocalFile()
            logger.warning("Could not play %s: %s", source, message)
            self._on_error(f"No se pudo reproducir «{Path(source).name}»: {message}")

        player.mediaStatusChanged.connect(on_status)
        player.errorOccurred.connect(on_error)
        self._players.append(player)
        return player
