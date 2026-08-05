"""Vista 1 — Biblioteca: semáforo de episodios (spec sección 6).

Cuadrícula de tarjetas por episodio, coloreadas por estado. El filtro por
serie/temporada lo maneja el árbol de la ventana principal (``set_scope``);
acá solo el filtro por estado y el semáforo en sí. El detalle rico
(idiomas de audio/subs, duración, desviación de runtime TMDB) queda en el
tooltip para no perder la información que ya mostraba la tabla de las
fases 1-2, sin saturar la tarjeta.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mediaqc.core.db import repo

STATUS_COLORS = {
    "sin_analizar": QColor(130, 130, 130),
    "analizado": QColor(224, 168, 0),
    "pendiente_revision": QColor(224, 168, 0),
    "aprobado": QColor(46, 160, 67),
    "problema": QColor(209, 60, 60),
    "corregido": QColor(60, 120, 216),
}

STATUS_LABELS = {
    "sin_analizar": "Sin analizar",
    "analizado": "Pendiente de revisión",
    "pendiente_revision": "Pendiente de revisión",
    "aprobado": "Aprobado",
    "problema": "Problema",
    "corregido": "Corregido",
}

CARD_SIZE = QSize(160, 92)


class LibraryView(QWidget):
    episode_activated = Signal(int)  # episode_id, para que el caller abra la revisión

    def __init__(self, session_factory, parent=None) -> None:
        super().__init__(parent)
        self.session_factory = session_factory
        self._scope: tuple[str, int] | None = None
        self._required_languages: set[str] | None = None
        self._current_episode_ids: list[int] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("Estado:"))
        self.status_filter = QComboBox()
        self.status_filter.addItem("Todos", None)
        for value in ("sin_analizar", "analizado", "problema", "aprobado", "corregido"):
            self.status_filter.addItem(STATUS_LABELS[value], value)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        filter_bar.addWidget(self.status_filter)
        filter_bar.addStretch()
        self.count_label = QLabel("")
        filter_bar.addWidget(self.count_label)
        layout.addLayout(filter_bar)

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setGridSize(CARD_SIZE)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.grid.setSpacing(4)
        self.grid.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.grid, 1)

    def set_scope(self, scope: tuple[str, int] | None) -> None:
        self._scope = scope
        self.refresh()

    def set_required_languages(self, languages: set[str] | None) -> None:
        """Si se setea, solo muestra episodios sin audio en ninguno de estos
        idiomas (el filtro "sin doblaje" de las fases 1-2)."""
        self._required_languages = languages
        self.refresh()

    def current_episode_ids(self) -> list[int]:
        """IDs en el orden mostrado actualmente -- la cola de revisión."""
        return list(self._current_episode_ids)

    def refresh(self) -> None:
        status_value = self.status_filter.currentData()
        self.grid.clear()

        with self.session_factory() as session:
            if self._scope is None:
                episodes = repo.list_all_episodes(session)
            elif self._scope[0] == "series":
                episodes = [e for e in repo.list_all_episodes(session) if e.season.series_id == self._scope[1]]
            else:
                episodes = repo.list_episodes_for_season(session, self._scope[1])

            if status_value:
                episodes = [e for e in episodes if e.status == status_value]

            if self._required_languages:
                episodes = [e for e in episodes if not repo.episode_has_audio_language(e, self._required_languages)]

            episodes.sort(key=lambda e: ((e.season.number if e.season else 0), e.number))

            for ep in episodes:
                self.grid.addItem(self._make_item(ep))

        self._current_episode_ids = [ep.id for ep in episodes]

        self.count_label.setText(f"{self.grid.count()} episodio(s)")

    def _make_item(self, ep) -> QListWidgetItem:
        title = ep.tmdb_title or Path(ep.path).stem
        season_no = ep.season.number if ep.season else "?"
        status_label = STATUS_LABELS.get(ep.status, ep.status)
        text = f"T{season_no}E{ep.number}\n{title}\n{status_label}"
        if ep.missing:
            text += "\n(ausente)"

        item = QListWidgetItem(text)
        item.setSizeHint(CARD_SIZE)
        color = STATUS_COLORS.get(ep.status, QColor(130, 130, 130))
        if ep.missing:
            color = color.darker(150)
        item.setBackground(QBrush(color))
        item.setForeground(QBrush(QColor(255, 255, 255) if color.lightness() < 150 else QColor(20, 20, 20)))
        item.setData(Qt.ItemDataRole.UserRole, ep.id)
        item.setToolTip(self._tooltip_for(ep))
        return item

    def _tooltip_for(self, ep) -> str:
        audio_langs = sorted({(t.language or "und") for t in ep.tracks if t.type == "audio"})
        sub_labels = sorted(
            {(t.language or "und") + (" (forzado)" if t.is_forced else "") for t in ep.tracks if t.type == "subtitle"}
        )
        lines = [
            Path(ep.path).name,
            f"Audio: {', '.join(audio_langs) or '—'}",
            f"Subtítulos: {', '.join(sub_labels) or '—'}",
        ]
        if ep.duration_s:
            lines.append(f"Duración: {ep.duration_s / 60:.1f} min")
        if repo.episode_runtime_deviates(ep):
            lines.append(f"⚠ Se desvía >15% del runtime TMDB ({ep.tmdb_runtime_min} min)")
        if ep.missing:
            lines.append("Archivo ausente (disco desconectado o borrado)")
        return "\n".join(lines)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        episode_id = item.data(Qt.ItemDataRole.UserRole)
        if episode_id is not None:
            self.episode_activated.emit(episode_id)
