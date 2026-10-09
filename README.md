# Video Assistant

一个基于大语言模型的视频总结与问答应用。输入公开视频链接后，系统获取视频信息和字幕；没有可用字幕时可尝试语音识别。用户可以查看内容总结、针对视频提问，或使用 Agent 深入查找字幕内容。

## 功能亮点

- **视频总结：** 根据字幕长度直接生成总结，或分块总结后合并结果。
- **视频问答：** 结合视频总结、局部摘要和与问题相关的字幕内容回答；字幕检索暂不可用或没有相关片段时，仍可根据已有总结回答。
- **ReAct Agent：** Agent 可按问题调用只读工具，查看总结提纲、搜索字幕或读取指定时间段，再根据结果继续回答。工具限定在当前视频，并设有调用范围限制。
- **可追溯的回答依据：** 相关字幕片段与视频时间戳关联，引用由后端校验；时间点只在用户询问时展示。
- **历史与流式响应：** SQLite 保存视频、总结和对话；SSE 将处理进度与回答逐步发送到页面。

### 深入分析 Agent

普通问答会结合视频总结和一次字幕检索回答。遇到需要跨片段梳理或核对细节的问题时，Agent 可以分步查找：先参考已保存的总结，并按需查看章节提纲；再搜索字幕关键词、读取相关时间段的原文。每次查找结果都会交回模型，让它决定继续查找还是整理回答。例如，用户询问视频前后几部分如何共同支撑一个观点时，Agent 可以针对不同关键词继续查找，再综合实际找到的片段作答。这种“决定下一步、调用工具、根据结果继续”的过程，让 Agent 能按问题调整查找路径。

Agent 的工具只能读取当前用户有权访问的视频。后端会检查工具名称、参数和读取范围，并记录本次问题实际查到的字幕；回答引用只能对应这些字幕，时间范围由后端从原始片段读取。没有找到字幕依据或引用校验失败时，系统会拒答。每个问题最多进行 5 轮工具决策、执行 5 次工具调用，单次读取不超过 120 秒，累计证据不超过 6000 字符，以限制调用成本和读取范围。

## 技术栈

Vue 3、TypeScript、Tailwind CSS、FastAPI、SQLite、Chroma、OpenAI 兼容 Chat API、yt-dlp、faster-whisper。

## 本地运行

### 环境要求

- Python 3.10 或更新版本
- Node.js 22.18 或更新版本
- 可用的 OpenAI 兼容 Chat API

### 快速体验一个视频样例

克隆项目后，可以先用 Python 脚本体验项目，不需要启动前端。脚本列出 8:43 有字幕的 B 站视频、5:21 无字幕的 B 站视频、40:41 B 站长视频和 7:52 无字幕的抖音视频；每次只处理你选择的一个视频，不会自动跑完全部样例。

先安装后端依赖，并按下方“配置并启动后端”中的方式创建 `backend/.env`、填写自己的 `CHAT_API_KEY`。然后在项目根目录运行：

```powershell
python -m pip install -r backend/requirements.txt
python demo.py
```

选择菜单中的视频后，脚本会复用已经运行的本地后端；如果后端没有运行，则自动启动。脚本只会关闭自己启动的后端。之后可以输入普通问答和 Agent 问题，直接回车会使用默认问题。无字幕视频会尝试本地语音识别，可能需要下载 Whisper 模型并等待较长时间；长视频也会消耗更多时间和模型额度。抖音等外部平台的访问能力受平台限制，样例是否能解析以实际运行结果为准。

此体验需要你自行提供可用的 Chat API Key，模型请求会使用你的服务额度。不要把 Key 写入脚本、README 或提交到 GitHub。字幕向量检索还需要单独配置 Embedding；未配置时，普通问答会按现有逻辑尝试使用已保存的总结回答。

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

总结和 Agent 使用 Chat API。视频问答会在单独 Embedding 配置可用时检索相关字幕，并结合总结回答；Embedding 暂不可用时会退回已有总结。可以使用兼容 Embedding API，也可以准备本地 Qwen3 Embedding 模型。

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
