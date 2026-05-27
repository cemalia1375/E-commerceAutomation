import { apiClient } from './client'
import type { VideoScene, VideoSegment, ReferenceVideoStatus } from '../types'

export interface UploadReferenceVideoResult {
  ref_video_id: number
  script_id: number
  task_id: string
  oss_key: string
  product: string | null
  status: string
  message?: string
}

export interface ReferenceVideo {
  id: number
  tenant_key: string
  oss_key: string
  oss_url: string
  name: string
  product: string | null
  duration: number
  file_size: number
  scene_data_json: VideoSegment[] | null
  status: ReferenceVideoStatus
  script_id: number | null
  created_at: string
}

function parseSceneData(raw: unknown): VideoSegment[] | null {
  if (!raw) return null
  const arr = typeof raw === 'string' ? JSON.parse(raw) : raw
  if (!Array.isArray(arr)) return null
  return (arr as Record<string, unknown>[]).map((s) => ({
    startTime: s.start_time as number,
    endTime: s.end_time as number,
    content: s.content as string,
    category: (s.category as VideoScene['category']) || '产品展示',
    sceneRole: s.scene_role as string | undefined,
  }))
}

function fromBackend(raw: Record<string, unknown>): ReferenceVideo {
  return {
    id: raw.id as number,
    tenant_key: raw.tenant_key as string,
    oss_key: raw.oss_key as string,
    oss_url: raw.oss_url as string,
    name: raw.name as string,
    product: (raw.product as string) ?? null,
    duration: (raw.duration as number) ?? 0,
    file_size: (raw.file_size as number) ?? 0,
    scene_data_json: parseSceneData(raw.scene_data_json),
    status: (raw.status as ReferenceVideoStatus) ?? 'PROCESSING',
    script_id: (raw.script_id as number | null) ?? null,
    created_at: raw.created_at as string,
  }
}

export async function uploadReferenceVideo(
  tenantKey: string,
  file: File,
  product?: string,
  onProgress?: (percent: number) => void,
): Promise<UploadReferenceVideoResult> {
  const form = new FormData()
  form.append('tenant_key', tenantKey)
  form.append('file', file)
  if (product) form.append('product', product)

  const { data } = await apiClient.post<UploadReferenceVideoResult>(
    '/reference-videos/upload',
    form,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
      },
    },
  )
  return data
}

export async function getReferenceVideo(refVideoId: number): Promise<ReferenceVideo> {
  const { data } = await apiClient.get<Record<string, unknown>>(`/reference-videos/${refVideoId}`)
  return fromBackend(data)
}

export async function pollReferenceVideo(
  refVideoId: number,
  condition: (rv: ReferenceVideo) => boolean,
  intervalMs = 2500,
  timeoutMs = 300_000,
): Promise<ReferenceVideo> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const rv = await getReferenceVideo(refVideoId)
    if (condition(rv)) return rv
    if (rv.status === 'FAILED') throw new Error(`处理失败（ref_video_id=${refVideoId}）`)
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error('等待超时（5 分钟）')
}

