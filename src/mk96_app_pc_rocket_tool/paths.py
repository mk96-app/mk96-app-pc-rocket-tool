"""Where the app keeps its files on Windows.

Roaming %APPDATA% holds what the user would miss on another PC (config, own sounds);
%LOCALAPPDATA% holds machine-local logs. The installer never writes or deletes either, so
updates and uninstalls keep the user's data.
"""

import os
from pathlib import Path

from mk96_app_pc_rocket_tool import APP_ID

PACKAGE_DIR = Path(__file__).resolve().parent
RESOURCES_DIR = PACKAGE_DIR / "resources"
BUNDLED_SOUNDS_DIR = RESOURCES_DIR / "sounds" / "default"
TRAY_ICON_SVG = RESOURCES_DIR / "tray-icon.svg"
APP_ICON_SVG = RESOURCES_DIR / "app-icon.svg"


def _known_folder(variable: str) -> Path:
    value = os.environ.get(variable)
    if not value:
        raise RuntimeError(f"Environment variable {variable} is not set")
    return Path(value)


def config_dir() -> Path:
    return _known_folder("APPDATA") / APP_ID


def config_path() -> Path:
    return config_dir() / "config.yaml"


def user_sounds_dir() -> Path:
    return config_dir() / "sounds"


def log_dir() -> Path:
    return _known_folder("LOCALAPPDATA") / APP_ID / "logs"
