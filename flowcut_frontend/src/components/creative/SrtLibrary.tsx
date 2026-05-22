import { useCreativeStore } from '../../stores/creativeStore'
import DateGroup from '../common/DateGroup'
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
  const { creatives } = useCreativeStore()
  const groups = groupByDate(creatives.filter((c) => c.srtLineCount !== undefined))
  return (
    <div className={styles.layout}>
      <div className={styles.topBar} />
      <div className={styles.grid}>
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
