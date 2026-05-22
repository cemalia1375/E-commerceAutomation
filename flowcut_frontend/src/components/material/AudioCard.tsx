import styles from './AudioCard.module.css'

interface Props {
  id: string
  name: string
  category: string
  audioDuration: string
  fileSize: number
}

const BARS = [40, 70, 55, 85, 45, 65, 90, 50, 75, 35, 60, 80]

export default function AudioCard({ name, category, audioDuration, fileSize }: Props) {
  return (
    <div className={styles.card}>
      <div className={styles.wave}>
        {BARS.map((h, i) => (
          <div key={i} className={styles.bar} style={{ height: `${h}%` }} />
        ))}
      </div>
      <div className={styles.info}>
        <div className={styles.name}>{name}</div>
        <div className={styles.meta}>
          <span>{category}</span>
          <span>{audioDuration}</span>
          <span>{(fileSize / 1_000_000).toFixed(1)} MB</span>
        </div>
      </div>
      <button className={styles.play}>▶</button>
    </div>
  )
}
