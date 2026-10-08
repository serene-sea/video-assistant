import { apiClient } from './client'

// TypeScript 类型定义，用于规范化 SSE（Server-Sent Events）流式响应的事件处理器结构
export type SseHandlers = Record<string, (payload: unknown) => void>

export interface SavedSummary {
  video_id: string
  content: string
  strategy: string
  model: string
  updated_at: string
}

export interface ConversationItem {
  conversation_id: string
  preview: string
  created_at: string
  updated_at: string
}

export interface SavedMessage {
  message_id: number
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  created_at: string
}

export interface Citation {
  chunk_id: string
  start: number
  end: number
}

interface ProcessVideoOptions {
  language?: string
  signal?: AbortSignal
}

interface ChatOptions {
  conversationId?: string | null
  // deep 会进入后端 ReAct 工具循环；summary/rag 仍走各自固定问答路径。
  mode?: 'summary' | 'rag' | 'deep'
}

async function postSse(
  path: string,
  body: unknown,
  handlers: SseHandlers,
  signal?: AbortSignal,
): Promise<void> {
  /**
   * 处理 SSE
   * fetch 比 axios 更适合逐块读取 ReadableStream。buffer 保留被网络分包截断的半个 SSE 事件，直到读到空行后才交给上层回调。
   * 后端每个事件只表达一种状态变化（例如 answer_delta、answer_reset、citations）；
   * 这里仅负责拆包和分发，不把进度、工具轨迹或回答内容混成一个字符串。
   */
  // signal 只关闭当前浏览器订阅；后台任务是否继续由服务端的刷新宽限逻辑决定。
  const response = await fetch(`/api${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || `请求失败（${response.status}）`)
  }
  if (!response.body) throw new Error('浏览器未收到流式响应')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const consume = (flush = false) => {
    buffer = buffer.replace(/\r\n/g, '\n')
    const blocks = buffer.split('\n\n')
    // 网络分包可能截断一条 SSE；只解析完整事件，把最后半条留给下一次读取。
    if (!flush) buffer = blocks.pop() ?? ''
    else buffer = ''

    for (const block of blocks) {
      if (!block.trim()) continue
      let eventName = 'message'
      const dataLines: string[] = []
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
      }
      const rawData = dataLines.join('\n')
      let payload: unknown = rawData
      try {
        payload = JSON.parse(rawData)
      } catch {
        // 文本增量不一定是 JSON；保持原字符串即可。
      }
      handlers[eventName]?.(payload)
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    consume()
  }
  buffer += decoder.decode()
  if (buffer.trim()) {
    buffer += '\n\n'
    consume(true)
  }
}

export function processVideo(  // export 表示该函数可以在其他模块中被导入使用，是 TypeScript 中的一个关键字，用于模块化编程。
  videoId: string,
  handlers: SseHandlers,
  options: ProcessVideoOptions = {},
) {
  return postSse(
    `/videos/${videoId}/process`,
    { language: options.language ?? 'zh' },
    handlers,
    options.signal,
  )
}

export function chatWithVideo(
  videoId: string,
  question: string,
  handlers: SseHandlers,
  options: ChatOptions = {},
) {
  // conversationId 标识要继续的持久化对话；null 表示让后端在第一次提问时新建。
  // 浏览器不上传历史消息和字幕，后端从 SQLite 读取并按所选模式组装模型上下文。
  return postSse(
    `/videos/${videoId}/chat`,
    {
      question,
      conversation_id: options.conversationId ?? null,
      mode: options.mode ?? 'summary',
    },
    handlers,
  )
}

export async function getSavedSummary(videoId: string): Promise<SavedSummary> {
  // 该 GET 接口只读取 SQLite 缓存，不会重新获取字幕或调用 LLM。
  const response = await apiClient.get<SavedSummary>(`/videos/${videoId}/summary`)
  return response.data
}

export async function listVideoConversations(
  videoId: string,
): Promise<ConversationItem[]> {
  const response = await apiClient.get<ConversationItem[]>(
    `/videos/${videoId}/conversations`,
  )
  return response.data
}

export async function getConversationMessages(
  videoId: string,
  conversationId: string,
): Promise<SavedMessage[]> {
  const response = await apiClient.get<SavedMessage[]>(
    `/videos/${videoId}/conversations/${conversationId}/messages`,
  )
  return response.data
}

export async function deleteVideoConversation(
  videoId: string,
  conversationId: string,
): Promise<void> {
  await apiClient.delete(`/videos/${videoId}/conversations/${conversationId}`)
}
