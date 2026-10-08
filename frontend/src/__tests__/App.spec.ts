import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App.vue'

interface VideoFixture {
  video_id: string
  source_url: string
  title: string
  author: string | null
  thumbnail: string | null
  duration: number | null
  platform: string
  view_count: number | null
  description: string | null
  status: string
  subtitle_source: string | null
  language: string | null
  error_message: string | null
}

const {
  ensureSessionMock,
  getVideoMock,
  parseVideoMock,
  getSavedSummaryMock,
  listConversationsMock,
  getMessagesMock,
} = vi.hoisted(() => ({
  ensureSessionMock: vi.fn<() => Promise<void>>(),
  getVideoMock: vi.fn<(videoId: string) => Promise<VideoFixture>>(),
  parseVideoMock: vi.fn<(url: string) => Promise<VideoFixture>>(),
  getSavedSummaryMock: vi.fn<(videoId: string) => Promise<Record<string, string>>>(),
  listConversationsMock: vi.fn<() => Promise<unknown[]>>(),
  getMessagesMock: vi.fn<() => Promise<unknown[]>>(),
}))

vi.mock('@/api/session', () => ({
  ensureAnonymousSession: ensureSessionMock,
}))

vi.mock('@/api/video', () => ({
  getVideo: getVideoMock,
  parseVideo: parseVideoMock,
}))

vi.mock('@/api/summarize', () => ({
  getSavedSummary: getSavedSummaryMock,
  listVideoConversations: listConversationsMock,
  getConversationMessages: getMessagesMock,
  processVideo: vi.fn<() => Promise<void>>(),
  chatWithVideo: vi.fn<() => Promise<void>>(),
}))

describe('App', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/')
    ensureSessionMock.mockReset()
    getVideoMock.mockReset()
    parseVideoMock.mockReset()
    getSavedSummaryMock.mockReset()
    listConversationsMock.mockReset()
    getMessagesMock.mockReset()
    ensureSessionMock.mockResolvedValue(undefined)
    listConversationsMock.mockResolvedValue([])
    getMessagesMock.mockResolvedValue([])
  })

  it('renders the trimmed video-assistant entry page', () => {
    const wrapper = mount(App)
    expect(wrapper.text()).toContain('视频总结与内容问答')
    expect(wrapper.text()).not.toContain('会员')
  })

  it('restores a ready video and its saved summary from URL ids', async () => {
    window.history.replaceState({}, '', '/?video=video_saved')
    getVideoMock.mockResolvedValue({
      video_id: 'video_saved',
      source_url: 'https://example.com/video',
      title: '已保存的视频',
      author: '作者',
      thumbnail: null,
      duration: 60,
      platform: 'Test',
      view_count: null,
      description: null,
      status: 'ready',
      subtitle_source: 'manual',
      language: 'zh',
      error_message: null,
    })
    getSavedSummaryMock.mockResolvedValue({
      video_id: 'video_saved',
      content: '这是数据库中的总结',
      strategy: 'direct',
      model: 'fake-model',
      updated_at: '2026-09-29T00:00:00+00:00',
    })

    const wrapper = mount(App)
    await flushPromises()

    expect(getVideoMock).toHaveBeenCalledWith('video_saved')
    expect(getSavedSummaryMock).toHaveBeenCalledWith('video_saved')
    expect(wrapper.text()).toContain('已保存的视频')
    expect(wrapper.text()).toContain('这是数据库中的总结')
    expect(wrapper.text()).not.toContain('内容依据：')
  })

  it('does not expose subtitle-source warnings when restoring an ASR summary', async () => {
    window.history.replaceState({}, '', '/?video=video_asr')
    getVideoMock.mockResolvedValue({
      video_id: 'video_asr',
      source_url: 'https://example.com/video',
      title: '语音转写视频',
      author: '作者',
      thumbnail: null,
      duration: 60,
      platform: 'Test',
      view_count: null,
      description: null,
      status: 'ready',
      subtitle_source: 'asr',
      language: 'zh',
      error_message: null,
    })
    getSavedSummaryMock.mockResolvedValue({
      video_id: 'video_asr',
      content: '> ⚠️ **信息范围说明：** 未获取到平台字幕，以下内容根据视频音频的语音识别结果生成，可能存在漏字或错字，也不包含只出现在画面中的文字和动作细节。\n\n语音识别生成的总结',
      strategy: 'direct',
      model: 'fake-model',
      updated_at: '2026-09-30T00:00:00+00:00',
    })

    const wrapper = mount(App)
    await flushPromises()

    expect(wrapper.text()).toContain('语音识别生成的总结')
    expect(wrapper.text()).not.toContain('内容依据：')
    expect(wrapper.text()).not.toContain('可能存在漏字或错字')
    expect(wrapper.text()).not.toContain('信息范围说明')
  })
})
