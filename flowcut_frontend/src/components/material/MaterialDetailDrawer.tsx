import { useState } from 'react'
import {
  Drawer,
  Descriptions,
  Table,
  Tag,
  Statistic,
  Row,
  Col,
  Button,
  Popconfirm,
  Input,
  message,
  Space,
} from 'antd'
import { useDetailDrawerStore } from '../../stores/detailDrawerStore'
import { useMaterialStore } from '../../stores/materialStore'
import { mockMaterialUsages } from '../../mocks/usages'
import { useCreativeStore } from '../../stores/creativeStore'
import MediaPreview from '../common/MediaPreview'
import {
  deleteMaterial,
  updateMaterial,
  type UpdateMaterialPatch,
} from '../../api/materials'
import type { Creative, Material, MaterialUsage } from '../../types'

const STATUS_COLORS: Record<string, string> = {
  ACTIVE: 'green',
  PENDING: 'blue',
  DRAFT: 'default',
}

type EditableField = 'name' | 'product' | 'sceneRole'

interface EditableTextProps {
  value: string | undefined
  placeholder?: string
  onSave: (next: string) => Promise<void>
}

function EditableText({ value, placeholder, onSave }: EditableTextProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value ?? '')
  const [saving, setSaving] = useState(false)

  const commit = async () => {
    const next = draft.trim()
    if (next === (value ?? '')) {
      setEditing(false)
      return
    }
    setSaving(true)
    try {
      await onSave(next)
      setEditing(false)
    } catch {
      // onSave 自行抛错 + 提示，这里仅保持编辑态
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    return (
      <span
        onClick={() => {
          setDraft(value ?? '')
          setEditing(true)
        }}
        style={{
          cursor: 'pointer',
          display: 'inline-block',
          minWidth: 80,
          color: value ? undefined : '#999',
          borderBottom: '1px dashed transparent',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.borderBottomColor = '#ccc')}
        onMouseLeave={(e) => (e.currentTarget.style.borderBottomColor = 'transparent')}
        title="点击编辑"
      >
        {value || placeholder || '点击设置'}
      </span>
    )
  }

  return (
    <Input
      autoFocus
      size="small"
      value={draft}
      disabled={saving}
      onChange={(e) => setDraft(e.target.value)}
      onPressEnter={commit}
      onBlur={commit}
      placeholder={placeholder}
      style={{ maxWidth: 240 }}
    />
  )
}

