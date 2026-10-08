"""从原始字幕片段构造独立于总结分块的检索窗口。"""

from __future__ import annotations

from dataclasses import dataclass


RAG_CHUNK_VERSION = "rag-segments-v1"
# 调整分块规则时升级版本，让 fingerprint 触发旧索引重建。
RAG_CHUNK_MAX_CHARS = 500
RAG_CHUNK_MAX_SECONDS = 45


@dataclass(frozen=True)
class RetrievalChunk:
    """保留来源片段编号，供检索和引用回查 SQLite。"""

    chunk_index: int
    text: str
    start: float
    end: float
    # 这组原始片段编号用于把检索结果还原成可点击的时间戳引用。
    source_segment_ids: tuple[int, ...]


def build_retrieval_chunks(segments: list[dict]) -> list[RetrievalChunk]:
    """按时间顺序合并短字幕，并让相邻检索块共享一个原始片段。"""
    # 先按原视频顺序处理，检索块的起止时间才会和字幕内容一致。
    ordered = sorted(segments, key=lambda item: (float(item["start"]), int(item["segment_index"])))
    chunks: list[RetrievalChunk] = []
    current: list[dict] = []

    def append_chunk(items: list[dict]) -> None:
        if not items:
            return
        chunks.append(
            RetrievalChunk(
                chunk_index=len(chunks),
                text=" ".join(str(item["text"]).strip() for item in items if str(item["text"]).strip()),
                start=float(items[0]["start"]),
                end=float(items[-1]["end"]),
                source_segment_ids=tuple(int(item["segment_index"]) for item in items),
            )
        )

    for segment in ordered:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        if not current:
            current = [segment]
            continue

        candidate = [*current, segment]
        text_size = sum(len(str(item["text"]).strip()) for item in candidate)
        duration = float(candidate[-1]["end"]) - float(candidate[0]["start"])
        if text_size <= RAG_CHUNK_MAX_CHARS and duration <= RAG_CHUNK_MAX_SECONDS:
            current = candidate
            continue

        append_chunk(current)
        # 上一块末尾片段可作为重叠；若超出窗口预算则从当前片段重新开始。
        overlap = [current[-1], segment]
        overlap_chars = sum(len(str(item["text"]).strip()) for item in overlap)
        overlap_seconds = float(segment["end"]) - float(current[-1]["start"])
        current = (
            overlap
            if overlap_chars <= RAG_CHUNK_MAX_CHARS
            and overlap_seconds <= RAG_CHUNK_MAX_SECONDS
            else [segment]
        )

    append_chunk(current)
    return [chunk for chunk in chunks if chunk.text]
