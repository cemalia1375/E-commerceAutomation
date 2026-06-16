import { useRef, useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  streamChat,
  type ToolResultPayload,
  type ToolResultContent,
  type NavigateDirective,
} from '../../api/chat'
import { uploadReferenceVideo } from '../../api/referenceVideos'
import { getTenantKey, useAuthStore } from '../../stores/authStore'
import StatsBubble from './StatsBubble'
import styles from './ChatPanel.module.css'

// 会话 / 消息的 localStorage key 按 tenant 命名空间隔离，避免同一浏览器多账号串数据。
// 每个 session 一份独立的 messages 存储 key，避免会话之间相互污染。
// 拆镜成功后 ChatPanel 会被 navigate 卸载，没有持久化历史就会丢光。
const sessionLsKey = () => `${getTenantKey()}.chat.session`
const messagesLsKey = (sessionKey: string) => `${getTenantKey()}.chat.messages.${sessionKey}`
const COLLAPSED_LS_KEY = 'flowcut.chat.collapsed'

// 工具结果可以请求跳转的白名单路由（防 agent 幻觉路径）
const ALLOWED_ROUTE_PATTERNS = [
  /^\/workspace\/[^/?]+(?:\?.*)?$/,
  /^\/material(?:\?.*)?$/,
  /^\/creative(?:\?.*)?$/,
]

type Role = 'user' | 'agent' | 'tool'

interface ChatMsg {
  id: string
  role: Role
  content: string
  toolName?: string
  toolResult?: ToolResultContent
}

const RENDERABLE_HINTS = new Set(['stats_card', 'table'])

function shouldRenderToolBubble(content: ToolResultContent | null): boolean {
  if (!content) return false
  const hint = content.ui_hint?.render_as
  return typeof hint === 'string' && RENDERABLE_HINTS.has(hint)
}

function messagesStorageKey(sessionKey: string): string {
  return messagesLsKey(sessionKey)
}

function isChatMsg(value: unknown): value is ChatMsg {
  if (!value || typeof value !== 'object') return false
  const obj = value as Record<string, unknown>
  return (
    typeof obj.id === 'string' &&
    (obj.role === 'user' || obj.role === 'agent' || obj.role === 'tool') &&
    typeof obj.content === 'string'
  )
}

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_LS_KEY) === '1'
  } catch {
    return false
  }
}

function persistCollapsed(collapsed: boolean): void {
  try {
    localStorage.setItem(COLLAPSED_LS_KEY, collapsed ? '1' : '0')
  } catch {
    // 忽略 quota / 隐私模式
  }
}

function loadMessages(sessionKey: string): ChatMsg[] {
  try {
    const raw = localStorage.getItem(messagesStorageKey(sessionKey))
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(isChatMsg)
  } catch {
    return []
  }
}

interface Attachment {
  refVideoId: number
  scriptId: number | null
  taskId: string | null
  filename: string
  ossKey: string
}

const MAX_VIDEO_BYTES = 500 * 1024 * 1024 // 500MB,与后端一致

function TypingIndicator() {
  return (
    <div className={styles.typing}>
      <span /><span /><span />
    </div>
  )
}

function getOrCreateSessionKey(): string {
  let key = localStorage.getItem(sessionLsKey())
  if (!key) {
    key = crypto.randomUUID()
    localStorage.setItem(sessionLsKey(), key)
  }
  return key
}

function genId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function applyRouteParams(
  template: string,
  params: Record<string, string | number> = {},
): string {
  return template.replace(/:(\w+)/g, (_, key: string) => {
    const v = params[key]
    return v === undefined ? `:${key}` : String(v)
  })
}

function isAllowedRoute(route: string): boolean {
  return ALLOWED_ROUTE_PATTERNS.some((re) => re.test(route))
}

function asToolResultContent(value: unknown): ToolResultContent | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as ToolResultContent)
    : null
}

