#!/usr/bin/env bash
# Install Flow Float for the current user and make it KDE's virtual keyboard.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

missing=()
python3 -c 'import PySide6' 2>/dev/null || missing+=(python3-pyside6)
python3 -c 'import evdev' 2>/dev/null || missing+=(python3-evdev)
[ -e /usr/lib64/libLayerShellQtInterface.so.6 ] || [ -e /usr/lib/libLayerShellQtInterface.so.6 ] \
  || ldconfig -p | grep -q LayerShellQtInterface || missing+=(layer-shell-qt)
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing packages: ${missing[*]}  (e.g. sudo dnf install ${missing[*]})" >&2
  exit 1
fi
if [ ! -w /dev/uinput ]; then
  echo "Note: /dev/uinput is not writable, so Backspace/Enter keys won't work." >&2
  echo "      Add yourself to the group that owns it (ls -l /dev/uinput) and log in again." >&2
fi

chmod +x flow-float
PYTHONPATH="$PWD" python3 - <<'PY'
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from flowfloat import kde
from flowfloat.settings import Settings
kde.install_desktop_file()
kde.use_flow_float(Settings())
print("Flow Float is now KDE's virtual keyboard (", kde.DESKTOP_FILE, ")")
PY
