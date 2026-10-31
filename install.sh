#!/usr/bin/env bash
# Install Flow Float for the current user and make it KDE's virtual keyboard.
#   ./install.sh              install
#   ./install.sh --uninstall  restore the previous virtual keyboard and remove the desktop files
# The desktop files point at this checkout: run this again if you move the folder.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [ "${1:-}" = "--uninstall" ]; then
  exec ./flow-float --uninstall
fi

missing=()
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { echo "Python 3.9 or newer is required." >&2; exit 1; }
python3 -c 'import PySide6.QtWidgets, PySide6.QtDBus, PySide6.QtNetwork, shiboken6' 2>/dev/null || missing+=(python3-pyside6)
python3 -c 'import evdev' 2>/dev/null || missing+=(python3-evdev)
ldconfig -p | grep -q LayerShellQtInterface || missing+=(layer-shell-qt)
command -v kwriteconfig6 >/dev/null || missing+=(kf6-kconfig)
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing packages: ${missing[*]}  (e.g. sudo dnf install ${missing[*]})" >&2
  exit 1
fi
if [ ! -w /dev/uinput ]; then
  echo "Note: /dev/uinput is not writable, so Backspace/Return and the virtual-keyboard method won't work." >&2
  echo "      Add yourself to the group that owns it (ls -l /dev/uinput) and log in again." >&2
fi
if ! ls /dev/input/event* >/dev/null 2>&1 || [ -z "$(find /dev/input -maxdepth 1 -name 'event*' -writable 2>/dev/null | head -1)" ]; then
  echo "Note: no /dev/input/event* device is writable, so sending the shortcut into Wispr Flow's keyboard won't work." >&2
  echo "      Wispr Flow's 'wispr-flow --install-udev-rules' grants this access." >&2
fi

chmod +x flow-float
PYTHONPATH="$PWD" python3 - <<'PY'
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from flowfloat import kde
from flowfloat.settings import Settings
s = Settings()
kde.use_flow_float(s)
print("Flow Float is now KDE's virtual keyboard (", kde.DESKTOP_FILE, ")")
PY
