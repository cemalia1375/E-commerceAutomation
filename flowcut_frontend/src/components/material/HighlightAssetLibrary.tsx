import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Checkbox,
  Input,
  InputNumber,
  Popconfirm,
  Segmented,
  Select,
  Tag,
  Upload,
  message,
} from 'antd'
import { UploadOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd'
import {
  deleteHighlightAssets,
  deleteHighlightAsset,
  listHighlightAssets,
  uploadHighlightAsset,
} from '../../api/highlightAssets'
import { useAuthStore } from '../../stores/authStore'
import type { HighlightAsset, HighlightAssetType } from '../../types'
import { useAuthStore } from '../../stores/authStore'
import { useUIContextStore } from '../../stores/uiContextStore'
import styles from './HighlightAssetLibrary.module.css'

type ViewMode = 'episode_source' | 'digital_human_connector' | 'preroll'

function formatSize(bytes: number) {
  if (!bytes) return '0 MB'
  const mb = bytes / 1024 / 1024
  return `${mb.toFixed(mb >= 10 ? 0 : 1)} MB`
}

function formatDuration(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0:00'
  const total = Math.round(seconds)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

function groupAssets(assets: HighlightAsset[], mode: ViewMode) {
  if (mode === 'preroll') {
    return [['前贴', assets]] as [string, HighlightAsset[]][]
  }
  const groups: Record<string, HighlightAsset[]> = {}
  for (const asset of assets) {
    const key =
      mode === 'episode_source'
        ? asset.dramaName || '未命名剧集'
        : asset.connectorRole || '通用数字人'
    if (!groups[key]) groups[key] = []
    groups[key].push(asset)
  }
  return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b, 'zh-Hans-CN'))
}

