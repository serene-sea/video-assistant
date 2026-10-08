"""Agent 主要行为的替身测试，不访问真实 DeepSeek 或视频服务。

测试用假字幕验证工具范围和引用映射，再用预先排好的模型响应检查 ReAct 是否把工具结果送回下一轮。
"""

import asyncio
from types import SimpleNamespace

import pytest

from backend import agent


def _segment(index: int = 0) -> dict:
    return {
        "segment_index": index,
        "start": float(index * 2),
        "end": float(index * 2 + 1),
        "text": f"这段字幕讲的是游泳动作{index}",
    }


def _tool_call(name: str, arguments: str, call_id: str = "call_1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_search_tool_is_scoped_to_video_and_registers_citable_segments(
    monkeypatch,
) -> None:
    """字幕搜索只使用 Agent 固定绑定的视频，并登记本轮引用。"""
    queried: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        agent,
        "search_transcript_segments",
        lambda video_id, query, limit: (
            queried.append((video_id, query, limit)) or [_segment()]
        ),
    )
    run = agent._AgentRun("video_owned")

    result = agent._run_tool(
        run,
        "search_transcript",
        {"query": "游泳动作", "limit": 4},
    )

    assert queried == [("video_owned", "游泳动作", 4)]
    assert '"citation_index": 1' in result.content
    assert run.citations([1]) == [
        {
            "chunk_id": "video_owned:segment:0",
            "start": 0.0,
            "end": 1.0,
        }
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "x" * 121},
        {"query": "动作", "limit": 13},
        {"query": "动作", "video_id": "another_video"},
    ],
)
def test_search_tool_rejects_invalid_or_unscoped_arguments(arguments) -> None:
    """工具参数有边界，模型不能覆盖服务端固定的视频 ID。"""
    with pytest.raises(ValueError):
        agent._run_tool(agent._AgentRun("video_owned"), "search_transcript", arguments)


def test_read_range_rejects_too_wide_or_invalid_range() -> None:
    """时间范围工具不能读取视频里无限长的字幕。"""
    for arguments in [
        {"start_seconds": 0, "end_seconds": 121},
        {"start_seconds": -1, "end_seconds": 2},
        {"start_seconds": 4, "end_seconds": 2},
    ]:
        with pytest.raises(ValueError):
            agent._run_tool(
                agent._AgentRun("video_owned"),
                "read_transcript_range",
                arguments,
            )


def test_invalid_citation_is_rejected_and_valid_one_maps_to_timestamp() -> None:
    """回答引用只能来自 Agent 本轮读取的原字幕片段。"""
    run = agent._AgentRun("video_owned")
    run.add_segments([_segment()])

    answer, citations, refused = agent._validated_answer(run, "有字幕依据[[1]]")
    assert answer == "有字幕依据"
    assert citations[0]["start"] == 0.0
    assert refused is False

    answer, citations, refused = agent._validated_answer(run, "伪造来源[[99]]")
    assert answer == agent.AGENT_REFUSAL
    assert citations == []
    assert refused is True


class _FakeChatService:
    """按测试给定顺序返回模型响应，便于稳定复现工具选择过程。"""

    agent_model = "fake-agent"

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    async def request_agent_tools(self, *, messages, tools):
        self.requests.append((list(messages), tools))
        return self.responses.pop(0)

    async def stream_agent_final(self, *, messages):
        self.final_messages = list(messages)
        yield "答案来自字幕"
        yield "[[1]]"


def _message(tool_calls=None, content=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def test_react_agent_returns_tool_trace_and_validated_citation(monkeypatch) -> None:
    """模型看到搜索结果后可以再选工具，最后只返回本轮证据引用。"""
    monkeypatch.setattr(agent, "search_transcript_segments", lambda *_args: [_segment()])
    service = _FakeChatService(
        [
            _message([_tool_call("search_transcript", '{"query":"游泳动作"}')]),
            _message(content="答案来自字幕[[1]]"),
        ]
    )
    async def collect_events():
        return [
            event
            async for event in agent.run_agent(
                chat_service=service,
                video={"id": "video_owned", "title": "游泳视频", "duration": 10},
                summary={"content": "总结"},
                history=[],
                question="动作是什么？",
            )
        ]

    events = asyncio.run(collect_events())

    traces = [event for event in events if event.name == "tool_trace"]
    assert [item.data["status"] for item in traces] == ["started", "completed"]
    assert any(event.name == "answer_delta" for event in events)
    assert events[-1].name == "citations"
    assert events[-1].data == [
        {
            "chunk_id": "video_owned:segment:0",
            "start": 0.0,
            "end": 1.0,
        }
    ]
    assert len(service.requests) == 2
    assert any(message["role"] == "tool" for message in service.requests[1][0])


def test_react_agent_stops_after_five_tool_rounds(monkeypatch) -> None:
    """达到五轮后关闭工具选择，并基于已经收集的字幕作最终回答。"""
    monkeypatch.setattr(agent, "search_transcript_segments", lambda *_args: [_segment()])
    service = _FakeChatService(
        [
            _message([_tool_call("search_transcript", '{"query":"游泳动作"}', f"call_{i}")])
            for i in range(agent.AGENT_MAX_TOOL_ROUNDS)
        ]
    )
    async def collect_events():
        return [
            event
            async for event in agent.run_agent(
                chat_service=service,
                video={"id": "video_owned", "title": "游泳视频", "duration": 10},
                summary={"content": "总结"},
                history=[],
                question="动作是什么？",
            )
        ]

    events = asyncio.run(collect_events())

    assert len(service.requests) == agent.AGENT_MAX_TOOL_ROUNDS
    assert service.final_messages
    assert events[-1].name == "citations"


def test_react_agent_refuses_when_no_subtitle_evidence(monkeypatch) -> None:
    """只有提纲或空搜索不能支持事实回答，必须固定拒答。"""
    monkeypatch.setattr(agent, "search_transcript_segments", lambda *_args: [])
    service = _FakeChatService(
        [
            _message([_tool_call("search_transcript", '{"query":"不存在"}')]),
            _message(content="猜测答案"),
        ]
    )
    async def collect_events():
        return [
            event
            async for event in agent.run_agent(
                chat_service=service,
                video={"id": "video_owned", "title": "无字幕视频", "duration": 10},
                summary={"content": "只有标题信息"},
                history=[],
                question="视频讲了什么？",
            )
        ]

    events = asyncio.run(collect_events())

    assert any(event.name == "answer_reset" for event in events)
    assert events[-1].name == "citations"
    assert events[-1].data == []
