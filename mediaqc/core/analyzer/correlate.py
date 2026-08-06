"""Extracción de audio por ventana y correlación cruzada (spec sección 5.5).

Convención de signo del offset: se usa ``scipy.signal.correlation_lags`` con
``correlate(ref, cand, mode='full')``. El lag en el pico satisface
``ref[n] ≈ cand[n - lag]`` — si ``lag > 0``, el candidato va ADELANTADO
respecto de la referencia y hay que retrasarlo (delay positivo) para
alinearlo. ``offset_ms`` sigue esa misma convención. Es funcionalmente
equivalente a la fórmula de la spec (``argmax(corr) - len(b) + 1``), pero
usando la API moderna de scipy en vez de aritmética manual de índices.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import signal

from mediaqc.core.analyzer.thresholds import SEARCH_RANGE_S

logger = logging.getLogger(__name__)


@dataclass
class WindowMeasurement:
    pos_s: float
    offset_ms: float
    score: float
    waveform_corr: float  # correlación normalizada de las formas de onda en el lag encontrado


def extract_audio_window(
    ffmpeg_bin: Path,
    file_path: Path,
    stream_index: int,
    pos_s: float,
    window_seconds: int,
    sample_rate: int,
    cache_dir: Path,
    file_hash: str,
) -> Path:
    """``ffmpeg -ss <pos> -t <window> -i <file> -map 0:<stream> ...`` (spec 5.5).

    Cacheado con clave ``sha1(file_hash + stream_index + pos)`` — importa: se
    re-analiza mucho durante el desarrollo, y decodificar TrueHD es lento.
    """
    cache_key = hashlib.sha1(f"{file_hash}|{stream_index}|{pos_s}".encode("utf-8")).hexdigest()
    out_path = cache_dir / f"{cache_key}.wav"
    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path

    cache_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(ffmpeg_bin),
        "-y",
        "-ss", str(pos_s),
        "-t", str(window_seconds),
        "-i", str(file_path),
        "-map", f"0:{stream_index}",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "wav",
        "-acodec", "pcm_s16le",
        str(out_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return out_path


def purge_audio_cache(cache_dir: Path, limit_bytes: int) -> int:
    """Purga LRU del caché de ventanas (spec 5.5): borra los ``.wav`` menos
    usados recientemente (por mtime) hasta bajar del límite configurado.
    Devuelve cuántos archivos se borraron."""
    if not cache_dir.is_dir() or limit_bytes <= 0:
        return 0

    files = sorted(cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    removed = 0

    for f in files:
        if total <= limit_bytes:
            break
        try:
            size = f.stat().st_size
            f.unlink()
            total -= size
            removed += 1
        except OSError:
            logger.warning("no se pudo purgar %s del caché de audio", f)

    return removed


def load_wav_mono(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float64)


def _normalize(x: np.ndarray) -> np.ndarray:
    std = x.std()
    if std == 0:
        return x - x.mean()
    return (x - x.mean()) / std


def _waveform_correlation_at_lag(ref: np.ndarray, cand: np.ndarray, lag_samples: int) -> float:
    """Correlación de Pearson entre las señales alineadas en ``lag_samples``:
    distingue "bien sincronizado" (score alto, forma de onda distinta) de
    "es literalmente el mismo audio" (spec sección 5.5)."""
    if lag_samples >= 0:
        ref_aligned = ref[lag_samples:]
        cand_aligned = cand[: len(ref_aligned)]
    else:
        cand_aligned = cand[-lag_samples:]
        ref_aligned = ref[: len(cand_aligned)]

    n = min(len(ref_aligned), len(cand_aligned))
    if n < 2:
        return 0.0
    ref_aligned = ref_aligned[:n]
    cand_aligned = cand_aligned[:n]
    if ref_aligned.std() == 0 or cand_aligned.std() == 0:
        return 0.0
    return float(np.corrcoef(ref_aligned, cand_aligned)[0, 1])


def measure_offset(
    ref: np.ndarray,
    cand: np.ndarray,
    sample_rate: int,
    pos_s: float,
    search_range_s: float = SEARCH_RANGE_S,
) -> WindowMeasurement:
    if len(ref) < 2 or len(cand) < 2:
        return WindowMeasurement(pos_s=pos_s, offset_ms=0.0, score=0.0, waveform_corr=0.0)

    ref_n = _normalize(ref)
    cand_n = _normalize(cand)

    corr = signal.correlate(ref_n, cand_n, mode="full", method="fft")
    lags = signal.correlation_lags(len(ref_n), len(cand_n), mode="full")

    max_lag_samples = int(search_range_s * sample_rate)
    mask = (lags >= -max_lag_samples) & (lags <= max_lag_samples)
    corr_windowed = corr[mask]
    lags_windowed = lags[mask]

    if len(corr_windowed) == 0:
        return WindowMeasurement(pos_s=pos_s, offset_ms=0.0, score=0.0, waveform_corr=0.0)

    peak_idx = int(np.argmax(corr_windowed))
    offset_samples = int(lags_windowed[peak_idx])
    offset_ms = offset_samples / sample_rate * 1000.0

    corr_std = corr_windowed.std()
    score = float((corr_windowed[peak_idx] - corr_windowed.mean()) / corr_std) if corr_std > 0 else 0.0

    waveform_corr = _waveform_correlation_at_lag(ref, cand, offset_samples)

    return WindowMeasurement(pos_s=pos_s, offset_ms=offset_ms, score=score, waveform_corr=waveform_corr)
