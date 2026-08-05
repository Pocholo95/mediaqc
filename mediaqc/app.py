"""Entry point. ``python -m mediaqc`` corre esto sin empaquetar (spec sección 7)."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from mediaqc.core import config, tools
from mediaqc.core.db import repo
from mediaqc.ui.main_window import MainWindow
from mediaqc.ui.settings_dialog import FirstRunWizard
from mediaqc.ui.theme import apply_light_palette


def _setup_logging() -> None:
    config.ensure_data_dirs()
    log_path = config.get_logs_dir() / "mediaqc.log"
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler())


def main() -> int:
    app = QApplication(sys.argv)
    apply_light_palette(app)
    _setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("MediaQC arrancando")

    cfg = config.load_config()
    first_run = cfg is None

    if cfg is None:
        wizard = FirstRunWizard()
        if wizard.exec() != QDialog.Accepted:
            logger.info("Asistente de primer arranque cancelado, saliendo")
            return 0
        cfg = wizard.build_config()
        try:
            config.save_config(cfg)
        except config.ConfigError as exc:
            QMessageBox.critical(None, "Configuración inválida", str(exc))
            return 1

    engine = repo.create_db_engine(config.get_db_path())
    session_factory = repo.make_session_factory(engine)

    tool_paths = tools.resolve_all(cfg.ffmpeg_path, cfg.mkvmerge_path)

    window = MainWindow(cfg, session_factory, tool_paths)
    window.show()

    if first_run and cfg.media_paths:
        reply = QMessageBox.question(
            window,
            "Escanear ahora",
            "¿Escanear la librería configurada ahora?",
        )
        if reply == QMessageBox.Yes:
            window._start_scan()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
