"""Application shell: tray icon, single instance, KWin input-method and tablet-mode integration."""

import argparse
import atexit
import os
import signal
import sys
import time
from pathlib import Path

from PySide6.QtCore import SLOT, QObject, QTimer, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface
from PySide6.QtGui import QAction, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import kde, keys
from .button import FloatingButton
from .config_window import ConfigWindow
from .log import log
from .settings import Settings

SOCKET = "flow-float"
KWIN = ("org.kde.KWin", "/org/kde/KWin", "org.kde.KWin.TabletModeManager")
HIDE_DELAY_MS = 300  # focus often bounces between widgets; don't flicker


class TabletMode(QObject):
    """Follows KWin's tablet mode over D-Bus."""

    def __init__(self, on_change):
        super().__init__()
        self.on_change = on_change
        self.active = False
        self.available = False  # devices without a tablet-mode switch never enter tablet mode
        bus = QDBusConnection.sessionBus()
        iface = QDBusInterface(*KWIN, bus)
        if iface.isValid():
            self.active = bool(iface.property("tabletMode"))
            self.available = bool(iface.property("tabletModeAvailable"))
        bus.connect(*KWIN, "tabletModeChanged", self, SLOT("changed(bool)"))
        bus.connect(*KWIN, "tabletModeAvailableChanged", self, SLOT("available_changed(bool)"))

    @Slot(bool)
    def changed(self, on):
        self.active = on
        self.on_change()

    @Slot(bool)
    def available_changed(self, on):
        self.available = on
        self.on_change()


