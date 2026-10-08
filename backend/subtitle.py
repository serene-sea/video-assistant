"""
通用 yt-dlp 字幕选择、下载与 WebVTT 解析
"""

from __future__ import annotations

import html
import logging
import re
import tempfile
from pathlib import Path

import yt_dlp

from .bilibili import extract_bilibili_subtitles, is_bilibili_url
from .video_service import build_yt_dlp_options


PREFERRED_LANGUAGES = ("zh-Hans", "zh-CN", "zh", "en", "ja", "ko")
logger = logging.getLogger(__name__)


def _pick_format_url(formats: list[dict]) -> str | None:
    """这里只用 URL 判断字幕是否可用；下载时仍统一要求 yt-dlp 输出 VTT。"""
    for extension in ("vtt", "json3", "srv3", "ttml"):
        for item in formats:
            if item.get("ext") == extension and item.get("url"):
                return item["url"]
    return next((item.get("url") for item in formats if item.get("url")), None)


def choose_subtitle(
    manual: dict, automatic: dict, preferred: tuple[str, ...] = PREFERRED_LANGUAGES
) -> tuple[str, str]:
    """优先偏好语言的人工字幕，其次自动字幕，再降级到其他语言。"""
    manual = {key: value for key, value in manual.items() if key != "danmaku"}  # 弹幕不算字幕

    for source, subtitles in (("manual", manual), ("auto", automatic)):  # 这个括号表示先遍历人工字幕，再遍历自动字幕。
        for language in preferred:
            if language in subtitles and _pick_format_url(subtitles[language]):  # 如果该语言的字幕存在且有可用 URL，则返回该语言和字幕来源。
                return language, source
    for source, subtitles in (("manual", manual), ("auto", automatic)):
        for language, formats in subtitles.items():
            if _pick_format_url(formats):  # 遍历所有语言的字幕，找到第一个有可用 URL 的字幕。
                return language, source
    return "", "none"


def _time_to_seconds(value: str) -> float:
    """把 ``MM:SS.mmm`` 或 ``HH:MM:SS.mmm`` 转成便于计算的秒数。"""
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError(f"无法识别的字幕时间戳: {value}")


def parse_vtt_text(content: str) -> list[dict]:
    """
    解析常见 WebVTT cue，并只去除相邻的滚动重复字幕
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    # VTT 使用空行分隔 cue；先统一 Windows 和 Unix 换行符，再按空行切块。
    blocks = re.split(r"\n{2,}", normalized)
    time_pattern = re.compile(
        r"(?P<start>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*"
        r"(?P<end>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})"
    )
    segments: list[dict] = []  # 记录每个字幕段的字典列表
    previous_text = ""

    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        # cue 第一行可能是编号，所以不能假设时间戳永远位于第 0 行。
        time_index = next(
            (index for index, line in enumerate(lines) if time_pattern.search(line)),
            None,
        )  # next() 函数用于返回迭代器的下一个项目，这里用于找到第一个匹配时间戳的行的索引。
        if time_index is None:
            continue  # 如果没有找到时间戳行，则跳过该块。
        match = time_pattern.search(lines[time_index])  # 在找到的时间戳行中搜索时间戳模式。
        if not match:
            continue

        text_lines = []
        for line in lines[time_index + 1:]:
            clean = html.unescape(re.sub(r"<[^>]+>", "", line)).strip()  # 去除 HTML 标签并解码 HTML 实体
            if clean:
                text_lines.append(clean)
        text = " ".join(text_lines)

        # 自动字幕常有滚动字幕造成的相邻重复；这里只删除相邻且完全相同的文本。
        if not text or text == previous_text:
            continue
        previous_text = text
        segments.append(
            {
                "segment_id": len(segments),
                "start": round(_time_to_seconds(match.group("start")), 3),
                "end": round(_time_to_seconds(match.group("end")), 3),
                "text": text,
            }
        )  # 追加字幕段字典到 segments 列表中，包含段 ID、开始时间、结束时间和文本内容。
    return segments


def extract_subtitles(url: str) -> dict:
    """获取字幕清单、选择字幕并下载 VTT；无字幕时返回明确状态。"""
    if is_bilibili_url(url):
        try:
            # B站直接查询平台字幕；明确没有字幕时立即进入上层 ASR，不再让
            # yt-dlp 重复做一次字幕清单解析。
            return extract_bilibili_subtitles(url)
        except Exception as exc:
            # 专属接口失效时才退回原来的通用实现。
            logger.warning("B站专属字幕解析失败，降级到 yt-dlp：%s", exc)

    metadata_options = build_yt_dlp_options(skip_download=True)
    with yt_dlp.YoutubeDL(metadata_options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise ValueError("无法解析该视频链接")

    language, source = choose_subtitle(
        info.get("subtitles") or {},
        info.get("automatic_captions") or {},
    )
    if source == "none":
        return {"has_subtitle": False, "language": "", "source": source, "segments": []}

    with tempfile.TemporaryDirectory() as temp_dir:
        options = build_yt_dlp_options(
            skip_download=True,
            writesubtitles=source == "manual",
            writeautomaticsub=source == "auto",
            subtitleslangs=[language],
            subtitlesformat="vtt",
            outtmpl=str(Path(temp_dir) / "subtitle"),
        )
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])  # 下载字幕文件到临时目录
        vtt_files = list(Path(temp_dir).glob("*.vtt"))  # 查找下载的 VTT 文件

        if not vtt_files:
            raise ValueError("字幕存在，但未能下载为 VTT 格式")
        content = vtt_files[0].read_text(encoding="utf-8")  # 读取 VTT 文件内容

    segments = parse_vtt_text(content)
    return {
        "has_subtitle": bool(segments),
        "language": language,
        "source": source,
        "segments": segments,
    }
