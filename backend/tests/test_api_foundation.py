from fastapi.testclient import TestClient

from backend import api
from backend.agent import AgentEvent
from backend.database import (
    get_db,
    get_summary_for_video,
    replace_transcript_segments,
    save_summary,
    save_transcript_chunks,
    update_chunk_summary,
    update_video_status,
)
from backend.retriever import RetrievedEvidence, RetrievalResult
from backend.main import app


def _fake_metadata(url: str) -> dict:
    return {
        "source_url": url,
        "source_video_id": "source-1",
        "title": "测试视频",
        "author": "测试作者",
        "thumbnail": "https://example.com/cover.jpg",
        "duration": 125,
        "platform": "TestPlatform",
        "view_count": 42,
        "description": "用于测试解析纵向切片",
    }


def test_video_parse_error_detail_distinguishes_failure_causes() -> None:
    """避免再次把所有 412/403 都误判成“没有 Cookie”。"""
    assert api._video_parse_error_detail(Exception("HTTP Error 412")) == (
        "视频平台临时拒绝访问，请稍后重试；若持续失败，可配置 YTDLP_COOKIE_FILE"
    )
    assert api._video_parse_error_detail(Exception("Unsupported URL")) == (
        "暂不支持这种视频链接格式，请尝试视频详情页的标准链接"
    )
    assert api._video_parse_error_detail(Exception("Fresh cookies are needed")) == (
        "视频平台要求提供新的浏览器 Cookie，请配置 YTDLP_COOKIE_FILE 后重试"
    )


def test_session_parse_and_session_scoped_query(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(api, "get_metadata", _fake_metadata)

    with TestClient(app) as owner:
        session_response = owner.post("/api/session")
        assert session_response.status_code == 200
        assert session_response.cookies.get("video_assistant_session")

        parse_response = owner.post(
            "/api/videos/parse",
            json={"url": "https://example.com/video"},
        )
        assert parse_response.status_code == 200
        video = parse_response.json()
        assert video["video_id"].startswith("video_")
        assert video["title"] == "测试视频"

        get_response = owner.get(f"/api/videos/{video['video_id']}")
        assert get_response.status_code == 200

        # 新浏览器会话不能读取前一个会话创建的视频。
        with TestClient(app) as stranger:
            assert stranger.post("/api/session").status_code == 200
            forbidden = stranger.get(f"/api/videos/{video['video_id']}")
            assert forbidden.status_code == 404


def test_process_video_streams_and_saves_summary(tmp_path, monkeypatch) -> None:
    """使用假字幕和假模型验证完整处理链，不消耗真实 API。"""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "process.db"))
    monkeypatch.setattr(api, "get_metadata", _fake_metadata)
    monkeypatch.setattr(
        api,
        "extract_subtitles",
        lambda _url: {
            "has_subtitle": True,
            "language": "zh",
            "source": "manual",
            "segments": [
                {"segment_id": 0, "start": 0.0, "end": 3.0, "text": "介绍主题"},
                {"segment_id": 1, "start": 3.0, "end": 8.0, "text": "给出结论"},
            ],
        },
    )

    class FakeSummarizer:
        """只模拟本测试会经过的直接总结方法。"""

        model = "fake-model"

        async def stream_direct_summary(
            self, _title, _text, _language, **_metadata
        ):
            yield "## 视频概述\n"
            yield "这是测试总结。"

    monkeypatch.setattr(api, "VideoSummarizer", FakeSummarizer)
    deleted_indexes: list[str] = []

    async def delete_index(video_id: str) -> None:
        deleted_indexes.append(video_id)

    monkeypatch.setattr(api, "delete_video_index_async", delete_index)

    with TestClient(app) as client:
        assert client.post("/api/session").status_code == 200
        video = client.post(
            "/api/videos/parse",
            json={"url": "https://example.com/video"},
        ).json()

        response = client.post(
            f"/api/videos/{video['video_id']}/process",
            json={"language": "zh"},
        )

        assert response.status_code == 200
        assert "event: summary_delta" in response.text
        assert "event: done" in response.text
        summary = get_summary_for_video(video["video_id"])
        assert summary is not None
        assert summary["content"] == "## 视频概述\n这是测试总结。"
        assert deleted_indexes == [video["video_id"]]

        with get_db() as conn:
            segment_count = conn.execute(
                "SELECT COUNT(*) FROM transcript_segments WHERE video_id = ?",
                (video["video_id"],),
            ).fetchone()[0]
        assert segment_count == 2


