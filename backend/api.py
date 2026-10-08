""" Video Assistant 的统一 API 层 """

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field, field_validator  # pydantic 是 FastAPI 默认使用的数据验证和序列化库，Field 用于定义模型字段的约束和默认值，field_validator 用于自定义字段验证逻辑。
from starlette.concurrency import run_in_threadpool  # 线程池

from .agent import AGENT_REFUSAL, run_agent
from .chat_service import CHAT_HISTORY_MESSAGE_LIMIT, VideoChatService
from .citation_parser import CitationStreamParser
from .database import (
    create_conversation,
    create_video,
    delete_conversation,
    get_chunk_summaries_for_video,
    get_conversation_for_video,
    get_summary_for_video,
    get_transcript_segments_by_ids,
    get_video_for_session,
    list_conversation_messages,
    list_conversations_for_video,
    list_recent_messages,
    list_videos_for_session,
    replace_transcript_segments,
    save_message,
    save_summary,
    save_transcript_chunks,
    update_chunk_summary,
    update_video_status,
)
from .embedding_provider import EmbeddingProvider
from .retriever import delete_video_index_async, ensure_video_index, search_video
from .session import ensure_session, get_current_session
from .subtitle import extract_subtitles
from .summarizer import VideoSummarizer, prepare_summary_chunks
from .transcriber import transcribe_video
from .video_service import get_metadata


# prefix 会自动加到本文件的每条路由前面，例如 /session 最终是 /api/session。
router = APIRouter(prefix="/api", tags=["video-assistant"])
logger = logging.getLogger(__name__)
RAG_REFUSAL = "我暂时没能从这个视频内容中找到足够信息来回答。"
PROCESS_DETACH_GRACE_SECONDS = 8


@dataclass
class _ProcessJob:
    """把视频处理放到 SSE 连接之外，刷新时可以重新订阅同一任务。"""

    events: list[ServerSentEvent] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    cancel_task: asyncio.Task | None = None
    finished: bool = False


_PROCESS_JOBS: dict[str, _ProcessJob] = {}
_PARSE_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


class ParseVideoRequest(BaseModel):
    """视频解析请求体；Pydantic 会在进入路由前完成类型和长度校验。"""

    url: str = Field(min_length=1, max_length=2048)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        # 去除复制链接时常见的首尾空格，避免把它们传给 yt-dlp。
        return value.strip()


class VideoResponse(BaseModel):
    """只返回前端需要的字段，不暴露数据库内部的 session_id。"""

    video_id: str
    source_url: str
    title: str
    author: str | None = None
    thumbnail: str | None = None
    duration: float | None = None
    platform: str
    view_count: int | None = None
    description: str | None = None
    status: str
    subtitle_source: str | None = None
    language: str | None = None
    error_message: str | None = None


class ProcessVideoRequest(BaseModel):
    """视频处理请求；当前只需要指定总结输出语言。"""
    # 声明一个名为 language 的字符串字段，如果前端没传这个值，就默认用 "zh"；如果传了，长度必须在 2 到 20 个字符之间
    language: str = Field(default="zh", min_length=2, max_length=20)


class ChatRequest(BaseModel):
    """视频问答请求；前端只传问题和对话 ID，不回传完整字幕。"""

    question: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = Field(default=None, max_length=80)
    # Agent、RAG 和总结问答共用同一会话接口，但走各自独立的处理路径。
    mode: Literal["summary", "rag", "deep"] = "summary"

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


class SavedSummaryResponse(BaseModel):
    """页面恢复时返回已经保存的总结，不会再次调用 LLM。"""

    video_id: str
    content: str
    strategy: str
    model: str
    updated_at: str


class ConversationResponse(BaseModel):
    """历史会话列表项；preview 是第一条用户问题的简短预览。"""

    conversation_id: str
    preview: str
    created_at: str
    updated_at: str


class HistoryVideoResponse(BaseModel):
    """历史列表只返回卡片所需字段，不携带字幕或总结正文。"""

    video_id: str
    title: str
    author: str | None = None
    thumbnail: str | None = None
    duration: float | None = None
    platform: str
    status: str
    subtitle_source: str | None = None
    created_at: str


class MessageResponse(BaseModel):
    """恢复到聊天界面的单条历史消息。"""

    message_id: int
    role: Literal["user", "assistant"]
    content: str
    citations: list[dict] = Field(default_factory=list)
    created_at: str









