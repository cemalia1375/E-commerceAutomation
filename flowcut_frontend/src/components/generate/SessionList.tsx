/**
 * SessionList — 会话列表下拉组件，复用 ChatPanel.module.css 中已有的 sessionList 样式。
 *
 * 功能：
 *   - 显示租户下全部会话（按最近活跃排序）
 *   - 点击切换会话
 *   - 右击或悬停时显示删除按钮
 *   - 有历史会话时自动展开
 */
import { useState, useEffect } from 'react'
import type { SessionSummary } from '../../api/chat'
import styles from './ChatPanel.module.css'

interface SessionListProps {
  sessions: SessionSummary[]
  activeKey: string
  onSwitch: (key: string) => void
  onNew: () => void
  onDelete: (key: string) => void
}

/** 将 ISO 时间戳转为短日期/时间显示 */
function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return '刚刚'
    if (diffMin < 60) return `${diffMin}分钟前`
    const diffHour = Math.floor(diffMin / 60)
    if (diffHour < 24) return `${diffHour}小时前`
    const diffDay = Math.floor(diffHour / 24)
    if (diffDay < 7) return `${diffDay}天前`
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
  } catch {
    return ''
  }
}

/** 用首条用户消息的前 20 字作为会话标题 */
function sessionLabel(s: SessionSummary): string {
  if (s.title) return s.title
  return `会话 ${s.session_key.slice(0, 8)}`
}

export default function SessionList({
  sessions,
  activeKey,
  onSwitch,
  onNew,
  onDelete,
}: SessionListProps) {
  const [expanded, setExpanded] = useState(false)
  const [confirmingKey, setConfirmingKey] = useState<string | null>(null)

  // 恢复完成后有历史会话 → 自动展开，用户一眼看到有记录
  useEffect(() => {
    if (sessions.length > 0) setExpanded(true)
  }, [sessions.length])

  const handleDelete = (e: React.MouseEvent, key: string) => {
    e.stopPropagation()
    if (confirmingKey === key) {
      onDelete(key)
      setConfirmingKey(null)
    } else {
      setConfirmingKey(key)
      // 3 秒后取消确认
      setTimeout(() => setConfirmingKey(null), 3000)
    }
  }

  return (
    <div className={styles.sessionListContainer}>
      <div className={styles.sessionListHeader} onClick={() => setExpanded(!expanded)}>
        <span className={styles.sessionListTitle}>
          📋 历史会话
          {sessions.length > 0 && (
            <span className={styles.sessionCount}>{sessions.length}</span>
          )}
        </span>
        <div className={styles.sessionListActions}>
          <button
            type="button"
            className={styles.sessionNewMini}
            onClick={(e) => { e.stopPropagation(); void onNew() }}
            title="新建对话"
          >
            +
          </button>
          <span className={styles.sessionExpandIcon}>{expanded ? '▾' : '▸'}</span>
        </div>
      </div>

      {expanded && (
        <div className={styles.sessionList}>
          {sessions.length === 0 ? (
            <div className={styles.sessionEmpty}>暂无历史会话</div>
          ) : (
            sessions.map((s) => {
              const isActive = s.session_key === activeKey
              const isConfirming = confirmingKey === s.session_key
              return (
                <div
                  key={s.session_key}
                  className={`${styles.sessionItem} ${isActive ? styles.sessionActive : ''}`}
                  onClick={() => onSwitch(s.session_key)}
                >
                  <span className={styles.sessionTitle}>
                    {sessionLabel(s)}
                  </span>
                  <span className={styles.sessionMeta}>
                    {s.message_count !== undefined && (
                      <span className={styles.sessionMsgCount}>{s.message_count}</span>
                    )}
                    <span className={styles.sessionTime}>{formatTime(s.updated_at)}</span>
                  </span>
                  <button
                    type="button"
                    className={`${styles.sessionDeleteBtn} ${isConfirming ? styles.sessionDeleteConfirm : ''}`}
                    onClick={(e) => handleDelete(e, s.session_key)}
                    title={isConfirming ? '确认删除' : '删除会话'}
                  >
                    {isConfirming ? '确认?' : '×'}
                  </button>
                </div>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
