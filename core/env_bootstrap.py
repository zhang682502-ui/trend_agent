# core/env_bootstrap.py
from __future__ import annotations

import os
import platform


def refresh_windows_path_from_registry() -> None:
    """
    Windows-only: refresh current process PATH from Machine + User registry values.

    Why:
    - New terminals sometimes don't see updated PATH immediately.
    - We need ffmpeg to be discoverable for Telegram voice conversion.
    """
    if platform.system() != "Windows":
        return

    try:
        import winreg  # type: ignore

        # Machine PATH
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ) as key:
            machine_path, _ = winreg.QueryValueEx(key, "Path")

        # User PATH (may not exist)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                user_path, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            user_path = ""

        # Set for this Python process only
        os.environ["Path"] = f"{machine_path};{user_path}".strip(";")

    except Exception:
        # If anything goes wrong, don't block startup.
        return