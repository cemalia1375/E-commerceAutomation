import type { ReactNode } from 'react'
import type { MaterialType } from '../../types'
import styles from './MediaPreview.module.css'

const VIDEO_EXT = new Set(['mp4', 'mov', 'avi', 'mkv', 'webm', 'flv', 'wmv', 'm4v'])
const AUDIO_EXT = new Set(['mp3', 'wav', 'aac', 'ogg', 'wma', 'flac', 'm4a'])
const IMAGE_EXT = new Set(['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg'])

export function inferMediaType(url: string | null | undefined): MaterialType | null {
  if (!url) return null
  const pathname = url.split('?')[0]
  const ext = pathname.split('.').pop()?.toLowerCase()
  if (!ext) return null
  if (VIDEO_EXT.has(ext)) return 'video'
  if (AUDIO_EXT.has(ext)) return 'audio'
  if (IMAGE_EXT.has(ext)) return 'image'
  return null
}

interface MediaPreviewProps {
  url: string | null | undefined
  type: MaterialType
  poster?: string
  name?: string
  height?: number
  empty?: ReactNode
}

export default function MediaPreview({
  url,
  type,
  poster,
  name,
  height = 200,
  empty,
}: MediaPreviewProps) {
  if (!url) {
    return (
      <div className={styles.empty} style={{ height }}>
        {empty ?? '无可预览资源'}
      </div>
    )
  }

  if (type === 'video') {
    return (
      <div className={styles.wrap}>
        <video
          className={styles.video}
          style={{ height }}
          src={url}
          poster={poster}
          controls
          preload="metadata"
        />
      </div>
    )
  }

  if (type === 'audio') {
    return (
      <div className={styles.audioBar}>
        {name && <div className={styles.audioName} title={name}>{name}</div>}
        <audio className={styles.audio} src={url} controls preload="metadata" />
      </div>
    )
  }

  return (
    <img
      className={styles.image}
      style={{ maxHeight: height }}
      src={url}
      alt={name ?? ''}
    />
  )
}