def _to_video_response(video: dict) -> VideoResponse:
    """把数据库字典转换成固定响应，避免直接返回包含 session_id 的整行数据。"""
    response_fields = {
        key: video.get(key)
        for key in VideoResponse.model_fields
        if key != "video_id"
    }
    # B站图片站可能拒绝浏览器从本地开发站点直连；改走带官方 Referer 的本项目代理。
    if str(video.get("platform", "")).casefold().startswith("bilibili") and video.get("thumbnail"):
        response_fields["thumbnail"] = f"/api/videos/{video['id']}/cover"
    return VideoResponse(video_id=video["id"], **response_fields)


def _normalize_bilibili_cover_url(raw_url: str) -> str:
    """只接受 B站图片域名，并把常见的 http 图片地址升级为 https。"""
    candidate = f"https:{raw_url}" if raw_url.startswith("//") else raw_url
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed_suffixes = ("hdslb.com", "bilivideo.com")
    allowed_host = any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in allowed_suffixes
    )
    if (
        parsed.scheme not in {"http", "https"}
        or not allowed_host
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(status_code=502, detail="视频封面地址无效")
    try:
        if parsed.port not in {None, 80, 443}:
            raise HTTPException(status_code=502, detail="视频封面端口无效")
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="视频封面地址无效") from exc

    # 不沿用 URL 中可能存在的非标准端口，避免把图片代理变成任意端口访问器。
    return urlunsplit(("https", host, parsed.path, parsed.query, ""))


def _canonical_source_url(raw_url: str) -> str:
    """去掉常见分享追踪参数，让同一链接不会重复创建视频记录。"""
    parsed = urlsplit(raw_url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return raw_url.strip()
    try:
        port = parsed.port
    except ValueError:
        port = None
    scheme = parsed.scheme.lower() or "https"
    default_port = port is None or (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = host if default_port else f"{host}:{port}"
    ignored = {"spm_id_from", "vd_source", "from", "trackid", "t", "from_spmid"}
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in ignored
            and not key.casefold().startswith(("utm_", "share_"))
        )
    )
    return urlunsplit((scheme, netloc, parsed.path.rstrip("/"), query, ""))


def _video_identity(video: dict) -> tuple[str, str, str] | None:
    """按平台视频编号去重；B站分P仍按各自页面区分。"""
    source_video_id = str(video.get("source_video_id") or "").strip()
    platform = str(video.get("platform") or "").casefold()
    if not source_video_id:
        return None
    part = ""
    if platform.startswith("bilibili"):
        part = dict(parse_qsl(urlsplit(video.get("source_url", "")).query)).get("p", "1")
    return platform, source_video_id, part


def _find_existing_video(session_id: str, source_url: str, metadata: dict | None = None) -> dict | None:
    """优先按规范化链接复用；链接写法不同时再按稳定平台视频编号匹配。"""
    videos = list_videos_for_session(session_id)
    canonical = _canonical_source_url(source_url)
    for video in videos:
        if _canonical_source_url(video.get("source_url", "")) == canonical:
            return video
    identity = _video_identity({**(metadata or {}), "source_url": source_url}) if metadata else None
    if identity:
        for video in videos:
            if _video_identity(video) == identity:
                return video
    return None


def _video_parse_error_detail(exc: Exception) -> str:
    """把 yt-dlp 的常见失败原因转换成简短、可操作的前端提示。

    412/403 只说明平台拒绝了这一次请求，不能据此断定用户一定没有配置
    Cookie；只有 yt-dlp 明确提示需要 Cookie 时，才给出确定的 Cookie 提示。
    """
    error_text = str(exc).lower()

    if "unsupported url" in error_text:
        return "暂不支持这种视频链接格式，请尝试视频详情页的标准链接"
    if "fresh cookies" in error_text or "cookies are needed" in error_text:
        return "视频平台要求提供新的浏览器 Cookie，请配置 YTDLP_COOKIE_FILE 后重试"
    if "http error 412" in error_text or "http error 403" in error_text:
        return "视频平台临时拒绝访问，请稍后重试；若持续失败，可配置 YTDLP_COOKIE_FILE"
    return "视频平台解析失败，请确认链接可公开访问"


@router.get("/health")
async def health_check() -> dict:
    """供本地启动和部署平台判断后端是否正常运行。"""
    return {"status": "ok"}  # 正常则运行这段代码并返回200状态码，异常返回 HTTP 500


@router.post("/session")
async def ensure_anonymous_session(request: Request, response: Response) -> dict:
    """创建或恢复匿名会话。

    Request 用于读取浏览器已有 Cookie，Response 用于写入新的 HttpOnly Cookie；
    Token 的生成和校验仍由 session.py 负责。
    """
    session = ensure_session(request, response)
    return {"session_id": session["id"], "expires_at": session["expires_at"]}


