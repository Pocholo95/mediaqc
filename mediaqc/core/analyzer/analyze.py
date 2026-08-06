"""Orquesta un análisis completo de un par (referencia, candidato): ventanas
+ extracción + correlación + clasificación (spec sección 5.5)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from mediaqc.core.analyzer.classify import ClassificationResult, classify_windows
from mediaqc.core.analyzer.correlate import (
    WindowMeasurement,
    extract_audio_window,
    load_wav_mono,
    measure_offset,
)
from mediaqc.core.analyzer.windows import window_positions_seconds

logger = logging.getLogger(__name__)


@dataclass
class PairAnalysisResult:
    windows: list[WindowMeasurement]
    classification: ClassificationResult
    error: str | None = None


def analyze_pair(
    ffmpeg_bin: Path,
    file_path: Path,
    file_hash: str,
    ref_stream_index: int,
    cand_stream_index: int,
    duration_s: float,
    window_seconds: int,
    sample_rate: int,
    cache_dir: Path,
) -> PairAnalysisResult:
    positions = window_positions_seconds(duration_s)
    measurements: list[WindowMeasurement] = []

    for pos in positions:
        try:
            ref_wav = extract_audio_window(
                ffmpeg_bin, file_path, ref_stream_index, pos, window_seconds, sample_rate, cache_dir, file_hash
            )
            cand_wav = extract_audio_window(
                ffmpeg_bin, file_path, cand_stream_index, pos, window_seconds, sample_rate, cache_dir, file_hash
            )
            ref_audio = load_wav_mono(ref_wav)
            cand_audio = load_wav_mono(cand_wav)
            measurements.append(measure_offset(ref_audio, cand_audio, sample_rate, pos))
        except Exception as exc:
            # una ventana en un tramo mudo (o que falla al extraer) da score
            # bajo/cero; con MIN_VALID_WINDOWS alcanza (spec trampa #4), así
            # que no hace falta abortar todo el análisis por una ventana.
            logger.warning("ventana en %.1fs falló para %s: %s", pos, file_path, exc)
            measurements.append(WindowMeasurement(pos_s=pos, offset_ms=0.0, score=0.0, waveform_corr=0.0))

    classification = classify_windows(measurements)
    return PairAnalysisResult(windows=measurements, classification=classification)


def windows_to_json(windows: list[WindowMeasurement]) -> str:
    return json.dumps([{"pos_s": w.pos_s, "offset_ms": w.offset_ms, "score": w.score} for w in windows])


def windows_from_json(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []
