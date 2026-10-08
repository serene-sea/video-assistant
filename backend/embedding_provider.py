"""独立配置的异步 Embedding Provider，不复用聊天模型凭据。"""

from __future__ import annotations

import asyncio
import math
import os
import threading
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from openai import AsyncOpenAI


# 同一本地模型只加载一次；推理加锁，避免多个请求同时抢同一份 CPU/GPU 模型。
_local_inference_lock = threading.Lock()


@lru_cache(maxsize=2)
def _load_local_model(model_path: str, device: str):
    """从磁盘加载缓存模型，强制离线，避免启动时意外联网下载。"""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "本地 Embedding 需要安装 sentence-transformers 和 PyTorch"
        ) from exc
    return SentenceTransformer(
        model_path,
        local_files_only=True,
        # auto 时交给 Sentence Transformers 检测 CUDA；CPU 版 PyTorch 会自然回退 CPU。
        device=None if device == "auto" else device,
    )


class EmbeddingProvider:
    """可选择异步远程服务或线程池中的本地模型，并统一校验向量。"""

    def __init__(self) -> None:
        self.provider_name = os.getenv("EMBEDDING_PROVIDER", "openai-compatible").strip()
        self.model = os.getenv("EMBEDDING_MODEL", "").strip()
        try:
            self.dimension = int(os.getenv("EMBEDDING_DIMENSION", "0"))
        except ValueError as exc:
            raise ValueError("EMBEDDING_DIMENSION 必须是正整数") from exc
        if not self.model or self.dimension < 1:
            raise ValueError(
                "请设置 EMBEDDING_MODEL 和正整数 EMBEDDING_DIMENSION"
            )
        self.model_path: Path | None = None
        self.device = "auto"
        self.query_prompt = ""
        self.client: AsyncOpenAI | None = None

        if self.provider_name == "sentence-transformers-local":
            configured_path = os.getenv("EMBEDDING_MODEL_PATH", "").strip()
            if not configured_path:
                raise ValueError("本地模型需要设置 EMBEDDING_MODEL_PATH")
            path = Path(configured_path)
            if not path.is_absolute():
                path = Path(__file__).resolve().parent / path
            self.model_path = path.resolve()
            if not self.model_path.is_dir():
                raise ValueError("EMBEDDING_MODEL_PATH 不存在或不是模型目录")
            # 本地模型没有网络 Endpoint；把路径纳入指纹，切换模型目录时重建向量。
            self.base_url = f"file://{self.model_path.as_posix()}"
            # 默认自动使用可用 GPU；仍可通过环境变量手动指定 cpu 或 cuda。
            self.device = os.getenv("EMBEDDING_DEVICE", "auto").strip() or "auto"
            # Qwen3 用 query 提示提高问题向量质量；没有该提示的模型会自动忽略。
            self.query_prompt = os.getenv("EMBEDDING_QUERY_PROMPT", "query").strip()
            return

        api_key = os.getenv("EMBEDDING_API_KEY")
        base_url = os.getenv("EMBEDDING_BASE_URL")
        self.base_url = (base_url or "").rstrip("/")
        if not api_key or not base_url:
            raise ValueError(
                "远程 Embedding 需要分别设置 EMBEDDING_API_KEY 和 EMBEDDING_BASE_URL"
            )
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "60")),
        )

    def _encode_local(
        self, texts: list[str], *, is_query: bool = False
    ) -> list[list[float]]:
        """在线程池中同步推理，问题可使用模型自带的 query 提示。"""
        assert self.model_path is not None
        with _local_inference_lock:
            # 首次加载也放在锁内，避免并发请求各自重复初始化模型。
            model = _load_local_model(str(self.model_path), self.device)
            options = {"prompt_name": self.query_prompt} if is_query else {}
            # MiniLM 没有 query prompt；只在模型确实提供该提示时才传入。
            if options and self.query_prompt not in model.prompts:
                options = {}
            vectors = model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
                **options,
            )
        return vectors.tolist()

    def _validate_vectors(self, vectors: list[list[float]], expected: int) -> None:
        """远程和本地向量共用同一套数量、维度与数值检查。"""
        if len(vectors) != expected or any(
            len(vector) != self.dimension
            or any(not math.isfinite(value) for value in vector)
            for vector in vectors
        ):
            raise ValueError(
                f"Embedding 返回结果无效（数量、维度或数值不符），预期 "
                f"{expected} 个 {self.dimension} 维向量"
            )

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """批量编码并按输入顺序返回向量；服务返回错维度时立即失败。"""
        if not texts:
            return []
        if self.provider_name == "sentence-transformers-local":
            # CPU/GPU 编码是同步重活，放进线程池，不能卡住 FastAPI 事件循环。
            vectors = await asyncio.to_thread(self._encode_local, list(texts))
            self._validate_vectors(vectors, len(texts))
            return vectors

        assert self.client is not None
        # 字幕块和用户问题必须交给同一个模型编码，结果才处于可比较的向量空间。
        response = await self.client.embeddings.create(
            model=self.model,
            input=list(texts),
        )
        # 按服务返回的 index 排序，避免向量顺序和字幕块顺序错位。
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [list(item.embedding) for item in ordered]
        self._validate_vectors(vectors, len(texts))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """单独编码查询问题，使用与字幕块相同的模型和维度。"""
        if self.provider_name == "sentence-transformers-local":
            vectors = await asyncio.to_thread(
                self._encode_local, [text], is_query=True
            )
            self._validate_vectors(vectors, 1)
            return vectors[0]
        vectors = await self.embed_texts([text])
        return vectors[0]
