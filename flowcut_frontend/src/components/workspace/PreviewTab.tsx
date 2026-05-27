import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Empty, Space } from 'antd'
import { useScriptStore } from '../../stores/scriptStore'
import type { MatchedMaterial } from '../../types/script'

const BLACK_FRAME_DURATION_MS = 1000

interface QueueItem {
  segIdx: number
  materialId: number | null // null = 空段，黑屏占位
  preview: string | null
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

export default function PreviewTab() {
  const { matchResults, selectedMaterials } = useScriptStore()
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const blackTimerRef = useRef<number | null>(null)
  const queueRef = useRef<QueueItem[]>([])
  const [cursor, setCursor] = useState(0)
  const [playing, setPlaying] = useState(false)

  const materialMap = useMemo(() => {
    const m = new Map<number, MatchedMaterial>()
    for (const r of matchResults) {
      for (const x of r.phase1) m.set(x.material_id, x)
      for (const x of r.phase2) m.set(x.material_id, x)
    }
    return m
  }, [matchResults])

  const queue = useMemo(() => {
    const segIdxs = matchResults.map((r) => r.seg_idx).sort((a, b) => a - b)
    return buildQueue(selectedMaterials, materialMap, segIdxs)
  }, [matchResults, selectedMaterials, materialMap])

  // 始终保持 queueRef 与最新 queue 一致（advance 闭包内取最新值用）
  useEffect(() => {
    queueRef.current = queue
  }, [queue])

  // queue 变化时停止播放、清定时器（不动 cursor，避免 setState in effect 警告；
  // 通过依赖 queue 的 video 重挂载来重置渲染状态）
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

  // 卸载清理
  useEffect(() => {
    return () => {
      if (blackTimerRef.current !== null) {
        window.clearTimeout(blackTimerRef.current)
        blackTimerRef.current = null
      }
    }
  }, [])

  // queue 缩短时 cursor 自动回退到有效范围
  const safeCursor = queue.length === 0 ? 0 : Math.min(cursor, queue.length - 1)
  const current = queue[safeCursor]

  // 黑屏段：定时切下一段
  useEffect(() => {
    if (!playing || !current) return
    if (current.materialId !== null) return
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
  }, [playing, cursor, current, advance])

  // 视频段：自动加载并播放
  useEffect(() => {
    const v = videoRef.current
    if (!v || !playing || !current || current.materialId === null) return
    if (!current.preview) return
    v.src = current.preview
    v.play().catch(() => {
      /* 浏览器拦截 autoplay 时静默 */
    })
  }, [cursor, current, playing])

  const onPlay = (): void => {
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

  if (queue.length === 0) {
    return (
      <div style={{ padding: 24 }}>
        <Empty description="尚未召回素材或未勾选任何素材" />
      </div>
    )
  }

  const totalSegs = matchResults.length
  const curSegIdx = current?.segIdx ?? 0
  const segNum = matchResults.findIndex((r) => r.seg_idx === curSegIdx) + 1

  const isBlack = current?.materialId === null
  const missingPreview =
    current?.materialId !== null && current?.preview === null

  return (
    <div style={{ padding: 24 }}>
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
          {current?.materialId !== null
            ? `素材 #${current?.materialId}`
            : '空段（黑屏）'}
        </span>
      </Space>

      <div
        style={{
          width: '100%',
          maxWidth: 720,
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
            段 {curSegIdx} · 未选素材（黑屏 1s）
          </div>
        ) : missingPreview ? (
          <div style={{ color: '#fff', fontSize: 14 }}>素材信息缺失</div>
        ) : (
          <video
            ref={videoRef}
            style={{ width: '100%', height: '100%', objectFit: 'contain' }}
            onEnded={advance}
            controls={false}
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
          段 {curSegIdx}
        </div>
      </div>

      <Alert
        type="info"
        showIcon
        style={{ marginTop: 12 }}
        message="预览仅按选材顺序拼接，不挂音轨，不强制对齐时长"
      />
    </div>
  )
}
