"""管理 SQLite 连接、数据表和常用数据访问函数。"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "video_assistant.db"


def _utc_now() -> str:
    """SQLite 中统一保存带时区的 UTC ISO 时间，避免本地时区混用。"""
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> Path:
    """返回数据库路径；相对路径按 backend 目录解析，而非启动目录。"""
    configured = os.getenv("DATABASE_PATH")
    path = Path(configured) if configured else DEFAULT_DB_PATH
    if not path.is_absolute():
        path = BACKEND_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """统一管理连接生命周期：正常提交，异常回滚，最终关闭。

    ``@contextmanager`` 让调用方可以写 ``with get_db() as conn``；离开 with
    代码块时会自动执行这里的提交、回滚和关闭逻辑，减少连接泄漏。
    """
    conn = sqlite3.connect(get_db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL 允许读取和写入更好地并行，适合本地 Web 服务的轻量并发。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """创建当前阶段需要的视频、字幕、总结和问答表。"""
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS anonymous_sessions (
                id TEXT PRIMARY KEY,
                token_hash TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_video_id TEXT,
                title TEXT NOT NULL,
                author TEXT,
                thumbnail TEXT,
                duration REAL,
                platform TEXT NOT NULL,
                view_count INTEGER,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'parsed',
                subtitle_source TEXT,
                language TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES anonymous_sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS transcript_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                segment_index INTEGER NOT NULL,
                start REAL NOT NULL,
                end REAL NOT NULL,
                text TEXT NOT NULL,
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
                UNIQUE (video_id, segment_index)
            );

            CREATE TABLE IF NOT EXISTS transcript_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                start REAL NOT NULL,
                end REAL NOT NULL,
                text TEXT NOT NULL,
                source_segment_ids TEXT NOT NULL,
                summary TEXT,
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
                UNIQUE (video_id, chunk_index)
            );

            CREATE TABLE IF NOT EXISTS summaries (
                video_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                strategy TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                citations_json TEXT NOT NULL DEFAULT '[]',
                mode TEXT NOT NULL DEFAULT 'summary',
                model TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_token_hash
                ON anonymous_sessions(token_hash);
            CREATE INDEX IF NOT EXISTS idx_videos_session_created
                ON videos(session_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_segments_video
                ON transcript_segments(video_id, segment_index);
            CREATE INDEX IF NOT EXISTS idx_chunks_video
                ON transcript_chunks(video_id, chunk_index);
            CREATE INDEX IF NOT EXISTS idx_conversations_video
                ON conversations(video_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(conversation_id, id);
            """
        )
        # 已有 SQLite 文件也要获得引用列；重复启动不会重复执行 ALTER TABLE。
        message_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(messages)")
        }
        if "citations_json" not in message_columns:
            conn.execute(
                "ALTER TABLE messages ADD COLUMN citations_json TEXT NOT NULL DEFAULT '[]'"
            )


def find_session_by_token_hash(token_hash: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM anonymous_sessions
            WHERE token_hash = ? AND expires_at > ?
            """,
            (token_hash, _utc_now()),
        ).fetchone()
        return dict(row) if row else None


def create_session(
    session_id: str, token_hash: str, expires_at: str
) -> dict:
    now = _utc_now()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO anonymous_sessions
                (id, token_hash, created_at, last_seen_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, token_hash, now, now, expires_at),
        )
    return {
        "id": session_id,
        "token_hash": token_hash,
        "created_at": now,
        "last_seen_at": now,
        "expires_at": expires_at,
    }


def touch_session(session_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE anonymous_sessions SET last_seen_at = ? WHERE id = ?",
            (_utc_now(), session_id),
        )


def create_video(video: dict) -> dict:
    now = _utc_now()
    record = {
        **video,
        "status": "parsed",
        "subtitle_source": None,
        "language": None,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO videos (
                id, session_id, source_url, source_video_id, title, author,
                thumbnail, duration, platform, view_count, description, status,
                subtitle_source, language, error_message, created_at, updated_at
            ) VALUES (
                :id, :session_id, :source_url, :source_video_id, :title, :author,
                :thumbnail, :duration, :platform, :view_count, :description, :status,
                :subtitle_source, :language, :error_message, :created_at, :updated_at
            )
            """,
            record,
        )
    return record


