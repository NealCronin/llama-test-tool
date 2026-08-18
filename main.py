from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app.services.flag_catalog import FlagCatalog
from app.settings import AppSettings


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("Llama Test Tool")
    app.setApplicationName("Llama Test Tool")

    settings = AppSettings.load()
    catalog = FlagCatalog.load_bundled(Path(__file__).parent / "data" / "llama_server_flags.json")
    window = MainWindow(settings=settings, catalog=catalog)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
