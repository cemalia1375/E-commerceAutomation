import styles from './UploadCard.module.css'

export default function UploadCard({ onClick }: { onClick?: () => void }) {
  return (
    <div className={styles.card} onClick={onClick}>
      <span className={styles.icon}>⊕</span>
      <span className={styles.label}>拖拽或点击上传</span>
    </div>
  )
}
