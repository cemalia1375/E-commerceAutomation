import ChatPanel from './ChatPanel'
import ContentPanel from './ContentPanel'
import styles from './GenerateTab.module.css'

export default function GenerateTab() {
  return (
    <div className={styles.layout}>
      <ChatPanel />
      <ContentPanel />
    </div>
  )
}
