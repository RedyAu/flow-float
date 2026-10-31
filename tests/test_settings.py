from flowfloat.settings import Settings


def test_type_coercion_from_ini(tmp_path, qapp, monkeypatch):
    from flowfloat import keys
    monkeypatch.setattr(keys, "wispr_shortcuts", lambda: {})
    conf = tmp_path / "flowfloat.conf"
    conf.write_text("[General]\nsize=64\nidle_opacity=0.5\ntablet_only=false\ncombo=KEY_LEFTCTRL\n"
                    "latch_ms=oops\nmode=hold\n")
    s = Settings(conf)
    assert s.get("size") == 64
    assert s.get("idle_opacity") == 0.5
    assert s.get("tablet_only") is False
    assert s.get("combo") == ["KEY_LEFTCTRL"]
    assert s.get("latch_ms") == 300  # unparsable -> default
    s.flush()
    assert "mode=" not in conf.read_text()  # stale keys are dropped


def test_set_and_reset_signal_only_on_change(settings):
    seen = []
    settings.changed.connect(seen.append)
    settings.set("size", 80)
    settings.set("size", 80)
    settings.reset("size", "margin_h")  # margin_h was never set
    assert seen == ["size", "size"]
    assert settings.get("size") == 60
