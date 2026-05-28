import { apiClient } from './client'
import type {
  Material,
  VideoScene,
  ZipPreviewItem,
  ZipUploadResponse,
  ZipOverride,
} from '../types'

function parseSceneData(raw: unknown): VideoScene[] | undefined {
  if (!raw) return undefined
  const arr = typeof raw === 'string' ? JSON.parse(raw) : raw
  if (!Array.isArray(arr)) return undefined
  return (arr as Record<string, unknown>[]).map((s) => ({
    startTime: s.start_time as number,
    endTime: s.end_time as number,
    content: s.content as string,
    category: (s.category as VideoScene['category']) ?? '产品展示',
  }))
}

function fromBackend(raw: Record<string, unknown>): Material {
  const fileType = raw.category as string
  const type: Material['type'] =
    fileType === 'image' ? 'image' : fileType === 'audio' ? 'audio' : 'video'
  return {
    id: String(raw.id),
    ossKey: raw.oss_key as string,
    ossUrl: raw.oss_url as string,
    thumbnailUrl: (raw.thumbnail_url as string | null) ?? undefined,
    previewUrl: (raw.preview_url as string | null) ?? undefined,
    name: raw.name as string,
    transcript: (raw.transcript as string | null) ?? undefined,
    sceneData: parseSceneData(raw.scene_data_json),
    category: (raw.category as Material['category']) || '产品展示',
    product: (raw.product as string | null) ?? undefined,
    sceneRole: (raw.scene_role as string | null) ?? undefined,
    duration: (raw.duration as number) ?? 0,
    fileSize: (raw.file_size as number) ?? 0,
    status: raw.status as Material['status'],
    usageCount: (raw.usage_count as number) ?? 0,
    createdAt: raw.created_at as string,
    type,
  }
}

export async function listMaterials(
  tenantKey: string,
  filters?: { product?: string; sceneRole?: string },
): Promise<Material[]> {
  const { data } = await apiClient.get<Record<string, unknown>[]>('/materials', {
    params: {
      tenant_key: tenantKey,
      product: filters?.product,
      scene_role: filters?.sceneRole,
    },
  })
  return data.map(fromBackend)
}

export async function getMaterial(materialId: number): Promise<Material> {
  const { data } = await apiClient.get<Record<string, unknown>>(`/materials/${materialId}`)
  return fromBackend(data)
}

export async function uploadMaterial(
  tenantKey: string,
  file: File,
  product: string,
  sceneRole?: string,
  onProgress?: (percent: number) => void,
): Promise<{ material_id: number; oss_key: string }> {
  const form = new FormData()
  form.append('tenant_key', tenantKey)
  form.append('file', file)
  form.append('product', product)
  if (sceneRole) form.append('scene_role', sceneRole)

  const { data } = await apiClient.post<{ material_id: number; oss_key: string }>(
    '/materials/upload',
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

export async function processMaterial(materialId: number) {
  const { data } = await apiClient.post<{
    material_id: number
    task_id: string
    status: string
  }>(`/materials/${materialId}/process`)
  return data
}

export async function triggerDecompose(materialId: number) {
  const { data } = await apiClient.post<{
    material_id: number
    task_id: string
    status: string
  }>(`/materials/${materialId}/decompose`)
  return data
}

export async function deleteMaterial(materialId: string) {
  await apiClient.delete(`/materials/${materialId}`)
}

export interface UpdateMaterialPatch {
  name?: string
  product?: string | null
  scene_role?: string | null
}

export async function updateMaterial(
  materialId: number | string,
  patch: UpdateMaterialPatch,
): Promise<Material> {
  const { data } = await apiClient.patch<Record<string, unknown>>(
    `/materials/${materialId}`,
    patch,
  )
  return fromBackend(data)
}

export async function pollMaterial(
  materialId: number,
  condition: (m: Material) => boolean,
  intervalMs = 2500,
  timeoutMs = 180_000,
): Promise<Material> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const m = await getMaterial(materialId)
    if (condition(m)) return m
    if (m.status === 'FAILED') throw new Error(`素材处理失败（id=${materialId}）`)
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error('等待超时（3 分钟）')
}

// ── ZIP 批量上传 ──────────────────────────────────────────────

function fromBackendZipItem(raw: Record<string, unknown>): ZipPreviewItem {
  return {
    product: (raw.product as string | null) ?? null,
    sceneRole: (raw.scene_role as string | null) ?? null,
    files: (raw.files as string[]) ?? [],
    status: raw.status as ZipPreviewItem['status'],
  }
}

export async function uploadZip(
  tenantKey: string,
  file: File,
): Promise<ZipUploadResponse> {
  const form = new FormData()
  form.append('tenant_key', tenantKey)
  form.append('file', file)
  const { data } = await apiClient.post<{
    upload_id: string
    preview: Record<string, unknown>[]
  }>('/materials/upload-zip', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return {
    uploadId: data.upload_id,
    preview: data.preview.map(fromBackendZipItem),
  }
}

export async function confirmZip(
  uploadId: string,
  tenantKey: string,
  overrides?: ZipOverride[],
): Promise<{ materialIds: number[] }> {
  const { data } = await apiClient.post<{ material_ids: number[] }>(
    '/materials/upload-zip/confirm',
    {
      upload_id: uploadId,
      tenant_key: tenantKey,
      overrides: overrides?.map((o) => ({
        index: o.index,
        product: o.product,
        scene_role: o.sceneRole,
      })),
    },
  )
  return { materialIds: data.material_ids }
}
