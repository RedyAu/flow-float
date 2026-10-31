import os
import sys
from pathlib import Path

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # no display needed
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(qapp, tmp_path, monkeypatch):
    from flowfloat import keys
    from flowfloat.settings import Settings
    monkeypatch.setattr(keys, "wispr_shortcuts", lambda: {})
    return Settings(tmp_path / "flowfloat.conf")


@pytest.fixture(autouse=True)
def _private_log(tmp_path, monkeypatch):
    from flowfloat import log
    monkeypatch.setattr(log, "PATH", tmp_path / "flowfloat.log")  # keep tests out of the real log
