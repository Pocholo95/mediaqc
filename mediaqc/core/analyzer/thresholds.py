"""Todos los umbrales del analizador, concentrados acá (spec sección 5.5).

Nada de números mágicos dispersos por correlate.py/classify.py.
"""

from __future__ import annotations

# Una ventana es válida si su score supera esto; con menos de
# MIN_VALID_WINDOWS ventanas válidas, no hay base para clasificar.
MIN_SCORE_VALID = 5.0
MIN_VALID_WINDOWS = 2

# Offset "constante" si el desvío estándar entre ventanas válidas es chico.
CONSTANT_OFFSET_STD_MS = 30.0
# Dentro de este margen, un offset constante se considera "ok" (no hace
# falta re-muxear por un par de frames de diferencia).
CONSTANT_OFFSET_OK_MS = 50.0

# sync_drift: qué tan bien tiene que ajustar la recta offset~posición.
DRIFT_R2_THRESHOLD = 0.95

# audio idéntico (pista_duplicada / audio_incompleto): score y correlación
# de forma de onda por encima de esto en offset≈0 significa "es la misma
# señal", no "está muy bien sincronizada".
DUPLICATE_SCORE_THRESHOLD = 40.0
DUPLICATE_CORR_THRESHOLD = 0.98

# No buscar picos de correlación más allá de esto (evita falsos positivos
# con openings idénticos entre episodios de anime, spec trampa #3).
SEARCH_RANGE_S = 30.0
