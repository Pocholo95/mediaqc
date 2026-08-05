import pytest

from mediaqc.core.config import ConfigError, validate_output_dir


def test_output_dir_inside_media_path_raises(tmp_path):
    media = tmp_path / "Series"
    media.mkdir()
    output = media / "_muxeado"
    with pytest.raises(ConfigError):
        validate_output_dir(str(output), [str(media)])


def test_output_dir_equal_to_media_path_raises(tmp_path):
    media = tmp_path / "Series"
    media.mkdir()
    with pytest.raises(ConfigError):
        validate_output_dir(str(media), [str(media)])


def test_output_dir_outside_media_paths_ok(tmp_path):
    media = tmp_path / "Series"
    media.mkdir()
    output = tmp_path / "_muxeado"
    validate_output_dir(str(output), [str(media)])  # no debe lanzar


def test_output_dir_empty_is_valid():
    validate_output_dir("", ["D:\\Series"])  # todavía no configurado, no debe lanzar


def test_output_dir_checked_against_all_media_paths(tmp_path):
    media1 = tmp_path / "Series"
    media2 = tmp_path / "Anime"
    media1.mkdir()
    media2.mkdir()
    output = media2 / "_muxeado"
    with pytest.raises(ConfigError):
        validate_output_dir(str(output), [str(media1), str(media2)])
