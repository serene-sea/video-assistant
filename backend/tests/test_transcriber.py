from pathlib import Path
from types import SimpleNamespace

from backend import transcriber


def test_transcribe_video_returns_timestamped_segments_and_cleans_temp(
    monkeypatch,
) -> None:
    """ASR 结果要保留时间戳，函数结束后临时音频应被自动删除。"""
    downloaded_paths: list[Path] = []

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, _urls):
            path = Path(self.options["outtmpl"].replace("%(ext)s", "m4a"))
            path.write_bytes(b"fake audio")
            downloaded_paths.append(path)

    class FakeWhisperModel:
        def transcribe(self, audio_path, **options):
            assert Path(audio_path).exists()
            assert options["vad_filter"] is True
            segments = [
                SimpleNamespace(start=0.0, end=2.5, text="第一段测试语音内容"),
                SimpleNamespace(start=2.5, end=5.0, text="第二段测试语音内容"),
            ]
            return iter(segments), SimpleNamespace(language="zh")

    monkeypatch.setattr(transcriber.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        transcriber,
        "_get_whisper_model",
        lambda *_args: FakeWhisperModel(),
    )
    monkeypatch.setattr(transcriber, "MIN_TRANSCRIPT_CHARS", 10)

    result = transcriber.transcribe_video("https://example.com/video")

    assert result["has_transcript"] is True
    assert result["source"] == "asr"
    assert result["language"] == "zh"
    assert result["segments"][1]["start"] == 2.5
    assert downloaded_paths and not downloaded_paths[0].exists()


def test_transcribe_video_rejects_too_short_result(monkeypatch) -> None:
    """音乐或环境声产生的极短误识别不能被当成可靠视频内容。"""
    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, _urls):
            Path(self.options["outtmpl"].replace("%(ext)s", "m4a")).write_bytes(
                b"fake"
            )

    class FakeWhisperModel:
        def transcribe(self, _audio_path, **_options):
            return iter([SimpleNamespace(start=0, end=1, text="嘿")]), SimpleNamespace(
                language="zh"
            )

    monkeypatch.setattr(transcriber.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        transcriber,
        "_get_whisper_model",
        lambda *_args: FakeWhisperModel(),
    )
    monkeypatch.setattr(transcriber, "MIN_TRANSCRIPT_CHARS", 40)

    result = transcriber.transcribe_video("https://example.com/video")

    assert result["has_transcript"] is False
    assert result["segments"] == []
