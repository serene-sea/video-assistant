<template>
  <section class="max-w-5xl mx-auto px-4 py-8 sm:px-6" aria-labelledby="history-title">
    <div class="flex items-center justify-between gap-4 mb-6">
      <div>
        <h1 id="history-title" class="text-2xl font-bold text-text-primary">历史记录</h1>
        <p class="mt-1 text-sm text-text-secondary">你之前解析和整理过的视频</p>
      </div>
      <div class="flex items-center gap-2">
        <button class="rounded-lg border border-border px-3 py-2 text-sm text-text-secondary hover:text-primary" @click="loadHistory">刷新</button>
        <button class="rounded-lg bg-primary px-3 py-2 text-sm text-white hover:bg-primary-dark" @click="$emit('close')">返回</button>
      </div>
    </div>

    <p v-if="error" class="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700" role="alert">{{ error }}</p>
    <div v-if="loading" class="py-16 text-center text-sm text-text-muted" role="status">正在载入历史记录…</div>
    <div v-else-if="videos.length === 0" class="rounded-2xl border border-dashed border-border bg-white py-16 text-center text-sm text-text-muted">
      还没有保存的视频。解析视频后，它们会显示在这里。
    </div>
    <ul v-else class="space-y-3">
      <li v-for="video in videos" :key="video.video_id">
        <button
          type="button"
          class="flex w-full items-center gap-4 rounded-xl border border-border-light bg-white p-3 text-left shadow-sm transition hover:border-primary/40 hover:shadow"
          @click="$emit('select-video', video.video_id)"
        >
          <div class="flex h-20 w-36 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-bg-section text-xs text-text-muted">
            <img v-if="video.thumbnail && !failedThumbnails.has(video.video_id)" :src="video.thumbnail" alt="" class="h-full w-full object-cover" @error="markThumbnailFailed(video.video_id)" />
            <span v-if="!video.thumbnail || failedThumbnails.has(video.video_id)">暂无封面</span>
          </div>
          <div class="min-w-0 flex-1">
            <h2 class="line-clamp-2 text-sm font-semibold text-text-primary">{{ video.title }}</h2>
            <p class="mt-1 text-xs text-text-secondary">{{ video.author || '未知作者' }} · {{ video.platform }}</p>
            <p class="mt-1 text-xs text-text-muted">{{ formatDate(video.created_at) }} · {{ statusLabel(video.status) }}</p>
          </div>
          <span v-if="video.duration" class="hidden text-xs text-text-muted sm:block">{{ formatDuration(video.duration) }}</span>
        </button>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getErrorMessage } from '@/api/client'
import { listVideoHistory, type HistoryVideo } from '@/api/video'

defineEmits<{ close: []; 'select-video': [videoId: string] }>()
const videos = ref<HistoryVideo[]>([])
const failedThumbnails = ref(new Set<string>())
const loading = ref(false)
const error = ref('')

async function loadHistory() {
  loading.value = true
  error.value = ''
  try {
    videos.value = await listVideoHistory()
    failedThumbnails.value = new Set()
  } catch (reason) {
    error.value = `历史记录读取失败：${getErrorMessage(reason)}`
  } finally {
    loading.value = false
  }
}

function markThumbnailFailed(videoId: string) {
  failedThumbnails.value = new Set(failedThumbnails.value).add(videoId)
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    parsed: '待分析', extracting: '正在整理', transcribing: '正在整理',
    summarizing: '正在整理', ready: '已完成', failed: '处理失败',
  }
  return labels[status] ?? '已保存'
}

function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })
}

function formatDuration(value: number) {
  const seconds = Math.max(0, Math.floor(value))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

onMounted(loadHistory)
</script>
