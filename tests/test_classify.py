"""Vectores sintéticos para los veredictos del clasificador (spec sección 9:
"con vectores de ventanas sintéticos para los cuatro veredictos de sync",
criterio de aceptación de la fase 4)."""

from mediaqc.core.analyzer.classify import classify_windows
from mediaqc.core.analyzer.correlate import WindowMeasurement

_POSITIONS = [100.0, 300.0, 600.0, 900.0, 1200.0]


def _windows(offsets_ms, score=20.0, waveform_corr=0.4):
    return [
        WindowMeasurement(pos_s=p, offset_ms=o, score=score, waveform_corr=waveform_corr)
        for p, o in zip(_POSITIONS, offsets_ms)
    ]


def test_ok_when_offsets_near_zero_and_stable():
    result = classify_windows(_windows([5, -3, 2, 0, 4]))
    assert result.verdict == "ok"


def test_sync_constante_when_offset_stable_but_large():
    result = classify_windows(_windows([500, 510, 495, 505, 502]))
    assert result.verdict == "sync_constante"
    assert result.suggested_delay_ms is not None
    assert abs(result.suggested_delay_ms - 502) <= 15


def test_sync_drift_matches_pal_ratio():
    # 25/23.976 = 1.0427: el sospechoso habitual de framerate (spec 5.5).
    ratio = 25 / 23.976
    offsets_ms = [(ratio - 1) * p * 1000 for p in _POSITIONS]
    result = classify_windows(_windows(offsets_ms))
    assert result.verdict == "sync_drift"
    assert result.suggested_resample_ratio is not None
    assert abs(result.suggested_resample_ratio - ratio) < 0.01


def test_sync_segmentado_when_offsets_dont_fit_a_line():
    # saltos escalonados, ni constante ni una recta limpia.
    result = classify_windows(_windows([0, 0, 800, 800, 50]))
    assert result.verdict == "sync_segmentado"


def test_episodio_equivocado_when_too_few_valid_windows():
    windows = _windows([0, 0, 0, 0, 0], score=1.0)  # todas bajo el piso de score
    result = classify_windows(windows)
    assert result.verdict == "episodio_equivocado"


def test_pista_duplicada_when_all_windows_identical():
    windows = _windows([0, 0, 0, 0, 0], score=50.0, waveform_corr=0.99)
    result = classify_windows(windows)
    assert result.verdict == "pista_duplicada"


def test_audio_incompleto_when_some_windows_identical():
    windows = [
        WindowMeasurement(pos_s=100.0, offset_ms=0, score=50.0, waveform_corr=0.99),
        WindowMeasurement(pos_s=300.0, offset_ms=20, score=15.0, waveform_corr=0.3),
        WindowMeasurement(pos_s=600.0, offset_ms=0, score=55.0, waveform_corr=0.995),
        WindowMeasurement(pos_s=900.0, offset_ms=25, score=12.0, waveform_corr=0.25),
        WindowMeasurement(pos_s=1200.0, offset_ms=18, score=14.0, waveform_corr=0.28),
    ]
    result = classify_windows(windows)
    assert result.verdict == "audio_incompleto"
    assert result.incomplete_positions == [100.0, 600.0]
