import axios from 'axios'

export const apiClient = axios.create({
  baseURL: '/api',
  timeout: 30_000,
  // 浏览器自动携带后端签发的 HttpOnly 会话 Cookie。
  withCredentials: true,
})

export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (detail?.message) return detail.message
    return error.message
  }
  return error instanceof Error ? error.message : '未知错误'
}
