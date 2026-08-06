"""core/suggestions.py — comandos sugeridos y export de script (spec 5.7).

``compute_final_delay_ms`` está validado empíricamente contra un mkvmerge
real (ver commit): muxeé un archivo con un delay conocido (+200ms) inyectado
en la pista candidata, medí con el pipeline real, apliqué el comando
sugerido, y remedí -- el residual quedó en ±1ms. El caso de este test
(-168 -> -168) reproduce esa medición real para que quede como regresión.
"""

from mediaqc.core.suggestions import (
    Suggestion,
    build_drift_ffmpeg_commands,
    build_external_sync_command,
    build_internal_sync_command,
    build_season_export_script,
    compute_final_delay_ms,
    format_command,
    suggest_for_analysis,
)


def test_compute_final_delay_ms_matches_empirical_validation():
    assert compute_final_delay_ms(-168.0) == -168


def test_compute_final_delay_ms_rounds():
    assert compute_final_delay_ms(12.6) == 13
    assert compute_final_delay_ms(-12.6) == -13
    assert compute_final_delay_ms(0) == 0


def test_format_command_windows_quotes_parts_with_spaces():
    cmd = ["mkvmerge.exe", "-o", "C:\\out\\a b.mkv", "--sync", "1:100", "C:\\in.mkv"]
    text = format_command(cmd, platform="windows")
    assert '"C:\\out\\a b.mkv"' in text
    assert "-o" in text


def test_format_command_linux_uses_shlex_quoting():
    cmd = ["mkvmerge", "-o", "/out/a b.mkv", "--sync", "1:100"]
    text = format_command(cmd, platform="linux")
    assert "'/out/a b.mkv'" in text


def test_build_internal_sync_command_structure():
    cmd = build_internal_sync_command("mkvmerge", "in.mkv", "out.mkv", mkv_track_id=2, delay_ms=-168)
    assert cmd == ["mkvmerge", "-o", "out.mkv", "--sync", "2:-168", "in.mkv"]


def test_build_external_sync_command_structure():
    cmd = build_external_sync_command(
        "mkvmerge", "bdrip.mkv", "candidato.ac3", "out.mkv", delay_ms=50, language="spa", track_name="Latino"
    )
    assert cmd[:2] == ["mkvmerge", "-o"]
    assert "bdrip.mkv" in cmd
    assert "--language" in cmd and "0:spa" in cmd
    assert "--track-name" in cmd and "0:Latino" in cmd
    assert "--sync" in cmd and "0:50" in cmd
    assert cmd[-1] == "candidato.ac3"


def test_build_drift_ffmpeg_commands_has_speed_and_tempo_variants():
    variants = build_drift_ffmpeg_commands("ffmpeg", "in.mkv", "speed.mka", "tempo.mka", ratio=1.0427)
    assert "asetrate=" in " ".join(variants["speed"])
    assert "rubberband=tempo=1.0427" in " ".join(variants["tempo"])


def test_suggest_for_analysis_ok_verdict_has_no_commands():
    s = suggest_for_analysis(
        verdict="ok",
        pair_source="internal",
        suggested_delay_ms=None,
        suggested_resample_ratio=None,
        mkv_track_id=1,
        episode_path="ep.mkv",
        output_filename="ep.mkv",
        output_dir="D:/out",
        mkvmerge_bin="mkvmerge",
        ffmpeg_bin="ffmpeg",
    )
    assert s.commands == []


def test_suggest_for_analysis_sync_constante_builds_command():
    s = suggest_for_analysis(
        verdict="sync_constante",
        pair_source="internal",
        suggested_delay_ms=-168,
        suggested_resample_ratio=None,
        mkv_track_id=2,
        episode_path="ep.mkv",
        output_filename="ep.mkv",
        output_dir="D:/out",
        mkvmerge_bin="mkvmerge",
        ffmpeg_bin="ffmpeg",
    )
    assert len(s.commands) == 1
    assert "--sync" in s.commands[0]
    assert "2:-168" in s.commands[0]
    assert s.warning is None


def test_suggest_for_analysis_sync_constante_missing_mkv_track_id_warns():
    s = suggest_for_analysis(
        verdict="sync_constante",
        pair_source="internal",
        suggested_delay_ms=-168,
        suggested_resample_ratio=None,
        mkv_track_id=None,
        episode_path="ep.mkv",
        output_filename="ep.mkv",
        output_dir="D:/out",
        mkvmerge_bin="mkvmerge",
        ffmpeg_bin="ffmpeg",
    )
    assert s.commands == []
    assert s.warning is not None


def test_suggest_for_analysis_sync_constante_missing_mkvmerge_warns():
    s = suggest_for_analysis(
        verdict="sync_constante",
        pair_source="internal",
        suggested_delay_ms=-168,
        suggested_resample_ratio=None,
        mkv_track_id=2,
        episode_path="ep.mkv",
        output_filename="ep.mkv",
        output_dir="D:/out",
        mkvmerge_bin=None,
        ffmpeg_bin="ffmpeg",
    )
    assert s.commands == []
    assert "mkvmerge" in s.warning.lower()


def test_suggest_for_analysis_sync_drift_builds_two_variants_with_warning():
    s = suggest_for_analysis(
        verdict="sync_drift",
        pair_source="internal",
        suggested_delay_ms=None,
        suggested_resample_ratio=1.0427,
        mkv_track_id=2,
        episode_path="ep.mkv",
        output_filename="ep.mkv",
        output_dir="D:/out",
        mkvmerge_bin="mkvmerge",
        ffmpeg_bin="ffmpeg",
    )
    assert len(s.commands) == 2
    assert s.warning is not None


def test_suggest_for_analysis_sync_segmentado_has_no_command():
    s = suggest_for_analysis(
        verdict="sync_segmentado",
        pair_source="internal",
        suggested_delay_ms=None,
        suggested_resample_ratio=None,
        mkv_track_id=2,
        episode_path="ep.mkv",
        output_filename="ep.mkv",
        output_dir="D:/out",
        mkvmerge_bin="mkvmerge",
        ffmpeg_bin="ffmpeg",
    )
    assert s.commands == []
    assert s.warning is not None


def test_build_season_export_script_windows_skips_episodes_without_commands():
    suggestions_list = [
        ("S01E01", Suggestion(verdict="ok", summary="ok")),
        ("S01E02", Suggestion(verdict="sync_constante", summary="delay -168ms", commands=[["mkvmerge", "-o", "out.mkv"]])),
    ]
    script = build_season_export_script(suggestions_list, platform="windows")
    assert script.startswith("@echo off")
    assert "S01E01" not in script
    assert "S01E02" in script
    assert "if errorlevel 1" in script


def test_build_season_export_script_linux_has_shebang_and_set_e():
    suggestions_list = [
        ("S01E01", Suggestion(verdict="sync_constante", summary="delay 50ms", commands=[["mkvmerge", "-o", "out.mkv"]])),
    ]
    script = build_season_export_script(suggestions_list, platform="linux")
    assert script.startswith("#!/bin/sh")
    assert "set -e" in script