class FlowFloat(QObject):
    def __init__(self, app, im_fd=None):
        super().__init__()
        self.app = app
        self.settings = Settings()
        self.holder = keys.KeyHolder()
        self.user_hidden = False
        self.config = None
        self.text_focus = False
        self._shut_down = False
        self._key_error_shown = False  # notify about failing keys once, until a press works again

        self.im = None
        if im_fd is not None:
            from .wayland_im import InputMethod
            self.im = InputMethod(im_fd)
            self.im.focus_changed.connect(self._on_text_focus)
            log("running as KWin input method")
            if kde.autostart_enabled():  # KWin starts us now; a login copy would only compete
                kde.set_autostart(False)
                log("removed the login autostart entry")

        self.button = FloatingButton(self.settings)
        self.button.pressed.connect(self._on_press)
        self.button.released.connect(self._on_release)
        self.button.released.connect(self.update_visibility)
        self.button.key_event.connect(self._on_key)
        self.button.drag_finished.connect(self.update_visibility)
        self.button.settings_requested.connect(self.show_settings)
        self.watchdog = QTimer(self, singleShot=True, timeout=self._watchdog_fired)
        self.hide_timer = QTimer(self, singleShot=True, interval=HIDE_DELAY_MS, timeout=self.update_visibility)
        self.busy_retry = QTimer(self, singleShot=True, interval=500, timeout=self.update_visibility)

        self.tablet = TabletMode(self.update_visibility)
        self.settings.changed.connect(lambda k: k in ("show_when", "tablet_only") and self.update_visibility())
        self._make_tray()
        self.update_visibility()
        if self.im is not None and self.settings.get("reopen_settings"):
            self.settings.set("reopen_settings", False)
            QTimer.singleShot(300, self.show_settings)

        if self.im is None and self.settings.get("show_when") == "text" and not kde.is_our_input_method():
            self.notify("Flow Float is not your virtual keyboard yet",
                        "Open Flow Float settings and choose “Use Flow Float as virtual keyboard” so the "
                        "button appears when a text field is focused.")
        if self.holder.uinput_error:
            self.notify("No virtual keyboard",
                        f"{self.holder.uinput_error}. Falling back to injecting into an existing keyboard.")

    # -- tray ---------------------------------------------------------------
    def _make_tray(self):
        self.tray = QSystemTrayIcon(QIcon(str(kde.ICON)), self)
        self.tray.setToolTip("Flow Float")
        menu = QMenu()
        self.show_action = QAction("Enable the dock", menu, checkable=True)
        self.show_action.toggled.connect(self._set_user_visible)
        menu.addAction(self.show_action)
        menu.addAction(QIcon.fromTheme("configure"), "Settings…", self.show_settings)
        menu.addSeparator()
        self.restore_action = menu.addAction(QIcon.fromTheme("input-keyboard-virtual"), "", self.restore_keyboard)
        menu.aboutToShow.connect(self._refresh_tray_menu)
        menu.addAction(QIcon.fromTheme("application-exit"), "Quit", self.app.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: reason == QSystemTrayIcon.ActivationReason.Trigger and self.show_settings())
        self.tray.show()
        self._tray_menu = menu

    def _refresh_tray_menu(self):
        name = kde.input_method_name(self.settings.get("previous_im") or kde.PLASMA_KEYBOARD)
        self.restore_action.setText(f"Switch virtual keyboard back to {name}")
        self.restore_action.setVisible(kde.is_our_input_method())

    def restore_keyboard(self):
        kde.restore_previous(self.settings)  # KWin stops us and starts the other keyboard

    def notify(self, title, text):
        self.tray.showMessage(title, text, QIcon(str(kde.ICON)), 6000)

    # -- visibility ---------------------------------------------------------
    def _set_user_visible(self, on):
        self.user_hidden = not on
        self.update_visibility()

    def toggle_button(self):
        self.show_action.setChecked(self.user_hidden)

    def _on_text_focus(self, focused):
        self.text_focus = focused
        if focused:
            self.hide_timer.stop()
            self.update_visibility()
        else:
            self.hide_timer.start()

    def update_visibility(self):
        s = self.settings
        wanted = (not self.user_hidden
                  and (s.get("show_when") == "always" or self.text_focus)
                  and (not s.get("tablet_only") or not self.tablet.available or self.tablet.active))
        # Never pull the dock out from under a finger; look again shortly.
        if not wanted and self.button.busy:
            self.busy_retry.start()
            return
        if wanted != self.button.isVisible():
            self.button.setVisible(wanted)
        self.show_action.blockSignals(True)
        self.show_action.setChecked(not self.user_hidden)
        self.show_action.blockSignals(False)

    # -- actions ------------------------------------------------------------
    def show_settings(self):
        if self.config is None:
            self.config = ConfigWindow(self.settings, self.holder, running_as_im=self.im is not None,
                                       tablet=self.tablet)
        self.config.show()
        self.config.raise_()
        self.config.activateWindow()

    def _on_press(self):
        s = self.settings
        err = self.holder.press(s.get("combo"), s.get("backend"), s.get("key_delay_ms"))
        if err:
            self.button.force_release()
            self._key_error("Flow Float could not press the keys", err)
            return
        self._key_error_shown = False
        log("holding", keys.combo_label(s.get("combo")), "via", self.holder.backend_used)
        self.watchdog.start(s.get("max_hold_s") * 1000)

    def _watchdog_fired(self):
        minutes = self.settings.get("max_hold_s") / 60
        log("watchdog released the keys")
        self.button.force_release()
        self.notify("Flow Float released the keys",
                    f"They were held for {minutes:g} min, the limit set in Settings → Advanced.")

    def _on_key(self, name, down):
        err = self.holder.key(name, down)
        if err and down:
            self._key_error("Flow Float could not press the key", err)
        elif down:
            self._key_error_shown = False

    def _key_error(self, title, text):
        if not self._key_error_shown:
            self._key_error_shown = True
            self.notify(title, text)

    def _on_release(self):
        self.watchdog.stop()
        self.holder.release(self.settings.get("key_delay_ms"))
        log("released")

    def handle_command(self, cmd):
        {"settings": self.show_settings, "toggle": self.toggle_button,
         "quit": self.app.quit}.get(cmd, self.show_settings)()

    def shutdown(self):
        """Release everything. Runs once, from aboutToQuit while Qt objects still exist."""
        if self._shut_down:
            return
        self._shut_down = True
        self.button.release_all()
        self.holder.close()
        self.settings.flush()


