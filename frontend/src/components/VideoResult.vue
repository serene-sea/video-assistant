<template>
  <div class="bg-white rounded-2xl border border-border shadow-lg overflow-hidden h-full">
    <div class="flex flex-col gap-5 p-5 sm:p-6">
      <div class="relative w-full aspect-video rounded-xl overflow-hidden bg-gray-100">
        <img
          v-if="video.thumbnail && !coverFailed"
          :src="video.thumbnail"
          :alt="video.title"
          class="w-full h-full object-cover"
          @error="coverFailed = true"
        />
        <div v-else class="w-full h-full flex items-center justify-center text-text-muted">封面暂不可用</div>
        <span v-if="video.duration" class="absolute bottom-2 right-2 px-2 py-0.5 bg-black/70 text-white text-xs rounded-md">
          {{ formatDuration(video.duration) }}
        </span>
      </div>

      <div>
        <h2 class="text-lg font-semibold text-text-primary leading-snug mb-2 line-clamp-2">{{ video.title }}</h2>
        <div class="flex flex-wrap items-center gap-3 text-sm text-text-secondary mb-3">
          <span>{{ video.author || '未知作者' }}</span>
          <span class="px-2 py-0.5 bg-primary-light text-primary rounded-full text-xs font-medium">{{ video.platform }}</span>
          <span v-if="video.view_count">{{ formatViewCount(video.view_count) }} 次观看</span>
        </div>
        <p v-if="video.description" class="text-sm text-text-muted line-clamp-2">{{ video.description }}</p>
      </div>
    </div>

    <div class="border-t border-border-light px-5 sm:px-6 py-5">
      <button
        class="w-full h-12 rounded-full border-2 border-primary text-primary hover:bg-primary hover:text-white font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed"
        :disabled="summarizing"
        @click="$emit('summarize')"
      >
        {{ summarizing ? 'AI 分析中...' : '开始 AI 分析' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { Video } from '@/api/video'

const props = defineProps<{ video: Video; summarizing?: boolean }>()
defineEmits<{ summarize: [] }>()
const coverFailed = ref(false)

// 解析新视频时清除上一张封面的失败状态，再尝试加载新地址。
watch(() => props.video.thumbnail, () => { coverFailed.value = false })

function formatDuration(totalSeconds: number) {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const rest = seconds % 60
  return hours > 0
    ? [hours, minutes, rest].map((part) => String(part).padStart(2, '0')).join(':')
    : [minutes, rest].map((part) => String(part).padStart(2, '0')).join(':')
}

function formatViewCount(count: number) {
  if (count >= 100_000_000) return `${(count / 100_000_000).toFixed(1)}亿`
  if (count >= 10_000) return `${(count / 10_000).toFixed(1)}万`
  return count.toLocaleString('zh-CN')
}
</script>
