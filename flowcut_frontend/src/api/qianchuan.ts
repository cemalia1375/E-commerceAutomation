import { apiClient } from './client'
import type { Creative } from '../types'

export interface TaskStatus {
  status: 'queued' | 'running' | 'completed' | 'failed'
  error: string | null
}

export interface AccountSummary {
  creativeCount: number
  totalCost: number
  totalImpressions: number
  totalClicks: number
  totalConversions: number
  lastSyncedAt: string | null
}

export async function triggerSync(): Promise<{ task_id: string }> {
  const { data } = await apiClient.post<{ task_id: string }>(
    '/qianchuan/sync', {},
  )
  return data
}

export async function getTaskStatus(taskId: string): Promise<TaskStatus> {
  const { data } = await apiClient.get<{
    ok: boolean
    status: TaskStatus['status']
    last_error: string | null
  }>(`/flowcut/tasks/${taskId}`)
  return {
    status: data.status,
    error: data.last_error ?? null,
  }
}

export function creativeFromBackend(raw: Record<string, unknown>): Creative {
  const ossKey = (raw.oss_key as string) ?? ''
  // 千川导入的 fc_creative.oss_key 形如 "qianchuan/视频名.mp4"
  // → name 取最后一段作为显示名；否则取 ossKey 最后一段（兼容老数据）
  const derivedName =
    ossKey.split('/').slice(-1)[0] || `creative-${raw.id}`
  return {
    id: String(raw.id),
    ossKey,
    ossUrl: (raw.oss_url as string) ?? '',
    thumbnailUrl: (raw.thumbnail_url as string | null) ?? undefined,
    name: (raw.name as string) ?? derivedName,
    duration: (raw.duration as number) ?? 0,
    status: (raw.status as Creative['status']) ?? 'ACTIVE',
    srtUrl: (raw.srt_url as string | null) ?? undefined,
    srtLineCount: (raw.srt_line_count as number | null) ?? undefined,
    createdAt: (raw.created_at as string) ?? new Date().toISOString(),
    qcMaterialId: (raw.qc_material_id as string | null) ?? null,
    qcCost: (raw.qc_cost as number | null) ?? null,
    qcImpressions: (raw.qc_impressions as number | null) ?? null,
    qcClicks: (raw.qc_clicks as number | null) ?? null,
    qcConversions: (raw.qc_conversions as number | null) ?? null,
    qcSyncedAt: (raw.qc_synced_at as string | null) ?? null,
  }
}

export async function uploadCreative(
  tenantKey: string,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<Creative> {
  const form = new FormData()
  form.append('tenant_key', tenantKey)
  form.append('file', file)
  const { data } = await apiClient.post<{
    ok: boolean
    data: Record<string, unknown>
  }>('/creatives/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
    },
  })
  return creativeFromBackend(data.data)
}

export async function listCreatives(tenantKey: string): Promise<Creative[]> {
  const { data } = await apiClient.get<{
    ok: boolean
    data: Record<string, unknown>[]
  }>('/creatives', { params: { tenant_key: tenantKey, limit: 100 } })
  return (data.data ?? []).map(creativeFromBackend)
}

export async function fetchAccountSummary(
  tenantKey: string,
): Promise<AccountSummary> {
  const { data } = await apiClient.get<{
    ok: boolean
    data: {
      creative_count: number
      total_cost: number
      total_impressions: number
      total_clicks: number
      total_conversions: number
      last_synced_at: string | null
    }
  }>('/qianchuan/account-summary', { params: { tenant_key: tenantKey } })
  const d = data.data
  return {
    creativeCount: d.creative_count,
    totalCost: d.total_cost,
    totalImpressions: d.total_impressions,
    totalClicks: d.total_clicks,
    totalConversions: d.total_conversions,
    lastSyncedAt: d.last_synced_at,
  }
}
