"""Settings, stored in ~/.config/flowfloat/flowfloat.conf."""

from PySide6.QtCore import QObject, QSettings, Signal

from . import keys

DEFAULTS = {
    "combo": ["KEY_LEFTALT", "KEY_LEFTMETA"],
    "latch_ms": 300,              # mic presses shorter than this latch; 0 = always hold-to-talk
    "backend": "auto",            # auto | uinput | inject
    "show_when": "text",          # text (a text field has focus) | always
    "tablet_only": True,          # only show in KWin tablet mode
    "orientation": "vertical",    # vertical | horizontal
    "show_backspace": True,
    "show_enter": True,           # Return key
    "return_long_ms": 500,        # long-press Return this long for Shift+Return
    "size": 60,                   # mic button diameter
    "idle_opacity": 0.85,
    "key_delay_ms": 10,
    "max_hold_s": 300,
    "corner_h": "right",          # left | right
    "corner_v": "bottom",         # top | bottom
    "margin_h": 32,
    "margin_v": 160,
    "previous_im": "",            # virtual keyboard to restore, e.g. Plasma Keyboard
    "first_run": True,
}

_TYPES = {k: type(v) for k, v in DEFAULTS.items()}


class Settings(QObject):
    changed = Signal(str)  # key name

    def __init__(self):
        super().__init__()
        self._qs = QSettings("flowfloat", "flowfloat")  # -> ~/.config/flowfloat/flowfloat.conf
        if self.get("first_run"):
            wispr = keys.wispr_shortcuts().get("ptt")
            if wispr:
                self._qs.setValue("combo", wispr)
            self._qs.setValue("first_run", False)

    def get(self, key):
        default = DEFAULTS[key]
        value = self._qs.value(key, default)
        t = _TYPES[key]
        if t is list:
            if isinstance(value, str):
                value = [value] if value else []
            return list(value)
        if t is bool:
            return value in (True, "true", "1", 1)
        try:
            return t(value)
        except (TypeError, ValueError):
            return default

    def set(self, key, value):
        if self.get(key) == value:
            return
        self._qs.setValue(key, value)
        self._qs.sync()
        self.changed.emit(key)

    def reset(self, *names):
        for n in names:
            self._qs.remove(n)
            self.changed.emit(n)
