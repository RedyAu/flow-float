"""The floating dock: push-to-talk mic, Backspace/Enter keys and a drag grip in one panel."""

import time

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (QColor, QEventPoint, QGuiApplication, QPainter, QPainterPath, QPen, QRegion)
from PySide6.QtWidgets import QWidget

from . import layershell
from .layershell import ANCHOR_BOTTOM, ANCHOR_LEFT, ANCHOR_RIGHT, ANCHOR_TOP
from .log import debug

PAD, GAP = 6, 6          # panel padding and spacing between items (logical px)
GRIP_LEN, GRIP_W = 20, 44  # the grip has extra touch slop around it (see _item_at)
KEY_MIN = 42              # smallest Backspace/Return diameter (logical px)
ACTIVE = QColor("#da4453")  # Breeze "negative" red
ALL_EDGES = ANCHOR_TOP | ANCHOR_BOTTOM | ANCHOR_LEFT | ANCHOR_RIGHT
QWIDGETSIZE_MAX = (1 << 24) - 1
TAP_SLOP = 8  # px a grip press may move and still count as a tap (opens settings)
KEY_NAMES = {"backspace": "KEY_BACKSPACE", "enter": "KEY_ENTER"}  # "enter" is shown as Return


def _mic_glyph(p, c, r, color):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    cw, ch = r * 0.36, r * 0.62
    cap = QRectF(c.x() - cw / 2, c.y() - r * 0.52, cw, ch)
    p.drawRoundedRect(cap, cw / 2, cw / 2)
    pen = QPen(color, max(1.5, r * 0.085))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    arc_w = r * 0.62
    arc = QRectF(c.x() - arc_w / 2, cap.top() + ch * 0.25, arc_w, ch * 0.95)
    path = QPainterPath()
    path.arcMoveTo(arc, 180)
    path.arcTo(arc, 180, 180)
    p.drawPath(path)
    stem = arc.bottom()
    p.drawLine(QPointF(c.x(), stem), QPointF(c.x(), stem + r * 0.17))
    p.drawLine(QPointF(c.x() - r * 0.2, stem + r * 0.17), QPointF(c.x() + r * 0.2, stem + r * 0.17))


def _backspace_glyph(p, c, r, color):
    w, h = r * 0.95, r * 0.62
    left, top = c.x() - w / 2, c.y() - h / 2
    path = QPainterPath()
    path.moveTo(left, c.y())
    path.lineTo(left + h / 2, top)
    path.lineTo(left + w, top)
    path.lineTo(left + w, top + h)
    path.lineTo(left + h / 2, top + h)
    path.closeSubpath()
    pen = QPen(color, max(1.5, r * 0.09))
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    x0, s = left + h / 2 + (w - h / 2) / 2, h * 0.22
    p.drawLine(QPointF(x0 - s, c.y() - s), QPointF(x0 + s, c.y() + s))
    p.drawLine(QPointF(x0 - s, c.y() + s), QPointF(x0 + s, c.y() - s))


def _enter_glyph(p, c, r, color):
    pen = QPen(color, max(1.5, r * 0.09))
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    right, top, bottom, left = c.x() + r * 0.42, c.y() - r * 0.4, c.y() + r * 0.2, c.x() - r * 0.42
    path = QPainterPath()
    path.moveTo(right, top)
    path.lineTo(right, bottom)
    path.lineTo(left, bottom)
    p.drawPath(path)
    a = r * 0.22
    p.drawLine(QPointF(left, bottom), QPointF(left + a, bottom - a))
    p.drawLine(QPointF(left, bottom), QPointF(left + a, bottom + a))


GLYPHS = {"mic": _mic_glyph, "backspace": _backspace_glyph, "enter": _enter_glyph}


