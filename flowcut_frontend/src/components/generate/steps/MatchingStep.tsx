import { useEffect, useState } from 'react'
import { Input, Button, message } from 'antd'
import { useGenerateStore } from '../../../stores/generateStore'
import MediaPreview, { inferMediaType } from '../../common/MediaPreview'
import type { MatchCandidate, MatchedSegment } from '../../../types'
import styles from './Step.module.css'

type MatchStatus = 'matched' | 'low' | 'missing'

const STATUS_MAP: Record<MatchStatus, { label: string; color: string; bg: string }> = {
  matched: { label: '已匹配', color: '#059669', bg: '#d1fae5' },
  low:     { label: '低匹配', color: '#d97706', bg: '#fef3c7' },
  missing: { label: '缺失',   color: '#dc2626', bg: '#fee2e2' },
}

const MAX_CANDIDATES_PER_SEGMENT = 3

function classify(seg: MatchedSegment): MatchStatus {
  if (seg.phase1.length > 0) return 'matched'
  if (seg.phase2.length > 0) return 'low'
  return 'missing'
}

function topCandidates(seg: MatchedSegment): MatchCandidate[] {
  const merged = [...seg.phase1, ...seg.phase2]
  const seen = new Set<number>()
  const unique: MatchCandidate[] = []
  for (const c of merged) {
    if (seen.has(c.id)) continue
    seen.add(c.id)
    unique.push(c)
    if (unique.length >= MAX_CANDIDATES_PER_SEGMENT) break
  }
  return unique
}

function buildDownloadName(c: MatchCandidate): string {
  if (!c.previewUrl) return c.name
  const ext = c.previewUrl.split('?')[0].split('.').pop()
  if (ext && ext.length <= 5 && !c.name.toLowerCase().endsWith(`.${ext.toLowerCase()}`)) {
    return `${c.name}.${ext}`
  }
  return c.name
}

function firstCandidate(results: MatchedSegment[]): MatchCandidate | null {
  for (const seg of results) {
    if (seg.phase1[0]) return seg.phase1[0]
    if (seg.phase2[0]) return seg.phase2[0]
  }
  return null
}

