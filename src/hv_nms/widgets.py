from __future__ import annotations

import math
import time
from typing import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .constants import BORDER, GREEN, MUTED, ORANGE, PANEL_BG, RED, TEXT
from .models import DeviceRecord


class Sparkline(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._points: list[tuple[float, float | None]] = []
        self._window_seconds = 15 * 60
        self._status = "unknown"
        self.setMinimumHeight(18)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, points: Iterable[tuple[float, float | None]], window_seconds: int, status: str) -> None:
        self._points = list(points)
        self._window_seconds = max(1, int(window_seconds))
        self._status = status
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawRect(rect)
        if rect.width() < 4 or rect.height() < 4:
            return
        valid = [(ts, v) for ts, v in self._points if isinstance(v, (int, float))]
        if not valid:
            p.setPen(QPen(QColor(RED), 1.5))
            y = rect.center().y()
            p.drawLine(rect.left() + 2, y, rect.right() - 2, y)
            return
        vals = [float(v) for _, v in valid]
        y_min, y_max = min(vals), max(vals)
        if math.isclose(y_min, y_max):
            y_min = max(0.0, y_min - 1.0)
            y_max += 1.0
        now = time.time()
        cutoff = now - self._window_seconds
        pen_color = GREEN if self._status == "ok" else ORANGE if self._status == "unknown" else RED
        p.setPen(QPen(QColor(pen_color), 1.5))
        last = None
        for ts, val in self._points:
            if val is None:
                last = None
                continue
            x_ratio = min(1.0, max(0.0, (ts - cutoff) / self._window_seconds))
            y_ratio = (float(val) - y_min) / max(1e-9, y_max - y_min)
            x = rect.left() + 2 + x_ratio * max(1, rect.width() - 4)
            y = rect.bottom() - 2 - y_ratio * max(1, rect.height() - 4)
            if last is not None:
                p.drawLine(int(last[0]), int(last[1]), int(x), int(y))
            last = (x, y)


class HistoryGraph(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.device: DeviceRecord | None = None
        self.window_seconds = 15 * 60
        self.setMinimumHeight(165)

    def set_device(self, device: DeviceRecord | None, window_seconds: int) -> None:
        self.device = device
        self.window_seconds = max(1, int(window_seconds))
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect().adjusted(48, 16, -14, -28)
        p.setPen(QPen(QColor("#283236"), 1))
        for i in range(5):
            y = r.top() + int(i * r.height() / 4)
            p.drawLine(r.left(), y, r.right(), y)
        for i in range(7):
            x = r.left() + int(i * r.width() / 6)
            p.drawLine(x, r.top(), x, r.bottom())
        p.setPen(QColor(MUTED))
        p.drawText(4, r.top() + 4, "100 ms")
        p.drawText(10, r.center().y() + 4, "50 ms")
        p.drawText(18, r.bottom() + 4, "0 ms")
        if not self.device:
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Select a device")
            return
        points = self.device.history_points(self.window_seconds)
        valid = [(ts, v) for ts, v in points if isinstance(v, (int, float))]
        if not valid:
            p.setPen(QPen(QColor(RED), 2))
            p.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
            return
        max_y = max(100.0, max(float(v) for _, v in valid) * 1.15)
        cutoff = time.time() - self.window_seconds
        p.setPen(QPen(QColor(GREEN), 2))
        last = None
        for ts, val in points:
            if val is None:
                last = None
                continue
            x = r.left() + ((ts - cutoff) / self.window_seconds) * r.width()
            y = r.bottom() - (min(float(val), max_y) / max_y) * r.height()
            if last:
                p.drawLine(int(last[0]), int(last[1]), int(x), int(y))
            last = (x, y)


class StatusDot(QWidget):
    def __init__(self, color: str = RED, size: int = 12, parent: QWidget | None = None):
        super().__init__(parent)
        self.color = color
        self._size = size
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        self.color = color
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(1, 1, self._size - 2, self._size - 2)


class FavouriteTile(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("favouriteTile")
        self.setFixedHeight(58)
        self.setMinimumWidth(290)
        main = QHBoxLayout(self)
        main.setContentsMargins(14, 7, 10, 7)
        main.setSpacing(10)
        self.dot = StatusDot(size=13)
        main.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignTop)
        text_wrap = QVBoxLayout()
        text_wrap.setContentsMargins(0, 0, 0, 0)
        text_wrap.setSpacing(1)
        self.name_label = QLabel("—")
        self.name_label.setObjectName("favName")
        self.detail_label = QLabel("Unassigned")
        self.detail_label.setObjectName("favDetail")
        text_wrap.addWidget(self.name_label)
        text_wrap.addWidget(self.detail_label)
        main.addLayout(text_wrap, 1)
        self.spark = Sparkline()
        self.spark.setFixedWidth(130)
        main.addWidget(self.spark, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_device(self, device: DeviceRecord | None, trend_seconds: int, green_max: float, orange_max: float) -> None:
        if device is None:
            self.name_label.setText("—")
            self.detail_label.setText("Unassigned")
            self.dot.set_color(RED)
            self.spark.set_data([], trend_seconds, "fail")
            return
        self.name_label.setText(device.name or device.ip)
        latency = device.latency_ms
        if latency is None:
            latency_text = "Offline"
            color = RED
        elif latency <= green_max:
            latency_text = f"{latency:.2f} ms"
            color = GREEN
        elif latency <= orange_max:
            latency_text = f"{latency:.2f} ms"
            color = ORANGE
        else:
            latency_text = f"{latency:.2f} ms"
            color = RED
        self.detail_label.setText(f"{device.ip}     {latency_text}")
        self.dot.set_color(color)
        self.spark.set_data(device.history_points(trend_seconds), trend_seconds, device.last_seen_status)


class DeviceTableFrame(QFrame):
    row_reordered = Signal(int, int)


class SectionTitle(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("sectionTitle")
