import { useCreativeStore } from '../../stores/creativeStore'
import { useDetailDrawerStore } from '../../stores/detailDrawerStore'
import type { CreativeStatusLabel } from '../../types'
import FilterChips from '../common/FilterChips'
import DateGroup from '../common/DateGroup'
import CreativeCard from './CreativeCard'
import styles from './CreativeLibrary.module.css'
import type { Creative } from '../../types'

const STATUS_OPTIONS = ['全部', '投放中', '待上架', '草稿']

function groupByDate(creatives: Creative[]) {
  const groups: Record<string, { creative: Creative; idx: number }[]> = {}
  creatives.forEach((c, idx) => {
    const d = c.createdAt.split('T')[0]
    const today = new Date().toISOString().split('T')[0]
    const label = d === today ? '今天' : d
    if (!groups[label]) groups[label] = []
    groups[label].push({ creative: c, idx })
  })
  return groups
}

export default function CreativeVideoLibrary() {
  const { filteredCreatives, activeStatus, setStatus } = useCreativeStore()
  const { openCreativeDetail } = useDetailDrawerStore()
  const creatives = filteredCreatives()
  const groups = groupByDate(creatives)
  return (
    <div className={styles.layout}>
      <div className={styles.topBar}>
        <FilterChips options={STATUS_OPTIONS} active={activeStatus} onChange={(v) => setStatus(v as CreativeStatusLabel)} />
      </div>
      <div className={styles.grid}>
        {Object.entries(groups).map(([label, items]) => (
          <DateGroup key={label} label={label}>
            <div className={styles.cardGrid}>
              {items.map(({ creative, idx }) => <CreativeCard key={creative.id} creative={creative} index={idx} onClick={openCreativeDetail} />)}
            </div>
          </DateGroup>
        ))}
      </div>
    </div>
  )
}
