import { apiClient } from './client'
import type { Creative, ClipPlan, ClipPlanEntry } from '../types'

export interface FailedDrama {
  drama: string
  error: string
}

export interface TaskProgress {
  stage: string           // starting | stage_a_done | stage_b_done | stage_c | stage_c_done | done
  stage_label: string     // 中文标签：开始规划 | 合并+拆镜完成 | 已选出高光起点 | ...
  progress_pct: number    // 0-100
  drama?: string          // 当前处理的剧名
  drama_count?: number    // 总剧数
  candidate_count?: number
  created_count?: number
  failed_dramas?: FailedDrama[]
  stage_a_s?: number
  stage_b_s?: number
  stage_c_s?: number
  wall_clock_s?: number
}

export interface TaskStatus {
  status: 'queued' | 'running' | 'succeeded' | 'completed' | 'failed' | 'noop' | 'wait_external'
  error: string | null
  resultUrl: string | null
  resultOssKey: string | null
  progress: TaskProgress | null
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
    result_url: string | null
    details?: Record<string, unknown>
  }>(`/flowcut/tasks/${taskId}`)
  const details = data.details ?? {}
  const progressRaw = details as Record<string, unknown>
  return {
    status: data.status,
    error: data.last_error ?? null,
    resultUrl: data.result_url ?? null,
    resultOssKey: typeof details.oss_key === 'string' ? (details.oss_key as string) : null,
    progress: progressRaw.stage != null ? {
      stage: typeof progressRaw.stage === 'string' ? progressRaw.stage : '',
      stage_label: typeof progressRaw.stage_label === 'string' ? progressRaw.stage_label : '',
      progress_pct: typeof progressRaw.progress_pct === 'number' ? progressRaw.progress_pct : 0,
      drama: typeof progressRaw.drama === 'string' ? progressRaw.drama : undefined,
      drama_count: typeof progressRaw.drama_count === 'number' ? progressRaw.drama_count : undefined,
      candidate_count: typeof progressRaw.candidate_count === 'number' ? progressRaw.candidate_count : undefined,
      created_count: typeof progressRaw.created_count === 'number' ? progressRaw.created_count : undefined,
      stage_a_s: typeof progressRaw.stage_a_s === 'number' ? progressRaw.stage_a_s : undefined,
      stage_b_s: typeof progressRaw.stage_b_s === 'number' ? progressRaw.stage_b_s : undefined,
      stage_c_s: typeof progressRaw.stage_c_s === 'number' ? progressRaw.stage_c_s : undefined,
      wall_clock_s: typeof progressRaw.wall_clock_s === 'number' ? progressRaw.wall_clock_s : undefined,
      failed_dramas: Array.isArray(progressRaw.failed_dramas)
        ? (progressRaw.failed_dramas as Array<Record<string, unknown>>).map((f) => ({
            drama: typeof f.drama === 'string' ? f.drama : '?',
            error: typeof f.error === 'string' ? f.error : '未知错误',
          }))
        : undefined,
    } : null,
  }
}

function parseClipPlan(rawValue: unknown): ClipPlan | null {
  try {
    const obj: unknown = typeof rawValue === 'string' ? JSON.parse(rawValue) : rawValue
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return null
    const o = obj as Record<string, unknown>
    const rawEntries = Array.isArray(o.entries) ? o.entries : []
    const entries: ClipPlanEntry[] = rawEntries
      .filter((e): e is Record<string, unknown> => !!e && typeof e === 'object' && !Array.isArray(e))
      .map((e) => ({
        episodeNo: typeof e.episode_no === 'number' ? e.episode_no : 0,
        cutStart: typeof e.cut_start === 'number' ? e.cut_start : 0,
        cutEnd: typeof e.cut_end === 'number' ? e.cut_end : 0,
      }))
    return {
      dramaName: typeof o.drama_name === 'string' ? o.drama_name : undefined,
      boundaryType: typeof o.boundary_type === 'string' ? o.boundary_type : undefined,
      totalDuration: typeof o.total_duration === 'number' ? o.total_duration : undefined,
      startEpisodeNo: typeof o.start_episode_no === 'number' ? o.start_episode_no : undefined,
      entries,
    }
  } catch {
    return null
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
    updatedAt: (raw.updated_at as string) ?? new Date().toISOString(),
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
    prerollAssetId: (raw.preroll_asset_id as number | null) ?? null,
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
    clipPlan: raw.clip_plan_json != null ? parseClipPlan(raw.clip_plan_json) : null,
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
  }>('/creatives', { params: { tenant_key: tenantKey, limit: 500 } })
  return (data.data ?? []).map(creativeFromBackend)
}

export interface HighlightPlanTask {
  taskId: string
  status: string
  dramaName: string | null
  numCandidates: number | null
  batchId: string | null
}

export async function listHighlightPlanTasks(
  tenantKey: string,
): Promise<HighlightPlanTask[]> {
  const { data } = await apiClient.get<{
    ok: boolean
    data: Array<Record<string, unknown>>
  }>('/creatives/highlight-plan-tasks', { params: { tenant_key: tenantKey } })
  return (data.data ?? []).map((t) => ({
    taskId: typeof t.task_id === 'string' ? t.task_id : '',
    status: typeof t.status === 'string' ? t.status : '',
    dramaName: typeof t.drama_name === 'string' ? t.drama_name : null,
    numCandidates: typeof t.num_candidates === 'number' ? t.num_candidates : null,
    batchId: typeof t.batch_id === 'string' ? t.batch_id : null,
  }))
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

export async function setCreativeConnector(
  creativeId: string | number,
  connectorAssetId: number | null,
): Promise<void> {
  await apiClient.patch(`/creatives/${creativeId}/connector`, {
    connector_asset_id: connectorAssetId,
  })
}

export async function setCreativePreroll(
  creativeId: string | number,
  prerollAssetId: number | null,
): Promise<void> {
  await apiClient.patch(`/creatives/${creativeId}/preroll`, {
    preroll_asset_id: prerollAssetId,
  })
}

export async function exportHighlightCreative(
  creativeId: string | number,
): Promise<{ taskId: string }> {
  const { data } = await apiClient.post<{
    ok: boolean
    task_id: string
  }>(`/creatives/${creativeId}/export-highlight`)
  return { taskId: data.task_id }
}

export async function deleteCreative(creativeId: string | number): Promise<void> {
  await apiClient.delete(`/creatives/${creativeId}`)
}

export async function batchDownloadZip(
  tenantKey: string,
  creativeIds: Array<string | number>,
): Promise<{ downloadUrl: string; count: number }> {
  const { data } = await apiClient.post<{
    ok: boolean
    token: string
    count: number
  }>('/creatives/batch-download-zip/prepare', {
    tenant_key: tenantKey,
    creative_ids: creativeIds,
  })
  const base = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8001') as string
  const downloadUrl = `${base}/creatives/batch-download-zip/${data.token}`
  return { downloadUrl, count: data.count }
}

export async function batchDownloadZipByKeys(
  tenantKey: string,
  items: Array<{ ossKey: string; filename: string }>,
): Promise<{ downloadUrl: string; count: number }> {
  const { data } = await apiClient.post<{
    ok: boolean
    token: string
    count: number
  }>('/creatives/batch-download-zip/prepare-by-keys', {
    tenant_key: tenantKey,
    items: items.map((it) => ({ oss_key: it.ossKey, filename: it.filename })),
  })
  const base = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8001') as string
  const downloadUrl = `${base}/creatives/batch-download-zip/${data.token}`
  return { downloadUrl, count: data.count }
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