def test_process_video_uses_chunked_strategy(tmp_path, monkeypatch) -> None:
    """验证较长字幕会先生成局部摘要，再合并最终总结。"""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "chunked.db"))
    monkeypatch.setattr(api, "get_metadata", _fake_metadata)
    monkeypatch.setattr(
        api,
        "extract_subtitles",
        lambda _url: {
            "has_subtitle": True,
            "language": "zh",
            "source": "auto",
            "segments": [
                {"segment_id": 0, "start": 0.0, "end": 5.0, "text": "第一部分"},
                {"segment_id": 1, "start": 5.0, "end": 10.0, "text": "第二部分"},
            ],
        },
    )
    fake_chunks = [
        {
            "chunk_index": 0,
            "start": 0.0,
            "end": 5.0,
            "text": "[00:00-00:05] 第一部分",
            "source_segment_ids": [0],
        },
        {
            "chunk_index": 1,
            "start": 5.0,
            "end": 10.0,
            "text": "[00:05-00:10] 第二部分",
            "source_segment_ids": [1],
        },
    ]
    monkeypatch.setattr(
        api,
        "prepare_summary_chunks",
        lambda _segments: ("chunked", fake_chunks),
    )

    class FakeChunkedSummarizer:
        model = "fake-model"

        async def summarize_chunk(
            self, _title, chunk, _language, **_metadata
        ):
            return f"局部摘要{chunk['chunk_index'] + 1}"

        async def stream_merged_summary(
            self, _title, partials, _language, **_metadata
        ):
            assert [item["summary"] for item in partials] == ["局部摘要1", "局部摘要2"]
            yield "合并后的总结"

    monkeypatch.setattr(api, "VideoSummarizer", FakeChunkedSummarizer)

    with TestClient(app) as client:
        client.post("/api/session")
        video = client.post(
            "/api/videos/parse",
            json={"url": "https://example.com/video"},
        ).json()
        response = client.post(
            f"/api/videos/{video['video_id']}/process",
            json={"language": "zh"},
        )

        assert response.status_code == 200
        assert '"strategy": "chunked"' in response.text
        with get_db() as conn:
            summaries = conn.execute(
                """
                SELECT summary FROM transcript_chunks
                WHERE video_id = ? ORDER BY chunk_index
                """,
                (video["video_id"],),
            ).fetchall()
        assert [row["summary"] for row in summaries] == ["局部摘要1", "局部摘要2"]


def test_process_video_falls_back_to_asr(tmp_path, monkeypatch) -> None:
    """没有平台字幕时，应使用 ASR 片段继续完成总结。"""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "asr.db"))
    monkeypatch.setattr(api, "get_metadata", _fake_metadata)
    monkeypatch.setattr(
        api,
        "extract_subtitles",
        lambda _url: {
            "has_subtitle": False,
            "language": "",
            "source": "none",
            "segments": [],
        },
    )
    monkeypatch.setattr(
        api,
        "transcribe_video",
        lambda _url: {
            "has_transcript": True,
            "language": "zh",
            "source": "asr",
            "segments": [
                {
                    "segment_id": 0,
                    "start": 0.0,
                    "end": 5.0,
                    "text": "这是从视频人声识别出的测试内容",
                }
            ],
        },
    )

    class FakeSummarizer:
        model = "fake-model"

        async def stream_direct_summary(
            self, _title, text, _language, **_metadata
        ):
            assert "视频人声" in text
            yield "ASR 总结"

    monkeypatch.setattr(api, "VideoSummarizer", FakeSummarizer)

    with TestClient(app) as client:
        client.post("/api/session")
        video = client.post(
            "/api/videos/parse", json={"url": "https://example.com/video"}
        ).json()
        response = client.post(
            f"/api/videos/{video['video_id']}/process",
            json={"language": "zh"},
        )

        assert response.status_code == 200
        assert "event: stage" in response.text
        assert '"source": "asr"' in response.text
        restored = client.get(f"/api/videos/{video['video_id']}").json()
        assert restored["subtitle_source"] == "asr"
        saved_summary = get_summary_for_video(video["video_id"])
        assert saved_summary is not None
        assert saved_summary["content"] == "ASR 总结"
        assert "信息范围说明" not in response.text
        assert "未获取到平台字幕" not in response.text


