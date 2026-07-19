"""Application entry point."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .gui.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(prog="dmso2d72")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="start the GUI and exit immediately (for automated testing)",
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    if args.smoke_test:
        QTimer.singleShot(500, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
