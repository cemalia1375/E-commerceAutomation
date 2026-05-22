import { Drawer, Descriptions, Table, Tag, Statistic, Row, Col } from 'antd'
import { useDetailDrawerStore } from '../../stores/detailDrawerStore'
import { useCreativeStore } from '../../stores/creativeStore'
import { useMaterialStore } from '../../stores/materialStore'
import { mockMaterialUsages } from '../../mocks/usages'
import type { Material, MaterialUsage } from '../../types'

const STATUS_COLORS: Record<string, string> = {
  ACTIVE: 'green',
  PENDING: 'blue',
  DRAFT: 'default',
}
const STATUS_LABELS: Record<string, string> = {
  ACTIVE: '投放中',
  PENDING: '待上架',
  DRAFT: '草稿',
}

export default function CreativeDetailDrawer() {
  const { selectedCreative, closeCreativeDetail } = useDetailDrawerStore()
  const { creatives } = useCreativeStore()
  const { materials } = useMaterialStore()

  const creative = selectedCreative
    ? creatives.find((c) => c.id === selectedCreative.id) ?? selectedCreative
    : null

  const usages = creative
    ? mockMaterialUsages.filter((u) => u.creativeId === creative.id)
    : []

  const usedMaterials: (Material & { usage: MaterialUsage })[] = usages
    .map((u) => {
      const m = materials.find((mat) => mat.id === u.materialId)
      return m ? { ...m, usage: u } : null
    })
    .filter(Boolean) as (Material & { usage: MaterialUsage })[]

  const totalCost = usages.reduce((s, u) => s + u.cost, 0)
  const totalImpressions = usages.reduce((s, u) => s + u.impressions, 0)
  const totalClicks = usages.reduce((s, u) => s + u.clicks, 0)
  const totalConversions = usages.reduce((s, u) => s + u.conversions, 0)
  const overallRoi = totalCost > 0 ? totalConversions / (totalCost / 100) : 0

  const columns = [
    { title: '素材名称', dataIndex: 'name', key: 'name', ellipsis: true },
    { title: '类别', dataIndex: 'category', key: 'category', width: 60 },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 60,
      render: (v: string) => (v === 'video' ? '视频' : v === 'image' ? '图片' : '音频'),
    },
    { title: '时长', dataIndex: 'duration', key: 'duration', width: 60, render: (v: number) => v > 0 ? `${v}s` : '-' },
    { title: '消耗(元)', dataIndex: ['usage', 'cost'], key: 'cost', width: 90, render: (v: number) => v.toLocaleString() },
    { title: '展现', dataIndex: ['usage', 'impressions'], key: 'impressions', width: 90, render: (v: number) => v.toLocaleString() },
    { title: '点击', dataIndex: ['usage', 'clicks'], key: 'clicks', width: 80, render: (v: number) => v.toLocaleString() },
    { title: '转化', dataIndex: ['usage', 'conversions'], key: 'conversions', width: 80, render: (v: number) => v.toLocaleString() },
    { title: 'ROI', dataIndex: ['usage', 'roi'], key: 'roi', width: 70, render: (v: number) => v.toFixed(1) },
  ]

  return (
    <Drawer
      title={creative?.name ?? '成片详情'}
      open={!!creative}
      onClose={closeCreativeDetail}
      width={800}
    >
      {creative && (
        <>
          <Descriptions column={2} size="small" bordered style={{ marginBottom: 24 }}>
            <Descriptions.Item label="时长">{creative.duration}s</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={STATUS_COLORS[creative.status]}>{STATUS_LABELS[creative.status] ?? creative.status}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="字幕行数">{creative.srtLineCount ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{creative.createdAt}</Descriptions.Item>
          </Descriptions>

          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={6}><Statistic title="总消耗" value={totalCost} suffix="元" precision={0} /></Col>
            <Col span={6}><Statistic title="总展现" value={totalImpressions} precision={0} /></Col>
            <Col span={6}><Statistic title="总点击" value={totalClicks} precision={0} /></Col>
            <Col span={6}><Statistic title="总转化" value={totalConversions} precision={0} /></Col>
          </Row>
          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={6}><Statistic title="整体 ROI" value={overallRoi} precision={2} /></Col>
            <Col span={6}><Statistic title="使用素材" value={usedMaterials.length} suffix="个" /></Col>
          </Row>

          <h4 style={{ marginBottom: 12, fontSize: 14, fontWeight: 600 }}>使用素材</h4>
          <Table
            dataSource={usedMaterials}
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