export default function HighlightAssetLibrary() {
  const TENANT_KEY = useAuthStore((s) => s.user?.tenantKey) ?? 'flowcut'
  const [mode, setMode] = useState<ViewMode>('episode_source')
  const [assets, setAssets] = useState<HighlightAsset[]>([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [activeDrama, setActiveDrama] = useState<string | null>(null)
  const setDrama = useUIContextStore((s) => s.setDrama)
  const [dramaName, setDramaName] = useState('')
  const [episodeNo, setEpisodeNo] = useState<number | null>(null)
  const [connectorRole, setConnectorRole] = useState('通用数字人')
  const [files, setFiles] = useState<File[]>([])
  const [selectedIds, setSelectedIds] = useState<Set<number>>(() => new Set())
  const [durations, setDurations] = useState<Record<number, number>>({})

  const fetchAssets = async (assetType: HighlightAssetType = mode) => {
    setLoading(true)
    try {
      const list = await listHighlightAssets(TENANT_KEY, { assetType })
      setAssets(list)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载高光资产失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setSelectedIds(new Set())
    setActiveDrama(null)
    fetchAssets(mode)
  }, [mode])

  useEffect(() => {
    setDrama(activeDrama)
  }, [activeDrama, setDrama])

  const visibleAssets = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    return assets.filter((asset) => {
      if (!kw) return true
      const haystack = [
        asset.name,
        asset.dramaName,
        asset.connectorRole,
        asset.episodeNo ? `第${asset.episodeNo}集` : '',
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return haystack.includes(kw)
    })
  }, [assets, keyword])

  const grouped = groupAssets(visibleAssets, mode)

  const canUpload =
    files.length > 0 &&
    (mode === 'digital_human_connector' || mode === 'preroll' || dramaName.trim().length > 0)

  const handleUpload = async () => {
    if (!canUpload) return
    setUploading(true)
    try {
      for (const [idx, file] of files.entries()) {
        await uploadHighlightAsset(TENANT_KEY, file, {
          assetType: mode,
          dramaName: mode === 'episode_source' ? dramaName.trim() : undefined,
          episodeNo:
            mode === 'episode_source' && episodeNo !== null
              ? episodeNo + idx
              : undefined,
          connectorRole:
            mode === 'digital_human_connector'
              ? connectorRole.trim() || '通用数字人'
              : undefined,
        })
      }
      message.success(`已上传 ${files.length} 个资产`)
      setFiles([])
      await fetchAssets(mode)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '上传失败')
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (asset: HighlightAsset) => {
    try {
      await deleteHighlightAsset(asset.id)
      setAssets((prev) => prev.filter((item) => item.id !== asset.id))
      message.success('已删除')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const toggleSelected = (assetId: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(assetId)) next.delete(assetId)
      else next.add(assetId)
      return next
    })
  }

  const handleBatchDelete = async (ids: number[]) => {
    if (ids.length === 0) return
    try {
      const result = await deleteHighlightAssets(TENANT_KEY, ids)
      setAssets((prev) => prev.filter((item) => !ids.includes(item.id)))
      setSelectedIds(new Set())
      if (result.errors.length > 0) {
        message.warning(`已删除 ${result.deleted} 个，部分 OSS 删除失败`)
      } else {
        message.success(`已删除 ${result.deleted} 个资产`)
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : '批量删除失败')
    }
  }

  const isEntryLevel = mode === 'episode_source' && activeDrama === null
  const drilledGroups =
    mode === 'episode_source' && activeDrama !== null
      ? grouped.filter(([group]) => group === activeDrama)
      : grouped

  const renderSection = (group: string, items: HighlightAsset[]) => (
    <section key={group} className={styles.section}>
      <div className={styles.sectionHead}>
        <h3 className={styles.sectionTitle}>
          {group} <Tag>{items.length}</Tag>
        </h3>
        <Button
          type="link"
          size="small"
          onClick={() =>
            setSelectedIds((prev) => {
              const next = new Set(prev)
              const allSelected = items.every((asset) => next.has(asset.id))
              for (const asset of items) {
                if (allSelected) next.delete(asset.id)
                else next.add(asset.id)
              }
              return next
            })
          }
        >
          {items.every((asset) => selectedIds.has(asset.id)) ? '取消本组' : '选择本组'}
        </Button>
      </div>
      <div className={styles.grid}>
        {items.map((asset) => (
          <article
            key={asset.id}
            className={`${styles.card} ${selectedIds.has(asset.id) ? styles.selected : ''}`}
          >
            <Checkbox
              className={styles.selectBox}
              checked={selectedIds.has(asset.id)}
              onChange={() => toggleSelected(asset.id)}
            />
            <div className={styles.thumbWrap}>
              {asset.assetType === 'preroll' ? (
                <img
                  className={styles.thumb}
                  src={asset.ossUrl}
                  alt={asset.name}
                />
              ) : (
                <video
                  className={styles.thumb}
                  src={asset.ossUrl}
                  controls
                  preload="metadata"
                  onLoadedMetadata={(e) => {
                    const dur = e.currentTarget.duration
                    if (Number.isFinite(dur) && dur > 0) {
                      setDurations((prev) => ({ ...prev, [asset.id]: dur }))
                    }
                  }}
                />
              )}
              {asset.assetType !== 'preroll' && (
                <span className={styles.duration}>
                  {formatDuration(durations[asset.id] ?? asset.duration)}
                </span>
              )}
            </div>
            <div className={styles.body}>
              <div className={styles.title}>{asset.name}</div>
              <div className={styles.meta}>
                {asset.assetType === 'episode_source' && asset.episodeNo && (
                  <Tag color="blue">第 {asset.episodeNo} 集</Tag>
                )}
                {asset.assetType === 'digital_human_connector' && (
                  <Tag color="green">{asset.connectorRole || '通用数字人'}</Tag>
                )}
                <span>{formatSize(asset.fileSize)}</span>
                <Tag>{asset.status}</Tag>
              </div>
              <Popconfirm
                title="删除这个资产？"
                description="只会删除资产库记录和对应 OSS 文件，不影响已生成脚本。"
                onConfirm={() => handleDelete(asset)}
              >
                <Button danger size="small" className={styles.deleteBtn}>
                  删除
                </Button>
              </Popconfirm>
            </div>
          </article>
        ))}
      </div>
    </section>
  )

  return (
    <div className={styles.panel}>
      <div className={styles.topBar}>
        <Segmented
          value={mode}
          onChange={(value) => setMode(value as ViewMode)}
          options={[
            { label: '原片库', value: 'episode_source' },
            { label: '数字人库', value: 'digital_human_connector' },
            { label: '前贴库', value: 'preroll' },
          ]}
          size="small"
        />
        <Input.Search
          placeholder="按名称、剧名或角色搜索"
          allowClear
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: 260 }}
          size="small"
        />
        <div className={styles.spacer} />
        <Popconfirm
          title={`删除选中的 ${selectedIds.size} 个资产？`}
          description="只会删除资产库记录和对应 OSS 文件，不影响已生成脚本。"
          disabled={selectedIds.size === 0}
          onConfirm={() => handleBatchDelete(Array.from(selectedIds))}
        >
          <Button danger size="small" disabled={selectedIds.size === 0}>
            删除选中
          </Button>
        </Popconfirm>
        <div className={styles.uploadRow}>
          {mode === 'episode_source' ? (
            <>
              <Input
                placeholder="AI 漫剧名称"
                value={dramaName}
                onChange={(e) => setDramaName(e.target.value)}
                style={{ width: 180 }}
                size="small"
              />
              <InputNumber
                placeholder="起始集数"
                min={1}
                value={episodeNo}
                onChange={(v) => setEpisodeNo(v ?? null)}
                style={{ width: 110 }}
                size="small"
              />
            </>
          ) : mode === 'digital_human_connector' ? (
            <Select
              value={connectorRole}
              onChange={setConnectorRole}
              options={[
                { label: '通用数字人', value: '通用数字人' },
                { label: '开场推荐', value: '开场推荐' },
                { label: '产品转化', value: '产品转化' },
                { label: '剧情承接', value: '剧情承接' },
              ]}
              style={{ width: 150 }}
              size="small"
            />
          ) : null}
          <Upload
            accept={mode === 'preroll' ? 'image/*' : 'video/*'}
            multiple
            beforeUpload={(file) => {
              setFiles((prev) => [...prev, file])
              return false
            }}
            fileList={files.map((file, idx) => ({
              uid: String(idx),
              name: file.name,
              status: 'done',
            })) as UploadFile[]}
            onRemove={(item) =>
              setFiles((prev) => prev.filter((_, idx) => String(idx) !== item.uid))
            }
          >
            <Button size="small" icon={<UploadOutlined />}>
              {mode === 'preroll' ? '选择图片' : '选择视频'}
            </Button>
          </Upload>
          <Button
            type="primary"
            size="small"
            disabled={!canUpload || uploading}
            loading={uploading}
            onClick={handleUpload}
          >
            {uploading ? '上传中...' : '上传资产'}
          </Button>
        </div>
      </div>

      <div className={styles.content}>
        <div className={styles.summary}>
          {loading ? '加载中...' : `共 ${visibleAssets.length} / ${assets.length} 个资产`}
        </div>
        {!loading && grouped.length === 0 && (
          <div className={styles.empty}>暂无资产，先上传原片或数字人视频。</div>
        )}
        {!loading && isEntryLevel && (
          <div className={styles.entryGrid}>
            {grouped.map(([group, items]) => (
              <button
                key={group}
                type="button"
                className={styles.entryCard}
                onClick={() => setActiveDrama(group)}
              >
                <span className={styles.entryName}>{group}</span>
                <Tag>{items.length}</Tag>
              </button>
            ))}
          </div>
        )}
        {!loading && !isEntryLevel && (
          <>
            {mode === 'episode_source' && activeDrama !== null && (
              <div className={styles.backBar}>
                <Button type="link" size="small" onClick={() => setActiveDrama(null)}>
                  ← 返回
                </Button>
                <span className={styles.backTitle}>{activeDrama}</span>
              </div>
            )}
            {drilledGroups.map(([group, items]) => renderSection(group, items))}
          </>
        )}
      </div>
    </div>
  )
}
