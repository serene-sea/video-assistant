<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between gap-3">
      <span class="text-xs font-medium text-text-secondary">对话记录</span>
      <div class="flex max-w-[80%] items-center gap-2">
        <select
          :value="conversationId ?? ''"
          :disabled="chatLoading || historyLoading"
          class="min-w-0 h-9 px-3 rounded-lg border border-border bg-white text-xs text-text-primary focus:outline-none focus:ring-2 focus:ring-primary/30"
          aria-label="选择历史对话"
          @change="changeConversation"
        >
          <option value="">新对话</option>
          <option
            v-for="item in conversationHistory"
            :key="item.conversation_id"
            :value="item.conversation_id"
          >
            {{ item.preview }}
          </option>
        </select>
        <button
          v-if="conversationId"
          type="button"
          class="shrink-0 rounded-lg border border-red-200 px-3 py-2 text-xs text-red-600 hover:bg-red-50 disabled:opacity-50"
          :disabled="chatLoading || historyLoading"
          @click="deleteCurrentConversation"
        >删除对话</button>
      </div>
    </div>

    <div class="flex flex-wrap gap-2" aria-label="问答模式">
      <button
        type="button"
        :aria-pressed="chatMode === 'summary'"
        class="px-3 py-1.5 rounded-lg text-xs border"
        :class="chatMode === 'summary' ? 'border-primary bg-primary/5 text-primary' : 'border-border text-text-secondary'"
        @click="chatMode = 'summary'"
      >总结问答</button>
      <button
        type="button"
        :aria-pressed="chatMode === 'deep'"
        class="px-3 py-1.5 rounded-lg text-xs border"
        :class="chatMode === 'deep' ? 'border-primary bg-primary/5 text-primary' : 'border-border text-text-secondary'"
        @click="chatMode = 'deep'"
      >深入分析</button>
    </div>
    <p class="-mt-2 text-xs text-text-muted" role="status">
      {{ chatMode === 'summary' ? '根据视频总结快速回答。' : '适合需要分步查找和梳理的问题。' }}
    </p>

    <p v-if="historyError" class="text-xs text-red-600" role="alert">
      {{ historyError }}
    </p>

    <div ref="chatContainer" class="space-y-4 max-h-[400px] overflow-y-auto pr-1">
      <div
        v-if="historyLoading"
        class="py-12 text-center text-sm text-text-muted"
      >
        正在加载历史对话...
      </div>
      <div
        v-else-if="chatMessages.length === 0"
        class="flex flex-col items-center justify-center py-12 text-text-muted"
      >
        <svg class="w-12 h-12 mb-3 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="1.5"
            d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
          />
        </svg>
        <p class="text-sm mb-1">向 AI 提问关于这个视频的问题</p>
        <p class="text-xs">例如：“这个视频的核心观点是什么？”</p>
      </div>

      <div
        v-for="(message, index) in chatMessages"
        :key="index"
        :class="['flex', message.role === 'user' ? 'justify-end' : 'justify-start']"
      >
        <div
          :class="[
            'max-w-[80%] px-4 py-2.5 rounded-2xl text-sm leading-relaxed',
            message.role === 'user'
              ? 'bg-primary text-white rounded-br-md'
              : 'bg-bg-section text-text-primary rounded-bl-md border border-border-light',
          ]"
        >
          <div
            v-if="message.role === 'assistant'"
            class="chat-prose prose prose-slate prose-sm max-w-none"
            v-html="renderMarkdown(message.content)"
          ></div>
          <span v-else>{{ message.content }}</span>
          <span
            v-if="message.role === 'assistant' && message.loading"
            class="inline-block w-1.5 h-4 bg-primary/60 rounded-sm animate-pulse ml-0.5 align-text-bottom"
          ></span>
          <div
            v-if="message.role === 'assistant' && message.citations?.length && shouldShowCitations(index)"
            class="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-border-light"
            aria-label="相关时间点"
          >
            <span
              v-for="citation in message.citations"
              :key="citation.chunk_id"
              class="rounded-md bg-white px-2 py-1 text-[11px] text-primary border border-border-light"
            >{{ formatCitationTime(citation.start, citation.end) }}</span>
          </div>
        </div>
      </div>
    </div>

    <p v-if="chatStage && chatLoading" class="text-xs text-text-secondary" role="status">
      {{ chatStage }}
    </p>
    <ol
      v-if="showAgentTrace && chatLoading && chatToolTrace.length"
      class="space-y-1 rounded-lg bg-bg-section px-3 py-2 text-xs text-text-secondary"
      aria-label="正在查找的视频内容"
    >
      <li v-for="(item, index) in chatToolTrace" :key="item.call_id ?? `${item.round}-${item.tool}-${index}`">
        第 {{ item.round }} 轮查找：{{ toolLabel(item.tool) }}，{{ item.message }}
      </li>
    </ol>

    <form class="flex gap-2 pt-3 border-t border-border-light" @submit.prevent="sendQuestion">
      <input
        v-model="chatInput"
        type="text"
        maxlength="1000"
        placeholder="输入你的问题..."
        class="flex-1 h-11 px-4 rounded-xl border border-border bg-white text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
        :disabled="chatLoading"
      />
      <button
        type="submit"
        :disabled="!chatInput.trim() || chatLoading"
        class="h-11 px-5 rounded-xl bg-primary hover:bg-primary-dark text-white text-sm font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer flex items-center gap-1.5"
      >
        <svg v-if="chatLoading" class="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
        </svg>
        发送
      </button>
    </form>
  </div>
