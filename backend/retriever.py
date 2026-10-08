"""按视频维护本地 Chroma 索引，并把命中证据映射回 SQLite 原字幕。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from starlette.concurrency import run_in_threadpool

from backend.database import get_transcript_segments
from backend.embedding_provider import EmbeddingProvider
from backend.retrieval_chunks import RAG_CHUNK_VERSION, RetrievalChunk, build_retrieval_chunks


COLLECTION_NAME = "video_transcript_chunks"
# cosine distance 越小越相似；0.35 只是起点，需用问题集校准。
DEFAULT_MAX_COSINE_DISTANCE = 0.35
DEFAULT_EVIDENCE_CHAR_LIMIT = 6000
_video_locks: dict[str, asyncio.Lock] = {}
_collection = None
_collection_lock = threading.Lock()


def _configured_chroma_path() -> Path:
    path = Path(os.getenv("CHROMA_PATH", "data/chroma"))
    return path if path.is_absolute() else Path(__file__).resolve().parent / path


@dataclass(frozen=True)
class RetrievedEvidence:
    """一条已按视频归属回查、可供模型引用的字幕证据。"""

    citation_index: int
    chunk_id: str
    text: str
    start: float
    end: float
    source_segment_ids: tuple[int, ...]
    distance: float


@dataclass(frozen=True)
class RetrievalResult:
    """保留最近距离，即使触发拒答也能在日志和测试中解释原因。"""

    evidence: tuple[RetrievedEvidence, ...]
    best_distance: float | None
    refused: bool


def _get_collection():
    """首次需要 RAG 时才加载 Chroma，避免阶段 A 启动依赖向量库。"""
    global _collection
    if _collection is None:
        with _collection_lock:
            if _collection is None:
                import chromadb

                client = chromadb.PersistentClient(path=str(_configured_chroma_path()))
                # 用 cosine 距离建集合，和上面的拒答阈值采用同一种距离。
                _collection = client.get_or_create_collection(
                    name=COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine"},
                )
    return _collection


def _index_id(video_id: str, chunk_index: int) -> str:
    return f"{video_id}:rag:{chunk_index}"


def _fingerprint(
    chunks: list[RetrievalChunk], provider: EmbeddingProvider
) -> str:
    """模型身份、维度、分块版本和完整文本共同决定索引是否可复用。"""
    # 模型或文本一变，旧向量就可能不兼容，所以把这些输入一起纳入指纹。
    payload = {
        "provider": provider.provider_name,
        "endpoint": provider.base_url,
        "model": provider.model,
        "dimension": provider.dimension,
        "chunk_version": RAG_CHUNK_VERSION,
        "texts": [chunk.text for chunk in chunks],
    }
    stable = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _get_video_records(video_id: str) -> tuple[list[str], list[dict]]:
    collection = _get_collection()
    result = collection.get(where={"video_id": video_id}, include=["metadatas"])
    return result.get("ids", []), result.get("metadatas", [])


def _delete_video_records(video_id: str) -> None:
    _get_collection().delete(where={"video_id": video_id})


def _upsert_records(
    video_id: str,
    chunks: list[RetrievalChunk],
    fingerprint: str,
    embeddings: list[list[float]],
) -> None:
    _get_collection().upsert(
        ids=[_index_id(video_id, chunk.chunk_index) for chunk in chunks],
        embeddings=embeddings,
        documents=[chunk.text for chunk in chunks],
        metadatas=[
            {
                "video_id": video_id,
                "chunk_index": chunk.chunk_index,
                "start": chunk.start,
                "end": chunk.end,
                "source_segment_ids": json.dumps(chunk.source_segment_ids),
                "index_fingerprint": fingerprint,
            }
            for chunk in chunks
        ],
    )


def _query_records(video_id: str, vector: list[float], count: int) -> dict:
    return _get_collection().query(
        query_embeddings=[vector],
        n_results=count,
        # 多个视频共用集合时，查询仍必须限制在当前视频字幕内。
        where={"video_id": video_id},
        include=["distances"],
    )


async def ensure_video_index(
    video_id: str,
    provider: EmbeddingProvider,
) -> bool:
    """索引缺失或 fingerprint/ID 集合不符时，删净后按当前字幕完整重建。"""
    # 同一视频并发首次提问时只允许一个任务重建索引。
    lock = _video_locks.setdefault(video_id, asyncio.Lock())
    async with lock:
        segments = get_transcript_segments(video_id)
        chunks = build_retrieval_chunks(segments)
        if not chunks:
            raise ValueError("当前视频没有可检索的原始字幕")

        fingerprint = _fingerprint(chunks, provider)
        expected_ids = {_index_id(video_id, chunk.chunk_index) for chunk in chunks}
        existing_ids, metadatas = await run_in_threadpool(_get_video_records, video_id)
        is_current = (
            set(existing_ids) == expected_ids
            and len(metadatas) == len(chunks)
            and all(item.get("index_fingerprint") == fingerprint for item in metadatas)
        )
        if is_current:
            return False

        # 先清除旧版和可能中断留下的部分记录，再完整写入当前索引。
        await run_in_threadpool(_delete_video_records, video_id)
        vectors: list[list[float]] = []
        # 小批量发送字幕，避免一次请求过大；最终仍按 chunk 顺序写入。
        batch_size = 64
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            vectors.extend(await provider.embed_texts([item.text for item in batch]))
        await run_in_threadpool(
            _upsert_records,
            video_id,
            chunks,
            fingerprint,
            vectors,
        )
        return True


def delete_video_index(video_id: str) -> None:
    """只删除一个视频的向量记录；绝不重置共享 Chroma 集合。"""
    # 阶段 A 首次处理视频时，不为删除不存在的索引而创建向量库。
    if _collection is None and not _configured_chroma_path().exists():
        return
    _delete_video_records(video_id)


async def delete_video_index_async(video_id: str) -> None:
    """与同视频的首次建索引串行删除，避免重处理后残留旧记录。"""
    lock = _video_locks.setdefault(video_id, asyncio.Lock())
    async with lock:
        await run_in_threadpool(delete_video_index, video_id)


async def search_video(
    video_id: str,
    question: str,
    provider: EmbeddingProvider,
    *,
    top_k: int = 5,
    max_cosine_distance: float = DEFAULT_MAX_COSINE_DISTANCE,
    max_evidence_chars: int = DEFAULT_EVIDENCE_CHAR_LIMIT,
) -> RetrievalResult:
    """按视频过滤检索，先去除大幅重叠窗口，再限制送入模型的证据长度。"""
    segments = get_transcript_segments(video_id)
    chunks = build_retrieval_chunks(segments)
    if not chunks:
        return RetrievalResult((), None, True)

    query_vector = await provider.embed_query(question)
    # 多取一些候选，重叠块去重后仍尽量保留足够的 Top-K 证据。
    raw = await run_in_threadpool(
        _query_records,
        video_id,
        query_vector,
        min(max(top_k * 3, top_k), len(chunks)),
    )
    ids = raw.get("ids", [[]])[0]
    distances = raw.get("distances", [[]])[0]
    if not ids or not distances:
        return RetrievalResult((), None, True)

    by_id = {_index_id(video_id, chunk.chunk_index): chunk for chunk in chunks}
    ranked = sorted(zip(ids, distances), key=lambda item: float(item[1]))
    best_distance = float(ranked[0][1])
    # Top-K 总会有最近邻；超过阈值说明它也不够相关，应拒答。
    if best_distance > max_cosine_distance:
        return RetrievalResult((), best_distance, True)

    selected: list[RetrievedEvidence] = []
    used_chars = 0
    for chunk_id, raw_distance in ranked:
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        source_ids = set(chunk.source_segment_ids)
        duplicates = False
        for item in selected:
            # 两块大部分来自同一批原字幕时只保留距离更近的一块。
            overlap = len(source_ids.intersection(item.source_segment_ids))
            denominator = min(len(source_ids), len(item.source_segment_ids))
            if denominator and overlap / denominator >= 0.5:
                duplicates = True
                break
        if duplicates:
            continue
        # 不截断字幕证据，避免引用文字和原始时间范围对不上。
        if used_chars + len(chunk.text) > max_evidence_chars:
            continue

        selected.append(
            RetrievedEvidence(
                # 编号只在本轮结果内有效，供模型引用和后端核对。
                citation_index=len(selected) + 1,
                chunk_id=chunk_id,
                text=chunk.text,
                start=chunk.start,
                end=chunk.end,
                source_segment_ids=chunk.source_segment_ids,
                distance=float(raw_distance),
            )
        )
        used_chars += len(chunk.text)
        if len(selected) >= top_k:
            break

    return RetrievalResult(tuple(selected), best_distance, not selected)
