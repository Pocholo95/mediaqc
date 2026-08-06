from mediaqc.core.langcodes import language_matches


def test_matches_iso_639_1_reference_against_639_2_track():
    # TMDB da "ja" (639-1); la pista del archivo dice "jpn" (639-2).
    assert language_matches("jpn", "ja") is True


def test_matches_exact_same_code():
    assert language_matches("spa", "spa") is True


def test_no_match_different_languages():
    assert language_matches("eng", "ja") is False


def test_handles_case_insensitivity():
    assert language_matches("JPN", "JA") is True


def test_none_values_do_not_match():
    assert language_matches(None, "ja") is False
    assert language_matches("jpn", None) is False


def test_french_bibliographic_and_terminological_variants():
    assert language_matches("fre", "fr") is True
    assert language_matches("fra", "fr") is True
