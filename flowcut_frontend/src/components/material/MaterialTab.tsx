import { useEffect } from 'react'
import { useMaterialStore } from '../../stores/materialStore'
import { useProductTreeStore } from '../../stores/productTreeStore'
import type { MaterialLibraryTab } from '../../types'
import VideoLibrary from './VideoLibrary'
import ImageLibrary from './ImageLibrary'
import AudioLibrary from './AudioLibrary'
import HighlightAssetLibrary from './HighlightAssetLibrary'
import MaterialDetailDrawer from './MaterialDetailDrawer'
import MaterialSidebar from './MaterialSidebar'
import styles from './MaterialTab.module.css'

const TENANT_KEY = 'flowcut'

const SUB_TABS: { key: MaterialLibraryTab; label: string }[] = [
  { key: 'video', label: '视频' },
  { key: 'highlight_asset', label: '高光资产' },
  { key: 'image', label: '图片' },
  { key: 'audio', label: '音频' },
]

const LIB_MAP: Record<MaterialLibraryTab, React.ComponentType> = {
  video: VideoLibrary,
  highlight_asset: HighlightAssetLibrary,
  image: ImageLibrary,
  audio: AudioLibrary,
}

export default function MaterialTab() {
  const { activeSubTab, setSubTab } = useMaterialStore()
  const fetchTree = useProductTreeStore((s) => s.fetchTree)
  const Lib = LIB_MAP[activeSubTab]

  useEffect(() => {
    fetchTree(TENANT_KEY)
  }, [fetchTree])

  return (
    <div className={`${styles.tab} ${styles.layout}`}>
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
        {activeSubTab === 'video' && <MaterialSidebar />}
        <div className={styles.content}>
          <Lib />
        </div>
      </div>
      {activeSubTab !== 'highlight_asset' && <MaterialDetailDrawer />}
    </div>
  )
}
