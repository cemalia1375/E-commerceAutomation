import { useEffect, useState } from 'react'
import { Modal, Tabs, AutoComplete, Select, Upload, message } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd'
import { useProductTreeStore } from '../../stores/productTreeStore'
import { useAuthStore } from '../../stores/authStore'
import { getProducts } from '../../api/products'
import {
  uploadMaterial,
  uploadZip,
  confirmZip,
} from '../../api/materials'
import type { ZipPreviewItem, ZipOverride } from '../../types'
import ZipPreview from './ZipPreview'

const PRESET_SCENE_ROLES = ['医生', '药材', '冲洗', '产品展示', '痛点', '美好']

interface UploadModalProps {
  open: boolean
  onClose: () => void
  onSuccess: () => void
}

export default function UploadModal({ open, onClose, onSuccess }: UploadModalProps) {
  const TENANT_KEY = useAuthStore((s) => s.user?.tenantKey) ?? 'flowcut'
  const { activeProduct, activeSceneRole } = useProductTreeStore()
  const refreshTree = useProductTreeStore((s) => s.refreshTree)
  const treeNodes = useProductTreeStore((s) => s.treeNodes)

  const [tab, setTab] = useState<'single' | 'zip'>('single')
  const [productOptions, setProductOptions] = useState<{ value: string }[]>([])
  const [product, setProduct] = useState<string>('')
  const [sceneRole, setSceneRole] = useState<string | undefined>(undefined)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)

  const [zipFile, setZipFile] = useState<File | null>(null)
  const [uploadId, setUploadId] = useState<string | null>(null)
  const [zipPreview, setZipPreview] = useState<ZipPreviewItem[] | null>(null)
  const [zipEdits, setZipEdits] = useState<Record<number, { product: string; sceneRole: string | null }>>({})

  useEffect(() => {
    if (!open) return
    getProducts(TENANT_KEY)
      .then((products) => {
        setProductOptions(products.map((p) => ({ value: p })))
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : '加载产品列表失败'
        message.error(msg)
      })
    setProduct(activeProduct ?? '')
    setSceneRole(activeSceneRole ?? undefined)
    setFile(null)
    setZipFile(null)
    setUploadId(null)
    setZipPreview(null)
    setZipEdits({})
    setTab('single')
  }, [open, activeProduct, activeSceneRole, TENANT_KEY])

  const handleSingleUpload = async () => {
    if (!file) {
      message.warning('请选择文件')
      return
    }
    if (!product.trim()) {
      message.warning('请填写产品')
      return
    }
    setBusy(true)
    try {
      await uploadMaterial(
        TENANT_KEY,
        file,
        product.trim(),
        sceneRole,
      )
      message.success('上传成功，正在处理…')
      await refreshTree(TENANT_KEY)
      onSuccess()
      onClose()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '上传失败'
      message.error(msg)
    } finally {
      setBusy(false)
    }
  }

  const handleZipParse = async (selected: File) => {
    setZipFile(selected)
    setBusy(true)
    try {
      const resp = await uploadZip(TENANT_KEY, selected)
      setUploadId(resp.uploadId)
      setZipPreview(resp.preview)
      const initialEdits: Record<number, { product: string; sceneRole: string | null }> = {}
      resp.preview.forEach((item, idx) => {
        if (item.status === 'ignored') return
        initialEdits[idx] = {
          product: item.product ?? '',
          sceneRole: item.sceneRole,
        }
      })
      setZipEdits(initialEdits)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '解析失败'
      message.error(msg)
    } finally {
      setBusy(false)
    }
  }

  const handleZipConfirm = async () => {
    if (!uploadId) return

    // Validate: all non-ignored items must have a non-empty product
    const hasEmptyProduct = Object.values(zipEdits).some((e) => !e.product.trim())
    if (hasEmptyProduct) {
      message.warning('请填写所有产品名')
      return
    }

    const overrides: ZipOverride[] = Object.entries(zipEdits).map(([idx, e]) => ({
      index: parseInt(idx, 10),
      product: e.product.trim(),
      sceneRole: e.sceneRole?.trim() || null,
    }))

    setBusy(true)
    try {
      const { materialIds } = await confirmZip(uploadId, TENANT_KEY, overrides)
      message.success(`已导入 ${materialIds.length} 个素材`)
      await refreshTree(TENANT_KEY)
      onSuccess()
      onClose()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '导入失败'
      message.error(msg)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="上传素材"
      open={open}
      onCancel={onClose}
      confirmLoading={busy}
      onOk={tab === 'single' ? handleSingleUpload : handleZipConfirm}
      okText={tab === 'single' ? '开始上传' : '确认导入'}
      okButtonProps={{ disabled: tab === 'zip' && !zipPreview }}
      destroyOnClose
    >
      <Tabs
        activeKey={tab}
        onChange={(k) => setTab(k as 'single' | 'zip')}
        items={[
          {
            key: 'single',
            label: '单文件',
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <Upload.Dragger
                  multiple={false}
                  beforeUpload={(f) => {
                    setFile(f)
                    return false
                  }}
                  fileList={file ? ([{ uid: '-1', name: file.name, status: 'done' } as UploadFile]) : []}
                  onRemove={() => setFile(null)}
                >
                  <p style={{ margin: 0 }}>
                    <InboxOutlined style={{ fontSize: 28, color: '#2563eb' }} />
                  </p>
                  <p style={{ margin: '4px 0', fontSize: 13 }}>拖拽或点击选择视频文件</p>
                </Upload.Dragger>
                <div>
                  <div style={{ fontSize: 12, marginBottom: 4 }}>
                    产品 <span style={{ color: '#e53e3e' }}>*</span>
                  </div>
                  <AutoComplete
                    style={{ width: '100%' }}
                    options={productOptions}
                    value={product}
                    onChange={(v) => setProduct(v)}
                    placeholder="从已有产品选择或输入新产品名"
                    filterOption={(input, option) =>
                      (option?.value as string).toLowerCase().includes(input.toLowerCase())
                    }
                  />
                </div>
                <div>
                  <div style={{ fontSize: 12, marginBottom: 4 }}>
                    场景角色 <span style={{ color: '#999', fontSize: 11 }}>（可选）</span>
                  </div>
                  <Select
                    style={{ width: '100%' }}
                    value={sceneRole}
                    onChange={(v) => setSceneRole(v)}
                    options={PRESET_SCENE_ROLES.map((r) => ({ value: r, label: r }))}
                    allowClear
                    placeholder="留空表示归入该产品根节点"
                  />
                </div>
              </div>
            ),
          },
          {
            key: 'zip',
            label: 'ZIP 批量',
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <Upload.Dragger
                  multiple={false}
                  accept=".zip"
                  beforeUpload={(f) => {
                    handleZipParse(f)
                    return false
                  }}
                  fileList={zipFile ? ([{ uid: '-1', name: zipFile.name, status: 'done' } as UploadFile]) : []}
                  onRemove={() => {
                    setZipFile(null)
                    setUploadId(null)
                    setZipPreview(null)
                  }}
                >
                  <p style={{ margin: 0 }}>
                    <InboxOutlined style={{ fontSize: 28, color: '#2563eb' }} />
                  </p>
                  <p style={{ margin: '4px 0', fontSize: 13 }}>拖拽或点击选择 .zip 文件</p>
                  <p style={{ margin: 0, fontSize: 11, color: '#999' }}>
                    内部目录：{'{产品}/{场景角色}/{文件}'}
                  </p>
                </Upload.Dragger>
                {zipPreview && (
                  <ZipPreview
                    preview={zipPreview}
                    edits={zipEdits}
                    onEdit={(idx, prod, role) =>
                      setZipEdits((prev) => ({ ...prev, [idx]: { product: prod, sceneRole: role } }))
                    }
                    existingTree={treeNodes}
                  />
                )}
              </div>
            ),
          },
        ]}
      />
    </Modal>
  )
}
