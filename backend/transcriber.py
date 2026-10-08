"""无平台字幕时，下载临时音频并使用 faster-whisper 转写。

本文件只负责“音频 → 带时间戳文本”。总结仍由 summarizer.py 完成，
所以 ASR 与 LLM 的职责不会混在一起。
"""

from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path

import yt_dlp
from faster_whisper import WhisperModel

from .video_service import build_yt_dlp_options


MIN_TRANSCRIPT_CHARS = int(os.getenv("MIN_TRANSCRIPT_CHARS", "40"))


@lru_cache(maxsize=2)
def _get_whisper_model(
    model_name: str, device: str, compute_type: str
) -> WhisperModel:
    """缓存模型，避免每处理一个视频都重新把模型加载到内存或显存。"""
    return WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
    )


def _find_downloaded_audio(temp_dir: str) -> Path:
    """找到 yt-dlp 最终生成的音频文件，并忽略未完成的 .part 文件。"""
    candidates = [
        path
        for path in Path(temp_dir).glob("audio.*")
        if path.is_file() and path.suffix != ".part"
    ]
    if not candidates:
        raise ValueError("音频下载完成后没有找到可转写文件")
    return max(candidates, key=lambda path: path.stat().st_size)


def transcribe_video(url: str) -> dict:
    """下载最佳音轨并返回与字幕结构一致的转写结果。

    ``TemporaryDirectory`` 离开 with 后会自动删除音频；即使转写抛出异常，
    也不会把用户视频长期留在服务器磁盘中。
    """
    model_name = os.getenv("WHISPER_MODEL", "medium")
    device = os.getenv("WHISPER_DEVICE", "cuda")
    compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "float16")

    try:
        with tempfile.TemporaryDirectory(prefix="video_assistant_asr_") as temp_dir:
            options = build_yt_dlp_options(
                format="bestaudio/best",
                outtmpl=str(Path(temp_dir) / "audio.%(ext)s"),
                skip_download=False,
            )
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([url])

            audio_path = _find_downloaded_audio(temp_dir)
            model = _get_whisper_model(model_name, device, compute_type)
            # segments 是延迟执行的生成器；真正的识别在下面 for 迭代时发生。
            whisper_segments, info = model.transcribe(
                str(audio_path),
                beam_size=5,
                vad_filter=True,
            )

            segments: list[dict] = []
            for item in whisper_segments:
                text = item.text.strip()
                if not text:
                    continue
                segments.append(
                    {
                        "segment_id": len(segments),
                        "start": round(float(item.start), 3),
                        "end": round(float(item.end), 3),
                        "text": text,
                    }
                )
    except ValueError:
        raise
    except Exception as exc:
        # 上层会把可预期的 ASR 失败降级为标题/简介总结。
        raise ValueError(f"语音识别失败：{exc}") from exc

    compact_text = "".join(
        "".join(segment["text"].split()) for segment in segments
    )
    has_transcript = len(compact_text) >= max(1, MIN_TRANSCRIPT_CHARS)
    return {
        "has_transcript": has_transcript,
        "language": info.language or "",
        "source": "asr",
        # 太短的识别结果往往是音乐或环境声误识别，不把它作为可靠字幕保存。
        "segments": segments if has_transcript else [],
    }
