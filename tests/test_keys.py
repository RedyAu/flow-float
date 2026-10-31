import json

from flowfloat import keys


def test_wispr_shortcuts_maps_vk_codes(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"prefs": {"cache": {"splitKeybinds": [
        {"value": "ptt", "shortcut": [164, 91]},
        {"value": "popo", "shortcut": [164, 32, 91]},
        {"value": "weird", "shortcut": [9999]},
    ]}}}))
    monkeypatch.setattr(keys, "WISPR_CONFIG", config)
    shortcuts = keys.wispr_shortcuts()
    assert shortcuts["ptt"] == ["KEY_LEFTALT", "KEY_LEFTMETA"]
    assert shortcuts["popo"] == ["KEY_LEFTALT", "KEY_LEFTMETA", "KEY_SPACE"]  # modifiers first
    assert "weird" not in shortcuts


def test_wispr_shortcuts_missing_config(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "WISPR_CONFIG", tmp_path / "nope.json")
    assert keys.wispr_shortcuts() == {}


def test_labels_and_key_list():
    assert keys.combo_label(["KEY_LEFTALT", "KEY_LEFTMETA"]) == "Alt + Meta"
    names = keys.selectable_keys()
    assert all(isinstance(n, str) for n in names)
    assert "KEY_MUTE" in names and "KEY_BACKSPACE" in names
