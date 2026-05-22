import styles from './Step.module.css'

export default function PlaceholderStep() {
  return (
    <div className={styles.wrap} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: 1, gap: 10 }}>
      <div style={{ fontSize: 40 }}>🚀</div>
      <div className={styles.title}>千川一键上架</div>
      <div className={styles.sub} style={{ textAlign: 'center', maxWidth: 280 }}>该功能正在开发中，即将上线。成片已保存至成片库，可手动上架千川。</div>
    </div>
  )
}