def test_process_video_uses_metadata_when_speech_is_unusable(
    tmp_path, monkeypatch
) -> None:
    """字幕和人声都不可用时，生成不带固定警告的元数据概览。"""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "metadata.db"))
    monkeypatch.setattr(api, "get_metadata", _fake_metadata)
    monkeypatch.setattr(
        api,
        "extract_subtitles",
        lambda _url: {
            "has_subtitle": False,
            "language": "",
            "source": "none",
            "segments": [],
        },
    )
    monkeypatch.setattr(
        api,
        "transcribe_video",
        lambda _url: {
            "has_transcript": False,
            "language": "",
            "source": "asr",
            "segments": [],
        },
    )

    class FakeMetadataSummarizer:
        model = "fake-model"

        async def stream_metadata_summary(
            self, title, author, description, _language
        ):
            assert title == "测试视频"
            assert author == "测试作者"
            assert description == "用于测试解析纵向切片"
            yield "## 内容概览\n仅复述标题和简介。"

    monkeypatch.setattr(api, "VideoSummarizer", FakeMetadataSummarizer)

    with TestClient(app) as client:
        client.post("/api/session")
        video = client.post(
            "/api/videos/parse", json={"url": "https://example.com/video"}
        ).json()
        response = client.post(
            f"/api/videos/{video['video_id']}/process",
            json={"language": "zh"},
        )

        assert response.status_code == 200
        assert '"strategy": "metadata"' in response.text
        assert '"source": "metadata"' in response.text
        summary = get_summary_for_video(video["video_id"])
        assert summary is not None
        assert summary["content"] == "## 内容概览\n仅复述标题和简介。"
        assert "信息范围说明" not in response.text
        assert "信息限制" not in summary["content"]
        assert "仅复述标题和简介" in summary["content"]


def test_chat_streams_and_persists_conversation(tmp_path, monkeypatch) -> None:
    """验证首次创建会话、第二轮复用历史，以及用户/助手消息落库。"""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(api, "get_metadata", _fake_metadata)

    class FakeChatService:
        model = "fake-chat-model"
        received_histories: list[list[dict]] = []

        async def stream_answer(self, **kwargs):
            self.received_histories.append(kwargs["history"])
            yield "这是"
            yield "回答"

    monkeypatch.setattr(api, "VideoChatService", FakeChatService)

    with TestClient(app) as client:
        client.post("/api/session")
        video = client.post(
            "/api/videos/parse",
            json={"url": "https://example.com/video"},
        ).json()
        video_id = video["video_id"]

        # 问答依赖已经完成的视频总结；测试直接准备数据，不调用真实模型。
        save_summary(video_id, "最终总结", "chunked", "fake-summary-model")
        save_transcript_chunks(
            video_id,
            [
                {
                    "chunk_index": 0,
                    "start": 0.0,
                    "end": 10.0,
                    "text": "测试字幕",
                    "source_segment_ids": [0],
                }
            ],
        )
        update_chunk_summary(video_id, 0, "局部摘要")
        update_video_status(video_id, "ready")

        first = client.post(
            f"/api/videos/{video_id}/chat",
            json={"question": "核心观点是什么？", "mode": "summary"},
        )
        assert first.status_code == 200
        assert "event: conversation" in first.text
        assert "event: answer_delta" in first.text
        assert "event: done" in first.text

        with get_db() as conn:
            conversation = conn.execute(
                "SELECT id FROM conversations WHERE video_id = ?", (video_id,)
            ).fetchone()
            first_messages = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
                (conversation["id"],),
            ).fetchall()

        assert conversation is not None
        assert [(row["role"], row["content"]) for row in first_messages] == [
            ("user", "核心观点是什么？"),
            ("assistant", "这是回答"),
        ]
        assert FakeChatService.received_histories[0] == []

        # 页面恢复使用独立 GET 接口，只读缓存，不再次执行处理或模型调用。
        saved_summary = client.get(f"/api/videos/{video_id}/summary")
        assert saved_summary.status_code == 200
        assert saved_summary.json()["content"] == "最终总结"

        conversations = client.get(f"/api/videos/{video_id}/conversations")
        assert conversations.status_code == 200
        assert conversations.json()[0]["conversation_id"] == conversation["id"]
        assert conversations.json()[0]["preview"] == "核心观点是什么？"

        restored_messages = client.get(
            f"/api/videos/{video_id}/conversations/{conversation['id']}/messages"
        )
        assert restored_messages.status_code == 200
        assert [item["role"] for item in restored_messages.json()] == [
            "user",
            "assistant",
        ]

        second = client.post(
            f"/api/videos/{video_id}/chat",
            json={
                "question": "再解释一下",
                "conversation_id": conversation["id"],
                "mode": "summary",
            },
        )
        assert second.status_code == 200
        assert [item["role"] for item in FakeChatService.received_histories[1]] == [
            "user",
            "assistant",
        ]

        with get_db() as conn:
            message_count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                (conversation["id"],),
            ).fetchone()[0]
        assert message_count == 4

        # 即使知道资源 ID，另一个匿名 Session 也不能读取总结和对话。
        with TestClient(app) as stranger:
            stranger.post("/api/session")
            assert stranger.get(f"/api/videos/{video_id}/summary").status_code == 404
            assert (
                stranger.get(f"/api/videos/{video_id}/conversations").status_code
                == 404
            )


