import { Drawer, Descriptions, Table, Tag, Statistic, Row, Col } from 'antd'
import { useDetailDrawerStore } from '../../stores/detailDrawerStore'
import { useMaterialStore } from '../../stores/materialStore'
import { mockMaterialUsages } from '../../mocks/usages'
import { useCreativeStore } from '../../stores/creativeStore'
import MediaPreview from '../common/MediaPreview'
import type { Creative, MaterialUsage } from '../../types'

const STATUS_COLORS: Record<string, string> = {
  ACTIVE: 'green',
  PENDING: 'blue',
  DRAFT: 'default',
}

export default function MaterialDetailDrawer() {
  const { selectedMaterial, closeMaterialDetail } = useDetailDrawerStore()
  const { materials } = useMaterialStore()
  const { creatives } = useCreativeStore()

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

  return (
    <Drawer
      title={material?.name ?? '素材详情'}
      open={!!material}
      onClose={closeMaterialDetail}
      width={720}
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