class FloatingButton(QWidget):
    """A small dock that floats over everything without ever taking keyboard focus.

    * mic: hold to hold the configured key combination; a quick tap latches it until the next tap
    * backspace: auto-repeats while held
    * enter (Return): tap for Return, long-press for Shift+Return
    * grip: drag to move the dock, tap to open the settings

    On Wayland it is a layer-shell surface. While dragging, the surface grows to
    cover the screen so touch coordinates are screen-absolute (moving a small
    layer surface under the finger makes it jitter).
    """

    pressed = Signal()
    released = Signal()
    key_event = Signal(str, bool)  # evdev key name, down
    drag_finished = Signal()
    settings_requested = Signal()

    def __init__(self, settings):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.Tool)
        self.settings = settings
        self.setWindowTitle("Flow Float")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.active = False         # mic keys are held
        self.latched = False        # a short tap left the keys held until the next tap
        self._unlatching = False    # the current press is the tap that ends a latch
        self._down_at = 0.0
        self.keys_down = set()      # "backspace"/"enter" currently pressed
        self._targets = {}          # touch point id (or "mouse") -> item name
        self.dragging = False
        self._drag_ready = False    # surface has grown to full screen
        self._drag_grab = None      # finger offset inside the compact dock
        self._drag_pos = None       # top-left of the compact dock, in screen coords
        self._drag_start = None
        self._drag_moved = False
        self._phase = 0.0
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=1200, loopCount=-1)
        self._anim.valueChanged.connect(self._tick)
        self._drag_timeout = QTimer(self, singleShot=True, interval=60_000, timeout=lambda: self.end_drag(False))
        self._return_timer = QTimer(self, singleShot=True, timeout=self._return_long_press)
        self._resize()
        self.layer = None
        if layershell.available():
            try:
                self.layer = layershell.LayerWindow(self)
            except RuntimeError as exc:
                print("flow-float: layer-shell unavailable, using a normal window:", exc)
        self.apply_position()
        settings.changed.connect(self._on_setting)

    # -- geometry -----------------------------------------------------------
    def _layout(self, origin=QPointF(0, 0)):
        """Rects of the panel and each item, plus the dock's compact size."""
        s = self.settings
        d = s.get("size")
        k = max(KEY_MIN, round(d * 0.72))  # Backspace/Return: smaller than the mic, still finger-sized
        vertical = s.get("orientation") == "vertical"
        # Everything in one line: mic, Backspace, Return, grip.
        items = [("mic", d)] + [(n, k) for n in ("backspace", "enter") if s.get(f"show_{n}")]
        items.append(("grip", GRIP_LEN))
        thick = d + 2 * PAD
        length = 2 * PAD + sum(size for _, size in items) + GAP * (len(items) - 1)
        rects, pos = {}, PAD
        for name, size in items:
            cross = min(d, GRIP_W) if name == "grip" else size
            r = (QRectF((thick - cross) / 2, pos, cross, size) if vertical
                 else QRectF(pos, (thick - cross) / 2, size, cross))
            rects[name] = r.translated(origin)
            pos += size + GAP
        compact = QSize(thick, length) if vertical else QSize(length, thick)
        rects["panel"] = QRectF(origin, compact.toSizeF())
        return rects, compact

    def _compact_size(self):
        return self._layout()[1]

    def _resize(self):
        self.setFixedSize(self._compact_size())

    def _item_at(self, pos):
        rects = self._layout()[0]
        for name in ("mic", "backspace", "enter"):
            if name in rects and rects[name].adjusted(-4, -4, 4, 4).contains(pos):
                return name
        if rects["grip"].adjusted(-8, -8, 8, 8).contains(pos):
            return "grip"
        return None

    def _apply_mask(self):
        if self.windowHandle() is None:
            return
        if self.dragging:
            self.windowHandle().setMask(QRegion())
        else:
            self.windowHandle().setMask(QRegion(self.rect()))

    def apply_position(self):
        s = self.settings
        mh, mv = max(0, s.get("margin_h")), max(0, s.get("margin_v"))
        left, top = s.get("corner_h") == "left", s.get("corner_v") == "top"
        if self.layer:
            self.layer.set_desired_size(self.width(), self.height())
            self.layer.set_anchors((ANCHOR_LEFT if left else ANCHOR_RIGHT) | (ANCHOR_TOP if top else ANCHOR_BOTTOM))
            self.layer.set_margins(mh if left else 0, mv if top else 0, 0 if left else mh, 0 if top else mv)
        else:
            area = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            x = area.left() + mh if left else area.right() - mh - self.width()
            y = area.top() + mv if top else area.bottom() - mv - self.height()
            self.move(x, y)
        self._apply_mask()

    def _on_setting(self, key):
        if self.dragging:
            return
        if key in ("size", "orientation", "show_backspace", "show_enter"):
            self._resize()
            self.apply_position()
        elif key in ("corner_h", "corner_v", "margin_h", "margin_v"):
            self.apply_position()
        self.update()

    # -- dragging -----------------------------------------------------------
    def _begin_drag(self, pos):
        self._drag_start = pos
        self._drag_moved = False
        if not self.layer:  # X11 fallback: let the window manager move us
            self.windowHandle().startSystemMove()
            return
        self.dragging = True
        self._drag_ready = False
        self._drag_grab = pos
        self._drag_pos = None
        self._drag_timeout.start()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
        self.layer.set_anchors(ALL_EDGES)
        self.layer.set_margins(0, 0, 0, 0)
        self.layer.set_desired_size(0, 0)
        self._apply_mask()
        self.update()
        debug("drag start")

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.dragging and not self._drag_ready and ev.size().width() > self._compact_size().width() * 2:
            # Coordinates are screen-absolute from now on. Start from where the dock was.
            s = self.settings
            cw = self._compact_size()
            x = s.get("margin_h") if s.get("corner_h") == "left" else self.width() - s.get("margin_h") - cw.width()
            y = s.get("margin_v") if s.get("corner_v") == "top" else self.height() - s.get("margin_v") - cw.height()
            self._drag_pos = self._clamp(QPointF(x, y))
            self._drag_start = self._drag_pos + self._drag_grab
            self._drag_ready = True
            self.update()

    def _clamp(self, pt):
        cw = self._compact_size()
        return QPointF(min(max(0, pt.x()), max(0, self.width() - cw.width())),
                       min(max(0, pt.y()), max(0, self.height() - cw.height())))

    def _drag_move(self, pos):
        if not (self.dragging and self._drag_ready):
            return
        if (pos - self._drag_start).manhattanLength() > TAP_SLOP:
            self._drag_moved = True
        if self._drag_moved:
            self._drag_pos = self._clamp(pos - self._drag_grab)
            self.update()

    def end_drag(self, save=True):
        if not self.dragging:
            return
        tapped = save and not self._drag_moved
        if save and self._drag_moved and self._drag_pos is not None:
            r = QRectF(self._drag_pos, self._compact_size().toSizeF())
            left = r.center().x() < self.width() / 2
            top = r.center().y() < self.height() / 2
            s = self.settings
            s.set("corner_h", "left" if left else "right")
            s.set("corner_v", "top" if top else "bottom")
            s.set("margin_h", int(round(r.left() if left else self.width() - r.right())))
            s.set("margin_v", int(round(r.top() if top else self.height() - r.bottom())))
        self.dragging = False
        self._drag_ready = False
        self._drag_timeout.stop()
        self._resize()
        self.apply_position()
        self.update()
        debug("drag end", "tap" if tapped else "saved" if save else "cancelled")
        self.drag_finished.emit()
        if tapped:
            self.settings_requested.emit()

    # -- input --------------------------------------------------------------
    def _press(self, tid, pos):
        if self.dragging:
            return
        item = self._item_at(pos)
        if item is None:
            return
        self._targets[tid] = item
        if item == "grip":
            if any(t != "grip" for t in self._targets.values()):
                self._targets.pop(tid)  # don't move the dock out from under another finger
                return
            self._begin_drag(pos)
        elif item == "mic":
            self._mic_down()
        elif item == "enter":
            # Return fires on release: a tap is Return, a long press is Shift+Return.
            self.keys_down.add(item)
            self._return_timer.start(self.settings.get("return_long_ms"))
            self.update()
        else:
            self.keys_down.add(item)
            self.key_event.emit(KEY_NAMES[item], True)
            self.update()

    def _tap_keys(self, names):
        for n in names:
            self.key_event.emit(n, True)
        for n in reversed(names):
            self.key_event.emit(n, False)

    def _return_long_press(self):
        if "enter" in self.keys_down:
            self._tap_keys(["KEY_LEFTSHIFT", "KEY_ENTER"])

    def _release(self, tid):
        item = self._targets.pop(tid, None)
        if item == "grip":
            self.end_drag(True)
        elif item == "mic":
            self._mic_up()
        elif item == "enter" and item in self.keys_down:
            self.keys_down.discard(item)
            if self._return_timer.isActive():  # released before the long press kicked in
                self._return_timer.stop()
                self._tap_keys(["KEY_ENTER"])
            self.update()
        elif item in self.keys_down:
            self.keys_down.discard(item)
            self.key_event.emit(KEY_NAMES[item], False)
            self.update()

    def release_all(self):
        """Let go of everything (touch cancelled, hidden, quitting)."""
        self._targets.clear()
        self.end_drag(False)
        self._return_timer.stop()
        for item in list(self.keys_down):
            self.keys_down.discard(item)
            if item != "enter":  # Return is only ever tapped, never left down
                self.key_event.emit(KEY_NAMES[item], False)
        self.force_release()

    def event(self, ev):
        t = ev.type()
        if t in (QEvent.Type.TouchBegin, QEvent.Type.TouchEnd, QEvent.Type.TouchCancel,
                 QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            debug("dock event", t.name)
        if t == QEvent.Type.TouchCancel:
            self.release_all()
            ev.accept()
            return True
        if t in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd):
            for pt in ev.points():  # several fingers: e.g. hold the mic and tap Backspace
                state = pt.state()
                if state == QEventPoint.State.Pressed:
                    self._press(pt.id(), pt.position())
                elif state == QEventPoint.State.Released:
                    if self._targets.get(pt.id()) == "grip":
                        self._drag_move(pt.position())
                    self._release(pt.id())
                elif self._targets.get(pt.id()) == "grip":
                    self._drag_move(pt.position())
            ev.accept()
            return True
        return super().event(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press("mouse", ev.position())
        ev.accept()

    def mouseMoveEvent(self, ev):
        if self._targets.get("mouse") == "grip":
            self._drag_move(ev.position())

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._release("mouse")
        ev.accept()

    def _mic_down(self):
        if self.latched:  # this tap ends a latched press
            self._unlatching = True
            self.force_release()
            return
        self._unlatching = False
        self._down_at = time.monotonic()
        if not self.active:
            self._set_active(True)
            self.pressed.emit()

    def _mic_up(self):
        if self._unlatching:
            self._unlatching = False
            return
        # A short press latches the keys down until the next tap; a long one is plain push-to-talk.
        latch_ms = self.settings.get("latch_ms")
        if self.active and latch_ms and (time.monotonic() - self._down_at) * 1000 < latch_ms:
            self.latched = True
            self.update()
            return
        self.force_release()

    def force_release(self):
        self.latched = False
        if self.active:
            self._set_active(False)
            self.released.emit()

    @property
    def busy(self):
        """True while a finger is on the dock or the mic is latched: don't hide now."""
        return self.active or self.dragging or bool(self._targets) or bool(self.keys_down)

    def showEvent(self, ev):
        super().showEvent(ev)
        self._apply_mask()  # the input region belongs to the surface, which is new after every show

    def hideEvent(self, ev):
        self.release_all()
        super().hideEvent(ev)

    # -- drawing ------------------------------------------------------------
    def _set_active(self, on):
        self.active = on
        if on:
            self._anim.start()
        else:
            self._anim.stop()
            self._phase = 0.0
        self.update()

    def _tick(self, v):
        self._phase = v
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.dragging:
            if not self._drag_ready or self._drag_pos is None:
                return
            p.fillRect(self.rect(), QColor(0, 0, 0, 60))
            origin = self._drag_pos
        else:
            origin = QPointF(0, 0)
        rects, _ = self._layout(origin)
        pal = self.palette()
        accent = pal.color(pal.ColorRole.Highlight)
        idle = not (self.active or self.keys_down or self.dragging)
        p.setOpacity(self.settings.get("idle_opacity") if idle else 1.0)

        # Panel in the Plasma colour scheme
        panel = rects["panel"]
        bg = QColor(pal.color(pal.ColorRole.Window))
        bg.setAlphaF(0.92)
        border = QColor(pal.color(pal.ColorRole.WindowText))
        border.setAlphaF(0.15)
        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        radius = min(panel.width(), panel.height()) / 2
        p.drawRoundedRect(panel.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        # Mic
        mic = rects["mic"]
        c, r = mic.center(), mic.width() / 2
        if self.active:
            for k in (0.0, 0.5):  # staggered pulse rings inside the panel padding
                t = (self._phase + k) % 1.0
                ring = QColor(ACTIVE)
                ring.setAlphaF(0.6 * (1 - t))
                p.setPen(QPen(ring, 2.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c, r + PAD * 0.9 * t, r + PAD * 0.9 * t)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ACTIVE if self.active else accent)
        p.drawEllipse(c, r, r)
        _mic_glyph(p, c, r, QColor("white"))

        # Keys
        text = pal.color(pal.ColorRole.ButtonText)
        for name in ("backspace", "enter"):
            if name not in rects:
                continue
            kr = rects[name]
            down = name in self.keys_down
            fill = QColor(accent if down else pal.color(pal.ColorRole.Button))
            p.setPen(QPen(border, 1))
            p.setBrush(fill)
            p.drawEllipse(kr.center(), kr.width() / 2, kr.width() / 2)
            GLYPHS[name](p, kr.center(), kr.width() / 2, QColor("white") if down else text)

        # Grip
        g = rects["grip"]
        dot = QColor(accent if self.dragging else text)
        dot.setAlphaF(1.0 if self.dragging else 0.55)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot)
        horizontal_dots = g.width() >= g.height()
        for i in (-1, 0, 1):
            for j in (-1, 1):
                dx, dy = (i * 6, j * 3.5) if horizontal_dots else (j * 3.5, i * 6)
                p.drawEllipse(g.center() + QPointF(dx, dy), 1.9, 1.9)
