import { useEffect, useState } from 'react'
import { Modal, AutoComplete, Select, Table, message } from 'antd'
import type { VideoSegment } from '../../../types'
import { getProducts } from '../../../api/products'
import { classifyReferenceVideo } from '../../../api/referenceVideos'

const TENANT_KEY = 'flowcut'
const PRESET_SCENE_ROLES = ['医生', '药材', '冲洗', '产品展示', '痛点', '美好']

interface ClassifyModalProps {
  open: boolean
  refVideoId: number | null
  segments: VideoSegment[]
  onClose: () => void
  onSuccess: (product: string) => void
}

function defaultSceneRole(category: string): string {
  if (category === '真人口播') return '医生'
  return '产品展示'
}

export default function ClassifyModal({
  open,
  refVideoId,
  segments,
  onClose,
  onSuccess,
}: ClassifyModalProps) {
  const [productOptions, setProductOptions] = useState<{ value: string }[]>([])
  const [product, setProduct] = useState('')
  const [sceneRoles, setSceneRoles] = useState<Record<number, string>>({})
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    getProducts(TENANT_KEY).then((products) => {
      setProductOptions(products.map((p) => ({ value: p })))
    })
    // Seed scene_role defaults based on category
    const defaults: Record<number, string> = {}
    segments.forEach((seg, idx) => {
      defaults[idx] = seg.sceneRole ?? defaultSceneRole(seg.category)
    })
    setSceneRoles(defaults)
    setProduct('')
  }, [open, segments])

  const handleSubmit = async () => {
    if (!product.trim()) {
      message.warning('请填写产品')
      return
    }
    if (refVideoId === null) return
    setBusy(true)
    try {
      const payload = Object.entries(sceneRoles).map(([idx, role]) => ({
        index: parseInt(idx, 10),
        sceneRole: role,
      }))
      const trimmed = product.trim()
      await classifyReferenceVideo(refVideoId, trimmed, payload)
      message.success('已开始生成子片段')
      onSuccess(trimmed)
      onClose()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '提交失败'
      message.error(msg)
    } finally {
      setBusy(false)
    }
  }

  const columns = [
    { title: '#', dataIndex: 'idx', width: 50 },
    { title: '时间', dataIndex: 'time', width: 110 },
    { title: '类型', dataIndex: 'category', width: 90 },
    { title: '描述', dataIndex: 'content', ellipsis: true },
    {
      title: '场景角色',
      dataIndex: 'role',
      width: 160,
      render: (_: unknown, row: { idx: number }) => (
        <Select
          size="small"
          style={{ width: 140 }}
          value={sceneRoles[row.idx]}
          onChange={(v) =>
            setSceneRoles((prev) => ({ ...prev, [row.idx]: v }))
          }
          showSearch
          options={PRESET_SCENE_ROLES.map((r) => ({ value: r, label: r }))}
        />
      ),
    },
  ]

  const dataSource = segments.map((seg, idx) => ({
    key: idx,
    idx,
    time: `${seg.startTime.toFixed(1)}–${seg.endTime.toFixed(1)}s`,
    category: seg.category,
    content: seg.content,
  }))

  return (
    <Modal
      title="为拆镜片段分类"
      open={open}
      width={780}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={busy}
      okText="确认并生成子片段"
      destroyOnClose
    >
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, marginBottom: 4 }}>
          产品 <span style={{ color: '#e53e3e' }}>*</span>
          <span style={{ color: '#999', fontSize: 11, marginLeft: 8 }}>
            （整段视频所有子片段使用同一产品）
          </span>
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
      <Table
        size="small"
        columns={columns}
        dataSource={dataSource}
        pagination={false}
        scroll={{ y: 320 }}
      />
    </Modal>
  )
}
