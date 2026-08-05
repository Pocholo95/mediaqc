"""Posiciones de ventana para el análisis de sincronización (spec sección 5.5).

Se usan tanto para la correlación cruzada (fase 4, todavía no implementada)
como para los botones de salto de la vista de revisión (fase 3): son
independientes del análisis en sí, solo dependen del runtime del episodio.
"""

from __future__ import annotations

WINDOW_POSITIONS_PCT = (0.02, 0.25, 0.50, 0.75, 0.95)


def window_positions_seconds(duration_s: float) -> list[float]:
    """5 posiciones dentro del runtime, evitando el primer/último minuto
    absolutos cuando el episodio es lo bastante largo (logos, negros,
    silencio -- spec sección 5.5)."""
    if not duration_s or duration_s <= 0:
        return [0.0] * len(WINDOW_POSITIONS_PCT)

    lo, hi = 0.0, duration_s
    if duration_s > 150:
        lo, hi = 60.0, duration_s - 60.0

    return [min(max(pct * duration_s, lo), hi) for pct in WINDOW_POSITIONS_PCT]