</template>










<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { getErrorMessage } from '@/api/client'
import {
  chatWithVideo,
  deleteVideoConversation,
  getConversationMessages,
  listVideoConversations,
  type Citation,
  type ConversationItem,
} from '@/api/summarize'
import { writeWorkspaceLocation } from '@/workspaceUrl'

const props = defineProps<{
  videoId: string
  initialConversationId?: string | null
}>()

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  loading?: boolean
  citations?: Citation[]
}

interface AgentTrace {
  call_id?: string
  round: number
  tool: string
  status: string
  message: string
}

const chatMessages = ref<ChatMessage[]>([])
const chatInput = ref('')
const chatLoading = ref(false)
const chatStage = ref('')
// 用户看到的是两种问答方式；选 deep 才启动后端的 Agent 工具循环。
const chatMode = ref<'summary' | 'rag' | 'deep'>('summary')
const chatToolTrace = ref<AgentTrace[]>([])
const showAgentTrace = ref(false)
const chatContainer = ref<HTMLElement | null>(null)
const conversationHistory = ref<ConversationItem[]>([])
const historyLoading = ref(false)
const historyError = ref('')
// 后端首次创建会话后返回该 ID；后续问题带上它，才能读取同一段历史。
const conversationId = ref<string | null>(props.initialConversationId ?? null)

marked.setOptions({ breaks: true, gfm: true })

function renderMarkdown(text: string) {
  if (!text) return ''
  // 模型内容最终进入 v-html，因此必须先清洗生成的 HTML。
  return DOMPurify.sanitize(marked.parse(text, { async: false }) as string)
}

function clearTransientStatus() {
  // 过程提示只属于当前请求，切换会话或请求结束后不保留到另一个窗口。
  chatStage.value = ''
  chatToolTrace.value = []
  showAgentTrace.value = false
}

function toolLabel(tool: string) {
  const labels: Record<string, string> = {
    get_video_outline: '查看内容提纲',
    search_transcript: '查找相关内容',
    read_transcript_range: '梳理相关片段',
  }
  return labels[tool] ?? '整理查找结果'
}

function getStageMessage(payload: unknown) {
  if (!payload || typeof payload !== 'object') return ''
  const stage = 'stage' in payload ? String(payload.stage ?? '') : ''
  const messages: Record<string, string> = {
    indexing: '正在查找视频中的相关内容…',
    retrieving: '正在整理相关内容…',
    agent_planning: '正在理解你的问题…',
    agent_tools: '正在查找相关内容…',
    agent_answer: '正在整理回答…',
  }
  return messages[stage] ?? ('message' in payload ? String(payload.message ?? '') : '')
}

function shouldShowCitations(messageIndex: number) {
  // 时间标签保留在历史数据中，只有用户明确询问时间时才显示，避免回答后堆出一排数字。
  const previousQuestion = chatMessages.value
    .slice(0, messageIndex)
    .reverse()
    .find((item) => item.role === 'user')?.content ?? ''
  return /时间|时刻|几点|几分|几秒|第.{0,4}分钟|第.{0,4}秒|什么时候|何时|哪一段|time\s?stamp/i.test(previousQuestion)
}

