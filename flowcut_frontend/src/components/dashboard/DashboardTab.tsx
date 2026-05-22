import { Card, Row, Col, Statistic, Table, Tag } from 'antd'
import { Column, Line } from '@ant-design/charts'
import { mockDailyMetrics, mockMaterialRanking } from '../../mocks/dashboard'
import styles from './DashboardTab.module.css'

/** 今日最新指标 */
const today = mockDailyMetrics[mockDailyMetrics.length - 1]

function MetricCard({ title, value, suffix, prefix, color }: {
  title: string
  value: string | number
  suffix?: string
  prefix?: string
  color?: string
}) {
  return (
    <Card className={styles.metricCard} bordered={false}>
      <Statistic
        title={title}
        value={value}
        suffix={suffix}
        prefix={prefix}
        valueStyle={{ color, fontSize: 24, fontWeight: 700 }}
      />
    </Card>
  )
}

export default function DashboardTab() {
  const rankingColumns = [
    { title: '排名', key: 'rank', width: 60, render: (_: unknown, __: unknown, i: number) => <span style={{ fontWeight: 600, color: i < 3 ? '#2563eb' : '#94a3b8' }}>{i + 1}</span> },
    { title: '素材名称', dataIndex: 'name', key: 'name', ellipsis: true },
    { title: '类别', dataIndex: 'category', key: 'category', width: 80 },
    { title: '使用次数', dataIndex: 'usageCount', key: 'usageCount', width: 90 },
    { title: '总消耗(元)', dataIndex: 'totalCost', key: 'totalCost', width: 110, render: (v: number) => v.toLocaleString() },
    {
      title: '平均 ROI',
      dataIndex: 'avgRoi',
      key: 'avgRoi',
      width: 100,
      render: (v: number) => (
        <Tag color={v >= 3 ? 'green' : v >= 2.5 ? 'blue' : 'orange'}>{v.toFixed(2)}</Tag>
      ),
    },
  ]

  return (
    <div className={styles.tab}>
      <div className={styles.header}>
        <h2 className={styles.title}>数据看板</h2>
        <span className={styles.subtitle}>近 7 天投放数据概览（Mock）</span>
      </div>

      {/* 指标卡片行 */}
      <Row gutter={[12, 12]} className={styles.metricRow}>
        <Col span={4}><MetricCard title="日消耗" value={today.cost} suffix="元" color="#2563eb" /></Col>
        <Col span={4}><MetricCard title="展现量" value={today.impressions.toLocaleString()} color="#059669" /></Col>
        <Col span={4}><MetricCard title="点击量" value={today.clicks.toLocaleString()} color="#d97706" /></Col>
        <Col span={4}><MetricCard title="转化数" value={today.conversions.toLocaleString()} color="#dc2626" /></Col>
        <Col span={4}><MetricCard title="ROI" value={today.roi.toFixed(1)} color="#7c3aed" /></Col>
        <Col span={4}><MetricCard title="产出成片" value={today.creativeOutput} suffix="条" color="#0891b2" /></Col>
      </Row>

      <Row gutter={12} className={styles.chartRow}>
        {/* 消耗趋势柱状图 */}
        <Col span={14}>
          <Card title="日消耗趋势" bordered={false} className={styles.chartCard}>
            <Column
              data={mockDailyMetrics}
              xField="date"
              yField="cost"
              color="#2563eb"
              columnStyle={{ radius: [4, 4, 0, 0] }}
              label={{ position: 'top', style: { fontSize: 10 } }}
              height={240}
            />
          </Card>
        </Col>

        {/* ROI 趋势折线图 */}
        <Col span={10}>
          <Card title="ROI 趋势" bordered={false} className={styles.chartCard}>
            <Line
              data={mockDailyMetrics}
              xField="date"
              yField="roi"
              color="#7c3aed"
              smooth
              point={{ size: 4, style: { fill: '#7c3aed' } }}
              label={{ style: { fontSize: 10 } }}
              height={240}
            />
          </Card>
        </Col>
      </Row>

      {/* 物料消耗排行 */}
      <Card title="物料消耗排行" bordered={false} className={styles.rankCard}>
        <Table
          dataSource={mockMaterialRanking}
          columns={rankingColumns}
          rowKey="materialId"
          pagination={false}
          size="middle"
        />
      </Card>
    </div>
  )
}
