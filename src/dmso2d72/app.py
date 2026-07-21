"""Application entry point."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from . import settings
from .gui.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(prog="dmso2d72")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="start the GUI and exit immediately (for automated testing)",
    )
    args = parser.parse_args()

    # Must precede any QSettings use, which is why it happens before MainWindow.
    QCoreApplication.setOrganizationName(settings.ORG_NAME)
    QCoreApplication.setApplicationName(settings.APP_NAME)

    app = QApplication(sys.argv)
    # Fusion is palette-driven on every desktop, so the theme switch in
    # gui/theme.py actually takes effect instead of being overridden by a
    # native style.
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    if args.smoke_test:
        QTimer.singleShot(500, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
