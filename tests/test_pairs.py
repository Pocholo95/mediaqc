from mediaqc.core.db.models import Episode, Track
from mediaqc.core.pairs import choose_reference_track, generate_internal_pairs


def _track(language, stream_index, is_default=False):
    return Track(type="audio", language=language, stream_index=stream_index, is_default=is_default)


def test_choose_reference_prefers_configured_language():
    ep = Episode(number=1, path="x.mkv")
    ep.tracks = [_track("spa", 1, is_default=True), _track("jpn", 2)]
    track, low_confidence = choose_reference_track(ep, "jpn")
    assert track.language == "jpn"
    assert low_confidence is False


def test_choose_reference_matches_tmdb_iso_639_1_against_track_iso_639_2():
    # series.reference_language viene de TMDB en 639-1 ("ja"); las pistas
    # del archivo están en 639-2 ("jpn") -- caso real que rompía sin el
    # mapeo de core/langcodes.py (la referencia caía siempre al default,
    # que en un rip con doblaje suele ser el doblaje, no el original).
    ep = Episode(number=1, path="x.mkv")
    ep.tracks = [_track("spa", 1, is_default=True), _track("jpn", 2)]
    track, low_confidence = choose_reference_track(ep, "ja")
    assert track.language == "jpn"
    assert low_confidence is False


def test_choose_reference_falls_back_to_default_without_configured_language():
    ep = Episode(number=1, path="x.mkv")
    ep.tracks = [_track("spa", 1, is_default=True), _track("jpn", 2)]
    track, low_confidence = choose_reference_track(ep, None)
    assert track.language == "spa"


def test_choose_reference_falls_back_to_first_track_low_confidence():
    ep = Episode(number=1, path="x.mkv")
    ep.tracks = [_track("eng", 1), _track("fra", 2)]
    track, low_confidence = choose_reference_track(ep, None)
    assert track.language == "eng"
    assert low_confidence is True


def test_choose_reference_no_audio_tracks():
    ep = Episode(number=1, path="x.mkv")
    ep.tracks = []
    track, low_confidence = choose_reference_track(ep, "jpn")
    assert track is None
    assert low_confidence is True


def test_generate_internal_pairs_excludes_reference():
    ep = Episode(id=1, number=1, path="x.mkv")
    ep.tracks = [_track("jpn", 1), _track("spa", 2, is_default=True)]
    pairs = generate_internal_pairs(ep, "jpn")
    assert len(pairs) == 1
    assert pairs[0].ref_track_index == 1
    assert pairs[0].cand_track_index == 2
    assert pairs[0].pair_source == "internal"


def test_generate_internal_pairs_one_per_non_reference_track():
    ep = Episode(id=1, number=1, path="x.mkv")
    ep.tracks = [_track("jpn", 1), _track("spa", 2), _track("cat", 3)]
    pairs = generate_internal_pairs(ep, "jpn")
    assert {p.cand_track_index for p in pairs} == {2, 3}


def test_generate_internal_pairs_empty_for_single_audio_track():
    ep = Episode(id=1, number=1, path="x.mkv")
    ep.tracks = [_track("jpn", 1)]
    assert generate_internal_pairs(ep, "jpn") == []
