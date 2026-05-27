import type {
  Script,
  ScriptSegment,
  SegmentMatchResult,
  TaskStatus,
} from '../types/script'

const BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8001'

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
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
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
