import time

import numpy as np

from mediaqc.core.analyzer.correlate import measure_offset, purge_audio_cache


def test_measure_offset_detects_known_shift():
    sr = 8000
    rng = np.random.default_rng(42)
    full = rng.normal(size=sr * 12)
    ref = full[: sr * 10]
    shift_samples = 400  # 50 ms
    cand = full[shift_samples : shift_samples + sr * 10]

    m = measure_offset(ref, cand, sample_rate=sr, pos_s=0.0)

    assert abs(m.offset_ms - (shift_samples / sr * 1000)) < 5
    assert m.score > 5


def test_measure_offset_zero_shift():
    sr = 8000
    rng = np.random.default_rng(7)
    sig = rng.normal(size=sr * 5)

    m = measure_offset(sig, sig.copy(), sample_rate=sr, pos_s=0.0)

    assert m.offset_ms == 0
    assert m.score > 5


def test_measure_offset_identical_signals_high_score_and_waveform_corr():
    sr = 8000
    rng = np.random.default_rng(1)
    sig = rng.normal(size=sr * 5)

    m = measure_offset(sig, sig.copy(), sample_rate=sr, pos_s=0.0)

    assert m.score > 40
    assert m.waveform_corr > 0.98


def test_measure_offset_uncorrelated_signals_low_waveform_corr():
    sr = 8000
    rng = np.random.default_rng(2)
    ref = rng.normal(size=sr * 5)
    cand = rng.normal(size=sr * 5)  # señal random distinta, no relacionada

    m = measure_offset(ref, cand, sample_rate=sr, pos_s=0.0)

    assert m.waveform_corr < 0.5


def test_purge_audio_cache_removes_oldest_files_first(tmp_path):
    # 5 archivos de 100 bytes, límite de 250 bytes -> deben sobrevivir los
    # 2 más nuevos (los últimos escritos), se borran los 3 más viejos.
    paths = []
    for i in range(5):
        p = tmp_path / f"{i}.wav"
        p.write_bytes(b"x" * 100)
        paths.append(p)
        time.sleep(0.01)  # asegura mtimes distintos y ordenados

    removed = purge_audio_cache(tmp_path, limit_bytes=250)

    assert removed == 3
    remaining = {p.name for p in tmp_path.glob("*.wav")}
    assert remaining == {"3.wav", "4.wav"}


def test_purge_audio_cache_noop_when_under_limit(tmp_path):
    p = tmp_path / "0.wav"
    p.write_bytes(b"x" * 100)
    removed = purge_audio_cache(tmp_path, limit_bytes=1_000_000)
    assert removed == 0
    assert p.is_file()


def test_purge_audio_cache_missing_dir_is_noop(tmp_path):
    missing = tmp_path / "no_existe"
    assert purge_audio_cache(missing, limit_bytes=100) == 0
