import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Checkbox, Input, Popconfirm, Select, Spin, Tag, message } from 'antd'
import {
  batchDownloadZipByKeys,
  composeHighlightCreative,
  deleteCreative,
  exportHighlightCreative,
  getTaskStatus,
  listHighlightPlanTasks,
  setCreativeConnector,
  setCreativePreroll,
  type HighlightPlanTask,
} from '../../api/qianchuan'
import { listHighlightAssets } from '../../api/highlightAssets'
import { useCreativeStore } from '../../stores/creativeStore'
import { getTenantKey } from '../../stores/authStore'
import { useUIContextStore } from '../../stores/uiContextStore'
import type { Creative, HighlightAsset } from '../../types'
import styles from './HighlightCreativeLibrary.module.css'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8001'
const POLL_INTERVAL_MS = 2500
const POLL_TIMEOUT_MS = 180000

function isHighlightCreative(creative: Creative) {
  return (
    creative.creativeType?.startsWith('highlight_') ||
    creative.creativeType === 'continuous_cross_episode'
  )
}

// 顺序预览：依次播放多段视频（剪辑 → 数字人）。首段需用户点击播放，
// 之后每段结束自动续播下一段。urls 变化时重置回第一段。
// 视频下方的圆点指示当前共几段、正在播第几段，点击可直接跳段，
// 避免与左侧单段剪辑视觉上无法区分。labels 与 urls 一一对应。
function SequentialPreview({
  urls,
  labels,
  className,
  onSegmentChange,
}: {
  urls: string[]
  labels?: string[]
  className?: string
  onSegmentChange?: (idx: number) => void
}) {
  const clips = urls
    .map((url, i) => ({ url, label: labels?.[i] ?? `第 ${i + 1} 段` }))
    .filter((c) => Boolean(c.url))
  const [idx, setIdx] = useState(0)
  const joined = clips.map((c) => c.url).join('|')
  useEffect(() => { setIdx(0); onSegmentChange?.(0) }, [joined])
  if (clips.length === 0) {
    return <div className={className}>暂无预览</div>
  }
  const current = Math.min(idx, clips.length - 1)
  return (
    <div>
      <video
        key={joined}
        className={className}
        src={clips[current].url}
        controls
        autoPlay={idx > 0}
        preload="metadata"
        onEnded={() => {
          setIdx((i) => {
            const next = i < clips.length - 1 ? i + 1 : i
            onSegmentChange?.(next)
            return next
          })
        }}
      />
      {clips.length > 1 && (
        <div className={styles.seqDots}>
          {clips.map((clip, i) => (
            <button
              key={clip.url}
              type="button"
              className={`${styles.seqDot} ${i === current ? styles.seqDotActive : ''}`}
              title={clip.label}
              aria-label={`跳到${clip.label}`}
              aria-current={i === current}
              onClick={() => { setIdx(i); onSegmentChange?.(i) }}
            />
          ))}
          <span className={styles.seqDotLabel}>
            {`${current + 1}/${clips.length} · ${clips[current].label}`}
          </span>
        </div>
      )}
    </div>
  )
}

function PrerollOverlayPreview({
  clipUrl,
  dhUrl,
  prerollUrl,
  videoClassName,
}: {
  clipUrl: string
  dhUrl: string
  prerollUrl: string | null
  videoClassName?: string
}) {
  const urls = [clipUrl, dhUrl].filter(Boolean)
  const labels = ['剪辑', '数字人'].slice(0, urls.length)
  const [segIdx, setSegIdx] = useState(0)
  const isClipSegment = segIdx === 0
  return (
    <div style={{ position: 'relative', display: 'inline-block', width: '100%' }}>
      <SequentialPreview
        urls={urls}
        labels={labels}
        className={videoClassName}
        onSegmentChange={setSegIdx}
      />
      {prerollUrl && isClipSegment && (
        <img
          src={prerollUrl}
          alt="前贴预览"
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            objectFit: 'fill',
            pointerEvents: 'none',
          }}
        />
      )}
    </div>
  )
}

function formatTime(value: number | null | undefined) {
  return typeof value === 'number' ? `${value.toFixed(2)}s` : '-'
}

