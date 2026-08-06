from mediaqc.core.subtitles import codec_supports_text_preview, parse_ass, parse_srt


def test_codec_supports_text_preview():
    assert codec_supports_text_preview("subrip") is True
    assert codec_supports_text_preview("ass") is True
    assert codec_supports_text_preview("hdmv_pgs_subtitle") is False
    assert codec_supports_text_preview(None) is False


def test_parse_srt_basic():
    content = (
        "1\n"
        "00:00:01,480 --> 00:00:04,980\n"
        "Año solar 198\n"
        "\n"
        "2\n"
        "00:00:04,980 --> 00:00:08,480\n"
        "Distrito de Kita, Tokio\n"
    )
    cues = parse_srt(content)
    assert len(cues) == 2
    assert cues[0].index == 1
    assert abs(cues[0].start_s - 1.48) < 0.001
    assert abs(cues[0].end_s - 4.98) < 0.001
    assert cues[0].text == "Año solar 198"
    assert cues[1].text == "Distrito de Kita, Tokio"


def test_parse_srt_multiline_and_html_tags_stripped():
    content = "1\n00:03:28,560 --> 00:03:32,480\n<i>Capítulo 1</i>\nShinra Kusakabe llega a la brigada\n"
    cues = parse_srt(content)
    assert len(cues) == 1
    assert cues[0].text == "Capítulo 1\nShinra Kusakabe llega a la brigada"


def test_parse_srt_ignores_malformed_blocks():
    content = "1\n00:00:01,000 --> 00:00:02,000\nOk\n\ngarbage block without arrow\n"
    cues = parse_srt(content)
    assert len(cues) == 1


def test_parse_srt_empty_content():
    assert parse_srt("") == []


def test_parse_ass_basic():
    content = (
        "[Script Info]\n"
        "Title: test\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.48,0:00:04.98,Default,,0,0,0,,Año solar 198\n"
        "Dialogue: 0,0:00:04.98,0:00:08.48,Default,,0,0,0,,Distrito de Kita\\NTokio\n"
    )
    cues = parse_ass(content)
    assert len(cues) == 2
    assert abs(cues[0].start_s - 1.48) < 0.01
    assert abs(cues[0].end_s - 4.98) < 0.01
    assert cues[0].text == "Año solar 198"
    assert cues[1].text == "Distrito de Kita\nTokio"


def test_parse_ass_strips_override_tags():
    content = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\an8}Texto arriba\n"
    )
    cues = parse_ass(content)
    assert cues[0].text == "Texto arriba"


def test_parse_ass_ignores_non_events_sections():
    content = "[Script Info]\nDialogue: esto no es un evento real\n"
    assert parse_ass(content) == []


def test_parse_ass_no_events_section():
    assert parse_ass("[Script Info]\nTitle: nada\n") == []
