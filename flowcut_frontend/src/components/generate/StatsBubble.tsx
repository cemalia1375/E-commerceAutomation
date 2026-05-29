import { Statistic, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { ToolResultContent } from '../../api/chat'
import styles from './StatsBubble.module.css'

interface StatsBubbleProps {
  toolName: string
  content: ToolResultContent
}

interface AccountStatsData {
  creative_count?: number
  total_cost?: number
  total_impressions?: number
  total_clicks?: number
  total_conversions?: number
  orphan_count?: number
  ctr?: number
  cvr?: number
  cpa?: number
  last_synced_at?: string | null
  requested_date_range?: string | null
}

interface CreativeSearchItem {
  creative_id: number
  script_id: number | null
  status: string
  ref_video_name: string | null
  product: string | null
  qc_material_id: string | null
  qc_synced_at: string | null
  has_qc_data: boolean
}

interface MaterialSearchItem {
  material_id: number
  name: string
  category: string | null
  product: string | null
  scene_role: string | null
  status: string
  usage_count: number
}

type SearchItem = CreativeSearchItem | MaterialSearchItem

interface SearchListData {
  items: SearchItem[]
  count: number
  query: string
  product_filter?: string | null
}

function isMaterialItem(item: SearchItem): item is MaterialSearchItem {
  return 'material_id' in item
}

function isObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function formatNumber(value: unknown, fractionDigits = 0): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return value.toLocaleString(undefined, {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })
}

function formatPercent(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(2)}%`
}

function formatCurrency(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `¥${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function pickMetric(
  data: Record<string, unknown>,
  totalKey: string,
  qcKey: string,
): unknown {
  const total = data[totalKey]
  if (total !== undefined && total !== null) return total
  return data[qcKey]
}

function StatsCard({ title, data }: { title?: string; data: Record<string, unknown> }) {
  const isAccount = 'creative_count' in data
  const isMaterial = 'used_in_creatives' in data
  const account = data as AccountStatsData
  const ratios = data as { ctr?: number; cvr?: number; cpa?: number }
  const orphanCount = isAccount ? account.orphan_count : undefined
  const usedInCreatives = isMaterial
    ? Number((data as { used_in_creatives?: number }).used_in_creatives ?? 0)
    : undefined

  return (
    <div className={styles.card}>
      {title && <div className={styles.cardTitle}>{title}</div>}
      <div className={styles.grid}>
        {isAccount && (
          <Statistic title="成片数" value={formatNumber(account.creative_count)} />
        )}
        {isMaterial && (
          <Statistic title="用于成片" value={formatNumber(usedInCreatives)} />
        )}
        <Statistic
          title="总消耗"
          value={formatCurrency(pickMetric(data, 'total_cost', 'qc_cost'))}
        />
        <Statistic
          title="曝光"
          value={formatNumber(pickMetric(data, 'total_impressions', 'qc_impressions'))}
        />
        <Statistic
          title="点击"
          value={formatNumber(pickMetric(data, 'total_clicks', 'qc_clicks'))}
        />
        <Statistic
          title="转化"
          value={formatNumber(pickMetric(data, 'total_conversions', 'qc_conversions'))}
        />
        <Statistic title="点击率" value={formatPercent(ratios.ctr)} />
        <Statistic title="转化率" value={formatPercent(ratios.cvr)} />
        <Statistic title="转化成本" value={formatCurrency(ratios.cpa)} />
        {typeof orphanCount === 'number' && orphanCount > 0 && (
          <Statistic title="未匹配数据" value={formatNumber(orphanCount)} />
        )}
      </div>
    </div>
  )
}

function CreativeTable({
  query,
  count,
  items,
}: {
  query: string
  count: number
  items: CreativeSearchItem[]
}) {
  const columns: ColumnsType<CreativeSearchItem> = [
    { title: '#', dataIndex: 'creative_id', width: 60 },
    {
      title: '产品 / 视频',
      key: 'name',
      render: (_, row) => (
        <div className={styles.tableName}>
          <div>{row.product || '—'}</div>
          {row.ref_video_name && (
            <div className={styles.tableSubName}>{row.ref_video_name}</div>
          )}
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (status: string, row) => (
        <span>
          <Tag>{status}</Tag>
          {row.has_qc_data ? <Tag color="green">有数据</Tag> : null}
        </span>
      ),
    },
  ]
  return (
    <div className={styles.card}>
      <div className={styles.cardTitle}>匹配「{query}」共 {count} 条成片</div>
      <Table<CreativeSearchItem>
        size="small"
        pagination={false}
        rowKey="creative_id"
        columns={columns}
        dataSource={items}
      />
    </div>
  )
}

function MaterialTable({
  query,
  count,
  items,
  productFilter,
}: {
  query: string
  count: number
  items: MaterialSearchItem[]
  productFilter?: string | null
}) {
  const columns: ColumnsType<MaterialSearchItem> = [
    { title: '#', dataIndex: 'material_id', width: 60 },
    {
      title: '素材',
      key: 'name',
      render: (_, row) => (
        <div className={styles.tableName}>
          <div>{row.name}</div>
          {(row.product || row.scene_role) && (
            <div className={styles.tableSubName}>
              {row.product || '—'} · {row.scene_role || '—'}
            </div>
          )}
        </div>
      ),
    },
    {
      title: '使用',
      dataIndex: 'usage_count',
      width: 70,
      render: (count: number) => `${count} 次`,
    },
  ]
  return (
    <div className={styles.card}>
      <div className={styles.cardTitle}>
        匹配「{query}」{productFilter ? `（产品：${productFilter}）` : ''}
        共 {count} 条素材
      </div>
      <Table<MaterialSearchItem>
        size="small"
        pagination={false}
        rowKey="material_id"
        columns={columns}
        dataSource={items}
      />
    </div>
  )
}

function SearchTable({ data }: { data: SearchListData }) {
  const items = data.items
  if (items.length === 0) {
    return (
      <div className={styles.card}>
        <div className={styles.cardTitle}>未匹配到「{data.query}」</div>
      </div>
    )
  }
  if (isMaterialItem(items[0])) {
    return (
      <MaterialTable
        query={data.query}
        count={data.count}
        items={items as MaterialSearchItem[]}
        productFilter={data.product_filter}
      />
    )
  }
  return (
    <CreativeTable
      query={data.query}
      count={data.count}
      items={items as CreativeSearchItem[]}
    />
  )
}

export default function StatsBubble({ toolName, content }: StatsBubbleProps) {
  const hint = content.ui_hint
  const renderAs = hint?.render_as ?? 'none'
  if (renderAs === 'none' || renderAs === 'text') return null
  if (!isObject(content.data)) return null

  const titleParts: string[] = []
  if (hint?.title) titleParts.push(hint.title)

  return (
    <div className={styles.wrap}>
      {renderAs === 'stats_card' && (
        <StatsCard title={hint?.title} data={content.data} />
      )}
      {renderAs === 'table' && Array.isArray((content.data as { items?: unknown }).items) && (
        <SearchTable data={content.data as unknown as SearchListData} />
      )}
      <div className={styles.metaRow}>
        <span className={styles.toolTag}>{toolName}</span>
        {content.source === 'snapshot_only' && (
          <Tag color="blue">累计快照</Tag>
        )}
        {content.warning && <span className={styles.warning}>⚠ {content.warning}</span>}
      </div>
    </div>
  )
}
