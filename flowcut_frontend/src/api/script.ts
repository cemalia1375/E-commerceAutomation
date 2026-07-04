import type {
  Script,
  ScriptSegment,
  ScriptSource,
  ScriptStatus,
  SegmentMatchResult,
  TaskStatus,
} from '../types/script'
import { getApiBase } from './client'

export interface ScriptListItem {
  id: number
  tenant_key: string
  source: ScriptSource
  status: ScriptStatus
  product: string | null
  reference_video_id: number | null
  segments: ScriptSegment[]
  created_at: string
  updated_at: string
}

interface ListResp {
  ok: boolean
  scripts: ScriptListItem[]
}

interface UploadResp {
  ok: boolean
  script_id: number
}

interface MatchResp {
  ok: boolean
  results: SegmentMatchResult[]
}

interface ExportResp {
  ok: boolean
  task_id: string
}

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${getApiBase()}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include', // 携带登录会话 cookie
    ...init,
  })
  if (!resp.ok) {
    if (resp.status === 401) {
      window.dispatchEvent(new Event('auth:unauthorized'))
    }
    let detail = ''
    try {
      detail = ((await resp.json()) as { detail?: string }).detail || ''
    } catch {
      // ignore JSON parse errors on error responses
    }
    throw new Error(`${resp.status}: ${detail || resp.statusText}`)
  }
  return resp.json() as Promise<T>
}

export const scriptApi = {
  upload: (tenantKey: string, segments: Partial<ScriptSegment>[]) =>
    jsonFetch<UploadResp>('/flowcut/scripts', {
      method: 'POST',
      body: JSON.stringify({ tenant_key: tenantKey, segments }),
    }),

  list: (tenantKey: string, status?: ScriptStatus, source?: ScriptSource) => {
    const params = new URLSearchParams({ tenant_key: tenantKey })
    if (status) params.set('status', status)
    if (source) params.set('source', source)
    return jsonFetch<ListResp>(`/flowcut/scripts?${params.toString()}`)
  },

  get: (scriptId: number) =>
    jsonFetch<{ ok: boolean } & Script>(`/flowcut/scripts/${scriptId}`),

  update: (scriptId: number, segments: ScriptSegment[]) =>
    jsonFetch<{ ok: boolean }>(`/flowcut/scripts/${scriptId}`, {
      method: 'PATCH',
      body: JSON.stringify({ segments }),
    }),

  confirm: (scriptId: number) =>
    jsonFetch<{ ok: boolean }>(`/flowcut/scripts/${scriptId}/confirm`, {
      method: 'POST',
    }),

  reopen: (scriptId: number) =>
    jsonFetch<{ ok: boolean }>(`/flowcut/scripts/${scriptId}/reopen`, {
      method: 'POST',
    }),

  saveHighlightCreative: (
    scriptId: number,
    tenantKey: string,
    creativeType: 'highlight_original' | 'highlight_digital_human' = 'highlight_original',
  ) =>
    jsonFetch<{
      ok: boolean
      creative_id: number
      highlight_start: number
      highlight_end: number
    }>(`/flowcut/scripts/${scriptId}/save-highlight-creative`, {
      method: 'POST',
      body: JSON.stringify({
        tenant_key: tenantKey,
        creative_type: creativeType,
      }),
    }),

  match: (scriptId: number, tenantKey: string, product = '') =>
    jsonFetch<MatchResp>(`/flowcut/scripts/${scriptId}/match`, {
      method: 'POST',
      body: JSON.stringify({ tenant_key: tenantKey, product }),
    }),

  export: (
    scriptId: number,
    selections: Record<number, number[]>,
    tenantKey: string,
  ) =>
    jsonFetch<ExportResp>(`/flowcut/scripts/${scriptId}/export`, {
      method: 'POST',
      body: JSON.stringify({ selections, tenant_key: tenantKey }),
    }),

  updateProduct: (scriptId: number, product: string | null) =>
    jsonFetch<{ ok: boolean; script_id: number; product: string | null }>(
      `/flowcut/scripts/${scriptId}/update-product`,
      {
        method: 'POST',
        body: JSON.stringify({ product }),
      },
    ),
}

export const taskApi = {
  get: (taskId: string) =>
    jsonFetch<{ ok: boolean } & TaskStatus>(`/flowcut/tasks/${taskId}`),
}
