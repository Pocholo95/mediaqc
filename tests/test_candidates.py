from mediaqc.core.candidates import (
    build_series_lookup,
    candidate_kind_for_extension,
    normalize_series_name,
    scan_candidate_paths,
)


def test_candidate_kind_for_extension():
    assert candidate_kind_for_extension(".srt") == "subtitle"
    assert candidate_kind_for_extension(".ass") == "subtitle"
    assert candidate_kind_for_extension(".mp3") == "audio"
    assert candidate_kind_for_extension(".flac") == "audio"
    assert candidate_kind_for_extension(".mkv") is None


def test_normalize_series_name_strips_scraper_tags():
    assert normalize_series_name("Fire Force (2019) [tmdbid-88046]") == "fire force (2019)"


class _FakeSeries:
    def __init__(self, id, folder_name, title=None):
        self.id = id
        self.folder_name = folder_name
        self.title = title


def test_build_series_lookup_multiple_keys_point_to_same_id():
    series = [_FakeSeries(1, "Fire Force (2019) [tmdbid-88046]", "Fire Force")]
    lookup = build_series_lookup(series)
    assert lookup["fire force (2019)"] == 1
    assert lookup["fire force"] == 1


def test_scan_candidate_paths_matches_by_number(tmp_path):
    root = tmp_path / "Candidatos"
    season_dir = root / "Fire Force" / "Season 01"
    season_dir.mkdir(parents=True)
    (season_dir / "01 - Piloto.srt").touch()
    (season_dir / "02 - Segundo.mp3").touch()

    result = scan_candidate_paths([root], known_series={"fire force": 42})

    assert len(result.candidates) == 2
    assert {c.kind for c in result.candidates} == {"subtitle", "audio"}
    assert all(c.series_id == 42 and c.season == 1 for c in result.candidates)
    assert {c.number for c in result.candidates} == {1, 2}


def test_scan_candidate_paths_unmatched_series_dir(tmp_path):
    root = tmp_path / "Candidatos"
    series_dir = root / "Serie Desconocida"
    series_dir.mkdir(parents=True)
    (series_dir / "01.srt").touch()

    result = scan_candidate_paths([root], known_series={})

    assert len(result.unmatched_series_dirs) == 1
    assert result.candidates == []


def test_scan_candidate_paths_unparseable_file(tmp_path):
    root = tmp_path / "Candidatos"
    series_dir = root / "Fire Force"
    series_dir.mkdir(parents=True)
    (series_dir / "Especial.srt").touch()  # sin número ni carpeta de temporada

    result = scan_candidate_paths([root], known_series={"fire force": 1})

    assert len(result.unparseable) == 1
    assert result.candidates == []


def test_scan_candidate_paths_ignores_non_candidate_extensions(tmp_path):
    root = tmp_path / "Candidatos"
    season_dir = root / "Fire Force" / "Season 01"
    season_dir.mkdir(parents=True)
    (season_dir / "01.nfo").touch()
    (season_dir / "cover.jpg").touch()

    result = scan_candidate_paths([root], known_series={"fire force": 1})

    assert result.candidates == []
    assert result.unparseable == []


def test_scan_candidate_paths_tracks_reachable_roots(tmp_path):
    root = tmp_path / "Candidatos"
    root.mkdir()
    missing_root = tmp_path / "no_existe"

    result = scan_candidate_paths([root, missing_root], known_series={})

    assert result.reachable_candidates_paths == [root]
