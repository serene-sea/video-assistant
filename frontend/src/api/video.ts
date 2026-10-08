import { apiClient } from './client'

export interface Video {
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

export interface HistoryVideo {
  video_id: string
  title: string
  author: string | null
  thumbnail: string | null
  duration: number | null
  platform: string
  status: string
  subtitle_source: string | null
  created_at: string
}

export async function parseVideo(url: string): Promise<Video> {
  const response = await apiClient.post<Video>('/videos/parse', { url })
  return response.data
}

export async function getVideo(videoId: string): Promise<Video> {
  const response = await apiClient.get<Video>(`/videos/${videoId}`)
  return response.data
}

export async function listVideoHistory(): Promise<HistoryVideo[]> {
  const response = await apiClient.get<HistoryVideo[]>('/history/videos')
  return response.data
}
