"""Minimal Wayland client for zwp_input_method_v1 (KWin's virtual keyboard slot).

When Flow Float is selected as the virtual keyboard in System Settings, KWin
starts it with a private connection in $WAYLAND_SOCKET. On that connection the
compositor offers ``zwp_input_method_v1``, which sends ``activate`` when a text
field gains focus and ``deactivate`` when it loses it. That is all we need, so
this speaks just enough of the wire protocol by hand instead of pulling in a
Wayland binding.
"""

import array
import os
import socket
import struct

from PySide6.QtCore import QObject, QSocketNotifier, Signal

from .log import log

DISPLAY_ID = 1
# Fields we stay hidden in: the password purpose, or the hidden-text / sensitive-data hints.
PURPOSE_PASSWORD = 8
HINT_HIDDEN_TEXT = 0x40
HINT_SENSITIVE_DATA = 0x80


def _string(s):
    b = s.encode() + b"\0"
    return struct.pack("<I", len(b)) + b + b"\0" * (-len(b) % 4)


def _read_string(data, off):
    (n,) = struct.unpack_from("<I", data, off)
    s = data[off + 4:off + 4 + n - 1].decode(errors="replace")
    return s, off + 4 + n + (-n % 4)


class InputMethod(QObject):
    """Emits ``focus_changed(bool)`` as text fields gain/lose focus."""

    focus_changed = Signal(bool)

    def __init__(self, fd):
        super().__init__()
        self.sock = socket.socket(fileno=fd)
        self.sock.setblocking(False)
        self._next_id = 2
        self._buf = b""
        self.registry = self._new_id()
        self.im_id = None
        self.context = None
        self.password = False
        self._send(DISPLAY_ID, 1, struct.pack("<I", self.registry))  # wl_display.get_registry
        self._notifier = QSocketNotifier(self.sock.fileno(), QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._read)

    @property
    def active(self):
        return self.context is not None and not self.password

    def _new_id(self):
        i = self._next_id
        self._next_id += 1
        return i

    def _send(self, obj, opcode, payload=b""):
        size = 8 + len(payload)
        self.sock.sendall(struct.pack("<II", obj, (size << 16) | opcode) + payload)

    def _read(self):
        try:
            data, ancdata, flags, _addr = self.sock.recvmsg(65536, socket.CMSG_SPACE(28 * 4))
        except BlockingIOError:
            return
        except OSError as exc:
            log("input method socket error:", exc)
            data = b""
        for level, typ, cdata in ancdata:  # we never need passed fds; don't leak them
            if level == socket.SOL_SOCKET and typ == socket.SCM_RIGHTS:
                fds = array.array("i")
                fds.frombytes(cdata[:len(cdata) - len(cdata) % fds.itemsize])
                for f in fds:
                    os.close(f)
        if flags & socket.MSG_CTRUNC:
            log("input method: passed file descriptors were truncated")
        if not data:
            self._close("input method connection closed by compositor")
            return
        self.feed(data)

    def feed(self, data):
        """Split buffered wire data into messages and dispatch them."""
        self._buf += data
        while len(self._buf) >= 8:
            obj, word = struct.unpack_from("<II", self._buf)
            size, opcode = word >> 16, word & 0xFFFF
            if size < 8 or size % 4:
                self._close(f"input method: malformed message (object {obj}, size {size})")
                return
            if len(self._buf) < size:
                break
            body, self._buf = self._buf[8:size], self._buf[size:]
            self._dispatch(obj, opcode, body)

    def _close(self, reason):
        log(reason)
        self._notifier.setEnabled(False)
        self._buf = b""
        try:
            self.sock.close()
        except OSError:
            pass
        if self.context is not None:
            self.context = None
            self.focus_changed.emit(False)

    def _dispatch(self, obj, opcode, body):
        if obj == DISPLAY_ID:
            if opcode == 0:  # error
                oid, code = struct.unpack_from("<II", body)
                msg, _ = _read_string(body, 8)
                log(f"wayland error on object {oid} code {code}: {msg}")
        elif obj == self.registry and opcode == 0:  # global
            (name,) = struct.unpack_from("<I", body)
            iface, off = _read_string(body, 4)
            if iface == "zwp_input_method_v1" and self.im_id is None:
                self.im_id = self._new_id()
                self._send(self.registry, 0, struct.pack("<I", name) + _string(iface)
                           + struct.pack("<II", 1, self.im_id))  # wl_registry.bind
                log("bound zwp_input_method_v1")
        elif obj == self.im_id:
            if opcode == 0:  # activate(new_id context)
                (ctx,) = struct.unpack_from("<I", body)
                self.context = ctx
                self.password = False
                log("text field focused, context", hex(ctx))
                self.focus_changed.emit(True)
            elif opcode == 1:  # deactivate(object context)
                (ctx,) = struct.unpack_from("<I", body)
                self._send(ctx, 0)  # zwp_input_method_context_v1.destroy
                if ctx == self.context:
                    self.context = None
                    log("text field unfocused")
                    self.focus_changed.emit(False)
        elif obj == self.context and opcode == 2:  # content_type(hint, purpose)
            hint, purpose = struct.unpack_from("<II", body)
            was = self.active
            self.password = purpose == PURPOSE_PASSWORD or bool(hint & (HINT_HIDDEN_TEXT | HINT_SENSITIVE_DATA))
            if was != self.active:
                self.focus_changed.emit(self.active)