def test_rag_chat_streams_valid_citations_and_restores_timestamps(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "rag-chat.db"))
    monkeypatch.setattr(api, "get_metadata", _fake_metadata)
    indexed: list[str] = []

    class FakeEmbeddingProvider:
        pass

    class FakeRagChatService:
        model = "fake-chat-model"
        answers = [["答案来自", "字幕 [[1", "]]。"], ["临时答案 [[9]]"]]

        async def stream_rag_answer(self, **_kwargs):
            for chunk in self.answers.pop(0):
                yield chunk

        async def stream_answer(self, **_kwargs):
            yield "总结答案"

    async def ensure(video_id, _provider):
        indexed.append(video_id)
        return True

    async def search(video_id, _question, _provider, **_kwargs):
        if _question == "没有证据的问题":
            return RetrievalResult(evidence=(), best_distance=0.8, refused=True)
        return RetrievalResult(
            evidence=(
                RetrievedEvidence(
                    citation_index=1,
                    chunk_id=f"{video_id}:rag:0",
                    text="测试字幕证据",
                    start=12.0,
                    end=20.0,
                    source_segment_ids=(0,),
                    distance=0.1,
                ),
            ),
            best_distance=0.1,
            refused=False,
        )

    monkeypatch.setattr(api, "EmbeddingProvider", FakeEmbeddingProvider)
    monkeypatch.setattr(api, "VideoChatService", FakeRagChatService)
    monkeypatch.setattr(api, "ensure_video_index", ensure)
    monkeypatch.setattr(api, "search_video", search)

    with TestClient(app) as client:
        client.post("/api/session")
        video = client.post(
            "/api/videos/parse", json={"url": "https://example.com/rag"}
        ).json()
        video_id = video["video_id"]
        replace_transcript_segments(
            video_id,
            [{"segment_id": 0, "start": 12.5, "end": 20.75, "text": "测试字幕证据"}],
        )
        save_summary(video_id, "总结", "direct", "fake-summary-model")
        update_video_status(video_id, "ready", subtitle_source="manual")

        response = client.post(
            f"/api/videos/{video_id}/chat",
            json={"question": "讲了什么？", "mode": "rag"},
        )
        assert response.status_code == 200
        assert "event: stage" in response.text
        assert '"stage": "indexing"' in response.text
        assert '"stage": "retrieving"' in response.text
        assert "event: citations" in response.text
        assert "[[1]]" not in response.text
        assert indexed == [video_id]

        conversation = client.get(
            f"/api/videos/{video_id}/conversations"
        ).json()[0]
        restored = client.get(
            f"/api/videos/{video_id}/conversations/"
            f"{conversation['conversation_id']}/messages"
        )
        assistant = restored.json()[1]
        assert assistant["content"] == "答案来自字幕 。"
        assert assistant["citations"] == [
            {
                "chunk_id": f"{video_id}:rag:0",
                "start": 12.5,
                "end": 20.75,
            }
        ]

        reset_response = client.post(
            f"/api/videos/{video_id}/chat",
            json={
                "question": "再问一个问题",
                "conversation_id": conversation["conversation_id"],
                "mode": "rag",
            },
        )
        assert "event: answer_reset" in reset_response.text
        restored_again = client.get(
            f"/api/videos/{video_id}/conversations/"
            f"{conversation['conversation_id']}/messages"
        ).json()
        assert restored_again[-1]["content"] == api.RAG_REFUSAL
        assert restored_again[-1]["citations"] == []

        refused_response = client.post(
            f"/api/videos/{video_id}/chat",
            json={
                "question": "没有证据的问题",
                "conversation_id": conversation["conversation_id"],
                "mode": "rag",
            },
        )
        assert "event: answer_delta" in refused_response.text
        assert "event: citations\ndata: []" in refused_response.text
        restored_final = client.get(
            f"/api/videos/{video_id}/conversations/"
            f"{conversation['conversation_id']}/messages"
        ).json()
        assert restored_final[-1]["content"] == api.RAG_REFUSAL
        assert restored_final[-1]["citations"] == []

        # 另一个匿名 Session 即使知道 video_id，也不会触发 Embedding 或向量检索。
        with TestClient(app, raise_server_exceptions=False) as stranger:
            stranger.post("/api/session")
            stranger.post(
                f"/api/videos/{video_id}/chat",
                json={"question": "越权", "mode": "rag"},
            )
        assert indexed == [video_id, video_id, video_id]


