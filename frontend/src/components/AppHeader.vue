<template>
  <header class="sticky top-0 z-50 bg-white/80 backdrop-blur-md border-b border-border-light">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <a href="/" class="flex items-center gap-3" title="AI 视频总结与问答">
        <div class="w-9 h-9 rounded-xl bg-gradient-to-br from-primary to-blue-600 flex items-center justify-center shadow-sm" role="img" aria-label="Video Assistant Logo">
          <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>
        <span class="text-lg font-semibold text-text-primary tracking-tight">Video Assistant</span>
        <span class="hidden sm:inline text-xs text-text-muted bg-primary-light px-2 py-0.5 rounded-full">AI 视频总结与问答</span>
      </a>
      <div class="flex items-center gap-4">
        <nav class="hidden md:flex items-center gap-6 text-sm text-text-secondary" aria-label="主导航">
          <a href="#features" class="hover:text-primary transition-colors" title="查看功能特性">功能特性</a>
          <a href="#how-to-use" class="hover:text-primary transition-colors" title="了解如何使用">使用教程</a>
        </nav>
        <button
          type="button"
          class="rounded-lg border border-border px-3 py-2 text-sm text-primary hover:bg-primary/5"
          @click="$emit('show-history')"
        >历史记录</button>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
defineEmits<{ 'show-history': [] }>()
</script>
