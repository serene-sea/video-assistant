"""按平台获取视频元数据；B站走专属解析，其他平台走 yt-dlp。"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

from .bilibili import get_bilibili_metadata, is_bilibili_url


BACKEND_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


def build_yt_dlp_options(**overrides) -> dict:
    """返回项目共用的 yt-dlp 配置，并按需加载 Cookie 文件。

    Cookie 文件只通过环境变量提供，不把浏览器 Cookie 内容写进源码或日志。
    这个配置是平台无关的：需要登录态或反爬校验的网站都可以复用。
    """
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "cachedir": False,
    }
    cookie_value = os.getenv("YTDLP_COOKIE_FILE", "").strip()
    if cookie_value:
        cookie_path = Path(cookie_value).expanduser()
        if not cookie_path.is_absolute():
            cookie_path = BACKEND_DIR / cookie_path
        if not cookie_path.is_file():
            raise ValueError("YTDLP_COOKIE_FILE 指向的 Cookie 文件不存在")
        options["cookiefile"] = str(cookie_path)

    options.update(overrides)
    return options


def _validate_video_url(url: str) -> None:
    """先做基础协议和主机检查；公开部署时还需要拦截本机及私网地址。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请输入有效的 http/https 视频链接")


def get_metadata(url: str) -> dict:
    """B站优先走页面专属解析，失败才降级到通用 yt-dlp。"""
    _validate_video_url(url)

    if is_bilibili_url(url):
        try:
            return get_bilibili_metadata(url)
        except Exception as exc:
            # 平台页面结构发生变化时仍保留通用退路，避免专属实现拖垮主流程。
            logger.warning("B站专属元数据解析失败，降级到 yt-dlp：%s", exc)

    options = build_yt_dlp_options(
        # download=False 已表示只解析；该选项再次明确不下载媒体文件。
        skip_download=True,
        extract_flat=False,
    )
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise ValueError("无法解析该视频链接")

    platform = info.get("extractor_key") or info.get("extractor") or "unknown"
    return {
        "source_url": url,
        "source_video_id": str(info.get("id")) if info.get("id") else None,
        "title": info.get("title") or "未命名视频",
        "author": info.get("uploader") or info.get("channel") or info.get("creator"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "platform": str(platform),
        "view_count": info.get("view_count"),
        "description": info.get("description"),
    }
