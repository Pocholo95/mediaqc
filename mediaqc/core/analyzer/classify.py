"""Clasificación de sincronización a partir de las 5 mediciones de ventana
(spec sección 5.5)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import numpy as np

from mediaqc.core.analyzer.correlate import WindowMeasurement
from mediaqc.core.analyzer.thresholds import (
    CONSTANT_OFFSET_OK_MS,
    CONSTANT_OFFSET_STD_MS,
    DRIFT_R2_THRESHOLD,
    DUPLICATE_CORR_THRESHOLD,
    DUPLICATE_SCORE_THRESHOLD,
    MIN_SCORE_VALID,
    MIN_VALID_WINDOWS,
)


@dataclass
class ClassificationResult:
    verdict: str
    confidence: float
    suggested_delay_ms: int | None = None
    suggested_resample_ratio: float | None = None
    incomplete_positions: list[float] | None = None


def _linear_regression_r2(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Devuelve ``(pendiente, r2)`` de la regresión lineal ``ys ~ xs``."""
    if len(xs) < 2:
        return 0.0, 0.0
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    slope, intercept = np.polyfit(x, y, 1)
    y_pred = slope * x + intercept
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope), r2


def classify_windows(windows: list[WindowMeasurement]) -> ClassificationResult:
    valid = [w for w in windows if w.score >= MIN_SCORE_VALID]

    if len(valid) < MIN_VALID_WINDOWS:
        # spec trampa #3: los openings de anime son idénticos entre
        # episodios y pueden correlacionar bien contra el candidato
        # equivocado en UNA ventana -- por eso hace falta que fallen casi
        # todas, no alcanza con una.
        return ClassificationResult(verdict="episodio_equivocado", confidence=0.3)

    # Audio idéntico: si el score es altísimo Y la forma de onda es
    # prácticamente la misma, no es "muy buena sincronización", es
    # literalmente la misma pista (spec sección 5.5).
    identical = [
        w for w in valid if w.score > DUPLICATE_SCORE_THRESHOLD and w.waveform_corr > DUPLICATE_CORR_THRESHOLD
    ]
    if identical and len(identical) == len(valid):
        return ClassificationResult(verdict="pista_duplicada", confidence=0.9)
    if identical:
        return ClassificationResult(
            verdict="audio_incompleto",
            confidence=0.7,
            incomplete_positions=[w.pos_s for w in identical],
        )

    offsets_ms = [w.offset_ms for w in valid]
    offset_std = statistics.pstdev(offsets_ms) if len(offsets_ms) > 1 else 0.0

    if offset_std <= CONSTANT_OFFSET_STD_MS:
        median_offset = statistics.median(offsets_ms)
        if abs(median_offset) <= CONSTANT_OFFSET_OK_MS:
            return ClassificationResult(verdict="ok", confidence=0.9)
        return ClassificationResult(
            verdict="sync_constante",
            confidence=0.85,
            suggested_delay_ms=round(median_offset),
        )

    positions = [w.pos_s for w in valid]
    # pendiente en "segundos de offset por segundo de posición" (ambos en
    # las mismas unidades) para que sea directamente comparable a los
    # sospechosos habituales de framerate: 25/23.976=1.0427, 24/23.976=1.001.
    offsets_s = [ms / 1000.0 for ms in offsets_ms]
    slope, r2 = _linear_regression_r2(positions, offsets_s)

    if r2 >= DRIFT_R2_THRESHOLD:
        ratio = 1 + slope
        return ClassificationResult(
            verdict="sync_drift",
            confidence=0.8,
            suggested_resample_ratio=round(ratio, 5),
        )

    return ClassificationResult(verdict="sync_segmentado", confidence=0.5)
