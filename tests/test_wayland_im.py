import socket
import struct

import pytest

from flowfloat import wayland_im
from flowfloat.wayland_im import InputMethod, _string


def msg(obj, opcode, payload=b""):
    return struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode) + payload


@pytest.fixture
def im(qapp):
    ours, compositor = socket.socketpair()
    method = InputMethod(ours.detach())
    compositor.setblocking(False)
    events = []
    method.focus_changed.connect(events.append)
    yield method, compositor, events
    compositor.close()


def sent(compositor):
    try:
        return compositor.recv(4096)
    except BlockingIOError:
        return b""


def bind(method, compositor):
    sent(compositor)  # get_registry
    method.feed(msg(method.registry, 0, struct.pack("<I", 7) + _string("zwp_input_method_v1") + struct.pack("<I", 1)))
    return sent(compositor)


def test_binds_input_method_global(im):
    method, compositor, _ = im
    request = bind(method, compositor)
    obj, word = struct.unpack_from("<II", request)
    assert obj == method.registry and word & 0xFFFF == 0  # wl_registry.bind
    assert b"zwp_input_method_v1" in request
    assert method.im_id is not None


def test_activate_deactivate_and_password(im):
    method, compositor, events = im
    bind(method, compositor)
    ctx = 0xFF000000
    method.feed(msg(method.im_id, 0, struct.pack("<I", ctx)))
    assert events == [True] and method.active
    method.feed(msg(ctx, 2, struct.pack("<II", 0, wayland_im.PURPOSE_PASSWORD)))
    assert events == [True, False] and not method.active
    method.feed(msg(ctx, 2, struct.pack("<II", 0, 0)))
    method.feed(msg(ctx, 2, struct.pack("<II", wayland_im.HINT_SENSITIVE_DATA, 0)))
    assert events == [True, False, True, False]
    method.feed(msg(method.im_id, 1, struct.pack("<I", ctx)))
    destroy = sent(compositor)
    assert struct.unpack_from("<II", destroy) == (ctx, (8 << 16) | 0)
    assert method.context is None


def test_split_messages_are_reassembled(im):
    method, compositor, events = im
    bind(method, compositor)
    data = msg(method.im_id, 0, struct.pack("<I", 0xFF000001))
    method.feed(data[:5])
    assert events == []
    method.feed(data[5:])
    assert events == [True]


def test_malformed_size_does_not_hang(im):
    method, compositor, _ = im
    method.feed(struct.pack("<II", 1, (4 << 16) | 0) + b"rest")  # size 4 < header size
    assert method._buf == b""
    assert not method._notifier.isEnabled()
