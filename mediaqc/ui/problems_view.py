"""Vista 3 — Problemas: lista plana de todo lo que está en ``problema``
(spec sección 6). Es la lista de pendientes de trabajo, ordenable por serie,
tipo (veredicto) y fecha vía el propio header de la tabla.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mediaqc.core.db import repo

_HEADERS = ["Serie", "Temporada", "#", "Archivo", "Último veredicto", "Nota", "Fecha"]


class ProblemsView(QWidget):
    episode_activated = Signal(int)  # episode_id

    def __init__(self, session_factory, parent=None) -> None:
        super().__init__(parent)
        self.session_factory = session_factory

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        bar = QHBoxLayout()
        self.count_label = QLabel("")
        bar.addWidget(self.count_label)
        bar.addStretch()
        refresh_btn = QPushButton("Actualizar")
        refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(refresh_btn)
        layout.addLayout(bar)

        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.table)

    def refresh(self) -> None:
        with self.session_factory() as session:
            episodes = repo.list_problem_episodes(session)
            rows = [self._row_for(ep) for ep in episodes]

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            values = [row["series"], row["season"], row["number"], row["file"], row["verdict"], row["note"], row["date"]]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row["episode_id"])
                self.table.setItem(row_idx, col, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.count_label.setText(f"{len(rows)} episodio(s) con problema")

    def current_episode_ids(self) -> list[int]:
        """IDs en el orden actual de la tabla (respeta el sort del usuario)."""
        ids = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def _row_for(self, ep) -> dict:
        series_title = "?"
        season_no = "?"
        if ep.season is not None:
            season_no = str(ep.season.number)
            if ep.season.series is not None:
                series_title = ep.season.series.title or ep.season.series.folder_name

        latest = max(ep.reviews, key=lambda r: r.created_at) if ep.reviews else None
        return {
            "episode_id": ep.id,
            "series": series_title,
            "season": season_no,
            "number": str(ep.number),
            "file": Path(ep.path).name,
            "verdict": latest.verdict if latest else "—",
            "note": (latest.note or "") if latest else "",
            "date": latest.created_at.strftime("%Y-%m-%d %H:%M") if latest and latest.created_at else "",
        }

    def _on_item_double_clicked(self, item: QTableWidgetItem) -> None:
        id_item = self.table.item(item.row(), 0)
        if id_item is None:
            return
        episode_id = id_item.data(Qt.ItemDataRole.UserRole)
        if episode_id is not None:
            self.episode_activated.emit(episode_id)
