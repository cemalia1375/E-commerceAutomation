import { useEffect, useState } from 'react'
import { Modal, Upload, AutoComplete, message } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { UploadFile, RcFile } from 'antd/es/upload/interface'
import {
  listHighlightAssets,
  uploadHighlightAsset,
  uploadHighlightZip,
} from '../../api/highlightAssets'
import { useAuthStore } from '../../stores/authStore'

interface Props {
  open: boolean
  onClose: () => void
  onSuccess: () => void
}

function parseEpisodeNo(filename: string): number | undefined {
  const base = filename.replace(/\.[^.]+$/, '')
  const m = base.match(/(\d+)/)
  return m ? parseInt(m[1], 10) : undefined
}

export default function HighlightUploadModal({ open, onClose, onSuccess }: Props) {
  const TENANT_KEY = useAuthStore((s) => s.user?.tenantKey) ?? 'flowcut'
  const [busy, setBusy] = useState(false)
  const [videoFiles, setVideoFiles] = useState<UploadFile[]>([])
  const [zipFile, setZipFile] = useState<File | null>(null)
  const [dramaName, setDramaName] = useState('')
  const [dramaOptions, setDramaOptions] = useState<{ value: string }[]>([])

  const isZip = zipFile !== null
  const hasFiles = isZip || videoFiles.length > 0

  useEffect(() => {
    if (!open) return
    setVideoFiles([])
    setZipFile(null)
    setDramaName('')
    listHighlightAssets(TENANT_KEY, { assetType: 'episode_source' })
      .then((assets) => {
        const names = Array.from(
          new Set(assets.map((a) => a.dramaName).filter(Boolean) as string[]),
        )
        setDramaOptions(names.map((n) => ({ value: n })))
      })
      .catch(() => {})
  }, [open, TENANT_KEY])

  const handleBeforeUpload = (file: RcFile) => {
    if (file.name.toLowerCase().endsWith('.zip')) {
      setZipFile(file)
      setVideoFiles([])
      setDramaName('')
    } else {
      setZipFile(null)
      setVideoFiles((prev) => [
        ...prev,
        { uid: file.uid, name: file.name, originFileObj: file, size: file.size, type: file.type },
      ])
    }
    return false
  }

  const handleSubmit = async () => {
    if (!hasFiles) {
      message.warning('请选择视频文件或 zip 压缩包')
      return
    }
    if (!isZip && !dramaName.trim()) {
      message.warning('请填写或选择剧名')
      return
    }
    setBusy(true)
    try {
      if (isZip) {
        const result = await uploadHighlightZip(TENANT_KEY, zipFile!)
        const dramaLabel = result.dramaNames.join('、')
        message.success(`已导入 ${result.created} 个视频到「${dramaLabel}」`)
      } else {
        const drama = dramaName.trim()
        for (const item of videoFiles) {
          const raw = item.originFileObj as File
          await uploadHighlightAsset(TENANT_KEY, raw, {
            assetType: 'episode_source',
            dramaName: drama,
            episodeNo: parseEpisodeNo(item.name),
          })
        }
        message.success(`已上传 ${videoFiles.length} 个视频到「${drama}」`)
      }
      onSuccess()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '上传失败'
      message.error(msg)
    } finally {
      setBusy(false)
    }
  }

  const displayFileList: UploadFile[] = isZip
    ? [{ uid: 'zip', name: zipFile!.name, status: 'done' }]
    : videoFiles

  return (
    <Modal
      title="上传原片视频"
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={busy}
      okText="开始上传"
      okButtonProps={{ disabled: !hasFiles || (!isZip && !dramaName.trim()) }}
      destroyOnClose
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Upload.Dragger
          multiple
          accept="video/*,.zip"
          beforeUpload={handleBeforeUpload}
          onRemove={(f) => {
            if (isZip) {
              setZipFile(null)
            } else {
              setVideoFiles((prev) => prev.filter((item) => item.uid !== f.uid))
            }
          }}
          fileList={displayFileList}
        >
          <p style={{ margin: 0 }}>
            <InboxOutlined style={{ fontSize: 28, color: '#2563eb' }} />
          </p>
          <p style={{ margin: '4px 0', fontSize: 13 }}>拖拽或点击选择视频文件（支持多选）</p>
          <p style={{ margin: 0, fontSize: 11, color: '#999' }}>
            也可上传 .zip 压缩包，文件夹名自动识别为剧名
          </p>
        </Upload.Dragger>

        {isZip && (
          <div
            style={{
              background: '#f0f9ff',
              border: '1px solid #bae6fd',
              padding: '8px 12px',
              borderRadius: 6,
              fontSize: 13,
              color: '#0369a1',
            }}
          >
            将自动从 zip 内文件夹名读取剧名，集数从文件名数字解析
          </div>
        )}

        {!isZip && videoFiles.length > 0 && (
          <div>
            <div style={{ fontSize: 12, marginBottom: 4 }}>
              剧名 <span style={{ color: '#e53e3e' }}>*</span>
            </div>
            <AutoComplete
              style={{ width: '100%' }}
              options={dramaOptions}
              value={dramaName}
              onChange={(v) => setDramaName(v)}
              placeholder="输入剧名或从已有剧名选择"
              filterOption={(input, option) =>
                (option?.value as string).toLowerCase().includes(input.toLowerCase())
              }
            />
          </div>
        )}
      </div>
    </Modal>
  )
}
