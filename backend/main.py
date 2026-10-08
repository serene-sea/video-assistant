""" Video Assistant FastAPI 入口 """

from contextlib import asynccontextmanager
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# .env 放在 backend 目录。显式传入路径后，无论从项目根目录还是 backend
# 目录启动，读取到的都是同一个配置文件。
load_dotenv(Path(__file__).resolve().with_name(".env"))

from .api import router as api_router
from .database import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用启动时建表；yield 之后属于关闭阶段，目前没有常驻资源需要释放。"""
    init_db()
    yield


app = FastAPI(
    title="Video Assistant API",
    description="视频内容总结与 AI 问答后端",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # 本地开发只允许 Vite 地址；部署时通过环境变量替换为真实前端域名。
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# API 统一定义在 api.py，main.py 只负责创建和组装 FastAPI 应用。
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn  # uvicorn 是一个高性能的 ASGI 服务器，用于运行 FastAPI 应用。

    # 字符串导入路径支持 reload；只用于本地开发。
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