export default function MaterialDetailDrawer() {
  const { selectedMaterial, closeMaterialDetail } = useDetailDrawerStore()
  const { materials, updateMaterial: updateInStore, removeMaterial } = useMaterialStore()
  const { creatives } = useCreativeStore()
  const [deleting, setDeleting] = useState(false)

  const material = selectedMaterial
    ? materials.find((m) => m.id === selectedMaterial.id) ?? selectedMaterial
    : null

  const usages = material
    ? mockMaterialUsages.filter((u) => u.materialId === material.id)
    : []

  const relatedCreatives: (Creative & { usage: MaterialUsage })[] = usages
    .map((u) => {
      const c = creatives.find((cr) => cr.id === u.creativeId)
      return c ? { ...c, usage: u } : null
    })
    .filter(Boolean) as (Creative & { usage: MaterialUsage })[]

  const totalCost = usages.reduce((s, u) => s + u.cost, 0)
  const avgRoi = usages.length
    ? usages.reduce((s, u) => s + u.roi, 0) / usages.length
    : 0

  const columns = [
    { title: '成片名称', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (v: string) => {
        const label = { ACTIVE: '投放中', PENDING: '待上架', DRAFT: '草稿' }[v] ?? v
        return <Tag color={STATUS_COLORS[v]}>{label}</Tag>
      },
    },
    { title: '消耗(元)', dataIndex: ['usage', 'cost'], key: 'cost', width: 90, render: (v: number) => v.toLocaleString() },
    { title: '展现', dataIndex: ['usage', 'impressions'], key: 'impressions', width: 90, render: (v: number) => v.toLocaleString() },
    { title: '点击', dataIndex: ['usage', 'clicks'], key: 'clicks', width: 80, render: (v: number) => v.toLocaleString() },
    { title: '转化', dataIndex: ['usage', 'conversions'], key: 'conversions', width: 80, render: (v: number) => v.toLocaleString() },
    { title: 'ROI', dataIndex: ['usage', 'roi'], key: 'roi', width: 70, render: (v: number) => v.toFixed(1) },
  ]

  const handleDelete = async () => {
    if (!material) return
    setDeleting(true)
    try {
      await deleteMaterial(material.id)
      removeMaterial(material.id)
      message.success('素材已删除')
      closeMaterialDetail()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '删除失败'
      message.error(msg)
    } finally {
      setDeleting(false)
    }
  }

  const handleFieldSave = async (field: EditableField, next: string) => {
    if (!material) return
    const patch: UpdateMaterialPatch = {}
    if (field === 'name') {
      if (!next) {
        message.warning('名称不能为空')
        throw new Error('empty name')
      }
      patch.name = next
    } else if (field === 'product') {
      patch.product = next || null
    } else if (field === 'sceneRole') {
      patch.scene_role = next || null
    }

    try {
      const updated: Material = await updateMaterial(material.id, patch)
      updateInStore(updated)
      message.success('保存成功')
    } catch (err) {
      const msg = err instanceof Error ? err.message : '保存失败'
      message.error(msg)
      throw err
    }
  }

  return (
    <Drawer
      title={
        material ? (
          <Space>
            <span>{material.name}</span>
          </Space>
        ) : (
          '素材详情'
        )
      }
      open={!!material}
      onClose={closeMaterialDetail}
      width={720}
      extra={
        material && (
          <Popconfirm
            title="确定要永久删除该素材吗？"
            description="删除后将清除素材文件与向量数据，操作不可撤销。"
            okText="删除"
            okButtonProps={{ danger: true, loading: deleting }}
            cancelText="取消"
            onConfirm={handleDelete}
          >
            <Button danger loading={deleting}>
              删除素材
            </Button>
          </Popconfirm>
        )
      }
    >
      {material && (
        <>
          <div style={{ marginBottom: 20 }}>
            <MediaPreview
              url={material.status === 'READY' ? (material.previewUrl ?? material.ossUrl) : null}
              type={material.type}
              poster={material.thumbnailUrl}
              name={material.name}
              height={280}
              empty={
                material.status === 'PROCESSING'
                  ? '素材处理中，暂不可预览'
                  : material.status === 'FAILED'
                    ? '素材处理失败，无法预览'
                    : '无可预览资源'
              }
            />
          </div>
          <Descriptions column={2} size="small" bordered style={{ marginBottom: 24 }}>
            <Descriptions.Item label="名称" span={2}>
              <EditableText
                value={material.name}
                placeholder="素材名称"
                onSave={(v) => handleFieldSave('name', v)}
              />
            </Descriptions.Item>
            <Descriptions.Item label="产品">
              <EditableText
                value={material.product}
                placeholder="未指定"
                onSave={(v) => handleFieldSave('product', v)}
              />
            </Descriptions.Item>
            <Descriptions.Item label="场景角色">
              <EditableText
                value={material.sceneRole}
                placeholder="未分类"
                onSave={(v) => handleFieldSave('sceneRole', v)}
              />
            </Descriptions.Item>
            <Descriptions.Item label="类别">{material.category}</Descriptions.Item>
            <Descriptions.Item label="类型">{material.type === 'video' ? '视频' : material.type === 'image' ? '图片' : '音频'}</Descriptions.Item>
            <Descriptions.Item label="时长">{material.duration > 0 ? `${material.duration}s` : '-'}</Descriptions.Item>
            <Descriptions.Item label="文件大小">{(material.fileSize / 1_000_000).toFixed(1)} MB</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={material.status === 'READY' ? 'green' : material.status === 'PROCESSING' ? 'gold' : 'red'}>
                {material.status === 'READY' ? '就绪' : material.status === 'PROCESSING' ? '处理中' : '失败'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="累计使用">{material.usageCount} 次</Descriptions.Item>
          </Descriptions>

          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={6}><Statistic title="总消耗" value={totalCost} suffix="元" precision={0} /></Col>
            <Col span={6}><Statistic title="平均 ROI" value={avgRoi} precision={2} /></Col>
            <Col span={6}><Statistic title="关联成片" value={relatedCreatives.length} suffix="条" /></Col>
            <Col span={6}><Statistic title="累计使用" value={material.usageCount} suffix="次" /></Col>
          </Row>

          <h4 style={{ marginBottom: 12, fontSize: 14, fontWeight: 600 }}>关联成片</h4>
          <Table
            dataSource={relatedCreatives}
            columns={columns}
            rowKey="id"
            pagination={false}
            size="small"
          />
        </>
      )}
    </Drawer>
  )
}
