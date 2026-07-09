import { useCreativeStore } from '../../stores/creativeStore'
import DateGroup from '../common/DateGroup'
import CenteredLoader from '../common/CenteredLoader'
import SrtCard from './SrtCard'
import styles from './CreativeLibrary.module.css'
import type { Creative } from '../../types'

function groupByDate(creatives: Creative[]) {
  const groups: Record<string, Creative[]> = {}
  creatives.forEach((c) => {
    const d = c.createdAt.split('T')[0]
    const today = new Date().toISOString().split('T')[0]
    const label = d === today ? '今天' : d
    if (!groups[label]) groups[label] = []
    groups[label].push(c)
  })
  return groups
}

export default function SrtLibrary() {
  const { creatives, loading } = useCreativeStore()
  const filtered = creatives.filter((c) => c.srtLineCount !== undefined)
  const groups = groupByDate(filtered)

  if (loading && creatives.length === 0) {
    return (
      <div className={styles.layout}>
        <CenteredLoader label="正在加载字幕文件" />
      </div>
    )
  }

  return (
    <div className={styles.layout}>
      <div className={styles.topBar} />
      <div className={styles.grid}>
        {!loading && filtered.length === 0 && (
          <div style={{ textAlign: 'center', padding: 60, color: '#999' }}>暂未生成字幕文件</div>
        )}
        {Object.entries(groups).map(([label, items]) => (
          <DateGroup key={label} label={label}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {items.map((c) => <SrtCard key={c.id} creative={c} />)}
            </div>
          </DateGroup>
        ))}
      </div>
    </div>
  )
}