def test_deep_agent_streams_trace_and_persists_verified_citations(
    tmp_path, monkeypatch
) -> None:
    """Agent 复用已有聊天会话，并保存本轮校验过的字幕引用。"""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setattr(api, "get_metadata", _fake_metadata)
    agent_calls: list[tuple[str, str]] = []

    class FakeChatService:
        model = "fake-chat-model"
        agent_model = "fake-agent-model"

    async def fake_agent(**kwargs):
        agent_calls.append((kwargs["video"]["id"], kwargs["question"]))
        yield AgentEvent(
            "tool_trace",
            {
                "round": 1,
                "tool": "search_transcript",
                "status": "completed",
                "message": "找到 1 条字幕片段",
            },
        )
        yield AgentEvent("answer_delta", {"text": "答案来自字幕"})
        yield AgentEvent(
            "citations",
            [
                {
                    "chunk_id": f"{kwargs['video']['id']}:segment:0",
                    "start": 2.0,
                    "end": 4.0,
                }
            ],
        )

    monkeypatch.setattr(api, "VideoChatService", FakeChatService)
    monkeypatch.setattr(api, "run_agent", fake_agent)

    with TestClient(app) as client:
        client.post("/api/session")
        video = client.post(
            "/api/videos/parse", json={"url": "https://example.com/agent"}
        ).json()
        video_id = video["video_id"]
        save_summary(video_id, "已有总结", "direct", "fake-summary-model")
        update_video_status(video_id, "ready", subtitle_source="manual")

        response = client.post(
            f"/api/videos/{video_id}/chat",
            json={"question": "请深入分析", "mode": "deep"},
        )
        assert response.status_code == 200
        assert "event: tool_trace" in response.text
        assert '"status": "completed"' in response.text
        assert "event: citations" in response.text
        assert agent_calls == [(video_id, "请深入分析")]

        conversation = client.get(
            f"/api/videos/{video_id}/conversations"
        ).json()[0]
        restored = client.get(
            f"/api/videos/{video_id}/conversations/"
            f"{conversation['conversation_id']}/messages"
        ).json()
        assert restored[-1]["content"] == "答案来自字幕"
        assert restored[-1]["citations"] == [
            {
                "chunk_id": f"{video_id}:segment:0",
                "start": 2.0,
                "end": 4.0,
            }
        ]
        with get_db() as conn:
            saved = conn.execute(
                "SELECT mode, model FROM messages WHERE conversation_id = ? AND role = 'assistant'",
                (conversation["conversation_id"],),
            ).fetchone()
        assert tuple(saved) == ("deep", "fake-agent-model")

        # 非当前 Session 即便知道 video_id，也不能启动 Agent。
        with TestClient(app, raise_server_exceptions=False) as stranger:
            stranger.post("/api/session")
            stranger.post(
                f"/api/videos/{video_id}/chat",
                json={"question": "越权", "mode": "deep"},
            )
        assert agent_calls == [(video_id, "请深入分析")]
