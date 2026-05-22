import { useMaterialStore } from '../../stores/materialStore'
import FilterChips from '../common/FilterChips'
import DateGroup from '../common/DateGroup'
import AudioCard from './AudioCard'
import styles from './Library.module.css'
import type { Material } from '../../types'

const CATEGORIES = ['全部', 'BGM', '音效']

export default function AudioLibrary() {
  const { audioMaterials } = useMaterialStore()
  return (
    <div className={styles.layout}>
      <div className={styles.topBar}>
        <FilterChips options={CATEGORIES} active="全部" onChange={() => {}} />
        <div className={styles.spacer} />
        <button className={styles.uploadBtn}>↑ 上传音频</button>
      </div>
      <div className={styles.grid}>
        <DateGroup label="今天">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {audioMaterials().map((a: Material) => (
              <AudioCard
                key={a.id}
                id={a.id}
                name={a.name}
                category={a.category}
                audioDuration={`${Math.floor(a.duration / 60)}:${String(Math.floor(a.duration % 60)).padStart(2, '0')}`}
                fileSize={a.fileSize}
              />
            ))}
          </div>
        </DateGroup>
      </div>
    </div>
  )
}