def get_video_for_session(video_id: str, session_id: str) -> dict | None:
    """资源查询同时校验 session_id，不能只凭 video_id 返回用户数据。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM videos WHERE id = ? AND session_id = ?",
            (video_id, session_id),
        ).fetchone()
        return dict(row) if row else None


def list_videos_for_session(session_id: str) -> list[dict]:
    """列出当前匿名会话保存的视频，供历史记录页展示。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM videos WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def update_video_status(
    video_id: str,
    status: str,
    *,
    error_message: str | None = None,
    subtitle_source: str | None = None,
    language: str | None = None,
) -> None:
    """更新处理状态；COALESCE 表示未传字幕信息时保留数据库原值。"""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE videos
            SET status = ?, error_message = ?,
                subtitle_source = COALESCE(?, subtitle_source),
                language = COALESCE(?, language),
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                error_message,
                subtitle_source,
                language,
                _utc_now(),
                video_id,
            ),
        )


def replace_transcript_segments(video_id: str, segments: list[dict]) -> None:
    """用新字幕替换旧处理结果，保证一次重新处理不会混入上次数据。"""
    with get_db() as conn:
        # 重新处理会改变问答依据，因此旧对话也要删除，避免混用新旧总结。
        conn.execute("DELETE FROM conversations WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM summaries WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM transcript_chunks WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM transcript_segments WHERE video_id = ?", (video_id,))
        conn.executemany(
            """
            INSERT INTO transcript_segments
                (video_id, segment_index, start, end, text)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    video_id,
                    segment["segment_id"],
                    segment["start"],
                    segment["end"],
                    segment["text"],
                )
                for segment in segments
            ],
        )


def save_transcript_chunks(video_id: str, chunks: list[dict]) -> None:
    """保存送入 LLM 的字幕块，后续问答可以继续使用这些文本和时间范围。"""
    with get_db() as conn:
        conn.execute("DELETE FROM transcript_chunks WHERE video_id = ?", (video_id,))
        conn.executemany(
            """
            INSERT INTO transcript_chunks (
                video_id, chunk_index, start, end, text, source_segment_ids, summary
            ) VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            [
                (
                    video_id,
                    chunk["chunk_index"],
                    chunk["start"],
                    chunk["end"],
                    chunk["text"],
                    json.dumps(chunk["source_segment_ids"], ensure_ascii=False),
                )
                for chunk in chunks
            ],
        )


def get_transcript_segments(video_id: str) -> list[dict]:
    """按时间顺序读取原始字幕片段，供独立 RAG 分块和引用回查使用。"""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT segment_index, start, end, text
            FROM transcript_segments
            WHERE video_id = ?
            ORDER BY segment_index
            """,
            (video_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def search_transcript_segments(
    video_id: str, query: str, limit: int = 6
) -> list[dict]:
    """在指定视频内按原文短语搜索字幕；使用参数化查询避免 SQL 注入。

    这是 Agent 的轻量关键词工具，不是语义检索：字幕必须包含用户给出的原词组。
    ``video_id`` 与短语都作为 SQL 参数传入，结果只会来自该视频。
    """
    phrase = query.strip()
    if not phrase:
        return []
    safe_limit = max(1, min(limit, 12))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT segment_index, start, end, text
            FROM transcript_segments
            WHERE video_id = ? AND instr(text, ?) > 0
            ORDER BY segment_index
            LIMIT ?
            """,
            (video_id, phrase, safe_limit),
        ).fetchall()
        return [dict(row) for row in rows]


def get_transcript_segments_in_range(
    video_id: str, start: float, end: float, limit: int = 30
) -> list[dict]:
    """只读取当前视频与指定时间范围相交的字幕片段。

    字幕可能跨过查询边界，所以判断条件是“片段结束不早于起点，且片段开始不晚于终点”；
    调用方还会限制时间范围和返回条数，避免一次读入整段长视频。
    """
    safe_limit = max(1, min(limit, 60))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT segment_index, start, end, text
            FROM transcript_segments
            WHERE video_id = ? AND end >= ? AND start <= ?
            ORDER BY segment_index
            LIMIT ?
            """,
            (video_id, start, end, safe_limit),
        ).fetchall()
        return [dict(row) for row in rows]


def get_transcript_segments_by_ids(
    video_id: str, segment_ids: list[int]
) -> list[dict]:
    """只按本视频的片段编号取回原文和时间戳，不信任向量库里的引用字段。"""
    if not segment_ids:
        return []
    placeholders = ",".join("?" for _ in segment_ids)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT segment_index, start, end, text
            FROM transcript_segments
            WHERE video_id = ? AND segment_index IN ({placeholders})
            ORDER BY segment_index
            """,
            (video_id, *segment_ids),
        ).fetchall()
        return [dict(row) for row in rows]