export default function MatchingStep() {
  const {
    addMessage,
    setAgentTyping,
    setStep,
    matchResults,
    matchLoading,
    matchError,
    runMaterialMatch,
    currentProduct,
    currentScriptId,
    setProduct,
  } = useGenerateStore()

  const [activeCandidate, setActiveCandidate] = useState<MatchCandidate | null>(null)
  const [productDraft, setProductDraft] = useState<string>(() => currentProduct ?? '')
  const [savingProduct, setSavingProduct] = useState(false)

  const handleSaveProduct = async () => {
    setSavingProduct(true)
    try {
      await setProduct(productDraft)
      message.success('产品已更新')
    } catch (err) {
      const msg = err instanceof Error ? err.message : '保存失败'
      message.error(msg)
    } finally {
      setSavingProduct(false)
    }
  }

  useEffect(() => {
    if (!matchResults) return
    setActiveCandidate((prev) => {
      if (prev) {
        const stillExists = matchResults.some(
          (seg) =>
            seg.phase1.some((c) => c.id === prev.id) ||
            seg.phase2.some((c) => c.id === prev.id),
        )
        if (stillExists) return prev
      }
      return firstCandidate(matchResults)
    })
  }, [matchResults])

  const handleConfirm = () => {
    addMessage({ role: 'user', type: 'text', content: '确认匹配结果，开始合成初剪。' })
    addMessage({
      role: 'agent',
      type: 'progress',
      content: '',
      label: '正在合成初剪…',
      subLabel: 'Agent 评估中（第 1/3 轮）',
      done: false,
    })
    setAgentTyping(true)
    setTimeout(() => {
      addMessage({ role: 'agent', type: 'text', content: '初剪评估通过，成片已生成，请在右侧确认。' })
      setAgentTyping(false)
      setStep(4)
    }, 2000)
  }

  if (matchLoading) {
    return (
      <div className={styles.wrap}>
        <div className={styles.title}>素材匹配结果</div>
        <div className={styles.sub}>正在匹配中，请稍候…</div>
      </div>
    )
  }

  if (matchError && !matchResults) {
    return (
      <div className={styles.wrap}>
        <div className={styles.title}>素材匹配失败</div>
        <div className={styles.sub} style={{ color: '#dc2626' }}>{matchError}</div>
        <button className={styles.actionBtn} onClick={() => runMaterialMatch()}>
          重试匹配
        </button>
      </div>
    )
  }

  const results = matchResults ?? []

  if (results.length === 0) {
    return (
      <div className={styles.wrap}>
        <div className={styles.title}>素材匹配结果</div>
        <div className={styles.sub}>暂无匹配数据，请回到上一步重新确认脚本。</div>
      </div>
    )
  }

  const matched = results.filter((r) => classify(r) === 'matched').length
  const low     = results.filter((r) => classify(r) === 'low').length
  const missing = results.filter((r) => classify(r) === 'missing').length

  const activeType = activeCandidate ? inferMediaType(activeCandidate.previewUrl) ?? 'video' : 'video'

  return (
    <div className={styles.wrap}>
      <div className={styles.title}>素材匹配结果</div>
      <div className={styles.sub}>已为脚本匹配素材，共 {results.length} 段。可在确认后进入合成。</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '8px 0 12px' }}>
        <span style={{ fontSize: 12, color: '#475569' }}>产品：</span>
        <Input
          size="small"
          placeholder="可选 — 留空将不限定产品"
          value={productDraft}
          onChange={(e) => setProductDraft(e.target.value)}
          style={{ maxWidth: 220 }}
          disabled={currentScriptId === null}
        />
        <Button size="small" loading={savingProduct} onClick={handleSaveProduct}>
          保存
        </Button>
      </div>
      <div className={styles.matchSummary}>
        <div className={styles.matchStat} style={{ background: '#d1fae5', color: '#059669' }}>✓ 已匹配 {matched}</div>
        <div className={styles.matchStat} style={{ background: '#fef3c7', color: '#d97706' }}>△ 低匹配 {low}</div>
        <div className={styles.matchStat} style={{ background: '#fee2e2', color: '#dc2626' }}>✗ 缺失 {missing}</div>
      </div>
      <div className={styles.matchSplit}>
        <div className={styles.matchList}>
        {results.map((r) => {
          const status = classify(r)
          const s = STATUS_MAP[status]
          const candidates = topCandidates(r)
          return (
            <div key={r.index} className={styles.matchCard}>
              <div className={styles.matchHead}>
                <span className={styles.matchIdx}>{r.index + 1}</span>
                <span className={styles.matchLabel}>{r.description.slice(0, 30) || `段 ${r.index + 1}`}</span>
                <span className={styles.matchBadge} style={{ background: s.bg, color: s.color }}>{s.label}</span>
              </div>
              <div className={styles.matchCandidates}>
                {candidates.length === 0 ? (
                  <div className={styles.matchCandEmpty}>无可用素材</div>
                ) : (
                  candidates.map((c) => {
                    const dur = c.duration ? ` · ${c.duration.toFixed(1)}s` : ''
                    const disabled = !c.previewUrl
                    const isActive = activeCandidate?.id === c.id
                    return (
                      <div
                        key={c.id}
                        className={`${styles.matchCand} ${isActive ? styles.matchCandActive : ''}`}
                      >
                        <span className={styles.matchCandName} title={c.name}>{c.name}{dur}</span>
                        <span className={styles.matchCandScore}>{(c.score * 100).toFixed(0)}%</span>
                        <button
                          type="button"
                          className={`${styles.matchPreviewBtn} ${isActive ? styles.matchPreviewBtnActive : ''}`}
                          disabled={disabled}
                          onClick={() => setActiveCandidate(c)}
                        >
                          ▶ 预览
                        </button>
                        {disabled ? (
                          <span className={`${styles.matchDlBtn} ${styles.matchDlBtnDisabled}`}>下载</span>
                        ) : (
                          <a
                            className={styles.matchDlBtn}
                            href={c.previewUrl ?? '#'}
                            download={buildDownloadName(c)}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            下载
                          </a>
                        )}
                      </div>
                    )
                  })
                )}
              </div>
            </div>
          )
        })}
        </div>
        <div className={styles.matchPlayerPanel}>
          <div className={styles.matchPlayerTitle}>预览</div>
          {activeCandidate ? (
            <>
              <div className={styles.matchPlayerMeta}>
                <span className={styles.matchPlayerMetaName} title={activeCandidate.name}>{activeCandidate.name}</span>
                <span>{(activeCandidate.score * 100).toFixed(0)}%</span>
              </div>
              <MediaPreview
                url={activeCandidate.previewUrl}
                type={activeType}
                name={activeCandidate.name}
                height={180}
              />
            </>
          ) : (
            <div className={styles.matchPlayerEmpty}>点击候选行的「▶ 预览」开始播放</div>
          )}
        </div>
      </div>
      <button className={styles.actionBtn} onClick={handleConfirm}>确认匹配，开始合成 →</button>
    </div>
  )
}
