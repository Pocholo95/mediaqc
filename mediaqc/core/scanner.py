"""Escaneo de la librería: descubre series, temporadas y episodios en disco.

Sin GUI, sin red. Solo filesystem. El parseo de nombres sigue el orden de
reglas de la spec (sección 5.1): se prueba cada regla en orden y se usa la
primera que matchea; si ninguna matchea, el archivo va a la lista de "no
parseables" — nunca se adivina.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".wmv"}

_RE_SXXEXX = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})")
_RE_NXM = re.compile(r"(?<!\d)(\d{1,2})[xX](\d{1,3})(?!\d)")
_RE_SEASON_FOLDER = re.compile(r"(?:season|temporada)\s*0*(\d{1,3})", re.IGNORECASE)
# Incluye '.' como separador además de espacio/guion/underscore: los release
# groups suelen nombrar episodios con puntos en vez de espacios
# ("Serie.S01.01.Titulo.mkv"), y la spec pide manejarlos (sección 9).
_RE_BARE_NUMBER = re.compile(r"(?:^|[ \-_.])0*(\d{1,3})(?:[ \-_.]|$)")
_RE_SERIES_YEAR = re.compile(r"^(.*?)\s*\((\d{4})\)\s*$")
# Tags de scraper estilo Jellyfin/Kodi/Emby pegados al nombre de carpeta:
# "Serie (2019) [tmdbid-88046]", "Serie (2019) {tmdb-88046}", "[imdbid-tt123]".
_RE_SCRAPER_TAG = re.compile(r"[\[\{][^\[\]\{\}]*[\]\}]")
_RE_TMDB_ID_HINT = re.compile(r"tmdb(?:id)?-(\d+)", re.IGNORECASE)


def parse_filename_sxxexx(name: str) -> tuple[int, int] | None:
    m = _RE_SXXEXX.search(name)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_filename_nxm(name: str) -> tuple[int, int] | None:
    m = _RE_NXM.search(name)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_season_folder(name: str) -> int | None:
    m = _RE_SEASON_FOLDER.search(name)
    if not m:
        return None
    return int(m.group(1))


def parse_filename_bare_number(name: str) -> int | None:
    stem = Path(name).stem
    m = _RE_BARE_NUMBER.search(stem)
    if not m:
        return None
    return int(m.group(1))


def strip_scraper_tags(name: str) -> str:
    """Saca sufijos de metadata tipo ``[tmdbid-88046]``/``{tmdb-88046}``/
    ``[imdbid-tt123]`` que Jellyfin/Kodi/Emby agregan al nombre de carpeta.
    Sin esto, esos tags terminan pegados al título y ensucian tanto el año
    parseado como la búsqueda en TMDB."""
    cleaned = _RE_SCRAPER_TAG.sub("", name).strip()
    return cleaned or name.strip()


def parse_tmdb_id_hint(name: str) -> int | None:
    """El propio nombre de carpeta a veces ya trae el tmdb id (convención
    Jellyfin/Kodi). Si está, es una señal mucho más confiable que buscar por
    título — se usa para saltear la búsqueda difusa por completo."""
    m = _RE_TMDB_ID_HINT.search(name)
    if not m:
        return None
    return int(m.group(1))


def parse_series_folder(name: str) -> tuple[str, int | None]:
    """``"Nombre (2005)"`` -> ``("Nombre", 2005)``. Sin año, el nombre tal cual."""
    cleaned = strip_scraper_tags(name)
    m = _RE_SERIES_YEAR.match(cleaned)
    if m:
        return m.group(1).strip(), int(m.group(2))
    return cleaned.strip(), None


def parse_episode(path: Path, series_root: Path) -> tuple[int, int] | None:
    """Devuelve ``(temporada, episodio)`` o ``None`` si no matchea ninguna regla."""
    name = path.name

    m = parse_filename_sxxexx(name)
    if m is not None:
        return m

    m = parse_filename_nxm(name)
    if m is not None:
        return m

    season = None
    cur = path.parent
    while True:
        season = parse_season_folder(cur.name)
        if season is not None:
            break
        if cur == series_root:
            break
        cur = cur.parent
    if season is not None:
        episode = parse_filename_bare_number(name)
        if episode is not None:
            return season, episode

    return None


def compute_file_hash(path: Path, size: int, mtime: float) -> str:
    return hashlib.sha1(f"{path}|{size}|{mtime}".encode("utf-8")).hexdigest()


@dataclass
class ScannedEpisode:
    path: Path
    season: int
    number: int
    file_size: int
    mtime: float
    file_hash: str


@dataclass
class ScannedSeries:
    path: Path
    folder_name: str
    title: str
    year: int | None
    episodes: list[ScannedEpisode] = field(default_factory=list)


@dataclass
class ScanResult:
    series: list[ScannedSeries] = field(default_factory=list)
    unparseable: list[Path] = field(default_factory=list)
    unreachable_media_paths: list[Path] = field(default_factory=list)
    reachable_media_paths: list[Path] = field(default_factory=list)

    def all_episode_paths(self) -> set[str]:
        return {str(ep.path) for s in self.series for ep in s.episodes}


def _series_dirs_under(media_path: Path) -> list[Path]:
    """Subcarpetas de ``media_path`` que hay que tratar como series.

    Normalmente cada hijo directo de ``media_path`` es una serie distinta.
    Pero si a ``media_path`` le apuntaron directo a la carpeta de UNA sola
    serie (todos sus hijos son carpetas "Season NN"/"Temporada NN"), tratar
    cada temporada como una "serie" separada rompe el catálogo: el nombre de
    la serie termina siendo "Season 01", y TMDB obviamente no encuentra nada
    con ese nombre. En ese caso, ``media_path`` completo es la serie.
    """
    children = sorted(p for p in media_path.iterdir() if p.is_dir())
    if children and all(parse_season_folder(c.name) is not None for c in children):
        return [media_path]
    return children


def scan_media_paths(
    media_paths: Iterable[Path | str],
    progress_cb: Callable[[str], None] | None = None,
) -> ScanResult:
    result = ScanResult()

    for raw_path in media_paths:
        media_path = Path(raw_path)
        if not media_path.is_dir():
            result.unreachable_media_paths.append(media_path)
            continue

        result.reachable_media_paths.append(media_path)

        for series_dir in _series_dirs_under(media_path):
            title, year = parse_series_folder(series_dir.name)
            scanned_series = ScannedSeries(
                path=series_dir, folder_name=series_dir.name, title=title, year=year
            )

            for file_path in sorted(series_dir.rglob("*")):
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                if progress_cb:
                    progress_cb(str(file_path))

                parsed = parse_episode(file_path, series_dir)
                if parsed is None:
                    result.unparseable.append(file_path)
                    continue

                season, number = parsed
                stat = file_path.stat()
                file_hash = compute_file_hash(file_path, stat.st_size, stat.st_mtime)
                scanned_series.episodes.append(
                    ScannedEpisode(
                        path=file_path,
                        season=season,
                        number=number,
                        file_size=stat.st_size,
                        mtime=stat.st_mtime,
                        file_hash=file_hash,
                    )
                )

            result.series.append(scanned_series)

    return result
