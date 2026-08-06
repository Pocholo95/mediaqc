"""Cliente de TMDB API v3 (spec sección 5.4).

Sin un solo import de PySide6. Sin API key configurada, todo devuelve
resultados vacíos sin lanzar — la app tiene que poder funcionar igual, solo
sin metadatos (spec sección 3/5.4). Cachea todo en disco con TTL de 30 días
para no repegarle a la API en cada escaneo, respeta un límite de 20
requests/segundo, y reintenta con backoff exponencial ante 429/5xx.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from mediaqc.core.db import repo
from mediaqc.core.db.models import Series
from mediaqc.core.scanner import parse_tmdb_id_hint

logger = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w342"
CACHE_TTL_SECONDS = 30 * 24 * 3600
MAX_REQUESTS_PER_SECOND = 20
MAX_RETRIES = 4


class TmdbError(Exception):
    pass


class _RateLimiter:
    """Como mucho ``max_per_second`` requests en cualquier ventana de 1s."""

    def __init__(self, max_per_second: int) -> None:
        self.max_per_second = max_per_second
        self._timestamps: list[float] = []

    def wait(self) -> None:
        now = time.monotonic()
        self._timestamps = [t for t in self._timestamps if now - t < 1.0]
        if len(self._timestamps) >= self.max_per_second:
            sleep_for = 1.0 - (now - self._timestamps[0])
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._timestamps.append(time.monotonic())


@dataclass
class TmdbSeriesResult:
    tmdb_id: int
    name: str
    first_air_year: int | None
    poster_path: str | None
    popularity: float


def _year_from_date(date_str: str | None) -> int | None:
    if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
        return int(date_str[:4])
    return None


class TmdbClient:
    def __init__(self, api_key: str, language: str, cache_dir: Path) -> None:
        self.api_key = api_key
        self.language = language or "es-MX"
        self.cache_dir = cache_dir
        self.posters_dir = cache_dir / "posters"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.posters_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(timeout=15.0)
        self._rate_limiter = _RateLimiter(MAX_REQUESTS_PER_SECOND)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TmdbClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # --- caché en disco -------------------------------------------------

    def _cache_path(self, cache_key: str) -> Path:
        digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _cache_get(self, cache_key: str) -> dict | None:
        path = self._cache_path(cache_key)
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - payload.get("cached_at", 0) > CACHE_TTL_SECONDS:
            return None
        return payload.get("data")

    def _cache_set(self, cache_key: str, data: dict) -> None:
        path = self._cache_path(cache_key)
        with path.open("w", encoding="utf-8") as fh:
            json.dump({"cached_at": time.time(), "data": data}, fh, ensure_ascii=False)

    # --- HTTP -------------------------------------------------------------

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        if not self.enabled:
            raise TmdbError("TMDB no está configurado (falta API key)")

        params = dict(params or {})
        params["api_key"] = self.api_key
        params.setdefault("language", self.language)
        cache_key = endpoint + "?" + json.dumps(params, sort_keys=True)

        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        url = f"{BASE_URL}{endpoint}"
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self._rate_limiter.wait()
            try:
                resp = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_exc = exc
                time.sleep(2**attempt)
                continue

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 2**attempt))
                time.sleep(retry_after)
                continue
            if resp.status_code >= 500:
                time.sleep(2**attempt)
                continue
            if resp.status_code >= 400:
                raise TmdbError(f"TMDB {resp.status_code} en {endpoint}: {resp.text[:200]}")

            data = resp.json()
            self._cache_set(cache_key, data)
            return data

        raise TmdbError(f"TMDB no respondió tras {MAX_RETRIES} reintentos en {endpoint}: {last_exc}")

    # --- endpoints ----------------------------------------------------------

    def search_tv(self, query: str, year: int | None = None) -> list[TmdbSeriesResult]:
        if not query.strip():
            return []
        params: dict = {"query": query}
        if year:
            params["first_air_date_year"] = year
        data = self._get("/search/tv", params)
        return [
            TmdbSeriesResult(
                tmdb_id=item["id"],
                name=item.get("name", ""),
                first_air_year=_year_from_date(item.get("first_air_date")),
                poster_path=item.get("poster_path"),
                popularity=item.get("popularity", 0.0),
            )
            for item in data.get("results", [])
        ]

    def get_tv_details(self, tmdb_id: int) -> dict:
        return self._get(f"/tv/{tmdb_id}")

    def get_season_details(self, tmdb_id: int, season_number: int) -> dict:
        return self._get(f"/tv/{tmdb_id}/season/{season_number}")

    def download_poster(self, poster_path: str | None) -> Path | None:
        if not poster_path:
            return None
        local_path = self.posters_dir / poster_path.lstrip("/")
        if local_path.is_file():
            return local_path
        self._rate_limiter.wait()
        try:
            resp = self._client.get(f"{IMAGE_BASE_URL}{poster_path}")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("no se pudo descargar poster %s: %s", poster_path, exc)
            return None
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(resp.content)
        return local_path


# --- matching de series -----------------------------------------------------

# Umbrales de scoring: título exacto + año exacto = 16 (bien por encima del
# piso de auto-match). Con esto un match "obvio" nunca queda ambiguo, pero
# cualquier caso dudoso (título parcial, año ausente, varios resultados
# parecidos) cae a selección manual en vez de adivinar.
_AUTO_MATCH_MIN_SCORE = 8.0
_AUTO_MATCH_MIN_MARGIN = 4.0


def score_candidate(candidate: TmdbSeriesResult, folder_title: str, folder_year: int | None) -> float:
    score = 0.0
    name = candidate.name.strip().lower()
    title = folder_title.strip().lower()

    if name == title:
        score += 10.0
    elif title in name or name in title:
        score += 4.0

    if folder_year is not None and candidate.first_air_year is not None:
        if candidate.first_air_year == folder_year:
            score += 6.0
        elif abs(candidate.first_air_year - folder_year) <= 1:
            score += 2.0

    score += min(candidate.popularity, 50.0) / 50.0
    return score


def find_best_match(
    results: list[TmdbSeriesResult], folder_title: str, folder_year: int | None
) -> tuple[TmdbSeriesResult | None, list[TmdbSeriesResult]]:
    """Devuelve ``(match_automático_o_None, candidatos_ordenados_por_score)``.

    Solo hay match automático si el mejor resultado supera un piso de
    confianza Y le saca margen claro al segundo — si no, mejor preguntarle
    al usuario que adivinar mal (spec sección 5.4).
    """
    if not results:
        return None, []

    scored = sorted(results, key=lambda r: score_candidate(r, folder_title, folder_year), reverse=True)
    top_score = score_candidate(scored[0], folder_title, folder_year)
    second_score = score_candidate(scored[1], folder_title, folder_year) if len(scored) > 1 else -999.0

    if top_score >= _AUTO_MATCH_MIN_SCORE and (top_score - second_score) >= _AUTO_MATCH_MIN_MARGIN:
        return scored[0], scored
    return None, scored


# --- orquestación: aplicar un match a la DB ----------------------------------


def apply_series_match(session: Session, client: TmdbClient, series: Series, tmdb_id: int, status: str) -> None:
    """Trae detalles + temporadas de TMDB y los guarda contra ``series``.

    Uso real más allá de lo cosmético (spec sección 5.4): guarda
    ``tmdb_episode_count`` por temporada (temporadas incompletas) y
    ``tmdb_runtime_min`` por episodio (contra el que se compara la duración
    real para detectar episodios equivocados/truncados).
    """
    details = client.get_tv_details(tmdb_id)
    poster_path = details.get("poster_path")
    local_poster = client.download_poster(poster_path)

    repo.update_series_tmdb(
        session,
        series,
        tmdb_id=tmdb_id,
        status=status,
        title=details.get("name") or None,
        year=_year_from_date(details.get("first_air_date")),
        poster_path=str(local_poster) if local_poster else None,
    )

    if series.reference_language is None:
        # Semilla inicial de la pista de referencia (spec 5.3b): la pista
        # que vino con el video del BD está en el idioma original, no en el
        # que TMDB marca como default de la ficha. El usuario la puede
        # sobrescribir una vez y queda para toda la serie.
        series.reference_language = details.get("original_language") or None

    for season in series.seasons:
        try:
            season_data = client.get_season_details(tmdb_id, season.number)
        except TmdbError as exc:
            logger.warning("no se pudo traer temporada %s de %s: %s", season.number, series.folder_name, exc)
            continue

        episodes_data = season_data.get("episodes", [])
        repo.update_season_tmdb_count(session, season, len(episodes_data))

        by_number = {ep["episode_number"]: ep for ep in episodes_data if "episode_number" in ep}
        for episode in season.episodes:
            ep_data = by_number.get(episode.number)
            if ep_data is None:
                continue
            repo.update_episode_tmdb(
                session,
                episode,
                title=ep_data.get("name") or None,
                air_date=ep_data.get("air_date") or None,
                runtime_min=ep_data.get("runtime"),
            )


def ensure_reference_language(session: Session, client: TmdbClient, series: Series) -> None:
    """Rellena ``reference_language`` en series que ya tienen ``tmdb_id`` de
    antes de que existiera este campo (ver migración en ``repo.create_db_engine``).
    Sin esto, ``core/pairs.py`` cae al fallback de ``is_default`` -- que en
    muchos rips es el doblaje, no el idioma original, y arruina la elección
    de referencia para el analizador."""
    if series.reference_language is not None:
        return
    if not series.tmdb_id or not client.enabled:
        return
    try:
        details = client.get_tv_details(series.tmdb_id)
    except TmdbError as exc:
        logger.warning("no se pudo traer original_language de %s: %s", series.folder_name, exc)
        return
    series.reference_language = details.get("original_language") or None


def sync_new_series(session: Session, client: TmdbClient, series: Series) -> str:
    """Busca, decide auto-match o marca ``unmatched``, y aplica si corresponde.

    Devuelve el ``tmdb_status`` resultante para que el caller lleve estadísticas.
    """
    if not client.enabled:
        return series.tmdb_status or "unmatched"

    hint = parse_tmdb_id_hint(series.folder_name)
    if hint is not None:
        try:
            apply_series_match(session, client, series, hint, status="auto")
            return "auto"
        except TmdbError as exc:
            logger.warning(
                "el tmdb id %s del nombre de carpeta '%s' no funcionó (%s), cae a búsqueda por título",
                hint,
                series.folder_name,
                exc,
            )

    query = series.title or series.folder_name
    results = client.search_tv(query, series.year)
    match, _candidates = find_best_match(results, query, series.year)

    if match is not None:
        apply_series_match(session, client, series, match.tmdb_id, status="auto")
        return "auto"

    repo.mark_series_tmdb_status(session, series, "unmatched")
    return "unmatched"
