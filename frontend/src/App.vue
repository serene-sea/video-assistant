<template>
  <div class="min-h-screen flex flex-col bg-bg-main">
    <AppHeader @show-history="historyOpen = true" />
    <VideoHistory
      v-if="historyOpen"
      @close="historyOpen = false"
      @select-video="openHistoryVideo"
    />
    <main v-show="!historyOpen" class="flex-1">
      <HeroSection
        :loading="loading"
        :compact="Boolean(videoData)"
        :show-slogan="!videoData"
        @parse="handleParse"
      />  <!-- emit 的 parse -->

      <p v-if="pageError" class="max-w-4xl mx-auto mt-4 px-4 text-sm text-red-600" role="alert">
        {{ pageError }}
      </p>

      <section v-if="videoData" class="py-4 sm:py-6 bg-white">
        <div class="max-w-7xl mx-auto px-4 sm:px-6">
          <div class="flex flex-col lg:flex-row gap-6">
            <div class="w-full lg:w-2/5 lg:flex-shrink-0">
              <VideoResult
                :video="videoData"
                :summarizing="summarizing"
                @summarize="startAnalysis"
              />
            </div>

            <div class="w-full lg:w-3/5 min-w-0">
              <VideoSummary
                v-if="analysisStarted"
                :key="summaryKey"
                :video-id="videoData.video_id"
                :initial-summary="restoredSummary"
                :initial-conversation-id="restoredConversationId"
                @loading-change="summarizing = $event"
              />  <!-- 将 emit 的第一个参数赋值给summarizing -->
              <div
                v-else
                class="h-full min-h-[360px] rounded-2xl border border-dashed border-border bg-bg-section flex items-center justify-center px-8 text-center"
              >
                <div>
                  <p class="text-lg font-semibold text-text-primary mb-2">视频已解析</p>
                  <p class="text-sm text-text-secondary">点击左侧“开始 AI 分析”，整理视频内容重点。</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <FeatureSection />
      <HowToSection />
    </main>
    <AppFooter v-show="!historyOpen" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import AppFooter from '@/components/AppFooter.vue'
import AppHeader from '@/components/AppHeader.vue'
import FeatureSection from '@/components/FeatureSection.vue'
import HeroSection from '@/components/HeroSection.vue'
import HowToSection from '@/components/HowToSection.vue'
import VideoHistory from '@/components/VideoHistory.vue'
import VideoResult from '@/components/VideoResult.vue'
import VideoSummary from '@/components/VideoSummary.vue'
import { getErrorMessage } from '@/api/client'
import { ensureAnonymousSession } from '@/api/session'
import { getSavedSummary } from '@/api/summarize'
import { getVideo, parseVideo, type Video } from '@/api/video'
import {
  clearWorkspaceLocation,
  readWorkspaceLocation,
  writeWorkspaceLocation,
} from '@/workspaceUrl'

const loading = ref(false)
const historyOpen = ref(false)
const videoData = ref<Video | null>(null)
const analysisStarted = ref(false)
const summarizing = ref(false)
const summaryKey = ref(0)
const pageError = ref('')
// 这两个值只用于把数据库中恢复的内容交给新创建的子组件。
const restoredSummary = ref('')
const restoredConversationId = ref<string | null>(null)

onMounted(async () => {
  try {
    // 后端用 HttpOnly Cookie 识别匿名用户，前端只负责建立会话，不读取令牌。
    await ensureAnonymousSession()
    await restoreWorkspaceFromUrl()
  } catch (error) {
    pageError.value = `初始化会话失败：${getErrorMessage(error)}`
  }
})

async function restoreWorkspaceFromUrl() {
  const { videoId, conversationId } = readWorkspaceLocation()
  if (!videoId) return

  loading.value = true
  pageError.value = ''
  try {
    // URL 只保存 ID，真正的视频数据仍通过带 Session Cookie 的 API 查询。
    const video = await getVideo(videoId)
    videoData.value = video

    if (video.status === 'ready') {
      // 已完成的视频直接读取 SQLite 中的总结，不能重新触发 LLM。
      const summary = await getSavedSummary(videoId)
      restoredSummary.value = summary.content
      restoredConversationId.value = conversationId
      analysisStarted.value = true
    } else if (isProcessing(video.status)) {
      // 页面刷新后重新订阅服务端正在运行的任务，不重新启动 LLM。
      restoredSummary.value = ''
      restoredConversationId.value = null
      analysisStarted.value = true
      writeWorkspaceLocation(videoId)
    } else {
      // 尚未开始或上次失败时恢复元数据，由用户决定是否重新处理。
      restoredSummary.value = ''
      restoredConversationId.value = null
      analysisStarted.value = false
      writeWorkspaceLocation(videoId)
    }
  } catch {
    // Cookie 过期或资源被删除时，URL 中的旧 ID 已经无法恢复，应及时清除。
    clearWorkspaceLocation()
    videoData.value = null
    analysisStarted.value = false
    restoredSummary.value = ''
    restoredConversationId.value = null
    pageError.value = '历史记录已失效，请重新提交视频链接'
  } finally {
    loading.value = false
  }
}

async function handleParse(url: string) {
  loading.value = true
  pageError.value = ''
  videoData.value = null
  analysisStarted.value = false
  summarizing.value = false
  restoredSummary.value = ''
  restoredConversationId.value = null
  // 用户开始解析新视频时，旧页面 ID 不应继续留在地址栏。
  clearWorkspaceLocation()

  try {
    // 后续总结与问答均只传 video_id，不再反复传 URL 或整段字幕。
    videoData.value = await parseVideo(url)
    writeWorkspaceLocation(videoData.value.video_id)
    if (videoData.value.status === 'ready') {
      // 相同视频已有保存结果时直接打开，不要求用户再次调用总结模型。
      const summary = await getSavedSummary(videoData.value.video_id)
      restoredSummary.value = summary.content
      analysisStarted.value = true
    } else if (isProcessing(videoData.value.status)) {
      restoredSummary.value = ''
      analysisStarted.value = true
    }
  } catch {
    // 对用户统一使用简单提示；具体错误仍由开发阶段的后端日志负责定位。
    pageError.value = '视频信息获取失败'
  } finally {
    loading.value = false
  }
}

async function openHistoryVideo(videoId: string) {
  historyOpen.value = false
  loading.value = true
  pageError.value = ''
  try {
    const video = await getVideo(videoId)
    videoData.value = video
    restoredConversationId.value = null
    writeWorkspaceLocation(videoId)
    if (video.status === 'ready') {
      const summary = await getSavedSummary(videoId)
      restoredSummary.value = summary.content
      analysisStarted.value = true
    } else if (isProcessing(video.status)) {
      restoredSummary.value = ''
      analysisStarted.value = true
    } else {
      restoredSummary.value = ''
      analysisStarted.value = false
    }
    summaryKey.value += 1
  } catch {
    pageError.value = '这条历史记录暂时无法打开，请刷新后重试'
  } finally {
    loading.value = false
  }
}

function isProcessing(status: string) {
  return ['extracting', 'transcribing', 'summarizing'].includes(status)
}

function startAnalysis() {
  restoredSummary.value = ''
  restoredConversationId.value = null
  if (videoData.value) writeWorkspaceLocation(videoData.value.video_id)
  analysisStarted.value = true
  summaryKey.value += 1
}
</script>
