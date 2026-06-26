import { useState } from 'react'
import {
  Card,
  Modal,
  Upload,
  Button,
  Input,
  Form,
  message,
  Spin,
} from 'antd'
import type { UploadFile, RcFile } from 'antd/es/upload/interface'
import { useNavigate } from 'react-router-dom'
import { uploadReferenceVideo } from '../../api/referenceVideos'
import { scriptApi } from '../../api/script'
import { useAuthStore } from '../../stores/authStore'
import type { ScriptSegment } from '../../types/script'
import ExistingScriptsModal from './ExistingScriptsModal'
import HighlightUploadModal from './HighlightUploadModal'

interface ParsedSegment {
  visual: string
  copy: string
}

function parseSegmentsText(text: string): ParsedSegment[] {
  const lines = text
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.length > 0)
  const segs: ParsedSegment[] = []
  for (const line of lines) {
    const parts = line.split('|').map((p) => p.trim())
    if (parts.length < 2) {
      throw new Error(`格式错误，每行需要 "visual | copy"：${line}`)
    }
    segs.push({ visual: parts[0], copy: parts.slice(1).join(' | ') })
  }
  if (segs.length === 0) {
    throw new Error('至少输入一段内容')
  }
  return segs
}

function toSegmentPayload(parsed: ParsedSegment[]): Partial<ScriptSegment>[] {
  return parsed.map((s, idx) => ({
    idx,
    start_time: 0,
    end_time: 0,
    visual: s.visual,
    copy: s.copy,
  }))
}

export default function UploadEntry() {
  const TENANT_KEY = useAuthStore((s) => s.user?.tenantKey) ?? 'flowcut'
  const navigate = useNavigate()

  const [videoFile, setVideoFile] = useState<UploadFile | null>(null)
  const [product, setProduct] = useState('')
  const [videoUploading, setVideoUploading] = useState(false)
  const [highlightModalOpen, setHighlightModalOpen] = useState(false)

  const [scriptModalOpen, setScriptModalOpen] = useState(false)
  const [segmentsText, setSegmentsText] = useState('')
  const [scriptCreating, setScriptCreating] = useState(false)

  const [existingOpen, setExistingOpen] = useState(false)

  const handleVideoUpload = async () => {
    if (!videoFile) {
      message.warning('请先选择视频文件')
      return
    }
    const raw = videoFile.originFileObj as File | undefined
    if (!raw) {
      message.error('文件读取失败')
      return
    }
    setVideoUploading(true)
    try {
      const resp = await uploadReferenceVideo(
        TENANT_KEY,
        raw,
        product.trim() || undefined,
      )
      if (!resp.script_id) {
        throw new Error('后端未返回 script_id')
      }
      message.success('上传成功，跳转到工作区')
      navigate(`/workspace/${resp.script_id}`)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '上传失败'
      message.error(`上传失败：${msg}`)
    } finally {
      setVideoUploading(false)
    }
  }

  const handleScriptSubmit = async () => {
    let parsed: ParsedSegment[]
    try {
      parsed = parseSegmentsText(segmentsText)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '格式错误'
      message.error(msg)
      return
    }
    setScriptCreating(true)
    try {
      const resp = await scriptApi.upload(TENANT_KEY, toSegmentPayload(parsed))
      message.success('脚本创建成功')
      setScriptModalOpen(false)
      navigate(`/workspace/${resp.script_id}`)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '创建失败'
      message.error(`创建失败：${msg}`)
    } finally {
      setScriptCreating(false)
    }
  }

  return (
    <div
      style={{
        flex: 1,
        padding: 32,
        overflow: 'auto',
        background: '#f8fafc',
      }}
    >
      <div
        style={{
          maxWidth: 960,
          margin: '0 auto',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: 20,
        }}
      >
        <Card title="上传爆款视频" hoverable>
          <Spin spinning={videoUploading}>
            <Form layout="vertical">
              <Form.Item label="视频文件">
                <Upload
                  beforeUpload={(file: RcFile) => {
                    setVideoFile({
                      uid: file.uid,
                      name: file.name,
                      originFileObj: file,
                      size: file.size,
                      type: file.type,
                    })
                    return false
                  }}
                  onRemove={() => setVideoFile(null)}
                  fileList={videoFile ? [videoFile] : []}
                  maxCount={1}
                  accept="video/*"
                >
                  <Button>选择视频</Button>
                </Upload>
              </Form.Item>
              <Form.Item label="产品（可选）">
                <Input
                  value={product}
                  onChange={(e) => setProduct(e.target.value)}
                  placeholder="留空则用「通用」"
                />
              </Form.Item>
              <Button
                type="primary"
                block
                onClick={handleVideoUpload}
                disabled={!videoFile}
                loading={videoUploading}
              >
                上传并拆镜
              </Button>
            </Form>
          </Spin>
        </Card>

        <Card title="上传提取高光视频" hoverable>
          <p style={{ color: '#64748b', fontSize: 13, marginBottom: 12 }}>
            上传原片视频，放入高光资产库。支持单集、多集或 zip 文件夹批量上传。
          </p>
          <Button type="primary" block onClick={() => setHighlightModalOpen(true)}>
            上传视频
          </Button>
          <HighlightUploadModal
            open={highlightModalOpen}
            onClose={() => setHighlightModalOpen(false)}
            onSuccess={() => setHighlightModalOpen(false)}
          />
        </Card>

        <Card title="上传脚本" hoverable>
          <p style={{ color: '#64748b', fontSize: 13, marginBottom: 12 }}>
            手工撰写或粘贴文案，自己定义每段视觉与文案。
          </p>
          <Button type="primary" block onClick={() => setScriptModalOpen(true)}>
            撰写脚本
          </Button>
        </Card>

        <Card title="打开已有脚本" hoverable>
          <p style={{ color: '#64748b', fontSize: 13, marginBottom: 12 }}>
            查看已创建的草稿、已确认或处理中的脚本。
          </p>
          <Button block onClick={() => setExistingOpen(true)}>
            浏览列表
          </Button>
        </Card>
      </div>

      <Modal
        open={scriptModalOpen}
        title="撰写脚本"
        onCancel={() => setScriptModalOpen(false)}
        onOk={handleScriptSubmit}
        confirmLoading={scriptCreating}
        okText="创建并进入工作区"
        width={640}
        destroyOnHidden
      >
        <p style={{ color: '#64748b', fontSize: 13 }}>
          每行一段，格式：<code>visual | copy</code>
        </p>
        <Input.TextArea
          value={segmentsText}
          onChange={(e) => setSegmentsText(e.target.value)}
          rows={10}
          placeholder={
            '产品近景特写 | 你还在为油皮烦恼吗？\n手部使用演示 | 一抹就能控油 8 小时'
          }
        />
      </Modal>

      <ExistingScriptsModal
        open={existingOpen}
        tenantKey={TENANT_KEY}
        onClose={() => setExistingOpen(false)}
      />
    </div>
  )
}
