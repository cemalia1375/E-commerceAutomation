import { useEffect, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { Alert, Spin, Tag } from 'antd'
import { scriptApi } from '../../api/script'
import { useScriptStore } from '../../stores/scriptStore'
import type { Script, ScriptStatus } from '../../types/script'
import WorkspaceTabBar, { type WorkspaceTab } from './WorkspaceTabBar'
import TabPlaceholder from './TabPlaceholder'
import ScriptTab from './ScriptTab'
import MatchTab from './MatchTab'
import PreviewTab from './PreviewTab'
import ExportTab from './ExportTab'
import HighlightTab from './HighlightTab'

const POLL_INTERVAL_MS = 3000
const VALID_TABS: WorkspaceTab[] = ['script', 'highlight', 'match', 'preview', 'export']

const WORKSPACE_ID_KEY = 'flowcut.workspace.activeId'
const WORKSPACE_TAB_KEY = 'flowcut.workspace.activeTab'
const WORKSPACE_MODE_KEY = 'flowcut.workspace.activeMode'
const WORKSPACE_CHANGED_EVENT = 'workspace-changed'

function writeActiveWorkspace(id: string | null, tab: string | null, mode: string | null): void {
  try {
    if (id !== null) localStorage.setItem(WORKSPACE_ID_KEY, id)
    if (tab !== null) localStorage.setItem(WORKSPACE_TAB_KEY, tab)
    if (mode !== null) localStorage.setItem(WORKSPACE_MODE_KEY, mode)
    window.dispatchEvent(new Event(WORKSPACE_CHANGED_EVENT))
  } catch {
    // ignore quota / privacy mode errors
  }
}

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

function isHighlightScript(script: Script | null): boolean {
  return Boolean(
    script?.segments?.some((seg) =>
      seg.hook_strength !== undefined ||
      seg.ending_connectability !== undefined ||
      seg.context_dependency !== undefined ||
      seg.continuity_risk !== undefined ||
      seg.candidate_use !== undefined ||
      seg.followup_fit !== undefined ||
      seg.bridge_text !== undefined,
    ),
  )
}

export default function WorkspaceLayout() {
  const { scriptId } = useParams<{ scriptId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const [script, setScriptLocal] = useState<Script | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)
  const setStoreScript = useScriptStore((s) => s.setScript)
  const resetStore = useScriptStore((s) => s.reset)

  const setScript = (s: Script | null): void => {
    setScriptLocal(s)
    setStoreScript(s)
  }

  const currentTab = parseTab(searchParams.get('tab'))
  const requestedMode = searchParams.get('mode') === 'highlight' ? 'highlight' : 'reference'
  const inferredHighlight = isHighlightScript(script)
  const workflowMode = requestedMode === 'highlight' || inferredHighlight ? 'highlight' : 'reference'

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idInvalid, parsedId])

  // 仅在 scriptId 真正变化时 reset store，避免离开页面就丢状态
  const prevScriptIdRef = useRef<number | null>(null)
  useEffect(() => {
    if (idInvalid) return
    const prev = prevScriptIdRef.current
    if (prev !== null && prev !== parsedId) {
      resetStore()
    }
    prevScriptIdRef.current = parsedId
  }, [idInvalid, parsedId, resetStore])

  // 同步当前工作台 id 到 localStorage，供 Header 显示"工作台" tab
  useEffect(() => {
    if (idInvalid || !scriptId) return
    writeActiveWorkspace(scriptId, currentTab, workflowMode)
  }, [idInvalid, scriptId, currentTab, workflowMode])

  const handleTabChange = (tab: WorkspaceTab): void => {
    const next = new URLSearchParams(searchParams)
    next.set('tab', tab)
    setSearchParams(next, { replace: true })
  }

  useEffect(() => {
    if (workflowMode === 'highlight' && searchParams.get('mode') !== 'highlight') {
      const next = new URLSearchParams(searchParams)
      next.set('mode', 'highlight')
      next.set('tab', 'highlight')
      setSearchParams(next, { replace: true })
      return
    }
    if (workflowMode === 'highlight' && !['script', 'highlight'].includes(currentTab)) {
      const next = new URLSearchParams(searchParams)
      next.set('tab', 'highlight')
      setSearchParams(next, { replace: true })
      return
    }
    if (workflowMode === 'reference' && currentTab === 'highlight') {
      const next = new URLSearchParams(searchParams)
      next.delete('mode')
      next.set('tab', 'script')
      setSearchParams(next, { replace: true })
    }
  }, [currentTab, searchParams, setSearchParams, workflowMode])

  const status: ScriptStatus = script?.status ?? 'PROCESSING'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%' }}>
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
        mode={workflowMode}
        onChange={handleTabChange}
      />

      <div style={{ flex: 1, overflow: 'auto' }}>
        {idInvalid ? (
          <TabPlaceholder name={currentTab} />
        ) : currentTab === 'script' ? (
          <ScriptTab />
        ) : currentTab === 'highlight' ? (
          <HighlightTab />
        ) : currentTab === 'match' ? (
          <MatchTab />
        ) : currentTab === 'preview' ? (
          <PreviewTab />
        ) : currentTab === 'export' ? (
          <ExportTab />
        ) : (
          <TabPlaceholder name={currentTab} />
        )}
      </div>
    </div>
  )
}
