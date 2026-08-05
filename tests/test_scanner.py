from pathlib import Path

from mediaqc.core.scanner import (
    parse_episode,
    parse_filename_bare_number,
    parse_filename_nxm,
    parse_filename_sxxexx,
    parse_season_folder,
    parse_series_folder,
    parse_tmdb_id_hint,
    scan_media_paths,
    strip_scraper_tags,
)


def test_parse_series_folder_with_year():
    assert parse_series_folder("Cowboy Bebop (1998)") == ("Cowboy Bebop", 1998)


def test_parse_series_folder_without_year():
    assert parse_series_folder("Serie Sin Año") == ("Serie Sin Año", None)


def test_parse_series_folder_strips_tmdbid_tag():
    # Convención Jellyfin/Kodi: el tag va después del año y ensucia tanto el
    # año parseado como la búsqueda en TMDB si no se saca.
    assert parse_series_folder("Fire Force (2019) [tmdbid-88046]") == ("Fire Force", 2019)


def test_parse_series_folder_strips_tag_without_year():
    assert parse_series_folder("Fire Force [tmdbid-88046]") == ("Fire Force", None)


def test_strip_scraper_tags_removes_bracketed_and_braced():
    assert strip_scraper_tags("Serie (2019) [tmdbid-88046]") == "Serie (2019)"
    assert strip_scraper_tags("Serie (2019) {tmdb-88046}") == "Serie (2019)"
    assert strip_scraper_tags("Serie [imdbid-tt1234567]") == "Serie"


def test_strip_scraper_tags_no_tag_unchanged():
    assert strip_scraper_tags("Serie Normal") == "Serie Normal"


def test_parse_tmdb_id_hint_found():
    assert parse_tmdb_id_hint("Fire Force (2019) [tmdbid-88046]") == 88046


def test_parse_tmdb_id_hint_tmdb_without_id_suffix():
    assert parse_tmdb_id_hint("Serie {tmdb-12345}") == 12345


def test_parse_tmdb_id_hint_absent():
    assert parse_tmdb_id_hint("Serie Sin Tag (2019)") is None


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


def test_scan_media_path_pointed_directly_at_one_series(tmp_path):
    # media_paths apuntando directo a la carpeta de UNA serie (todos sus hijos
    # son "Season NN"): no hay que tratar cada temporada como una serie
    # distinta, o el nombre de la serie termina siendo "Season 01" y TMDB
    # nunca la encuentra.
    show_root = tmp_path / "Nombre De La Serie"
    (show_root / "Season 01").mkdir(parents=True)
    (show_root / "Season 02").mkdir(parents=True)
    (show_root / "Season 01" / "01 - Piloto.mkv").touch()
    (show_root / "Season 02" / "01 - Vuelta.mkv").touch()

    result = scan_media_paths([show_root])

    assert len(result.series) == 1
    assert result.series[0].folder_name == "Nombre De La Serie"
    seasons = {ep.season for ep in result.series[0].episodes}
    assert seasons == {1, 2}


def test_scan_media_path_with_multiple_series_unaffected(tmp_path):
    library_root = tmp_path / "Biblioteca"
    show_a = library_root / "Serie A"
    show_b = library_root / "Serie B"
    (show_a / "Season 01").mkdir(parents=True)
    (show_b / "Season 01").mkdir(parents=True)
    (show_a / "Season 01" / "01.mkv").touch()
    (show_b / "Season 01" / "01.mkv").touch()

    result = scan_media_paths([library_root])

    folder_names = {s.folder_name for s in result.series}
    assert folder_names == {"Serie A", "Serie B"}
