import { useEffect, useState } from 'react'
import { useMaterialStore } from '../../stores/materialStore'
import { useProductTreeStore } from '../../stores/productTreeStore'
import { useDetailDrawerStore } from '../../stores/detailDrawerStore'
import DateGroup from '../common/DateGroup'
import MaterialCard from './MaterialCard'
import UploadCard from './UploadCard'
import UploadModal from './UploadModal'
import styles from './Library.module.css'
import type { Material } from '../../types'

const TENANT_KEY = 'flowcut'

function groupByDate(materials: Material[]) {
  const groups: Record<string, Material[]> = {}
  for (const m of materials) {
    const d = m.createdAt.split('T')[0]
    const label = d === new Date().toISOString().split('T')[0] ? '今天' : d
    if (!groups[label]) groups[label] = []
    groups[label].push(m)
  }
  return groups
}

export default function VideoLibrary() {
  const { filteredMaterials, fetchMaterials, isLoading } = useMaterialStore()
  const { activeProduct, activeSceneRole } = useProductTreeStore()
  const { openMaterialDetail } = useDetailDrawerStore()
  const [modalOpen, setModalOpen] = useState(false)

  useEffect(() => {
    fetchMaterials(TENANT_KEY, {
      product: activeProduct ?? undefined,
      sceneRole: activeSceneRole ?? undefined,
    })
  }, [fetchMaterials, activeProduct, activeSceneRole])

  const materials = filteredMaterials()
  const groups = groupByDate(materials)

  const breadcrumb = activeProduct
    ? `${activeProduct}${activeSceneRole ? ` / ${activeSceneRole}` : ''} · ${materials.length} 个素材`
    : `全部 · ${materials.length} 个素材`

  return (
    <div className={styles.layout}>
      <div className={styles.topBar}>
        <div style={{ fontSize: 13, color: '#555', fontWeight: 500 }}>{breadcrumb}</div>
        <div className={styles.spacer} />
        <button className={styles.uploadBtn} onClick={() => setModalOpen(true)}>
          ↑ 上传素材
        </button>
      </div>
      {isLoading && <div style={{ padding: '24px', color: '#999' }}>加载中…</div>}
      {!isLoading && (
        <div className={styles.grid}>
          {Object.entries(groups).map(([label, items], gi) => (
            <DateGroup key={label} label={label}>
              <div className={styles.cardGrid}>
                {gi === 0 && <UploadCard onClick={() => setModalOpen(true)} />}
                {items.map((m) => (
                  <MaterialCard key={m.id} material={m} onClick={openMaterialDetail} />
                ))}
              </div>
            </DateGroup>
          ))}
          {Object.keys(groups).length === 0 && (
            <DateGroup label="今天">
              <div className={styles.cardGrid}>
                <UploadCard onClick={() => setModalOpen(true)} />
              </div>
            </DateGroup>
          )}
        </div>
      )}
      <UploadModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSuccess={() =>
          fetchMaterials(TENANT_KEY, {
            product: activeProduct ?? undefined,
            sceneRole: activeSceneRole ?? undefined,
          })
        }
      />
    </div>
  )
}
