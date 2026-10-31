import pytest

from flowfloat import layershell


@pytest.fixture
def dock(settings, monkeypatch):
    monkeypatch.setattr(layershell, "available", lambda: False)
    from flowfloat.button import FloatingButton
    return FloatingButton(settings)


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_items_fit_in_panel_without_overlap(dock, settings, orientation):
    settings.set("orientation", orientation)
    rects, size = dock._layout()
    panel = rects["panel"]
    assert (panel.width(), panel.height()) == (size.width(), size.height())
    items = [rects[n] for n in ("mic", "backspace", "enter", "grip")]
    for r in items:
        assert panel.contains(r)
    for a, b in zip(items, items[1:]):
        assert not a.intersects(b)
    long_side = max(size.width(), size.height())
    assert (size.width() == long_side) == (orientation == "horizontal")


def test_keys_stay_finger_sized(dock, settings):
    settings.set("size", 44)
    rects, _ = dock._layout()
    assert rects["backspace"].width() >= 42


def test_hit_testing(dock):
    rects, _ = dock._layout()
    for name in ("mic", "backspace", "enter", "grip"):
        assert dock._item_at(rects[name].center()) == name
