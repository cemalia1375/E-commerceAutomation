import ChatPanel from './ChatPanel'
import UploadEntry from './UploadEntry'
import styles from './GenerateTab.module.css'

export default function GenerateTab() {
  return (
    <div className={styles.layout}>
      <ChatPanel />
      <UploadEntry />
    </div>
  )
}
