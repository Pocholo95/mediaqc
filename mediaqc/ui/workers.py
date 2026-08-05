"""QRunnable que envuelven las funciones de core/ para correr en QThreadPool.

La GUI nunca bloquea (spec sección 6/9): cualquier escaneo, probe o llamada
de red va acá, nunca en el hilo principal. La comunicación de vuelta a la UI
es por señales Qt.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from mediaqc.core import probe, scanner
from mediaqc.core.db import repo
from mediaqc.core.db.models import Episode, Job

logger = logging.getLogger(__name__)


class WorkerSignals(QObject):
    progress = Signal(str, int, int)  # mensaje, actual, total (total=0 => indeterminado)
    finished = Signal(dict)
    error = Signal(str)


class ScanWorker(QRunnable):
    """Escanea media_paths, vuelca a la DB, y hace probe de lo nuevo/cambiado."""

    def __init__(
        self,
        session_factory,
        media_paths: list[str],
        ffprobe_bin: Path | None,
        mkvmerge_bin: Path | None,
    ) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self.session_factory = session_factory
        self.media_paths = media_paths
        self.ffprobe_bin = ffprobe_bin
        self.mkvmerge_bin = mkvmerge_bin
        self._cancel_event = threading.Event()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancel_event.set()

    def _on_walk_progress(self, path_str: str) -> None:
        self.signals.progress.emit(f"Escaneando: {Path(path_str).name}", 0, 0)

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:  # nunca tumbar el thread pool ni la GUI
            logger.exception("scan worker failed")
            self.signals.error.emit(str(exc))

    def _run(self) -> None:
        if self.ffprobe_bin is None:
            self.signals.error.emit(
                "No se encontró ffprobe. Configuralo en Preferencias o instalalo en el PATH."
            )
            return

        with self.session_factory() as session:
            job = repo.create_job(session, kind="scan")
            session.commit()
            job_id = job.id

        try:
            self.signals.progress.emit("Escaneando archivos...", 0, 0)
            scan_result = scanner.scan_media_paths(self.media_paths, progress_cb=self._on_walk_progress)

            with self.session_factory() as session:
                job = session.get(Job, job_id)
                repo.update_job(session, job, state="running", message="Volcando a la base de datos")
                session.commit()

                stats = repo.apply_scan_result(session, scan_result)
                missing_count = repo.mark_missing_episodes(
                    session, scan_result.reachable_media_paths, stats["seen_paths"]
                )
                session.commit()

            changed_ids = stats["changed_episode_ids"]
            total = len(changed_ids)
            probe_errors = 0

            for i, ep_id in enumerate(changed_ids, start=1):
                if self._cancel_event.is_set():
                    break
                with self.session_factory() as session:
                    episode = session.get(Episode, ep_id)
                    if episode is None or episode.missing:
                        continue
                    self.signals.progress.emit(f"Analizando: {Path(episode.path).name}", i, total)
                    result = probe.probe_file(Path(episode.path), self.ffprobe_bin, self.mkvmerge_bin)
                    if result.error:
                        probe_errors += 1
                        logger.warning("probe failed for %s: %s", episode.path, result.error)
                    else:
                        repo.save_probe_result(session, episode, result)
                        episode.status = "analizado"
                    session.commit()

            cancelled = self._cancel_event.is_set()
            with self.session_factory() as session:
                job = session.get(Job, job_id)
                repo.update_job(
                    session,
                    job,
                    state="failed" if cancelled else "done",
                    progress=1.0,
                    message=(
                        f"{len(scan_result.series)} series, {total} episodios nuevos/cambiados, "
                        f"{missing_count} marcados ausentes, {probe_errors} errores de análisis"
                    ),
                )
                session.commit()

            self.signals.finished.emit(
                {
                    "series_count": len(scan_result.series),
                    "changed_count": total,
                    "missing_count": missing_count,
                    "probe_errors": probe_errors,
                    "unparseable": [str(p) for p in scan_result.unparseable],
                    "unreachable_media_paths": [str(p) for p in scan_result.unreachable_media_paths],
                    "cancelled": cancelled,
                }
            )
        except Exception as exc:
            with self.session_factory() as session:
                job = session.get(Job, job_id)
                repo.update_job(session, job, state="failed", message=str(exc))
                session.commit()
            raise
