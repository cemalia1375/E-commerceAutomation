import { useGenerateStore } from '../../stores/generateStore'
import type { GenerateStep } from '../../types'
import styles from './StepBar.module.css'

const STEPS: { n: GenerateStep; label: string }[] = [
  { n: 1, label: '上传视频' },
  { n: 2, label: '选脚本' },
  { n: 3, label: '素材匹配' },
  { n: 4, label: '确认成片' },
  { n: 5, label: '上架千川' },
]

export default function StepBar() {
  const { step } = useGenerateStore()
  return (
    <div className={styles.bar}>
      {STEPS.map((s, i) => {
        const isDone    = step > s.n
        const isActive  = step === s.n
        const isLast    = s.n === 5
        const isDisabled = s.n === 5
        return (
          <div key={s.n} className={styles.item} style={{ flex: isLast ? 'none' : 1, minWidth: 0 }}>
            <div className={`${styles.dot} ${isDone ? styles.ok : isActive ? styles.on : styles.off} ${isDisabled ? styles.disabled : ''}`}>
              {isDone ? '✓' : s.n}
            </div>
            <span className={`${styles.label} ${isDone ? styles.labelOk : isActive ? styles.labelOn : ''} ${isDisabled ? styles.labelDisabled : ''}`}>
              {s.label}
            </span>
            {i < STEPS.length - 1 && (
              <div className={`${styles.line} ${isDone ? styles.lineOk : ''}`} />
            )}
          </div>
        )
      })}
    </div>
  )
}
