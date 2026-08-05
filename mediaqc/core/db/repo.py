"""Repositorio: toda la escritura/lectura de SQLite pasa por acá.

Funciones puras sobre una ``Session`` de SQLAlchemy — nada de PySide6. El
caller (``ui/workers.py``) decide cuándo abrir sesión y hacer commit.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload, sessionmaker

from mediaqc.core.db.models import Analysis, Base, Episode, Job, Review, Season, Series, Track
from mediaqc.core.probe import ProbeResult
from mediaqc.core.scanner import ScannedEpisode, ScanResult

# Vocabulario cerrado de veredictos (spec sección 4). "otro" exige nota
# obligatoria -- lo hace cumplir la UI, no este módulo.
VALID_VERDICTS = {
    "ok",
    "sync_constante",
    "sync_drift",
    "sync_segmentado",
    "audio_faltante",
    "audio_incompleto",
    "subs_faltantes",
    "episodio_equivocado",
    "calidad_audio",
    "pista_duplicada",
    "otro",
}

# "analizado": recién probado, todavía no pasó por el analizador de audio
# (fase 4, no existe todavía). "pendiente_revision": ya tiene un veredicto
# automático esperando confirmación humana. Sin analizador, ambos significan
# lo mismo para el semáforo y la cola de revisión: "listo para que lo mire
# un humano".
REVIEW_ELIGIBLE_STATUSES = {"analizado", "pendiente_revision"}

SEMAPHORE_COLORS = {
    "sin_analizar": "gray",
    "analizado": "amber",
    "pendiente_revision": "amber",
    "aprobado": "green",
    "problema": "red",
    "corregido": "blue",
}


def create_db_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


# --- series / seasons / episodes --------------------------------------------------


def upsert_series(session: Session, path: Path, folder_name: str, title: str, year: int | None) -> Series:
    series = session.execute(select(Series).where(Series.path == str(path))).scalar_one_or_none()
    if series is None:
        series = Series(path=str(path), folder_name=folder_name, title=title, year=year)
        session.add(series)
        session.flush()
        return series

    series.folder_name = folder_name
    if series.tmdb_status is None:
        # todavía no hay metadata de TMDB pisando el título: se puede actualizar libre
        series.title = title
        series.year = year
    return series


def upsert_season(session: Session, series_id: int, number: int) -> Season:
    season = session.execute(
        select(Season).where(Season.series_id == series_id, Season.number == number)
    ).scalar_one_or_none()
    if season is None:
        season = Season(series_id=series_id, number=number)
        session.add(season)
        session.flush()
    return season


def upsert_episode(session: Session, season_id: int, scanned: ScannedEpisode) -> tuple[Episode, bool]:
    """Devuelve ``(episode, changed)``. ``changed`` dispara re-probe/re-análisis.

    Se busca por ``path`` (identidad estable del archivo físico), no por
    ``(season_id, number)``: si un escaneo posterior reparenta el archivo a
    otra temporada/serie (p.ej. al corregir una detección de carpetas mal
    hecha), esto lo re-parenta en vez de intentar insertar una fila
    duplicada y romper la unicidad de ``episodes.path``.
    """
    episode = session.execute(
        select(Episode).where(Episode.path == str(scanned.path))
    ).scalar_one_or_none()

    if episode is None:
        episode = Episode(
            season_id=season_id,
            number=scanned.number,
            path=str(scanned.path),
            file_size=scanned.file_size,
            mtime=scanned.mtime,
            file_hash=scanned.file_hash,
            status="sin_analizar",
            missing=False,
            last_scanned_at=_dt.datetime.now(_dt.timezone.utc),
        )
        session.add(episode)
        session.flush()
        return episode, True

    changed = episode.file_hash != scanned.file_hash
    episode.season_id = season_id
    episode.number = scanned.number
    episode.file_size = scanned.file_size
    episode.mtime = scanned.mtime
    episode.missing = False
    episode.last_scanned_at = _dt.datetime.now(_dt.timezone.utc)

    if changed:
        episode.file_hash = scanned.file_hash
        episode.status = "sin_analizar"
        session.execute(delete(Analysis).where(Analysis.episode_id == episode.id))

    return episode, changed


def apply_scan_result(session: Session, scan_result: ScanResult) -> dict:
    """Vuelca un ``ScanResult`` a la DB: upsert de series/temporadas/episodios y
    marcado de ausentes. Nunca borra filas de ``episodes`` (spec sección 5.1)."""
    seen_paths: set[str] = set()
    changed_episode_ids: list[int] = []
    new_count = 0

    for scanned_series in scan_result.series:
        series = upsert_series(
            session,
            path=scanned_series.path,
            folder_name=scanned_series.folder_name,
            title=scanned_series.title,
            year=scanned_series.year,
        )
        seasons_seen: dict[int, int] = {}
        for scanned_ep in scanned_series.episodes:
            season_id = seasons_seen.get(scanned_ep.season)
            if season_id is None:
                season = upsert_season(session, series.id, scanned_ep.season)
                season_id = season.id
                seasons_seen[scanned_ep.season] = season_id

            episode, changed = upsert_episode(session, season_id, scanned_ep)
            seen_paths.add(str(scanned_ep.path))
            if changed:
                changed_episode_ids.append(episode.id)
                new_count += 1

    removed_empty_series = cleanup_empty_series(session)

    return {
        "seen_paths": seen_paths,
        "changed_episode_ids": changed_episode_ids,
        "changed_count": new_count,
        "removed_empty_series": removed_empty_series,
    }


def cleanup_empty_series(session: Session) -> int:
    """Borra series sin ningún episodio (ninguna temporada tiene filas).

    No borra episodios (spec sección 5.1): esto solo limpia contenedores
    vacíos, típicamente series/temporadas fantasma que quedan cuando un
    escaneo posterior reparenta sus episodios a la serie correcta (ver
    ``upsert_episode``), o carpetas que nunca tuvieron video reconocible.
    """
    stmt = select(Series).options(selectinload(Series.seasons).selectinload(Season.episodes))
    removed = 0
    for series in session.execute(stmt).unique().scalars().all():
        total_episodes = sum(len(season.episodes) for season in series.seasons)
        if total_episodes == 0:
            session.delete(series)
            removed += 1
    return removed


def mark_missing_episodes(session: Session, reachable_media_paths: list[Path], seen_paths: set[str]) -> int:
    """Marca ``missing=True`` en episodios cuyo archivo desapareció, mirando
    solo dentro de rutas que efectivamente se pudieron escanear (si un disco
    está desconectado, no se toca nada de lo que había bajo esa ruta)."""
    if not reachable_media_paths:
        return 0
    roots = [str(Path(p)) for p in reachable_media_paths]
    episodes = session.execute(select(Episode)).scalars().all()
    count = 0
    for ep in episodes:
        under_reachable_root = any(ep.path.startswith(root) for root in roots)
        if not under_reachable_root:
            continue
        should_be_missing = ep.path not in seen_paths
        if should_be_missing and not ep.missing:
            ep.missing = True
            count += 1
        elif not should_be_missing and ep.missing:
            ep.missing = False
    return count


def save_probe_result(session: Session, episode: Episode, probe_result: ProbeResult) -> None:
    episode.duration_s = probe_result.duration_s
    episode.video_fps = probe_result.video_fps
    episode.container = probe_result.container

    session.execute(delete(Track).where(Track.episode_id == episode.id))
    for t in probe_result.tracks:
        session.add(
            Track(
                episode_id=episode.id,
                stream_index=t.stream_index,
                mkv_track_id=t.mkv_track_id,
                type=t.type,
                codec=t.codec,
                language=t.language,
                title=t.title,
                channels=t.channels,
                is_default=t.is_default,
                is_forced=t.is_forced,
                container_delay_ms=t.container_delay_ms,
            )
        )


# --- consultas para la UI -----------------------------------------------------


def list_series_tree(session: Session) -> list[Series]:
    stmt = (
        select(Series)
        .options(selectinload(Series.seasons).selectinload(Season.episodes))
        .order_by(Series.title)
    )
    return list(session.execute(stmt).unique().scalars().all())


def list_episodes_for_season(session: Session, season_id: int) -> list[Episode]:
    stmt = (
        select(Episode)
        .where(Episode.season_id == season_id)
        .options(selectinload(Episode.tracks))
        .order_by(Episode.number)
    )
    return list(session.execute(stmt).unique().scalars().all())


def list_all_episodes(session: Session) -> list[Episode]:
    stmt = select(Episode).options(
        selectinload(Episode.tracks),
        selectinload(Episode.season).selectinload(Season.series),
    )
    return list(session.execute(stmt).unique().scalars().all())


def episode_has_audio_language(episode: Episode, language_codes: set[str]) -> bool:
    return any(
        t.type == "audio" and (t.language or "").lower() in language_codes for t in episode.tracks
    )


def episode_runtime_deviates(episode: Episode, threshold: float = 0.15) -> bool:
    """Desviación > 15% entre duración real y runtime oficial de TMDB (sección 5.4):
    señal de episodio equivocado, versión extendida, o archivo truncado."""
    if not episode.duration_s or not episode.tmdb_runtime_min:
        return False
    expected_s = episode.tmdb_runtime_min * 60
    if expected_s <= 0:
        return False
    return abs(episode.duration_s - expected_s) / expected_s > threshold


# --- TMDB -------------------------------------------------------------------


def update_series_tmdb(
    session: Session,
    series: Series,
    tmdb_id: int,
    status: str,
    title: str | None = None,
    year: int | None = None,
    poster_path: str | None = None,
) -> None:
    series.tmdb_id = tmdb_id
    series.tmdb_status = status
    if title:
        series.title = title
    if year is not None:
        series.year = year
    if poster_path is not None:
        series.poster_path = poster_path


def mark_series_tmdb_status(session: Session, series: Series, status: str) -> None:
    series.tmdb_status = status


def update_season_tmdb_count(session: Session, season: Season, episode_count: int) -> None:
    season.tmdb_episode_count = episode_count


def update_episode_tmdb(
    session: Session,
    episode: Episode,
    title: str | None,
    air_date: str | None,
    runtime_min: int | None,
) -> None:
    episode.tmdb_title = title
    episode.tmdb_air_date = air_date
    episode.tmdb_runtime_min = runtime_min


def list_series_needing_tmdb_sync(session: Session) -> list[Series]:
    """Series que todavía no pasaron por ningún intento de matching."""
    stmt = select(Series).where(Series.tmdb_status.is_(None)).order_by(Series.folder_name)
    return list(session.execute(stmt).scalars().all())


def list_series_pending_manual_tmdb(session: Session) -> list[Series]:
    """Series ambiguas o previamente omitidas: necesitan que el usuario elija."""
    stmt = (
        select(Series)
        .where(Series.tmdb_status.in_(("unmatched", "skipped")))
        .order_by(Series.folder_name)
    )
    return list(session.execute(stmt).scalars().all())


# --- reviews / estados -------------------------------------------------------


def record_review(
    session: Session,
    episode: Episode,
    verdict: str,
    timestamp_ms: int | None = None,
    note: str | None = None,
) -> Review:
    """Agrega una fila a ``reviews`` (append-only, es el historial) y mueve el
    estado del episodio. ``ok`` aprueba; cualquier otro veredicto es un
    problema a resolver (spec sección 4). El analizador automático nunca
    otorga ``aprobado`` — solo una review humana lo hace (trampa #10)."""
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"veredicto desconocido: {verdict}")

    review = Review(episode_id=episode.id, verdict=verdict, timestamp_ms=timestamp_ms, note=note)
    session.add(review)
    episode.status = "aprobado" if verdict == "ok" else "problema"
    return review


