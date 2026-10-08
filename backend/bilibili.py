"""B站视频元数据与平台字幕的轻量专属解析。

先读取视频页面中的 ``window.__INITIAL_STATE__``，再调用可用的播放器字幕接口。
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import httpx


_BVID_PATTERN = re.compile(r"(BV[a-zA-Z0-9]+)")
_INITIAL_STATE_PATTERN = re.compile(r"window\.__INITIAL_STATE__\s*=\s*")
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bilibili.com/",
}


def is_bilibili_url(url: str) -> bool:
    """严格检查主机名，避免用字符串包含关系误判恶意域名。"""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return (
        host == "bilibili.com"
        or host.endswith(".bilibili.com")
        or host == "b23.tv"
        or host.endswith(".b23.tv")
    )


def _extract_bvid(url: str) -> str:
    """从标准链接、查询参数或短链跳转后的地址中提取 BV 号。"""
    match = _BVID_PATTERN.search(url)
    if not match:
        raise ValueError("无法从 B站链接中提取 BV 号")
    return match.group(1)


def _read_initial_state(page_html: str) -> dict:
    """读取页面脚本中的完整 JSON，不用无法处理嵌套对象的简单正则。"""
    match = _INITIAL_STATE_PATTERN.search(page_html)
    if not match:
        raise ValueError("B站页面中没有找到视频数据")

    # raw_decode 会从第一个 { 开始读取一个完整 JSON 对象，并在对象结束处停止。
    json_text = page_html[match.end():].lstrip()
    try:
        state, _ = json.JSONDecoder().raw_decode(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError("B站页面视频数据格式发生变化") from exc
    return state


def _load_video_data(url: str) -> tuple[dict, str]:
    """跟随 b23.tv 短链并从最终视频页面读取元数据。"""
    with httpx.Client(
        headers=_DEFAULT_HEADERS,
        timeout=15,
        follow_redirects=True,
    ) as client:
        response = client.get(url)
        response.raise_for_status()

    resolved_url = str(response.url)
    if not is_bilibili_url(resolved_url):
        raise ValueError("B站短链没有跳转到 B站视频页面")

    state = _read_initial_state(response.text)
    video_data = state.get("videoData") or state.get("videoInfo")
    if not isinstance(video_data, dict) or not video_data:
        raise ValueError("B站页面中没有找到可用的视频信息")
    return video_data, resolved_url


def _select_page(video_data: dict, resolved_url: str) -> dict:
    """多分P视频按 URL 的 p 参数选择当前分P；普通视频返回默认 cid。"""
    pages = video_data.get("pages") or []
    raw_part = parse_qs(urlparse(resolved_url).query).get("p", ["1"])[0]
    try:
        part_index = max(0, int(raw_part) - 1)
    except (TypeError, ValueError):
        part_index = 0
    if pages and part_index < len(pages):
        return pages[part_index]
    return {"cid": video_data.get("cid"), "duration": video_data.get("duration")}


def _request_json(url: str, *, params: dict | None = None, referer: str) -> dict:
    """请求 B站 JSON 接口，并同时检查 HTTP 状态和响应内的业务状态码。"""
    headers = {**_DEFAULT_HEADERS, "Referer": referer, "Origin": "https://www.bilibili.com"}
    response = httpx.get(url, params=params, headers=headers, timeout=15)
    response.raise_for_status()
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ValueError("B站接口没有返回 JSON") from exc
    # 播放器接口带 code 字段，字幕文件本身通常只有 body；两种 JSON 共用此函数。
    if "code" in payload and payload.get("code") != 0:
        raise ValueError(f"B站接口拒绝请求：{payload.get('message') or payload.get('code')}")
    return payload


def get_bilibili_metadata(url: str) -> dict:
    """不经过 yt-dlp，直接把 B站页面字段转换成项目统一的视频结构。"""
    video_data, resolved_url = _load_video_data(url)
    bvid = video_data.get("bvid") or _extract_bvid(resolved_url)
    page = _select_page(video_data, resolved_url)
    owner = video_data.get("owner") or {}
    stats = video_data.get("stat") or {}

    return {
        "source_url": url,
        "source_video_id": str(bvid),
        "title": video_data.get("title") or "未命名视频",
        "author": owner.get("name"),
        "thumbnail": video_data.get("pic"),
        "duration": page.get("duration") or video_data.get("duration"),
        "platform": "BiliBili",
        "view_count": stats.get("view"),
        "description": video_data.get("desc"),
    }


def _pick_subtitle(subtitles: list[dict]) -> dict | None:
    """优先中文，再选英文，最后使用平台返回的第一条字幕。"""
    if not subtitles:
        return None
    for prefix in ("zh", "ai-zh", "en"):
        match = next(
            (item for item in subtitles if str(item.get("lan", "")).startswith(prefix)),
            None,
        )
        if match:
            return match
    return subtitles[0]


def extract_bilibili_subtitles(url: str) -> dict:
    """通过 B站播放器接口获取平台字幕；烧录在画面里的文字不会出现在这里。"""
    video_data, resolved_url = _load_video_data(url)
    bvid = video_data.get("bvid") or _extract_bvid(resolved_url)
    page = _select_page(video_data, resolved_url)
    cid = page.get("cid")
    if not cid:
        raise ValueError("B站页面中没有找到视频 cid")

    player = _request_json(
        "https://api.bilibili.com/x/player/v2",
        params={"bvid": bvid, "cid": cid},
        referer=resolved_url,
    )
    subtitle_items = (
        ((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    )
    selected = _pick_subtitle(subtitle_items)
    if not selected:
        return {"has_subtitle": False, "language": "", "source": "none", "segments": []}

    subtitle_url = selected.get("subtitle_url") or ""
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    if not subtitle_url:
        raise ValueError("B站字幕条目中没有下载地址")

    subtitle_data = _request_json(subtitle_url, referer=resolved_url)
    segments = []
    for item in subtitle_data.get("body") or []:
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        segments.append(
            {
                "segment_id": len(segments),
                "start": round(float(item.get("from") or 0), 3),
                "end": round(float(item.get("to") or 0), 3),
                "text": text,
            }
        )

    language = str(selected.get("lan") or "")
    source = "auto" if language.startswith("ai-") else "manual"
    return {
        "has_subtitle": bool(segments),
        "language": language,
        "source": source,
        "segments": segments,
    }