def _send(cmd):
    """Send a command to a running instance. Returns True if one answered."""
    sock = QLocalSocket()
    sock.connectToServer(SOCKET)
    if not sock.waitForConnected(300):
        return False
    sock.write(cmd.encode())
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def main():
    ap = argparse.ArgumentParser(prog="flow-float", description="Floating push-to-talk button for Wispr Flow")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--settings", dest="cmd", action="store_const", const="settings", help="open the settings window")
    g.add_argument("--toggle", dest="cmd", action="store_const", const="toggle", help="enable/disable the button")
    g.add_argument("--quit", dest="cmd", action="store_const", const="quit", help="quit the running instance")
    g.add_argument("--background", dest="cmd", action="store_const", const="ping",
                   help="start without opening anything (used at login)")
    g.add_argument("--uninstall", dest="cmd", action="store_const", const="uninstall",
                   help="restore the previous virtual keyboard and remove Flow Float's desktop files")
    args = ap.parse_args()

    # When KWin starts us as the virtual keyboard it hands over a private Wayland
    # connection here. Take it before Qt does; Qt connects via WAYLAND_DISPLAY.
    im_fd = os.environ.pop("WAYLAND_SOCKET", None)
    if im_fd is not None:
        im_fd = int(im_fd)
        if not os.environ.get("WAYLAND_DISPLAY"):
            runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
            sockets = sorted(p.name for p in Path(runtime).glob("wayland-[0-9]") if p.is_socket())
            os.environ["WAYLAND_DISPLAY"] = sockets[0] if sockets else "wayland-0"

    app = QApplication(sys.argv)
    app.setApplicationName("flow-float")
    app.setApplicationDisplayName("Flow Float")
    app.setDesktopFileName("flow-float")
    app.setWindowIcon(QIcon(str(kde.ICON)))
    app.setQuitOnLastWindowClosed(False)

    if args.cmd == "uninstall":
        _send("quit")
        settings = Settings()
        kde.uninstall(settings)
        settings.flush()
        print("Flow Float removed. Virtual keyboard:", kde.input_method_name(kde.current_input_method()))
        return 0

    server = QLocalServer()
    if im_fd is None:
        # Forward to a running instance. "ping" (--background) just checks it is there.
        if _send(args.cmd or "settings"):
            return 0
        if args.cmd == "quit":
            return 0
        if args.cmd == "ping":
            args.cmd = None
        # Claim the socket before building anything. If another copy got there in
        # the meantime it answers and we leave; otherwise the socket file is stale.
        if not server.listen(SOCKET):
            if _send(args.cmd or "ping"):
                return 0
            QLocalServer.removeServer(SOCKET)
            if not server.listen(SOCKET):
                log("cannot listen on the command socket:", server.errorString())
                return 1
    else:
        # The copy KWin starts must win: KWin talks only to it.
        if _send("quit"):
            for _ in range(30):
                time.sleep(0.1)
                if not _send("ping"):
                    break
        QLocalServer.removeServer(SOCKET)
        if not server.listen(SOCKET):
            log("cannot listen on the command socket:", server.errorString())

    kde.install_desktop_file()
    ff = FlowFloat(app, im_fd)

    def on_conn():
        c = server.nextPendingConnection()

        def read():
            cmd = bytes(c.readAll()).decode().strip()
            if cmd != "ping":
                ff.handle_command(cmd)
        c.readyRead.connect(read)
        c.disconnected.connect(c.deleteLater)
    server.newConnection.connect(on_conn)

    # Never leave keys stuck down.
    app.aboutToQuit.connect(ff.shutdown)
    atexit.register(ff.holder.close)  # last resort if Qt never got to aboutToQuit
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: app.quit())
    tick = QTimer(interval=300)  # lets Python run its signal handlers
    tick.timeout.connect(lambda: None)
    tick.start()

    if args.cmd and im_fd is None:
        QTimer.singleShot(0, lambda: ff.handle_command(args.cmd))
    return app.exec()
