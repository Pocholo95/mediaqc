from pathlib import Path

from mediaqc.core.scanner import (
    parse_episode,
    parse_filename_bare_number,
    parse_filename_nxm,
    parse_filename_sxxexx,
    parse_season_folder,
    parse_series_folder,
)


def test_parse_series_folder_with_year():
    assert parse_series_folder("Cowboy Bebop (1998)") == ("Cowboy Bebop", 1998)


def test_parse_series_folder_without_year():
    assert parse_series_folder("Serie Sin Año") == ("Serie Sin Año", None)


def test_parse_season_folder_season():
    assert parse_season_folder("Season 02") == 2


def test_parse_season_folder_temporada():
    assert parse_season_folder("Temporada 1") == 1


def test_parse_season_folder_no_match():
    assert parse_season_folder("Extras") is None


def test_sxxexx_basic():
    assert parse_filename_sxxexx("Serie.S01E05.mkv") == (1, 5)


def test_sxxexx_case_insensitive():
    assert parse_filename_sxxexx("serie.s01e05.mkv") == (1, 5)


def test_sxxexx_double_episode_keeps_first_match():
    # "S01E01-E02": se queda con el primer match (S01E01), no intenta parsear el rango.
    assert parse_filename_sxxexx("Serie S01E01-E02.mkv") == (1, 1)


def test_sxxexx_ignores_trailing_half_episode():
    # "S02E13.5": el ".5" no forma parte de \d{1,3}, se corta en E13.
    assert parse_filename_sxxexx("Serie S02E13.5.mkv") == (2, 13)


def test_nxm_basic():
    assert parse_filename_nxm("Serie 1x05.mkv") == (1, 5)


def test_bare_number_with_dots():
    assert parse_filename_bare_number("Serie.Nombre.De.Episodio.03.mkv") == 3


def test_bare_number_with_spaces():
    assert parse_filename_bare_number("03 - Titulo del episodio.mkv") == 3


def test_parse_episode_sxxexx(tmp_path):
    series_root = tmp_path / "Cowboy Bebop (1998)"
    series_root.mkdir()
    f = series_root / "Cowboy Bebop S01E05.mkv"
    assert parse_episode(f, series_root) == (1, 5)


def test_parse_episode_nxm(tmp_path):
    series_root = tmp_path / "Serie"
    series_root.mkdir()
    f = series_root / "Serie 2x13.mkv"
    assert parse_episode(f, series_root) == (2, 13)


def test_parse_episode_season_folder_plus_bare_number(tmp_path):
    series_root = tmp_path / "Serie"
    season_dir = series_root / "Temporada 1"
    season_dir.mkdir(parents=True)
    f = season_dir / "01 - Titulo.mkv"
    assert parse_episode(f, series_root) == (1, 1)


def test_parse_episode_season_folder_plus_dotted_filename(tmp_path):
    series_root = tmp_path / "Serie"
    season_dir = series_root / "Season 02"
    season_dir.mkdir(parents=True)
    f = season_dir / "Serie.Nombre.De.Episodio.03.mkv"
    assert parse_episode(f, series_root) == (2, 3)


def test_parse_episode_especial_unparseable(tmp_path):
    series_root = tmp_path / "Serie"
    series_root.mkdir()
    f = series_root / "Especial.mkv"
    assert parse_episode(f, series_root) is None


def test_parse_episode_ova_unparseable(tmp_path):
    series_root = tmp_path / "Serie"
    series_root.mkdir()
    f = series_root / "OVA.mkv"
    assert parse_episode(f, series_root) is None


def test_parse_episode_no_season_context_unparseable(tmp_path):
    # Sin SxxExx, sin NxM, y sin carpeta de temporada ancestro -> no adivinar.
    series_root = tmp_path / "Serie"
    series_root.mkdir()
    f = series_root / "07.mkv"
    assert parse_episode(f, series_root) is None
