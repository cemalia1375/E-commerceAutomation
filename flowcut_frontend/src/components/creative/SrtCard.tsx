import type { Creative } from '../../types'
import styles from './SrtCard.module.css'

export default function SrtCard({ creative }: { creative: Creative }) {
  const date = new Date(creative.createdAt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  return (
    <div className={styles.card}>
      <div className={styles.icon}>SRT</div>
      <div className={styles.info}>
        <div className={styles.name}>{creative.name}.srt</div>
        <div className={styles.meta}>{creative.duration}s · {creative.srtLineCount ?? 0} 条字幕 · {date}</div>
      </div>
      <button className={styles.dl}>↓ 下载</button>
    </div>
  )
}
