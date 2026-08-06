"""Ventana principal: árbol serie/temporada a la izquierda, y a la derecha
las tres vistas de la spec sección 6 (Biblioteca/semáforo, Revisión,
Problemas) en pestañas, con una barra de estado abajo para el progreso de
los jobs.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, QThreadPool
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mediaqc.core import suggestions as suggestions_module
from mediaqc.core import tmdb, tools
from mediaqc.core.config import (
    Config,
    ConfigError,
    get_audio_cache_dir,
    get_logs_dir,
    get_tmdb_cache_dir,
    save_config,
)
from mediaqc.core.db import repo
from mediaqc.core.db.models import Series
from mediaqc.ui.library_view import LibraryView
from mediaqc.ui.problems_view import ProblemsView
from mediaqc.ui.review_view import ReviewView
from mediaqc.ui.settings_dialog import SettingsDialog
from mediaqc.ui.theme import apply_theme
from mediaqc.ui.tmdb_dialog import TmdbMatchDialog
from mediaqc.ui.workers import (
    AnalyzeWorker,
    CandidatesScanWorker,
    ScanWorker,
    TmdbApplyWorker,
    TmdbSearchWorker,
    TmdbSyncWorker,
)


class MainWindow(QMainWindow):
    def __init__(self, config: Config, session_factory, tool_paths: tools.ToolPaths) -> None:
        super().__init__()
        self.config = config
        self.session_factory = session_factory
        self.tool_paths = tool_paths
        self.thread_pool = QThreadPool.globalInstance()
        self._current_worker: ScanWorker | None = None
        self._current_tmdb_worker: TmdbSyncWorker | None = None
        self._current_analyze_worker: AnalyzeWorker | None = None
        self._current_candidates_worker: CandidatesScanWorker | None = None
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

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Biblioteca"])
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        left_layout.addWidget(self.tree, 1)

        self.filter_checkbox = QCheckBox(self._filter_label())
        self.filter_checkbox.stateChanged.connect(self._on_language_filter_changed)
        left_layout.addWidget(self.filter_checkbox)

        splitter.addWidget(left)

        self.view_tabs = QTabWidget()
        self.library_view = LibraryView(self.session_factory)
        self.library_view.episode_activated.connect(self._open_review_from_library)
        self.view_tabs.addTab(self.library_view, "Biblioteca")

        self.review_view = ReviewView(self.session_factory, self.config, self.tool_paths)
        self.review_view.verdict_recorded.connect(self._on_verdict_recorded)
        self.review_view.queue_exhausted.connect(self._on_review_queue_exhausted)
        self.view_tabs.addTab(self.review_view, "Revisión")

        self.problems_view = ProblemsView(self.session_factory)
        self.problems_view.episode_activated.connect(self._open_review_from_problems)
        self.view_tabs.addTab(self.problems_view, "Problemas")

        self.view_tabs.currentChanged.connect(self._on_view_tab_changed)

        splitter.addWidget(self.view_tabs)
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
        self.cancel_button.clicked.connect(self._cancel_current_worker)
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

        self.force_reprobe_action = menu.addAction("Forzar re-análisis técnico de toda la biblioteca")
        self.force_reprobe_action.triggered.connect(self._start_force_reprobe)

        self.candidates_scan_action = menu.addAction("Escanear candidatos externos")
        self.candidates_scan_action.triggered.connect(self._start_candidates_scan)

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

        view_menu = self.menuBar().addMenu("&Ver")
        self.dark_mode_action = view_menu.addAction("Modo oscuro")
        self.dark_mode_action.setCheckable(True)
        self.dark_mode_action.setChecked(self.config.dark_mode)
        self.dark_mode_action.toggled.connect(self._toggle_dark_mode)

        tmdb_menu = self.menuBar().addMenu("&TMDB")
        self.tmdb_sync_action = tmdb_menu.addAction("Sincronizar series nuevas")
        self.tmdb_sync_action.triggered.connect(self._start_tmdb_sync)

        review_menu = self.menuBar().addMenu("&Revisión")
        start_review_action = review_menu.addAction("Empezar a revisar (selección actual)")
        start_review_action.setShortcut("Ctrl+R")
        start_review_action.triggered.connect(self._start_review_from_selection)

        analysis_menu = self.menuBar().addMenu("&Análisis")
        self.analyze_action = analysis_menu.addAction("Analizar sincronización (episodios pendientes)")
        self.analyze_action.triggered.connect(self._start_analyze)

    def _toggle_dark_mode(self, checked: bool) -> None:
        self.config.dark_mode = checked
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, dark=checked)
        try:
            save_config(self.config)
        except ConfigError:
            pass  # output_dir/media_paths no cambiaron acá, no debería pasar

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

    # --- árbol / navegación entre vistas ---------------------------------

    def _reload_tree(self) -> None:
        self.tree.clear()
        with self.session_factory() as session:
            series_list = repo.list_series_tree(session)
            for series in series_list:
                label = series.title or series.folder_name
                if series.year:
                    label += f" ({series.year})"
                if series.tmdb_status in (None, "unmatched"):
                    label += " — sin TMDB"
                elif series.tmdb_status == "skipped":
                    label += " — TMDB omitido"
                series_item = QTreeWidgetItem([label])
                series_item.setData(0, Qt.UserRole, ("series", series.id))
                for season in series.seasons:
                    season_label = f"Temporada {season.number}"
                    on_disk = sum(1 for e in season.episodes if not e.missing)
                    if season.tmdb_episode_count:
                        season_label += f" ({on_disk}/{season.tmdb_episode_count})"
                        if on_disk < season.tmdb_episode_count:
                            season_label += " — incompleta"
                    season_item = QTreeWidgetItem([season_label])
                    season_item.setData(0, Qt.UserRole, ("season", season.id))
                    series_item.addChild(season_item)
                self.tree.addTopLevelItem(series_item)
        self._on_tree_selection_changed()

    def _on_tree_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        scope = item.data(0, Qt.UserRole)
        if scope is None:
            return

        menu = QMenu(self)
        if scope[0] == "series":
            action = menu.addAction("Resolver TMDB...")
            action.triggered.connect(lambda: self._resolve_tmdb_for_series(scope[1]))
        elif scope[0] == "season":
            action = menu.addAction("Exportar script de la temporada...")
            action.triggered.connect(lambda: self._export_season_script(scope[1]))

        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _selected_scope(self) -> tuple[str, int] | None:
        items = self.tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.UserRole)

    def _on_tree_selection_changed(self) -> None:
        self.library_view.set_scope(self._selected_scope())

    def _on_language_filter_changed(self) -> None:
        if self.filter_checkbox.isChecked():
            expected = {lang.lower() for lang in self.config.expected_languages}
            self.library_view.set_required_languages(expected)
        else:
            self.library_view.set_required_languages(None)

    def _on_view_tab_changed(self, index: int) -> None:
        widget = self.view_tabs.widget(index)
        if widget is self.problems_view:
            self.problems_view.refresh()

    def _open_review_from_library(self, episode_id: int) -> None:
        self.review_view.load_episode(episode_id, queue=self.library_view.current_episode_ids())
        self.view_tabs.setCurrentWidget(self.review_view)

    def _open_review_from_problems(self, episode_id: int) -> None:
        self.review_view.load_episode(episode_id, queue=self.problems_view.current_episode_ids())
        self.view_tabs.setCurrentWidget(self.review_view)

    def _on_verdict_recorded(self, episode_id: int) -> None:
        self.library_view.refresh()
        self.problems_view.refresh()

    def _on_review_queue_exhausted(self) -> None:
        QMessageBox.information(self, "Revisión", "No hay más episodios en esta cola de revisión.")

    def _start_review_from_selection(self) -> None:
        """Atajo: abre el primer episodio pendiente de revisión de lo que
        esté seleccionado en el árbol (o de toda la librería si no hay
        selección), con el resto de la cola detrás para el atajo Espacio."""
        scope = self._selected_scope()
        with self.session_factory() as session:
            if scope is None:
                episodes = repo.list_all_episodes(session)
            elif scope[0] == "series":
                episodes = [e for e in repo.list_all_episodes(session) if e.season.series_id == scope[1]]
            else:
                episodes = repo.list_episodes_for_season(session, scope[1])

            pending = [
                e for e in episodes if e.status in repo.REVIEW_ELIGIBLE_STATUSES and not e.missing
            ]
            pending.sort(key=lambda e: ((e.season.number if e.season else 0), e.number))
            queue = [e.id for e in pending]

        if not queue:
            QMessageBox.information(self, "Revisión", "No hay episodios pendientes de revisión en esta selección.")
            return

        self.review_view.load_episode(queue[0], queue=queue)
        self.view_tabs.setCurrentWidget(self.review_view)

    # --- escaneo -------------------------------------------------------

    def _start_scan(self, force_reprobe: bool = False) -> None:
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
            force_reprobe=force_reprobe,
        )
        worker.signals.progress.connect(self._on_scan_progress)
        worker.signals.finished.connect(self._on_scan_finished)
        worker.signals.error.connect(self._on_scan_error)
        self._current_worker = worker

        self.scan_action.setEnabled(False)
        self.force_reprobe_action.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.cancel_button.setVisible(True)
        self.status_label.setText("Re-analizando toda la biblioteca..." if force_reprobe else "Escaneando...")

        self.thread_pool.start(worker)

    def _start_force_reprobe(self) -> None:
        reply = QMessageBox.question(
            self,
            "Forzar re-análisis técnico",
            "Vuelve a correr ffprobe/mkvmerge sobre TODOS los episodios, aunque no hayan "
            "cambiado en disco. Usalo después de instalar o configurar mkvmerge/ffmpeg más "
            "tarde, para recuperar los datos que dependen de ellos (como el ID de pista de "
            "mkvmerge que necesitan los comandos sugeridos).\n\n"
            "Ojo: episodios ya aprobados o marcados como corregidos vuelven a 'analizado' "
            "(el historial de reviews no se pierde, pero hay que volver a confirmarlos). "
            "No modifica ningún archivo de la librería.\n\n"
            "¿Continuar?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_scan(force_reprobe=True)

    def _cancel_current_worker(self) -> None:
        if self._current_worker is not None:
            self._current_worker.cancel()
        if self._current_tmdb_worker is not None:
            self._current_tmdb_worker.cancel()
        if self._current_analyze_worker is not None:
            self._current_analyze_worker.cancel()
        if self._current_candidates_worker is not None:
            self._current_candidates_worker.cancel()
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
        self.force_reprobe_action.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self._last_unparseable = summary.get("unparseable", [])

        msg = (
            f"Listo: {summary['series_count']} series, {summary['changed_count']} episodios nuevos/cambiados, "
            f"{summary['missing_count']} marcados ausentes, {summary['probe_errors']} errores de análisis."
        )
        if summary.get("unreachable_media_paths"):
            msg += f" {len(summary['unreachable_media_paths'])} carpeta(s) de librería no accesibles."
        if summary.get("removed_empty_series"):
            msg += f" {summary['removed_empty_series']} serie(s) vacías eliminadas del catálogo."
        if self._last_unparseable:
            msg += f" {len(self._last_unparseable)} archivo(s) no reconocidos."
        if summary.get("cancelled"):
            msg = "Escaneo cancelado. " + msg

        self.status_label.setText(msg)
        self._reload_tree()
        self.problems_view.refresh()

    def _on_scan_error(self, message: str) -> None:
        self._current_worker = None
        self.scan_action.setEnabled(True)
        self.force_reprobe_action.setEnabled(True)
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

    # --- TMDB ------------------------------------------------------------

    def _make_tmdb_client(self) -> tmdb.TmdbClient:
        return tmdb.TmdbClient(self.config.tmdb_api_key, self.config.tmdb_language, get_tmdb_cache_dir())

    def _start_tmdb_sync(self) -> None:
        if not self.config.tmdb_api_key:
            QMessageBox.information(
                self,
                "TMDB no configurado",
                "Configurá una API key de TMDB en Preferencias para traer metadatos.",
            )
            return
        if self._current_tmdb_worker is not None:
            return

        worker = TmdbSyncWorker(self.session_factory, self._make_tmdb_client)
        worker.signals.progress.connect(self._on_scan_progress)
        worker.signals.finished.connect(self._on_tmdb_sync_finished)
        worker.signals.error.connect(self._on_tmdb_error)
        self._current_tmdb_worker = worker

        self.tmdb_sync_action.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.cancel_button.setVisible(True)
        self.status_label.setText("Sincronizando TMDB...")

        self.thread_pool.start(worker)

    def _on_tmdb_sync_finished(self, summary: dict) -> None:
        self._current_tmdb_worker = None
        self.tmdb_sync_action.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)

        if summary.get("total", 0) == 0:
            self.status_label.setText("TMDB: no había series nuevas para sincronizar.")
        else:
            msg = (
                f"TMDB: {summary['auto']} coincidencias automáticas, "
                f"{summary['unmatched']} necesitan revisión manual (click derecho en el árbol)."
            )
            if summary.get("errors"):
                msg += f" {summary['errors']} errores."
            self.status_label.setText(msg)

        self._reload_tree()

    def _on_tmdb_error(self, message: str) -> None:
        self._current_tmdb_worker = None
        self.tmdb_sync_action.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.status_label.setText("Error de TMDB.")
        QMessageBox.critical(self, "Error de TMDB", message)

    def _resolve_tmdb_for_series(self, series_id: int) -> None:
        if not self.config.tmdb_api_key:
            QMessageBox.information(
                self,
                "TMDB no configurado",
                "Configurá una API key de TMDB en Preferencias para buscar coincidencias.",
            )
            return

        with self.session_factory() as session:
            series = session.get(Series, series_id)
            if series is None:
                return
            folder_name = series.folder_name
            query = series.title or series.folder_name
            year = series.year

        dialog = TmdbMatchDialog(folder_name, query, year, self)
        dialog.search_requested.connect(lambda q, y: self._run_tmdb_search(dialog, q, y))
        self._run_tmdb_search(dialog, query, year)

        if dialog.exec() != QDialog.Accepted:
            return

        if dialog.was_skipped():
            with self.session_factory() as session:
                series = session.get(Series, series_id)
                if series is not None:
                    repo.mark_series_tmdb_status(session, series, "skipped")
                    session.commit()
            self._reload_tree()
            return

        tmdb_id = dialog.selected_tmdb_id()
        if tmdb_id is None:
            return

        worker = TmdbApplyWorker(self.session_factory, self._make_tmdb_client, series_id, tmdb_id, status="manual")
        worker.signals.finished.connect(lambda _: self._on_tmdb_apply_finished())
        worker.signals.error.connect(lambda msg: QMessageBox.warning(self, "Error de TMDB", msg))
        self.status_label.setText("Aplicando coincidencia de TMDB...")
        self.thread_pool.start(worker)

    def _on_tmdb_apply_finished(self) -> None:
        self.status_label.setText("Metadatos de TMDB actualizados.")
        self._reload_tree()

    def _run_tmdb_search(self, dialog: TmdbMatchDialog, query: str, year: int | None) -> None:
        worker = TmdbSearchWorker(self._make_tmdb_client, query, year)

        def _on_finished(payload: dict) -> None:
            # el diálogo puede haberse cerrado (o esta puede ser una búsqueda
            # vieja superada por una más nueva) antes de que la respuesta
            # llegue: tocar un widget ya destruido tira RuntimeError en vez
            # de crashear, pero igual no hay nada útil que actualizar.
            try:
                dialog.set_results(payload.get("results", []))
            except RuntimeError:
                pass

        def _on_error(message: str) -> None:
            try:
                dialog.set_error(message)
            except RuntimeError:
                pass

        worker.signals.finished.connect(_on_finished)
        worker.signals.error.connect(_on_error)
        self.thread_pool.start(worker)

    # --- análisis de sincronización ---------------------------------------

    def _start_analyze(self) -> None:
        if self._current_analyze_worker is not None:
            return

        worker = AnalyzeWorker(
            self.session_factory,
            self.tool_paths.ffmpeg,
            get_audio_cache_dir(),
            self.config.window_seconds,
            self.config.sample_rate,
            self._make_tmdb_client,
            audio_cache_limit_gb=self.config.audio_cache_limit_gb,
        )
        worker.signals.progress.connect(self._on_scan_progress)
        worker.signals.finished.connect(self._on_analyze_finished)
        worker.signals.error.connect(self._on_analyze_error)
        self._current_analyze_worker = worker

        self.analyze_action.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.cancel_button.setVisible(True)
        self.status_label.setText("Analizando sincronización...")

        self.thread_pool.start(worker)

    def _on_analyze_finished(self, summary: dict) -> None:
        self._current_analyze_worker = None
        self.analyze_action.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)

        if summary.get("total", 0) == 0:
            self.status_label.setText("Análisis: no había episodios pendientes.")
        else:
            msg = (
                f"Análisis: {summary['analyzed']} episodios analizados, "
                f"{summary['no_pairs']} sin pistas para comparar."
            )
            if summary.get("errors"):
                msg += f" {summary['errors']} con errores."
            self.status_label.setText(msg)

        self.library_view.refresh()
        self.problems_view.refresh()

    def _on_analyze_error(self, message: str) -> None:
        self._current_analyze_worker = None
        self.analyze_action.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.status_label.setText("Error en el análisis.")
        QMessageBox.critical(self, "Error de análisis", message)

    # --- candidatos externos (modo externo) -------------------------------

    def _start_candidates_scan(self) -> None:
        if not self.config.candidates_paths:
            QMessageBox.information(
                self,
                "Sin carpetas de candidatos",
                "Configurá al menos una carpeta de candidatos en Preferencias. "
                "Es opcional: sin ella, la app funciona igual analizando solo pistas internas.",
            )
            return
        if self._current_candidates_worker is not None:
            return

        worker = CandidatesScanWorker(self.session_factory, self.config.candidates_paths)
        worker.signals.progress.connect(self._on_scan_progress)
        worker.signals.finished.connect(self._on_candidates_scan_finished)
        worker.signals.error.connect(self._on_candidates_scan_error)
        self._current_candidates_worker = worker

        self.candidates_scan_action.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.cancel_button.setVisible(True)
        self.status_label.setText("Escaneando candidatos externos...")

        self.thread_pool.start(worker)

    def _on_candidates_scan_finished(self, summary: dict) -> None:
        self._current_candidates_worker = None
        self.candidates_scan_action.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)

        if summary.get("cancelled"):
            self.status_label.setText("Escaneo de candidatos cancelado.")
        else:
            msg = f"Candidatos: {summary['matched']} emparejados."
            if summary.get("unmatched_episode"):
                msg += f" {summary['unmatched_episode']} sin episodio correspondiente."
            if summary.get("unmatched_series"):
                msg += f" {summary['unmatched_series']} carpeta(s) sin serie reconocida."
            if summary.get("unparseable"):
                msg += f" {summary['unparseable']} archivo(s) no reconocidos."
            if summary.get("missing"):
                msg += f" {summary['missing']} marcados ausentes."
            self.status_label.setText(msg)

    def _on_candidates_scan_error(self, message: str) -> None:
        self._current_candidates_worker = None
        self.candidates_scan_action.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.status_label.setText("Error escaneando candidatos.")
        QMessageBox.critical(self, "Error de candidatos", message)

    # --- sugerencias / export de temporada --------------------------------

    def _export_season_script(self, season_id: int) -> None:
        if not self.config.output_dir:
            QMessageBox.warning(
                self,
                "Sin carpeta de salida",
                "Configurá una carpeta de salida en Preferencias antes de exportar.",
            )
            return

        with self.session_factory() as session:
            episodes = repo.list_episodes_for_export(session, season_id)
            episode_suggestions = []
            for ep in episodes:
                analysis, _ref_track, cand_track = repo.get_latest_analysis_with_tracks(session, ep)
                if analysis is None:
                    continue
                suggestion = suggestions_module.suggest_for_analysis(
                    verdict=analysis.verdict,
                    pair_source=analysis.pair_source,
                    suggested_delay_ms=analysis.suggested_delay_ms,
                    suggested_resample_ratio=analysis.suggested_resample_ratio,
                    mkv_track_id=cand_track.mkv_track_id if cand_track else None,
                    episode_path=ep.path,
                    output_filename=Path(ep.path).name,
                    output_dir=self.config.output_dir,
                    mkvmerge_bin=str(self.tool_paths.mkvmerge) if self.tool_paths.mkvmerge else None,
                    ffmpeg_bin=str(self.tool_paths.ffmpeg) if self.tool_paths.ffmpeg else None,
                )
                episode_suggestions.append((Path(ep.path).name, suggestion))

        if not any(s.commands for _, s in episode_suggestions):
            QMessageBox.information(
                self,
                "Nada para exportar",
                "No hay episodios con comando sugerido en esta temporada "
                "(todo ok, faltan analizar, o falta mkvmerge/ffmpeg).",
            )
            return

        default_name = "script.bat" if sys.platform == "win32" else "script.sh"
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Exportar script de la temporada", default_name, "Scripts (*.bat *.sh);;Todos los archivos (*)"
        )
        if not path_str:
            return

        platform = "windows" if path_str.lower().endswith(".bat") else "linux"
        script_text = suggestions_module.build_season_export_script(episode_suggestions, platform=platform)

        try:
            Path(path_str).write_text(script_text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Error al exportar", str(exc))
            return

        QMessageBox.information(self, "Exportado", f"Script guardado en:\n{path_str}")

    # --- preferencias / jobs pendientes --------------------------------

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.Accepted:
            self.config = dialog.result_config()
            self.filter_checkbox.setText(self._filter_label())
            self._on_language_filter_changed()
            self.tool_paths = tools.resolve_all(self.config.ffmpeg_path, self.config.mkvmerge_path)
            self._warn_missing_tools()
            self.review_view.config = self.config
            self.review_view.tool_paths = self.tool_paths

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
        if self._current_tmdb_worker is not None:
            self._current_tmdb_worker.cancel()
        if self._current_analyze_worker is not None:
            self._current_analyze_worker.cancel()
        if self._current_candidates_worker is not None:
            self._current_candidates_worker.cancel()
        self.review_view.terminate_player()
        super().closeEvent(event)
