<template>
  <div class="bg-white rounded-2xl border border-border shadow-lg overflow-hidden h-full flex flex-col">

    <!-- 第一阶段 A 只保留“视频总结”和“AI 问答”两个核心入口。 -->
    <div class="flex border-b border-border-light">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        :disabled="tab.key === 'qa' && !summaryReady"
        :title="tab.key === 'qa' && !summaryReady ? '完成视频总结后即可提问' : ''"
        @click="selectTab(tab.key)"
        :class="[
          'flex items-center gap-2 px-5 py-3.5 text-sm font-medium transition-all relative cursor-pointer',
          tab.key === 'qa' && !summaryReady ? 'opacity-40 cursor-not-allowed' : '',
          activeTab === tab.key
            ? 'text-primary'
            : 'text-text-secondary hover:text-text-primary',
        ]"
      >
        <span>{{ tab.icon }}</span>
        <span>{{ tab.label }}</span>
        <div
          v-if="activeTab === tab.key"
          class="absolute bottom-0 left-0 right-0 h-0.5 bg-primary"
        ></div>
      </button>
    </div>

    <div class="p-5 sm:p-6 min-h-[400px] flex-1 overflow-y-auto">
      <div
        v-if="loading && !summaryText && activeTab === 'summary'"
        class="flex flex-col items-center justify-center py-16"
      >
        <div class="w-12 h-12 border-4 border-primary/20 border-t-primary rounded-full animate-spin mb-4"></div>
        <p class="text-text-secondary text-sm">{{ loadingMessage }}</p>
      </div>

      <!-- 视频总结：只消费后端流式事件，不在浏览器保存完整字幕。 -->
      <div v-show="activeTab === 'summary'">
        <div v-if="errorMessage" class="mb-4 rounded-xl bg-red-50 p-4" role="alert">
          <p class="text-sm text-red-600 mb-3">{{ errorMessage }}</p>
          <button
            class="text-sm font-medium text-primary hover:text-primary-dark"
            @click="startSummarize"
          >
            重新分析
          </button>
        </div>

        <div
          v-if="summaryText"
          class="prose prose-slate prose-sm max-w-none summary-prose"
          v-html="renderedSummary"
        ></div>
        <div
          v-if="loading && summaryText"
          class="mt-2 inline-flex items-center gap-1.5 text-xs text-text-muted"
        >
          <span class="w-1.5 h-1.5 bg-primary rounded-full animate-pulse"></span>
          AI 正在生成中...
        </div>
      </div>

      <!-- 问答状态和请求逻辑放在独立组件中，VideoSummary 只控制 Tab 和总结。 -->
      <VideoChat
        v-show="activeTab === 'qa'"
        :video-id="videoId"
        :initial-conversation-id="initialConversationId"
      />
    </div>
  </div>
</template>





















<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { processVideo } from '@/api/summarize'
import VideoChat from '@/components/VideoChat.vue'

const props = defineProps<{
  videoId: string
  initialSummary?: string
  initialConversationId?: string | null
}>()
const emit = defineEmits<{ 'loading-change': [value: boolean] }>()

const tabs = [
  { key: 'summary', label: '视频总结', icon: '📝' },
  { key: 'qa', label: 'AI 问答', icon: '💬' },
]

// URL 中指定了历史会话时，恢复完成后直接打开问答 Tab。
const activeTab = ref(props.initialConversationId ? 'qa' : 'summary')
const loading = ref(false)
const loadingMessage = ref('正在准备视频内容…')
// props 只提供初始值；之后的流式增量仍由本组件自己的 ref 管理。
const summaryText = ref(removeLegacySourceNotice(props.initialSummary ?? ''))
const renderedSummary = ref('')
const errorMessage = ref('')
const summaryReady = ref(Boolean(props.initialSummary))
let processController: AbortController | null = null

marked.setOptions({
  breaks: true,
  gfm: true,
})

// watch() 是 Vue 3 组合式 API（Composition API）中的侦听器，用于在响应式数据发生变化时执行操作。
watch(loading, (value) => {
  emit('loading-change', value)
})
watch(summaryText, (value) => {
  renderedSummary.value = renderMarkdown(value)
}, { immediate: true })

function renderMarkdown(text: string) {
  if (!text) return ''
  // LLM 输出会进入 v-html；先清洗生成的 HTML，避免把不可信内容直接写入页面。
  return DOMPurify.sanitize(marked.parse(text, { async: false }) as string)
}

function removeLegacySourceNotice(text: string) {
  // 旧版本把固定来源警告存进总结；恢复历史总结时只移除开头那段旧提示。
  return text.replace(/^> ⚠️ \*\*信息范围说明：\*\*[^\n]*(?:\n[^\n]*)*\n\n/, '')
}

function selectTab(tab: string) {
  if (tab === 'qa' && !summaryReady.value) return
  activeTab.value = tab
}

