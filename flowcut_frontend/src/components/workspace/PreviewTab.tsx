import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Empty, Space, Tag } from 'antd'
import { useScriptStore } from '../../stores/scriptStore'
import type { MatchedMaterial, SegmentMatchResult } from '../../types/script'

const BLACK_FRAME_DURATION_MS = 1000

interface QueueItem {
  segIdx: number
  materialId: number | null
  preview: string | null
}

interface OverrideView {
  segIdx: number
  materialId: number
}

function buildQueue(
  selected: Record<number, number[]>,
  materialMap: Map<number, MatchedMaterial>,
  segIdxs: number[],
): QueueItem[] {
  const queue: QueueItem[] = []
  for (const segIdx of segIdxs) {
    const ids = selected[segIdx] ?? []
    if (ids.length === 0) {
      queue.push({ segIdx, materialId: null, preview: null })
      continue
    }
    for (const mid of ids) {
      const mat = materialMap.get(mid)
      queue.push({
        segIdx,
        materialId: mid,
        preview: mat?.preview_url ?? null,
      })
    }
  }
  return queue
}

function collectCandidates(r: SegmentMatchResult): MatchedMaterial[] {
  return [...r.phase1, ...r.phase2]
}

export default function PreviewTab() {
  const { matchResults, selectedMaterials, toggleMaterial } = useScriptStore()
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const blackTimerRef = useRef<number | null>(null)
  const queueRef = useRef<QueueItem[]>([])
  const [cursor, setCursor] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [override, setOverride] = useState<OverrideView | null>(null)

  const materialMap = useMemo(() => {
    const m = new Map<number, MatchedMaterial>()
    for (const r of matchResults) {
      for (const x of r.phase1) m.set(x.material_id, x)
      for (const x of r.phase2) m.set(x.material_id, x)
    }
    return m
  }, [matchResults])

  const orderedSegments = useMemo(
    () => [...matchResults].sort((a, b) => a.seg_idx - b.seg_idx),
    [matchResults],
  )

  const queue = useMemo(() => {
    const segIdxs = orderedSegments.map((r) => r.seg_idx)
    return buildQueue(selectedMaterials, materialMap, segIdxs)
  }, [orderedSegments, selectedMaterials, materialMap])

  useEffect(() => {
    queueRef.current = queue
  }, [queue])

  useEffect(() => {
    return () => {
      if (blackTimerRef.current !== null) {
        window.clearTimeout(blackTimerRef.current)
        blackTimerRef.current = null
      }
    }
  }, [queue])

  const advance = useCallback((): void => {
    setCursor((c) => {
      const q = queueRef.current
      if (c + 1 >= q.length) {
        setPlaying(false)
        return 0
      }
      return c + 1
    })
  }, [])

  useEffect(() => {
    return () => {
      if (blackTimerRef.current !== null) {
        window.clearTimeout(blackTimerRef.current)
        blackTimerRef.current = null
      }
    }
  }, [])

  const safeCursor = queue.length === 0 ? 0 : Math.min(cursor, queue.length - 1)
  const queueItem = queue[safeCursor]

  const overrideMat = override ? materialMap.get(override.materialId) ?? null : null
  const activeSegIdx = override ? override.segIdx : queueItem?.segIdx ?? 0
  const activeMaterialId = override ? override.materialId : queueItem?.materialId ?? null
  const activePreview = override
    ? overrideMat?.preview_url ?? null
    : queueItem?.preview ?? null

  const isBlack = !override && queueItem?.materialId === null
  const missingPreview =
    activeMaterialId !== null && activePreview === null

  // 黑屏段：定时切下一段（仅 queue 模式且 playing）
  useEffect(() => {
    if (!playing || override) return
    if (!queueItem || queueItem.materialId !== null) return
    if (blackTimerRef.current !== null) {
      window.clearTimeout(blackTimerRef.current)
    }
    blackTimerRef.current = window.setTimeout(() => {
      blackTimerRef.current = null
      advance()
    }, BLACK_FRAME_DURATION_MS)
    return () => {
      if (blackTimerRef.current !== null) {
        window.clearTimeout(blackTimerRef.current)
        blackTimerRef.current = null
      }
    }
  }, [playing, cursor, queueItem, override, advance])

  // 视频段：加载 src；override 时不自动播，由用户手动控；queue + playing 时自动播
  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    if (activeMaterialId === null || !activePreview) return
    v.src = activePreview
    if (playing && !override) {
      v.play().catch(() => {
        /* 浏览器拦截 autoplay 时静默 */
      })
    } else {
      v.pause()
    }
  }, [activePreview, activeMaterialId, playing, override])

  const onPlay = (): void => {
    setOverride(null)
    setCursor(0)
    setPlaying(true)
  }

  const onPause = (): void => {
    setPlaying(false)
    if (videoRef.current) videoRef.current.pause()
    if (blackTimerRef.current !== null) {
      window.clearTimeout(blackTimerRef.current)
      blackTimerRef.current = null
    }
  }

  const jumpToSegment = useCallback(
    (segIdx: number): void => {
      const idx = queue.findIndex((q) => q.segIdx === segIdx)
      if (idx < 0) return
      setOverride(null)
      setCursor(idx)
    },
    [queue],
  )

  const previewCandidate = useCallback(
    (segIdx: number, materialId: number): void => {
      setPlaying(false)
      if (blackTimerRef.current !== null) {
        window.clearTimeout(blackTimerRef.current)
        blackTimerRef.current = null
      }
      setOverride({ segIdx, materialId })
    },
    [],
  )

  if (orderedSegments.length === 0) {
    return (
      <div style={{ padding: 24 }}>
        <Empty description="尚未召回素材" />
      </div>
    )
  }

  const totalSegs = orderedSegments.length
  const segNum =
    orderedSegments.findIndex((r) => r.seg_idx === activeSegIdx) + 1

  const currentSegResult = orderedSegments.find(
    (r) => r.seg_idx === activeSegIdx,
  )
  const candidates = currentSegResult ? collectCandidates(currentSegResult) : []
  const selectedForCurrent =
    selectedMaterials[activeSegIdx] ?? []

  return (
    <div style={{ display: 'flex', gap: 16, padding: 24 }}>
      {/* 左侧 预览 */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <Space style={{ marginBottom: 12 }}>
          {playing ? (
            <Button onClick={onPause}>暂停</Button>
          ) : (
            <Button type="primary" onClick={onPlay}>
              播放
            </Button>
          )}
          <span style={{ color: '#888' }}>
            段 {Math.max(segNum, 1)}/{totalSegs} ·{' '}
            {activeMaterialId !== null
              ? `素材 #${activeMaterialId}`
              : '空段（黑屏）'}
          </span>
          {override && <Tag color="orange">手动预览中</Tag>}
        </Space>

        <div
          style={{
            width: '100%',
            maxWidth: 640,
            background: '#000',
            position: 'relative',
            aspectRatio: '16 / 9',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {isBlack ? (
            <div style={{ color: '#fff', fontSize: 14 }}>
              段 {activeSegIdx + 1} · 未选素材（黑屏 1s）
            </div>
          ) : missingPreview ? (
            <div style={{ color: '#fff', fontSize: 14 }}>素材信息缺失</div>
          ) : (
            <video
              ref={videoRef}
              style={{ width: '100%', height: '100%', objectFit: 'contain' }}
              onEnded={() => {
                if (!override) advance()
              }}
              controls={!!override}
              muted
            />
          )}
          <div
            style={{
              position: 'absolute',
              bottom: 8,
              left: 8,
              background: 'rgba(0,0,0,0.5)',
              color: '#fff',
              padding: '2px 8px',
              borderRadius: 4,
              fontSize: 12,
            }}
          >
            段 {activeSegIdx + 1}
          </div>
        </div>

        <Alert
          type="info"
          showIcon
          style={{ marginTop: 12, maxWidth: 640 }}
          message={
            override
              ? '当前为手动预览，不计入播放队列；点击播放可回到按勾选拼接的预览'
              : '预览按勾选顺序拼接，不挂音轨，不强制对齐时长'
          }
        />
      </div>

      {/* 右侧 段时间线 + 当前段候选 */}
      <div
        style={{
          width: 300,
          flexShrink: 0,
          maxHeight: '80vh',
          display: 'flex',
          flexDirection: 'column',
          borderLeft: '1px solid #f0f0f0',
          paddingLeft: 12,
        }}
      >
        <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
          段时间线（共 {totalSegs}）
        </div>
        <div
          style={{
            maxHeight: 200,
            overflowY: 'auto',
            marginBottom: 12,
            paddingRight: 4,
          }}
        >
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            {orderedSegments.map((r, i) => {
              const sel = selectedMaterials[r.seg_idx] ?? []
              const total = r.phase1.length + r.phase2.length
              const active = r.seg_idx === activeSegIdx
              return (
                <div
                  key={r.seg_idx}
                  onClick={() => jumpToSegment(r.seg_idx)}
                  style={{
                    cursor: 'pointer',
                    padding: '6px 10px',
                    borderRadius: 4,
                    background: active ? '#e6f0ff' : 'transparent',
                    border: active
                      ? '1px solid #2563eb'
                      : '1px solid transparent',
                    fontSize: 12,
                  }}
                >
                  <div style={{ fontWeight: active ? 600 : 400 }}>
                    段 {i + 1}
                  </div>
                  <div style={{ color: '#888', fontSize: 11 }}>
                    已选 {sel.length} / 候选 {total}
                  </div>
                </div>
              )
            })}
          </Space>
        </div>

        <div
          style={{
            fontSize: 12,
            color: '#888',
            marginBottom: 8,
            borderTop: '1px solid #f0f0f0',
            paddingTop: 12,
          }}
        >
          段 {activeSegIdx + 1} 候选 · 点缩略图切预览，复选框改勾选
        </div>
        <div style={{ flex: 1, overflowY: 'auto', paddingRight: 4 }}>
          {candidates.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="本段无候选"
            />
          ) : (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              {candidates.map((m) => {
                const checked = selectedForCurrent.includes(m.material_id)
                const isPreviewing = activeMaterialId === m.material_id
                const isPhase2 = currentSegResult?.phase2.some(
                  (x) => x.material_id === m.material_id,
                )
                return (
                  <div
                    key={m.material_id}
                    style={{
                      display: 'flex',
                      gap: 8,
                      border: isPreviewing
                        ? '2px solid #2563eb'
                        : '1px solid #e8e8e8',
                      borderRadius: 4,
                      padding: 6,
                      opacity: isPhase2 ? 0.85 : 1,
                    }}
                  >
                    <div
                      style={{ cursor: 'pointer', flexShrink: 0 }}
                      onClick={() =>
                        previewCandidate(activeSegIdx, m.material_id)
                      }
                    >
                      {m.preview_url ? (
                        <video
                          src={m.preview_url}
                          style={{
                            width: 96,
                            height: 64,
                            objectFit: 'cover',
                            background: '#000',
                          }}
                          muted
                        />
                      ) : (
                        <div
                          style={{
                            width: 96,
                            height: 64,
                            background: '#f0f0f0',
                          }}
                        />
                      )}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 4,
                        }}
                      >
                        <Checkbox
                          checked={checked}
                          onChange={() =>
                            toggleMaterial(activeSegIdx, m.material_id)
                          }
                        />
                        <span
                          style={{
                            fontSize: 12,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            flex: 1,
                          }}
                          title={m.name}
                        >
                          {m.name}
                        </span>
                      </div>
                      <div
                        style={{
                          fontSize: 10,
                          color: '#aaa',
                          marginTop: 4,
                        }}
                      >
                        {m.duration?.toFixed(1)}s · {m.score.toFixed(2)}
                        {isPhase2 && (
                          <Tag style={{ marginLeft: 4 }}>兜底</Tag>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </Space>
          )}
        </div>
      </div>
    </div>
  )
}
