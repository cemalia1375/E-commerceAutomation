import { useState } from 'react'
import { Button, Modal, Progress, message } from 'antd'
import { useScriptStore } from '../../stores/scriptStore'
import { scriptApi, taskApi } from '../../api/script'

const POLL_INTERVAL = 2000
const MAX_POLL_DURATION = 5 * 60 * 1000

interface ExportButtonProps {
  scriptId: number
  tenantKey: string
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

async function pollTask(taskId: string): Promise<string> {
  const startedAt = Date.now()
  while (Date.now() - startedAt < MAX_POLL_DURATION) {
    const t = await taskApi.get(taskId)
    if (t.status === 'succeeded' && t.result_url) return t.result_url
    if (t.status === 'failed') throw new Error(t.last_error || '任务失败')
    await new Promise((r) => setTimeout(r, POLL_INTERVAL))
  }
  throw new Error('导出耗时较久，请稍后再来')
}

export default function ExportButton({
  scriptId,
  tenantKey,
}: ExportButtonProps) {
  const { selectedMaterials } = useScriptStore()
  const [exporting, setExporting] = useState(false)
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const onClick = async () => {
    if (selectedMaterials.size === 0) {
      message.warning('至少选一个素材')
      return
    }
    setExporting(true)
    setDownloadUrl(null)
    setErrorMsg(null)
    try {
      const resp = await scriptApi.export(
        scriptId,
        [...selectedMaterials],
        tenantKey,
      )
      const url = await pollTask(resp.task_id)
      setDownloadUrl(url)
    } catch (e: unknown) {
      setErrorMsg(getErrorMessage(e))
    } finally {
      setExporting(false)
    }
  }

  return (
    <>
      <Button type="primary" onClick={onClick} disabled={exporting}>
        {exporting
          ? '导出中...'
          : `导出素材包（已选 ${selectedMaterials.size}）`}
      </Button>

      <Modal open={exporting} closable={false} footer={null} title="导出中">
        <p>请勿关闭页面...</p>
        <Progress percent={undefined} status="active" />
      </Modal>

      <Modal
        open={!!downloadUrl}
        title="导出成功"
        onCancel={() => setDownloadUrl(null)}
        footer={[
          <Button
            key="ok"
            type="primary"
            onClick={() => {
              if (downloadUrl) window.open(downloadUrl)
            }}
          >
            下载 ZIP
          </Button>,
        ]}
      >
        <p>素材包已生成。链接 24 小时有效。</p>
      </Modal>

      <Modal
        open={!!errorMsg}
        title="导出失败"
        onCancel={() => setErrorMsg(null)}
        footer={[
          <Button
            key="retry"
            onClick={() => {
              setErrorMsg(null)
              onClick()
            }}
          >
            重试
          </Button>,
          <Button key="close" onClick={() => setErrorMsg(null)}>
            关闭
          </Button>,
        ]}
      >
        <p>{errorMsg}</p>
      </Modal>
    </>
  )
}
