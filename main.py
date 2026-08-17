from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from token_running.realtime_ui import RealtimeWindow


def main() -> int:
    app = QApplication(sys.argv)
    win = RealtimeWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
