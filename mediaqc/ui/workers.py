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

from mediaqc.core import pairs as pairs_module
from mediaqc.core import probe, scanner, tmdb
from mediaqc.core.analyzer import analyze, correlate
from mediaqc.core.db import repo
from mediaqc.core.db.models import Episode, Job, Series

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
                    "removed_empty_series": stats.get("removed_empty_series", 0),
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


class TmdbSyncWorker(QRunnable):
    """Pasada masiva: busca match para toda serie que todavía no lo tiene."""

    def __init__(self, session_factory, client_factory) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self.session_factory = session_factory
        self.client_factory = client_factory
        self._cancel_event = threading.Event()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:
            logger.exception("tmdb sync worker failed")
            self.signals.error.emit(str(exc))

    def _run(self) -> None:
        with self.session_factory() as session:
            series_ids = [s.id for s in repo.list_series_needing_tmdb_sync(session)]

        total = len(series_ids)
        if total == 0:
            self.signals.finished.emit({"auto": 0, "unmatched": 0, "errors": 0, "total": 0})
            return

        auto = unmatched = errors = 0
        with self.client_factory() as client:
            if not client.enabled:
                self.signals.error.emit(
                    "No hay API key de TMDB configurada. Configurala en Preferencias para traer metadatos."
                )
                return

            for i, series_id in enumerate(series_ids, start=1):
                if self._cancel_event.is_set():
                    break
                with self.session_factory() as session:
                    series = session.get(Series, series_id)
                    if series is None:
                        continue
                    self.signals.progress.emit(f"TMDB: {series.folder_name}", i, total)
                    try:
                        status = tmdb.sync_new_series(session, client, series)
                        session.commit()
                    except tmdb.TmdbError as exc:
                        errors += 1
                        logger.warning("tmdb sync failed for %s: %s", series.folder_name, exc)
                        session.rollback()
                        continue
                    if status == "auto":
                        auto += 1
                    else:
                        unmatched += 1

        self.signals.finished.emit(
            {"auto": auto, "unmatched": unmatched, "errors": errors, "total": total}
        )


class TmdbSearchWorker(QRunnable):
    """Búsqueda puntual de una serie en TMDB, con pósters ya descargados."""

    def __init__(self, client_factory, query: str, year: int | None) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self.client_factory = client_factory
        self.query = query
        self.year = year
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            with self.client_factory() as client:
                if not client.enabled:
                    self.signals.error.emit("No hay API key de TMDB configurada.")
                    return
                results = client.search_tv(self.query, self.year)
                payload = []
                for r in results[:12]:
                    poster = client.download_poster(r.poster_path)
                    payload.append(
                        {
                            "tmdb_id": r.tmdb_id,
                            "name": r.name,
                            "year": r.first_air_year,
                            "poster_local_path": str(poster) if poster else None,
                        }
                    )
            self.signals.finished.emit({"results": payload})
        except Exception as exc:
            logger.exception("tmdb search worker failed")
            self.signals.error.emit(str(exc))


class TmdbApplyWorker(QRunnable):
    """Aplica un tmdb_id elegido (a mano o automático) a una serie puntual."""

    def __init__(self, session_factory, client_factory, series_id: int, tmdb_id: int, status: str) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.series_id = series_id
        self.tmdb_id = tmdb_id
        self.status = status
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            with self.client_factory() as client:
                with self.session_factory() as session:
                    series = session.get(Series, self.series_id)
                    if series is None:
                        self.signals.error.emit("La serie ya no existe en la base de datos.")
                        return
                    tmdb.apply_series_match(session, client, series, self.tmdb_id, status=self.status)
                    session.commit()
            self.signals.finished.emit({"series_id": self.series_id})
        except Exception as exc:
            logger.exception("tmdb apply worker failed")
            self.signals.error.emit(str(exc))


class AnalyzeWorker(QRunnable):
    """Corre el analizador de sincronización (spec sección 5.5) sobre los
    episodios recién probados. Un episodio sin pares que analizar (una sola
    pista de audio, sin candidatos) no es un error: se salta y queda en
    'analizado' tal cual."""

    def __init__(
        self,
        session_factory,
        ffmpeg_bin: Path | None,
        audio_cache_dir: Path,
        window_seconds: int,
        sample_rate: int,
        tmdb_client_factory,
        audio_cache_limit_gb: float = 5.0,
    ) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self.session_factory = session_factory
        self.ffmpeg_bin = ffmpeg_bin
        self.audio_cache_dir = audio_cache_dir
        self.window_seconds = window_seconds
        self.sample_rate = sample_rate
        self.tmdb_client_factory = tmdb_client_factory
        self.audio_cache_limit_gb = audio_cache_limit_gb
        self._cancel_event = threading.Event()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:
            logger.exception("analyze worker failed")
            self.signals.error.emit(str(exc))

    def _run(self) -> None:
        if self.ffmpeg_bin is None:
            self.signals.error.emit(
                "No se encontró ffmpeg. Configuralo en Preferencias para poder analizar."
            )
            return

        with self.session_factory() as session:
            episode_ids = [e.id for e in repo.list_episodes_needing_analysis(session)]

        total = len(episode_ids)
        if total == 0:
            self.signals.finished.emit({"analyzed": 0, "no_pairs": 0, "errors": 0, "total": 0})
            return

        analyzed = no_pairs = errors = 0

        with self.tmdb_client_factory() as client:
            for i, episode_id in enumerate(episode_ids, start=1):
                if self._cancel_event.is_set():
                    break

                with self.session_factory() as session:
                    episode = session.get(Episode, episode_id)
                    if episode is None or episode.missing:
                        continue

                    series = episode.season.series if episode.season else None
                    if series is not None and client.enabled:
                        tmdb.ensure_reference_language(session, client, series)

                    reference_language = series.reference_language if series else None
                    ep_pairs = pairs_module.generate_internal_pairs(episode, reference_language)

                    if not ep_pairs:
                        no_pairs += 1
                        session.commit()
                        continue

                    self.signals.progress.emit(f"Analizando: {Path(episode.path).name}", i, total)

                    had_error = False
                    for pair in ep_pairs:
                        try:
                            result = analyze.analyze_pair(
                                self.ffmpeg_bin,
                                Path(episode.path),
                                episode.file_hash or "",
                                pair.ref_track_index,
                                pair.cand_track_index,
                                episode.duration_s or 0.0,
                                self.window_seconds,
                                self.sample_rate,
                                self.audio_cache_dir,
                            )
                        except Exception as exc:
                            logger.warning("análisis falló para %s: %s", episode.path, exc)
                            had_error = True
                            continue

                        windows_json = analyze.windows_to_json(result.windows)
                        repo.save_analysis(
                            session,
                            episode,
                            pair,
                            verdict=result.classification.verdict,
                            confidence=result.classification.confidence,
                            suggested_delay_ms=result.classification.suggested_delay_ms,
                            suggested_resample_ratio=result.classification.suggested_resample_ratio,
                            windows_json=windows_json,
                            source_hash=episode.file_hash or "",
                        )

                    episode.status = "pendiente_revision"
                    session.commit()

                    if had_error:
                        errors += 1
                    else:
                        analyzed += 1

        limit_bytes = int(self.audio_cache_limit_gb * 1024**3)
        correlate.purge_audio_cache(self.audio_cache_dir, limit_bytes)

        self.signals.finished.emit({"analyzed": analyzed, "no_pairs": no_pairs, "errors": errors, "total": total})
