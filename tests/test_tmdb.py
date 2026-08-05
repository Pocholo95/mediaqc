from mediaqc.core.tmdb import TmdbSeriesResult, find_best_match, score_candidate


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
