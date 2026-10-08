from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from backend import retriever
from backend.citation_parser import CitationStreamParser
from backend.database import init_db
from backend.embedding_provider import EmbeddingProvider
from backend.retrieval_chunks import build_retrieval_chunks


def _segments(count: int = 4) -> list[dict]:
    return [
        {
            "segment_index": index,
            "start": float(index * 10),
            "end": float(index * 10 + 5),
            "text": "abc",
        }
        for index in range(count)
    ]


def test_retrieval_chunks_are_independent_and_keep_source_timestamps(monkeypatch) -> None:
    """RAG 窗口来自原始片段并保留来源编号和时间边界。"""
    monkeypatch.setattr("backend.retrieval_chunks.RAG_CHUNK_MAX_CHARS", 6)
    chunks = build_retrieval_chunks(_segments(4))

    assert chunks[0].source_segment_ids == (0, 1)
    assert chunks[1].source_segment_ids == (1, 2)
    assert chunks[0].start == 0.0
    assert chunks[0].end == 15.0
    assert chunks[0].text == "abc abc"


class FakeProvider:
    provider_name = "test-provider"
    base_url = "https://embedding.test/v1"
    model = "test-model"
    dimension = 3

    def __init__(self) -> None:
        self.document_batches: list[list[str]] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.document_batches.append(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def embed_query(self, _text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


def test_fingerprint_changes_with_provider_model_dimension_chunk_version_or_text(monkeypatch) -> None:
    """任何会改变向量语义或输入内容的版本变化都会使索引失效。"""
    import backend.retriever as module

    chunks = build_retrieval_chunks(_segments(1))
    provider = FakeProvider()
    original = module._fingerprint(chunks, provider)

    provider.model = "other-model"
    assert module._fingerprint(chunks, provider) != original
    provider.model = "test-model"
    provider.dimension = 4
    assert module._fingerprint(chunks, provider) != original

    provider.dimension = 3
    provider.provider_name = "other-provider"
    assert module._fingerprint(chunks, provider) != original
    provider.provider_name = "test-provider"
    provider.base_url = "https://other.test/v1"
    assert module._fingerprint(chunks, provider) != original
    provider.base_url = "https://embedding.test/v1"
    changed_text = build_retrieval_chunks([{**_segments(1)[0], "text": "changed"}])
    assert module._fingerprint(changed_text, provider) != original
    monkeypatch.setattr(module, "RAG_CHUNK_VERSION", "next-version")
    assert module._fingerprint(chunks, provider) != original


def test_ensure_video_index_rebuilds_when_ids_or_fingerprint_mismatch(monkeypatch) -> None:
    """ID 集合不吻合时会先定向删除，再写入完整的新索引。"""
    existing_ids = ["video-a:rag:99"]
    existing_meta = [{"index_fingerprint": "old"}]
    deleted: list[str] = []
    upserted: list[tuple[str, list[str], str]] = []

    monkeypatch.setattr(retriever, "get_transcript_segments", lambda _video: _segments(2))
    monkeypatch.setattr(
        retriever,
        "_get_video_records",
        lambda _video: (existing_ids.copy(), existing_meta.copy()),
    )

    def delete(video_id: str) -> None:
        deleted.append(video_id)

    def upsert(video_id, chunks, fingerprint, _vectors) -> None:
        existing_ids[:] = [retriever._index_id(video_id, item.chunk_index) for item in chunks]
        existing_meta[:] = [{"index_fingerprint": fingerprint} for _ in chunks]
        upserted.append((video_id, [item.text for item in chunks], fingerprint))

    monkeypatch.setattr(retriever, "_delete_video_records", delete)
    monkeypatch.setattr(retriever, "_upsert_records", upsert)
    monkeypatch.setattr(retriever, "_video_locks", {})
    provider = FakeProvider()

    assert asyncio.run(retriever.ensure_video_index("video-a", provider)) is True
    assert deleted == ["video-a"]
    assert upserted[0][0] == "video-a"
    assert len(provider.document_batches) == 1
    assert asyncio.run(retriever.ensure_video_index("video-a", provider)) is False


def test_search_deduplicates_overlap_caps_evidence_and_rejects_distance(monkeypatch) -> None:
    """先去掉高度重叠窗口，再执行证据长度和 cosine 阈值判断。"""
    monkeypatch.setattr("backend.retrieval_chunks.RAG_CHUNK_MAX_CHARS", 6)
    monkeypatch.setattr(retriever, "get_transcript_segments", lambda _video: _segments(4))
    monkeypatch.setattr(
        retriever,
        "_query_records",
        lambda _video, _vector, _count: {
            "ids": [["video-a:rag:0", "video-a:rag:1", "video-a:rag:2"]],
            "distances": [[0.2, 0.21, 0.22]],
        },
    )
    provider = FakeProvider()

    result = asyncio.run(
        retriever.search_video(
            "video-a", "问题", provider, top_k=3, max_cosine_distance=0.2,
            max_evidence_chars=20,
        )
    )
    assert result.refused is False
    assert result.best_distance == 0.2
    assert [item.chunk_id for item in result.evidence] == [
        "video-a:rag:0",
        "video-a:rag:2",
    ]
    assert [item.citation_index for item in result.evidence] == [1, 2]

    capped = asyncio.run(
        retriever.search_video(
            "video-a", "问题", provider, top_k=3, max_cosine_distance=0.2,
            max_evidence_chars=6,
        )
    )
    assert sum(len(item.text) for item in capped.evidence) <= 6

    refused = asyncio.run(
        retriever.search_video(
            "video-a", "无关问题", provider, max_cosine_distance=0.19
        )
    )
    assert refused.refused is True
    assert refused.evidence == ()


def test_citation_stream_parser_handles_split_valid_and_invalid_markers() -> None:
    """分片引用可解析，非法编号不会被登记为可发送的引用。"""
    parser = CitationStreamParser({1, 2})
    visible = parser.feed("答案 [[")
    visible += parser.feed("1")
    visible += parser.feed("]]，继续 [[9]]")
    visible += parser.feed("。")
    visible += parser.feed("", final=True)

    assert visible == "答案 ，继续 。"
    assert parser.references == [1]
    assert parser.invalid_reference is True


def test_database_migrates_citations_json_idempotently(tmp_path, monkeypatch) -> None:
    """旧消息表通过 PRAGMA 检查后重复启动仍只增加一次引用列。"""
    db_path = tmp_path / "old.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id TEXT, "
            "role TEXT, content TEXT, mode TEXT, model TEXT, created_at TEXT)"
        )

    init_db()
    init_db()
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    assert "citations_json" in columns


def test_embedding_provider_is_separate_and_validates_dimensions(monkeypatch) -> None:
    """Embedding 密钥单独读取，返回维度不符时请求失败。"""
    class FakeEmbedding:
        async def create(self, **_kwargs):
            class Item:
                index = 0
                embedding = [0.1, 0.2, 0.3]

            class Response:
                data = [Item()]

            return Response()

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "embedding-only-key"
            self.embeddings = FakeEmbedding()

    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-only-key")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embedding.test/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "embed-test")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "3")
    monkeypatch.setattr("backend.embedding_provider.AsyncOpenAI", FakeAsyncClient)

    provider = EmbeddingProvider()
    assert asyncio.run(provider.embed_texts(["字幕"])) == [[0.1, 0.2, 0.3]]

    provider.dimension = 2
    with pytest.raises(ValueError, match="维度"):
        asyncio.run(provider.embed_query("问题"))


def test_local_embedding_provider_uses_query_prompt_off_event_loop(
    monkeypatch, tmp_path
) -> None:
    """本地模型不需要 API Key；文档和问题分别编码，且阻塞推理在线程池执行。"""
    import threading
    import numpy as np

    monkeypatch.setenv("EMBEDDING_PROVIDER", "sentence-transformers-local")
    monkeypatch.setenv("EMBEDDING_MODEL", "Qwen3-Embedding-0.6B")
    monkeypatch.setenv("EMBEDDING_MODEL_PATH", str(tmp_path))
    monkeypatch.setenv("EMBEDDING_DIMENSION", "3")
    monkeypatch.setenv("EMBEDDING_DEVICE", "cpu")
    monkeypatch.setenv("EMBEDDING_QUERY_PROMPT", "query")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)

    calls: list[dict] = []
    main_thread_id = threading.get_ident()

    class FakeModel:
        prompts = {"query": "find relevant passages"}

        def encode(self, texts, **kwargs):
            calls.append(
                {
                    "texts": list(texts),
                    "kwargs": kwargs,
                    "thread_id": threading.get_ident(),
                }
            )
            return np.tile(np.array([[0.1, 0.2, 0.3]]), (len(texts), 1))

    monkeypatch.setattr(
        "backend.embedding_provider._load_local_model",
        lambda _path, _device: FakeModel(),
    )

    provider = EmbeddingProvider()
    documents = asyncio.run(provider.embed_texts(["字幕一", "字幕二"]))
    query = asyncio.run(provider.embed_query("问题"))

    assert documents == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert query == [0.1, 0.2, 0.3]
    assert calls[0]["texts"] == ["字幕一", "字幕二"]
    assert "prompt_name" not in calls[0]["kwargs"]
    assert calls[1]["kwargs"]["prompt_name"] == "query"
    assert all(call["thread_id"] != main_thread_id for call in calls)


