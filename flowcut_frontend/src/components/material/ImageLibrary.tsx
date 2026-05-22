import { useEffect, useRef } from 'react'
import { message } from 'antd'
import { useMaterialStore } from '../../stores/materialStore'
import { useDetailDrawerStore } from '../../stores/detailDrawerStore'
import FilterChips from '../common/FilterChips'
import DateGroup from '../common/DateGroup'
import MaterialCard from './MaterialCard'
import UploadCard from './UploadCard'
import styles from './Library.module.css'
import { uploadMaterial, processMaterial } from '../../api/materials'

const TENANT_KEY = 'flowcut'
const CATEGORIES = ['全部', '产品图', '背景图', '字幕板']

export default function ImageLibrary() {
  const { filteredMaterials, fetchMaterials, isLoading, addMaterial } = useMaterialStore()
  const { openMaterialDetail } = useDetailDrawerStore()
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    fetchMaterials(TENANT_KEY)
  }, [fetchMaterials])

  const materials = filteredMaterials()

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    try {
      const { material_id } = await uploadMaterial(TENANT_KEY, file, '')
      await processMaterial(material_id)
      addMaterial({
        id: String(material_id),
        ossKey: '',
        ossUrl: '',
        name: file.name,
        category: '产品',
        duration: 0,
        fileSize: file.size,
        status: 'PROCESSING',
        usageCount: 0,
        createdAt: new Date().toISOString(),
        type: 'image',
      })
    } catch (_err) {
      message.error('上传失败')
    }
  }

  return (
    <div className={styles.layout}>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={handleFileChange}
      />
      <div className={styles.topBar}>
        <FilterChips options={CATEGORIES} active="全部" onChange={() => {}} />
        <div className={styles.spacer} />
        <button className={styles.uploadBtn} onClick={() => fileInputRef.current?.click()}>
          ↑ 上传图片
        </button>
      </div>
      {isLoading && <div style={{ padding: '24px', color: '#999' }}>加载中…</div>}
      {!isLoading && (
        <div className={styles.grid}>
          <DateGroup label="今天">
            <div className={styles.cardGrid}>
              <UploadCard onClick={() => fileInputRef.current?.click()} />
              {materials.map((m) => (
                <MaterialCard key={m.id} material={m} aspectRatio="1/1" onClick={openMaterialDetail} />
              ))}
            </div>
          </DateGroup>
        </div>
      )}
    </div>
  )
}
