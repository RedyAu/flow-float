"""Application shell: tray icon, single instance, KWin input-method and tablet-mode integration."""

import argparse
import atexit
import os
import signal
import sys
import time

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
        bus = QDBusConnection.sessionBus()
        iface = QDBusInterface(*KWIN, bus)
        if iface.isValid():
            self.active = bool(iface.property("tabletMode"))
        bus.connect(*KWIN, "tabletModeChanged", self, SLOT("changed(bool)"))

    @Slot(bool)
    def changed(self, on):
        self.active = on
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

        self.im = None
        if im_fd is not None:
            from .wayland_im import InputMethod
            self.im = InputMethod(im_fd)
            self.im.focus_changed.connect(self._on_text_focus)
            log("running as KWin input method")

        self.button = FloatingButton(self.settings)
        self.button.pressed.connect(self._on_press)
        self.button.released.connect(self._on_release)
        self.button.released.connect(self.update_visibility)
        self.button.key_event.connect(self._on_key)
        self.button.drag_finished.connect(self.update_visibility)
        self.button.settings_requested.connect(self.show_settings)
        self.watchdog = QTimer(self, singleShot=True, timeout=self.button.force_release)
        self.hide_timer = QTimer(self, singleShot=True, interval=HIDE_DELAY_MS, timeout=self.update_visibility)

        self.tablet = TabletMode(self.update_visibility)
        self.settings.changed.connect(lambda k: k in ("show_when", "tablet_only") and self.update_visibility())
        self._make_tray()
        self.update_visibility()

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
                  and (not s.get("tablet_only") or self.tablet.active))
        # Never pull the button out from under a finger.
        if not wanted and self.button.busy:
            QTimer.singleShot(500, self.update_visibility)
            return
        if wanted != self.button.isVisible():
            self.button.setVisible(wanted)
        self.show_action.blockSignals(True)
        self.show_action.setChecked(not self.user_hidden)
        self.show_action.blockSignals(False)

    # -- actions ------------------------------------------------------------
    def show_settings(self):
        if self.config is None:
            self.config = ConfigWindow(self.settings, self.holder, running_as_im=self.im is not None)
        self.config.show()
        self.config.raise_()
        self.config.activateWindow()

    def _on_press(self):
        s = self.settings
        err = self.holder.press(s.get("combo"), s.get("backend"), s.get("key_delay_ms"))
        if err:
            self.button.force_release()
            self.notify("Flow Float could not press the keys", err)
            return
        log("holding", keys.combo_label(s.get("combo")), "via", self.holder.backend_used)
        self.watchdog.start(s.get("max_hold_s") * 1000)

    def _on_key(self, name, down):
        err = self.holder.key(name, down)
        if err and down:
            self.notify("Flow Float could not press the key", err)

    def _on_release(self):
        self.watchdog.stop()
        self.holder.release(self.settings.get("key_delay_ms"))
        log("released")

    def handle_command(self, cmd):
        {"settings": self.show_settings, "toggle": self.toggle_button,
         "quit": self.app.quit}.get(cmd, self.show_settings)()

    def shutdown(self):
        self.button.release_all()
        self.holder.close()


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
    args = ap.parse_args()

    # When KWin starts us as the virtual keyboard it hands over a private Wayland
    # connection here. Take it before Qt does; Qt connects via WAYLAND_DISPLAY.
    im_fd = os.environ.pop("WAYLAND_SOCKET", None)
    if im_fd is not None:
        im_fd = int(im_fd)
        os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")

    app = QApplication(sys.argv)
    app.setApplicationName("flow-float")
    app.setApplicationDisplayName("Flow Float")
    app.setDesktopFileName("flow-float")
    app.setWindowIcon(QIcon(str(kde.ICON)))
    app.setQuitOnLastWindowClosed(False)

    if im_fd is None:
        # Forward to a running instance. "ping" (--background) just checks it is there.
        if _send(args.cmd or "settings"):
            return 0
        if args.cmd == "quit":
            return 0
        if args.cmd == "ping":
            args.cmd = None
    elif _send("quit"):
        # The copy KWin starts must win: KWin talks only to it.
        for _ in range(30):
            time.sleep(0.1)
            if not _send("ping"):
                break

    kde.install_desktop_file()
    ff = FlowFloat(app, im_fd)
    server = QLocalServer()
    QLocalServer.removeServer(SOCKET)
    server.listen(SOCKET)

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
    atexit.register(ff.shutdown)
    app.aboutToQuit.connect(ff.shutdown)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: app.quit())
    tick = QTimer(interval=300)  # lets Python run its signal handlers
    tick.timeout.connect(lambda: None)
    tick.start()

    if args.cmd and im_fd is None:
        QTimer.singleShot(0, lambda: ff.handle_command(args.cmd))
    return app.exec()
