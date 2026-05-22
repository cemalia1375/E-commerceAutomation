import { useCreativeStore } from '../../stores/creativeStore'
import CreativeVideoLibrary from './CreativeVideoLibrary'
import SrtLibrary from './SrtLibrary'
import CreativeDetailDrawer from './CreativeDetailDrawer'
import styles from './CreativeTab.module.css'

const SUB_TABS = [
  { key: 'video' as const, label: '成片视频' },
  { key: 'srt'   as const, label: '字幕文件' },
]

export default function CreativeTab() {
  const { activeSubTab, setSubTab } = useCreativeStore()
  return (
    <div className={styles.tab}>
      <div className={styles.subBar}>
        {SUB_TABS.map((t) => (
          <button
            key={t.key}
            className={`${styles.stb} ${activeSubTab === t.key ? styles.active : ''}`}
            onClick={() => setSubTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className={styles.body}>
        {activeSubTab === 'video' ? <CreativeVideoLibrary /> : <SrtLibrary />}
      </div>
      <CreativeDetailDrawer />
    </div>
  )
}
