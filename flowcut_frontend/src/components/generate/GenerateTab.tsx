import ChatPanel from './ChatPanel'
import ContentPanel from './ContentPanel'
import ClassifyModal from './steps/ClassifyModal'
import styles from './GenerateTab.module.css'
import { useGenerateStore } from '../../stores/generateStore'

export default function GenerateTab() {
  const classifyModalOpen = useGenerateStore((s) => s.classifyModalOpen)
  const classifyRefVideoId = useGenerateStore((s) => s.classifyRefVideoId)
  const classifySegments = useGenerateStore((s) => s.classifySegments)
  const closeClassifyModal = useGenerateStore((s) => s.closeClassifyModal)
  const continueAfterClassify = useGenerateStore((s) => s.continueAfterClassify)

  return (
    <div className={styles.layout}>
      <ChatPanel />
      <ContentPanel />
      <ClassifyModal
        open={classifyModalOpen}
        refVideoId={classifyRefVideoId}
        segments={classifySegments}
        onClose={closeClassifyModal}
        onSuccess={(product) => continueAfterClassify(product)}
      />
    </div>
  )
}
