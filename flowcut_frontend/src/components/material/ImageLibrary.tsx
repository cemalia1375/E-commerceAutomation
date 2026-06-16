import { useEffect, useRef } from 'react'
import { message } from 'antd'
import { useMaterialStore } from '../../stores/materialStore'
import { useDetailDrawerStore } from '../../stores/detailDrawerStore'
import { useAuthStore } from '../../stores/authStore'
import FilterChips from '../common/FilterChips'
import DateGroup from '../common/DateGroup'
import MaterialCard from './MaterialCard'
import UploadCard from './UploadCard'
import styles from './Library.module.css'
import { uploadMaterial } from '../../api/materials'

const CATEGORIES = ['全部', '产品图', '背景图', '字幕板']

export default function ImageLibrary() {
  const TENANT_KEY = useAuthStore((s) => s.user?.tenantKey) ?? 'flowcut'
  const { filteredMaterials, fetchMaterials, isLoading, addMaterial } = useMaterialStore()
  const { openMaterialDetail } = useDetailDrawerStore()
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    fetchMaterials(TENANT_KEY)
  }, [fetchMaterials, TENANT_KEY])

  const materials = filteredMaterials()

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    try {
      // upload 路由内部已经自动入队 MATERIAL_PROCESS；不要再调 processMaterial，
      // 图片 worker 秒级 mark READY，再调 process 会因 status!=PROCESSING 返回 400。
      const { material_id } = await uploadMaterial(TENANT_KEY, file, '')
      addMaterial({
        id: String(material_id),
        ossKey: '',
        ossUrl: '',
        name: file.name,
        category: '产品展示',
        duration: 0,
        fileSize: file.size,
        status: 'PROCESSING',
        usageCount: 0,
        createdAt: new Date().toISOString(),
        type: 'image',
      })
      // 后端 worker 跑完会把 thumbnail_url 等字段补齐；重新拉一次确保 UI 同步。
      void fetchMaterials(TENANT_KEY)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '上传失败'
      message.error(`上传失败：${msg}`)
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
