"""Selector de coincidencias de TMDB con pósters (spec sección 5.4).

Puramente de presentación: no hace llamadas de red. La búsqueda (inicial y
las de "buscar de nuevo") las dispara la ventana principal vía
``TmdbSearchWorker`` y empuja los resultados acá con ``set_results``.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

POSTER_ICON_SIZE = QSize(92, 138)


class TmdbMatchDialog(QDialog):
    search_requested = Signal(str, object)  # query, year (int | None)

    def __init__(self, folder_name: str, initial_query: str, initial_year: int | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Coincidencia TMDB — {folder_name}")
        self.resize(520, 480)
        self._skipped = False

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"Carpeta: {folder_name}"))

        search_bar = QHBoxLayout()
        self.query_edit = QLineEdit(initial_query)
        self.year_spin = QSpinBox()
        self.year_spin.setRange(0, 2100)
        self.year_spin.setSpecialValueText("(cualquier año)")
        self.year_spin.setValue(initial_year or 0)
        search_btn = QPushButton("Buscar")
        search_btn.clicked.connect(self._on_search_clicked)
        search_bar.addWidget(self.query_edit, 1)
        search_bar.addWidget(self.year_spin)
        search_bar.addWidget(search_btn)
        layout.addLayout(search_bar)

        self.status_label = QLabel("Buscando...")
        layout.addWidget(self.status_label)

        self.results_list = QListWidget()
        self.results_list.setIconSize(POSTER_ICON_SIZE)
        self.results_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.results_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.results_list.setGridSize(QSize(140, 190))
        self.results_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.results_list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.results_list, 1)

        buttons = QHBoxLayout()
        skip_btn = QPushButton("Omitir esta serie")
        skip_btn.clicked.connect(self._on_skip)
        buttons.addWidget(skip_btn)
        buttons.addStretch()
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._on_accept)
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        layout.addLayout(buttons)

    def _on_search_clicked(self) -> None:
        self.results_list.clear()
        self.status_label.setText("Buscando...")
        year = self.year_spin.value() or None
        self.search_requested.emit(self.query_edit.text().strip(), year)

    def set_results(self, results: list[dict]) -> None:
        self.results_list.clear()
        if not results:
            self.status_label.setText("Sin resultados. Probá con otro texto o año.")
            return
        self.status_label.setText(f"{len(results)} resultado(s) — doble click para elegir")
        for r in results:
            label = f"{r['name']} ({r['year'] or '?'})"
            item = QListWidgetItem(label)
            if r.get("poster_local_path"):
                item.setIcon(QIcon(r["poster_local_path"]))
            item.setData(Qt.ItemDataRole.UserRole, r["tmdb_id"])
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            self.results_list.addItem(item)

    def set_error(self, message: str) -> None:
        self.status_label.setText(f"Error: {message}")

    def _on_skip(self) -> None:
        self._skipped = True
        self.accept()

    def _on_accept(self) -> None:
        if self.selected_tmdb_id() is None:
            self.status_label.setText("Elegí un resultado de la lista, o usá 'Omitir esta serie'.")
            return
        self.accept()

    def was_skipped(self) -> bool:
        return self._skipped

    def selected_tmdb_id(self) -> int | None:
        items = self.results_list.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.ItemDataRole.UserRole)
