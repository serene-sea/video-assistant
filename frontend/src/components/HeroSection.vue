<template>
  <section
    class="relative overflow-hidden bg-gradient-to-b from-primary-light/50 to-white transition-all"
    :class="compact ? 'pt-6 pb-4 sm:pt-8 sm:pb-6' : 'pt-16 pb-12 sm:pt-24 sm:pb-16'"
  >
    <div class="absolute inset-0 overflow-hidden pointer-events-none">
      <div class="absolute -top-40 -right-40 w-96 h-96 bg-primary/5 rounded-full blur-3xl"></div>
      <div class="absolute -bottom-20 -left-20 w-72 h-72 bg-blue-400/5 rounded-full blur-3xl"></div>
    </div>

    <div class="relative max-w-4xl mx-auto px-4 sm:px-6 text-center">
      <template v-if="showSlogan">
        <h1 class="text-3xl sm:text-5xl mb-4 font-bold text-text-primary leading-tight">
          视频总结与内容问答
        </h1>
        <p class="mb-10 text-base sm:text-lg text-text-secondary max-w-2xl mx-auto leading-relaxed">
          输入公开视频链接，快速整理内容重点，并围绕视频继续提问。
        </p>
      </template>

      <form class="relative flex items-center max-w-2xl mx-auto" role="search" aria-label="视频链接解析" @submit.prevent="onSubmit">  <!-- @submit.prevent: 当表单提交时，调用 onSubmit 方法，并阻止默认行为，防止页面刷新。 -->
        <div class="relative flex-1">
          <label for="video-url-input" class="sr-only">粘贴视频网址进行解析</label>
          <svg class="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
          </svg>
          <input
            id="video-url-input"
            v-model="url"
            type="url"
            placeholder="输入公开视频链接"
            class="w-full h-14 pl-12 pr-4 rounded-full sm:rounded-r-none border border-border bg-white text-base text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all shadow-sm"
            :disabled="loading"
            autocomplete="url"
          />  <!-- 输入框绑定 url 变量，用户输入的链接会实时更新 url 的值。⭐ -->
        </div>

        <button
          type="submit"
          :disabled="loading || !url.trim()"
          class="hidden sm:flex items-center gap-2 h-14 px-8 rounded-r-full bg-primary hover:bg-primary-dark text-white font-medium text-base transition-all disabled:opacity-50 disabled:cursor-not-allowed shadow-md cursor-pointer"
        >  <!-- type="submit": 表示这是一个提交按钮 -->
          <span v-if="loading" class="w-5 h-5 border-2 border-white/40 border-t-white rounded-full animate-spin"></span>
          {{ loading ? '解析中' : '解析视频' }}
        </button>

        <button
          type="submit"
          :disabled="loading || !url.trim()"
          class="sm:hidden absolute right-2 top-1/2 -translate-y-1/2 w-9 h-9 flex items-center justify-center rounded-full bg-primary text-white disabled:opacity-50"
          aria-label="解析视频"
        >  <!-- type="submit": 表示这是一个提交按钮 -->
          →
        </button>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'  // ref 用于创建响应式数据

defineProps({
  loading: Boolean,
  compact: Boolean,
  showSlogan: { type: Boolean, default: true },
})

const emit = defineEmits<{ parse: [url: string] }>()
const url = ref('')  // 输入框绑定 url 变量，用户输入的链接会实时更新 url 的值。⭐只能用于 setup 语法糖中

function onSubmit() {
  const normalizedUrl = url.value.trim()
  if (normalizedUrl) emit('parse', normalizedUrl)
}
</script>
