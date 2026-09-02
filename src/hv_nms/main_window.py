from __future__ import annotations

import csv
import ipaddress
import json
import queue
import time
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .backend import MonitorBackend
from .config import load_config, save_config
from .constants import (
    APP_TITLE,
    APP_VERSION,
    BORDER,
    BORDER_SOFT,
    CYAN,
    GREEN,
    GREEN_BORDER,
    GREEN_DARK,
    DISCOVERY_INTERVAL_OPTIONS,
    MUTED,
    ORANGE,
    PANEL_BG,
    PANEL_BG_2,
    RED,
    RED_BORDER,
    RED_DARK,
    ROW_ALT,
    SCAN_INTERVAL_OPTIONS,
    SCAN_MODE_ALL_AT_ONCE,
    SCAN_MODE_ONE_BY_ONE,
    SELECTED,
    TEXT,
    TREND_GRAPH_OPTIONS,
    WINDOW_BG,
)
from .models import DeviceRecord, EventRecord, find_device
from .network import NetworkInterface, interface_discovery_range, scan_interfaces
from .widgets import FavouriteTile, HistoryGraph, SectionTitle, Sparkline, StatusDot


class ReorderTable(QTableWidget):
    reordered = Signal(int, int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._drag_row = -1
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def startDrag(self, supportedActions):
        self._drag_row = self.currentRow()
        super().startDrag(supportedActions)

    def dropEvent(self, event):
        if self._drag_row < 0:
            return super().dropEvent(event)
        index = self.indexAt(event.position().toPoint())
        dest = index.row() if index.isValid() else self.rowCount() - 1
        source = self._drag_row
        event.ignore()
        if source != dest and source >= 0 and dest >= 0:
            self.reordered.emit(source, dest)
        self._drag_row = -1


class Panel(QFrame):
    def __init__(self, title: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(14, 12, 14, 12)
        self.layout.setSpacing(8)
        self.title_label: SectionTitle | None = None
        if title:
            self.title_label = SectionTitle(title)
            self.layout.addWidget(self.title_label)


class MainWindow(QMainWindow):
    def __init__(self, backend: MonitorBackend):
        super().__init__()
        self.backend = backend
        self.settings = backend.settings
        self.selected_device_id: str | None = None
        self.selected_discovery_id: str | None = None
        self.interfaces: list[NetworkInterface] = []
        self._loading_controls = False
        self._started_at = time.time()
        self.setWindowTitle(f"{APP_TITLE} {APP_VERSION}")
        self.resize(1648, 928)
        self.setMinimumSize(1380, 780)
        self._apply_style()
        self._build_ui()
        self._refresh_interfaces()
        self._load_controls_from_settings()
        self._refresh_all()
        self.backend.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(200)

    # ---------- global visual shell ----------
    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {WINDOW_BG}; color: {TEXT}; font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 13px; }}
            QFrame#panel, QFrame#favouriteTile {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 5px; }}
            QLabel#sectionTitle {{ color: {CYAN}; font-size: 16px; font-weight: 500; border: none; background: transparent; }}
            QLabel#favName {{ color: {TEXT}; font-size: 16px; font-weight: 600; border: none; background: transparent; }}
            QLabel#favDetail {{ color: {TEXT}; font-size: 12px; border: none; background: transparent; }}
            QLabel#muted {{ color: {MUTED}; }}
            QPushButton {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 4px; min-height: 30px; padding: 3px 13px; color: {TEXT}; }}
            QPushButton:hover {{ border-color: #657076; }}
            QPushButton:pressed {{ background: #1b252a; }}
            QPushButton#primary {{ color: {CYAN}; border-color: #087aa0; }}
            QPushButton#danger {{ color: {RED}; border-color: {RED_BORDER}; }}
            QPushButton#scanActive {{ color: {GREEN}; border-color: {GREEN_BORDER}; background: #102516; font-weight: 600; }}
            QPushButton#scanStopped {{ color: {RED}; border-color: {RED_BORDER}; background: #241316; font-weight: 600; }}
            QPushButton#tab {{ border: none; border-radius: 0; background: #11191d; min-height: 38px; font-size: 15px; }}
            QPushButton#tab:checked {{ border-bottom: 2px solid #d7dde0; background: #141c20; }}
            QPushButton#sideTab {{ text-align: left; padding-left: 22px; border: none; min-height: 48px; font-size: 16px; }}
            QPushButton#sideTab:checked {{ background: #133047; color: {CYAN}; border-left: 2px solid {CYAN}; }}
            QLineEdit, QComboBox, QSpinBox {{ background: #10171b; border: 1px solid {BORDER}; border-radius: 4px; min-height: 30px; padding: 2px 10px; selection-background-color: {SELECTED}; }}
            QComboBox::drop-down {{ border: none; width: 24px; }}
            QTableWidget {{ background: {PANEL_BG}; alternate-background-color: {ROW_ALT}; border: 1px solid {BORDER}; gridline-color: {BORDER_SOFT}; selection-background-color: {SELECTED}; selection-color: {TEXT}; }}
            QHeaderView::section {{ background: #121a1e; color: {TEXT}; border: none; border-right: 1px solid {BORDER_SOFT}; border-bottom: 1px solid {BORDER}; padding: 7px; }}
            QScrollBar:vertical {{ background: #0e1519; width: 11px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: #4c555a; border-radius: 5px; min-height: 25px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(5, 4, 5, 5)
        outer.setSpacing(7)
        outer.addWidget(self._build_header())
        self.status_strip = QLabel()
        self.status_strip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_strip.setFixedHeight(38)
        outer.addWidget(self.status_strip)
        outer.addWidget(self._build_tabs())
        self.stack = QStackedWidget()
        self.run_page = self._build_run_page()
        self.setup_page = self._build_setup_page()
        self.discovery_page = self._build_discovery_page()
        self.log_page = self._build_log_page()
        for page in (self.run_page, self.setup_page, self.discovery_page, self.log_page):
            self.stack.addWidget(page)
        outer.addWidget(self.stack, 1)
        outer.addWidget(self._build_footer())
        self._show_page(0)

    def _build_header(self) -> QWidget:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(15, 5, 15, 3)
        layout.setSpacing(15)
        badge = QLabel("HV P2P\nNMS")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(100, 64)
        badge.setStyleSheet(f"border:1px solid {GREEN_BORDER}; border-radius:9px; font-size:18px; background:{WINDOW_BG};")
        layout.addWidget(badge)
        layout.addStretch(1)
        self.favourite_tiles = [FavouriteTile(), FavouriteTile(), FavouriteTile()]
        for tile in self.favourite_tiles:
            layout.addWidget(tile, 3)
        layout.addStretch(1)
        self.version_label = QLabel(APP_VERSION)
        self.version_label.setStyleSheet(f"color:{MUTED};")
        self.version_label.setMinimumWidth(120)
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.version_label)
        frame.setFixedHeight(75)
        return frame

    def _build_tabs(self) -> QWidget:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(0)
        self.tab_buttons: list[QPushButton] = []
        for i, text in enumerate(("▷  Run", "⚙  Setup", "⌖  Discovery", "▤  Log")):
            btn = QPushButton(text)
            btn.setObjectName("tab")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, idx=i: self._show_page(idx))
            self.tab_buttons.append(btn)
            layout.addWidget(btn, 1)
        frame.setFixedHeight(43)
        return frame

    def _build_footer(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 5, 14, 5)
        self.time_label = QLabel()
        self.uptime_label = QLabel()
        self.time_label.setStyleSheet(f"color:{MUTED};")
        self.uptime_label.setStyleSheet(f"color:{MUTED};")
        layout.addWidget(self.time_label)
        layout.addStretch(1)
        layout.addWidget(self.uptime_label)
        frame.setFixedHeight(45)
        return frame

    def _show_page(self, index: int) -> None:
        if not hasattr(self, "stack"):
            return
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.tab_buttons):
            btn.setChecked(i == index)
        self._update_status_strip()

    def _update_status_strip(self) -> None:
        active = self.backend.scan_active
        page = self.stack.currentIndex() if hasattr(self, "stack") else 0
        if page == 0:
            detail = "Scan Mode Active" if active else "Scan Mode Inactive"
        elif page == 1:
            detail = "Setup | Scan Active in Background" if active else "Setup | Scan Inactive"
        elif page == 2:
            detail = "Discovery Mode | Scan Active in Background" if active else "Discovery Mode | Scan Inactive"
        else:
            detail = "Log | Scan Active in Background" if active else "Log | Scan Inactive"
        if active:
            self.status_strip.setText(f"◇  Network Monitor | {detail}")
            self.status_strip.setStyleSheet(
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "stop:0 #103518, stop:0.50 #174b20, stop:1 #103518);"
                f"border:1px solid {GREEN_BORDER}; border-radius:4px; color:#d9e8db; font-size:20px;"
            )
        else:
            self.status_strip.setText(f"◇  Network Monitor | {detail}")
            self.status_strip.setStyleSheet(
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "stop:0 #321416, stop:0.50 #4d171a, stop:1 #321416);"
                f"border:1px solid {RED_BORDER}; border-radius:4px; color:#ff5961; font-size:20px;"
            )

    # ---------- Run page ----------
    def _build_run_page(self) -> QWidget:
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(14, 0, 14, 0)
        outer.setSpacing(9)

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(8)
        left_l.addWidget(self._build_run_controls())
        left_l.addWidget(self._build_device_table(), 3)
        left_l.addWidget(self._build_run_bottom(), 2)
        outer.addWidget(left, 1)
        outer.addWidget(self._build_run_sidebar())
        return page

    def _labelled_control(self, label: str, widget: QWidget, parent_layout: QHBoxLayout | QGridLayout, row: int | None = None, col: int | None = None):
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        lbl = QLabel(label.upper())
        lbl.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        v.addWidget(lbl)
        v.addWidget(widget)
        if row is None:
            parent_layout.addWidget(wrap)
        else:
            parent_layout.addWidget(wrap, row, col)
        return wrap

    def _build_run_controls(self) -> QWidget:
        frame = QFrame(); frame.setObjectName("panel")
        l = QHBoxLayout(frame); l.setContentsMargins(10, 8, 10, 8); l.setSpacing(10)
        self.operating_combo = QComboBox(); self.operating_combo.addItems(["Scan Mode", "Discovery Mode"])
        self.operating_combo.currentTextChanged.connect(self._operating_mode_changed)
        self.scan_mode_combo = QComboBox(); self.scan_mode_combo.addItems([SCAN_MODE_ALL_AT_ONCE, SCAN_MODE_ONE_BY_ONE]); self.scan_mode_combo.currentTextChanged.connect(self._settings_changed)
        self.frequency_combo = QComboBox(); [self.frequency_combo.addItem(f"{x:g} sec", x) for x in SCAN_INTERVAL_OPTIONS]; self.frequency_combo.currentIndexChanged.connect(self._settings_changed)
        self.trend_combo = QComboBox(); [self.trend_combo.addItem(label, seconds) for label, seconds in TREND_GRAPH_OPTIONS]; self.trend_combo.currentIndexChanged.connect(self._settings_changed)
        self._labelled_control("Operating Mode", self.operating_combo, l)
        self._labelled_control("Scan Mode", self.scan_mode_combo, l)
        self._labelled_control("Scan Frequency", self.frequency_combo, l)
        self._labelled_control("Trend Graph Window", self.trend_combo, l)
        self.add_btn = QPushButton("＋  Add"); self.add_btn.setObjectName("primary"); self.add_btn.clicked.connect(self._add_device)
        self.edit_btn = QPushButton("✎  Edit"); self.edit_btn.clicked.connect(self._edit_device)
        self.remove_btn = QPushButton("−  Remove"); self.remove_btn.clicked.connect(self._remove_devices)
        self.scan_btn = QPushButton(); self.scan_btn.clicked.connect(self._toggle_scan)
        for btn in (self.add_btn, self.edit_btn, self.remove_btn, self.scan_btn): l.addWidget(btn)
        return frame

    def _build_device_table(self) -> QWidget:
        self.device_table = ReorderTable(0, 7)
        self.device_table.setHorizontalHeaderLabels(["", "Device", "Host Name", "IP Address", "Latency", "Trend Graph", "Last Failed"])
        self.device_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.device_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.device_table.setAlternatingRowColors(True)
        self.device_table.verticalHeader().setVisible(False)
        self.device_table.verticalHeader().setDefaultSectionSize(30)
        header = self.device_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed); self.device_table.setColumnWidth(0, 42)
        for col in (1, 2, 3, 4, 6): header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.device_table.itemSelectionChanged.connect(self._device_selection_changed)
        self.device_table.doubleClicked.connect(lambda _idx: self._edit_device())
        self.device_table.reordered.connect(self._reorder_device)
        return self.device_table

    def _build_run_bottom(self) -> QWidget:
        wrap = QWidget(); g = QGridLayout(wrap); g.setContentsMargins(0, 0, 0, 0); g.setSpacing(9)
        # Details
        details = Panel("SELECTED DEVICE DETAILS")
        self.detail_labels: dict[str, QLabel] = {}
        for key, label in (("name", "Device"), ("hostname", "Host Name"), ("ip", "IP Address"), ("latency", "Latency"), ("failed", "Last Failed"), ("status", "Status")):
            row = QHBoxLayout(); row.addWidget(QLabel(label)); row.addStretch(1); val = QLabel("—"); self.detail_labels[key] = val; row.addWidget(val); details.layout.addLayout(row)
        favrow = QHBoxLayout(); favrow.addWidget(QLabel("Assign to Favourite")); favrow.addStretch(1)
        self.fav_buttons = []
        for slot in range(3):
            b = QPushButton(str(slot + 1)); b.setFixedWidth(48); b.clicked.connect(lambda _=False, s=slot: self._assign_favourite(s)); self.fav_buttons.append(b); favrow.addWidget(b)
        details.layout.addLayout(favrow)
        g.addWidget(details, 0, 0)
        # History
        history = Panel("LATENCY HISTORY (15 MIN)")
        self.history_panel = history
        self.history_legend = QLabel("—")
        self.history_legend.setAlignment(Qt.AlignmentFlag.AlignRight)
        history.layout.addWidget(self.history_legend)
        self.history_graph = HistoryGraph(); history.layout.addWidget(self.history_graph, 1)
        g.addWidget(history, 0, 1)
        # Event log - deliberately independent of Tools sidebar to prevent clipping/overlap.
        events = Panel("EVENT LOG")
        self.run_event_table = self._make_event_table(compact=True)
        events.layout.addWidget(self.run_event_table, 1)
        g.addWidget(events, 0, 2)
        g.setColumnStretch(0, 3); g.setColumnStretch(1, 7); g.setColumnStretch(2, 5)
        return wrap

    def _build_run_sidebar(self) -> QWidget:
        side = QWidget(); side.setFixedWidth(265)
        l = QVBoxLayout(side); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(8)
        summary = Panel("NETWORK SUMMARY")
        self.summary_labels = {}
        for key, name in (("total", "Total Devices"), ("online", "Online"), ("warning", "Warning (> 50 ms)"), ("offline", "Offline")):
            row=QHBoxLayout(); row.addWidget(QLabel(name)); row.addStretch(1); value=QLabel("0"); self.summary_labels[key]=value; row.addWidget(value); summary.layout.addLayout(row)
        l.addWidget(summary)
        thresholds = Panel("LATENCY THRESHOLDS")
        self.run_threshold_labels = {}
        for key, name, color in (("good","Good (<=)",GREEN),("warning","Warning (<=)",ORANGE),("poor","Poor (>)",RED)):
            row=QHBoxLayout(); dot=StatusDot(color, 12); row.addWidget(QLabel(name)); row.addStretch(1); row.addWidget(dot); val=QLabel(); self.run_threshold_labels[key]=val; row.addWidget(val); row.addWidget(QLabel("ms")); thresholds.layout.addLayout(row)
        l.addWidget(thresholds)
        tools = Panel("TOOLS")
        for text, handler in (("♜  Clear All Data", self._clear_all_data), ("⇧  Load Config", self._load_config_dialog), ("▣  Save Config", self._save_config_dialog), ("⇧  Export Data", self._export_data)):
            b=QPushButton(text); b.clicked.connect(handler); tools.layout.addWidget(b)
        l.addWidget(tools)
        l.addStretch(1)
        return side

    # ---------- Setup page ----------
    def _build_setup_page(self) -> QWidget:
        # All settings fit comfortably on one page. The previous left-side
        # General/Network/Scan/Discovery/Threshold/Config buttons only scrolled
        # to panels that were already visible, so they have been deliberately
        # removed rather than retaining controls that appear to do nothing.
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(14, 0, 14, 0)
        outer.setSpacing(9)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        g = QGridLayout(content)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(9)

        p = Panel("NETWORK INTERFACE")
        self.setup_network_panel = p
        self.interface_combo = QComboBox()
        self.interface_combo.currentIndexChanged.connect(self._interface_changed)
        p.layout.addWidget(QLabel("Interface"))
        p.layout.addWidget(self.interface_combo)
        g.addWidget(p, 0, 0)

        p = Panel("SCAN FREQUENCY")
        self.setup_scan_panel = p
        self.setup_frequency = QComboBox()
        for value in SCAN_INTERVAL_OPTIONS:
            self.setup_frequency.addItem(f"{value:g} sec", value)
        self.setup_frequency.currentIndexChanged.connect(self._setup_frequency_changed)
        p.layout.addWidget(QLabel("Frequency"))
        p.layout.addWidget(self.setup_frequency)
        g.addWidget(p, 0, 1)

        p = Panel("SCAN MODE")
        self.setup_all = QPushButton("All at Once")
        self.setup_one = QPushButton("One by One")
        self.setup_all.setCheckable(True)
        self.setup_one.setCheckable(True)
        row = QHBoxLayout()
        row.addWidget(self.setup_all)
        row.addWidget(self.setup_one)
        p.layout.addWidget(QLabel("Mode"))
        p.layout.addLayout(row)
        self.setup_all.clicked.connect(lambda: self._set_scan_mode(SCAN_MODE_ALL_AT_ONCE))
        self.setup_one.clicked.connect(lambda: self._set_scan_mode(SCAN_MODE_ONE_BY_ONE))
        g.addWidget(p, 0, 2)

        p = Panel("TREND GRAPH WINDOW")
        row = QHBoxLayout()
        self.setup_trend_buttons = []
        for label, seconds in TREND_GRAPH_OPTIONS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, s=seconds: self._set_trend(s))
            self.setup_trend_buttons.append((b, seconds))
            row.addWidget(b)
        p.layout.addWidget(QLabel("Window"))
        p.layout.addLayout(row)
        g.addWidget(p, 1, 0)

        p = Panel("PING TIMEOUT")
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(100, 10000)
        self.timeout_spin.setSuffix(" ms")
        self.timeout_spin.valueChanged.connect(self._settings_changed)
        p.layout.addWidget(QLabel("Timeout"))
        p.layout.addWidget(self.timeout_spin)
        g.addWidget(p, 1, 1, 1, 2)

        p = Panel("DISCOVERY RANGE")
        self.setup_discovery_panel = p
        rangeg = QGridLayout()
        self.start_ip = QLineEdit()
        self.end_ip = QLineEdit()
        self.subnet = QLineEdit()
        for c, (label, widget) in enumerate((("Start IP", self.start_ip), ("End IP", self.end_ip), ("Subnet", self.subnet))):
            rangeg.addWidget(QLabel(label), 0, c)
            rangeg.addWidget(widget, 1, c)
        for widget in (self.start_ip, self.end_ip, self.subnet):
            widget.editingFinished.connect(self._settings_changed)
        p.layout.addLayout(rangeg)
        g.addWidget(p, 2, 0, 1, 3)

        p = Panel("LATENCY THRESHOLDS")
        self.setup_threshold_panel = p
        row = QHBoxLayout()
        self.green_spin = QSpinBox()
        self.orange_spin = QSpinBox()
        self.poor_spin = QSpinBox()
        for label, spin, color in (("Good (<=)", self.green_spin, GREEN), ("Warning (<=)", self.orange_spin, ORANGE), ("Poor (>)", self.poor_spin, RED)):
            wrap = QWidget()
            vl = QVBoxLayout(wrap)
            vl.setContentsMargins(0, 0, 0, 0)
            hl = QHBoxLayout()
            hl.addWidget(StatusDot(color, 12))
            hl.addWidget(QLabel(label))
            vl.addLayout(hl)
            spin.setRange(0, 9999)
            spin.setSuffix(" ms")
            vl.addWidget(spin)
            row.addWidget(wrap)
        self.green_spin.valueChanged.connect(lambda value: self._threshold_changed("green", value))
        self.orange_spin.valueChanged.connect(lambda value: self._threshold_changed("orange", value))
        self.poor_spin.valueChanged.connect(lambda value: self._threshold_changed("poor", value))
        p.layout.addLayout(row)
        g.addWidget(p, 3, 0)

        p = Panel("CONFIG ACTIONS")
        self.setup_config_panel = p
        row = QHBoxLayout()
        for text, handler, danger in (("▣  Load Config", self._load_config_dialog, False), ("▣  Save Config", self._save_config_dialog, False), ("⇩  Import", self._load_config_dialog, False), ("⇧  Export", self._save_config_dialog, False), ("↻  Reset Defaults", self._reset_defaults, True)):
            b = QPushButton(text)
            b.clicked.connect(handler)
            b.setObjectName("danger" if danger else "")
            row.addWidget(b)
        p.layout.addLayout(row)
        g.addWidget(p, 3, 1, 1, 2)

        g.setColumnStretch(0, 4)
        g.setColumnStretch(1, 3)
        g.setColumnStretch(2, 3)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        return page

    # ---------- Discovery page ----------
    def _build_discovery_page(self) -> QWidget:
        page=QWidget(); outer=QVBoxLayout(page); outer.setContentsMargins(14,0,14,0); outer.setSpacing(8)
        top=QFrame(); top.setObjectName("panel"); tl=QHBoxLayout(top); tl.setContentsMargins(10,8,10,8); tl.setSpacing(10)
        self.disc_start=QLineEdit(); self.disc_end=QLineEdit(); self.disc_subnet=QLineEdit(); self.disc_frequency=QComboBox(); [self.disc_frequency.addItem(f"{x:g} sec",x) for x in DISCOVERY_INTERVAL_OPTIONS]
        for label,w in (("Start IP",self.disc_start),("End IP",self.disc_end),("Subnet",self.disc_subnet),("Scan Frequency",self.disc_frequency)): self._labelled_control(label,w,tl)
        self.add_scan_btn=QPushButton("＋  Add to Scan Mode"); self.add_scan_btn.setObjectName("primary"); self.add_scan_btn.clicked.connect(self._add_discovery_to_scan); tl.addWidget(self.add_scan_btn)
        self.clear_disc_btn=QPushButton("♨  Clear List"); self.clear_disc_btn.clicked.connect(self._clear_discovery); tl.addWidget(self.clear_disc_btn)
        self.discovery_btn=QPushButton(); self.discovery_btn.clicked.connect(self._toggle_discovery); tl.addWidget(self.discovery_btn)
        self.disc_start.editingFinished.connect(self._discovery_settings_changed); self.disc_end.editingFinished.connect(self._discovery_settings_changed); self.disc_subnet.editingFinished.connect(self._discovery_settings_changed); self.disc_frequency.currentIndexChanged.connect(self._discovery_frequency_changed)
        outer.addWidget(top)
        mid=QHBoxLayout(); mid.setSpacing(9)
        self.discovery_table=QTableWidget(0,8); self.discovery_table.setHorizontalHeaderLabels(["", "Device","Host Name","IP Address","Status","Latency","Discovery Source","Last Seen"]); self.discovery_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.discovery_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection); self.discovery_table.setAlternatingRowColors(True); self.discovery_table.verticalHeader().setVisible(False); self.discovery_table.verticalHeader().setDefaultSectionSize(30); self.discovery_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.discovery_table.itemSelectionChanged.connect(self._discovery_selection_changed); mid.addWidget(self.discovery_table,1)
        side=QWidget(); side.setFixedWidth(270); sl=QVBoxLayout(side); sl.setContentsMargins(0,0,0,0); sl.setSpacing(8)
        p=Panel("DISCOVERY SUMMARY"); self.disc_summary={}
        for k,n in (("total","Total Scanned"),("reachable","Reachable"),("unknown","Unknown"),("no_response","No Response")):
            r=QHBoxLayout(); r.addWidget(QLabel(n)); r.addStretch(1); v=QLabel("0"); self.disc_summary[k]=v; r.addWidget(v); p.layout.addLayout(r)
        sl.addWidget(p)
        p=Panel("NETWORK SCOPE"); self.scope_labels={}
        for k,n in (("interface","Interface"),("subnet","Subnet"),("start","Start IP"),("end","End IP"),("broadcast","Broadcast"),("freq","Scan Frequency")):
            r=QHBoxLayout(); r.addWidget(QLabel(n)); r.addStretch(1); v=QLabel("—"); self.scope_labels[k]=v; r.addWidget(v); p.layout.addLayout(r)
        sl.addWidget(p); sl.addStretch(1); mid.addWidget(side); outer.addLayout(mid,3)
        bottom=QHBoxLayout(); bottom.setSpacing(9)
        p=Panel("SELECTED DISCOVERY DEVICE DETAILS"); self.disc_detail={}
        for k,n in (("name","Device"),("hostname","Host Name"),("ip","IP Address"),("status","Status"),("latency","Latency"),("mac","MAC Address"),("source","Discovery Source"),("seen","Last Seen")):
            r=QHBoxLayout(); r.addWidget(QLabel(n)); r.addStretch(1); v=QLabel("—"); self.disc_detail[k]=v; r.addWidget(v); p.layout.addLayout(r)
        p.setFixedWidth(420); bottom.addWidget(p)
        p=Panel("DISCOVERY EVENT LOG"); self.discovery_event_table=self._make_event_table(compact=False); p.layout.addWidget(self.discovery_event_table,1); bottom.addWidget(p,1); outer.addLayout(bottom,2)
        return page

    # ---------- Log page ----------
    def _build_log_page(self) -> QWidget:
        page=QWidget(); l=QVBoxLayout(page); l.setContentsMargins(14,0,14,0); l.setSpacing(8)
        controls=QFrame(); controls.setObjectName("panel"); hl=QHBoxLayout(controls); hl.addWidget(QLabel("Level")); self.log_level=QComboBox(); self.log_level.addItems(["All","INFO","WARN","ERROR"]); self.log_level.currentTextChanged.connect(self._refresh_event_tables); hl.addWidget(self.log_level); hl.addStretch(1); clear=QPushButton("Clear"); clear.clicked.connect(self._clear_log); export=QPushButton("Export"); export.setObjectName("primary"); export.clicked.connect(self._export_log); hl.addWidget(clear); hl.addWidget(export); l.addWidget(controls)
        p=Panel("EVENT LOG"); self.log_table=self._make_event_table(compact=False); p.layout.addWidget(self.log_table,1); l.addWidget(p,1); return page

    def _make_event_table(self, compact: bool) -> QTableWidget:
        t=QTableWidget(0,4); t.setHorizontalHeaderLabels(["Time","Level","Source","Message"]); t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); t.verticalHeader().setVisible(False); t.verticalHeader().setDefaultSectionSize(24); h=t.horizontalHeader(); h.setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents); h.setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents); h.setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents); h.setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch); return t

    # ---------- settings ----------
    def _refresh_interfaces(self) -> None:
        self.interfaces=scan_interfaces()
        if hasattr(self,"interface_combo"):
            self.interface_combo.blockSignals(True); self.interface_combo.clear()
            for interface in self.interfaces: self.interface_combo.addItem(interface.label, interface)
            chosen=0
            # Prefer the exact saved interface name. Default Route and the
            # underlying physical NIC intentionally share an IP, so matching
            # IP first would make a saved en0/en7 choice appear as Default
            # Route after every restart. Fall back to IP only if the named
            # interface is no longer present.
            exact = next((i for i, interface in enumerate(self.interfaces) if interface.name == self.settings.selected_interface_name), None)
            if exact is not None:
                chosen = exact
            elif self.settings.selected_interface_ip:
                fallback = next((i for i, interface in enumerate(self.interfaces) if interface.ip == self.settings.selected_interface_ip), None)
                if fallback is not None:
                    chosen = fallback
            self.interface_combo.setCurrentIndex(chosen); self.interface_combo.blockSignals(False)

    def _load_controls_from_settings(self) -> None:
        self._loading_controls=True
        try:
            self.scan_mode_combo.setCurrentText(self.settings.scan_mode)
            for combo in (self.frequency_combo,self.setup_frequency):
                idx=combo.findData(self.settings.refresh_seconds); combo.setCurrentIndex(max(0,idx))
            idx=self.disc_frequency.findData(self.settings.discovery_interval_seconds); self.disc_frequency.setCurrentIndex(max(0,idx))
            idx=self.trend_combo.findData(self.settings.trend_graph_seconds); self.trend_combo.setCurrentIndex(max(0,idx))
            self.timeout_spin.setValue(self.settings.ping_timeout_ms)
            self.start_ip.setText(self.settings.discovery_start_ip); self.end_ip.setText(self.settings.discovery_end_ip); self.subnet.setText(self.settings.discovery_subnet)
            self.disc_start.setText(self.settings.discovery_start_ip); self.disc_end.setText(self.settings.discovery_end_ip); self.disc_subnet.setText(self.settings.discovery_subnet)
            self.green_spin.setValue(int(round(self.settings.green_max_ms))); self.orange_spin.setValue(int(round(self.settings.orange_max_ms))); self.poor_spin.setValue(int(round(self.settings.orange_max_ms)))
            self._sync_setup_buttons()
        finally:
            self._loading_controls=False

    def _sync_setup_buttons(self):
        self.setup_all.setChecked(self.settings.scan_mode==SCAN_MODE_ALL_AT_ONCE); self.setup_one.setChecked(self.settings.scan_mode==SCAN_MODE_ONE_BY_ONE)
        for b,s in self.setup_trend_buttons: b.setChecked(s==self.settings.trend_graph_seconds)

    def _settings_changed(self) -> None:
        if self._loading_controls: return
        self.settings.scan_mode=self.scan_mode_combo.currentText()
        if self.frequency_combo.currentData() is not None: self.settings.refresh_seconds=float(self.frequency_combo.currentData())
        if self.trend_combo.currentData() is not None: self.settings.trend_graph_seconds=int(self.trend_combo.currentData())
        self.settings.ping_timeout_ms=self.timeout_spin.value() if hasattr(self,"timeout_spin") else self.settings.ping_timeout_ms
        if hasattr(self,"start_ip"):
            self.settings.discovery_start_ip=self.start_ip.text().strip(); self.settings.discovery_end_ip=self.end_ip.text().strip(); self.settings.discovery_subnet=self.subnet.text().strip()
        self.backend.save(); self.backend._scan_wakeup.set(); self._sync_mirrored_controls(); self._refresh_all()

    def _sync_mirrored_controls(self):
        if self._loading_controls: return
        self._loading_controls=True
        try:
            self.scan_mode_combo.setCurrentText(self.settings.scan_mode)
            for combo in (self.frequency_combo,self.setup_frequency):
                idx=combo.findData(self.settings.refresh_seconds)
                if idx>=0: combo.setCurrentIndex(idx)
            idx=self.disc_frequency.findData(self.settings.discovery_interval_seconds)
            if idx>=0: self.disc_frequency.setCurrentIndex(idx)
            idx=self.trend_combo.findData(self.settings.trend_graph_seconds)
            if idx>=0: self.trend_combo.setCurrentIndex(idx)
            self.timeout_spin.setValue(self.settings.ping_timeout_ms)
            self.start_ip.setText(self.settings.discovery_start_ip); self.end_ip.setText(self.settings.discovery_end_ip); self.subnet.setText(self.settings.discovery_subnet)
            self.disc_start.setText(self.settings.discovery_start_ip); self.disc_end.setText(self.settings.discovery_end_ip); self.disc_subnet.setText(self.settings.discovery_subnet)
            self._sync_setup_buttons()
        finally: self._loading_controls=False

    def _setup_frequency_changed(self):
        if self._loading_controls:return
        self.settings.refresh_seconds=float(self.setup_frequency.currentData()); self.backend.save(); self.backend._scan_wakeup.set(); self._sync_mirrored_controls()
    def _discovery_frequency_changed(self):
        if self._loading_controls:return
        self.settings.discovery_interval_seconds=float(self.disc_frequency.currentData()); self.backend.save(); self._sync_mirrored_controls(); self._refresh_scope()
    def _set_scan_mode(self,mode:str):
        self.settings.scan_mode=mode; self.backend.save(); self._sync_mirrored_controls()
    def _set_trend(self,seconds:int):
        self.settings.trend_graph_seconds=seconds; self.backend.save(); self._sync_mirrored_controls(); self._refresh_all()
    def _threshold_changed(self, source: str, value: int):
        if self._loading_controls:
            return
        if source == "green":
            self.settings.green_max_ms = max(0.0, float(value))
            self.settings.orange_max_ms = max(self.settings.green_max_ms, self.settings.orange_max_ms)
        else:
            # Warning <= X and Poor > X share the same boundary. Either of the
            # two displayed controls can therefore adjust that boundary.
            self.settings.orange_max_ms = max(self.settings.green_max_ms, float(value))
        self._loading_controls = True
        try:
            self.green_spin.setValue(int(round(self.settings.green_max_ms)))
            boundary = int(round(self.settings.orange_max_ms))
            self.orange_spin.setValue(boundary)
            self.poor_spin.setValue(boundary)
        finally:
            self._loading_controls = False
        self.backend.save()
        self._refresh_all()
    def _interface_changed(self):
        if self._loading_controls:return
        interface=self.interface_combo.currentData()
        if isinstance(interface,NetworkInterface): self.settings.selected_interface_name=interface.name; self.settings.selected_interface_ip=interface.ip; self._set_discovery_range_from_interface(interface)
        self.backend.save(); self._sync_mirrored_controls(); self._refresh_scope()
    def _set_discovery_range_from_interface(self, interface: NetworkInterface):
        try:
            start, end, subnet = interface_discovery_range(interface)
            self.settings.discovery_start_ip = start
            self.settings.discovery_end_ip = end
            self.settings.discovery_subnet = subnet
        except Exception:
            pass
    def _discovery_settings_changed(self):
        if self._loading_controls:return
        self.settings.discovery_start_ip=self.disc_start.text().strip(); self.settings.discovery_end_ip=self.disc_end.text().strip(); self.settings.discovery_subnet=self.disc_subnet.text().strip(); self.backend.save(); self._sync_mirrored_controls(); self._refresh_scope()

    # ---------- actions ----------
    def _operating_mode_changed(self,text:str):
        if text=="Discovery Mode": self._show_page(2); QTimer.singleShot(0,lambda:self.operating_combo.setCurrentText("Scan Mode"))
    def _toggle_scan(self): self.backend.set_scan_active(not self.backend.scan_active); self._refresh_scan_state()
    def _toggle_discovery(self):
        if self.backend.discovery_active: self.backend.stop_discovery()
        else:
            self._discovery_settings_changed()
            try:self.backend.start_discovery()
            except Exception as exc: QMessageBox.warning(self,"Discovery",str(exc))
        self._refresh_scan_state()
    def _add_device(self):
        values=self._device_dialog("Add Device")
        if values:self.backend.add_device(*values); self._refresh_device_table()
    def _edit_device(self):
        d=self._selected_device()
        if not d: QMessageBox.information(self,"Edit Device","Select a device row to edit."); return
        values=self._device_dialog("Edit Device",d.name,d.ip)
        if values:self.backend.update_device(d.device_id,name=values[0],ip=values[1]); self._refresh_device_table()
    def _device_dialog(self,title:str,name:str="",ip:str=""):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout
        dlg=QDialog(self); dlg.setWindowTitle(title); layout=QFormLayout(dlg); name_e=QLineEdit(name); ip_e=QLineEdit(ip); layout.addRow("Device",name_e); layout.addRow("IP Address",ip_e); buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok); buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject); layout.addRow(buttons)
        if not dlg.exec():return None
        name_v=name_e.text().strip(); ip_v=ip_e.text().strip()
        try:ipaddress.ip_address(ip_v)
        except Exception:QMessageBox.warning(self,title,"Enter a valid IP address."); return None
        if not name_v:return None
        return name_v,ip_v
    def _remove_devices(self):
        ids={self.device_table.item(r,1).data(Qt.ItemDataRole.UserRole) for r in {i.row() for i in self.device_table.selectedIndexes()} if self.device_table.item(r,1)}
        ids.discard(None)
        if not ids:return
        if QMessageBox.question(self,"Remove Device",f"Remove {len(ids)} selected device(s)?")==QMessageBox.StandardButton.Yes:self.backend.remove_devices(ids); self.selected_device_id=None; self._refresh_all()
    def _reorder_device(self,source:int,dest:int): self.backend.reorder_device(source,dest); self._refresh_device_table(); self.device_table.selectRow(dest)
    def _assign_favourite(self,slot:int):
        d=self._selected_device()
        if not d:return
        self.backend.set_favourite(slot,d.device_id); self._refresh_favourites(); self._refresh_selected_device()
    def _clear_all_data(self):
        if QMessageBox.question(self,"Clear All Data","Clear latency history, failure timestamps and event history?")==QMessageBox.StandardButton.Yes:self.backend.clear_history(); self._refresh_all()
    def _add_discovery_to_scan(self):
        ids={self.discovery_table.item(r,1).data(Qt.ItemDataRole.UserRole) for r in {i.row() for i in self.discovery_table.selectedIndexes()} if self.discovery_table.item(r,1)}; ids.discard(None)
        if not ids: QMessageBox.information(self,"Discovery","Select one or more discovered devices."); return
        count=self.backend.add_discovery_to_scan(ids); QMessageBox.information(self,"Discovery",f"Added {count} device(s) to Scan Mode."); self._refresh_all()
    def _clear_log(self):
        self.backend.clear_event_history()
        self._refresh_event_tables()

    def _load_config_dialog(self):
        path,_=QFileDialog.getOpenFileName(self,"Load Config","","JSON files (*.json)")
        if not path:return
        try:
            settings, devices = load_config(path)
            # Import into the normal Application Support config. Loading an
            # external file must not redirect future auto-saves back into that
            # arbitrary source file.
            self.backend.replace_configuration(settings, devices, persist=True)
            self.settings = self.backend.settings
            self._refresh_interfaces()
            self._load_controls_from_settings()
            self._refresh_all()
        except Exception as exc:
            QMessageBox.critical(self, "Load Config", str(exc))
    def _save_config_dialog(self):
        path,_=QFileDialog.getSaveFileName(self,"Save Config","config.json","JSON files (*.json)")
        if path:
            try:save_config(path,self.settings,self.backend.devices)
            except Exception as exc:QMessageBox.critical(self,"Save Config",str(exc))
    def _export_data(self):
        path,_=QFileDialog.getSaveFileName(self,"Export Data","hv_nms_data.csv","CSV files (*.csv)")
        if not path:return
        try:
            with open(path,"w",newline="",encoding="utf-8") as f:
                w=csv.writer(f); w.writerow(["Device","Host Name","IP Address","Latency ms","Last Failed","Timestamp"])
                for d in self.backend.devices:w.writerow([d.name,d.hostname,d.ip,"" if d.latency_ms is None else f"{d.latency_ms:.3f}",d.last_failed_ts or "",time.time()])
        except Exception as exc:QMessageBox.critical(self,"Export Data",str(exc))
    def _export_log(self):
        path,_=QFileDialog.getSaveFileName(self,"Export Log","hv_nms_log.csv","CSV files (*.csv)")
        if not path:return
        try:
            with open(path,"w",newline="",encoding="utf-8") as f:
                w=csv.writer(f); w.writerow(["Time","Level","Source","Message"])
                for e in self.backend.event_snapshot():w.writerow([time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(e.ts)),e.level,e.source,e.message])
        except Exception as exc:QMessageBox.critical(self,"Export Log",str(exc))
    def _reset_defaults(self):
        if QMessageBox.question(self,"Reset Defaults","Reset settings to defaults? Device list will be retained.")!=QMessageBox.StandardButton.Yes:return
        from .models import AppSettings
        fresh = AppSettings()
        fresh.favourite_device_ids = self.settings.normalised_favourites()
        devices = list(self.backend.devices)
        self.backend.replace_configuration(fresh, devices, persist=True)
        self.settings = self.backend.settings
        self._refresh_interfaces()
        self._load_controls_from_settings()
        self._refresh_all()

    # ---------- refresh ----------
    def _tick(self):
        device_updates: set[str] = set()
        device_list_changed = False
        device_related_changed = False
        discovery_changed = False
        scan_state_changed = False
        event_changed = False
        discovery_errors: list[str] = []

        # Drain the queue first, then redraw each expensive table at most once
        # per 200 ms UI tick. Streaming Discovery can produce dozens of events
        # in one tick, so redrawing for every event was a major slowdown.
        while True:
            try:
                item = self.backend.events.get_nowait()
            except queue.Empty:
                break
            if not item:
                continue
            kind = item[0]
            if kind == "device_update":
                device_updates.add(item[1])
                device_related_changed = True
            elif kind in {"device_list", "full_refresh"}:
                device_list_changed = True
                device_related_changed = True
            elif kind == "favourites":
                device_related_changed = True
            elif kind == "scan_state":
                scan_state_changed = True
            elif kind in {"discovery_update", "discovery_list", "discovery_state"}:
                discovery_changed = True
                if kind == "discovery_state":
                    scan_state_changed = True
            elif kind == "discovery_error":
                discovery_errors.append(str(item[1]))
            elif kind in {"event", "events_cleared"}:
                event_changed = True

        if device_list_changed:
            self._refresh_device_table()
        elif device_updates:
            self._refresh_device_rows(device_updates)

        if device_related_changed:
            self._refresh_selected_device()
            self._refresh_favourites()
            self._refresh_summary()
        if discovery_changed:
            self._refresh_discovery_table()
            self._refresh_discovery_selected()
        if scan_state_changed or discovery_changed:
            self._refresh_scan_state()
        if event_changed:
            self._refresh_event_tables()
        for message in discovery_errors:
            QMessageBox.warning(self, "Discovery", message)
        self._refresh_footer()

    def _refresh_all(self):
        self._refresh_device_table(); self._refresh_selected_device(); self._refresh_favourites(); self._refresh_summary(); self._refresh_scan_state(); self._refresh_discovery_table(); self._refresh_discovery_selected(); self._refresh_scope(); self._refresh_event_tables(); self._refresh_footer()

    def _refresh_scan_state(self):
        if self.backend.scan_active:self.scan_btn.setText("■  SCAN ACTIVE"); self.scan_btn.setObjectName("scanActive")
        else:self.scan_btn.setText("■  SCAN STOPPED"); self.scan_btn.setObjectName("scanStopped")
        if self.backend.discovery_active:self.discovery_btn.setText("■  DISCOVERY ACTIVE"); self.discovery_btn.setObjectName("scanActive")
        else:self.discovery_btn.setText("■  DISCOVERY STOPPED"); self.discovery_btn.setObjectName("scanStopped")
        for b in (self.scan_btn,self.discovery_btn):b.style().unpolish(b); b.style().polish(b)
        self._update_status_strip()

    def _refresh_favourites(self):
        favs=self.settings.normalised_favourites()
        for i,tile in enumerate(self.favourite_tiles):tile.set_device(find_device(self.backend.devices,favs[i]),self.settings.trend_graph_seconds,self.settings.green_max_ms,self.settings.orange_max_ms)

    def _populate_device_row(self, row: int, d: DeviceRecord) -> None:
        handle = self.device_table.item(row, 0)
        if handle is None:
            handle = QTableWidgetItem("⋮⋮")
            handle.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.device_table.setItem(row, 0, handle)

        name = self.device_table.item(row, 1)
        if name is None:
            name = QTableWidgetItem()
            self.device_table.setItem(row, 1, name)
        name.setText(d.name)
        name.setData(Qt.ItemDataRole.UserRole, d.device_id)

        hostname = self.device_table.item(row, 2)
        if hostname is None:
            hostname = QTableWidgetItem()
            self.device_table.setItem(row, 2, hostname)
        hostname.setText(d.hostname or "—")

        ip_item = self.device_table.item(row, 3)
        if ip_item is None:
            ip_item = QTableWidgetItem()
            self.device_table.setItem(row, 3, ip_item)
        ip_item.setText(d.ip)

        lat = self.device_table.item(row, 4)
        if lat is None:
            lat = QTableWidgetItem()
            lat.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.device_table.setItem(row, 4, lat)
        lat.setText("Offline" if d.latency_ms is None else f"{d.latency_ms:.2f} ms")
        lat.setForeground(QBrush(QColor(self._latency_color(d.latency_ms))))

        spark = self.device_table.cellWidget(row, 5)
        if not isinstance(spark, Sparkline):
            spark = Sparkline()
            self.device_table.setCellWidget(row, 5, spark)
        spark.set_data(
            d.history_points(self.settings.trend_graph_seconds),
            self.settings.trend_graph_seconds,
            d.last_seen_status,
        )

        failed = self.device_table.item(row, 6)
        if failed is None:
            failed = QTableWidgetItem()
            failed.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.device_table.setItem(row, 6, failed)
        failed.setText(self._ago(d.last_failed_ts, "No failures"))

    def _refresh_device_table(self):
        selected = self.selected_device_id
        self.device_table.blockSignals(True)
        try:
            self.device_table.setRowCount(len(self.backend.devices))
            self._device_row_by_id: dict[str, int] = {}
            for row, d in enumerate(self.backend.devices):
                self._device_row_by_id[d.device_id] = row
                self._populate_device_row(row, d)
                if d.device_id == selected:
                    self.device_table.selectRow(row)
        finally:
            self.device_table.blockSignals(False)

    def _refresh_device_rows(self, device_ids: set[str]) -> None:
        row_map = getattr(self, "_device_row_by_id", {})
        if len(row_map) != len(self.backend.devices):
            self._refresh_device_table()
            return
        self.device_table.blockSignals(True)
        try:
            for device_id in device_ids:
                row = row_map.get(device_id)
                d = find_device(self.backend.devices, device_id)
                if row is None or d is None:
                    self._refresh_device_table()
                    return
                self._populate_device_row(row, d)
        finally:
            self.device_table.blockSignals(False)

    def _device_selection_changed(self):
        rows={i.row() for i in self.device_table.selectedIndexes()}
        if rows:
            row=min(rows); item=self.device_table.item(row,1); self.selected_device_id=item.data(Qt.ItemDataRole.UserRole) if item else None
        self._refresh_selected_device()
    def _selected_device(self):return find_device(self.backend.devices,self.selected_device_id)
    def _refresh_selected_device(self):
        d=self._selected_device(); vals={"name":"—","hostname":"—","ip":"—","latency":"—","failed":"—","status":"—"}
        if d:
            vals={"name":d.name,"hostname":d.hostname or "—","ip":d.ip,"latency":"Offline" if d.latency_ms is None else f"{d.latency_ms:.2f} ms","failed":self._ago(d.last_failed_ts,"No failures"),"status":"●  Online" if d.latency_ms is not None else "●  Offline"}; self.history_legend.setText(f"—  {d.name} ({d.ip})")
        else:self.history_legend.setText("—")
        for k,v in vals.items():self.detail_labels[k].setText(v)
        if d:self.detail_labels["latency"].setStyleSheet(f"color:{self._latency_color(d.latency_ms)};"); self.detail_labels["status"].setStyleSheet(f"color:{GREEN if d.latency_ms is not None else RED};")
        self.history_graph.set_device(d, self.settings.trend_graph_seconds)
        trend_label = next((label for label, seconds in TREND_GRAPH_OPTIONS if seconds == self.settings.trend_graph_seconds), "15 min")
        if getattr(self, "history_panel", None) is not None and self.history_panel.title_label is not None:
            self.history_panel.title_label.setText(f"LATENCY HISTORY ({trend_label.upper()})")
        favs=self.settings.normalised_favourites()
        for i,b in enumerate(self.fav_buttons):b.setObjectName("primary" if d and favs[i]==d.device_id else ""); b.style().unpolish(b); b.style().polish(b)

    def _refresh_summary(self):
        total=len(self.backend.devices); online=sum(d.latency_ms is not None for d in self.backend.devices); warning=sum(d.latency_ms is not None and d.latency_ms>self.settings.orange_max_ms for d in self.backend.devices); offline=total-online
        for k,v,c in (("total",total,TEXT),("online",online,GREEN),("warning",warning,ORANGE),("offline",offline,RED)):self.summary_labels[k].setText(str(v)); self.summary_labels[k].setStyleSheet(f"color:{c}; font-size:16px;")
        self.run_threshold_labels["good"].setText(f"{self.settings.green_max_ms:g}"); self.run_threshold_labels["warning"].setText(f"{self.settings.orange_max_ms:g}"); self.run_threshold_labels["poor"].setText(f"{self.settings.orange_max_ms:g}")

    def _refresh_discovery_table(self):
        selected=self.selected_discovery_id; ds=self.backend.discovery_snapshot(); self.discovery_table.blockSignals(True); self.discovery_table.setRowCount(len(ds))
        for row,d in enumerate(ds):
            self.discovery_table.setItem(row,0,QTableWidgetItem("⋮⋮")); name=QTableWidgetItem(d.name); name.setData(Qt.ItemDataRole.UserRole,d.device_id); self.discovery_table.setItem(row,1,name); self.discovery_table.setItem(row,2,QTableWidgetItem(d.hostname or "—")); self.discovery_table.setItem(row,3,QTableWidgetItem(d.ip))
            status="Reachable" if d.latency_ms is not None else "No Response" if d.last_seen_status=="fail" else "Unknown"; color=GREEN if d.latency_ms is not None else RED if d.last_seen_status=="fail" else ORANGE
            st=QTableWidgetItem(status); st.setForeground(QBrush(QColor(color))); st.setTextAlignment(Qt.AlignmentFlag.AlignCenter); self.discovery_table.setItem(row,4,st)
            lat=QTableWidgetItem("—" if d.latency_ms is None else f"{d.latency_ms:.2f} ms"); lat.setForeground(QBrush(QColor(self._latency_color(d.latency_ms) if d.latency_ms is not None else MUTED))); lat.setTextAlignment(Qt.AlignmentFlag.AlignCenter); self.discovery_table.setItem(row,5,lat); self.discovery_table.setItem(row,6,QTableWidgetItem(d.discovery_source or "—")); seen=QTableWidgetItem(self._ago(d.last_seen_ts,"—")); seen.setTextAlignment(Qt.AlignmentFlag.AlignCenter); self.discovery_table.setItem(row,7,seen)
            if d.device_id==selected:self.discovery_table.selectRow(row)
        self.discovery_table.blockSignals(False); total=len(ds); reachable=sum(d.latency_ms is not None for d in ds); unknown=total-reachable; self.disc_summary["total"].setText(str(self.backend.discovery_total_scanned)); self.disc_summary["reachable"].setText(str(reachable)); self.disc_summary["reachable"].setStyleSheet(f"color:{GREEN}"); unknown=sum(d.latency_ms is None and d.last_seen_status!="fail" for d in ds); no_response=sum(d.last_seen_status=="fail" for d in ds); self.disc_summary["unknown"].setText(str(unknown)); self.disc_summary["unknown"].setStyleSheet(f"color:{ORANGE}"); self.disc_summary["no_response"].setText(str(no_response)); self.disc_summary["no_response"].setStyleSheet(f"color:{RED}")

    def _discovery_selection_changed(self):
        rows={i.row() for i in self.discovery_table.selectedIndexes()}
        if rows:
            item=self.discovery_table.item(min(rows),1); self.selected_discovery_id=item.data(Qt.ItemDataRole.UserRole) if item else None
        self._refresh_discovery_selected()
    def _refresh_discovery_selected(self):
        d=find_device(self.backend.discovery_snapshot(),self.selected_discovery_id); vals={k:"—" for k in self.disc_detail}
        if d: vals={"name":d.name,"hostname":d.hostname or "—","ip":d.ip,"status":"●  Reachable" if d.latency_ms is not None else "●  No Response" if d.last_seen_status=="fail" else "●  Unknown","latency":"—" if d.latency_ms is None else f"{d.latency_ms:.2f} ms","mac":d.mac_address or "—","source":d.discovery_source or "—","seen":self._ago(d.last_seen_ts,"—")}
        for k,v in vals.items():self.disc_detail[k].setText(v)
        if d:self.disc_detail["status"].setStyleSheet(f"color:{GREEN if d.latency_ms is not None else RED if d.last_seen_status=="fail" else ORANGE};")

    def _refresh_scope(self):
        self.scope_labels["interface"].setText(self.settings.selected_interface_name or "Default Route"); self.scope_labels["start"].setText(self.settings.discovery_start_ip); self.scope_labels["end"].setText(self.settings.discovery_end_ip); self.scope_labels["freq"].setText(f"{self.settings.discovery_interval_seconds:g} sec")
        try:
            net=ipaddress.ip_network(f"{self.settings.discovery_start_ip}/{self.settings.discovery_subnet}",strict=False); self.scope_labels["subnet"].setText(str(net)); self.scope_labels["broadcast"].setText(str(net.broadcast_address))
        except Exception:self.scope_labels["subnet"].setText("Invalid"); self.scope_labels["broadcast"].setText("—")

    def _append_event(self, record: EventRecord):
        # Kept as a small compatibility hook; _tick batches actual redraws.
        self._refresh_event_tables()

    def _fill_event_table(self, table: QTableWidget | None, records: list[EventRecord]) -> None:
        if table is None:
            return
        records = records[-500:]
        table.setRowCount(len(records))
        for row, event in enumerate(reversed(records)):
            values = [
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(event.ts)),
                event.level,
                event.source,
                event.message,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 1:
                    item.setForeground(QBrush(QColor(GREEN if event.level == "INFO" else ORANGE if event.level == "WARN" else RED)))
                table.setItem(row, col, item)

    def _refresh_event_tables(self):
        records = self.backend.event_snapshot()
        # Run shows the complete operational log. Discovery's dedicated log is
        # limited to Discovery events. Only the Log page obeys its level filter.
        self._fill_event_table(getattr(self, "run_event_table", None), records)
        discovery_records = [event for event in records if event.source == "DISCOVERY"]
        self._fill_event_table(getattr(self, "discovery_event_table", None), discovery_records)
        level = self.log_level.currentText() if hasattr(self, "log_level") else "All"
        log_records = [event for event in records if level == "All" or event.level == level]
        self._fill_event_table(getattr(self, "log_table", None), log_records)

    def _clear_discovery(self):self.backend.clear_discovery();self.selected_discovery_id=None;self._refresh_discovery_table();self._refresh_discovery_selected()
    def _refresh_footer(self):
        self.time_label.setText("NMS Time:   "+time.strftime("%Y-%m-%d  %H:%M:%S")); elapsed=max(0,int(time.time()-self._started_at)); self.uptime_label.setText(f"Uptime:     {elapsed//3600:02d}:{(elapsed%3600)//60:02d}:{elapsed%60:02d}")
    def _latency_color(self,latency:float|None)->str:
        if latency is None:return RED
        if latency<=self.settings.green_max_ms:return GREEN
        if latency<=self.settings.orange_max_ms:return ORANGE
        return RED
    @staticmethod
    def _ago(ts:float|None,never:str="—")->str:
        if ts is None:return never
        sec=max(0,int(time.time()-ts))
        if sec<60:return f"{sec} sec ago"
        mins=sec//60
        if mins<60:return f"{mins} min ago"
        return f"{mins//60}h {mins%60}m ago"

    def closeEvent(self,event):
        try:self.backend.save();self.backend.shutdown()
        finally:event.accept()
