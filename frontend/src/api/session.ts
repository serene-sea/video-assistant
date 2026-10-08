import { apiClient } from './client'

export async function ensureAnonymousSession(): Promise<void> {
  await apiClient.post('/session')
}
