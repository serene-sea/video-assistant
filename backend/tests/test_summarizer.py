from backend.summarizer import build_subtitle_chunks


def test_build_chunks_keeps_order_and_source_ids() -> None:
    """分块不能打乱字幕顺序，也要保留每块来自哪些原始片段。"""
    segments = [
        {"segment_id": 0, "start": 0.0, "end": 2.0, "text": "第一段内容"},
        {"segment_id": 1, "start": 2.0, "end": 4.0, "text": "第二段内容"},
    ]

    chunks = build_subtitle_chunks(segments, max_chars=30)

    assert len(chunks) == 2
    assert chunks[0]["source_segment_ids"] == [0]
    assert chunks[1]["source_segment_ids"] == [1]
    assert chunks[0]["start"] == 0.0
    assert chunks[1]["end"] == 4.0
