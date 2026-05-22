import { useRef, useEffect, useState } from 'react'
import { useGenerateStore } from '../../stores/generateStore'
import type { ChatMessage } from '../../types'
import styles from './ChatPanel.module.css'

function ProgressCard({ msg }: { msg: ChatMessage }) {
  return (
    <div className={styles.progCard}>
      <div className={`${styles.progIcon} ${msg.done ? styles.done : styles.running}`}>
        {msg.done ? '✅' : <span className={styles.spinner} />}
      </div>
      <div>
        <div className={styles.progLabel}>{msg.label}</div>
        <div className={styles.progSub}>{msg.subLabel}</div>
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className={styles.typing}>
      <span /><span /><span />
    </div>
  )
}

function formatRelativeTime(dateStr: string): string {
  const now = Date.now()
  const then = new Date(dateStr + 'Z').getTime()
  const diffMin = Math.floor((now - then) / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin}分钟前`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}小时前`
  return `${Math.floor(diffHr / 24)}天前`
}

export default function ChatPanel() {
  const { messages, isAgentTyping, sendUserMessage, newSession, fetchSessions, sessions, sessionKey } = useGenerateStore()
  const [input, setInput] = useState('')
  const [showList, setShowList] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    fetchSessions()
  }, [fetchSessions])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isAgentTyping])

  const handleNewTask = () => {
    newSession()
    setShowList(false)
  }

  const handleSend = () => {
    const text = input.trim()
    if (!text) return
    sendUserMessage(text)
    setInput('')
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = `${Math.min(e.target.scrollHeight, 72)}px`
  }

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span
          className={styles.title}
          onClick={() => setShowList((v) => !v)}
          style={{ cursor: 'pointer', userSelect: 'none' }}
        >
          {sessions.find((s) => s.session_key === sessionKey)?.title || '当前任务'} ▾
        </span>
        <button className={styles.newBtn} onClick={handleNewTask}>＋ 新任务</button>
      </div>

      {showList && (
        <div className={styles.sessionList}>
          {sessions.map((s) => (
            <div
              key={s.session_key}
              className={`${styles.sessionItem} ${s.session_key === sessionKey ? styles.sessionActive : ''}`}
              onClick={() => setShowList(false)}
            >
              <div className={styles.sessionTitle}>{s.title || s.session_key}</div>
              <div className={styles.sessionTime}>{formatRelativeTime(s.updated_at)}</div>
            </div>
          ))}
          {sessions.length === 0 && (
            <div className={styles.sessionEmpty}>暂无任务记录</div>
          )}
        </div>
      )}

      <div className={styles.messages}>
        {messages.map((msg) => (
          <div key={msg.id} className={`${styles.msg} ${msg.role === 'user' ? styles.user : styles.agent}`}>
            {msg.role === 'agent' && msg.type === 'text' && <span className={styles.who}>Agent</span>}
            {msg.role === 'user' && msg.type === 'text' && <span className={styles.who}>我</span>}
            {msg.type === 'progress'
              ? <ProgressCard msg={msg} />
              : <div className={styles.bubble}>{msg.content}</div>
            }
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
