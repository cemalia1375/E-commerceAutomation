import { useEffect, useMemo, useState } from 'react'
import { Button, Input, Select, Tag, message } from 'antd'
import { composeHighlightCreative, getTaskStatus } from '../../api/qianchuan'
import { useCreativeStore } from '../../stores/creativeStore'
import type { Creative } from '../../types'
import styles from './HighlightCreativeLibrary.module.css'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8001'
const POLL_INTERVAL_MS = 2500
const POLL_TIMEOUT_MS = 180000

function isHighlightCreative(creative: Creative) {
  return creative.creativeType?.startsWith('highlight_')
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
  const [composingId, setComposingId] = useState<string | null>(null)

  useEffect(() => {
    refetch()
  }, [refetch])

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

  const drilledRows = activeDrama
    ? rows.filter((c) => (c.sourceDramaName || '未命名剧集') === activeDrama)
    : []

  const handleCompose = async (creative: Creative) => {
    setComposingId(creative.id)
    try {
      const { taskId } = await composeHighlightCreative(creative.id)
      message.success('已开始生成组合视频')
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

  const renderCreative = (creative: Creative) => {
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
      <article key={creative.id} className={styles.card}>
        <header className={styles.cardHeader}>
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
              <video className={styles.outputVideo} src={creative.ossUrl} controls preload="metadata" />
            ) : (
              <div className={styles.outputPlaceholder}>尚未生成</div>
            )}
          </section>
        </div>
      </article>
    )
  }

  return (
    <div className={styles.layout}>
      <div className={styles.topBar}>
        <div className={styles.count}>共 {rows.length} 条高光</div>
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
          ]}
          size="small"
          style={{ width: 150 }}
        />
        <div className={styles.spacer} />
      </div>

      {activeDrama === null ? (
        <div className={styles.entryGrid}>
          {dramaGroups.length === 0 && (
            <div className={styles.empty}>暂无高光成片记录</div>
          )}
          {dramaGroups.map(([name, items]) => (
            <button
              key={name}
              type="button"
              className={styles.entryCard}
              onClick={() => setActiveDrama(name)}
            >
              <span className={styles.entryName}>{name}</span>
              <Tag>{items.length}</Tag>
            </button>
          ))}
        </div>
      ) : (
        <div className={styles.list}>
          <div className={styles.backBar}>
            <Button type="link" size="small" onClick={() => setActiveDrama(null)}>
              ← 返回
            </Button>
            <span className={styles.backTitle}>{activeDrama}</span>
          </div>
          {drilledRows.map((creative) => renderCreative(creative))}
        </div>
      )}
    </div>
  )
}
