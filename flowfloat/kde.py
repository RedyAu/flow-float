"""KDE Plasma integration: desktop entry and KWin's virtual keyboard (input method) slot."""

import configparser
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "flow-float"
ICON = ROOT / "data" / "flow-float.svg"
DESKTOP_FILE = Path.home() / ".local" / "share" / "applications" / "flow-float.desktop"
AUTOSTART = Path.home() / ".config" / "autostart" / "flow-float.desktop"
PLASMA_KEYBOARD = "/usr/share/applications/org.kde.plasma.keyboard.desktop"


def desktop_entry(autostart=False):
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=Flow Float",
        "GenericName=Push-to-talk button",
        "Comment=Floating push-to-talk button for Wispr Flow, for use without a keyboard",
        f"Exec={LAUNCHER} --background" if autostart else f"Exec={LAUNCHER}",
        f"Icon={ICON}",
        "Categories=Utility;Accessibility;",
        "Keywords=wispr;dictation;voice;push to talk;tablet;",
    ]
    if autostart:
        lines.append("X-KDE-autostart-phase=2")
    else:
        # Makes Flow Float selectable under System Settings > Keyboard > Virtual Keyboard.
        lines.append("X-KDE-Wayland-VirtualKeyboard=true")
        lines += ["Actions=Settings;Toggle;", "",
                  "[Desktop Action Settings]", "Name=Settings", f"Exec={LAUNCHER} --settings", "",
                  "[Desktop Action Toggle]", "Name=Show/Hide Button", f"Exec={LAUNCHER} --toggle"]
    return "\n".join(lines) + "\n"


def install_desktop_file():
    DESKTOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DESKTOP_FILE.exists() or DESKTOP_FILE.read_text() != desktop_entry():
        DESKTOP_FILE.write_text(desktop_entry())


def autostart_enabled():
    return AUTOSTART.exists()


def set_autostart(on):
    if on:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text(desktop_entry(autostart=True))
    elif AUTOSTART.exists():
        AUTOSTART.unlink()


def current_input_method():
    try:
        return subprocess.run(["kreadconfig6", "--file", "kwinrc", "--group", "Wayland", "--key", "InputMethod"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def is_our_input_method():
    return current_input_method() == str(DESKTOP_FILE)


def input_method_name(path):
    if not path:
        return "None"
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read(path)
        return cp["Desktop Entry"].get("Name", Path(path).stem)
    except (KeyError, configparser.Error):
        return Path(path).stem


def set_input_method(path):
    """Point KWin's virtual keyboard at ``path``. KWin restarts the input method itself."""
    subprocess.run(["kwriteconfig6", "--file", "kwinrc", "--group", "Wayland", "--key", "InputMethod", "--notify",
                    path], check=False, timeout=5)
    subprocess.run(["busctl", "--user", "call", "org.kde.KWin", "/KWin", "org.kde.KWin", "reconfigure"],
                   check=False, timeout=5, capture_output=True)


def use_flow_float(settings):
    install_desktop_file()
    prev = current_input_method()
    if prev and prev != str(DESKTOP_FILE):
        settings.set("previous_im", prev)
    set_input_method(str(DESKTOP_FILE))


def restore_previous(settings):
    prev = settings.get("previous_im") or (PLASMA_KEYBOARD if Path(PLASMA_KEYBOARD).exists() else "")
    set_input_method(prev)
    return prev
