import { useState } from 'react'
import { Upload, Card, Button, message } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useGenerateStore } from '../../../stores/generateStore'
import { scriptApi } from '../../../api/script'
import styles from './Step.module.css'

const TENANT_KEY = 'flowcut'

const STATUS_LABEL: Record<string, string> = {
  uploading:   '正在上传视频…',
  processing:  '正在拆解分镜（Gemini）…',
  decomposing: '正在拆解分镜（Gemini）…',
}

export default function UploadStep() {
  const { startDecomposeFlow, decomposeStatus, decomposeError } = useGenerateStore()
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)

  const isRunning = decomposeStatus !== 'idle' && decomposeStatus !== 'error'

  function handleFile(file: File) {
    if (isRunning) return false
    startDecomposeFlow(file)
    return false
  }

  async function handleCreateBlankScript() {
    if (creating) return
    setCreating(true)
    try {
      const resp = await scriptApi.upload(TENANT_KEY, [{ visual: '新片段', copy: '' }])
      navigate(`/scripts/${resp.script_id}`)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '创建脚本失败'
      message.error(msg)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.title}>上传爆款视频</div>
      <div className={styles.sub}>
        上传一条 30-40 秒的爆款视频，Agent 将自动拆解分镜并生成差异化脚本。
      </div>

      <Upload.Dragger
        accept="video/*"
        beforeUpload={handleFile}
        showUploadList={false}
        disabled={isRunning}
        style={{ borderRadius: 10 }}
      >
        <p style={{ fontSize: 32 }}>{isRunning ? '⏳' : '🎬'}</p>
        {isRunning ? (
          <>
            <p style={{ fontSize: 14, fontWeight: 600, color: '#0f172a', margin: '8px 0 4px' }}>
              {STATUS_LABEL[decomposeStatus] ?? '处理中…'}
            </p>
            <p style={{ fontSize: 12, color: '#94a3b8' }}>请稍候，这可能需要 1-2 分钟</p>
          </>
        ) : (
          <>
            <p style={{ fontSize: 14, fontWeight: 600, color: '#0f172a', margin: '8px 0 4px' }}>
              拖拽视频文件到此处，或点击上传
            </p>
            <p style={{ fontSize: 12, color: '#94a3b8' }}>支持 MP4、MOV，建议 30-40 秒</p>
          </>
        )}
      </Upload.Dragger>

      {decomposeStatus === 'error' && (
        <div style={{ marginTop: 12, padding: '8px 12px', background: '#fee2e2', borderRadius: 8, fontSize: 12, color: '#dc2626' }}>
          {decomposeError}
        </div>
      )}

      <Card title="直接编写脚本" style={{ marginTop: 16 }}>
        <p style={{ minHeight: 40, color: '#666', margin: '0 0 12px' }}>
          跳过拆镜，手动填写画面与文案，直接进入素材匹配
        </p>
        <Button block onClick={handleCreateBlankScript} loading={creating}>
          新建空脚本
        </Button>
      </Card>
    </div>
  )
}
