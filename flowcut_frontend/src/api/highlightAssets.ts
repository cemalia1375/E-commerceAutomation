import { apiClient } from './client'
import type { HighlightAsset, HighlightAssetType } from '../types'

function fromBackend(raw: Record<string, unknown>): HighlightAsset {
  return {
    id: raw.id as number,
    tenantKey: raw.tenant_key as string,
    assetType: raw.asset_type as HighlightAssetType,
    dramaName: (raw.drama_name as string | null) ?? undefined,
    episodeNo: (raw.episode_no as number | null) ?? undefined,
    connectorRole: (raw.connector_role as string | null) ?? undefined,
    ossKey: raw.oss_key as string,
    ossUrl: raw.oss_url as string,
    name: raw.name as string,
    duration: (raw.duration as number) ?? 0,
    fileSize: (raw.file_size as number) ?? 0,
    status: raw.status as HighlightAsset['status'],
    createdAt: raw.created_at as string,
  }
}

export async function listHighlightAssets(
  tenantKey: string,
  filters?: {
    assetType?: HighlightAssetType
    dramaName?: string
    connectorRole?: string
  },
): Promise<HighlightAsset[]> {
  const { data } = await apiClient.get<Record<string, unknown>[]>('/highlight-assets', {
    params: {
      tenant_key: tenantKey,
      asset_type: filters?.assetType,
      drama_name: filters?.dramaName,
      connector_role: filters?.connectorRole,
    },
  })
  return data.map(fromBackend)
}

export async function uploadHighlightAsset(
  tenantKey: string,
  file: File,
  options: {
    assetType: HighlightAssetType
    dramaName?: string
    episodeNo?: number
    connectorRole?: string
  },
  onProgress?: (percent: number) => void,
): Promise<HighlightAsset> {
  const form = new FormData()
  form.append('tenant_key', tenantKey)
  form.append('file', file)
  form.append('asset_type', options.assetType)
  if (options.dramaName) form.append('drama_name', options.dramaName)
  if (options.episodeNo !== undefined) form.append('episode_no', String(options.episodeNo))
  if (options.connectorRole) form.append('connector_role', options.connectorRole)

  const { data } = await apiClient.post<Record<string, unknown>>(
    '/highlight-assets/upload',
    form,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
      },
    },
  )
  return fromBackend(data)
}

export async function deleteHighlightAsset(assetId: number): Promise<void> {
  await apiClient.delete(`/highlight-assets/${assetId}`)
}

export async function deleteHighlightAssets(
  tenantKey: string,
  assetIds: number[],
): Promise<{ deleted: number; skipped: number[]; errors: string[] }> {
  const { data } = await apiClient.post<{
    ok: true
    deleted: number
    skipped: number[]
    errors: string[]
  }>('/highlight-assets/batch-delete', {
    tenant_key: tenantKey,
    asset_ids: assetIds,
  })
  return {
    deleted: data.deleted,
    skipped: data.skipped,
    errors: data.errors,
  }
}

export async function runHighlightAssetBatch(
  tenantKey: string,
  options: {
    dramaName: string
    mode: 'highlight_original' | 'highlight_digital_human'
    connectorAssetId?: number
    connectorQuery?: string
    limit?: number
  },
): Promise<{
  batchId: string
  createdCount: number
}> {
  const { data } = await apiClient.post<{
    ok: boolean
    data: {
      batch_id: string
      created_count: number
    }
  }>('/highlight-assets/batch-run', {
    tenant_key: tenantKey,
    drama_name: options.dramaName,
    mode: options.mode,
    connector_asset_id: options.connectorAssetId,
    connector_query: options.connectorQuery,
    limit: options.limit ?? 200,
  })
  return {
    batchId: data.data.batch_id,
    createdCount: data.data.created_count,
  }
}
