"""Pressing and holding key combinations at the evdev level.

Two ways of producing key events:

* ``uinput``  - our own persistent virtual keyboard. Seen by KWin and by any
  program that reads /dev/input, as long as that program noticed the device.
* ``inject``  - write events straight into a keyboard node that the listener
  (e.g. the Wispr Flow helper) already has open. The kernel delivers them to
  every reader of that node. This works even when the listener's hotplug
  watcher missed our virtual keyboard.

``auto`` uses uinput when the Wispr Flow helper can see it (or isn't running)
and falls back to injection otherwise.
"""

import json
import os
import time
from pathlib import Path

import evdev
from evdev import ecodes as e

DEVICE_NAME = "Flow Float virtual keyboard"
MAX_KEY_DELAY_MS = 50  # delays run on the GUI thread; keep them short
WISPR_CONFIG = Path.home() / ".config" / "Wispr Flow" / "config.json"

MODIFIERS = [  # (label, evdev name) - offered as toggle buttons in the settings
    ("Ctrl", "KEY_LEFTCTRL"),
    ("Shift", "KEY_LEFTSHIFT"),
    ("Alt", "KEY_LEFTALT"),
    ("Meta", "KEY_LEFTMETA"),
]

_PRETTY = {
    "LEFTCTRL": "Ctrl", "RIGHTCTRL": "Right Ctrl", "LEFTSHIFT": "Shift", "RIGHTSHIFT": "Right Shift",
    "LEFTALT": "Alt", "RIGHTALT": "AltGr", "LEFTMETA": "Meta", "RIGHTMETA": "Right Meta",
    "ESC": "Esc", "SPACE": "Space", "ENTER": "Enter", "BACKSPACE": "Backspace", "CAPSLOCK": "Caps Lock",
    "PAGEUP": "Page Up", "PAGEDOWN": "Page Down", "SCROLLLOCK": "Scroll Lock", "SYSRQ": "Print",
    "COMPOSE": "Menu",
}

# Windows virtual-key codes (used by Wispr Flow's config) -> evdev names.
_VK = {
    8: "BACKSPACE", 9: "TAB", 13: "ENTER", 16: "LEFTSHIFT", 17: "LEFTCTRL", 18: "LEFTALT", 19: "PAUSE",
    20: "CAPSLOCK", 27: "ESC", 32: "SPACE", 33: "PAGEUP", 34: "PAGEDOWN", 35: "END", 36: "HOME",
    37: "LEFT", 38: "UP", 39: "RIGHT", 40: "DOWN", 44: "SYSRQ", 45: "INSERT", 46: "DELETE",
    91: "LEFTMETA", 92: "RIGHTMETA", 93: "COMPOSE",
    160: "LEFTSHIFT", 161: "RIGHTSHIFT", 162: "LEFTCTRL", 163: "RIGHTCTRL", 164: "LEFTALT", 165: "RIGHTALT",
    186: "SEMICOLON", 187: "EQUAL", 188: "COMMA", 189: "MINUS", 190: "DOT", 191: "SLASH", 192: "GRAVE",
    219: "LEFTBRACE", 220: "BACKSLASH", 221: "RIGHTBRACE", 222: "APOSTROPHE",
}
_VK.update({c: chr(c) for c in range(ord("A"), ord("Z") + 1)})
_VK.update({c: chr(c) for c in range(ord("0"), ord("9") + 1)})
_VK.update({111 + n: f"F{n}" for n in range(1, 25)})

WISPR_ACTIONS = {
    "ptt": "Push to talk",
    "popo": "Hands-free mode",
    "lens": "Command mode",
    "dismiss": "Dismiss",
    "undo": "Undo",
    "paste_event": "Paste",
    "paste_last_text": "Paste last transcript",
    "copy_last_text": "Copy last transcript",
    "open_meeting_recorder": "Meeting recorder",
}


def key_label(name):
    short = name.removeprefix("KEY_")
    return _PRETTY.get(short, short.title() if len(short) > 1 else short)


def combo_label(names):
    return " + ".join(key_label(n) for n in names) if names else "(nothing)"


def selectable_keys():
    """All ordinary keyboard keys, as evdev names, for the key picker."""
    names = set()
    for code in range(1, 0x2ff):
        n = e.KEY.get(code)
        if n is None:
            continue
        # Aliased codes come as a list/tuple of names; the last one is the common name (KEY_MUTE).
        names.add(n if isinstance(n, str) else n[-1])
    return sorted(names, key=lambda n: e.ecodes[n])


def wispr_shortcuts():
    """{action: [evdev names]} read from Wispr Flow's config, or {} if unavailable."""
    try:
        binds = json.loads(WISPR_CONFIG.read_text())["prefs"]["cache"]["splitKeybinds"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    out = {}
    for b in binds:
        keys = [_VK.get(vk) for vk in b.get("shortcut") or []]
        if keys and all(keys):
            # Wispr lists the main key between modifiers (Alt+Space+Meta); hold modifiers first.
            keys = [f"KEY_{k}" for k in keys]
            mods = [k for k in keys if any(m in k for m in ("CTRL", "SHIFT", "ALT", "META"))]
            out[b.get("value")] = mods + [k for k in keys if k not in mods]
    return out


_helper_pids = []  # cached Wispr Flow helper PIDs; rescanned only when they are gone


def _is_wispr_helper(pid):
    try:
        # " (deleted)" appears when Wispr Flow was updated while running.
        return os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)").endswith("wispr-flow-linux-helper")
    except OSError:
        return False


