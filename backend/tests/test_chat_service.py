from backend.chat_service import build_chat_context, build_chat_messages


def test_build_chat_context_keeps_final_summary_first() -> None:
    """上下文预算不足时也必须优先保留最终总结。"""
    context = build_chat_context(
        "最终总结",
        [
            {
                "chunk_index": 0,
                "start": 0.0,
                "end": 10.0,
                "summary": "局部摘要",
            }
        ],
        max_chars=200,
    )

    assert context.startswith("## 视频最终总结\n最终总结")
    assert "局部摘要 1" in context
    assert len(context) <= 200


def test_build_chat_messages_places_history_before_current_question() -> None:
    """多轮历史必须位于当前问题之前，且保持原来的角色顺序。"""
    messages = build_chat_messages(
        "测试视频",
        "最终总结",
        [],
        [
            {"role": "user", "content": "上一问"},
            {"role": "assistant", "content": "上一答"},
        ],
        "当前问题",
    )

    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "user",
        "assistant",
        "user",
    ]
    assert messages[-1]["content"] == "当前问题"


def test_metadata_chat_prompt_limits_visual_claims() -> None:
    """只有元数据时，系统提示必须禁止声称已经看过视频画面。"""
    messages = build_chat_messages(
        "测试视频",
        "仅根据标题生成的概览",
        [],
        [],
        "舞蹈动作是什么？",
        evidence_source="metadata",
    )

    assert "不代表已经分析视频画面" in messages[0]["content"]
    assert "必须明确说明无法确定" in messages[0]["content"]
