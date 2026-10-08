<template>
  <section id="how-to-use" class="py-16 sm:py-20 bg-white" aria-labelledby="howto-heading">
    <div class="max-w-5xl mx-auto px-4 sm:px-6">
      <div class="text-center mb-12">
        <h2 id="howto-heading" class="text-2xl sm:text-3xl font-bold text-text-primary mb-3">
          如何使用 <span class="text-primary">Video Assistant</span>
        </h2>
        <p class="text-text-secondary text-base max-w-xl mx-auto">
          只需 3 步，快速整理视频内容并继续提问
        </p>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-6 sm:gap-8">
        <article v-for="step in steps" :key="step.number" class="relative text-center">
          <div class="w-14 h-14 rounded-2xl bg-primary/10 text-primary text-2xl font-bold flex items-center justify-center mx-auto mb-5">
            {{ step.number }}
          </div>
          <h3 class="text-lg font-semibold text-text-primary mb-2">{{ step.title }}</h3>
          <p class="text-sm text-text-secondary leading-relaxed">{{ step.desc }}</p>
          <div v-if="step.number < steps.length" class="hidden md:block absolute top-7 -right-4 text-border" aria-hidden="true">
            <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 5l7 7-7 7" />
            </svg>
          </div>
        </article>
      </div>

    </div>
  </section>
</template>

<script setup lang="ts">
const steps = [
  {
    number: 1,
    title: '粘贴视频链接',
    desc: '粘贴公开视频链接，查看视频信息并开始分析。',
  },
  {
    number: 2,
    title: '获取并分析内容',
    desc: 'AI 会整理视频的主要内容、关键观点和重要结论。',
  },
  {
    number: 3,
    title: '阅读总结并提问',
    desc: '阅读视频总结并继续提问；需要多步查找时可选择深入分析。',
  },
]
</script>
