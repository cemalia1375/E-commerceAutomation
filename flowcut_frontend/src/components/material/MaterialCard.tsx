import { useRef } from 'react'
import type { Material } from '../../types'
import StatusBadge from '../common/StatusBadge'
import styles from './MaterialCard.module.css'

interface Props {
  material: Material
  aspectRatio?: string
  onClick?: (material: Material) => void
}

export default function MaterialCard({ material, aspectRatio = '16/9', onClick }: Props) {
  const urlCacheRef = useRef<Record<string, string>>({})

  const stableThumbUrl = (): string => {
    if (!material.thumbnailUrl) return ''
    const key = material.ossKey || String(material.id)
    if (!urlCacheRef.current[key]) urlCacheRef.current[key] = material.thumbnailUrl
    return urlCacheRef.current[key]
  }

  return (
    <div className={styles.card} onClick={() => onClick?.(material)}>
      <div className={styles.thumb} style={{ aspectRatio }}>
        {material.thumbnailUrl ? (
          <img className={styles.thumbImg} src={stableThumbUrl()} alt={material.name} loading="lazy" />
        ) : (
          <div className={styles.thumbBg} />
        )}
        {material.duration > 0 && <div className={styles.dur}>{Math.round(material.duration)}s</div>}
        <div className={styles.stat}><StatusBadge status={material.status} /></div>
      </div>
      <div className={styles.info}>
        <div className={styles.name}>{material.name}</div>
        <div className={styles.meta}>
          <span>{material.category}</span>
          <span>{(material.fileSize / 1_000_000).toFixed(1)} MB</span>
        </div>
      </div>
    </div>
  )
}
