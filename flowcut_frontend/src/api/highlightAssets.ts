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

export async function uploadHighlightZip(
  tenantKey: string,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<{ ok: boolean; dramaNames: string[]; created: number; assets: HighlightAsset[] }> {
  const form = new FormData()
  form.append('tenant_key', tenantKey)
  form.append('file', file)

  const { data } = await apiClient.post<{
    ok: boolean
    drama_names: string[]
    created: number
    assets: Record<string, unknown>[]
  }>('/highlight-assets/upload-zip', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
    },
  })

  return {
    ok: data.ok,
    dramaNames: data.drama_names,
    created: data.created,
    assets: data.assets.map(fromBackend),
  }
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

