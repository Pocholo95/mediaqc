"""Visor de subtítulos: verifica que los tiempos coincidan con el video.

Solo lectura — no es un editor, la app nunca reescribe archivos de la
librería (regla número uno de la spec). No modal: se puede dejar abierto al
lado del reproductor mientras se sigue mirando el episodio.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from mediaqc.core.subtitles import SubtitleCue


def _format_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours:02d}:{int(minutes):02d}:{secs:06.3f}"


class SubtitleViewerDialog(QDialog):
    def __init__(self, title: str, cues: list[SubtitleCue], player, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Subtítulos — {title}")
        self.resize(560, 640)
        self.setModal(False)

        self.cues = cues
        self.player = player
        self._active_row = -1

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{len(cues)} línea(s). Doble click en una fila para saltar ahí."))

        self.table = QTableWidget(len(cues), 3)
        self.table.setHorizontalHeaderLabels(["Inicio", "Fin", "Texto"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, cue in enumerate(cues):
            self.table.setItem(row, 0, QTableWidgetItem(_format_timestamp(cue.start_s)))
            self.table.setItem(row, 1, QTableWidgetItem(_format_timestamp(cue.end_s)))
            self.table.setItem(row, 2, QTableWidgetItem(cue.text.replace("\n", " / ")))
        self.table.cellDoubleClicked.connect(self._on_row_activated)
        layout.addWidget(self.table)

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._sync_with_player)
        self._timer.start()

    def _on_row_activated(self, row: int, _col: int) -> None:
        if 0 <= row < len(self.cues) and self.player is not None:
            self.player.seek_absolute(self.cues[row].start_s)

    def _sync_with_player(self) -> None:
        if self.player is None or not self.player.is_loaded():
            return
        row = self._find_active_row(self.player.position_seconds)
        if row == self._active_row:
            return
        self._active_row = row
        self.table.clearSelection()
        if row >= 0:
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, 0))

    def _find_active_row(self, pos: float) -> int:
        for i, cue in enumerate(self.cues):
            if cue.start_s <= pos <= cue.end_s:
                return i
        return -1

    def closeEvent(self, event) -> None:  # noqa: N802 (override de Qt)
        self._timer.stop()
        super().closeEvent(event)
