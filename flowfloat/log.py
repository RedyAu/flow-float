"""Tiny file logger: KWin owns our stdout when it launches us as the input method."""

import os
import time
from pathlib import Path

PATH = Path.home() / ".cache" / "flowfloat" / "flowfloat.log"
DEBUG = bool(os.environ.get("FLOWFLOAT_DEBUG"))


def log(*parts):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    if PATH.exists() and PATH.stat().st_size > 1_000_000:
        PATH.unlink()
    with PATH.open("a") as f:
        f.write(f"{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} {' '.join(map(str, parts))}\n")


def debug(*parts):
    if DEBUG:
        log(*parts)
