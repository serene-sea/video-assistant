import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import VideoChat from '@/components/VideoChat.vue'

type ChatWithVideo = (
  videoId: string,
  question: string,
  handlers: Record<string, (value: unknown) => void>,
  options: { conversationId: string | null; mode: 'summary' | 'rag' | 'deep' },
) => Promise<void>

type ConversationItem = {
  conversation_id: string
  preview: string
  created_at: string
  updated_at: string
}

type SavedMessage = {
  message_id: number
  role: 'user' | 'assistant'
  content: string
  citations: { chunk_id: string; start: number; end: number }[]
  created_at: string
}

const { chatWithVideoMock, listConversationsMock, getMessagesMock } = vi.hoisted(() => ({
  chatWithVideoMock: vi.fn<ChatWithVideo>(),
  listConversationsMock: vi.fn<(videoId: string) => Promise<ConversationItem[]>>(),
  getMessagesMock: vi.fn<
    (videoId: string, conversationId: string) => Promise<SavedMessage[]>
  >(),
}))

vi.mock('@/api/summarize', () => ({
  chatWithVideo: chatWithVideoMock,
  listVideoConversations: listConversationsMock,
  getConversationMessages: getMessagesMock,
}))

describe('VideoChat', () => {
  beforeEach(() => {
    chatWithVideoMock.mockReset()
    listConversationsMock.mockReset()
    getMessagesMock.mockReset()
    listConversationsMock.mockResolvedValue([])
    getMessagesMock.mockResolvedValue([])
    window.history.replaceState({}, '', '/')
    chatWithVideoMock.mockImplementation(
      async (_videoId: string, _question: string, handlers: Record<string, (value: unknown) => void>) => {
        handlers.conversation?.({ conversation_id: 'conv_test' })
        handlers.answer_delta?.({ text: '流式回答' })
        handlers.done?.({ conversation_id: 'conv_test' })
      },
    )
  })

  it('shows streamed text and reuses the returned conversation id', async () => {
    const wrapper = mount(VideoChat, { props: { videoId: 'video_test' } })
    const input = wrapper.get('input')

    await input.setValue('第一个问题')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('第一个问题')
    expect(wrapper.text()).toContain('流式回答')
    expect(chatWithVideoMock.mock.calls[0]?.[3]).toEqual({
      conversationId: null,
      mode: 'summary',
    })

    await input.setValue('追问')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(chatWithVideoMock.mock.calls[1]?.[3]).toEqual({
      conversationId: 'conv_test',
      mode: 'summary',
    })
    expect(window.location.search).toContain('conversation=conv_test')
  })

  it('restores saved messages from the conversation in the URL', async () => {
    listConversationsMock.mockResolvedValue([
      {
        conversation_id: 'conv_saved',
        preview: '历史问题',
        created_at: '2026-09-29T00:00:00+00:00',
        updated_at: '2026-09-29T00:01:00+00:00',
      },
    ])
    getMessagesMock.mockResolvedValue([
      {
        message_id: 1,
        role: 'user',
        content: '这个动作出现在什么时间？',
        citations: [],
        created_at: '2026-09-29T00:00:00+00:00',
      },
      {
        message_id: 2,
        role: 'assistant',
        content: '历史回答',
        citations: [{ chunk_id: 'video_test:rag:0', start: 65, end: 82 }],
        created_at: '2026-09-29T00:01:00+00:00',
      },
    ])

    const wrapper = mount(VideoChat, {
      props: {
        videoId: 'video_test',
        initialConversationId: 'conv_saved',
      },
    })
    await flushPromises()

    expect(getMessagesMock).toHaveBeenCalledWith('video_test', 'conv_saved')
    expect(wrapper.text()).toContain('历史问题')
    expect(wrapper.text()).toContain('历史回答')
    expect(wrapper.text()).toContain('[01:05–01:22]')
    expect(window.location.search).toContain('conversation=conv_saved')
  })

  it('offers summary and deep modes without exposing RAG as a separate UI mode', async () => {
    const wrapper = mount(VideoChat, { props: { videoId: 'video_test' } })
    const modes = wrapper.find('[aria-label="问答模式"]').findAll('button')

    expect(modes).toHaveLength(2)
    expect(wrapper.text()).toContain('总结问答')
    expect(wrapper.text()).toContain('深入分析')
    expect(wrapper.text()).not.toContain('字幕检索')
    expect(chatWithVideoMock).not.toHaveBeenCalled()
  })

  it('answer_reset replaces streamed text and clears provisional citations', async () => {
    chatWithVideoMock.mockImplementationOnce(
      async (_videoId, _question, handlers) => {
        handlers.answer_delta?.({ text: '临时回答' })
        handlers.citations?.([
          { chunk_id: 'video_test:rag:0', start: 1, end: 2 },
        ])
        handlers.answer_reset?.({ text: '根据当前检索到的字幕内容无法可靠回答这个问题。' })
        handlers.citations?.([])
        handlers.done?.({})
      },
    )
    const wrapper = mount(VideoChat, { props: { videoId: 'video_test' } })
    await wrapper.get('button').trigger('click')
    await wrapper.get('input').setValue('问题')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).not.toContain('临时回答')
    expect(wrapper.text()).toContain('根据当前检索到的字幕内容无法可靠回答这个问题。')
    expect(wrapper.text()).not.toContain('[00:01–00:02]')
  })

  it('shows the ReAct Agent tool trace and uses deep mode', async () => {
    let finishToolSearch: (() => void) | undefined
    chatWithVideoMock.mockImplementationOnce(
      async (_videoId, _question, handlers) => {
        handlers.stage?.({ stage: 'agent_tools', message: '正在执行第 1 轮工具查找' })
        handlers.tool_trace?.({
          round: 1,
          tool: 'search_transcript',
          status: 'started',
          message: '正在读取当前视频资料',
        })
        handlers.tool_trace?.({
          round: 1,
          tool: 'search_transcript',
          status: 'completed',
          message: '找到 1 条字幕片段',
        })
        await new Promise<void>((resolve) => {
          finishToolSearch = resolve
        })
        handlers.answer_delta?.({ text: '答案' })
        handlers.citations?.([
          { chunk_id: 'video_test:segment:0', start: 2, end: 4 },
        ])
        handlers.done?.({})
      },
    )
    const wrapper = mount(VideoChat, { props: { videoId: 'video_test' } })
    const agentModeButton = wrapper
      .find('[aria-label="问答模式"]')
      .findAll('button')[1]
    expect(agentModeButton).toBeDefined()
    await agentModeButton!.trigger('click')
    await wrapper.get('input').setValue('讲了什么？')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(chatWithVideoMock.mock.calls[0]?.[3]?.mode).toBe('deep')
    expect(wrapper.text()).toContain('深入分析')
    expect(wrapper.text()).toContain('查找相关内容')
    expect(wrapper.text()).toContain('找到 1 条字幕片段')
    finishToolSearch?.()
    await flushPromises()
    expect(wrapper.text()).toContain('答案')
    expect(wrapper.text()).not.toContain('找到 1 条字幕片段')
    expect(wrapper.text()).not.toContain('[[1]]')
    expect(wrapper.text()).not.toContain('[00:02–00:04]')
  })
})
