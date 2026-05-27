import { useRef, useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { streamChat, type ToolResultPayload } from '../../api/chat'
import { taskApi } from '../../api/script'
import styles from './ChatPanel.module.css'

const TENANT_KEY = 'flowcut'
const SESSION_LS_KEY = 'flowcut.chat.session'
const POLL_INTERVAL_MS = 2000
const POLL_MAX_ATTEMPTS = 300 // 10 分钟兜底

type Role = 'user' | 'agent'

interface ChatMsg {
  id: string
  role: Role
  content: string
}

function TypingIndicator() {
  return (
    <div className={styles.typing}>
      <span /><span /><span />
    </div>
  )
}

function getOrCreateSessionKey(): string {
  let key = localStorage.getItem(SESSION_LS_KEY)
  if (!key) {
    key = crypto.randomUUID()
    localStorage.setItem(SESSION_LS_KEY, key)
  }
  return key
}

function genId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function pickScriptId(content: unknown): number | null {
  const rec = asRecord(content)
  if (!rec) return null
  const v = rec.script_id
  return typeof v === 'number' ? v : null
}

function pickTaskId(content: unknown): string | null {
  const rec = asRecord(content)
  if (!rec) return null
  const v = rec.task_id
  return typeof v === 'string' ? v : null
}

export default function ChatPanel() {
  const navigate = useNavigate()
  const [sessionKey] = useState<string>(() => getOrCreateSessionKey())
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [input, setInput] = useState('')
  const [isAgentTyping, setIsAgentTyping] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const cancelRef = useRef<(() => void) | null>(null)
  // 轮询计时器集合（卸载时清理）
  const pollersRef = useRef<Set<ReturnType<typeof setInterval>>>(new Set())

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isAgentTyping])

  useEffect(() => {
    const pollers = pollersRef.current
    return () => {
      cancelRef.current?.()
      pollers.forEach((id) => clearInterval(id))
      pollers.clear()
    }
  }, [])

  const appendAgentText = useCallback((token: string) => {
    setMessages((prev) => {
      const last = prev[prev.length - 1]
      if (last && last.role === 'agent') {
        const updated = { ...last, content: last.content + token }
        return [...prev.slice(0, -1), updated]
      }
      return [...prev, { id: genId(), role: 'agent', content: token }]
    })
  }, [])

  const pollTaskForScriptId = useCallback(
    (taskId: string) => {
      let attempts = 0
      const intervalId = setInterval(async () => {
        attempts += 1
        if (attempts > POLL_MAX_ATTEMPTS) {
          clearInterval(intervalId)
          pollersRef.current.delete(intervalId)
          return
        }
        try {
          const resp = await taskApi.get(taskId)
          if (resp.status === 'succeeded') {
            clearInterval(intervalId)
            pollersRef.current.delete(intervalId)
            const scriptId = resp.details?.script_id
            if (typeof scriptId === 'number') {
              navigate(`/workspace/${scriptId}`)
            }
          } else if (resp.status === 'failed') {
            clearInterval(intervalId)
            pollersRef.current.delete(intervalId)
          }
        } catch {
          // 单次失败容忍；attempts 计数已加，超过上限自动放弃
        }
      }, POLL_INTERVAL_MS)
      pollersRef.current.add(intervalId)
    },
    [navigate],
  )

  const handleToolResult = useCallback(
    (payload: ToolResultPayload) => {
      if (!payload.ok) return
      if (payload.tool_name === 'upload_script') {
        const scriptId = pickScriptId(payload.content)
        if (scriptId !== null) {
          navigate(`/workspace/${scriptId}`)
        }
      } else if (payload.tool_name === 'decompose_video') {
        const taskId = pickTaskId(payload.content)
        if (taskId) pollTaskForScriptId(taskId)
      }
    },
    [navigate, pollTaskForScriptId],
  )

  const handleSend = () => {
    const text = input.trim()
    if (!text || isAgentTyping) return

    setMessages((prev) => [...prev, { id: genId(), role: 'user', content: text }])
    setInput('')
    if (taRef.current) taRef.current.style.height = 'auto'
    setIsAgentTyping(true)

    cancelRef.current?.()
    cancelRef.current = streamChat({
      tenantKey: TENANT_KEY,
      sessionKey,
      query: text,
      onChunk: (token) => {
        setIsAgentTyping(false)
        appendAgentText(token)
      },
      onDone: () => {
        setIsAgentTyping(false)
      },
      onError: (msg) => {
        setIsAgentTyping(false)
        setMessages((prev) => [
          ...prev,
          { id: genId(), role: 'agent', content: `[出错] ${msg}` },
        ])
      },
      onToolResult: handleToolResult,
    })
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = `${Math.min(e.target.scrollHeight, 72)}px`
  }

  const handleNewTask = () => {
    cancelRef.current?.()
    cancelRef.current = null
    pollersRef.current.forEach((id) => clearInterval(id))
    pollersRef.current.clear()
    const fresh = crypto.randomUUID()
    localStorage.setItem(SESSION_LS_KEY, fresh)
    setMessages([])
    setIsAgentTyping(false)
    // 通过 reload 触发 sessionKey 重新读取（保持组件简单）
    window.location.reload()
  }

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span className={styles.title}>当前任务</span>
        <button className={styles.newBtn} onClick={handleNewTask}>＋ 新任务</button>
      </div>

      <div className={styles.messages}>
        {messages.map((msg) => (
          <div key={msg.id} className={`${styles.msg} ${msg.role === 'user' ? styles.user : styles.agent}`}>
            {msg.role === 'agent' && <span className={styles.who}>Agent</span>}
            {msg.role === 'user' && <span className={styles.who}>我</span>}
            <div className={styles.bubble}>{msg.content}</div>
          </div>
        ))}
        {isAgentTyping && (
          <div className={`${styles.msg} ${styles.agent}`}>
            <TypingIndicator />
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className={styles.inputArea}>
        <div className={styles.inputBox}>
          <textarea
            ref={taRef}
            rows={1}
            placeholder="输入指令，或直接确认当前步骤…"
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
          />
          <button className={styles.sendBtn} onClick={handleSend}>↑</button>
        </div>
      </div>
    </div>
  )
}
