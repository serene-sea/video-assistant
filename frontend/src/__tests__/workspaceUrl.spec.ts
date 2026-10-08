import { beforeEach, describe, expect, it } from 'vitest'
import {
  clearWorkspaceLocation,
  readWorkspaceLocation,
  writeWorkspaceLocation,
} from '@/workspaceUrl'

describe('workspace URL state', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/')
  })

  it('stores only video and conversation ids without reloading the page', () => {
    writeWorkspaceLocation('video_test', 'conv_test')

    expect(readWorkspaceLocation()).toEqual({
      videoId: 'video_test',
      conversationId: 'conv_test',
    })

    clearWorkspaceLocation()
    expect(window.location.search).toBe('')
  })
})