def test_local_embedding_provider_rejects_non_finite_values(monkeypatch, tmp_path) -> None:
    """本地模型输出 NaN 时不把坏向量写入 Chroma。"""
    import numpy as np

    monkeypatch.setenv("EMBEDDING_PROVIDER", "sentence-transformers-local")
    monkeypatch.setenv("EMBEDDING_MODEL", "local-test")
    monkeypatch.setenv("EMBEDDING_MODEL_PATH", str(tmp_path))
    monkeypatch.setenv("EMBEDDING_DIMENSION", "2")
    monkeypatch.setattr(
        "backend.embedding_provider._load_local_model",
        lambda _path, _device: type(
            "FakeModel",
            (),
            {"prompts": {}, "encode": lambda _self, _texts, **_kwargs: np.array([[float("nan"), 0.1]])},
        )(),
    )

    with pytest.raises(ValueError, match="Embedding 返回结果无效"):
        asyncio.run(EmbeddingProvider().embed_texts(["字幕"]))


def test_chroma_helpers_apply_video_scope_to_query_and_delete(monkeypatch) -> None:
    """向量查询和清理都必须带上唯一目标 video_id。"""
    calls: list[tuple[str, dict]] = []

    class FakeCollection:
        def query(self, **kwargs):
            calls.append(("query", kwargs))
            return {}

        def delete(self, **kwargs):
            calls.append(("delete", kwargs))

    monkeypatch.setattr(retriever, "_get_collection", lambda: FakeCollection())
    retriever._query_records("video-a", [0.1, 0.2], 3)
    retriever._delete_video_records("video-a")

    assert calls[0][1]["where"] == {"video_id": "video-a"}
    assert calls[1][1]["where"] == {"video_id": "video-a"}