async function sendQuestion() {
  const question = chatInput.value.trim()
  if (!question || chatLoading.value) return

  chatInput.value = ''
  chatMessages.value.push({ role: 'user', content: question })
  // 先乐观地在当前窗口显示提问和空回答；真实保存由后端在 SSE 流程中完成。
  // 后端发来 answer_reset 时必须覆盖这条临时正文，不能把拒答拼到旧输出后面。
  const assistantMessage: ChatMessage = {
    role: 'assistant',
    content: '',
    loading: true,
  }
  chatMessages.value.push(assistantMessage)
  chatLoading.value = true
  historyError.value = ''
  clearTransientStatus()
  scrollToBottom()

  let streamFailed = false
  try {
    await chatWithVideo(
      props.videoId,
      question,
      {
        stage: (payload) => {
          chatStage.value = getStageMessage(payload)
        },
        tool_trace: (payload) => {
          if (!payload || typeof payload !== 'object') return
          const trace = payload as Partial<AgentTrace>
          if (
            typeof trace.round === 'number' &&
            typeof trace.tool === 'string' &&
            typeof trace.status === 'string' &&
            typeof trace.message === 'string'
          ) {
            if (!assistantMessage.content && assistantMessage.loading) {
              showAgentTrace.value = true
            }
            // started 与 completed/failed 事件用 call_id 配对；同一轮并行的同名工具也不会混在一起。
            const previous = trace.call_id
              ? chatToolTrace.value.find((item) => item.call_id === trace.call_id)
              : [...chatToolTrace.value].reverse().find((item) =>
                  item.round === trace.round &&
                  item.tool === trace.tool &&
                  item.status === 'started'
                )
            if (trace.status !== 'started' && previous) {
              previous.status = trace.status
              previous.message = trace.message
            } else {
              chatToolTrace.value.push({
                call_id: trace.call_id,
                round: trace.round,
                tool: trace.tool,
                status: trace.status,
                message: trace.message,
              })
            }
          }
        },
        conversation: (payload) => {
          if (payload && typeof payload === 'object' && 'conversation_id' in payload) {
            conversationId.value = String(payload.conversation_id)
            // 后端可能刚创建新对话；拿到 ID 后立即写入地址，刷新页面时才能定位此会话。
            // URL 只保存会话 ID；刷新后再通过只读接口获取真实消息。
            writeWorkspaceLocation(props.videoId, conversationId.value)
          }
        },
        answer_delta: (payload) => {
          // 后端发送 { text: "本次新增内容" }，所以这里必须追加 text 字段。
          assistantMessage.content += getEventText(payload, 'text')
          clearTransientStatus()
          scrollToBottom()
        },
        done: () => {
          assistantMessage.loading = false
          clearTransientStatus()
        },
        answer_reset: (payload) => {
          // 引用校验失败时，后端用固定拒答替换临时输出；这里覆盖而不是追加。
          assistantMessage.content = getEventText(payload, 'text')
          assistantMessage.citations = []
          clearTransientStatus()
        },
        citations: (payload) => {
          assistantMessage.citations = normalizeCitations(payload)
        },
        error: (payload) => {
          streamFailed = true
          assistantMessage.loading = false
          assistantMessage.content = `❌ ${getEventError(payload, '回答失败')}`
          clearTransientStatus()
        },
      },
      { conversationId: conversationId.value, mode: chatMode.value },
    )
    if (!streamFailed) await loadConversationList()
  } catch (error) {
    streamFailed = true
    assistantMessage.content = `❌ 请求失败：${error instanceof Error ? error.message : '未知错误'}`
  } finally {
    assistantMessage.loading = false
    chatLoading.value = false
    clearTransientStatus()
    scrollToBottom()
  }
}

async function loadConversationList() {
  try {
    conversationHistory.value = await listVideoConversations(props.videoId)
  } catch (error) {
    historyError.value = `历史对话加载失败：${getErrorMessage(error)}`
  }
}