@router.post("/videos/parse", response_model=VideoResponse)
async def parse_video(
    request: ParseVideoRequest,
    session: dict = Depends(get_current_session),
) -> VideoResponse:
    """获取视频基本信息并创建数据库记录，不下载视频文件。"""
    lock_key = (session["id"], _canonical_source_url(request.url))
    lock = _PARSE_LOCKS.setdefault(lock_key, asyncio.Lock())
    async with lock:
        # 对完全相同的视频链接先查 SQLite，避免重复请求元数据，更不会重新跑总结 LLM。
        existing = _find_existing_video(session["id"], request.url)
        if existing:
            return _to_video_response(existing)
        try:
            # B站专属 HTTP 请求和 yt-dlp 都是同步阻塞操作，统一放入线程池，
            # 避免网络等待卡住 FastAPI 事件循环。
            metadata = await run_in_threadpool(get_metadata, request.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            # 不把 yt-dlp 的内部异常和目标站点细节直接暴露给前端。
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=_video_parse_error_detail(exc),
            ) from exc

        # 短链和标准链接可能指向同一视频；解析后再用平台编号做第二次复用检查。
        existing = _find_existing_video(session["id"], request.url, metadata)
        if existing:
            return _to_video_response(existing)
        video = create_video(
            {
                "id": f"video_{uuid4().hex}",
                "session_id": session["id"],
                **metadata,
            }
        )
        return _to_video_response(video)


@router.get("/videos/{video_id}", response_model=VideoResponse)
async def get_video(
    video_id: str,
    session: dict = Depends(get_current_session),
) -> VideoResponse:
    """读取视频信息，并同时检查它是否属于当前匿名会话。"""
    video = get_video_for_session(video_id, session["id"])
    if not video:
        # 不区分“不存在”和“不属于当前会话”，避免泄露其他用户的视频编号。
        raise HTTPException(status_code=404, detail="视频不存在")
    return _to_video_response(video)


@router.get("/history/videos", response_model=list[HistoryVideoResponse])
async def get_video_history(
    session: dict = Depends(get_current_session),
) -> list[HistoryVideoResponse]:
    """列出当前匿名会话保存的视频，重复解析过的同一视频只显示一次。"""
    videos = list_videos_for_session(session["id"])
    history: list[HistoryVideoResponse] = []
    seen: set[tuple] = set()
    for video in videos:
        identity = _video_identity(video)
        key = ("platform-id", *identity) if identity else ("url", _canonical_source_url(video["source_url"]))
        if key in seen:
            continue
        seen.add(key)
        response = _to_video_response(video)
        history.append(
            HistoryVideoResponse(
                video_id=response.video_id,
                title=response.title,
                author=response.author,
                thumbnail=response.thumbnail,
                duration=response.duration,
                platform=response.platform,
                status=response.status,
                subtitle_source=response.subtitle_source,
                created_at=video["created_at"],
            )
        )
    return history


@router.get("/videos/{video_id}/cover")
async def get_video_cover(
    video_id: str,
    session: dict = Depends(get_current_session),
) -> Response:
    """代取 B站封面，补上来源页 Referer，避免浏览器直连图片站失败。"""
    video = get_video_for_session(video_id, session["id"])
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    raw_url = video.get("thumbnail")
    if not raw_url:
        raise HTTPException(status_code=404, detail="视频封面不存在")
    cover_url = _normalize_bilibili_cover_url(str(raw_url))

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=10.0,
            headers={
                "Referer": "https://www.bilibili.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
                ),
            },
        ) as client:
            current_url = cover_url
            for _ in range(5):
                async with client.stream("GET", current_url) as remote:
                    if remote.status_code in {301, 302, 303, 307, 308}:
                        location = remote.headers.get("location")
                        if not location:
                            raise HTTPException(status_code=502, detail="视频封面跳转地址无效")
                        # 手动跟随跳转，每一步先验证仍指向 B站图片域名，再发出下一次请求。
                        current_url = _normalize_bilibili_cover_url(
                            urljoin(current_url, location)
                        )
                        continue

                    remote.raise_for_status()
                    media_type = remote.headers.get("content-type", "").split(";", 1)[0].lower()
                    if media_type not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}:
                        raise HTTPException(status_code=502, detail="视频封面格式无效")

                    body = bytearray()
                    async for chunk in remote.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 5 * 1024 * 1024:
                            raise HTTPException(status_code=502, detail="视频封面文件过大")
                    break
            else:
                raise HTTPException(status_code=502, detail="视频封面跳转次数过多")
    except httpx.HTTPError as exc:
        logger.info("视频 %s 封面获取失败：%s", video_id, type(exc).__name__)
        raise HTTPException(status_code=502, detail="暂时无法加载视频封面") from exc

    return Response(
        content=bytes(body),
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/videos/{video_id}/summary",
    response_model=SavedSummaryResponse,
)
async def get_saved_summary(
    video_id: str,
    session: dict = Depends(get_current_session),
) -> SavedSummaryResponse:
    """读取已保存总结，供刷新页面恢复使用，不触发视频处理。"""
    video = get_video_for_session(video_id, session["id"])
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")

    summary = get_summary_for_video(video_id)
    if not summary:
        raise HTTPException(status_code=404, detail="视频总结不存在")
    return SavedSummaryResponse(
        video_id=video_id,
        content=summary["content"],
        strategy=summary["strategy"],
        model=summary["model"],
        updated_at=summary["updated_at"],
    )


