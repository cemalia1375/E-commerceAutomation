import { useGenerateStore } from '../../../stores/generateStore'
import styles from './Step.module.css'

export default function ConfirmStep() {
  const { addMessage, setStep } = useGenerateStore()

  const handleConfirm = () => {
    addMessage({ role: 'user', type: 'text', content: '确认成片，可以上架。' })
    setStep(5)
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.title}>确认成片</div>
      <div className={styles.sub}>初剪已完成，请预览并确认。SRT 字幕已自动生成。</div>
      <div className={styles.videoPreview}>
        <div style={{ width: '100%', aspectRatio: '16/9', background: 'linear-gradient(135deg, #fde68a, #f59e0b, #ef4444)', borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 40 }}>
          🎬
        </div>
        <div style={{ marginTop: 8, fontSize: 12, color: '#475569' }}>护肤品-痛点版-v1 · 36s · 18 条字幕</div>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        <button className={styles.actionBtn} onClick={handleConfirm} style={{ flex: 2 }}>确认成片 →</button>
        <button className={styles.actionBtnSecondary} style={{ flex: 1 }}>重新合成</button>
      </div>
    </div>
  )
}