async function deleteCurrentConversation() {
  const targetId = conversationId.value
  if (!targetId || chatLoading.value || historyLoading.value) return
  if (!window.confirm('删除这段对话及其中的消息？此操作无法撤销。')) return
  historyLoading.value = true
  historyError.value = ''
  try {
    await deleteVideoConversation(props.videoId, targetId)
    conversationId.value = null
    chatMessages.value = []
    writeWorkspaceLocation(props.videoId)
    await loadConversationList()
  } catch (error) {
    historyError.value = `对话删除失败：${getErrorMessage(error)}`
  } finally {
    historyLoading.value = false
  }
}

async function restoreConversation(targetConversationId: string) {
  clearTransientStatus()
  historyLoading.value = true
  historyError.value = ''
  try {
    const messages = await getConversationMessages(
      props.videoId,
      targetConversationId,
    )
    // 恢复数据来自 SQLite；loading 只属于正在生成的新回答，不写入历史记录。
    chatMessages.value = messages.map((message) => ({
      role: message.role,
      content: message.content,
      citations: message.citations ?? [],
    }))
    conversationId.value = targetConversationId
    writeWorkspaceLocation(props.videoId, targetConversationId)
    scrollToBottom()
  } catch (error) {
    // URL 中的会话可能已被删除或不属于当前视频，此时退回“新对话”。
    conversationId.value = null
    chatMessages.value = []
    writeWorkspaceLocation(props.videoId)
    historyError.value = `历史消息恢复失败：${getErrorMessage(error)}`
  } finally {
    historyLoading.value = false
  }
}

async function changeConversation(event: Event) {
  const selectedId = (event.target as HTMLSelectElement).value
  if (!selectedId) {
    // “新对话”并不会立刻写数据库；用户发送第一条问题时后端才创建会话。
    conversationId.value = null
    chatMessages.value = []
    historyError.value = ''
    clearTransientStatus()
    writeWorkspaceLocation(props.videoId)
    return
  }
  await restoreConversation(selectedId)
}

function getEventText(payload: unknown, field: string) {
  if (typeof payload === 'string') return payload
  if (payload && typeof payload === 'object' && field in payload) {
    return String(payload[field as keyof typeof payload] ?? '')
  }
  return ''
}

function getEventError(payload: unknown, fallback: string) {
  if (typeof payload === 'string') return payload || fallback
  if (payload && typeof payload === 'object' && 'message' in payload) {
    return String(payload.message)
  }
  return fallback
}

function normalizeCitations(payload: unknown): Citation[] {
  if (!Array.isArray(payload)) return []
  return payload.filter((item): item is Citation => {
    return Boolean(
      item &&
        typeof item === 'object' &&
        'chunk_id' in item &&
        typeof item.chunk_id === 'string' &&
        'start' in item &&
        typeof item.start === 'number' &&
        'end' in item &&
        typeof item.end === 'number',
    )
  })
}

function formatCitationTime(start: number, end: number) {
  return `[${formatTime(start)}–${formatTime(end)}]`
}

function formatTime(value: number) {
  const seconds = Math.max(0, Math.floor(value))
  const minutes = Math.floor(seconds / 60)
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

function scrollToBottom() {
  // 等 Vue 把新文本更新到 DOM 后再读取 scrollHeight，否则拿到的是旧高度。
  nextTick(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  })
}

onMounted(async () => {
  await loadConversationList()
  if (props.initialConversationId) {
    await restoreConversation(props.initialConversationId)
  }
})
</script>









<style scoped>
.chat-prose :deep(p) {
  margin-bottom: 0.5rem;
  line-height: 1.7;
}

.chat-prose :deep(p:last-child) {
  margin-bottom: 0;
}

.chat-prose :deep(ul),
.chat-prose :deep(ol) {
  margin-bottom: 0.5rem;
  padding-left: 1.25rem;
}

.chat-prose :deep(li) {
  margin-bottom: 0.2rem;
  line-height: 1.7;
}

.chat-prose :deep(li::marker) {
  color: var(--color-primary);
}

.chat-prose :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  padding: 0.1rem 0.35rem;
  border-radius: 3px;
  font-size: 0.85em;
}

.chat-prose :deep(blockquote) {
  border-left: 2px solid var(--color-primary);
  padding-left: 0.75rem;
  color: var(--color-text-secondary);
  margin: 0.5rem 0;
}

.chat-prose :deep(strong) {
  font-weight: 600;
}
</style>