def _wispr_helper_inputs():
    """Event nodes opened by running Wispr Flow helpers, or None if no helper runs."""
    global _helper_pids
    if not _helper_pids or not all(_is_wispr_helper(p) for p in _helper_pids):
        _helper_pids = [p for p in filter(str.isdigit, os.listdir("/proc")) if _is_wispr_helper(p)]
    found = None
    for pid in _helper_pids:
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        found = found or set()
        for fd in fds:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if target.startswith("/dev/input/event"):
                found.add(target)
    return found


class KeyHolder:
    def __init__(self):
        self._uinput = None
        self.uinput_error = None
        self._held = []
        self._extra_down = set()  # single keys (Backspace, Enter) currently down
        self._inject_dev = None
        self.backend_used = None
        self.ensure_uinput()

    def ensure_uinput(self):
        if self._uinput is not None:
            return True
        try:
            keys = sorted({c for n, c in e.ecodes.items() if n.startswith("KEY_") and c < 0x2ff})
            self._uinput = evdev.UInput({e.EV_KEY: keys}, name=DEVICE_NAME)
            self.uinput_error = None
            return True
        except Exception as exc:  # PermissionError, missing module, ...
            self.uinput_error = str(exc)
            return False

    @property
    def uinput_path(self):
        return self._uinput.device.path if self._uinput else None

    def describe_route(self, backend="auto"):
        """Human readable description of what press() would do right now."""
        kind, path = self._route(backend, [])
        if kind == "uinput":
            text = f"Virtual keyboard ({self.uinput_path})"
            listened = _wispr_helper_inputs()
            if backend == "auto" and listened is not None and self.uinput_path not in listened:
                text += " (Wispr Flow has no keyboard to inject into, so this may not reach it)"
            return text
        if kind == "inject":
            try:
                name = evdev.InputDevice(path).name
            except OSError:
                name = "?"
            return f"Injecting into {path} ({name}), a keyboard Wispr Flow reads"
        if backend == "uinput":
            return "Virtual keyboard unavailable: " + (self.uinput_error or "?")
        if backend == "inject":
            return "No writable keyboard device to inject into"
        return "No way to send keys: " + (self.uinput_error or "no writable keyboard")

    def _route(self, backend, codes):
        """Pick ("uinput" | "inject", device path) or (None, None).

        Only "auto" falls back from one method to the other; an explicit choice
        either works or reports an error.
        """
        if backend == "uinput":
            return ("uinput", self.uinput_path) if self.ensure_uinput() else (None, None)
        listened = _wispr_helper_inputs()
        if backend == "auto" and (listened is None or self.uinput_path in listened) and self.ensure_uinput():
            return "uinput", self.uinput_path
        candidates = sorted(listened or ()) or sorted(evdev.list_devices())
        for path in candidates:
            if path == self.uinput_path:
                continue
            try:
                dev = evdev.InputDevice(path)
                supported = set(dev.capabilities().get(e.EV_KEY, []))
                dev.close()
            except OSError:
                continue
            if e.KEY_A in supported and all(c in supported for c in codes):
                return "inject", path
        if backend == "auto" and self.ensure_uinput():
            return "uinput", self.uinput_path
        return None, None

    def _write(self, code, value):
        if self._inject_dev is not None:
            self._inject_dev.write(e.EV_KEY, code, value)
            self._inject_dev.write(e.EV_SYN, e.SYN_REPORT, 0)
        else:
            self._uinput.write(e.EV_KEY, code, value)
            self._uinput.syn()

    @property
    def holding(self):
        return bool(self._held)

    def press(self, names, backend="auto", delay_ms=10):
        """Press the given keys in order and keep them down. Returns an error string or None."""
        delay_ms = min(delay_ms, MAX_KEY_DELAY_MS)
        self.release()
        codes = [e.ecodes[n] for n in names if n in e.ecodes]
        if not codes:
            return "No key combination configured"
        kind, path = self._route(backend, codes)
        if kind is None:
            return self.describe_route(backend)
        try:
            if kind == "inject":
                self._inject_dev = evdev.InputDevice(path)
            self.backend_used = kind
            for i, code in enumerate(codes):
                if i and delay_ms:
                    time.sleep(delay_ms / 1000)
                self._write(code, 1)
                self._held.append(code)
        except OSError as exc:
            self.release()
            return f"Sending keys failed: {exc}"
        return None

    def release(self, delay_ms=10):
        delay_ms = min(delay_ms, MAX_KEY_DELAY_MS)
        while self._held:
            code = self._held.pop()
            try:
                self._write(code, 0)
            except OSError:
                pass
            if self._held and delay_ms:
                time.sleep(delay_ms / 1000)
        if self._inject_dev is not None:
            self._inject_dev.close()
            self._inject_dev = None

    def key(self, name, down):
        """Press or release a single ordinary key (Backspace, Enter) for the focused app.

        Goes through our virtual keyboard so KWin delivers it like a real key
        press; the app then auto-repeats it while held. Returns an error or None.
        """
        if not self.ensure_uinput():
            return "No virtual keyboard: " + (self.uinput_error or "?")
        code = e.ecodes[name]
        self._uinput.write(e.EV_KEY, code, 1 if down else 0)
        self._uinput.syn()
        if down:
            self._extra_down.add(code)
        else:
            self._extra_down.discard(code)
        return None

    def close(self):
        self.release()
        for code in list(self._extra_down):
            self.key(e.KEY[code] if isinstance(e.KEY[code], str) else e.KEY[code][-1], False)
        if self._uinput is not None:
            self._uinput.close()
            self._uinput = None
