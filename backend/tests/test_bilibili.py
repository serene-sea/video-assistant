from backend import bilibili, subtitle, video_service


def _fake_video_data() -> dict:
    return {
        "bvid": "BV1TEST12345",
        "title": "测试视频",
        "owner": {"name": "测试作者"},
        "pic": "https://example.com/cover.jpg",
        "duration": 60,
        "pages": [{"cid": 123, "duration": 59}],
        "stat": {"view": 456},
        "desc": "测试简介",
    }


def test_is_bilibili_url_uses_hostname_instead_of_substring() -> None:
    assert bilibili.is_bilibili_url("https://www.bilibili.com/video/BV1TEST12345")
    assert bilibili.is_bilibili_url("https://b23.tv/abcd")
    assert not bilibili.is_bilibili_url("https://evil.example/bilibili.com/video")


def test_bilibili_metadata_comes_from_page_data(monkeypatch) -> None:
    monkeypatch.setattr(
        bilibili,
        "_load_video_data",
        lambda _url: (_fake_video_data(), "https://www.bilibili.com/video/BV1TEST12345"),
    )

    result = bilibili.get_bilibili_metadata(
        "https://www.bilibili.com/video/BV1TEST12345"
    )

    assert result["source_video_id"] == "BV1TEST12345"
    assert result["platform"] == "BiliBili"
    assert result["duration"] == 59
    assert result["view_count"] == 456


def test_bilibili_subtitle_is_converted_to_shared_segment_format(monkeypatch) -> None:
    monkeypatch.setattr(
        bilibili,
        "_load_video_data",
        lambda _url: (_fake_video_data(), "https://www.bilibili.com/video/BV1TEST12345"),
    )

    def fake_request(url: str, **_kwargs) -> dict:
        if "player/v2" in url:
            return {
                "code": 0,
                "data": {
                    "subtitle": {
                        "subtitles": [
                            {
                                "lan": "ai-zh",
                                "subtitle_url": "//example.com/subtitle.json",
                            }
                        ]
                    }
                },
            }
        return {
            "code": 0,
            "body": [
                {"from": 0, "to": 2.5, "content": "第一句"},
                {"from": 2.5, "to": 5, "content": "第二句"},
            ],
        }

    monkeypatch.setattr(bilibili, "_request_json", fake_request)
    result = bilibili.extract_bilibili_subtitles(
        "https://www.bilibili.com/video/BV1TEST12345"
    )

    assert result["source"] == "auto"
    assert result["language"] == "ai-zh"
    assert result["segments"][1] == {
        "segment_id": 1,
        "start": 2.5,
        "end": 5.0,
        "text": "第二句",
    }


def test_bilibili_dispatch_avoids_yt_dlp_when_special_path_succeeds(
    monkeypatch,
) -> None:
    expected_metadata = {"platform": "BiliBili"}
    expected_subtitles = {
        "has_subtitle": False,
        "language": "",
        "source": "none",
        "segments": [],
    }
    monkeypatch.setattr(video_service, "get_bilibili_metadata", lambda _url: expected_metadata)
    monkeypatch.setattr(subtitle, "extract_bilibili_subtitles", lambda _url: expected_subtitles)

    class ForbiddenYoutubeDL:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("B站专属路径成功时不应创建 yt-dlp")

    monkeypatch.setattr(video_service.yt_dlp, "YoutubeDL", ForbiddenYoutubeDL)
    monkeypatch.setattr(subtitle.yt_dlp, "YoutubeDL", ForbiddenYoutubeDL)

    url = "https://www.bilibili.com/video/BV1TEST12345"
    assert video_service.get_metadata(url) is expected_metadata
    assert subtitle.extract_subtitles(url) is expected_subtitles
