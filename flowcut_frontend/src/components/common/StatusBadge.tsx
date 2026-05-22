import type { MaterialStatus } from '../../types'

const CONFIG: Record<MaterialStatus, { label: string; bg: string; color: string }> = {
  READY:      { label: 'READY',  bg: '#d1fae5', color: '#059669' },
  PROCESSING: { label: '处理中', bg: '#fef3c7', color: '#d97706' },
  FAILED:     { label: '失败',   bg: '#fee2e2', color: '#dc2626' },
}

export default function StatusBadge({ status }: { status: MaterialStatus }) {
  const c = CONFIG[status]
  return (
    <span style={{ fontSize: 10, padding: '2px 5px', borderRadius: 3, fontWeight: 600, background: c.bg, color: c.color }}>
      {c.label}
    </span>
  )
}
