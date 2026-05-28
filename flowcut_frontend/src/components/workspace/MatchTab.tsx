import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Checkbox, Empty, Input, Space, Tag, message } from 'antd'
import { useScriptStore } from '../../stores/scriptStore'
import { scriptApi } from '../../api/script'
import type { MatchedMaterial } from '../../types/script'

const TENANT_KEY = 'flowcut'

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

interface MaterialCardProps {
  mat: MatchedMaterial
  checked: boolean
  order: number | null
  onToggle: () => void
  dim?: boolean
}

function MaterialCard({ mat, checked, order, onToggle, dim }: MaterialCardProps) {
  const [previewFailed, setPreviewFailed] = useState(false)
  const showVideo = mat.preview_url && !previewFailed

  return (
    <Card
      hoverable
      style={{ width: 200, opacity: dim ? 0.7 : 1, position: 'relative' }}
      cover={
        showVideo ? (
          <video
            src={mat.preview_url ?? undefined}
            style={{ height: 120, objectFit: 'cover', width: '100%' }}
            muted
            onError={() => setPreviewFailed(true)}
          />
        ) : (
          <div style={{ height: 120, background: '#f0f0f0' }} />
        )
      }
      styles={{ body: { padding: 8 } }}
    >
      {order !== null && (
        <div
          style={{
            position: 'absolute',
            top: 4,
            left: 4,
            background: '#2563eb',
            color: '#fff',
            borderRadius: 10,
            padding: '0 6px',
            fontSize: 11,
            fontWeight: 600,
          }}
        >
          #{order + 1}
        </div>
      )}
      <Checkbox checked={checked} onChange={onToggle}>
        <span style={{ fontSize: 12 }}>{mat.name}</span>
      </Checkbox>
      <div style={{ fontSize: 11, color: '#888' }}>
        {mat.duration?.toFixed(1)}s · score {mat.score.toFixed(2)}
      </div>
      {mat.scene_role && <Tag style={{ marginTop: 4 }}>{mat.scene_role}</Tag>}
    </Card>
  )
}

export default function MatchTab() {
  const {
    currentScript,
    matchResults,
    selectedMaterials,
    setMatchResults,
    toggleMaterial,
  } = useScriptStore()
  const [product, setProduct] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const initializedRef = useRef(false)

  // 首次进入 tab 且无结果时，自动跑一次默认召回
  useEffect(() => {
    if (!currentScript || initializedRef.current) return
    if (matchResults.length > 0) {
      initializedRef.current = true
      return
    }
    if (currentScript.status !== 'CONFIRMED' && currentScript.status !== 'DRAFT') {
      return
    }
    initializedRef.current = true
    const run = async (): Promise<void> => {
      setLoading(true)
      try {
        const m = await scriptApi.match(
          currentScript.id,
          TENANT_KEY,
          currentScript.product ?? '',
        )
        setMatchResults(m.results)
      } catch (e: unknown) {
        message.error(getErrorMessage(e))
      } finally {
        setLoading(false)
      }
    }
    void run()
  }, [currentScript, matchResults.length, setMatchResults])

  if (!currentScript) {
    return <div style={{ padding: 24 }}>加载中…</div>
  }

  const onRematch = async (): Promise<void> => {
    setLoading(true)
    try {
      const m = await scriptApi.match(currentScript.id, TENANT_KEY, product)
      setMatchResults(m.results)
      message.success('召回完成')
    } catch (e: unknown) {
      message.error(getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          placeholder="产品（留空查通用素材）"
          value={product}
          onChange={(e) => setProduct(e.target.value)}
          style={{ width: 320 }}
          allowClear
        />
        <Button type="primary" loading={loading} onClick={onRematch}>
          按产品重新召回
        </Button>
        <span style={{ color: '#888' }}>共 {matchResults.length} 段</span>
      </Space>

      {currentScript.status === 'DRAFT' && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="脚本未确认，召回结果仅供预览"
        />
      )}

      {!loading && matchResults.length === 0 && (
        <Empty description="暂无召回结果，请点击按钮发起召回" />
      )}

      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        {matchResults.map((r) => {
          const selected = selectedMaterials[r.seg_idx] ?? []
          const totalCandidates = r.phase1.length + r.phase2.length
          const orderOf = (mid: number): number | null => {
            const i = selected.indexOf(mid)
            return i >= 0 ? i : null
          }
          return (
            <Card
              key={r.seg_idx}
              title={`段 ${r.seg_idx}`}
              extra={
                <Tag color="blue">
                  已选 {selected.length} / 候选 {totalCandidates}
                </Tag>
              }
            >
              <div style={{ marginBottom: 12, fontSize: 13, color: '#555' }}>
                <div>画面：{r.visual}</div>
                <div>文案：{r.copy}</div>
              </div>
              {r.phase1.length === 0 && r.phase2.length === 0 && (
                <Empty description="召回为空" />
              )}
              {r.phase1.length > 0 && (
                <>
                  <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
                    产品专属（默认勾选）
                  </div>
                  <Space wrap>
                    {r.phase1.map((m) => (
                      <MaterialCard
                        key={m.material_id}
                        mat={m}
                        checked={selected.includes(m.material_id)}
                        order={orderOf(m.material_id)}
                        onToggle={() => toggleMaterial(r.seg_idx, m.material_id)}
                      />
                    ))}
                  </Space>
                </>
              )}
              {r.phase2.length > 0 && (
                <>
                  <div style={{ fontSize: 12, color: '#888', margin: '12px 0 8px' }}>
                    通用兜底
                  </div>
                  <Space wrap>
                    {r.phase2.map((m) => (
                      <MaterialCard
                        key={m.material_id}
                        mat={m}
                        checked={selected.includes(m.material_id)}
                        order={orderOf(m.material_id)}
                        onToggle={() => toggleMaterial(r.seg_idx, m.material_id)}
                        dim
                      />
                    ))}
                  </Space>
                </>
              )}
            </Card>
          )
        })}
      </Space>
    </div>
  )
}
