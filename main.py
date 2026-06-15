from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from catx.core.application import create_main_window


def main() -> int:
    app = QApplication(sys.argv)
    style_list = ["Fusion", "windows11", "Windows"]
    app.setStyle(style_list[1])
    window = create_main_window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
