import { useRef, useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  streamChat,
  getMessages,
  type ToolResultPayload,
  type ToolResultContent,
  type NavigateDirective,
  type ChatMessage,
} from '../../api/chat'
import { uploadReferenceVideo } from '../../api/referenceVideos'
import { getTenantKey, useAuthStore } from '../../stores/authStore'
import { useUIContextStore } from '../../stores/uiContextStore'
import { useSessionRestore } from '../../hooks/useSessionRestore'
import SessionList from './SessionList'
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

type Role = 'user' | 'agent' | 'tool' | 'tool_call'

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
    (obj.role === 'user' || obj.role === 'agent' || obj.role === 'tool' || obj.role === 'tool_call') &&
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

/** 将后端 OpenAI 格式消息转为前端 ChatMsg（用户/助手/工具气泡）。 */
function openaiToChatMsg(msg: ChatMessage): ChatMsg {
  if (msg.role === 'user') {
    return { id: genId(), role: 'user', content: msg.content || '' }
  }
  if (msg.role === 'assistant') {
    // 带 tool_calls 的 assistant 消息：content 通常为 null，
    // 生成占位文本告知用户 Agent 正在调用工具
    if (msg.tool_calls && msg.tool_calls.length > 0) {
      const names = msg.tool_calls.map((tc) => tc.function?.name || 'unknown').join(', ')
      return { id: genId(), role: 'agent', content: msg.content || `🔧 调用工具：${names}` }
    }
    return { id: genId(), role: 'agent', content: msg.content || '' }
  }
  if (msg.role === 'tool') {
    return {
      id: genId(),
      role: 'tool',
      content: '',
      toolName: msg.name || msg.tool_call_id || '',
      toolResult: { ok: true, data: msg.content },
    }
  }
  return { id: genId(), role: 'agent', content: JSON.stringify(msg) }
}

/** 合并后端历史与本地消息：后端优先，按 id 前缀 + content 去重。 */
function mergeBackendMessages(localMsgs: ChatMsg[], backendMsgs: ChatMessage[]): ChatMsg[] {
  const converted = backendMsgs.map(openaiToChatMsg)
  // 按 (role, content) 组合去重，比纯 content hash 更精确
  const seen = new Set(converted.map((m) => `${m.role}::${m.content}`))
  const uniqueLocal = localMsgs.filter((m) => !seen.has(`${m.role}::${m.content}`))
  return [...converted, ...uniqueLocal]
}

export default function ChatPanel() {
  const navigate = useNavigate()
  const TENANT_KEY = useAuthStore((s) => s.user?.tenantKey) ?? 'flowcut'
  const uiCtx = useUIContextStore((s) => s.ctx)

  // session 生命周期管理（含后端恢复）
  const {
    sessionKey,
    sessions,
    switchSession,
    newSession,
    removeSession,
    isRestoring,
  } = useSessionRestore(TENANT_KEY)

  const [messages, setMessages] = useState<ChatMsg[]>(() =>
    sessionKey ? loadMessages(sessionKey) : [],
  )
  const [loadingMessages, setLoadingMessages] = useState(false)
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
  // 防护：永不写入空数组（防止异常情况覆盖已有历史）
  useEffect(() => {
    try {
      if (messages.length > 0) {
        localStorage.setItem(messagesStorageKey(sessionKey), JSON.stringify(messages))
      }
    } catch {
      // quota / 隐私模式失败：放弃持久化但不影响功能
    }
  }, [messages, sessionKey])

  // 当 sessionKey 变更时（切换/恢复），从后端 + localStorage 恢复消息
  useEffect(() => {
    if (!sessionKey) return

    setLoadingMessages(true)

    // 先从 localStorage 加载（瞬时，无闪烁）
    const local = loadMessages(sessionKey)
    setMessages(local)

    // 再从后端同步（可合并更完整的历史），10s 超时防卡死
    let cancelled = false
    const abort = new AbortController()

    const sync = async () => {
      try {
        const { messages: backendMsgs } = await getMessages(
          TENANT_KEY, sessionKey, 0, undefined, abort.signal,
        )
        if (cancelled || abort.signal.aborted) return
        if (backendMsgs.length > 0) {
          setMessages((prev) => {
            const merged = mergeBackendMessages(prev, backendMsgs)
            if (merged.length < prev.length) return prev
            return merged
          })
        }
      } catch (err) {
        if (cancelled || abort.signal.aborted) return
        if (import.meta.env.DEV) {
          console.warn('[ChatPanel] 后端历史同步失败:', err instanceof Error ? err.message : err)
        }
      } finally {
        if (!cancelled && !abort.signal.aborted) setLoadingMessages(false)
      }
    }

    // 10 秒超时：后端不可用时不无限等待
    const timeout = setTimeout(() => {
      abort.abort()
      if (!cancelled) setLoadingMessages(false)
    }, 10_000)

    void sync()
    return () => {
      cancelled = true
      abort.abort()
      clearTimeout(timeout)
    }
  }, [sessionKey, TENANT_KEY])

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

      // 降级工具感知：后端无「工具开始」事件，收到 tool_result 时补一条提示
      setMessages((prev) => [
        ...prev,
        {
          id: genId(),
          role: 'tool_call',
          content: payload.tool_name,
        },
      ])

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
      uiContext: uiCtx,
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
    // 不再删除旧 session 数据 — 旧会话保留在会话列表中可随时切换
    void newSession()
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
        <span className={styles.title}>
          当前任务
          {sessions.length > 1 && (
            <span className={styles.sessionCount}>{sessions.length}</span>
          )}
        </span>
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

      {/* 会话列表：始终显示，恢复中也能看到历史会话 */}
      <SessionList
        sessions={sessions}
        activeKey={sessionKey}
        onSwitch={switchSession}
        onNew={handleNewTask}
        onDelete={removeSession}
      />

      <div className={styles.messages}>
        {(() => {
          // 恢复未完成且尚无任何本地缓存 → 显示加载态
          if (!sessionKey && isRestoring) {
            return <div className={styles.sessionEmpty}>加载消息中...</div>
          }
          // session 已确定但消息列表为空（本地 + 后端均无） → 显示空状态
          if (messages.length === 0 && !loadingMessages) {
            return <div className={styles.sessionEmpty}>开始新对话吧 👋</div>
          }
          // 本地已有消息但后端仍在同步 → 显示消息 + 顶部轻量同步条
          if (messages.length === 0 && loadingMessages) {
            return <div className={styles.sessionEmpty}>加载消息中...</div>
          }
          return messages.map((msg) => {
          if (msg.role === 'tool_call') {
            return (
              <div key={msg.id} className={`${styles.msg} ${styles.agent}`}>
                <div className={styles.toolCallChip}>
                  <span className={styles.toolCallIcon}>🔧</span>
                  <span>已调用工具：{msg.content}</span>
                </div>
              </div>
            )
          }
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
        })})()}
        {loadingMessages && messages.length > 0 && (
          <div className={styles.syncBar}>
            <span className={styles.spinner} />
            <span>同步最新消息...</span>
          </div>
        )}
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
