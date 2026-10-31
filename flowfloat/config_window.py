"""Settings window, laid out like a KDE System Settings module and usable with touch only."""

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialogButtonBox, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from . import kde, keys

ROW_H = 40  # touch-friendly control height
ADVANCED_PAGE = 3


def _tall(widget, h=ROW_H):
    widget.setMinimumHeight(h)
    return widget


def _note(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)  # KDE's convention for explanatory text
    return label


def _separator():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


class Page(QScrollArea):
    """A titled, scrollable page with a centred form, like a Kirigami FormLayout."""

    def __init__(self, title):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(14)
        heading = QLabel(title)
        f = heading.font()
        f.setPointSizeF(f.pointSizeF() * 1.4)
        heading.setFont(f)
        outer.addWidget(heading)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.form.setHorizontalSpacing(16)
        self.form.setVerticalSpacing(12)
        outer.addLayout(self.form)
        outer.addStretch()
        self.setWidget(inner)

    def row(self, label, field):
        self.form.addRow(label, field)

    def full(self, widget):
        self.form.addRow(widget)

    def gap(self):
        self.form.addRow(_separator())


class ConfigWindow(QWidget):
    def __init__(self, settings, holder, running_as_im=False, tablet=None):
        super().__init__()
        self.tablet = tablet
        self.settings = settings
        self.holder = holder
        self.running_as_im = running_as_im
        self.setWindowTitle("Flow Float Settings")
        self.resize(860, 620)
        self.setMinimumSize(640, 480)
        self._loading = False

        self.nav = QListWidget()
        self.nav.setIconSize(QSize(28, 28))
        self.nav.setFixedWidth(200)
        self.nav.setSpacing(2)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.pages = QStackedWidget()
        for icon, title, page in (("preferences-system", "General", self._general_page()),
                                  ("input-keyboard", "Shortcut", self._shortcut_page()),
                                  ("view-grid", "Dock", self._dock_page()),
                                  ("configure", "Advanced", self._advanced_page())):
            item = QListWidgetItem(QIcon.fromTheme(icon), title)
            item.setSizeHint(QSize(0, 48))
            self.nav.addItem(item)
            self.pages.addWidget(page)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.currentRowChanged.connect(lambda _row: self._refresh_status())
        self.nav.setCurrentRow(0)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        for b in buttons.buttons():
            _tall(b)

        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self.nav)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        body.addWidget(divider)
        body.addWidget(self.pages, 1)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 8)
        root.addLayout(body, 1)
        root.addWidget(_separator())
        foot = QHBoxLayout()
        foot.setContentsMargins(12, 0, 12, 0)
        foot.addWidget(buttons)
        root.addLayout(foot)

        # Only the Advanced page shows live state worth polling.
        self._route_timer = QTimer(self, interval=3000, timeout=self._refresh_route)
        self._own_change = False
        self.load()
        settings.changed.connect(self._on_setting_changed)

    # -- pages --------------------------------------------------------------
    def _general_page(self):
        page = Page("General")
        self.kb_status = QLabel()
        self.kb_status.setWordWrap(True)
        page.full(self.kb_status)
        self.kb_use = _tall(QPushButton(QIcon.fromTheme("input-keyboard-virtual"),
                                        "Use Flow Float as the virtual keyboard"), 44)
        self.kb_use.clicked.connect(self._use_flow_float)
        self.kb_restore = _tall(QPushButton(QIcon.fromTheme("edit-undo"), ""), 44)
        self.kb_restore.clicked.connect(lambda: (kde.restore_previous(self.settings), self._refresh_status()))
        row = QHBoxLayout()
        row.addWidget(self.kb_use)
        row.addWidget(self.kb_restore)
        row.addStretch()
        page.full(_wrap(row))
        page.gap()

        self.show_when = _tall(QComboBox())
        self.show_when.addItem("When a text field has focus", "text")
        self.show_when.addItem("Always", "always")
        self.show_when.currentIndexChanged.connect(lambda: self._set("show_when", self.show_when.currentData()))
        page.row("Show the dock:", self.show_when)
        self.tablet_only = QCheckBox("Only in tablet mode")
        self.tablet_only.toggled.connect(lambda on: self._set("tablet_only", on))
        page.row("", self.tablet_only)
        self.tablet_note = _note("This device has no tablet mode, so the dock shows regardless.")
        page.row("", self.tablet_note)
        page.gap()

        self.autostart = QCheckBox("Start Flow Float when I log in")
        self.autostart.toggled.connect(lambda on: self._loading or kde.set_autostart(on))
        page.row("Login:", self.autostart)
        self.autostart_note = _note("Not needed while Flow Float is the virtual keyboard: KWin starts it then.")
        page.row("", self.autostart_note)
        return page

    def _shortcut_page(self):
        page = Page("Shortcut")
        self._shortcut_form = page.form
        self.wispr = {}
        self.wispr_combo = _tall(QComboBox())
        use = _tall(QPushButton("Use"))
        use.clicked.connect(lambda: self.wispr_combo.currentData() in self.wispr and
                            self.settings.set("combo", self.wispr[self.wispr_combo.currentData()]))
        row = QHBoxLayout()
        row.addWidget(self.wispr_combo, 1)
        row.addWidget(use)
        self.wispr_row = _wrap(row)
        page.row("Wispr Flow shortcut:", self.wispr_row)
        self.wispr_gap = _separator()
        page.full(self.wispr_gap)

        mods = QHBoxLayout()
        self.mod_buttons = {}
        for label, name in keys.MODIFIERS:
            b = _tall(QPushButton(label), 44)
            b.setCheckable(True)
            b.setMinimumWidth(72)
            b.toggled.connect(self._combo_edited)
            mods.addWidget(b)
            self.mod_buttons[name] = b
        mods.addStretch()
        page.row("Modifiers:", _wrap(mods))

        self.key_combo = _tall(QComboBox())
        self.key_combo.setMaxVisibleItems(15)
        self.key_combo.addItem("(none)", "")
        mod_names = {n for _, n in keys.MODIFIERS}
        for n in keys.selectable_keys():
            if n not in mod_names:
                self.key_combo.addItem(keys.key_label(n), n)
        self.key_combo.currentIndexChanged.connect(self._combo_edited)
        page.row("Plus key:", self.key_combo)

        self.combo_label = QLabel()
        f = self.combo_label.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.combo_label.setFont(f)
        page.row("Holds:", self.combo_label)
        page.gap()

        self.latch = _tall(QSpinBox(minimum=0, maximum=2000, singleStep=50, suffix=" ms"))
        self.latch.setSpecialValueText("Off")
        self.latch.valueChanged.connect(lambda v: self._set("latch_ms", v))
        page.row("Tap to latch:", self.latch)
        page.row("", _note("A tap shorter than this keeps the keys held until you tap the mic again. "
                           "Hold the mic longer and it works as push-to-talk, releasing when you let go. "
                           "Off means always push-to-talk."))
        return page

    def _dock_page(self):
        page = Page("Dock")
        self.show_backspace = QCheckBox("Backspace")
        self.show_backspace.toggled.connect(lambda on: self._set("show_backspace", on))
        self.show_enter = QCheckBox("Return")
        self.show_enter.toggled.connect(lambda on: self._set("show_enter", on))
        page.row("Keys:", self.show_backspace)
        page.row("", self.show_enter)
        self.return_long = _tall(QSpinBox(minimum=200, maximum=2000, singleStep=50, suffix=" ms"))
        self.return_long.valueChanged.connect(lambda v: self._set("return_long_ms", v))
        page.row("Shift+Return after:", self.return_long)
        page.row("", _note("Tap Return for Return. Keep it pressed this long for Shift+Return "
                           "(a new line without sending, in most chat apps)."))
        page.gap()

        self.orientation = _tall(QComboBox())
        self.orientation.addItem("Vertical", "vertical")
        self.orientation.addItem("Horizontal", "horizontal")
        self.orientation.currentIndexChanged.connect(lambda: self._set("orientation", self.orientation.currentData()))
        self.orientation.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        page.row("Layout:", self.orientation)
        self.size_slider = _tall(QSlider(Qt.Orientation.Horizontal, minimum=44, maximum=140))
        self.size_slider.valueChanged.connect(lambda v: self._set("size", v))
        page.row("Size:", self.size_slider)
        self.opacity = _tall(QSlider(Qt.Orientation.Horizontal, minimum=20, maximum=100))
        self.opacity.valueChanged.connect(lambda v: self._set("idle_opacity", v / 100))
        page.row("Opacity when idle:", self.opacity)
        page.gap()

        reset = _tall(QPushButton(QIcon.fromTheme("edit-reset"), "Reset position"))
        reset.clicked.connect(lambda: self.settings.reset("corner_h", "corner_v", "margin_h", "margin_v"))
        reset.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        page.row("Position:", reset)
        page.row("", _note("Drag the dotted grip to move the dock. Tap the grip to open these settings."))
        return page

    def _advanced_page(self):
        page = Page("Advanced")
        self.backend = _tall(QComboBox())
        self.backend.addItem("Automatic", "auto")
        self.backend.addItem("Virtual keyboard (uinput)", "uinput")
        self.backend.addItem("Inject into the keyboard Wispr Flow reads", "inject")
        self.backend.currentIndexChanged.connect(lambda: self._set("backend", self.backend.currentData()))
        page.row("Send the shortcut via:", self.backend)
        self.route = QLabel()
        self.route.setWordWrap(True)
        self.route.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        page.row("Right now:", self.route)
        page.row("", _note("Automatic uses Flow Float's virtual keyboard when Wispr Flow can see it, and "
                           "otherwise sends the keys through a keyboard Wispr Flow already reads. "
                           "Backspace and Return always use the virtual keyboard."))
        page.gap()
        self.max_hold = _tall(QSpinBox(minimum=10, maximum=3600, singleStep=30, suffix=" s"))
        self.max_hold.valueChanged.connect(lambda v: self._set("max_hold_s", v))
        page.row("Release automatically after:", self.max_hold)
        self.delay = _tall(QSpinBox(minimum=0, maximum=keys.MAX_KEY_DELAY_MS, singleStep=5, suffix=" ms"))
        self.delay.valueChanged.connect(lambda v: self._set("key_delay_ms", v))
        page.row("Delay between keys:", self.delay)
        return page

    # -- data flow ----------------------------------------------------------
    def _set(self, key, value):
        if not self._loading:
            self._own_change = True
            try:
                self.settings.set(key, value)
            finally:
                self._own_change = False

    def _on_setting_changed(self, _key):
        # Our own controls already show the new value; reload only for outside changes.
        if not self._own_change:
            self.load()

    def _use_flow_float(self):
        kde.use_flow_float(self.settings, reopen_settings=not self.running_as_im)
        self._refresh_status()
        if not self.running_as_im:
            self.kb_status.setText("Switching… KWin is starting Flow Float as the virtual keyboard. "
                                   "This window will reopen in a moment.")

    def _refresh_wispr(self):
        self.wispr = keys.wispr_shortcuts()
        current = self.wispr_combo.currentData() or "ptt"
        self.wispr_combo.clear()
        for action, combo in self.wispr.items():
            self.wispr_combo.addItem(f"{keys.WISPR_ACTIONS.get(action, action)}  ({keys.combo_label(combo)})",
                                     action)
        self.wispr_combo.setCurrentIndex(max(0, self.wispr_combo.findData(current)))
        for w in (self.wispr_row, self.wispr_gap):  # hide the rows when Wispr Flow isn't set up
            self._shortcut_form.setRowVisible(w, bool(self.wispr))

    def _combo_edited(self):
        if self._loading:
            return
        combo = [n for _, n in keys.MODIFIERS if self.mod_buttons[n].isChecked()]
        if self.key_combo.currentData():
            combo.append(self.key_combo.currentData())
        self._set("combo", combo)
        self.combo_label.setText(keys.combo_label(combo))

    def load(self):
        s = self.settings
        self._loading = True
        try:
            combo = s.get("combo")
            for name, b in self.mod_buttons.items():
                b.setChecked(name in combo)
            extra = [n for n in combo if n not in self.mod_buttons]
            i = self.key_combo.findData(extra[0]) if extra else 0
            self.key_combo.setCurrentIndex(max(i, 0))
            self.combo_label.setText(keys.combo_label(combo))
            self.latch.setValue(s.get("latch_ms"))
            self.max_hold.setValue(s.get("max_hold_s"))
            self.delay.setValue(s.get("key_delay_ms"))
            self.size_slider.setValue(s.get("size"))
            self.opacity.setValue(int(s.get("idle_opacity") * 100))
            self.show_when.setCurrentIndex(max(0, self.show_when.findData(s.get("show_when"))))
            self.tablet_only.setChecked(s.get("tablet_only"))
            self.show_backspace.setChecked(s.get("show_backspace"))
            self.show_enter.setChecked(s.get("show_enter"))
            self.return_long.setValue(s.get("return_long_ms"))
            self.orientation.setCurrentIndex(max(0, self.orientation.findData(s.get("orientation"))))
            self.backend.setCurrentIndex(max(0, self.backend.findData(s.get("backend"))))
            self.autostart.setChecked(kde.autostart_enabled())
        finally:
            self._loading = False

    def _refresh_route(self):
        if self.pages.currentIndex() == ADVANCED_PAGE:
            self.route.setText(self.holder.describe_route(self.settings.get("backend")))

    def _refresh_status(self):
        self._refresh_route()
        self.tablet_note.setVisible(self.tablet is not None and not self.tablet.available
                                    and self.settings.get("tablet_only"))
        current = kde.current_input_method()
        ours = current == str(kde.DESKTOP_FILE)
        prev = self.settings.get("previous_im") or kde.PLASMA_KEYBOARD
        if ours:
            self.kb_status.setText("<b>Flow Float is your virtual keyboard.</b> The dock appears in place of the "
                                   "on-screen keyboard whenever a text field gets focus."
                                   + ("" if self.running_as_im else " (Waiting for KWin to start it…)"))
        else:
            self.kb_status.setText(f"KDE's virtual keyboard is currently <b>{kde.input_method_name(current)}</b>. "
                                   "Make Flow Float the virtual keyboard so the dock appears exactly when a "
                                   "text field has focus.")
        # KWin starts the virtual keyboard itself; a login autostart would only compete with it.
        self.autostart.setEnabled(not ours)
        self.autostart_note.setVisible(ours)
        self.kb_use.setVisible(not ours)
        self.kb_restore.setVisible(ours)
        self.kb_restore.setText(f"Switch back to {kde.input_method_name(prev)}")

    def showEvent(self, ev):
        self._refresh_wispr()
        self.load()
        self._refresh_status()
        self._route_timer.start()
        super().showEvent(ev)

    def hideEvent(self, ev):
        self._route_timer.stop()
        super().hideEvent(ev)


def _wrap(layout):
    layout.setContentsMargins(0, 0, 0, 0)
    w = QWidget()
    w.setLayout(layout)
    return w