def update_chunk_summary(video_id: str, chunk_index: int, summary: str) -> None:
    """局部摘要生成一块就保存一块，便于观察和后续复用。"""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE transcript_chunks SET summary = ?
            WHERE video_id = ? AND chunk_index = ?
            """,
            (summary, video_id, chunk_index),
        )


def save_summary(
    video_id: str, content: str, strategy: str, model: str
) -> None:
    """保存最终 Markdown 总结；重复处理同一视频时覆盖旧结果。"""
    now = _utc_now()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO summaries
                (video_id, content, strategy, model, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                content = excluded.content,
                strategy = excluded.strategy,
                model = excluded.model,
                updated_at = excluded.updated_at
            """,
            (video_id, content, strategy, model, now, now),
        )


def get_summary_for_video(video_id: str) -> dict | None:
    """读取已生成的总结；再次点击分析时可直接返回，避免重复调用 LLM。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM summaries WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        return dict(row) if row else None


def get_chunk_summaries_for_video(video_id: str) -> list[dict]:
    """按视频时间顺序读取局部摘要；直接总结模式下可能返回空列表。"""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT chunk_index, start, end, summary
            FROM transcript_chunks
            WHERE video_id = ? AND summary IS NOT NULL AND summary != ''
            ORDER BY chunk_index
            """,
            (video_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def create_conversation(conversation_id: str, video_id: str) -> dict:
    """为一个视频创建对话；对话 ID 由 API 层生成后传入。"""
    now = _utc_now()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO conversations (id, video_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, video_id, now, now),
        )
    return {
        "id": conversation_id,
        "video_id": video_id,
        "created_at": now,
        "updated_at": now,
    }


def get_conversation_for_video(
    conversation_id: str, video_id: str
) -> dict | None:
    """只返回属于当前视频的对话，防止拿其他视频的对话 ID 串用。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ? AND video_id = ?",
            (conversation_id, video_id),
        ).fetchone()
        return dict(row) if row else None


def delete_conversation(conversation_id: str, video_id: str) -> None:
    """删除指定视频的一段对话；消息由外键级联删除。"""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM conversations WHERE id = ? AND video_id = ?",
            (conversation_id, video_id),
        )


def list_conversations_for_video(video_id: str) -> list[dict]:
    """列出视频的历史会话，并用第一条用户问题作为简短标题。

    子查询只读取每段会话最早的一条用户消息；前端无需把所有消息下载下来，
    用户真正选择某段会话后，再调用消息查询接口读取内容。
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                c.id,
                c.video_id,
                c.created_at,
                c.updated_at,
                COALESCE(
                    (
                        SELECT substr(m.content, 1, 60)
                        FROM messages AS m
                        WHERE m.conversation_id = c.id AND m.role = 'user'
                        ORDER BY m.id ASC
                        LIMIT 1
                    ),
                    '新对话'
                ) AS preview
            FROM conversations AS c
            WHERE c.video_id = ?
            ORDER BY c.updated_at DESC
            """,
            (video_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def list_conversation_messages(
    conversation_id: str, limit: int = 100
) -> list[dict]:
    """读取用于页面恢复的消息，返回最近若干条并保持时间正序。

    聊天记录可能长期增长，因此第一版限制最多返回 100 条；这和 LLM 只读取最近
    10 条是两个不同限制：前者服务页面展示，后者控制模型上下文。
    """
    safe_limit = max(1, min(limit, 200))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT id, role, content, citations_json, mode, model, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (conversation_id, safe_limit),
        ).fetchall()
        return [dict(row) for row in rows]


def list_recent_messages(
    conversation_id: str, limit: int = 10
) -> list[dict]:
    """读取最近若干条消息，并恢复成从旧到新的顺序交给模型。"""
    safe_limit = max(1, min(limit, 50))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT id, role, content, mode, model, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (conversation_id, safe_limit),
        ).fetchall()
        return [dict(row) for row in rows]


def save_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    mode: str = "summary",
    model: str | None = None,
    citations: list[dict] | None = None,
) -> int:
    """保存一条用户或助手消息，并更新对话的最后活动时间。"""
    if role not in {"user", "assistant"}:
        raise ValueError("消息角色只能是 user 或 assistant")

    now = _utc_now()
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO messages
                (conversation_id, role, content, citations_json, mode, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                role,
                content,
                json.dumps(citations or [], ensure_ascii=False),
                mode,
                model,
                now,
            ),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        return int(cursor.lastrowid)
