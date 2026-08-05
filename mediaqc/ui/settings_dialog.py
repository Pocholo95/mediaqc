"""Diálogo de preferencias y asistente de primer arranque (spec sección 3)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from mediaqc.core.config import Config, ConfigError, save_config


class _PathList(QVBoxLayout):
    """Lista editable de carpetas: Agregar (QFileDialog) / Quitar seleccionada."""

    def __init__(self, initial: list[str] | None = None) -> None:
        super().__init__()
        self.list_widget = QListWidget()
        for p in initial or []:
            self.list_widget.addItem(p)

        buttons = QHBoxLayout()
        add_btn = QPushButton("Agregar carpeta...")
        remove_btn = QPushButton("Quitar seleccionada")
        add_btn.clicked.connect(self._add)
        remove_btn.clicked.connect(self._remove)
        buttons.addWidget(add_btn)
        buttons.addWidget(remove_btn)

        self.addWidget(self.list_widget)
        self.addLayout(buttons)

    def _add(self) -> None:
        directory = QFileDialog.getExistingDirectory(None, "Elegir carpeta")
        if directory:
            existing = [self.list_widget.item(i).text() for i in range(self.list_widget.count())]
            if directory not in existing:
                self.list_widget.addItem(directory)

    def _remove(self) -> None:
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def values(self) -> list[str]:
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]


class _OutputDirPicker(QHBoxLayout):
    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self.line_edit = QLineEdit(initial)
        browse_btn = QPushButton("Elegir...")
        browse_btn.clicked.connect(self._browse)
        self.addWidget(self.line_edit)
        self.addWidget(browse_btn)

    def _browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(None, "Elegir carpeta de salida")
        if directory:
            self.line_edit.setText(directory)

    def value(self) -> str:
        return self.line_edit.text().strip()


class SettingsDialog(QDialog):
    """Edita una copia de la configuración; solo la pisa si valida al aceptar."""

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferencias — MediaQC")
        self.resize(560, 520)
        self._config = config

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.media_paths_list = _PathList(config.media_paths)
        form.addRow("Carpetas de la librería:", self.media_paths_list)

        self.candidates_paths_list = _PathList(config.candidates_paths)
        form.addRow("Carpetas de candidatos (opcional):", self.candidates_paths_list)

        self.output_dir_picker = _OutputDirPicker(config.output_dir)
        form.addRow("Carpeta de salida (muxeados):", self.output_dir_picker)

        self.tmdb_key_edit = QLineEdit(config.tmdb_api_key)
        form.addRow("TMDB API key (opcional):", self.tmdb_key_edit)

        tmdb_link = QLabel(
            '<a href="https://www.themoviedb.org/settings/api">Obtener una API key de TMDB</a>'
        )
        tmdb_link.setOpenExternalLinks(True)
        form.addRow("", tmdb_link)

        self.tmdb_language_edit = QLineEdit(config.tmdb_language)
        form.addRow("Idioma TMDB:", self.tmdb_language_edit)

        self.expected_languages_edit = QLineEdit(", ".join(config.expected_languages))
        form.addRow("Idiomas de doblaje esperados (códigos ISO 639-2):", self.expected_languages_edit)

        self.ffmpeg_path_edit = QLineEdit(config.ffmpeg_path or "")
        form.addRow("Ruta a ffmpeg (opcional):", self.ffmpeg_path_edit)

        self.mkvmerge_path_edit = QLineEdit(config.mkvmerge_path or "")
        form.addRow("Ruta a mkvmerge (opcional):", self.mkvmerge_path_edit)

        self.max_jobs_spin = QSpinBox()
        self.max_jobs_spin.setRange(1, 16)
        self.max_jobs_spin.setValue(config.max_concurrent_jobs)
        form.addRow("Jobs concurrentes máximos:", self.max_jobs_spin)

        self.audio_cache_spin = QDoubleSpinBox()
        self.audio_cache_spin.setRange(0.5, 100.0)
        self.audio_cache_spin.setValue(config.audio_cache_limit_gb)
        self.audio_cache_spin.setSuffix(" GB")
        form.addRow("Límite de caché de audio:", self.audio_cache_spin)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_config(self) -> Config:
        expected = [s.strip() for s in self.expected_languages_edit.text().split(",") if s.strip()]
        return Config(
            media_paths=self.media_paths_list.values(),
            candidates_paths=self.candidates_paths_list.values(),
            output_dir=self.output_dir_picker.value(),
            tmdb_api_key=self.tmdb_key_edit.text().strip(),
            tmdb_language=self.tmdb_language_edit.text().strip() or "es-MX",
            expected_languages=expected or ["spa"],
            analysis_windows=self._config.analysis_windows,
            window_seconds=self._config.window_seconds,
            sample_rate=self._config.sample_rate,
            max_concurrent_jobs=self.max_jobs_spin.value(),
            audio_cache_limit_gb=self.audio_cache_spin.value(),
            ffmpeg_path=self.ffmpeg_path_edit.text().strip() or None,
            mkvmerge_path=self.mkvmerge_path_edit.text().strip() or None,
        )

    def _on_accept(self) -> None:
        new_config = self._build_config()
        try:
            save_config(new_config)
        except ConfigError as exc:
            QMessageBox.critical(self, "Configuración inválida", str(exc))
            return
        self._config = new_config
        self.accept()

    def result_config(self) -> Config:
        return self._config


class FirstRunWizard(QWizard):
    """Asistente de tres pasos: librería, candidatos (opcional), TMDB (opcional)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bienvenido a MediaQC")
        self.setOption(QWizard.NoBackButtonOnStartPage, True)

        self._media_page, self.media_paths_list = self._make_path_page(
            "Carpetas de la librería",
            "Elegí las carpetas donde están tus series (BD rips). Podés agregar varias.",
        )
        self._candidates_page, self.candidates_paths_list = self._make_path_page(
            "Carpetas de candidatos (opcional)",
            "Si tenés doblajes sueltos todavía sin muxear, elegí dónde están. "
            "Se puede dejar vacío y la app funciona igual, analizando solo pistas internas.",
        )
        self._tmdb_page, self.tmdb_key_edit = self._make_tmdb_page()

        self.addPage(self._media_page)
        self.addPage(self._candidates_page)
        self.addPage(self._tmdb_page)

    def _make_path_page(self, title: str, subtitle: str) -> tuple[QWizardPage, _PathList]:
        page = QWizardPage()
        page.setTitle(title)
        page.setSubTitle(subtitle)
        path_list = _PathList()
        page.setLayout(path_list)
        return page, path_list

    def _make_tmdb_page(self) -> tuple[QWizardPage, QLineEdit]:
        page = QWizardPage()
        page.setTitle("TMDB (opcional)")
        page.setSubTitle(
            "Con una API key de TMDB la app trae títulos de episodio, fechas y pósters. "
            "Sin ella, funciona igual solo que sin esos metadatos."
        )
        layout = QVBoxLayout()
        link = QLabel('<a href="https://www.themoviedb.org/settings/api">Obtener una API key de TMDB</a>')
        link.setOpenExternalLinks(True)
        layout.addWidget(link)
        key_edit = QLineEdit()
        key_edit.setPlaceholderText("API key (opcional)")
        layout.addWidget(key_edit)
        page.setLayout(layout)
        return page, key_edit

    def build_config(self) -> Config:
        return Config(
            media_paths=self.media_paths_list.values(),
            candidates_paths=self.candidates_paths_list.values(),
            tmdb_api_key=self.tmdb_key_edit.text().strip(),
        )
