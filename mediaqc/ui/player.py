"""Reproductor mpv embebido (spec sección 5.6).

mpv reproduce HEVC 10-bit, TrueHD, DTS-HD y todo lo que trae un BD rip sin
transcodificar — es lo que hace viable revisar 200 capítulos a mano. Se
embebe en un ``QWidget`` vía ``wid`` (python-mpv + libmpv).

Requiere que ``mediaqc.core.tools.ensure_mpv_loadable()`` ya haya corrido
antes de este import (pasa automáticamente al importar el paquete
``mediaqc``, ver ``mediaqc/__init__.py``) — si no, ``import mpv`` explota
buscando la DLL.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

import mpv

logger = logging.getLogger(__name__)


class MpvInitError(Exception):
    """libmpv no se pudo inicializar (falta la DLL, o falló libmpv mismo)."""


class MpvPlayerWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.setMinimumHeight(240)
        self.setStyleSheet("background-color: black;")

        self._terminated = False
        self._current_path: str | None = None

        try:
            self.mpv = mpv.MPV(
                wid=str(int(self.winId())),
                input_default_bindings=False,
                input_vo_keyboard=False,
                osc=False,
                keep_open="yes",
                log_handler=self._on_mpv_log,
                loglevel="error",
            )
        except Exception as exc:  # DLL ausente, libmpv roto, etc.
            raise MpvInitError(str(exc)) from exc

    def _on_mpv_log(self, loglevel: str, component: str, message: str) -> None:
        logger.warning("mpv[%s] %s: %s", loglevel, component, message)

    # --- transporte ------------------------------------------------------

    def load(self, path: str) -> None:
        self._current_path = path
        self.mpv.play(path)
        self.mpv.pause = False

    def is_loaded(self) -> bool:
        return self._current_path is not None

    def seek_absolute(self, position_seconds: float) -> None:
        if self._current_path is None:
            return
        try:
            self.mpv.command("seek", position_seconds, "absolute")
        except Exception:
            logger.exception("seek falló en %s", self._current_path)

    def toggle_pause(self) -> None:
        self.mpv.pause = not self.mpv.pause

    @property
    def position_seconds(self) -> float:
        try:
            return float(self.mpv.time_pos or 0.0)
        except Exception:
            return 0.0

    # --- pistas de audio ---------------------------------------------------

    @property
    def track_list(self) -> list[dict]:
        """Pistas tal como las ve mpv. Cada entrada trae ``id`` (el que espera
        ``aid``) y, para demuxers que lo exponen, ``ff-index`` — el índice de
        stream de ffmpeg, que es el puente para mapear contra ``tracks.stream_index``
        de la DB (mpv numera las pistas a su manera, no como ffprobe/mkvmerge)."""
        try:
            return list(self.mpv.track_list)
        except Exception:
            return []

    def set_audio_track(self, mpv_track_id: int) -> None:
        try:
            self.mpv.aid = mpv_track_id
        except Exception:
            logger.exception("no se pudo cambiar a la pista de audio mpv id=%s", mpv_track_id)

    def add_external_audio(self, path: str) -> None:
        """Carga un candidato externo sobre el video, sin muxear nada (spec 5.6)."""
        try:
            self.mpv.command("audio-add", path, "select")
        except Exception:
            logger.exception("no se pudo cargar el candidato externo %s", path)

    # --- delay ---------------------------------------------------------------

    def set_audio_delay(self, seconds: float) -> None:
        try:
            self.mpv.audio_delay = seconds
        except Exception:
            logger.exception("no se pudo aplicar audio_delay=%s", seconds)

    def get_audio_delay(self) -> float:
        try:
            return float(self.mpv.audio_delay)
        except Exception:
            return 0.0

    # --- ciclo de vida -----------------------------------------------------

    def terminate(self) -> None:
        """Hay que llamarlo al cerrar la ventana o el proceso de mpv queda
        colgado en Windows (trampa #8)."""
        if self._terminated:
            return
        self._terminated = True
        try:
            self.mpv.terminate()
        except Exception:
            logger.exception("mpv.terminate() falló")

    def closeEvent(self, event) -> None:  # noqa: N802 (override de Qt)
        self.terminate()
        super().closeEvent(event)
