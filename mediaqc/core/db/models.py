"""Esquema SQLAlchemy — mapea 1:1 con la spec sección 4.

Sin un solo import de PySide6 (regla de la sección 2). SQLite vía SQLAlchemy,
un archivo en el directorio de datos del usuario.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class Series(Base):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    folder_name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tmdb_status: Mapped[str | None] = mapped_column(String, nullable=True)  # unmatched|auto|manual|skipped
    poster_path: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_now)

    seasons: Mapped[list["Season"]] = relationship(
        back_populates="series", cascade="all, delete-orphan", order_by="Season.number"
    )


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("series_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"))
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    tmdb_episode_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    series: Mapped[Series] = relationship(back_populates="seasons")
    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="season", cascade="all, delete-orphan", order_by="Episode.number"
    )


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("season_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"))
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mtime: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    video_fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    container: Mapped[str | None] = mapped_column(String, nullable=True)
    tmdb_title: Mapped[str | None] = mapped_column(String, nullable=True)
    tmdb_air_date: Mapped[str | None] = mapped_column(String, nullable=True)
    tmdb_runtime_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="sin_analizar")
    missing: Mapped[bool] = mapped_column(Boolean, default=False)
    last_scanned_at: Mapped[_dt.datetime | None] = mapped_column(DateTime, nullable=True)

    season: Mapped[Season] = relationship(back_populates="episodes")
    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="episode", cascade="all, delete-orphan"
    )
    tracks: Mapped[list["Track"]] = relationship(
        back_populates="episode", cascade="all, delete-orphan"
    )
    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="episode", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="episode", cascade="all, delete-orphan", order_by="Review.created_at"
    )


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    path: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str | None] = mapped_column(String, nullable=True)  # audio|subtitle|video_con_audio
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mtime: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    matched_by: Mapped[str | None] = mapped_column(String, nullable=True)  # auto|manual
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    missing: Mapped[bool] = mapped_column(Boolean, default=False)

    episode: Mapped[Episode] = relationship(back_populates="candidates")
    tracks: Mapped[list["Track"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=True
    )
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=True
    )
    stream_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mkv_track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)  # video|audio|subtitle
    codec: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_default: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_forced: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    container_delay_ms: Mapped[int] = mapped_column(Integer, default=0)

    episode: Mapped[Episode | None] = relationship(back_populates="tracks")
    candidate: Mapped[Candidate | None] = relationship(back_populates="tracks")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"))
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=True
    )
    pair_source: Mapped[str] = mapped_column(String, nullable=False)  # internal|external
    ref_track_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cand_track_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    windows_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[str] = mapped_column(String, nullable=False)
    suggested_delay_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggested_resample_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    analyzed_at: Mapped[_dt.datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    episode: Mapped[Episode] = relationship(back_populates="analyses")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    verdict: Mapped[str] = mapped_column(String, nullable=False)
    timestamp_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_now)

    episode: Mapped[Episode] = relationship(back_populates="reviews")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str | None] = mapped_column(String, nullable=True)  # scan|probe|analyze|tmdb
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String, default="pending")  # pending|running|done|failed
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[_dt.datetime | None] = mapped_column(DateTime, nullable=True)
