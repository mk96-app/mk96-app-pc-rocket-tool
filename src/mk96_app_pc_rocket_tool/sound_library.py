"""Which sounds exist and where their files are: the bundled CC0 palette (one subfolder per
category) and the user's own sounds (flat folder in %APPDATA%, untouched by updates).

A sound id is "category/name" for bundled sounds and just "name" for the user's own.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

SOUND_EXTENSIONS = (".ogg", ".wav", ".mp3", ".flac")
USER_CATEGORY = "Tus sonidos"
CATEGORY_LABELS = {
    "digital-audio": "Digital",
    "impact-sounds": "Impactos",
    "interface-sounds": "Interfaz",
    "music-jingles": "Jingles musicales",
    "rpg-audio": "RPG",
    "sci-fi-sounds": "Ciencia ficción",
}


@dataclass(frozen=True, slots=True)
class SoundEntry:
    sound_id: str
    category: str  # display label
    name: str
    is_user_sound: bool


def display_name(sound_id: str) -> str:
    """'category/name' -> 'name'; user sounds have no category and stay as they are."""
    return sound_id.rsplit("/", 1)[-1]


def _is_audio(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SOUND_EXTENSIONS


def resolve_sound_path(sound_id: str, user_dir: Path, bundled_dir: Path) -> Path | None:
    """The user's own sounds win over bundled ones with the same id."""
    for directory in (user_dir, bundled_dir):
        for extension in SOUND_EXTENSIONS:
            candidate = directory / f"{sound_id}{extension}"
            if candidate.is_file():
                return candidate
    return None


def list_bundled_sounds(base_dir: Path) -> list[SoundEntry]:
    if not base_dir.is_dir():
        return []
    result = []
    for category_dir in sorted(p for p in base_dir.iterdir() if p.is_dir()):
        label = CATEGORY_LABELS.get(category_dir.name, category_dir.name)
        for file in sorted(category_dir.iterdir(), key=lambda p: p.name.lower()):
            if _is_audio(file):
                result.append(
                    SoundEntry(f"{category_dir.name}/{file.stem}", label, file.stem, False)
                )
    return result


def list_user_sounds(base_dir: Path) -> list[SoundEntry]:
    if not base_dir.is_dir():
        return []
    return [
        SoundEntry(file.stem, USER_CATEGORY, file.stem, True)
        for file in sorted(base_dir.iterdir(), key=lambda p: p.name.lower())
        if _is_audio(file)
    ]


def add_user_sound(source: Path, user_dir: Path) -> str:
    """Copies an audio file chosen by the user into their sounds folder; returns its id."""
    if source.suffix.lower() not in SOUND_EXTENSIONS:
        raise ValueError(f"Formato de audio no soportado: {source.suffix or '(sin extensión)'}")
    user_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, user_dir / source.name)
    return source.stem
