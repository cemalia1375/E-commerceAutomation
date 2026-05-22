import type { Creative } from '../../types'
import styles from './CreativeCard.module.css'

const GRADIENTS = [
  'linear-gradient(160deg,#fde68a,#f59e0b,#ef4444)',
  'linear-gradient(160deg,#a7f3d0,#059669,#064e3b)',
  'linear-gradient(160deg,#bfdbfe,#3b82f6,#1e3a8a)',
  'linear-gradient(160deg,#ede9fe,#8b5cf6,#4c1d95)',
  'linear-gradient(160deg,#fce7f3,#ec4899,#9d174d)',
]

const STATUS_MAP = {
  ACTIVE:  { label: '投放中', bg: '#d1fae5', color: '#059669' },
  PENDING: { label: '待上架', bg: '#f1f5f9', color: '#475569' },
  DRAFT:   { label: '草稿',   bg: '#f1f5f9', color: '#475569' },
}

interface Props {
  creative: Creative
  index: number
  onClick?: (creative: Creative) => void
}

export default function CreativeCard({ creative, index, onClick }: Props) {
  const s = STATUS_MAP[creative.status]
  const date = new Date(creative.createdAt)
  const dateStr = `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  return (
    <div className={styles.card} onClick={() => onClick?.(creative)}>
      <div className={styles.thumb} style={{ background: GRADIENTS[index % GRADIENTS.length] }}>
        <div className={styles.overlay}><div className={styles.play}>▶</div></div>
        <div className={styles.dur}>{creative.duration}s</div>
      </div>
      <div className={styles.info}>
        <div className={styles.name}>{creative.name}</div>
        <div className={styles.meta}>
          <span>{dateStr}</span>
          <span className={styles.badge} style={{ background: s.bg, color: s.color }}>{s.label}</span>
        </div>
      </div>
    </div>
  )
}
