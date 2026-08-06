"""Decide qué se compara contra qué antes de analizar (spec sección 5.3b).

Es lo que hace que el modo interno funcione sin ningún archivo externo: un
episodio con japonés + latino genera un par (japonés=referencia,
latino=candidato); uno con japonés + latino + castellano genera dos pares.
El modo externo (candidatos sueltos) llega en fases posteriores.
"""

from __future__ import annotations

from dataclasses import dataclass

from mediaqc.core.db.models import Episode, Track
from mediaqc.core.langcodes import language_matches


@dataclass
class AnalysisPair:
    episode_id: int
    pair_source: str  # 'internal' | 'external' (por ahora solo 'internal')
    ref_track_index: int
    cand_track_index: int
    ref_language: str | None
    cand_language: str | None
    low_confidence: bool  # True si la referencia se eligió sin señal de idioma


def choose_reference_track(episode: Episode, reference_language: str | None) -> tuple[Track | None, bool]:
    """Orden de elección (spec 5.3b):
    1. La pista cuyo idioma coincide con ``reference_language`` (viene de
       ``series.reference_language``, poblado desde el ``original_language``
       de TMDB al aplicar un match).
    2. La marcada ``is_default``.
    3. La primera pista de audio, marcada ``low_confidence`` para que la UI
       la deje sobrescribir.

    Devuelve ``(pista, low_confidence)``.
    """
    audio_tracks = [t for t in episode.tracks if t.type == "audio"]
    if not audio_tracks:
        return None, True

    if reference_language:
        for t in audio_tracks:
            if language_matches(t.language, reference_language):
                return t, False

    for t in audio_tracks:
        if t.is_default:
            return t, reference_language is not None  # hubo idioma configurado pero no matcheó ninguna pista

    return audio_tracks[0], True


def generate_internal_pairs(episode: Episode, reference_language: str | None) -> list[AnalysisPair]:
    """Un par por cada pista de audio que no sea la referencia (spec 5.3b).
    Un episodio con una sola pista de audio no genera pares -- no es un
    error, su veredicto es directo (ok o audio_faltante) sin necesidad de
    analizar nada."""
    ref_track, low_confidence = choose_reference_track(episode, reference_language)
    if ref_track is None:
        return []

    pairs = []
    for t in episode.tracks:
        # comparar por stream_index, no por t.id: es el identificador
        # natural ya usado en toda la app, y funciona igual con objetos
        # todavía no persistidos (t.id es None hasta el commit).
        if t.type != "audio" or t.stream_index == ref_track.stream_index:
            continue
        pairs.append(
            AnalysisPair(
                episode_id=episode.id,
                pair_source="internal",
                ref_track_index=ref_track.stream_index,
                cand_track_index=t.stream_index,
                ref_language=ref_track.language,
                cand_language=t.language,
                low_confidence=low_confidence,
            )
        )
    return pairs
