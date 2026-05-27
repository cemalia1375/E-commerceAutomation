import { useEffect, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { Alert, Spin, Tag } from 'antd'
import { scriptApi } from '../../api/script'
import type { Script, ScriptStatus } from '../../types/script'
import WorkspaceTabBar, { type WorkspaceTab } from './WorkspaceTabBar'
import TabPlaceholder from './TabPlaceholder'

const POLL_INTERVAL_MS = 3000
const VALID_TABS: WorkspaceTab[] = ['script', 'match', 'preview', 'export']

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return '未知错误'
}

function parseTab(raw: string | null): WorkspaceTab {
  if (raw && (VALID_TABS as string[]).includes(raw)) {
    return raw as WorkspaceTab
  }
  return 'script'
}

function statusColor(status: ScriptStatus): string {
  switch (status) {
    case 'PROCESSING':
      return 'blue'
    case 'DRAFT':
      return 'orange'
    case 'CONFIRMED':
      return 'green'
    case 'FAILED':
      return 'red'
    default:
      return 'default'
  }
}

export default function WorkspaceLayout() {
  const { scriptId } = useParams<{ scriptId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const [script, setScript] = useState<Script | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)

  const currentTab = parseTab(searchParams.get('tab'))

  const parsedId = scriptId ? Number(scriptId) : NaN
  const idInvalid = !scriptId || Number.isNaN(parsedId)

  useEffect(() => {
    if (idInvalid) return
    const id = parsedId

    let cancelled = false

    const fetchOnce = async (): Promise<void> => {
      try {
        const s = await scriptApi.get(id)
        if (cancelled) return
        setScript(s)
        setLoadError(null)
        if (s.status === 'PROCESSING') {
          timerRef.current = window.setTimeout(fetchOnce, POLL_INTERVAL_MS)
        }
      } catch (err: unknown) {
        if (cancelled) return
        setLoadError(getErrorMessage(err))
        // 失败时也继续轮询，避免短暂网络抖动卡死
        timerRef.current = window.setTimeout(fetchOnce, POLL_INTERVAL_MS)
      }
    }

    fetchOnce()

    return () => {
      cancelled = true
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }
  }, [idInvalid, parsedId])

  const handleTabChange = (tab: WorkspaceTab): void => {
    const next = new URLSearchParams(searchParams)
    next.set('tab', tab)
    setSearchParams(next, { replace: true })
  }

  const status: ScriptStatus = script?.status ?? 'PROCESSING'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid #eee',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
        }}
      >
        <span style={{ fontWeight: 600 }}>脚本 #{scriptId}</span>
        {script && <Tag color={statusColor(status)}>{status}</Tag>}
        {status === 'PROCESSING' && (
          <span style={{ color: '#888', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <Spin size="small" /> 拆镜中…
          </span>
        )}
      </div>

      {status === 'FAILED' && (
        <Alert
          type="error"
          showIcon
          message="拆镜失败"
          description={
            <span>
              该脚本拆镜失败，请返回 <Link to="/">入口页</Link> 重试。
            </span>
          }
          style={{ margin: 16 }}
        />
      )}

      {loadError && status !== 'FAILED' && (
        <Alert
          type="warning"
          showIcon
          message={`加载失败：${loadError}`}
          style={{ margin: 16 }}
        />
      )}

      {idInvalid && (
        <Alert
          type="error"
          showIcon
          message="无效的 scriptId"
          style={{ margin: 16 }}
        />
      )}

      <WorkspaceTabBar
        current={currentTab}
        status={status}
        onChange={handleTabChange}
      />

      <div style={{ flex: 1, overflow: 'auto' }}>
        <TabPlaceholder name={currentTab} />
      </div>
    </div>
  )
}
