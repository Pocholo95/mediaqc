from mediaqc.core.db.models import Episode, Season, Series
from mediaqc.core.tmdb import TmdbSeriesResult, find_best_match, score_candidate, sync_new_series


def _result(tmdb_id, name, year=None, popularity=1.0):
    return TmdbSeriesResult(tmdb_id=tmdb_id, name=name, first_air_year=year, poster_path=None, popularity=popularity)


def test_exact_title_and_year_auto_matches():
    results = [_result(1, "Cowboy Bebop", 1998), _result(2, "Cowboy Bebop: The Movie", 2001)]
    match, candidates = find_best_match(results, "Cowboy Bebop", 1998)
    assert match is not None
    assert match.tmdb_id == 1
    assert len(candidates) == 2


def test_no_results_returns_none_and_empty_candidates():
    match, candidates = find_best_match([], "Nada", None)
    assert match is None
    assert candidates == []


def test_ambiguous_homonyms_do_not_auto_match():
    # mismo título, sin año para desempatar: no hay que adivinar (spec 5.4)
    results = [_result(1, "Extras", popularity=5.0), _result(2, "Extras", popularity=5.0)]
    match, candidates = find_best_match(results, "Extras", None)
    assert match is None
    assert len(candidates) == 2


def test_single_exact_result_with_year_matches():
    results = [_result(1, "Serie X", 2010)]
    match, _candidates = find_best_match(results, "Serie X", 2010)
    assert match is not None
    assert match.tmdb_id == 1


def test_single_weak_result_does_not_auto_match():
    # ni el título ni el año coinciden: score bajo, no hay que aplicarlo solo.
    results = [_result(1, "Algo Completamente Distinto", 1975)]
    match, candidates = find_best_match(results, "Serie X", 2010)
    assert match is None
    assert candidates == results


def test_score_candidate_rewards_exact_title_and_year():
    candidate = _result(1, "Cowboy Bebop", 1998)
    assert score_candidate(candidate, "Cowboy Bebop", 1998) > score_candidate(candidate, "Cowboy Bebop", 2005)
    assert score_candidate(candidate, "Cowboy Bebop", 1998) > score_candidate(candidate, "Otra Cosa", 1998)


class _FakeTmdbClient:
    """No pega a la red: para probar que el hint de tmdbid en el nombre de
    carpeta salta la búsqueda difusa por completo (caso real reportado)."""

    def __init__(self) -> None:
        self.enabled = True
        self.search_called = False

    def get_tv_details(self, tmdb_id: int) -> dict:
        assert tmdb_id == 88046
        return {"name": "Fire Force", "first_air_date": "2019-07-05", "poster_path": None}

    def download_poster(self, poster_path):
        return None

    def get_season_details(self, tmdb_id: int, season_number: int) -> dict:
        return {
            "episodes": [
                {"episode_number": 1, "name": "Rekka no Shoujo", "air_date": "2019-07-05", "runtime": 24}
            ]
        }

    def search_tv(self, query: str, year=None):
        self.search_called = True
        return []


def test_sync_new_series_uses_tmdb_id_hint_from_folder_name():
    # "Fire Force (2019) [tmdbid-88046]": convención Jellyfin/Kodi, el id ya
    # viene en el nombre de carpeta y hay que usarlo en vez de buscar por
    # título (que además queda sucio con el tag si no se limpia).
    series = Series(path="X", folder_name="Fire Force (2019) [tmdbid-88046]")
    season = Season(number=1)
    episode = Episode(number=1, path="ep1.mkv")
    season.episodes.append(episode)
    series.seasons.append(season)

    client = _FakeTmdbClient()
    status = sync_new_series(session=None, client=client, series=series)

    assert status == "auto"
    assert series.tmdb_id == 88046
    assert series.title == "Fire Force"
    assert client.search_called is False
    assert episode.tmdb_title == "Rekka no Shoujo"
