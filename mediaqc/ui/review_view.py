"""Vista 2 — Revisión de episodio (spec sección 6). La pantalla crítica:
tiene que permitir juzgar un capítulo en menos de 30 segundos.

Sin analizador todavía (fase 4): la tabla de ventanas muestra las 5
posiciones de muestreo sin offset/score, y el delay arranca en 0 en vez de
precargado con un valor sugerido — igual el usuario puede ajustarlo a oído
en vivo contra el reproductor, que es el valor central de esta pantalla.
El comando `mkvmerge` sugerido llega en la fase 5 (``core/suggestions.py``).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mediaqc.core.analyzer.windows import window_positions_seconds
from mediaqc.core.db import repo
from mediaqc.core.db.models import Episode
from mediaqc.ui.library_view import STATUS_LABELS
from mediaqc.ui.player import MpvInitError, MpvPlayerWidget

_VERDICT_SHORTCUTS = [
    ("1", "ok", "OK"),
    ("2", "sync_constante", "Sync constante"),
    ("3", "sync_drift", "Drift"),
    ("4", "sync_segmentado", "Segmentado"),
    ("5", "audio_faltante", "Falta audio"),
]


def _format_mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class ReviewView(QWidget):
    verdict_recorded = Signal(int)  # episode_id, para que MainWindow refresque el semáforo
    queue_exhausted = Signal()

    def __init__(self, session_factory, parent=None) -> None:
        super().__init__(parent)
        self.session_factory = session_factory
        self._queue: list[int] = []
        self._queue_index: int = -1
        self._episode_id: int | None = None
        self._marked_timestamp_ms: int | None = None
        self._window_positions: list[float] = []
        self._timeline_dragging = False

        self._build_ui()
        self._setup_shortcuts()

        self._position_timer = QTimer(self)
        self._position_timer.setInterval(300)
        self._position_timer.timeout.connect(self._update_position_display)
        self._position_timer.start()

    # --- construcción de UI ------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.title_label = QLabel("Elegí un episodio para revisar")
        self.title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        header.addWidget(self.title_label, 1)
        self.queue_label = QLabel("")
        header.addWidget(self.queue_label)
        layout.addLayout(header)

        self.player: MpvPlayerWidget | None = None
        self._player_error: str | None = None
        try:
            self.player = MpvPlayerWidget(self)
            layout.addWidget(self.player, 3)
        except MpvInitError as exc:
            self._player_error = str(exc)
            placeholder = QLabel(
                "No se pudo inicializar el reproductor mpv.\n\n"
                f"{self._player_error}\n\n"
                "Instalá libmpv y colocá libmpv-2.dll junto al ejecutable (carpeta bin/), "
                "o configurá su ruta en Preferencias."
            )
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setWordWrap(True)
            placeholder.setStyleSheet("background: #222; color: #ccc; padding: 40px;")
            layout.addWidget(placeholder, 3)

        transport_bar = QHBoxLayout()
        self.play_pause_btn = QPushButton("Pausar")
        self.play_pause_btn.clicked.connect(self._toggle_pause)
        transport_bar.addWidget(self.play_pause_btn)
        back_btn = QPushButton("« 10s")
        back_btn.clicked.connect(lambda: self._seek_relative(-10))
        transport_bar.addWidget(back_btn)
        fwd_btn = QPushButton("10s »")
        fwd_btn.clicked.connect(lambda: self._seek_relative(10))
        transport_bar.addWidget(fwd_btn)
        self.position_label = QLabel("00:00 / 00:00")
        transport_bar.addWidget(self.position_label)
        self.timeline_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.sliderPressed.connect(self._on_timeline_pressed)
        self.timeline_slider.sliderReleased.connect(self._on_timeline_released)
        transport_bar.addWidget(self.timeline_slider, 1)
        layout.addLayout(transport_bar)

        jump_bar = QHBoxLayout()
        jump_bar.addWidget(QLabel("Saltar a:"))
        self.jump_buttons: list[QPushButton] = []
        for i in range(5):
            btn = QPushButton("--:--")
            btn.setEnabled(False)
            btn.clicked.connect(lambda _checked=False, idx=i: self._jump_to_window(idx))
            jump_bar.addWidget(btn)
            self.jump_buttons.append(btn)
        jump_bar.addStretch()
        layout.addLayout(jump_bar)

        audio_bar = QHBoxLayout()
        audio_bar.addWidget(QLabel("Pista de audio:"))
        self.audio_track_combo = QComboBox()
        self.audio_track_combo.currentIndexChanged.connect(self._on_audio_track_changed)
        audio_bar.addWidget(self.audio_track_combo, 1)
        self.load_candidate_btn = QPushButton("Cargar candidato con delay sugerido")
        self.load_candidate_btn.setEnabled(False)  # candidatos/sugerencias llegan en fases 4-5
        self.load_candidate_btn.setToolTip("Disponible cuando el analizador de audio esté implementado (fase 4).")
        audio_bar.addWidget(self.load_candidate_btn)
        layout.addLayout(audio_bar)

        subtitle_bar = QHBoxLayout()
        subtitle_bar.addWidget(QLabel("Subtítulos:"))
        self.subtitle_track_combo = QComboBox()
        self.subtitle_track_combo.currentIndexChanged.connect(self._on_subtitle_track_changed)
        subtitle_bar.addWidget(self.subtitle_track_combo, 1)
        layout.addLayout(subtitle_bar)

        delay_bar = QHBoxLayout()
        delay_bar.addWidget(QLabel("Delay de audio:"))
        self.delay_slider = QSlider(Qt.Orientation.Horizontal)
        self.delay_slider.setRange(-5000, 5000)
        self.delay_slider.valueChanged.connect(self._on_delay_slider_changed)
        delay_bar.addWidget(self.delay_slider, 1)
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(-5000, 5000)
        self.delay_spin.setSuffix(" ms")
        self.delay_spin.valueChanged.connect(self._on_delay_spin_changed)
        delay_bar.addWidget(self.delay_spin)
        layout.addLayout(delay_bar)

        self.windows_table = QTableWidget(5, 3)
        self.windows_table.setHorizontalHeaderLabels(["Posición", "Offset medido", "Score"])
        self.windows_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.windows_table.setMaximumHeight(160)
        self.windows_table.verticalHeader().setVisible(False)
        layout.addWidget(self.windows_table)

        verdict_bar = QHBoxLayout()
        self.verdict_buttons: dict[str, QPushButton] = {}
        for key, verdict, label in _VERDICT_SHORTCUTS:
            btn = QPushButton(f"{label} ({key})")
            btn.clicked.connect(lambda _checked=False, v=verdict: self._record_verdict(v))
            verdict_bar.addWidget(btn)
            self.verdict_buttons[verdict] = btn

        other_verdicts = sorted(repo.VALID_VERDICTS - {v for _, v, _ in _VERDICT_SHORTCUTS})
        self.other_verdict_combo = QComboBox()
        self.other_verdict_combo.addItems(other_verdicts)
        verdict_bar.addWidget(self.other_verdict_combo)
        other_btn = QPushButton("Registrar (N: nota)")
        other_btn.clicked.connect(lambda: self._prompt_note_and_record(self.other_verdict_combo.currentText()))
        verdict_bar.addWidget(other_btn)
        layout.addLayout(verdict_bar)

        mark_bar = QHBoxLayout()
        self.mark_problem_btn = QPushButton("Marcar problema aquí")
        self.mark_problem_btn.clicked.connect(self._mark_problem_here)
        mark_bar.addWidget(self.mark_problem_btn)
        self.mark_label = QLabel("")
        mark_bar.addWidget(self.mark_label)
        mark_bar.addStretch()
        next_btn = QPushButton("Siguiente episodio (Espacio)")
        next_btn.clicked.connect(self.go_next)
        mark_bar.addWidget(next_btn)
        layout.addLayout(mark_bar)

        layout.addWidget(QLabel("Historial de reviews:"))
        self.history_list = QListWidget()
        self.history_list.setMaximumHeight(110)
        layout.addWidget(self.history_list)

    def _setup_shortcuts(self) -> None:
        bindings = [(key, verdict) for key, verdict, _label in _VERDICT_SHORTCUTS]
        for key, verdict in bindings:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda v=verdict: self._record_verdict(v))

        note_sc = QShortcut(QKeySequence("N"), self)
        note_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        note_sc.activated.connect(lambda: self._prompt_note_and_record("otro"))

        next_sc = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        next_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        next_sc.activated.connect(self.go_next)

    # --- carga de episodio ------------------------------------------------

    def load_episode(self, episode_id: int, queue: list[int] | None = None) -> None:
        if queue is not None:
            self._queue = queue
            self._queue_index = queue.index(episode_id) if episode_id in queue else -1

        self._episode_id = episode_id
        self._marked_timestamp_ms = None
        self.mark_label.setText("")

        with self.session_factory() as session:
            episode = session.get(Episode, episode_id)
            if episode is None:
                return

            series_title = "?"
            season_no = "?"
            if episode.season is not None:
                season_no = episode.season.number
                if episode.season.series is not None:
                    series_title = episode.season.series.title or episode.season.series.folder_name

            status_label = STATUS_LABELS.get(episode.status, episode.status)
            display_title = episode.tmdb_title or Path(episode.path).stem
            self.title_label.setText(f"{series_title} — T{season_no}E{episode.number} — {display_title} [{status_label}]")

            self._populate_audio_tracks(episode)
            self._populate_subtitle_tracks(episode)
            self._populate_windows(episode.duration_s)
            self._populate_history(session, episode_id)

            file_path = episode.path

        if self._queue:
            self.queue_label.setText(f"{self._queue_index + 1} / {len(self._queue)}")
        else:
            self.queue_label.setText("")

        self.delay_slider.blockSignals(True)
        self.delay_spin.blockSignals(True)
        self.delay_slider.setValue(0)
        self.delay_spin.setValue(0)
        self.delay_slider.blockSignals(False)
        self.delay_spin.blockSignals(False)

        self.play_pause_btn.setText("Pausar")
        self.timeline_slider.setRange(0, 0)
        self.position_label.setText("00:00 / 00:00")

        if self.player is not None:
            self.player.load(file_path)
            # mpv respeta el flag "default" del contenedor al cargar, que en
            # la práctica muchas veces es el doblaje, no la referencia -- hay
            # que forzarlo a lo que el combo ya muestra como elegido, si no
            # el audio que suena y el que dice la UI quedan desincronizados.
            self.player.set_audio_track(self.audio_track_combo.currentData())
            self.player.set_subtitle_track(self.subtitle_track_combo.currentData())

    def _populate_audio_tracks(self, episode: Episode) -> None:
        self.audio_track_combo.blockSignals(True)
        self.audio_track_combo.clear()
        audio_tracks = sorted(
            (t for t in episode.tracks if t.type == "audio"),
            key=lambda t: t.stream_index if t.stream_index is not None else 0,
        )
        default_index = 0  # si ninguna viene marcada default, la primera
        for ordinal, t in enumerate(audio_tracks, start=1):
            label = f"{t.language or 'und'} · {t.codec or '?'} · {t.channels or '?'}ch"
            if t.title:
                label += f" · {t.title}"
            if t.is_default:
                label += " [default]"
                default_index = ordinal - 1
            # ordinal 1-based dentro de las pistas de audio: es como mpv
            # numera `aid` para un demuxer estándar, no el stream_index de
            # ffprobe ni el track id de mkvmerge (trampa #1, aplica también acá).
            self.audio_track_combo.addItem(label, ordinal)
        if audio_tracks:
            self.audio_track_combo.setCurrentIndex(default_index)
        self.audio_track_combo.blockSignals(False)

    def _populate_subtitle_tracks(self, episode: Episode) -> None:
        self.subtitle_track_combo.blockSignals(True)
        self.subtitle_track_combo.clear()
        self.subtitle_track_combo.addItem("Sin subtítulos", None)
        sub_tracks = sorted(
            (t for t in episode.tracks if t.type == "subtitle"),
            key=lambda t: t.stream_index if t.stream_index is not None else 0,
        )
        default_index = 0  # "Sin subtítulos" si ninguna pista viene marcada default
        for ordinal, t in enumerate(sub_tracks, start=1):
            label = t.language or "und"
            if t.is_forced:
                label += " (forzado)"
            if t.title:
                label += f" · {t.title}"
            if t.is_default:
                label += " [default]"
                default_index = ordinal
            self.subtitle_track_combo.addItem(label, ordinal)
        self.subtitle_track_combo.setCurrentIndex(default_index)
        self.subtitle_track_combo.blockSignals(False)

    def _populate_windows(self, duration_s: float | None) -> None:
        self._window_positions = window_positions_seconds(duration_s or 0.0)
        for i, pos in enumerate(self._window_positions):
            label = _format_mmss(pos)
            self.windows_table.setItem(i, 0, QTableWidgetItem(label))
            self.windows_table.setItem(i, 1, QTableWidgetItem("—"))
            self.windows_table.setItem(i, 2, QTableWidgetItem("—"))
            if i < len(self.jump_buttons):
                self.jump_buttons[i].setText(label)
                self.jump_buttons[i].setEnabled(bool(duration_s))

    def _populate_history(self, session, episode_id: int) -> None:
        self.history_list.clear()
        reviews = repo.list_episode_reviews(session, episode_id)
        if not reviews:
            self.history_list.addItem("(sin reviews previas)")
            return
        for r in reviews:
            when = r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""
            ts = f" @ {_format_mmss(r.timestamp_ms / 1000)}" if r.timestamp_ms else ""
            note = f" — {r.note}" if r.note else ""
            self.history_list.addItem(f"[{when}] {r.verdict}{ts}{note}")

    # --- reproductor -----------------------------------------------------

    def _jump_to_window(self, index: int) -> None:
        if self.player is None or index >= len(self._window_positions):
            return
        self.player.seek_absolute(self._window_positions[index])

    def _on_audio_track_changed(self, index: int) -> None:
        if self.player is None or index < 0:
            return
        ordinal = self.audio_track_combo.itemData(index)
        if ordinal is not None:
            self.player.set_audio_track(ordinal)

    def _on_subtitle_track_changed(self, index: int) -> None:
        if self.player is None or index < 0:
            return
        ordinal = self.subtitle_track_combo.itemData(index)
        self.player.set_subtitle_track(ordinal)

    def _toggle_pause(self) -> None:
        if self.player is None:
            return
        self.player.toggle_pause()
        self.play_pause_btn.setText("Reanudar" if self.player.is_paused else "Pausar")

    def _seek_relative(self, delta_seconds: float) -> None:
        if self.player is not None:
            self.player.seek_relative(delta_seconds)

    def _on_timeline_pressed(self) -> None:
        self._timeline_dragging = True

    def _on_timeline_released(self) -> None:
        self._timeline_dragging = False
        if self.player is not None:
            self.player.seek_absolute(self.timeline_slider.value())

    def _update_position_display(self) -> None:
        if self.player is None or not self.player.is_loaded() or self._timeline_dragging:
            return
        pos = self.player.position_seconds
        dur = self.player.duration_seconds
        self.position_label.setText(f"{_format_mmss(pos)} / {_format_mmss(dur)}")
        if dur > 0:
            self.timeline_slider.blockSignals(True)
            self.timeline_slider.setRange(0, int(dur))
            self.timeline_slider.setValue(int(pos))
            self.timeline_slider.blockSignals(False)
        self.play_pause_btn.setText("Reanudar" if self.player.is_paused else "Pausar")

    def _on_delay_slider_changed(self, value: int) -> None:
        if self.delay_spin.value() != value:
            self.delay_spin.blockSignals(True)
            self.delay_spin.setValue(value)
            self.delay_spin.blockSignals(False)
        if self.player is not None:
            self.player.set_audio_delay(value / 1000.0)

    def _on_delay_spin_changed(self, value: int) -> None:
        if self.delay_slider.value() != value:
            self.delay_slider.blockSignals(True)
            self.delay_slider.setValue(value)
            self.delay_slider.blockSignals(False)
        if self.player is not None:
            self.player.set_audio_delay(value / 1000.0)

    def _mark_problem_here(self) -> None:
        if self.player is None:
            return
        pos = self.player.position_seconds
        self._marked_timestamp_ms = int(pos * 1000)
        self.mark_label.setText(f"Marcado en {_format_mmss(pos)}")

    # --- veredictos ------------------------------------------------------

    def _record_verdict(self, verdict: str) -> None:
        if verdict == "otro":
            self._prompt_note_and_record(verdict)
            return
        self._save_review(verdict, note=None)

    def _prompt_note_and_record(self, verdict: str) -> None:
        if self._episode_id is None:
            return
        note, ok = QInputDialog.getMultiLineText(self, "Nota", f"Nota para el veredicto '{verdict}':")
        if not ok:
            return
        note = note.strip()
        if verdict == "otro" and not note:
            QMessageBox.warning(self, "Nota requerida", "El veredicto 'otro' exige una nota (spec sección 4).")
            return
        self._save_review(verdict, note=note or None)

    def _save_review(self, verdict: str, note: str | None) -> None:
        if self._episode_id is None:
            return
        with self.session_factory() as session:
            episode = session.get(Episode, self._episode_id)
            if episode is None:
                return
            repo.record_review(session, episode, verdict, timestamp_ms=self._marked_timestamp_ms, note=note)
            session.commit()
            status_label = STATUS_LABELS.get(episode.status, episode.status)
            self._populate_history(session, self._episode_id)

        self.title_label.setText(self.title_label.text().rsplit(" [", 1)[0] + f" [{status_label}]")
        self._marked_timestamp_ms = None
        self.mark_label.setText("")
        self.verdict_recorded.emit(self._episode_id)

    # --- navegación ------------------------------------------------------

    def go_next(self) -> None:
        if not self._queue:
            return
        if self._queue_index + 1 < len(self._queue):
            self._queue_index += 1
            self.load_episode(self._queue[self._queue_index])
        else:
            self.queue_exhausted.emit()

    def go_previous(self) -> None:
        if not self._queue or self._queue_index <= 0:
            return
        self._queue_index -= 1
        self.load_episode(self._queue[self._queue_index])

    def terminate_player(self) -> None:
        self._position_timer.stop()
        if self.player is not None:
            self.player.terminate()
