from types import SimpleNamespace

import pytest

from mediaqc.core.db.repo import (
    apply_scan_result,
    create_db_engine,
    episode_runtime_deviates,
    list_episode_reviews,
    list_problem_episodes,
    list_review_queue,
    list_series_tree,
    make_session_factory,
    record_review,
)
from mediaqc.core.db.models import Episode, Season, Series
from mediaqc.core.scanner import ScannedEpisode, ScannedSeries, ScanResult


def test_runtime_deviates_true_for_truncated_file():
    ep = SimpleNamespace(duration_s=600, tmdb_runtime_min=24)  # 10 min real vs 24 min oficial
    assert episode_runtime_deviates(ep) is True


def test_runtime_deviates_false_within_threshold():
    ep = SimpleNamespace(duration_s=23 * 60, tmdb_runtime_min=24)  # ~4% de diferencia
    assert episode_runtime_deviates(ep) is False


def test_runtime_deviates_false_when_duration_missing():
    ep = SimpleNamespace(duration_s=None, tmdb_runtime_min=24)
    assert episode_runtime_deviates(ep) is False


def test_runtime_deviates_false_when_tmdb_runtime_missing():
    ep = SimpleNamespace(duration_s=600, tmdb_runtime_min=None)
    assert episode_runtime_deviates(ep) is False


def test_runtime_deviates_false_when_tmdb_runtime_zero():
    ep = SimpleNamespace(duration_s=600, tmdb_runtime_min=0)
    assert episode_runtime_deviates(ep) is False


def _episode(path, season, number):
    return ScannedEpisode(
        path=path, season=season, number=number, file_size=100, mtime=1.0, file_hash=f"hash-{path}"
    )


def test_rescan_reparents_episodes_and_cleans_up_ghost_series(tmp_path):
    # Reproduce el bug real: un primer escaneo agrupó mal las carpetas
    # "Season NN" como series propias; el fix agrupa todo bajo la serie
    # correcta. Un re-escaneo no debe romper por episodes.path duplicado, y
    # las series fantasma que quedan sin episodios deben desaparecer solas.
    engine = create_db_engine(tmp_path / "mediaqc.db")
    session_factory = make_session_factory(engine)

    show_root = tmp_path / "Nombre De La Serie"
    ep1_path = show_root / "Season 01" / "01 - Piloto.mkv"
    ep2_path = show_root / "Season 02" / "01 - Vuelta.mkv"

    buggy_scan = ScanResult(
        series=[
            ScannedSeries(
                path=show_root / "Season 01",
                folder_name="Season 01",
                title="Season 01",
                year=None,
                episodes=[_episode(ep1_path, season=1, number=1)],
            ),
            ScannedSeries(
                path=show_root / "Season 02",
                folder_name="Season 02",
                title="Season 02",
                year=None,
                episodes=[_episode(ep2_path, season=2, number=1)],
            ),
        ]
    )

    with session_factory() as session:
        apply_scan_result(session, buggy_scan)
        session.commit()
        bad_series = {s.folder_name for s in list_series_tree(session)}
    assert bad_series == {"Season 01", "Season 02"}

    fixed_scan = ScanResult(
        series=[
            ScannedSeries(
                path=show_root,
                folder_name="Nombre De La Serie",
                title="Nombre De La Serie",
                year=None,
                episodes=[
                    _episode(ep1_path, season=1, number=1),
                    _episode(ep2_path, season=2, number=1),
                ],
            )
        ]
    )

    with session_factory() as session:
        apply_scan_result(session, fixed_scan)  # no debe lanzar IntegrityError
        session.commit()
        series_after = list_series_tree(session)

    assert {s.folder_name for s in series_after} == {"Nombre De La Serie"}
    (series,) = series_after
    seasons = {season.number for season in series.seasons}
    assert seasons == {1, 2}


def _seed_one_episode(tmp_path, status: str = "analizado", missing: bool = False) -> tuple[object, int]:
    engine = create_db_engine(tmp_path / "mediaqc.db")
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        series = Series(path="X", folder_name="Serie")
        season = Season(number=1)
        episode = Episode(number=1, path="ep1.mkv", status=status, missing=missing)
        season.episodes.append(episode)
        series.seasons.append(season)
        session.add(series)
        session.commit()
        episode_id = episode.id
    return session_factory, episode_id


def test_record_review_ok_approves_episode(tmp_path):
    session_factory, episode_id = _seed_one_episode(tmp_path)
    with session_factory() as session:
        episode = session.get(Episode, episode_id)
        record_review(session, episode, "ok")
        session.commit()
        assert episode.status == "aprobado"


def test_record_review_non_ok_marks_problema(tmp_path):
    session_factory, episode_id = _seed_one_episode(tmp_path)
    with session_factory() as session:
        episode = session.get(Episode, episode_id)
        record_review(session, episode, "sync_constante")
        session.commit()
        assert episode.status == "problema"


def test_record_review_rejects_unknown_verdict(tmp_path):
    session_factory, episode_id = _seed_one_episode(tmp_path)
    with session_factory() as session:
        episode = session.get(Episode, episode_id)
        with pytest.raises(ValueError):
            record_review(session, episode, "veredicto_inventado")


def test_record_review_persists_timestamp_and_note(tmp_path):
    session_factory, episode_id = _seed_one_episode(tmp_path)
    with session_factory() as session:
        episode = session.get(Episode, episode_id)
        record_review(session, episode, "otro", timestamp_ms=12345, note="se corta el audio")
        session.commit()

    with session_factory() as session:
        reviews = list_episode_reviews(session, episode_id)
        assert len(reviews) == 1
        assert reviews[0].timestamp_ms == 12345
        assert reviews[0].note == "se corta el audio"


def test_list_episode_reviews_is_append_only_history(tmp_path):
    session_factory, episode_id = _seed_one_episode(tmp_path)
    with session_factory() as session:
        episode = session.get(Episode, episode_id)
        record_review(session, episode, "sync_constante")
        record_review(session, episode, "ok")
        session.commit()

    with session_factory() as session:
        reviews = list_episode_reviews(session, episode_id)
        assert len(reviews) == 2  # ambas quedan, nada se pisa ni se borra


def test_list_review_queue_includes_analizado_and_pendiente_revision(tmp_path):
    session_factory, episode_id = _seed_one_episode(tmp_path, status="analizado")
    with session_factory() as session:
        queue = list_review_queue(session)
        assert [e.id for e in queue] == [episode_id]


def test_list_review_queue_excludes_missing_episodes(tmp_path):
    session_factory, episode_id = _seed_one_episode(tmp_path, status="analizado", missing=True)
    with session_factory() as session:
        queue = list_review_queue(session)
        assert queue == []


def test_list_review_queue_excludes_already_reviewed(tmp_path):
    session_factory, episode_id = _seed_one_episode(tmp_path, status="aprobado")
    with session_factory() as session:
        queue = list_review_queue(session)
        assert queue == []


def test_list_problem_episodes_only_problema_status(tmp_path):
    session_factory, episode_id = _seed_one_episode(tmp_path, status="analizado")
    with session_factory() as session:
        episode = session.get(Episode, episode_id)
        record_review(session, episode, "audio_faltante")
        session.commit()

    with session_factory() as session:
        problems = list_problem_episodes(session)
        assert [e.id for e in problems] == [episode_id]
