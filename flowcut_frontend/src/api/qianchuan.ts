import { apiClient } from './client'
import type { Creative } from '../types'

export interface TaskStatus {
  status: 'queued' | 'running' | 'succeeded' | 'completed' | 'failed' | 'noop' | 'wait_external'
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
    (raw.source_asset_name as string | undefined) ||
    (raw.ref_video_name as string | undefined) ||
    ossKey.split('/').slice(-1)[0] ||
    `creative-${raw.id}`
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
    creativeType: (raw.creative_type as Creative['creativeType']) ?? 'normal',
    batchId: (raw.batch_id as string | null) ?? null,
    sourceAssetId: (raw.source_asset_id as number | null) ?? null,
    connectorAssetId: (raw.connector_asset_id as number | null) ?? null,
    sourceAssetName: (raw.source_asset_name as string | null) ?? null,
    sourceDramaName: (raw.source_drama_name as string | null) ?? null,
    sourceEpisodeNo: (raw.source_episode_no as number | null) ?? null,
    sourceAssetOssUrl: (raw.source_asset_oss_url as string | null) ?? null,
    connectorAssetName: (raw.connector_asset_name as string | null) ?? null,
    connectorRole: (raw.connector_role as string | null) ?? null,
    connectorAssetOssUrl: (raw.connector_asset_oss_url as string | null) ?? null,
    highlightStart: (raw.highlight_start as number | null) ?? null,
    highlightEnd: (raw.highlight_end as number | null) ?? null,
    highlightReason: (raw.highlight_reason_json as Record<string, unknown> | null) ?? null,
    composePlan: (raw.compose_plan_json as Record<string, unknown> | null) ?? null,
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

export async function getHighlightCreativeByScript(
  tenantKey: string,
  scriptId: number,
): Promise<Creative | null> {
  const { data } = await apiClient.get<{
    ok: boolean
    data: Record<string, unknown> | null
  }>(`/creatives/highlight-by-script/${scriptId}`, {
    params: { tenant_key: tenantKey },
  })
  return data.data ? creativeFromBackend(data.data) : null
}

export async function composeHighlightCreative(
  creativeId: string | number,
): Promise<{ taskId: string }> {
  const { data } = await apiClient.post<{
    ok: boolean
    task_id: string
  }>(`/creatives/${creativeId}/compose-highlight`)
  return { taskId: data.task_id }
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