function segmentPreviewUrl(creative: Creative) {
  const scriptId = creative.composePlan?.script_id
  const idx = creative.highlightReason?.idx
  if (typeof scriptId !== 'number' || typeof idx !== 'number') return null
  return `${API_BASE}/flowcut/scripts/${scriptId}/segments/${idx}/preview.mp4`
}

function statusText(creative: Creative) {
  if (creative.ossUrl) return '已合成'
  if (creative.status === 'PROCESSING') return '生成中'
  if (creative.status === 'FAILED') return '失败'
  return '待合成'
}

function AssetPreview({
  title,
  name,
  meta,
  url,
}: {
  title: string
  name: string
  meta?: string
  url?: string | null
}) {
  return (
    <div className={styles.assetItem}>
      <div className={styles.assetHeader}>{title}</div>
      {url ? (
        <video className={styles.assetVideo} src={url} controls preload="metadata" />
      ) : (
        <div className={styles.assetPlaceholder}>已入库</div>
      )}
      <div className={styles.assetName}>{name}</div>
      {meta && <div className={styles.assetMeta}>{meta}</div>}
    </div>
  )
}

export default function HighlightCreativeLibrary() {
  const { creatives, refetch } = useCreativeStore()
  const [keyword, setKeyword] = useState('')
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [activeDrama, setActiveDrama] = useState<string | null>(null)
  const setDrama = useUIContextStore((s) => s.setDrama)
  const [composingId, setComposingId] = useState<string | null>(null)
  const [exportingId, setExportingId] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [batchExporting, setBatchExporting] = useState(false)
  const [activeTasks, setActiveTasks] = useState<HighlightPlanTask[]>([])
  const [digitalHumans, setDigitalHumans] = useState<HighlightAsset[]>([])
  // 每条跨集成片要拼接的数字人选择（null = 纯片）；未设置时回退到 creative.connectorAssetId
  const [dhChoice, setDhChoice] = useState<Record<string, number | null>>({})
  const [prerollAssets, setPrerollAssets] = useState<HighlightAsset[]>([])
  const [prerollChoice, setPrerollChoice] = useState<Record<string, number | null | undefined>>({})

  const refetchTasks = useCallback(async () => {
    try {
      setActiveTasks(await listHighlightPlanTasks(getTenantKey()))
    } catch {
      // 后端不可用时保持现状，不打断 UI
    }
  }, [])

  useEffect(() => {
    void refetch()
    void refetchTasks()
    void (async () => {
      try {
        setDigitalHumans(
          await listHighlightAssets(getTenantKey(), { assetType: 'digital_human_connector' }),
        )
      } catch {
        // 数字人素材拉取失败不阻断主流程
      }
    })()
    void (async () => {
      try {
        setPrerollAssets(
          await listHighlightAssets(getTenantKey(), { assetType: 'preroll' }),
        )
      } catch {
        // 前贴素材拉取失败不阻断主流程
      }
    })()
  }, [refetch, refetchTasks])

  const rows = useMemo(
    () =>
      creatives.filter(isHighlightCreative).filter((creative) => {
        if (typeFilter !== 'all' && creative.creativeType !== typeFilter) return false
        const kw = keyword.trim().toLowerCase()
        if (!kw) return true
        return [
          creative.name,
          creative.sourceAssetName,
          creative.sourceDramaName,
          creative.connectorAssetName,
          creative.connectorRole,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
          .includes(kw)
      }),
    [creatives, typeFilter, keyword],
  )

  const dramaGroups = useMemo(() => {
    const groups: Record<string, Creative[]> = {}
    for (const c of rows) {
      const key = c.sourceDramaName || '未命名剧集'
      if (!groups[key]) groups[key] = []
      groups[key].push(c)
    }
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b, 'zh-Hans-CN'))
  }, [rows])

  // 在途规划任务（"生成中"占位）：剔除已建出真成片的任务（按 batchId 去重），
  // 并套用与成片相同的剧名搜索 / 类型筛选（跨集高光只在 all / continuous_cross_episode 下显示）。
  const pendingTasks = useMemo(() => {
    if (typeFilter !== 'all' && typeFilter !== 'continuous_cross_episode') return []
    const builtBatchIds = new Set(
      creatives.map((c) => c.batchId).filter((b): b is string => Boolean(b)),
    )
    const kw = keyword.trim().toLowerCase()
    return activeTasks.filter((t) => {
      if (t.batchId && builtBatchIds.has(t.batchId)) return false
      if (kw && !(t.dramaName || '').toLowerCase().includes(kw)) return false
      return true
    })
  }, [activeTasks, creatives, typeFilter, keyword])

  const pendingByDrama = useMemo(() => {
    const groups: Record<string, HighlightPlanTask[]> = {}
    for (const t of pendingTasks) {
      const key = t.dramaName || '未命名剧集'
      if (!groups[key]) groups[key] = []
      groups[key].push(t)
    }
    return groups
  }, [pendingTasks])

  // 剧名入口：成片分组 ∪ 仅有在途任务的剧（这样规划阶段也能在入口层看到）
  const entryNames = useMemo(() => {
    const names = new Set<string>()
    for (const [name] of dramaGroups) names.add(name)
    for (const name of Object.keys(pendingByDrama)) names.add(name)
    return Array.from(names).sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'))
  }, [dramaGroups, pendingByDrama])

  const rowsByDrama = useMemo(
    () => new Map(dramaGroups),
    [dramaGroups],
  )

  // 仅当「有正在合成中(PROCESSING)的成片」或「有在途规划任务」时才每 3 秒轮询。
  // 注意不能把 PENDING(待合成)纳入：跨集高光的 PENDING 是等用户手动点合成的静止态，
  // 不会自行变化，纳入会导致永不停歇的轮询（每次 refetch 重签 oss_url → 视频闪烁）。
  const hasProcessing = rows.some((c) => c.status === 'PROCESSING')
  const shouldPoll = hasProcessing || activeTasks.length > 0
  useEffect(() => {
    if (!shouldPoll) return
    const id = window.setInterval(() => {
      void refetch()
      void refetchTasks()
    }, 3000)
    return () => { window.clearInterval(id) }
  }, [shouldPoll, refetch, refetchTasks])

  useEffect(() => {
    setDrama(activeDrama)
  }, [activeDrama, setDrama])

  const drilledRows = activeDrama
    ? rows.filter((c) => (c.sourceDramaName || '未命名剧集') === activeDrama)
    : []
  const drilledTasks = activeDrama ? pendingByDrama[activeDrama] ?? [] : []

  const exportableInView = useMemo(
    () => drilledRows.filter((c) => Boolean(c.ossUrl)),
    [drilledRows],
  )

  // 同一 oss_key 复用首个签名 URL：后端每次 list 都会重签 presigned oss_url，
  // 若直接喂给 <video src> 会每轮刷新都变 → 视频重载闪烁。按 oss_key 锁定首个 URL。
  const urlCacheRef = useRef<Record<string, string>>({})
  const stableVideoUrl = (creative: Creative): string => {
    if (!creative.ossUrl) return ''
    const key = creative.ossKey || creative.id
    const cache = urlCacheRef.current
    if (!cache[key]) cache[key] = creative.ossUrl
    return cache[key]
  }

  const connectorOf = (creative: Creative): number | null =>
    creative.id in dhChoice ? dhChoice[creative.id] : (creative.connectorAssetId ?? null)

  const prerollOf = (creative: Creative): number | null => {
    if (creative.id in prerollChoice) return prerollChoice[creative.id] ?? null
    if (creative.prerollAssetId != null) return creative.prerollAssetId
    return prerollAssets[0]?.id ?? null
  }

  const handleSelectConnector = async (creative: Creative, connectorId: number | null) => {
    setDhChoice((prev) => ({ ...prev, [creative.id]: connectorId }))
    try {
      await setCreativeConnector(creative.id, connectorId)
      await refetch()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存数字人选择失败')
    }
  }

  const handleSelectPreroll = async (creative: Creative, prerollId: number | null) => {
    setPrerollChoice((prev) => ({ ...prev, [creative.id]: prerollId }))
    try {
      await setCreativePreroll(creative.id, prerollId)
      await refetch()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存前贴选择失败')
    }
  }

  const handleCompose = async (creative: Creative) => {
    setComposingId(creative.id)
    try {
      const { taskId } = await composeHighlightCreative(creative.id)
      message.success('已开始生成视频')
      await refetch()
      const startedAt = Date.now()
      while (Date.now() - startedAt < POLL_TIMEOUT_MS) {
        await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS))
        const task = await getTaskStatus(taskId)
        await refetch()
        if (task.status === 'succeeded' || task.status === 'completed' || task.status === 'noop') {
          message.success('组合视频已生成')
          return
        }
        if (task.status === 'failed') {
          message.error(task.error || '组合视频生成失败')
          return
        }
      }
      message.warning('组合视频仍在生成，请稍后刷新查看')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '触发合成失败')
    } finally {
      setComposingId(null)
    }
  }

  const handleDelete = async (creative: Creative) => {
    try {
      await deleteCreative(creative.id)
      message.success('已删除')
      await refetch()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  function triggerBrowserDownload(url: string): void {
    const a = document.createElement('a')
    a.href = url
    a.rel = 'noopener noreferrer'
    document.body.appendChild(a)
    a.click()
    requestAnimationFrame(() => { if (a.parentNode) a.parentNode.removeChild(a) })
  }

  const toggleSelected = (id: string, checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  // 若前端的前贴选择与 DB 不一致，先持久化再导出
  const ensurePrerollSaved = async (creative: Creative) => {
    const resolved = prerollOf(creative)
    if (resolved !== (creative.prerollAssetId ?? null)) {
      await setCreativePreroll(creative.id, resolved)
    }
  }

  const handleExport = async (creative: Creative) => {
    if (!creative.ossUrl) return
    setExportingId(creative.id)
    try {
      // 没选数字人且没选前贴：后端 302 → presigned URL（含 attachment 文件名），用 <a> 触发下载
      if (connectorOf(creative) == null && prerollOf(creative) == null) {
        triggerBrowserDownload(`${API_BASE}/flowcut/creatives/${creative.id}/download`)
        return
      }
      await ensurePrerollSaved(creative)
      // 选了数字人或前贴：后端 ffmpeg 拼接任务 → 轮询 → 下载产物
      const { taskId } = await exportHighlightCreative(creative.id)
      message.loading({ content: '正在拼接导出…', key: `export-${creative.id}`, duration: 0 })
      const startedAt = Date.now()
      while (Date.now() - startedAt < POLL_TIMEOUT_MS) {
        await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS))
        const task = await getTaskStatus(taskId)
        if (task.status === 'succeeded' || task.status === 'completed') {
          message.destroy(`export-${creative.id}`)
          if (!task.resultUrl) throw new Error('导出完成但缺少下载链接')
          triggerBrowserDownload(task.resultUrl)
          message.success('已导出')
          return
        }
        if (task.status === 'failed') {
          message.destroy(`export-${creative.id}`)
          throw new Error(task.error || '导出失败')
        }
      }
      message.destroy(`export-${creative.id}`)
      message.warning('导出仍在进行，请稍后重试')
    } catch (err) {
      message.destroy(`export-${creative.id}`)
      message.error(err instanceof Error ? `导出失败：${err.message}` : '导出失败')
    } finally {
      setExportingId(null)
    }
  }

  const handleBatchExport = async () => {
    const exportable = drilledRows.filter((c) => selectedIds.has(c.id) && Boolean(c.ossUrl))
    if (exportable.length === 0) return

    setBatchExporting(true)
    const msgKey = 'batch-zip'
    try {
      message.loading({ content: `正在合成导出 ${exportable.length} 个成片…`, key: msgKey, duration: 0 })

      // 有数字人或前贴的需先 ffmpeg 合成，其余直接使用已有 oss_key
      const needsCompose = exportable.filter((c) => connectorOf(c) != null || prerollOf(c) != null)
      const simple = exportable.filter((c) => connectorOf(c) == null && prerollOf(c) == null)

      // 先确保所有前贴选择已写入 DB，再并行触发合成任务
      await Promise.all(needsCompose.map((c) => ensurePrerollSaved(c)))
      const composeTasks = await Promise.all(
        needsCompose.map(async (c) => {
          const { taskId } = await exportHighlightCreative(c.id)
          return { creative: c, taskId }
        }),
      )

      // 轮询直到所有合成任务完成，收集 oss_key
      const composeResults: Array<{ creative: typeof exportable[0]; ossKey: string }> = []
      if (composeTasks.length > 0) {
        const startedAt = Date.now()
        const pending = new Map(composeTasks.map(({ creative, taskId }, i) => [i, { creative, taskId }]))
        while (pending.size > 0 && Date.now() - startedAt < POLL_TIMEOUT_MS) {
          await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS))
          for (const [i, { creative, taskId }] of Array.from(pending.entries())) {
            const task = await getTaskStatus(taskId)
            if (task.status === 'succeeded' || task.status === 'completed') {
              if (task.resultOssKey) composeResults.push({ creative, ossKey: task.resultOssKey })
              pending.delete(i)
            } else if (task.status === 'failed') {
              pending.delete(i)
            }
          }
        }
      }

      // 组装所有 items：合成产物 + 纯片
      const buildFilename = (c: typeof exportable[0], suffix = '') => {
        const drama = c.sourceDramaName || c.sourceAssetName || c.name || '高光'
        const episode = c.sourceEpisodeNo ? `_第${c.sourceEpisodeNo}集` : ''
        return `${drama}${episode}_${c.id}${suffix}.mp4`
      }

      const items: Array<{ ossKey: string; filename: string }> = [
        ...composeResults.map(({ creative, ossKey }) => ({
          ossKey,
          filename: buildFilename(creative, '_导出'),
        })),
        ...simple
          .filter((c) => Boolean(c.ossKey))
          .map((c) => ({ ossKey: c.ossKey, filename: buildFilename(c) })),
      ]

      if (items.length === 0) {
        throw new Error('所有成片合成均失败，无法打包')
      }

      const { downloadUrl, count } = await batchDownloadZipByKeys(getTenantKey(), items)
      message.destroy(msgKey)
      triggerBrowserDownload(downloadUrl)
      message.success(`已打包 ${count} 个成片，开始下载`)
      setSelectedIds(new Set())
    } catch (err) {
      message.destroy(msgKey)
      message.error(err instanceof Error ? err.message : '打包失败')
    } finally {
      setBatchExporting(false)
    }
  }

  const renderDeleteButton = (creative: Creative) => (
    <Popconfirm
      title="删除这条高光成片？"
      description="会删除成片记录和已合成视频，保留来源原片/数字人资产。"
      okText="删除"
      okButtonProps={{ danger: true }}
      cancelText="取消"
      onConfirm={() => handleDelete(creative)}
    >
      <Button danger size="small">
        删除
      </Button>
    </Popconfirm>
  )

  const renderCrossEpisodeCreative = (creative: Creative) => {
    const plan = creative.clipPlan
    const hasVideo = creative.status === 'READY' && Boolean(creative.ossUrl)
    const episodeNos = plan?.entries.map((e) => e.episodeNo).join('+')
    const boundaryLabel =
      plan?.boundaryType === 'sentence'
        ? '句子完整'
        : plan?.boundaryType === 'shot'
          ? '分镜切点'
          : '硬切'
    const subtitleParts = [
      '跨集高光',
      creative.sourceDramaName,
    ].filter(Boolean).join(' · ')
    const busy = composingId === creative.id || creative.status === 'PROCESSING'
    const selectedConnectorId = connectorOf(creative)
    const dhAsset =
      selectedConnectorId != null
        ? digitalHumans.find((d) => d.id === selectedConnectorId) ?? null
        : null
    const clipUrl = hasVideo ? stableVideoUrl(creative) : ''

    return (
      <article
        key={creative.id}
        className={`${styles.card} ${selectedIds.has(creative.id) ? styles.cardSelected : ''}`}
      >
        <header className={styles.cardHeader}>
          <div className={styles.cardLeft}>
            {hasVideo && (
              <Checkbox
                checked={selectedIds.has(creative.id)}
                onChange={(e) => toggleSelected(creative.id, e.target.checked)}
              />
            )}
            <div>
              <div className={styles.titleLine}>
                <span className={styles.cardTitle}>{creative.sourceAssetName || creative.name}</span>
                <span className={`${styles.statusPill} ${hasVideo ? styles.statusReady : ''}`}>
                  {statusText(creative)}
                </span>
              </div>
              <div className={styles.subtitle}>{subtitleParts}</div>
            </div>
          </div>
          <div className={styles.headerActions}>
            {hasVideo && (
              <Button
                size="small"
                loading={exportingId === creative.id}
                onClick={() => handleExport(creative)}
              >
                导出
              </Button>
            )}
            {renderDeleteButton(creative)}
          </div>
        </header>
        {plan && (
          <div style={{ padding: '14px 22px 0' }}>
            <div className={styles.tagRow}>
              {plan.startEpisodeNo != null && (
                <span className={styles.tag}>起点第{plan.startEpisodeNo}集</span>
              )}
              {episodeNos && <span className={styles.tag}>跨集 {episodeNos}</span>}
              {plan.totalDuration != null && (
                <span className={styles.tag}>计划时长 {plan.totalDuration}s</span>
              )}
              <span className={styles.tag}>收尾：{boundaryLabel}</span>
              {dhAsset && <span className={styles.tag}>+ 数字人：{dhAsset.name}</span>}
            </div>
          </div>
        )}
        <div className={styles.crossGrid}>
          <div className={styles.crossLeft}>
            <section className={styles.crossPanel}>
              <div className={styles.sectionTitle}>1 分钟跨集剪辑</div>
              {hasVideo ? (
                <video className={styles.crossVideo} src={clipUrl} controls preload="metadata" />
              ) : (
                <div className={styles.crossPlaceholder}>
                  {creative.status === 'PROCESSING'
                    ? '生成中'
                    : creative.status === 'FAILED'
                      ? '失败'
                      : '待生成'}
                </div>
              )}
            </section>
            <section className={styles.crossPanel}>
              <div className={styles.sectionTitle}>数字人</div>
              <Select
                size="small"
                className={styles.crossSelect}
                style={{ width: '100%' }}
                value={selectedConnectorId ?? 0}
                disabled={busy}
                onChange={(v) => handleSelectConnector(creative, v === 0 ? null : v)}
                options={[
                  { label: '不接数字人（纯片）', value: 0 },
                  ...digitalHumans.map((d) => ({
                    label: `${d.connectorRole || '数字人'} · ${d.name}`,
                    value: d.id,
                  })),
                ]}
              />
              {dhAsset ? (
                <video
                  className={styles.crossVideo}
                  src={dhAsset.ossUrl}
                  controls
                  preload="metadata"
                />
              ) : (
                <div className={styles.crossPlaceholder}>未选择数字人</div>
              )}
            </section>
            <section className={styles.crossPanel}>
              <div className={styles.sectionTitle}>前贴</div>
              <Select
                size="small"
                className={styles.crossSelect}
                style={{ width: '100%' }}
                value={prerollOf(creative) ?? 0}
                disabled={busy}
                onChange={(v) => handleSelectPreroll(creative, v === 0 ? null : (v as number))}
                options={[
                  { label: '不使用前贴', value: 0 },
                  ...prerollAssets.map((p) => ({ label: p.name, value: p.id })),
                ]}
              />
              {(() => {
                const asset = prerollAssets.find((p) => p.id === prerollOf(creative))
                return asset ? (
                  <img
                    src={asset.ossUrl}
                    alt={asset.name}
                    style={{ width: '100%', marginTop: 8, objectFit: 'contain', maxHeight: 120, background: '#f0f0f0' }}
                  />
                ) : null
              })()}
            </section>
          </div>
          <section className={styles.crossPanel}>
            <div className={styles.sectionTitle}>顺序预览（剪辑 → 数字人）</div>
            {clipUrl ? (
              <PrerollOverlayPreview
                clipUrl={clipUrl}
                dhUrl={dhAsset?.ossUrl ?? ''}
                prerollUrl={
                  prerollOf(creative) != null
                    ? (prerollAssets.find((p) => p.id === prerollOf(creative))?.ossUrl ?? null)
                    : null
                }
                videoClassName={styles.crossVideo}
              />
            ) : (
              <div className={styles.crossPlaceholder}>待生成</div>
            )}
          </section>
        </div>
      </article>
    )
  }

  const renderCreative = (creative: Creative) => {
    if (creative.creativeType === 'continuous_cross_episode') {
      return renderCrossEpisodeCreative(creative)
    }
    const isDigital = creative.creativeType === 'highlight_digital_human'
    const previewUrl = segmentPreviewUrl(creative)
    const hasComposedVideo = Boolean(creative.ossUrl)
    const bridgeText = creative.highlightReason?.bridge_text
    const frontloadRecommendation = creative.highlightReason?.frontload_recommendation
    const sourceMeta = [
      creative.sourceDramaName,
      creative.sourceEpisodeNo ? `第${creative.sourceEpisodeNo}集` : null,
    ]
      .filter(Boolean)
      .join(' / ')
    return (
      <article
        key={creative.id}
        className={`${styles.card} ${selectedIds.has(creative.id) ? styles.cardSelected : ''}`}
      >
        <header className={styles.cardHeader}>
          <div className={styles.cardLeft}>
            {hasComposedVideo && (
              <Checkbox
                checked={selectedIds.has(creative.id)}
                onChange={(e) => toggleSelected(creative.id, e.target.checked)}
              />
            )}
            <div>
              <div className={styles.titleLine}>
                <span className={styles.cardTitle}>{creative.sourceAssetName || creative.name}</span>
                <span className={`${styles.statusPill} ${hasComposedVideo ? styles.statusReady : ''}`}>
                  {statusText(creative)}
                </span>
              </div>
              <div className={styles.subtitle}>
                {isDigital ? '高光 + 数字人' : '高光 + 原片'}
                {sourceMeta ? ` · ${sourceMeta}` : ''}
              </div>
            </div>
          </div>
          <div className={styles.headerActions}>
            {hasComposedVideo && (
              <Button
                size="small"
                loading={exportingId === creative.id}
                onClick={() => handleExport(creative)}
              >
                导出
              </Button>
            )}
            <button
              className={styles.button}
              disabled={composingId === creative.id || creative.status === 'PROCESSING'}
              onClick={() => handleCompose(creative)}
            >
              {creative.status === 'PROCESSING'
                ? '生成中'
                : hasComposedVideo
                  ? '重新生成'
                  : '生成组合视频'}
            </button>
            {renderDeleteButton(creative)}
          </div>
        </header>

        <div className={styles.workflowGrid}>
          <section className={styles.inputPanel}>
            <div className={styles.sectionTitle}>输入素材</div>
            <div className={styles.assetStack}>
              <AssetPreview
                title="原片"
                name={creative.sourceAssetName || creative.name}
                meta={sourceMeta || undefined}
                url={creative.sourceAssetOssUrl}
              />
              {isDigital && (
                <AssetPreview
                  title="数字人"
                  name={creative.connectorAssetName || creative.connectorRole || '-'}
                  meta={creative.connectorRole || undefined}
                  url={creative.connectorAssetOssUrl}
                />
              )}
            </div>
          </section>

          <section className={styles.decisionPanel}>
            <div className={styles.sectionTitle}>高光判断</div>
            <div className={styles.highlightBody}>
              {previewUrl ? (
                <video className={styles.highlightVideo} src={previewUrl} controls preload="metadata" />
              ) : (
                <div className={styles.previewPlaceholder}>暂无片段预览</div>
              )}
              <div className={styles.highlightText}>
                <div className={styles.tagRow}>
                  <span className={styles.tag}>
                    {creative.creativeType === 'highlight_digital_human' ? '高光+数字人' : '高光+原片'}
                  </span>
                  <span className={styles.tag}>
                    {formatTime(creative.highlightStart)} - {formatTime(creative.highlightEnd)}
                  </span>
                </div>
                <div className={styles.reason}>
                  {String(
                    frontloadRecommendation ||
                      creative.highlightReason?.reason ||
                      creative.highlightReason?.open_question ||
                      '暂无说明',
                  )}
                </div>
                {Boolean(bridgeText) && (
                  <div className={styles.bridge}>桥接：{String(bridgeText)}</div>
                )}
              </div>
            </div>
          </section>

          <section className={styles.outputPanel}>
            <div className={styles.sectionTitle}>组合视频</div>
            {hasComposedVideo ? (
              <video className={styles.outputVideo} src={stableVideoUrl(creative)} controls preload="metadata" />
            ) : (
              <div className={styles.outputPlaceholder}>尚未生成</div>
            )}
          </section>
        </div>
      </article>
    )
  }

  const renderPlanningCard = (task: HighlightPlanTask) => (
    <article key={`planning-${task.taskId}`} className={styles.card}>
      <header className={styles.cardHeader}>
        <div>
          <div className={styles.titleLine}>
            <span className={styles.cardTitle}>{task.dramaName || '跨集高光'}</span>
            <span className={styles.statusPill}>生成中</span>
          </div>
          <div className={styles.subtitle}>
            跨集高光
            {task.dramaName ? ` · ${task.dramaName}` : ''}
            {task.numCandidates ? ` · 约 ${task.numCandidates} 条候选` : ''}
          </div>
        </div>
      </header>
      <div className={styles.planningBody}>
        <Spin />
        <span className={styles.planningText}>
          正在识别高光起点并拼接生成，预计 1–3 分钟…
        </span>
      </div>
    </article>
  )

  return (
    <div className={styles.layout}>
      <div className={styles.topBar}>
        <div className={styles.count}>
          共 {rows.length} 条高光
          {pendingTasks.length > 0 && ` · ${pendingTasks.length} 条生成中`}
        </div>
        <Input.Search
          placeholder="按剧名、文件名或数字人搜索"
          allowClear
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: 260 }}
          size="small"
        />
        <Select
          value={typeFilter}
          onChange={setTypeFilter}
          options={[
            { label: '全部类型', value: 'all' },
            { label: '高光+原片', value: 'highlight_original' },
            { label: '高光+数字人', value: 'highlight_digital_human' },
            { label: '跨集高光', value: 'continuous_cross_episode' },
          ]}
          size="small"
          style={{ width: 150 }}
        />
        <div className={styles.spacer} />
      </div>

      {activeDrama === null ? (
        <div className={styles.entryGrid}>
          {entryNames.length === 0 && (
            <div className={styles.empty}>暂无高光成片记录</div>
          )}
          {entryNames.map((name) => {
            const items = rowsByDrama.get(name) ?? []
            const planningCount = (pendingByDrama[name] ?? []).length
            const unComposedCount = items.filter(
              (c) => c.status === 'PENDING' || c.status === 'PROCESSING',
            ).length
            return (
              <button
                key={name}
                type="button"
                className={styles.entryCard}
                onClick={() => setActiveDrama(name)}
              >
                <span className={styles.entryName}>{name}</span>
                <span>
                  <Tag>{items.length}</Tag>
                  {unComposedCount > 0 && (
                    <Tag color="warning">{unComposedCount} 待合成</Tag>
                  )}
                  {planningCount > 0 && (
                    <Tag color="processing" icon={<Spin size="small" />}>
                      生成中
                    </Tag>
                  )}
                </span>
              </button>
            )
          })}
        </div>
      ) : (
        <div className={styles.list}>
          <div className={styles.backBar}>
            <Button
              type="link"
              size="small"
              onClick={() => { setActiveDrama(null); setSelectedIds(new Set()) }}
            >
              ← 返回
            </Button>
            <span className={styles.backTitle}>{activeDrama}</span>
            <div style={{ flex: 1 }} />
            {exportableInView.length > 0 && (
              <>
                <Checkbox
                  indeterminate={
                    selectedIds.size > 0 && selectedIds.size < exportableInView.length
                  }
                  checked={
                    exportableInView.length > 0 &&
                    selectedIds.size === exportableInView.length
                  }
                  onChange={(e) => {
                    if (e.target.checked) {
                      setSelectedIds(new Set(exportableInView.map((c) => c.id)))
                    } else {
                      setSelectedIds(new Set())
                    }
                  }}
                >
                  全选
                </Checkbox>
                {selectedIds.size > 0 && (
                  <Button
                    type="primary"
                    size="small"
                    loading={batchExporting}
                    onClick={handleBatchExport}
                  >
                    批量导出（{selectedIds.size}）
                  </Button>
                )}
              </>
            )}
          </div>
          {drilledTasks.map((task) => renderPlanningCard(task))}
          {drilledRows.map((creative) => renderCreative(creative))}
        </div>
      )}
    </div>
  )
}
