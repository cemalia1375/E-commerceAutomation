import { useEffect, useRef, useState } from 'react'
import { Button, Tooltip, message } from 'antd'
import { SyncOutlined } from '@ant-design/icons'
import { useCreativeStore } from '../../stores/creativeStore'
import { useDetailDrawerStore } from '../../stores/detailDrawerStore'
import type { CreativeStatusLabel } from '../../types'
import FilterChips from '../common/FilterChips'
import DateGroup from '../common/DateGroup'
import CreativeCard from './CreativeCard'
import styles from './CreativeLibrary.module.css'
import type { Creative } from '../../types'
import {
  triggerSync,
  getTaskStatus,
  fetchAccountSummary,
  uploadCreative,
  type AccountSummary,
} from '../../api/qianchuan'

const TENANT_KEY = import.meta.env.VITE_TENANT_KEY ?? 'flowcut'

function fmtNum(n: number | null | undefined, decimals = 0): string {
  if (n === null || n === undefined) return '—'
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

const STATUS_OPTIONS = ['全部', '投放中', '待上架', '草稿']
const POLL_INTERVAL_MS = 2_000
const POLL_TIMEOUT_MS = 120_000

function groupByDate(creatives: Creative[]) {
  const groups: Record<string, { creative: Creative; idx: number }[]> = {}
  creatives.forEach((c, idx) => {
    const d = c.createdAt.split('T')[0]
    const today = new Date().toISOString().split('T')[0]
    const label = d === today ? '今天' : d
    if (!groups[label]) groups[label] = []
    groups[label].push({ creative: c, idx })
  })
  return groups
}

export default function CreativeVideoLibrary() {
  const { filteredCreatives, activeStatus, setStatus, refetch } = useCreativeStore()
  const { openCreativeDetail } = useDetailDrawerStore()
  const [syncing, setSyncing] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [summary, setSummary] = useState<AccountSummary | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pollStartRef = useRef<number>(0)

  // 挂载时拉一次真实创意 + 账户汇总
  useEffect(() => {
    refetch()
    fetchAccountSummary(TENANT_KEY).then(setSummary).catch(() => {
      // 后端不可用 → 不展示汇总条
    })
  }, [refetch])

  const reloadSummary = () => {
    fetchAccountSummary(TENANT_KEY).then(setSummary).catch(() => {})
  }

  const stopPolling = () => {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }

  const handleUploadClick = () => {
    if (uploading) return
    fileInputRef.current?.click()
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (e.target) e.target.value = ''
    if (!file) return
    setUploading(true)
    try {
      await uploadCreative(TENANT_KEY, file)
      message.success(`成片「${file.name}」上传成功`)
      refetch()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '上传失败'
      message.error(`上传失败：${msg}`)
    } finally {
      setUploading(false)
    }
  }

  const handleSync = async () => {
    if (syncing) return
    setSyncing(true)
    let taskId: string
    try {
      const res = await triggerSync()
      taskId = res.task_id
    } catch {
      message.error('触发同步失败，请稍后重试')
      setSyncing(false)
      return
    }

    pollStartRef.current = Date.now()
    pollTimerRef.current = setInterval(async () => {
      if (Date.now() - pollStartRef.current > POLL_TIMEOUT_MS) {
        stopPolling()
        setSyncing(false)
        message.warning('同步超时，请稍后手动刷新')
        return
      }
      try {
        const task = await getTaskStatus(taskId)
        if (task.status === 'completed') {
          stopPolling()
          setSyncing(false)
          message.success('千川数据已同步')
          refetch()
          reloadSummary()
        } else if (task.status === 'failed') {
          stopPolling()
          setSyncing(false)
          message.error(task.error ?? '同步失败')
        }
      } catch {
        // 网络抖动，继续轮询
      }
    }, POLL_INTERVAL_MS)
  }

  const creatives = filteredCreatives()
  const groups = groupByDate(creatives)

  return (
    <div className={styles.layout}>
      {summary && (
        <div className={styles.summaryBar}>
          <div className={styles.summaryItem}>
            <div className={styles.summaryLabel}>总消耗</div>
            <div className={styles.summaryValue}>¥ {fmtNum(summary.totalCost, 2)}</div>
          </div>
          <div className={styles.summaryItem}>
            <div className={styles.summaryLabel}>总展示</div>
            <div className={styles.summaryValue}>{fmtNum(summary.totalImpressions)}</div>
          </div>
          <div className={styles.summaryItem}>
            <div className={styles.summaryLabel}>总点击</div>
            <div className={styles.summaryValue}>{fmtNum(summary.totalClicks)}</div>
          </div>
          <div className={styles.summaryItem}>
            <div className={styles.summaryLabel}>总转化</div>
            <div className={styles.summaryValue}>{fmtNum(summary.totalConversions)}</div>
          </div>
          <div className={styles.summaryItem}>
            <div className={styles.summaryLabel}>素材总数</div>
            <div className={styles.summaryValue}>{fmtNum(summary.creativeCount)}</div>
          </div>
        </div>
      )}
      <div className={styles.topBar}>
        <FilterChips options={STATUS_OPTIONS} active={activeStatus} onChange={(v) => setStatus(v as CreativeStatusLabel)} />
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <input
            ref={fileInputRef}
            type="file"
            accept="video/mp4,video/quicktime,video/webm,video/x-msvideo"
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
          <Button
            size="small"
            type="primary"
            loading={uploading}
            onClick={handleUploadClick}
          >
            ↑ 上传成片
          </Button>
          <Tooltip title="T+1 数据，点击立即拉一次">
            <Button
              icon={<SyncOutlined spin={syncing} />}
              size="small"
              loading={syncing}
              onClick={handleSync}
            >
              立即同步千川数据
            </Button>
          </Tooltip>
        </div>
      </div>
      <div className={styles.grid}>
        {Object.entries(groups).map(([label, items]) => (
          <DateGroup key={label} label={label}>
            <div className={styles.cardGrid}>
              {items.map(({ creative, idx }) => <CreativeCard key={creative.id} creative={creative} index={idx} onClick={openCreativeDetail} />)}
            </div>
          </DateGroup>
        ))}
      </div>
    </div>
  )
}
