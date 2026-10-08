/**
 * 页面恢复只把资源 ID 写进 URL，视频总结和聊天正文仍以服务端 SQLite 为准。
 * 第一版仍是单页面应用，因此直接使用浏览器 History API，不额外引入 Vue Router。
 */

export interface WorkspaceLocation {
  videoId: string | null
  conversationId: string | null
}

export function readWorkspaceLocation(): WorkspaceLocation {
  // URLSearchParams 专门解析 ?video=...&conversation=... 这一段查询参数。
  const params = new URLSearchParams(window.location.search)
  return {
    videoId: params.get('video'),
    conversationId: params.get('conversation'),
  }
}

export function writeWorkspaceLocation(videoId: string, conversationId?: string | null) {
  const url = new URL(window.location.href)
  url.searchParams.set('video', videoId)

  if (conversationId) url.searchParams.set('conversation', conversationId)
  else url.searchParams.delete('conversation')

  // replaceState 只修改地址栏，不会刷新页面，也不会额外增加一条“后退”记录。
  window.history.replaceState(window.history.state, '', url)
}

export function clearWorkspaceLocation() {
  const url = new URL(window.location.href)
  url.searchParams.delete('video')
  url.searchParams.delete('conversation')
  window.history.replaceState(window.history.state, '', url)
}
