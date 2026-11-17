"""Start with Windows, per user, through the HKCU Run key (no admin rights).

The installer writes the same value when its "start with Windows" task is ticked and removes
it on uninstall, so the checkbox in Settings and the installer always agree. Keep
RUN_VALUE_NAME in sync with packaging/installer.iss.
"""

import contextlib
import sys
import winreg
from pathlib import Path

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "MK96 PC Rocket Tool"


def launch_command(is_frozen: bool, executable: Path) -> str:
    """Command line Windows runs at sign-in. From source, pythonw.exe avoids a console."""
    if is_frozen:
        return f'"{executable}"'
    pythonw = executable.with_name("pythonw.exe")
    return f'"{pythonw}" -m mk96_app_pc_rocket_tool'


def current_launch_command() -> str:
    return launch_command(getattr(sys, "frozen", False), Path(sys.executable))


def is_enabled(key_path: str = RUN_KEY_PATH) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, RUN_VALUE_NAME)
    except FileNotFoundError:
        return False
    return True


def set_enabled(enabled: bool, key_path: str = RUN_KEY_PATH) -> None:
    """Raises OSError if the registry refuses the change."""
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, current_launch_command())
            return
        with contextlib.suppress(FileNotFoundError):  # already disabled
            winreg.DeleteValue(key, RUN_VALUE_NAME)
