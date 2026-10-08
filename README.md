# Video Assistant

一个基于大语言模型的视频总结与问答应用。输入公开视频链接后，系统获取视频信息和字幕；没有可用字幕时可尝试语音识别。用户可以查看内容总结、针对视频提问，或使用 Agent 深入查找字幕内容。

## 功能亮点

- **视频总结：** 根据字幕长度直接生成总结，或分块总结后合并结果。
- **视频问答：** 结合视频总结和当前对话回答问题，支持连续交流。
- **ReAct Agent：** Agent 可按问题调用只读工具，查看总结提纲、搜索字幕或读取指定时间段，再根据结果继续回答。工具限定在当前视频，并设有调用范围限制。
- **字幕检索与引用：** 可选使用 Embedding 和 Chroma 检索字幕；来源片段与视频时间戳关联，回答引用经过后端校验。
- **历史与流式响应：** SQLite 保存视频、总结和对话；SSE 将处理进度与回答逐步发送到页面。

## 技术栈

Vue 3、TypeScript、Tailwind CSS、FastAPI、SQLite、Chroma、OpenAI 兼容 Chat API、yt-dlp、faster-whisper。

## 本地运行

### 环境要求

- Python 3.10 或更新版本
- Node.js 22.18 或更新版本
- 可用的 OpenAI 兼容 Chat API

### 1. 配置并启动后端

在项目根目录运行：

```powershell
Copy-Item .env.example backend/.env
```

编辑 `backend/.env`，填写聊天服务配置：

```dotenv
CHAT_API_KEY=你的API密钥
CHAT_BASE_URL=https://api.deepseek.com
CHAT_MODEL=deepseek-chat
AGENT_MODEL=deepseek-flash
```

安装依赖并从项目根目录启动：

```powershell
python -m pip install -r backend/requirements.txt
python -m backend.main
```

请勿把密钥写入代码或提交到 Git。不要在 `backend` 目录中直接运行 `python main.py`。

### 2. 启动前端

另开终端，在项目根目录运行：

```powershell
cd frontend
npm install
npm run dev
```

打开终端显示的本地地址，通常是 `http://localhost:5173`。

### 3. 配置字幕向量检索（可选）

总结、问答和 Agent 使用 Chat API；字幕向量检索还需要单独的 Embedding 配置。可以使用兼容 Embedding API，也可以准备本地 Qwen3 Embedding 模型。

本地模型依赖安装命令：

```powershell
python -m pip install -r backend/requirements-local-embedding.txt
```

然后在 `backend/.env` 中设置本地模型，例如：

```dotenv
EMBEDDING_PROVIDER=sentence-transformers-local
EMBEDDING_MODEL=Qwen3-Embedding-0.6B
EMBEDDING_MODEL_PATH=./models/Qwen3-Embedding-0.6B
EMBEDDING_DIMENSION=1024
EMBEDDING_DEVICE=auto
```

模型文件需自行放入 `backend/models/`。该目录和运行时数据库不会提交到 Git。
