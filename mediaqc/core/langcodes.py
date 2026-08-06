"""Equivalencias mínimas ISO 639-1 <-> ISO 639-2.

TMDB devuelve ``original_language`` en 639-1 (``"ja"``); ffprobe/mkvmerge
etiquetan las pistas en 639-2 (``"jpn"``). Sin este mapeo,
``series.reference_language`` (poblado desde TMDB) nunca matchea contra
``track.language`` (poblado desde el archivo), y ``core/pairs.py`` cae
siempre al fallback de ``is_default`` -- que en un rip con doblaje suele ser
el doblaje, no el idioma original que se necesita como referencia.

No pretende ser exhaustivo, cubre los idiomas más comunes en librerías de
medios. Fácil de extender si aparece un idioma que falta.
"""

from __future__ import annotations

ISO_639_1_TO_2 = {
    "ja": {"jpn"},
    "en": {"eng"},
    "es": {"spa"},
    "fr": {"fre", "fra"},
    "de": {"ger", "deu"},
    "it": {"ita"},
    "pt": {"por"},
    "ko": {"kor"},
    "zh": {"chi", "zho"},
    "ru": {"rus"},
    "ar": {"ara"},
    "hi": {"hin"},
    "nl": {"dut", "nld"},
    "sv": {"swe"},
    "no": {"nor"},
    "da": {"dan"},
    "fi": {"fin"},
    "pl": {"pol"},
    "tr": {"tur"},
    "th": {"tha"},
    "vi": {"vie"},
    "id": {"ind"},
    "he": {"heb"},
    "el": {"gre", "ell"},
    "cs": {"cze", "ces"},
    "hu": {"hun"},
    "ro": {"rum", "ron"},
    "uk": {"ukr"},
    "ca": {"cat"},
}


def language_matches(track_language: str | None, reference_language: str | None) -> bool:
    """``reference_language`` viene de TMDB (639-1); ``track_language`` del
    archivo, en cualquiera de los dos estándares según quien lo haya
    muxeado. Compara ambos."""
    if not track_language or not reference_language:
        return False
    t = track_language.strip().lower()
    ref = reference_language.strip().lower()
    if t == ref:
        return True
    return t in ISO_639_1_TO_2.get(ref, set())
