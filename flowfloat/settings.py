"""Settings, stored in ~/.config/flowfloat/flowfloat.conf."""

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

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
    "reopen_settings": False,     # show settings once KWin has started the new copy
    "first_run": True,
}

_TYPES = {k: type(v) for k, v in DEFAULTS.items()}


class Settings(QObject):
    changed = Signal(str)  # key name

    def __init__(self, path=None):
        super().__init__()
        # Default: ~/.config/flowfloat/flowfloat.conf. ``path`` is for tests.
        self._qs = (QSettings(str(path), QSettings.Format.IniFormat) if path
                    else QSettings("flowfloat", "flowfloat"))
        # Writes reach the disk shortly after the last change (sliders send many).
        self._sync = QTimer(self, singleShot=True, interval=500, timeout=self._qs.sync)
        for stale in set(self._qs.allKeys()) - set(DEFAULTS):  # e.g. "mode" from older versions
            self._qs.remove(stale)
        if self.get("first_run"):
            wispr = keys.wispr_shortcuts().get("ptt")
            if wispr:
                self._qs.setValue("combo", wispr)
            self._qs.setValue("first_run", False)
        self._qs.sync()

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
        self._sync.start()
        self.changed.emit(key)

    def reset(self, *names):
        for n in names:
            if self._qs.contains(n):
                self._qs.remove(n)
                self._sync.start()
                self.changed.emit(n)

    def flush(self):
        self._sync.stop()
        self._qs.sync()
