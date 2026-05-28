import { useEffect, useMemo, useState } from 'react'
import { Input, Select } from 'antd'
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
const UNSPECIFIED_VALUE = '__UNSPECIFIED__'

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
  const [keyword, setKeyword] = useState('')
  const [productFilter, setProductFilter] = useState<string | undefined>(undefined)

  useEffect(() => {
    fetchMaterials(TENANT_KEY, {
      product: activeProduct ?? undefined,
      sceneRole: activeSceneRole ?? undefined,
    })
  }, [fetchMaterials, activeProduct, activeSceneRole])

  const materials = filteredMaterials()

  // 聚合产品选项（不含已被侧边栏选中的 activeProduct 影响——侧边栏拉的是当前列表）
  const productOptions = useMemo(() => {
    const set = new Set<string>()
    let hasEmpty = false
    for (const m of materials) {
      if (m.product) set.add(m.product)
      else hasEmpty = true
    }
    const opts = Array.from(set)
      .sort()
      .map((p) => ({ label: p, value: p }))
    if (hasEmpty) opts.push({ label: '未指定', value: UNSPECIFIED_VALUE })
    return opts
  }, [materials])

  const visibleMaterials = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    return materials.filter((m) => {
      if (kw && !m.name.toLowerCase().includes(kw)) return false
      if (productFilter !== undefined) {
        if (productFilter === UNSPECIFIED_VALUE) {
          if (m.product) return false
        } else if (m.product !== productFilter) {
          return false
        }
      }
      return true
    })
  }, [materials, keyword, productFilter])

  const groups = groupByDate(visibleMaterials)

  const breadcrumb = activeProduct
    ? `${activeProduct}${activeSceneRole ? ` / ${activeSceneRole}` : ''} · ${visibleMaterials.length} / ${materials.length} 个素材`
    : `全部 · ${visibleMaterials.length} / ${materials.length} 个素材`

  return (
    <div className={styles.layout}>
      <div className={styles.topBar}>
        <div style={{ fontSize: 13, color: '#555', fontWeight: 500 }}>{breadcrumb}</div>
        <Input.Search
          placeholder="按名称搜索"
          allowClear
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: 240 }}
          size="small"
        />
        <Select
          placeholder="产品筛选"
          allowClear
          value={productFilter}
          onChange={(v) => setProductFilter(v)}
          options={productOptions}
          style={{ width: 160 }}
          size="small"
        />
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
