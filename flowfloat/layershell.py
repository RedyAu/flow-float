"""Minimal ctypes binding to KDE's LayerShellQt.

Turning the floating button into a wlr-layer-shell surface is what lets it sit
above every window *without ever taking keyboard focus* on KWin/Wayland - the
text field you are dictating into keeps focus while you hold the button.
"""

import ctypes
import ctypes.util

import shiboken6

# Enum values from LayerShellQt/window.h (they mirror the protocol).
ANCHOR_TOP, ANCHOR_BOTTOM, ANCHOR_LEFT, ANCHOR_RIGHT = 1, 2, 4, 8
LAYER_TOP, LAYER_OVERLAY = 2, 3
KEYBOARD_NONE = 0

_lib = None


def _load():
    global _lib
    if _lib is not None:
        return _lib or None
    try:
        lib = ctypes.CDLL(ctypes.util.find_library("LayerShellQtInterface") or "libLayerShellQtInterface.so.6")
    except OSError:
        _lib = False
        return None

    # Mangled names, enum values and argument layouts below were checked against
    # LayerShellQt 6.7 (Plasma 6.7). If the ABI drifts, we fall back to a normal window.
    def fn(name, *args):
        f = getattr(lib, name)
        f.argtypes = [ctypes.c_void_p, *args]
        f.restype = None
        return f

    try:
        lib.get = lib._ZN12LayerShellQt6Window3getEP7QWindow
        lib.get.argtypes = [ctypes.c_void_p]
        lib.get.restype = ctypes.c_void_p
        lib.setLayer = fn("_ZN12LayerShellQt6Window8setLayerENS0_5LayerE", ctypes.c_int)
        # QFlags<Anchor> is a trivially copyable int wrapper -> passed as int.
        lib.setAnchors = fn("_ZN12LayerShellQt6Window10setAnchorsE6QFlagsINS0_6AnchorEE", ctypes.c_int)
        lib.setMargins = fn("_ZN12LayerShellQt6Window10setMarginsERK8QMargins", ctypes.POINTER(ctypes.c_int * 4))
        lib.setDesiredSize = fn("_ZN12LayerShellQt6Window14setDesiredSizeERK5QSize",
                                ctypes.POINTER(ctypes.c_int * 2))
        lib.setExclusiveZone = fn("_ZN12LayerShellQt6Window16setExclusiveZoneEi", ctypes.c_int)
        lib.setKeyboardInteractivity = fn(
            "_ZN12LayerShellQt6Window24setKeyboardInteractivityENS0_21KeyboardInteractivityE", ctypes.c_int)
        lib.setActivateOnShow = fn("_ZN12LayerShellQt6Window17setActivateOnShowEb", ctypes.c_bool)
    except AttributeError as exc:  # a symbol is missing: different LayerShellQt ABI
        from .log import log
        log("LayerShellQt symbols not found, using a normal window:", exc)
        _lib = False
        return None
    _lib = lib
    return lib


class LayerWindow:
    """Wraps LayerShellQt::Window for a QWidget. Must be created before the first show()."""

    def __init__(self, widget, *, anchors=0, layer=LAYER_OVERLAY, desired_size=None):
        lib = _load()
        if lib is None:
            raise RuntimeError("LayerShellQt library not found")
        widget.winId()  # make sure the QWindow exists
        self._lib = lib
        self._ptr = lib.get(shiboken6.getCppPointer(widget.windowHandle())[0])
        if not self._ptr:
            raise RuntimeError("LayerShellQt::Window::get returned null")
        lib.setLayer(self._ptr, layer)
        lib.setKeyboardInteractivity(self._ptr, KEYBOARD_NONE)
        lib.setActivateOnShow(self._ptr, False)
        lib.setExclusiveZone(self._ptr, 0)
        lib.setAnchors(self._ptr, anchors)
        if desired_size is not None:
            self.set_desired_size(*desired_size)

    def set_anchors(self, anchors):
        self._lib.setAnchors(self._ptr, anchors)

    def set_margins(self, left, top, right, bottom):
        m = (ctypes.c_int * 4)(left, top, right, bottom)
        self._lib.setMargins(self._ptr, ctypes.byref(m))

    def set_desired_size(self, w, h):
        s = (ctypes.c_int * 2)(w, h)
        self._lib.setDesiredSize(self._ptr, ctypes.byref(s))


def available():
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.platformName().startswith("wayland") and _load() is not None
