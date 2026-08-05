from types import SimpleNamespace

from mediaqc.core.db.repo import (
    apply_scan_result,
    create_db_engine,
    episode_runtime_deviates,
    list_series_tree,
    make_session_factory,
)
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
