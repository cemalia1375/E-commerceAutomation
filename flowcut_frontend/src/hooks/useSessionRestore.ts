/**
 * useSessionRestore — 挂载时从后端恢复最近的会话，解决 localStorage 丢失导致聊天记录"消失"的问题。
 *
 * 恢复优先级：
 *   1. localStorage 中缓存的 sessionKey 仍然有效 → 直接使用
 *   2. localStorage 丢失/无效 → 调 GET /sessions 找回最近会话
 *   3. 后端也无历史会话 → 生成新 sessionKey 并 POST /sessions 同步到后端
 *
 * 性能：
 *   - 验证消息时只取 1 条（limit=1），不等全量历史
 *   - 全局 5s 超时，后端不可用时快速降级到本地模式
 *
 * 始终在挂载时调用一次，返回 { sessionKey, sessions, switchSession, isRestoring }。
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import {
  listSessions,
  getMessages,
  createSession,
  updateSession,
  deleteSession as deleteSessionApi,
  type SessionSummary,
} from '../api/chat'
import { getTenantKey } from '../stores/authStore'

const RESTORE_TIMEOUT_MS = 5000

const sessionLsKey = () => `${getTenantKey()}.chat.session`

function safeUUID(): string {
  try {
    return crypto.randomUUID()
  } catch {
    const arr = new Uint8Array(16)
    crypto.getRandomValues(arr)
    arr[6] = (arr[6]! & 0x0f) | 0x40
    arr[8] = (arr[8]! & 0x3f) | 0x80
    const hex = (n: number) => n.toString(16).padStart(2, '0')
    const d = (i: number) => hex(arr[i]!)
    return `${d(0)}${d(1)}${d(2)}${d(3)}-${d(4)}${d(5)}-${d(6)}${d(7)}-${d(8)}${d(9)}-${d(10)}${d(11)}${d(12)}${d(13)}${d(14)}${d(15)}`
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), ms)
    promise.then(
      (val) => { clearTimeout(timer); resolve(val) },
      (err) => { clearTimeout(timer); reject(err) },
    )
  })
}

export interface SessionRestoreResult {
  sessionKey: string
  sessions: SessionSummary[]
  switchSession: (key: string) => void
  newSession: () => Promise<string>
  removeSession: (key: string) => Promise<void>
  renameSession: (key: string, title: string) => Promise<void>
  isRestoring: boolean
}

export function useSessionRestore(
  tenantKey: string,
  onSessionChanged?: (newKey: string) => void,
): SessionRestoreResult {
  const [sessionKey, setSessionKey] = useState<string>('')
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [isRestoring, setIsRestoring] = useState(true)
  const restoredRef = useRef(false)

  useEffect(() => {
    if (restoredRef.current) return
    restoredRef.current = true

    let cancelled = false

    async function restore() {
      const cached = (() => {
        try { return localStorage.getItem(sessionLsKey()) } catch { return null }
      })()

      let resolved = false

      // 并行拉取：验证缓存 + 拉会话列表，5s 超时（带 AbortController 真正取消请求）
      const restoreAbort = new AbortController()
      const { signal } = restoreAbort

      const verifyCached = cached
        ? getMessages(tenantKey, cached, 0, 1, signal).then(
            (r) => ({ ok: true as const, messages: r.messages }),
            () => ({ ok: false as const }),
          )
        : Promise.resolve({ ok: false as const })

      const fetchSessions = listSessions(tenantKey, 50, 0, signal).then(
        (list) => ({ ok: true as const, list }),
        () => ({ ok: false as const }),
      )

      try {
        await withTimeout(
          Promise.all([verifyCached, fetchSessions]),
          RESTORE_TIMEOUT_MS,
        )
      } catch {
        // 超时 → 取消底层 fetch，Promise.allSettled 立即返回
        restoreAbort.abort()
      }

      if (cancelled) return

      const [cachedResult, sessionsResult] = await Promise.allSettled([
        verifyCached, fetchSessions,
      ])

      const remoteSessions: SessionSummary[] =
        sessionsResult.status === 'fulfilled' && sessionsResult.value.ok
          ? sessionsResult.value.list : []
      if (!cancelled) setSessions(remoteSessions)

      // 1) 缓存有效 → 直接使用
      if (
        cachedResult.status === 'fulfilled' &&
        cachedResult.value.ok &&
        cachedResult.value.messages.length > 0
      ) {
        if (!cancelled) {
          setSessionKey(cached!)
          localStorage.setItem(sessionLsKey(), cached!)
          setIsRestoring(false)
        }
        resolved = true
      }

      // 2) 后端列表恢复
      if (!resolved && remoteSessions.length > 0) {
        const lastSession = remoteSessions[0]
        if (!cancelled) {
          setSessionKey(lastSession.session_key)
          localStorage.setItem(sessionLsKey(), lastSession.session_key)
          setIsRestoring(false)
        }
        resolved = true
      }

      // 3) 全新用户 — 创建新会话同步到后端
      if (!resolved) {
        const newKey = safeUUID()
        try { await createSession(newKey) } catch { /* 不阻塞 */ }
        if (!cancelled) {
          setSessionKey(newKey)
          localStorage.setItem(sessionLsKey(), newKey)
          setIsRestoring(false)
        }
      }
    }

    void restore()
    return () => { cancelled = true }
  }, [tenantKey])

  // 会话变更后静默刷新列表
  useEffect(() => {
    if (!sessionKey || isRestoring) return
    let cancelled = false
    const load = async () => {
      try {
        const remoteSessions = await listSessions(tenantKey)
        if (!cancelled) setSessions(remoteSessions)
      } catch { /* 静默 */ }
    }
    void load()
    return () => { cancelled = true }
  }, [sessionKey, tenantKey, isRestoring])

  const switchSession = useCallback((key: string) => {
    setSessionKey(key)
    localStorage.setItem(sessionLsKey(), key)
    onSessionChanged?.(key)
  }, [onSessionChanged])

  const newSession = useCallback(async (): Promise<string> => {
    const newKey = safeUUID()
    try { await createSession(newKey) } catch { /* 不阻塞 */ }
    setSessionKey(newKey)
    localStorage.setItem(sessionLsKey(), newKey)
    try {
      const remoteSessions = await listSessions(tenantKey)
      setSessions(remoteSessions)
    } catch { /* 静默 */ }
    onSessionChanged?.(newKey)
    return newKey
  }, [tenantKey, onSessionChanged])

  const removeSession = useCallback(async (key: string) => {
    try { await deleteSessionApi(tenantKey, key) } catch { /* 不阻塞 */ }
    setSessions((prev) => prev.filter((s) => s.session_key !== key))
    if (key === sessionKey) {
      // need fresh state — re-filter from current list
      setSessions((current) => {
        const remaining = current.filter((s) => s.session_key !== key)
        if (remaining.length > 0) {
          switchSession(remaining[0].session_key)
        } else {
          void newSession()
        }
        return current
      })
    }
  }, [tenantKey, sessionKey, switchSession, newSession])

  const renameSession = useCallback(async (key: string, title: string) => {
    try {
      await updateSession(key, title)
      setSessions((prev) =>
        prev.map((s) => (s.session_key === key ? { ...s, title } : s)),
      )
    } catch { /* 静默 */ }
  }, [])

  return {
    sessionKey,
    sessions,
    switchSession,
    newSession,
    removeSession,
    renameSession,
    isRestoring,
  }
}