def list_episode_reviews(session: Session, episode_id: int) -> list[Review]:
    stmt = select(Review).where(Review.episode_id == episode_id).order_by(Review.created_at.desc())
    return list(session.execute(stmt).scalars().all())


def list_review_queue(session: Session, season_id: int | None = None) -> list[Episode]:
    """Episodios listos para que un humano los revise (Vista 2)."""
    stmt = (
        select(Episode)
        .where(Episode.status.in_(REVIEW_ELIGIBLE_STATUSES), Episode.missing.is_(False))
        .options(
            selectinload(Episode.tracks),
            selectinload(Episode.season).selectinload(Season.series),
        )
        .order_by(Episode.number)
    )
    if season_id is not None:
        stmt = stmt.where(Episode.season_id == season_id)
    return list(session.execute(stmt).unique().scalars().all())


def list_problem_episodes(session: Session) -> list[Episode]:
    """Vista 3: lista plana de todo lo que está en ``problema``."""
    stmt = (
        select(Episode)
        .where(Episode.status == "problema")
        .options(
            selectinload(Episode.season).selectinload(Season.series),
            selectinload(Episode.reviews),
        )
        .order_by(Episode.number)
    )
    return list(session.execute(stmt).unique().scalars().all())


# --- jobs -----------------------------------------------------------------------


def create_job(session: Session, kind: str, target_id: int | None = None) -> Job:
    job = Job(kind=kind, target_id=target_id, state="pending", progress=0.0)
    session.add(job)
    session.flush()
    return job


def update_job(
    session: Session,
    job: Job,
    state: str | None = None,
    progress: float | None = None,
    message: str | None = None,
) -> None:
    if state is not None:
        job.state = state
        if state in ("done", "failed"):
            job.finished_at = _dt.datetime.now(_dt.timezone.utc)
    if progress is not None:
        job.progress = progress
    if message is not None:
        job.message = message


def list_pending_jobs(session: Session) -> list[Job]:
    stmt = select(Job).where(Job.state.in_(("pending", "running")))
    return list(session.execute(stmt).scalars().all())
