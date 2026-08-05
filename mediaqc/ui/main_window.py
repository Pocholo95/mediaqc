"""Ventana principal — fase 1: árbol serie/temporada + tabla de episodios.

Las vistas de revisión (mpv), problemas y el semáforo de estados llegan en
fases posteriores (spec sección 6); esta ventana ya deja la estructura de
tres zonas (árbol / contenido / barra de estado) sobre la que se construyen.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt, QThreadPool
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mediaqc.core import tools
from mediaqc.core.config import Config, get_logs_dir
from mediaqc.core.db import repo
from mediaqc.ui.settings_dialog import SettingsDialog
from mediaqc.ui.workers import ScanWorker

TABLE_HEADERS = ["Serie", "Temporada", "#", "Archivo", "Audio", "Estado", "Duración", "Ausente"]


class MainWindow(QMainWindow):
    def __init__(self, config: Config, session_factory, tool_paths: tools.ToolPaths) -> None:
        super().__init__()
        self.config = config
        self.session_factory = session_factory
        self.tool_paths = tool_paths
        self.thread_pool = QThreadPool.globalInstance()
        self._current_worker: ScanWorker | None = None
        self._last_unparseable: list[str] = []

        self.setWindowTitle("MediaQC")
        self.resize(1200, 800)

        self._build_ui()
        self._build_menu()
        self._warn_missing_tools()
        self._reload_tree()
        self._check_pending_jobs()

    # --- construcción de UI ------------------------------------------------

    def _build_ui(self) -> None:
        splitter = QSplitter()

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Biblioteca"])
        self.tree.itemSelectionChanged.connect(self._refresh_table)
        splitter.addWidget(self.tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)

        filter_bar = QHBoxLayout()
        self.filter_checkbox = QCheckBox(self._filter_label())
        self.filter_checkbox.stateChanged.connect(self._refresh_table)
        filter_bar.addWidget(self.filter_checkbox)
        filter_bar.addStretch()
        right_layout.addLayout(filter_bar)

        self.table = QTableWidget(0, len(TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        right_layout.addWidget(self.table)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        status = QStatusBar()
        self.status_label = QLabel("Listo.")
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(240)
        self.progress_bar.setVisible(False)
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel_scan)
        status.addWidget(self.status_label, 1)
        status.addWidget(self.progress_bar)
        status.addWidget(self.cancel_button)
        self.setStatusBar(status)

    def _filter_label(self) -> str:
        langs = ", ".join(self.config.expected_languages) or "—"
        return f"Solo episodios sin audio en idioma esperado ({langs})"

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&Archivo")

        self.scan_action = menu.addAction("Escanear biblioteca")
        self.scan_action.triggered.connect(self._start_scan)

        unparseable_action = menu.addAction("Ver archivos no reconocidos...")
        unparseable_action.triggered.connect(self._show_unparseable)

        menu.addSeparator()
        settings_action = menu.addAction("Preferencias...")
        settings_action.triggered.connect(self._open_settings)

        logs_action = menu.addAction("Abrir carpeta de logs")
        logs_action.triggered.connect(self._open_logs_dir)

        menu.addSeparator()
        exit_action = menu.addAction("Salir")
        exit_action.triggered.connect(self.close)

    def _open_logs_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_logs_dir())))

    def _warn_missing_tools(self) -> None:
        missing = self.tool_paths.missing()
        if missing:
            QMessageBox.warning(
                self,
                "Faltan binarios externos",
                "No se encontraron: " + ", ".join(missing) + ".\n\n"
                "Instalalos y agregalos al PATH, o configurá su ruta en Preferencias. "
                "Sin ffmpeg/ffprobe el escaneo no puede analizar los archivos.",
            )

    # --- árbol / tabla -------------------------------------------------

    def _reload_tree(self) -> None:
        self.tree.clear()
        with self.session_factory() as session:
            series_list = repo.list_series_tree(session)
            for series in series_list:
                label = series.title or series.folder_name
                if series.year:
                    label += f" ({series.year})"
                series_item = QTreeWidgetItem([label])
                series_item.setData(0, Qt.UserRole, ("series", series.id))
                for season in series.seasons:
                    season_item = QTreeWidgetItem([f"Temporada {season.number}"])
                    season_item.setData(0, Qt.UserRole, ("season", season.id))
                    series_item.addChild(season_item)
                self.tree.addTopLevelItem(series_item)
        self._refresh_table()

    def _selected_scope(self) -> tuple[str, int] | None:
        items = self.tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.UserRole)

    def _refresh_table(self) -> None:
        scope = self._selected_scope()
        expected = {lang.lower() for lang in self.config.expected_languages}
        only_missing = self.filter_checkbox.isChecked()

        with self.session_factory() as session:
            if scope is None:
                episodes = repo.list_all_episodes(session)
            elif scope[0] == "series":
                episodes = [e for e in repo.list_all_episodes(session) if e.season.series_id == scope[1]]
            else:
                episodes = repo.list_episodes_for_season(session, scope[1])

            if only_missing:
                episodes = [e for e in episodes if not repo.episode_has_audio_language(e, expected)]

            self._populate_table(episodes)

    def _populate_table(self, episodes) -> None:
        self.table.setRowCount(len(episodes))
        for row, ep in enumerate(episodes):
            series_title = "?"
            season_number = "?"
            if ep.season is not None:
                season_number = str(ep.season.number)
                if ep.season.series is not None:
                    series_title = ep.season.series.title or ep.season.series.folder_name

            audio_langs = ", ".join(sorted({(t.language or "und") for t in ep.tracks if t.type == "audio"}))
            duration = f"{ep.duration_s / 60:.1f} min" if ep.duration_s else "—"

            values = [
                series_title,
                season_number,
                str(ep.number),
                Path(ep.path).name,
                audio_langs or "—",
                ep.status,
                duration,
                "sí" if ep.missing else "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if ep.missing:
                    item.setForeground(QColor("gray"))
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()

    # --- escaneo -------------------------------------------------------

    def _start_scan(self) -> None:
        if not self.config.media_paths:
            QMessageBox.warning(
                self,
                "Sin carpetas configuradas",
                "Configurá al menos una carpeta de librería en Preferencias antes de escanear.",
            )
            return
        if self._current_worker is not None:
            return

        worker = ScanWorker(
            self.session_factory,
            self.config.media_paths,
            self.tool_paths.ffprobe,
            self.tool_paths.mkvmerge,
        )
        worker.signals.progress.connect(self._on_scan_progress)
        worker.signals.finished.connect(self._on_scan_finished)
        worker.signals.error.connect(self._on_scan_error)
        self._current_worker = worker

        self.scan_action.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.cancel_button.setVisible(True)
        self.status_label.setText("Escaneando...")

        self.thread_pool.start(worker)

    def _cancel_scan(self) -> None:
        if self._current_worker is not None:
            self._current_worker.cancel()
            self.status_label.setText("Cancelando...")

    def _on_scan_progress(self, message: str, current: int, total: int) -> None:
        self.status_label.setText(message)
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
        else:
            self.progress_bar.setRange(0, 0)

    def _on_scan_finished(self, summary: dict) -> None:
        self._current_worker = None
        self.scan_action.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self._last_unparseable = summary.get("unparseable", [])

        msg = (
            f"Listo: {summary['series_count']} series, {summary['changed_count']} episodios nuevos/cambiados, "
            f"{summary['missing_count']} marcados ausentes, {summary['probe_errors']} errores de análisis."
        )
        if summary.get("unreachable_media_paths"):
            msg += f" {len(summary['unreachable_media_paths'])} carpeta(s) de librería no accesibles."
        if self._last_unparseable:
            msg += f" {len(self._last_unparseable)} archivo(s) no reconocidos."
        if summary.get("cancelled"):
            msg = "Escaneo cancelado. " + msg

        self.status_label.setText(msg)
        self._reload_tree()

    def _on_scan_error(self, message: str) -> None:
        self._current_worker = None
        self.scan_action.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.status_label.setText("Error en el escaneo.")
        QMessageBox.critical(self, "Error de escaneo", message)

    def _show_unparseable(self) -> None:
        if not self._last_unparseable:
            QMessageBox.information(
                self,
                "Archivos no reconocidos",
                "No hay archivos sin reconocer del último escaneo (o todavía no escaneaste).",
            )
            return
        QMessageBox.information(self, "Archivos no reconocidos", "\n".join(self._last_unparseable))

    # --- preferencias / jobs pendientes --------------------------------

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.Accepted:
            self.config = dialog.result_config()
            self.filter_checkbox.setText(self._filter_label())
            self.tool_paths = tools.resolve_all(self.config.ffmpeg_path, self.config.mkvmerge_path)
            self._warn_missing_tools()

    def _check_pending_jobs(self) -> None:
        with self.session_factory() as session:
            pending = repo.list_pending_jobs(session)
        if pending:
            self.status_label.setText(
                f"{len(pending)} job(s) quedaron sin terminar de la última sesión. "
                "Volvé a escanear para retomarlos."
            )

    def closeEvent(self, event) -> None:  # noqa: N802 (override de Qt)
        if self._current_worker is not None:
            self._current_worker.cancel()
        super().closeEvent(event)
