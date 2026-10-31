from flowfloat import kde


def test_reads_input_method_from_kwinrc(tmp_path, monkeypatch):
    rc = tmp_path / "kwinrc"
    rc.write_text("[Other]\nInputMethod=nope\n\n[Wayland]\nInputMethod[$e]=$HOME/kb.desktop\n"
                  "VirtualKeyboardEnabled=true\n")
    monkeypatch.setenv("HOME", "/home/test")
    assert kde.current_input_method(rc) == "/home/test/kb.desktop"
    assert kde.current_input_method(tmp_path / "missing") == ""


def test_exec_quoting():
    assert kde._exec_arg("/plain/path") == "/plain/path"
    assert kde._exec_arg("/My Apps/flow-float") == '"/My Apps/flow-float"'
    assert kde._exec_arg("/a$b c") == '"/a\\\\$b c"'
