import { Tooltip } from 'antd'
import type { ScriptStatus } from '../../types/script'

export type WorkspaceTab = 'script' | 'match' | 'preview' | 'export'

interface TabDef {
  key: WorkspaceTab
  label: string
}

const TABS: TabDef[] = [
  { key: 'script', label: '脚本' },
  { key: 'match', label: '素材匹配' },
  { key: 'preview', label: '成片预览' },
  { key: 'export', label: '素材导出' },
]

interface WorkspaceTabBarProps {
  current: WorkspaceTab
  status: ScriptStatus
  onChange: (tab: WorkspaceTab) => void
}

interface GateInfo {
  disabled: boolean
  hint: string | null
}

function gateFor(tabKey: WorkspaceTab, status: ScriptStatus): GateInfo {
  if (tabKey === 'script') {
    return { disabled: false, hint: null }
  }
  if (status === 'PROCESSING' || status === 'FAILED') {
    return { disabled: true, hint: '请等待拆镜完成' }
  }
  if (status === 'DRAFT') {
    return { disabled: false, hint: '建议先确认脚本' }
  }
  return { disabled: false, hint: null }
}

const baseStyle: React.CSSProperties = {
  padding: '8px 16px',
  cursor: 'pointer',
  borderBottom: '2px solid transparent',
  userSelect: 'none',
  fontSize: 14,
}

const activeStyle: React.CSSProperties = {
  ...baseStyle,
  borderBottomColor: '#2563eb',
  color: '#2563eb',
  fontWeight: 600,
}

const disabledStyle: React.CSSProperties = {
  ...baseStyle,
  cursor: 'not-allowed',
  color: '#bbb',
}

export default function WorkspaceTabBar({
  current,
  status,
  onChange,
}: WorkspaceTabBarProps) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 8,
        borderBottom: '1px solid #eee',
        padding: '0 16px',
      }}
    >
      {TABS.map((tab) => {
        const gate = gateFor(tab.key, status)
        const isActive = tab.key === current
        const style = gate.disabled
          ? disabledStyle
          : isActive
          ? activeStyle
          : baseStyle

        const node = (
          <div
            key={tab.key}
            style={style}
            onClick={() => {
              if (gate.disabled) return
              onChange(tab.key)
            }}
          >
            {tab.label}
            {gate.hint && !gate.disabled && (
              <span style={{ marginLeft: 6, fontSize: 12, color: '#faad14' }}>
                ·提示
              </span>
            )}
          </div>
        )

        if (gate.hint) {
          return (
            <Tooltip key={tab.key} title={gate.hint}>
              {node}
            </Tooltip>
          )
        }
        return node
      })}
    </div>
  )
}
