<template>
  <section id="features" class="py-16 sm:py-20 bg-bg-section" aria-labelledby="features-heading">
    <div class="max-w-6xl mx-auto px-4 sm:px-6">
      <div class="text-center mb-12">
        <h2 id="features-heading" class="text-2xl sm:text-3xl font-bold text-text-primary mb-3">
          为什么使用 <span class="text-primary">Video Assistant</span>
        </h2>
        <p class="text-text-secondary text-base max-w-xl mx-auto">
          从视频内容获取到总结和问答，帮助你更高效地理解视频
        </p>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
        <article
          v-for="feature in features"
          :key="feature.id"
          class="group bg-white rounded-2xl p-6 border border-border-light shadow-sm hover:shadow-md hover:border-primary/20 transition-all"
        >
          <div class="w-12 h-12 rounded-xl flex items-center justify-center mb-4 text-2xl"
            :class="feature.bgClass" role="img" :aria-label="feature.title">
            {{ feature.icon }}
          </div>
          <h3 class="text-base font-semibold text-text-primary mb-2">{{ feature.title }}</h3>
          <p class="text-sm text-text-secondary leading-relaxed">{{ feature.desc }}</p>
        </article>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
const features = [
  {
    id: 'content',
    icon: '🎬',
    title: '视频内容获取',
    desc: '输入公开视频链接，快速获取视频内容',
    bgClass: 'bg-blue-50',
  },
  {
    id: 'summary',
    icon: '📝',
    title: '智能视频总结',
    desc: '梳理视频的主要内容、关键观点和重要结论',
    bgClass: 'bg-amber-50',
  },
  {
    id: 'persistence',
    icon: '💾',
    title: '随时继续',
    desc: '视频总结和对话会自动保存，之后可以继续查看',
    bgClass: 'bg-green-50',
  },
  {
    id: 'chat',
    icon: '💬',
    title: '围绕视频追问',
    desc: '围绕视频内容连续提问，AI 会结合前面的交流理解你的追问',
    bgClass: 'bg-purple-50',
  },
]
</script>
