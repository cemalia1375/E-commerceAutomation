const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8001'

export interface SessionSummary {
  session_key: string
  title: string | null
  session_type: string
  created_at: string
  updated_at: string
}

export async function createSession(tenantKey: string, title?: string): Promise<SessionSummary> {
  const res = await fetch(`${BASE_URL}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ tenant_key: tenantKey, title }),
  })
  if (!res.ok) throw new Error(`Failed to create session: ${res.status}`)
  return res.json()
}

export async function listSessions(tenantKey: string): Promise<SessionSummary[]> {
  const res = await fetch(`${BASE_URL}/sessions?tenant_key=${encodeURIComponent(tenantKey)}`, {
    credentials: 'include',
  })
  if (!res.ok) throw new Error(`Failed to list sessions: ${res.status}`)
  return res.json()
}

export interface NavigateDirective {
  route: string
  params?: Record<string, string | number>
  mode?: 'push' | 'replace'
}

export interface UiHint {
  render_as?: 'stats_card' | 'table' | 'text' | 'none'
  title?: string
}

export interface ToolResultContent {
  ok?: boolean
  data?: unknown
  navigate?: NavigateDirective
  ui_hint?: UiHint
  source?: string
  warning?: string
  error?: string
}

export interface ToolResultPayload {
  tool_name: string
  content: unknown
  ok: boolean
}

interface StreamChatParams {
  tenantKey: string
  sessionKey: string
  query: string
  onChunk: (token: string) => void
  onDone: () => void
  onError: (msg: string) => void
  onToolResult?: (payload: ToolResultPayload) => void
  uiContext?: { route: string; tab?: string; drama?: string }
}

// Returns a cancel function
export function streamChat(params: StreamChatParams): () => void {
  const { tenantKey, sessionKey, query, onChunk, onDone, onError, onToolResult, uiContext } = params
  const ctrl = new AbortController()

  fetch(`${BASE_URL}/agent/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({
      tenant_key: tenantKey,
      session_key: sessionKey,
      query,
      ...(uiContext ? { ui_context: uiContext } : {}),
    }),
    signal: ctrl.signal,
  })
    .then(async (res) => {
      if (!res.ok || !res.body) {
        onError(`HTTP ${res.status}`)
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const msg = JSON.parse(line.slice(6)) as { event: string; data?: unknown }
            if (msg.event === 'chunk') onChunk(typeof msg.data === 'string' ? msg.data : '')
            else if (msg.event === 'done') onDone()
            else if (msg.event === 'error') onError(typeof msg.data === 'string' ? msg.data : 'unknown error')
            else if (msg.event === 'tool_result' && onToolResult && msg.data && typeof msg.data === 'object') {
              onToolResult(msg.data as ToolResultPayload)
            }
          } catch { /* malformed SSE line */ }
        }
      }
    })
    .catch((err: unknown) => {
      if (err instanceof Error && err.name !== 'AbortError') onError(err.message)
    })

  return () => ctrl.abort()
}
