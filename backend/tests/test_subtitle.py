from backend.subtitle import choose_subtitle, parse_vtt_text


def test_manual_subtitle_has_priority() -> None:
    language, source = choose_subtitle(
        {"zh": [{"ext": "vtt", "url": "https://example.com/manual.vtt"}]},
        {"zh": [{"ext": "vtt", "url": "https://example.com/auto.vtt"}]},
    )
    assert (language, source) == ("zh", "manual")


def test_parse_vtt_supports_crlf_and_removes_adjacent_duplicates() -> None:
    content = (
        "WEBVTT\r\n\r\n"
        "00:00.000 --> 00:02.000\r\n<b>第一句</b>\r\n\r\n"
        "00:02.000 --> 00:04.000\r\n第一句\r\n\r\n"
        "00:04.000 --> 00:06.500\r\n第二句 &amp; 补充\r\n"
    )

    assert parse_vtt_text(content) == [
        {"segment_id": 0, "start": 0.0, "end": 2.0, "text": "第一句"},
        {"segment_id": 1, "start": 4.0, "end": 6.5, "text": "第二句 & 补充"},
    ]
