from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .main_window import MainWindow
from .paths import resources_folder


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("AamerBarood")
    icon = resources_folder() / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    window = MainWindow()
    window.show()
    # `pdftranslator file.pdf` opens that file (also how "Open With" passes files on Windows and Linux).
    for arg in sys.argv[1:]:
        if arg.lower().endswith(".pdf") and Path(arg).exists():
            window.open(Path(arg))
            break
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
