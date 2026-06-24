import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useCreativeStore } from '../../stores/creativeStore'
import { useUIContextStore } from '../../stores/uiContextStore'
import CreativeVideoLibrary from './CreativeVideoLibrary'
import HighlightCreativeLibrary from './HighlightCreativeLibrary'
import SrtLibrary from './SrtLibrary'
import CreativeDetailDrawer from './CreativeDetailDrawer'
import styles from './CreativeTab.module.css'

const SUB_TABS = [
  { key: 'video' as const, label: '成片视频' },
  { key: 'srt'   as const, label: '字幕文件' },
  { key: 'highlight' as const, label: '高光' },
]

const VALID_SUB_TABS = SUB_TABS.map((t) => t.key)

export default function CreativeTab() {
  const { activeSubTab, setSubTab } = useCreativeStore()
  const [searchParams] = useSearchParams()
  const setUIContext = useUIContextStore((s) => s.setUIContext)

  // 支持 /creative?tab=highlight 直达对应子 tab（工具导航等场景）
  const tabParam = searchParams.get('tab')
  useEffect(() => {
    if (tabParam && (VALID_SUB_TABS as string[]).includes(tabParam)) {
      setSubTab(tabParam as (typeof VALID_SUB_TABS)[number])
    }
  }, [tabParam, setSubTab])

  useEffect(() => {
    setUIContext({ route: '/creative', tab: activeSubTab })
  }, [activeSubTab, setUIContext])

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
        {activeSubTab === 'video' && <CreativeVideoLibrary />}
        {activeSubTab === 'srt' && <SrtLibrary />}
        {activeSubTab === 'highlight' && <HighlightCreativeLibrary />}
      </div>
      <CreativeDetailDrawer />
    </div>
  )
}
