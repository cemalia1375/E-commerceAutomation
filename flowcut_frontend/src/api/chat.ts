import { getApiBase } from './client'

export interface SessionSummary {
  session_key: string
  title: string | null
  session_type: string
  created_at: string
  updated_at: string
  message_count?: number
}

export async function createSession(sessionKey: string, title?: string): Promise<SessionSummary> {
  const res = await fetch(`${getApiBase()}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ session_key: sessionKey, title }),
  })
  if (!res.ok) throw new Error(`Failed to create session: ${res.status}`)
  return res.json()
}

export async function updateSession(
  sessionKey: string,
  title: string,
): Promise<void> {
  const res = await fetch(
    `${getApiBase()}/sessions/${encodeURIComponent(sessionKey)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ title }),
    },
  )
  if (!res.ok) throw new Error(`Failed to update session: ${res.status}`)
}

export async function listSessions(
  tenantKey: string,
  limit: number = 50,
  offset: number = 0,
  signal?: AbortSignal,
): Promise<SessionSummary[]> {
  const res = await fetch(
    `${getApiBase()}/sessions?tenant_key=${encodeURIComponent(tenantKey)}&limit=${limit}&offset=${offset}`,
    { credentials: 'include', signal },
  )
  if (!res.ok) throw new Error(`Failed to list sessions: ${res.status}`)
  return res.json()
}

export async function deleteSession(tenantKey: string, sessionKey: string): Promise<void> {
  const res = await fetch(
    `${getApiBase()}/sessions/${encodeURIComponent(sessionKey)}?tenant_key=${encodeURIComponent(tenantKey)}`,
    { method: 'DELETE', credentials: 'include' },
  )
  if (!res.ok) throw new Error(`Failed to delete session: ${res.status}`)
}

export interface ChatMessage {
  role: string
  content: string | null
  tool_calls?: Array<{
    id: string
    type: string
    function: { name: string; arguments: string }
  }>
  tool_call_id?: string
  name?: string
}

export interface SessionMessages {
  session_key: string
  messages: ChatMessage[]
  last_consolidated: number
}

export async function getMessages(
  tenantKey: string,
  sessionKey: string,
  offset: number = 0,
  limit?: number,
  signal?: AbortSignal,
): Promise<SessionMessages> {
  const params = new URLSearchParams({ tenant_key: tenantKey })
  params.set('offset', String(offset))
  if (limit !== undefined) params.set('limit', String(limit))
  const res = await fetch(
    `${getApiBase()}/sessions/${encodeURIComponent(sessionKey)}/messages?${params.toString()}`,
    { credentials: 'include', signal },
  )
  if (!res.ok) throw new Error(`Failed to load messages: ${res.status}`)
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

  fetch(`${getApiBase()}/agent/chat`, {
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
      let streamEndedCleanly = true
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
            else if (msg.event === 'done') { streamEndedCleanly = false; onDone() }
            else if (msg.event === 'error') { streamEndedCleanly = false; onError(typeof msg.data === 'string' ? msg.data : 'unknown error') }
            else if (msg.event === 'tool_result' && onToolResult && msg.data && typeof msg.data === 'object') {
              onToolResult(msg.data as ToolResultPayload)
            }
          } catch { /* malformed SSE line */ }
        }
      }
      // 流结束但从未收到 done/error 事件（后端崩溃、网络闪断等）→ 兜底通知
      if (streamEndedCleanly) {
        onDone()
      }
    })
    .catch((err: unknown) => {
      if (err instanceof Error && err.name !== 'AbortError') onError(err.message)
    })

  return () => ctrl.abort()
}
