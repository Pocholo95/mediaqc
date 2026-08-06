"""Escanea las ``candidates_paths`` y empareja audios/subtítulos sueltos con
episodios por número (spec sección 5.3) — el modo externo: doblajes o
subtítulos que todavía no se muxearon contra el BD rip.

Sin GUI, sin DB directa: recibe ``known_series`` (nombre normalizado ->
series_id) ya resuelto por el caller, y devuelve datos planos para que
``core/db/repo.py`` los vuelque. Reusa el mismo parser de nombres que
``core/scanner.py`` — misma lógica, mismos casos feos.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from mediaqc.core.scanner import parse_episode, parse_series_folder, series_dirs_under, strip_scraper_tags

CANDIDATE_EXTENSIONS = {
    "audio": {".mp3", ".ac3", ".dts", ".flac", ".wav", ".mka", ".m4a", ".aac", ".opus", ".eac3", ".thd", ".truehd", ".ogg"},
    "subtitle": {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx"},
}


def candidate_kind_for_extension(suffix: str) -> str | None:
    suffix = suffix.lower()
    for kind, extensions in CANDIDATE_EXTENSIONS.items():
        if suffix in extensions:
            return kind
    return None


def normalize_series_name(name: str) -> str:
    return strip_scraper_tags(name).strip().lower()


def build_series_lookup(series_rows: Iterable) -> dict[str, int]:
    """``series_rows``: objetos con ``.id``, ``.folder_name``, ``.title``.
    Varias claves (nombre de carpeta, título, título sin año) apuntan al
    mismo ``series_id`` -- sigue siendo comparación exacta de texto, no
    fuzzy matching (spec 5.3: "no agregar fuzzy matching... en la v1")."""
    lookup: dict[str, int] = {}
    for series in series_rows:
        for name in filter(None, (series.folder_name, series.title)):
            lookup.setdefault(normalize_series_name(name), series.id)
            title_only, _year = parse_series_folder(name)
            lookup.setdefault(normalize_series_name(title_only), series.id)
    return lookup


@dataclass
class ScannedCandidate:
    path: Path
    series_id: int
    season: int
    number: int
    kind: str
    file_size: int
    mtime: float


@dataclass
class CandidatesScanResult:
    candidates: list[ScannedCandidate] = field(default_factory=list)
    unparseable: list[Path] = field(default_factory=list)
    unmatched_series_dirs: list[Path] = field(default_factory=list)
    reachable_candidates_paths: list[Path] = field(default_factory=list)


def scan_candidate_paths(
    candidates_paths: Iterable[Path | str],
    known_series: dict[str, int],
    progress_cb: Callable[[str], None] | None = None,
) -> CandidatesScanResult:
    result = CandidatesScanResult()

    for raw_path in candidates_paths:
        root = Path(raw_path)
        if not root.is_dir():
            continue
        result.reachable_candidates_paths.append(root)

        for series_dir in series_dirs_under(root):
            series_id = known_series.get(normalize_series_name(series_dir.name))
            if series_id is None:
                result.unmatched_series_dirs.append(series_dir)
                continue

            for file_path in sorted(series_dir.rglob("*")):
                if not file_path.is_file():
                    continue
                kind = candidate_kind_for_extension(file_path.suffix)
                if kind is None:
                    continue
                if progress_cb:
                    progress_cb(str(file_path))

                parsed = parse_episode(file_path, series_dir)
                if parsed is None:
                    result.unparseable.append(file_path)
                    continue

                season, number = parsed
                stat = file_path.stat()
                result.candidates.append(
                    ScannedCandidate(
                        path=file_path,
                        series_id=series_id,
                        season=season,
                        number=number,
                        kind=kind,
                        file_size=stat.st_size,
                        mtime=stat.st_mtime,
                    )
                )

    return result