export default function ChatPanel() {
  const navigate = useNavigate()
  const TENANT_KEY = useAuthStore((s) => s.user?.tenantKey) ?? 'flowcut'
  const [sessionKey] = useState<string>(() => getOrCreateSessionKey())
  const [messages, setMessages] = useState<ChatMsg[]>(() => loadMessages(sessionKey))
  const [input, setInput] = useState('')
  const [isAgentTyping, setIsAgentTyping] = useState(false)
  const [attachment, setAttachment] = useState<Attachment | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [isDraggingFile, setIsDraggingFile] = useState(false)
  const [collapsed, setCollapsed] = useState<boolean>(() => loadCollapsed())
  const dragCounterRef = useRef(0)
  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const cancelRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isAgentTyping])

  // messages 持久化到 localStorage：跳转到 /workspace/:id 后再回 / 时仍可恢复。
  // 流式 token 触发的高频写入对小数组也只是几微秒，无需 debounce。
  useEffect(() => {
    try {
      localStorage.setItem(messagesStorageKey(sessionKey), JSON.stringify(messages))
    } catch {
      // quota / 隐私模式失败：放弃持久化但不影响功能
    }
  }, [messages, sessionKey])

  useEffect(() => {
    return () => {
      cancelRef.current?.()
    }
  }, [])

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev
      persistCollapsed(next)
      return next
    })
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

  const handleToolResult = useCallback(
    (payload: ToolResultPayload) => {
      if (payload.ok === false) return
      const content = asToolResultContent(payload.content)
      if (!content || content.ok === false) return

      if (shouldRenderToolBubble(content)) {
        setMessages((prev) => [
          ...prev,
          {
            id: genId(),
            role: 'tool',
            content: '',
            toolName: payload.tool_name,
            toolResult: content,
          },
        ])
      }

      const directive: NavigateDirective | undefined = content.navigate
      if (!directive?.route) return
      const target = applyRouteParams(directive.route, directive.params)
      if (!isAllowedRoute(target)) {
        console.warn('[chat] 拒绝非白名单跳转', target)
        return
      }
      navigate(target, { replace: directive.mode === 'replace' })
    },
    [navigate],
  )

  const handlePickFile = () => {
    if (uploading || attachment) return
    fileRef.current?.click()
  }

  const processFile = useCallback(
    async (file: File) => {
      if (!file.type.startsWith('video/')) {
        setUploadError(`仅支持视频文件，实际类型 ${file.type || '未知'}`)
        return
      }
      if (file.size > MAX_VIDEO_BYTES) {
        setUploadError(`视频超过 500MB（实际 ${(file.size / 1024 / 1024).toFixed(1)}MB）`)
        return
      }
      setUploadError(null)
      setUploading(true)
      setUploadProgress(0)
      try {
        const resp = await uploadReferenceVideo(
          TENANT_KEY,
          file,
          undefined,
          (percent) => setUploadProgress(percent),
          'pending',
        )
        setAttachment({
          refVideoId: resp.ref_video_id,
          scriptId: resp.script_id,
          taskId: resp.task_id,
          filename: file.name,
          ossKey: resp.oss_key,
        })
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : '上传失败'
        setUploadError(msg)
      } finally {
        setUploading(false)
        setUploadProgress(0)
      }
    },
    [TENANT_KEY],
  )

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (e.target) e.target.value = ''
    if (!file) return
    void processFile(file)
  }

  const handleDragEnter = (e: React.DragEvent<HTMLDivElement>) => {
    if (uploading || attachment) return
    if (!Array.from(e.dataTransfer.types).includes('Files')) return
    e.preventDefault()
    dragCounterRef.current += 1
    setIsDraggingFile(true)
  }

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    if (uploading || attachment) return
    if (!Array.from(e.dataTransfer.types).includes('Files')) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1)
    if (dragCounterRef.current === 0) setIsDraggingFile(false)
  }

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    dragCounterRef.current = 0
    setIsDraggingFile(false)
    if (uploading || attachment) return
    const file = e.dataTransfer.files?.[0]
    if (!file) return
    void processFile(file)
  }

  const handleRemoveAttachment = () => {
    setAttachment(null)
    setUploadError(null)
  }

  const handleSend = () => {
    const text = input.trim()
    if ((!text && !attachment) || isAgentTyping || uploading) return

    const userBubble = attachment
      ? `📎 ${attachment.filename}${text ? `\n\n${text}` : ''}`
      : text

    const queryParts: string[] = []
    if (attachment) {
      const safeName = attachment.filename.replace(/"/g, '\\"')
      queryParts.push(
        `[USER_ATTACHED_VIDEO ref_video_id=${attachment.refVideoId} ` +
          `filename="${safeName}" status="pending"]`,
      )
    }
    if (text) queryParts.push(text)
    const query = queryParts.join('\n')

    setMessages((prev) => [...prev, { id: genId(), role: 'user', content: userBubble }])
    setInput('')
    setAttachment(null)
    if (taRef.current) taRef.current.style.height = 'auto'
    setIsAgentTyping(true)

    cancelRef.current?.()
    cancelRef.current = streamChat({
      tenantKey: TENANT_KEY,
      sessionKey,
      query,
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
    // 先清掉旧 session 的持久化消息，避免遗留垃圾在 localStorage 里堆积。
    // 必须在 reload 之前同步删，否则刷新后就找不到旧 sessionKey 了。
    localStorage.removeItem(messagesStorageKey(sessionKey))
    const fresh = crypto.randomUUID()
    localStorage.setItem(sessionLsKey(), fresh)
    // 通过 reload 触发 sessionKey 重新读取（保持组件简单）
    window.location.reload()
  }

  if (collapsed) {
    return (
      <div className={`${styles.panel} ${styles.panelCollapsed}`}>
        <button
          type="button"
          className={styles.expandBtn}
          onClick={toggleCollapsed}
          aria-label="展开对话面板"
          title="展开对话面板"
        >
          <span className={styles.expandIcon}>›</span>
          <span className={styles.expandLabel}>对话</span>
        </button>
      </div>
    )
  }

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span className={styles.title}>当前任务</span>
        <div className={styles.headerActions}>
          <button className={styles.newBtn} onClick={handleNewTask}>＋ 新任务</button>
          <button
            type="button"
            className={styles.collapseBtn}
            onClick={toggleCollapsed}
            aria-label="收起对话面板"
            title="收起"
          >
            ‹
          </button>
        </div>
      </div>

      <div className={styles.messages}>
        {messages.map((msg) => {
          if (msg.role === 'tool' && msg.toolResult) {
            return (
              <div key={msg.id} className={`${styles.msg} ${styles.agent}`}>
                <StatsBubble toolName={msg.toolName ?? ''} content={msg.toolResult} />
              </div>
            )
          }
          return (
            <div
              key={msg.id}
              className={`${styles.msg} ${msg.role === 'user' ? styles.user : styles.agent}`}
            >
              {msg.role === 'agent' && <span className={styles.who}>Agent</span>}
              {msg.role === 'user' && <span className={styles.who}>我</span>}
              <div className={styles.bubble}>
                {msg.role === 'agent' ? (
                  <div className={styles.markdown}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                  </div>
                ) : (
                  <div className={styles.userText}>{msg.content}</div>
                )}
              </div>
            </div>
          )
        })}
        {isAgentTyping && (
          <div className={`${styles.msg} ${styles.agent}`}>
            <TypingIndicator />
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div
        className={`${styles.inputArea} ${isDraggingFile ? styles.inputAreaDragging : ''}`}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {isDraggingFile && (
          <div className={styles.dropOverlay}>
            <span>松开鼠标上传爆款视频</span>
          </div>
        )}
        {(attachment || uploading || uploadError) && (
          <div className={styles.attachmentBar}>
            {uploading && (
              <div className={styles.attachmentChip}>
                <span className={styles.spinner} />
                <span className={styles.attachmentName}>上传中… {uploadProgress}%</span>
              </div>
            )}
            {attachment && !uploading && (
              <div className={styles.attachmentChip}>
                <span className={styles.attachmentIcon}>🎬</span>
                <span className={styles.attachmentName} title={attachment.filename}>
                  {attachment.filename}
                </span>
                <span className={styles.attachmentMeta}>
                  ref #{attachment.refVideoId} · 拆镜中
                </span>
                <button
                  type="button"
                  className={styles.attachmentRemove}
                  onClick={handleRemoveAttachment}
                  aria-label="移除附件"
                >
                  ×
                </button>
              </div>
            )}
            {uploadError && (
              <div className={styles.attachmentError}>{uploadError}</div>
            )}
          </div>
        )}
        <div className={styles.inputBox}>
          <input
            ref={fileRef}
            type="file"
            accept="video/*"
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
          <button
            type="button"
            className={styles.attachBtn}
            onClick={handlePickFile}
            disabled={uploading || !!attachment}
            aria-label="上传视频"
            title={attachment ? '已附带视频' : '上传爆款视频'}
          >
            📎
          </button>
          <textarea
            ref={taRef}
            rows={1}
            placeholder={attachment ? '补充说明，例如「按这个视频生成 2 版脚本」…' : '输入指令，或直接确认当前步骤…'}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
          />
          <button
            className={styles.sendBtn}
            onClick={handleSend}
            disabled={uploading || isAgentTyping || (!input.trim() && !attachment)}
          >
            ↑
          </button>
        </div>
      </div>
    </div>
  )
}