@router.get(
    "/videos/{video_id}/conversations",
    response_model=list[ConversationResponse],
)
async def get_video_conversations(
    video_id: str,
    session: dict = Depends(get_current_session),
) -> list[ConversationResponse]:
    """列出当前匿名会话在该视频下创建的历史对话。"""
    video = get_video_for_session(video_id, session["id"])
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")

    return [
        ConversationResponse(
            conversation_id=item["id"],
            preview=item["preview"],
            created_at=item["created_at"],
            updated_at=item["updated_at"],
        )
        for item in list_conversations_for_video(video_id)
    ]


@router.delete(
    "/videos/{video_id}/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_video_conversation(
    video_id: str,
    conversation_id: str,
    session: dict = Depends(get_current_session),
) -> Response:
    """删除当前会话拥有的视频下的一段聊天及其消息。"""
    video = get_video_for_session(video_id, session["id"])
    if not video or not get_conversation_for_video(conversation_id, video_id):
        raise HTTPException(status_code=404, detail="对话不存在")
    delete_conversation(conversation_id, video_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/videos/{video_id}/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
async def get_conversation_messages(
    video_id: str,
    conversation_id: str,
    session: dict = Depends(get_current_session),
) -> list[MessageResponse]:
    """读取一段历史会话；视频和会话都要经过归属校验。"""
    video = get_video_for_session(video_id, session["id"])
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    if not get_conversation_for_video(conversation_id, video_id):
        raise HTTPException(status_code=404, detail="对话不存在")

    return [
        MessageResponse(
            message_id=item["id"],
            role=item["role"],
            content=item["content"],
            citations=json.loads(item.get("citations_json") or "[]"),
            created_at=item["created_at"],
        )
        for item in list_conversation_messages(conversation_id)
    ]


async def _process_video_events(
    video: dict, language: str
) -> AsyncIterator[ServerSentEvent]:
    """执行字幕获取和总结，并逐步产生 SSE 事件。

    ``yield`` 每执行一次，EventSourceResponse 就向浏览器发送一个事件；函数随后暂停，
    等下一次迭代再继续，因此前端能在整个处理完成前看到实时进度。
    """
    video_id = video["id"]
    try:
        existing_summary = get_summary_for_video(video_id)
        if existing_summary and video["status"] == "ready":
            # 已生成的结果直接返回，避免刷新或重复点击再次消耗 LLM Token。
            yield ServerSentEvent(
                data={"message": "正在载入已生成的总结", "stage": "ready"},
                event="stage",
            )
            yield ServerSentEvent(
                data={"text": existing_summary["content"]},
                event="summary_delta",
            )
            yield ServerSentEvent(
                data={"video_id": video_id, "status": "ready", "cached": True},
                event="done",
            )
            return

        update_video_status(video_id, "extracting", error_message=None)
        # 视频正在更换字幕时先清除旧索引，避免新旧证据交叉使用。
        await delete_video_index_async(video_id)
        yield ServerSentEvent(
            data={"stage": "extracting", "message": "正在读取视频内容"},
            event="stage",
        )
        # 主动让出一次事件循环，使上面的进度事件尽快发送给浏览器。
        await asyncio.sleep(0)

        # 平台专属字幕请求和 yt-dlp 都是同步阻塞代码，统一在线程池中执行。
        transcript_data = await run_in_threadpool(
            extract_subtitles, video["source_url"]
        )

        if not transcript_data["has_subtitle"]:
            update_video_status(video_id, "transcribing", error_message=None)
            yield ServerSentEvent(
                data={
                    "stage": "transcribing",
                    "message": "正在识别视频语音",
                },
                event="stage",
            )
            await asyncio.sleep(0)
            try:
                transcript_data = await run_in_threadpool(
                    transcribe_video, video["source_url"]
                )
            except ValueError as exc:
                # 音频下载、模型环境或识别失败时仍可使用标题和简介，但必须降级标记。
                logger.warning("视频 %s 语音识别不可用：%s", video_id, exc)
                transcript_data = {
                    "has_transcript": False,
                    "language": "",
                    "source": "metadata",
                    "segments": [],
                }

        has_transcript = transcript_data.get(
            "has_subtitle", transcript_data.get("has_transcript", False)
        )
        source = transcript_data["source"] if has_transcript else "metadata"
        segments = transcript_data["segments"] if has_transcript else []

        # 即使进入 metadata 降级，也要清除上一次可能残留的字幕块和旧问答。
        replace_transcript_segments(video_id, segments)
        strategy, chunks = (
            prepare_summary_chunks(segments) if segments else ("metadata", [])
        )
        save_transcript_chunks(video_id, chunks)

        if source != "metadata" and not chunks:
            raise ValueError("字幕内容为空，无法生成总结")

        update_video_status(
            video_id,
            "summarizing",
            error_message=None,
            subtitle_source=source,
            language=transcript_data.get("language") or language,
        )
        yield ServerSentEvent(
            data={
                "stage": "summarizing",
                "message": "正在整理视频内容",
                "strategy": strategy,
                "total": len(chunks),
                "source": source,
            },
            event="stage",
        )

        # 到真正需要调用模型时才创建客户端；没有 API Key 时，视频解析仍可正常使用。
        summarizer = VideoSummarizer()
        final_parts: list[str] = []

        if strategy == "metadata":
            summary_stream = summarizer.stream_metadata_summary(
                video["title"],
                video.get("author"),
                video.get("description"),
                language,
            )
        elif strategy == "direct":
            summary_stream = summarizer.stream_direct_summary(
                video["title"],
                chunks[0]["text"],
                language,
                author=video.get("author"),
                description=video.get("description"),
            )
        else:
            partial_summaries: list[dict] = []
            for index, chunk in enumerate(chunks, start=1):
                yield ServerSentEvent(
                    data={
                        "stage": "summarizing_chunks",
                        "message": f"正在整理内容（{index}/{len(chunks)}）",
                        "current": index,
                        "total": len(chunks),
                    },
                    event="stage",
                )
                local_summary = await summarizer.summarize_chunk(
                    video["title"],
                    chunk,
                    language,
                    author=video.get("author"),
                    description=video.get("description"),
                )
                update_chunk_summary(video_id, chunk["chunk_index"], local_summary)
                partial_summaries.append({**chunk, "summary": local_summary})

            yield ServerSentEvent(
                data={"stage": "merging", "message": "正在汇总主要内容"},
                event="stage",
            )
            summary_stream = summarizer.stream_merged_summary(
                video["title"],
                partial_summaries,
                language,
                author=video.get("author"),
                description=video.get("description"),
            )

        # 两种策略最后都会得到一个异步文本流，因此后面的保存和 SSE 代码可以共用。
        async for delta in summary_stream:
            final_parts.append(delta)
            yield ServerSentEvent(
                data={"text": delta},
                event="summary_delta",
            )

        final_summary = "".join(final_parts).strip()
        if not final_summary:
            raise ValueError("模型没有返回总结内容")

        save_summary(video_id, final_summary, strategy, summarizer.model)
        update_video_status(video_id, "ready", error_message=None)
        yield ServerSentEvent(
            data={
                "video_id": video_id,
                "status": "ready",
                "strategy": strategy,
                "source": source,
                "cached": False,
            },
            event="done",
        )
    except Exception as exc:
        # 服务端日志保留具体堆栈用于开发排错，前端只接收简短、可理解的信息。
        logger.exception("处理视频 %s 失败", video_id)
        message = str(exc) if isinstance(exc, ValueError) else "视频总结失败，请稍后重试"
        update_video_status(video_id, "failed", error_message=message)
        yield ServerSentEvent(
            data={"code": "PROCESS_FAILED", "message": message},
            event="error",
        )


async def _produce_process_events(
    video_id: str, video: dict, language: str, job: _ProcessJob
) -> None:
    """后台推进总结任务，并把同一份 SSE 事件广播给当前订阅者。"""
    try:
        async for event in _process_video_events(video, language):
            job.events.append(event)
            for subscriber in tuple(job.subscribers):
                subscriber.put_nowait(event)
    except asyncio.CancelledError:
        # 总结一旦落库就保留 ready；否则把已取消的任务恢复成可重试状态。
        next_status = "ready" if get_summary_for_video(video_id) else "parsed"
        update_video_status(video_id, next_status, error_message=None)
    finally:
        job.finished = True
        for subscriber in tuple(job.subscribers):
            subscriber.put_nowait(None)
        if _PROCESS_JOBS.get(video_id) is job:
            _PROCESS_JOBS.pop(video_id, None)


async def _cancel_after_page_exit(video_id: str, job: _ProcessJob) -> None:
    """给刷新后的页面留出重新连接时间；真正离开后再取消未完成的 LLM 工作。"""
    try:
        await asyncio.sleep(PROCESS_DETACH_GRACE_SECONDS)
        if not job.subscribers and not job.finished and job.task:
            job.task.cancel()
    except asyncio.CancelledError:
        return


async def _subscribe_to_process(
    video_id: str, video: dict, language: str
) -> AsyncIterator[ServerSentEvent]:
    """接入已有任务或启动一次新任务；SSE 断开不会取消后台工作。"""
    job = _PROCESS_JOBS.get(video_id)
    if not job or job.finished:
        job = _ProcessJob()
        _PROCESS_JOBS[video_id] = job
        job.task = asyncio.create_task(
            _produce_process_events(video_id, video, language, job)
        )
    if job.cancel_task:
        job.cancel_task.cancel()
        job.cancel_task = None

    subscriber: asyncio.Queue = asyncio.Queue()
    job.subscribers.add(subscriber)
    # 先重放连接断开期间已有事件，再接着收实时事件，浏览器不会丢总结增量。
    backlog = list(job.events)
    finished_at_subscribe = job.finished
    try:
        for event in backlog:
            yield event
        if finished_at_subscribe:
            return
        while True:
            event = await subscriber.get()
            if event is None:
                break
            yield event
    finally:
        job.subscribers.discard(subscriber)
        # 连接意外中断或页面离开时也安排取消；刷新重连会在宽限期内撤销它。
        if not job.subscribers and not job.finished and job.task and not job.cancel_task:
            job.cancel_task = asyncio.create_task(
                _cancel_after_page_exit(video_id, job)
            )


@router.post(
    "/videos/{video_id}/process",
    response_class=EventSourceResponse,
)  # EventSourceResponse 是 FastAPI 提供的一个响应类，用于实现服务器发送事件（SSE）。它会将异步生成器的每个 yield 结果编码为 SSE 格式，并持续向客户端发送数据流⭐
async def process_video(
    video_id: str,
    request: ProcessVideoRequest,
    session: dict = Depends(get_current_session),
) -> AsyncIterator[ServerSentEvent]:
    """启动或恢复视频处理订阅，并以 SSE 返回阶段信息和总结增量。"""
    video = get_video_for_session(video_id, session["id"])
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    # 后端任务独立于浏览器连接；刷新后再次 POST 会接入同一任务而不是新调模型。
    async for event in _subscribe_to_process(video_id, video, request.language):
        yield event


@router.post("/videos/{video_id}/process/detach")
async def detach_process_video(
    video_id: str,
    session: dict = Depends(get_current_session),
) -> dict:
    """页面离开时安排任务取消；刷新后快速重连会撤销取消安排。"""
    video = get_video_for_session(video_id, session["id"])
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    job = _PROCESS_JOBS.get(video_id)
    if job and not job.finished and job.task:
        if job.cancel_task:
            job.cancel_task.cancel()
        job.cancel_task = asyncio.create_task(
            _cancel_after_page_exit(video_id, job)
        )
    return {"scheduled": bool(job and not job.finished)}


async def _chat_events(
    video: dict, request: ChatRequest
) -> AsyncIterator[ServerSentEvent]:
    """执行总结问答、字幕 RAG 或 Agent，并产生阶段、回答和引用事件。

    本函数负责串起数据库与模型；真正的 Prompt 组装和 LLM 调用放在
    chat_service.py / agent.py，避免把具体功能实现全部堆进 API 文件。
    三种模式共用 Session 校验、会话历史、消息持久化和 SSE 外壳，只在取证和生成步骤分流。

    顺序也有意固定：先确认视频已经处理完成，再准备所需模型/检索，校验或创建对话，
    读取旧历史并保存当前问题，最后生成答案、保存回答和引用，再发 done。这样刷新后
    SQLite 是已完成对话的事实来源；SSE 只承载当前请求的即时状态与文本增量。
    """
    video_id = video["id"]
    try:
        summary = get_summary_for_video(video_id)
        if not summary or video["status"] != "ready":
            yield ServerSentEvent(
                data={"code": "SUMMARY_NOT_READY", "message": "请先完成视频总结"},
                event="error",
            )
            return

        # 先确认模型配置可用，再创建新会话，避免缺配置时留下空会话。
        # Chat 服务只持有模型客户端；Session/视频归属在路由校验，会话和消息由本函数持久化。
        chat_service = VideoChatService()
        # 只有固定 RAG 需要向量模型；总结问答读已有摘要，Agent 用白名单工具查 SQLite。
        provider = EmbeddingProvider() if request.mode == "rag" else None
        retrieval = None
        if provider is not None:
            try:
                max_distance = float(
                    os.getenv("RAG_MAX_COSINE_DISTANCE", "0.35")
                )
                top_k = max(1, int(os.getenv("RAG_TOP_K", "5")))
                evidence_char_limit = max(
                    1, int(os.getenv("RAG_MAX_EVIDENCE_CHARS", "6000"))
                )
            except ValueError as exc:
                raise ValueError("RAG 检索阈值和长度配置必须是有效数字") from exc
            if not 0 <= max_distance <= 2:
                raise ValueError("RAG_MAX_COSINE_DISTANCE 必须在 0 到 2 之间")

            yield ServerSentEvent(
                data={"stage": "indexing", "message": "正在检查并准备字幕索引"},
                event="stage",
            )
            # 指纹未变就复用索引；字幕或模型有变化时才完整重建。
            rebuilt = await ensure_video_index(video_id, provider)
            yield ServerSentEvent(
                data={
                    "stage": "retrieving",
                    "message": "索引已更新，正在检索字幕" if rebuilt else "正在检索字幕",
                },
                event="stage",
            )
            retrieval = await search_video(
                video_id,
                request.question,
                provider,
                top_k=top_k,
                max_cosine_distance=max_distance,
                max_evidence_chars=evidence_char_limit,
            )

        conversation_id = request.conversation_id
        if conversation_id:
            conversation = get_conversation_for_video(conversation_id, video_id)
            if not conversation:
                yield ServerSentEvent(
                    data={
                        "code": "CONVERSATION_NOT_FOUND",
                        "message": "当前视频的对话不存在，请重新开始问答",
                    },
                    event="error",
                )
                return
        else:
            conversation_id = f"conv_{uuid4().hex}"
            create_conversation(conversation_id, video_id)

        # 读取的是保存本轮问题之前的历史，避免把当前问题重复发送给模型。
        # SQLite 保留完整历史；只取最近若干条是上下文窗口控制，不会删除较早消息。
        history = list_recent_messages(
            conversation_id, limit=CHAT_HISTORY_MESSAGE_LIMIT
        )
        chunk_summaries = (
            get_chunk_summaries_for_video(video_id)
            if request.mode == "summary"
            else []
        )

        # 新会话 ID 必须先发给前端，下一轮提问才能继续同一段历史。
        yield ServerSentEvent(
            data={"conversation_id": conversation_id},
            event="conversation",
        )
        save_message(
            conversation_id,
            "user",
            request.question,
            mode=request.mode,
        )

        # 从这里开始按模式分流：总结问答用已有总结，RAG 先取证，Agent 可以多轮调用只读工具。
        # 分支统一写入 answer_parts / citations，因此后面的落库和 done 事件不需要知道内部算法。
        answer_parts: list[str] = []
        citations: list[dict] = []
        if retrieval is not None and retrieval.refused:
            # 检索也有结果不代表有答案；最近的字幕不够相关时直接固定拒答。
            answer = RAG_REFUSAL
            answer_parts.append(answer)
            yield ServerSentEvent(data={"text": answer}, event="answer_delta")
        elif retrieval is not None:
            evidence = list(retrieval.evidence)
            parser = CitationStreamParser(
                {item.citation_index for item in evidence}
            )
            async for delta in chat_service.stream_rag_answer(
                video_title=video["title"],
                history=history,
                question=request.question,
                evidence=evidence,
            ):
                visible = parser.feed(delta)
                if visible:
                    answer_parts.append(visible)
                    yield ServerSentEvent(
                        data={"text": visible}, event="answer_delta"
                    )
            tail = parser.feed("", final=True)
            if tail:
                answer_parts.append(tail)
                yield ServerSentEvent(data={"text": tail}, event="answer_delta")

            if parser.invalid_reference or not parser.references:
                # 这里校验编号和是否引用，不判断引用内容是否真的支持每句话。
                answer = RAG_REFUSAL
                answer_parts = [answer]
                citations = []
                yield ServerSentEvent(
                    data={"text": answer}, event="answer_reset"
                )
            else:
                evidence_by_index = {
                    item.citation_index: item for item in evidence
                }
                for citation_index in parser.references:
                    item = evidence_by_index[citation_index]
                    # 时间戳从原字幕数据库读取，不采信模型自己生成的时间。
                    source_segments = get_transcript_segments_by_ids(
                        video_id, list(item.source_segment_ids)
                    )
                    if not source_segments:
                        continue
                    citations.append(
                        {
                            "chunk_id": item.chunk_id,
                            "start": float(source_segments[0]["start"]),
                            "end": float(source_segments[-1]["end"]),
                        }
                    )
                if not citations:
                    answer = RAG_REFUSAL
                    answer_parts = [answer]
                    yield ServerSentEvent(
                        data={"text": answer}, event="answer_reset"
                    )
                else:
                    answer = "".join(answer_parts).strip()
                    if not answer:
                        answer = RAG_REFUSAL
                        answer_parts = [answer]
                        citations = []
                        yield ServerSentEvent(
                            data={"text": answer}, event="answer_reset"
                        )
        elif request.mode == "deep":
            # Agent 只产出统一事件，不直接写 HTTP 响应或数据库；这里负责转成 SSE，
            # 同时累计最终文本和引用，供本次请求结束时保存到现有会话消息表。
            async for agent_event in run_agent(
                chat_service=chat_service,
                video=video,
                summary=summary,
                history=history,
                question=request.question,
            ):
                if agent_event.name == "answer_delta":
                    text = str(agent_event.data.get("text", ""))
                    if text:
                        answer_parts.append(text)
                        yield ServerSentEvent(
                            data={"text": text}, event="answer_delta"
                        )
                elif agent_event.name == "answer_reset":
                    # 前端可能已收到部分生成文本；保存内容也必须同步替换成固定拒答。
                    text = str(agent_event.data.get("text", AGENT_REFUSAL))
                    answer_parts[:] = [text]
                    citations = []
                    yield ServerSentEvent(data={"text": text}, event="answer_reset")
                elif agent_event.name == "citations":
                    citations = agent_event.data if isinstance(agent_event.data, list) else []
                else:
                    yield ServerSentEvent(
                        data=agent_event.data, event=agent_event.name
                    )
        else:
            async for delta in chat_service.stream_answer(
                video_title=video["title"],
                final_summary=summary["content"],
                chunk_summaries=chunk_summaries,
                history=history,
                question=request.question,
                evidence_source=video.get("subtitle_source") or "manual",
            ):
                answer_parts.append(delta)
                yield ServerSentEvent(data={"text": delta}, event="answer_delta")

        if retrieval is None:
            answer = "".join(answer_parts).strip()
        if not answer:
            raise ValueError("模型没有返回回答内容")

        assistant_message_id = save_message(
            conversation_id,
            "assistant",
            answer,
            mode=request.mode,
            model=(
                chat_service.agent_model
                if request.mode == "deep"
                else chat_service.model
            ),
            citations=citations,
        )
        # done 表示回答已成功保存；若浏览器没收到最后事件但服务端已完成落库，历史接口可恢复它。
        # 这不表示正在生成的聊天请求能跨页面关闭继续运行；独立恢复目前只用于视频总结任务。
        if request.mode in {"rag", "deep"}:
            yield ServerSentEvent(data=citations, event="citations")
        yield ServerSentEvent(
            data={
                "conversation_id": conversation_id,
                "message_id": assistant_message_id,
            },
            event="done",
        )
    except Exception as exc:
        logger.exception("视频 %s 问答失败", video_id)
        message = str(exc) if isinstance(exc, ValueError) else "回答生成失败，请稍后重试"
        yield ServerSentEvent(
            data={"code": "CHAT_FAILED", "message": message},
            event="error",
        )


@router.post(
    "/videos/{video_id}/chat",
    response_class=EventSourceResponse,
)
async def chat_with_video(
    video_id: str,
    request: ChatRequest,
    session: dict = Depends(get_current_session),
) -> AsyncIterator[ServerSentEvent]:
    """校验视频归属后，把内部问答事件流交给 SSE 响应。"""
    # Agent 工具只拿到这个已校验视频；不能只凭用户提交的 video_id 读取其他人的字幕。
    video = get_video_for_session(video_id, session["id"])
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")

    async for event in _chat_events(video, request):
        yield event