async function startSummarize() {  // 生成视频总结⭐
  processController?.abort()
  const controller = new AbortController()
  processController = controller
  loading.value = true
  summaryText.value = ''
  summaryReady.value = false
  errorMessage.value = ''
  loadingMessage.value = '正在准备视频内容…'

  try {
    await processVideo(props.videoId, {
      stage: (payload) => {
        if (typeof payload === 'string') loadingMessage.value = payload
        else if (payload && typeof payload === 'object' && 'message' in payload) {
          loadingMessage.value = String(payload.message)
        }
      },
      summary_delta: (payload) => {
        // summary_delta 是增量内容，覆盖赋值会丢掉先前已到达的文本。
        summaryText.value += getEventText(payload, 'text')
      },
      done: () => {
        loading.value = false
        summaryReady.value = true
      },
      error: (payload) => {
        loading.value = false
        summaryReady.value = false
        errorMessage.value = getEventError(payload, '总结失败')
      },
    }, { signal: controller.signal })
  } catch (error) {
    // 组件切换视频或卸载时主动关闭 SSE，不把取消显示成总结错误。
    if (controller.signal.aborted) return
    loading.value = false
    errorMessage.value = `总结请求失败：${error instanceof Error ? error.message : '未知错误'}`
  }
}

function getEventError(payload: unknown, fallback: string) {
  if (typeof payload === 'string') return payload || fallback
  if (payload && typeof payload === 'object' && 'message' in payload) return String(payload.message)
  return fallback
}

function getEventText(payload: unknown, field: string) {
  if (typeof payload === 'string') return payload
  if (payload && typeof payload === 'object' && field in payload) {
    return String(payload[field as keyof typeof payload] ?? '')
  }
  return ''
}

function detachProcessingOnPageExit() {
  if (!loading.value) return
  // 刷新会重新订阅同一任务；没有页面回来时，服务端会在短暂宽限后取消任务。
  void fetch(`/api/videos/${props.videoId}/process/detach`, {
    method: 'POST',
    credentials: 'include',
    keepalive: true,
  }).catch(() => undefined)
}

onMounted(() => {
  window.addEventListener('pagehide', detachProcessingOnPageExit)
  if (summaryText.value) {
    // 页面恢复时已经从 GET /summary 得到结果，不能再次调用 /process。
    summaryReady.value = true
    return
  }
  // 新视频没有保存结果，挂载后才启动字幕和总结处理链。⭐
  startSummarize()
})

onBeforeUnmount(() => {
  window.removeEventListener('pagehide', detachProcessingOnPageExit)
  processController?.abort()
})
</script>













<style scoped>
/* 总结 Markdown 排版 */
.summary-prose :deep(h1) {
  font-size: 1.25rem;
  font-weight: 700;
  margin-top: 1.5rem;
  margin-bottom: 0.75rem;
  color: var(--color-text-primary);
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--color-primary-light);
}

.summary-prose :deep(h2) {
  font-size: 1.125rem;
  font-weight: 700;
  margin-top: 1.5rem;
  margin-bottom: 0.75rem;
  color: var(--color-text-primary);
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--color-border-light);
}

.summary-prose :deep(h3) {
  font-size: 1rem;
  font-weight: 600;
  margin-top: 1.25rem;
  margin-bottom: 0.5rem;
  color: var(--color-text-primary);
}

.summary-prose :deep(p) {
  margin-bottom: 0.75rem;
  line-height: 1.8;
  color: var(--color-text-primary);
}

.summary-prose :deep(ul),
.summary-prose :deep(ol) {
  margin-bottom: 0.75rem;
  padding-left: 1.5rem;
}

.summary-prose :deep(li) {
  margin-bottom: 0.35rem;
  line-height: 1.8;
}

.summary-prose :deep(li::marker) {
  color: var(--color-primary);
}

.summary-prose :deep(strong) {
  color: var(--color-text-primary);
  font-weight: 600;
}

.summary-prose :deep(hr) {
  margin: 1.5rem 0;
  border-color: var(--color-border-light);
}

.summary-prose :deep(blockquote) {
  border-left: 3px solid var(--color-primary);
  padding: 0.75rem 1rem;
  color: var(--color-text-secondary);
  font-style: normal;
  margin: 1rem 0;
  background: var(--color-bg-section);
  border-radius: 0 8px 8px 0;
}

.summary-prose :deep(code) {
  background: var(--color-bg-section);
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
  font-size: 0.85em;
  color: var(--color-primary-dark);
  font-weight: 500;
}

.summary-prose :deep(pre) {
  background: #1e293b;
  color: #e2e8f0;
  border-radius: 8px;
  padding: 1rem;
  overflow-x: auto;
  margin: 1rem 0;
}

.summary-prose :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
  font-weight: normal;
}

.summary-prose :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 1rem 0;
  font-size: 0.875rem;
}

.summary-prose :deep(th) {
  background: var(--color-bg-section);
  padding: 0.5rem 0.75rem;
  text-align: left;
  font-weight: 600;
  border-bottom: 2px solid var(--color-border);
}

.summary-prose :deep(td) {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--color-border-light);
}

.summary-prose :deep(a) {
  color: var(--color-primary);
  text-decoration: none;
}

.summary-prose :deep(a:hover) {
  text-decoration: underline;
}

</style>
