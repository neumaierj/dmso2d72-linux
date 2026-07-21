"""Multimeter reading history: a rolling plot plus optional CSV logging."""

from __future__ import annotations

import csv
from collections import deque
from datetime import datetime, timezone
from time import monotonic

import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..protocol import DmmReading

# Label -> seconds of history to keep. None keeps everything in memory.
WINDOWS = {"1 min": 60.0, "5 min": 300.0, "1 hour": 3600.0, "All": None}


class DmmHistoryView(QWidget):
    """Plots recent readings and can append them to a CSV as they arrive."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: deque[tuple[float, float]] = deque()
        self._t0 = monotonic()
        self._unit = ""
        self._dirty = False
        self._log_file = None
        self._log_writer = None

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "seconds")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.curve = self.plot.plot()

        self.window_combo = QComboBox()
        self.window_combo.addItems(WINDOWS)
        self.window_combo.setCurrentText("5 min")

        self.log_button = QPushButton("Log to CSV…")
        self.log_button.setCheckable(True)
        self.log_button.toggled.connect(self._toggle_log)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear)
        self.log_label = QLabel("")
        self.log_label.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Keep"))
        controls.addWidget(self.window_combo)
        controls.addStretch()
        controls.addWidget(self.clear_button)
        controls.addWidget(self.log_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.plot, stretch=1)
        layout.addLayout(controls)
        layout.addWidget(self.log_label)

        # Readings arrive at 5 Hz today; redraw on a timer anyway so a faster
        # poll interval later cannot turn into a repaint per sample.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._redraw)
        self._timer.start(200)

    # ------------------------------------------------------------------- data

    def add(self, reading: DmmReading) -> None:
        if reading.value is None:
            return
        if reading.unit != self._unit:
            self._unit = reading.unit
            self.plot.setLabel("left", reading.unit or "value")
        self._points.append((monotonic() - self._t0, reading.value))
        self._trim()
        self._dirty = True
        if self._log_writer is not None:
            self._log_writer.writerow(
                [
                    datetime.now(timezone.utc).isoformat(),
                    f"{monotonic() - self._t0:.3f}",
                    reading.value,
                    reading.unit,
                    reading.mode,
                    int(reading.overload),
                ]
            )
            self._log_file.flush()  # a crash should still leave a usable log

    def _trim(self) -> None:
        span = WINDOWS[self.window_combo.currentText()]
        if span is None or not self._points:
            return
        cutoff = self._points[-1][0] - span
        while self._points and self._points[0][0] < cutoff:
            self._points.popleft()

    def clear(self) -> None:
        self._points.clear()
        self._dirty = True

    def has_data(self) -> bool:
        return bool(self._points)

    def _redraw(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        if self._points:
            xs = [t for t, _ in self._points]
            ys = [v for _, v in self._points]
            self.curve.setData(xs, ys)
        else:
            self.curve.clear()

    def apply_theme(self, theme) -> None:
        self.plot.setBackground(theme.plot_background)
        for edge in ("left", "bottom"):
            axis = self.plot.getAxis(edge)
            axis.setPen(theme.axis)
            axis.setTextPen(theme.axis)
        self.plot.showGrid(x=True, y=True, alpha=theme.grid_alpha)
        self.curve.setPen(pg.mkPen(theme.dmm_history, width=2))

    # ----------------------------------------------------------------- logging

    def _toggle_log(self, on: bool) -> None:
        if on:
            path, _ = QFileDialog.getSaveFileName(
                self, "Log multimeter readings", "dmm_log.csv", "CSV (*.csv)"
            )
            if not path:
                self.log_button.setChecked(False)
                return
            self._log_file = open(path, "w", newline="")
            self._log_writer = csv.writer(self._log_file)
            self._log_writer.writerow(
                ["timestamp_utc", "seconds", "value", "unit", "mode", "overload"]
            )
            self.log_button.setText("Stop logging")
            self.log_label.setText(f"Logging to {path}")
        else:
            self.stop_logging()

    def stop_logging(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
        self._log_file = None
        self._log_writer = None
        self.log_button.setText("Log to CSV…")
        self.log_label.setText("")
        if self.log_button.isChecked():
            self.log_button.setChecked(False)

    def export(self, parent) -> None:
        """Write the in-memory history to a file the user picks."""
        if not self._points:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(parent, "Export history", "No readings recorded yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            parent, "Export multimeter history", "dmm_history.csv", "CSV (*.csv)"
        )
        if not path:
            return
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["seconds", f"value_{self._unit}".rstrip("_")])
            for t, v in self._points:
                writer.writerow([f"{t:.3f}", v])
