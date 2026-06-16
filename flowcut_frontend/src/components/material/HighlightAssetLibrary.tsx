import { useEffect, useMemo, useState } from 'react'
import { Input, InputNumber, Popconfirm, Select, Tag, message } from 'antd'
import {
  deleteHighlightAssets,
  deleteHighlightAsset,
  listHighlightAssets,
  uploadHighlightAsset,
} from '../../api/highlightAssets'
import type { HighlightAsset, HighlightAssetType } from '../../types'
import styles from './HighlightAssetLibrary.module.css'

const TENANT_KEY = 'flowcut'

type ViewMode = 'episode_source' | 'digital_human_connector'

function formatSize(bytes: number) {
  if (!bytes) return '0 MB'
  const mb = bytes / 1024 / 1024
  return `${mb.toFixed(mb >= 10 ? 0 : 1)} MB`
}

function groupAssets(assets: HighlightAsset[], mode: ViewMode) {
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
  const [mode, setMode] = useState<ViewMode>('episode_source')
  const [assets, setAssets] = useState<HighlightAsset[]>([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [dramaName, setDramaName] = useState('')
  const [episodeNo, setEpisodeNo] = useState<number | null>(null)
  const [connectorRole, setConnectorRole] = useState('通用数字人')
  const [files, setFiles] = useState<File[]>([])
  const [selectedIds, setSelectedIds] = useState<Set<number>>(() => new Set())

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
    fetchAssets(mode)
  }, [mode])

  const visibleAssets = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    if (!kw) return assets
    return assets.filter((asset) => {
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
    (mode === 'digital_human_connector' || dramaName.trim().length > 0)

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

  return (
    <div className={styles.panel}>
      <div className={styles.topBar}>
        <div className={styles.segmented}>
          <button
            className={mode === 'episode_source' ? styles.active : ''}
            onClick={() => setMode('episode_source')}
          >
            原片库
          </button>
          <button
            className={mode === 'digital_human_connector' ? styles.active : ''}
            onClick={() => setMode('digital_human_connector')}
          >
            数字人库
          </button>
        </div>
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
          <button className={styles.batchBtn} disabled={selectedIds.size === 0}>
            删除选中
          </button>
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
          ) : (
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
          )}
          <input
            className={styles.fileInput}
            type="file"
            accept="video/*"
            multiple
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
          <button
            className={styles.uploadBtn}
            disabled={!canUpload || uploading}
            onClick={handleUpload}
          >
            {uploading ? '上传中...' : '上传资产'}
          </button>
        </div>
      </div>

      <div className={styles.content}>
        <div className={styles.summary}>
          {loading ? '加载中...' : `共 ${visibleAssets.length} / ${assets.length} 个资产`}
        </div>
        {!loading && grouped.length === 0 && (
          <div className={styles.empty}>暂无资产，先上传原片或数字人视频。</div>
        )}
        {!loading &&
          grouped.map(([group, items]) => (
            <section key={group} className={styles.section}>
              <div className={styles.sectionHead}>
                <h3 className={styles.sectionTitle}>
                  {group} <Tag>{items.length}</Tag>
                </h3>
                <button
                  className={styles.sectionAction}
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
                </button>
              </div>
              <div className={styles.grid}>
                {items.map((asset) => (
                  <article
                    key={asset.id}
                    className={`${styles.card} ${selectedIds.has(asset.id) ? styles.selected : ''}`}
                  >
                    <input
                      className={styles.selectBox}
                      type="checkbox"
                      checked={selectedIds.has(asset.id)}
                      onChange={() => toggleSelected(asset.id)}
                    />
                    <video className={styles.thumb} src={asset.ossUrl} controls preload="metadata" />
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
                        <button className={styles.deleteBtn}>删除</button>
                      </Popconfirm>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          ))}
      </div>
    </div>
  )
}
