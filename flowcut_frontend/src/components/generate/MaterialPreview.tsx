import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Card, Checkbox, Button, Space, message, Tag, Empty, Input } from 'antd'
import { useScriptStore } from '../../stores/scriptStore'
import { scriptApi } from '../../api/script'
import type { MatchedMaterial } from '../../types/script'
import ExportButton from './ExportButton'

const TENANT_KEY = 'default'

interface MaterialCardProps {
  mat: MatchedMaterial
  checked: boolean
  onToggle: () => void
  dim?: boolean
}

function MaterialCard({ mat, checked, onToggle, dim }: MaterialCardProps) {
  const [previewFailed, setPreviewFailed] = useState(false)
  const showVideo = mat.preview_url && !previewFailed

  return (
    <Card
      hoverable
      style={{ width: 200, opacity: dim ? 0.7 : 1 }}
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

export default function MaterialPreview() {
  const { scriptId } = useParams<{ scriptId: string }>()
  const navigate = useNavigate()
  const {
    currentScript,
    matchResults,
    selectedMaterials,
    setScript,
    setMatchResults,
    toggleMaterial,
  } = useScriptStore()
  const [loading, setLoading] = useState(true)
  const [product, setProduct] = useState<string>('')
  const [matching, setMatching] = useState(false)

  useEffect(() => {
    if (!scriptId) return
    let alive = true
    setLoading(true)
    ;(async () => {
      try {
        const s = await scriptApi.get(Number(scriptId))
        if (!alive) return
        setScript(s)
        const m = await scriptApi.match(Number(scriptId), TENANT_KEY, '')
        if (!alive) return
        setMatchResults(m.results)
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e)
        message.error(msg)
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [scriptId, setScript, setMatchResults])

  const onRematch = async () => {
    if (!currentScript) return
    setMatching(true)
    try {
      const m = await scriptApi.match(currentScript.id, TENANT_KEY, product)
      setMatchResults(m.results)
      message.success('召回完成')
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setMatching(false)
    }
  }

  if (loading) return <div style={{ padding: 24, flex: 1, overflow: 'auto', width: '100%' }}>召回中...</div>
  if (!currentScript) return <Empty description="脚本不存在" />

  return (
    <div style={{ padding: 24, flex: 1, overflow: 'auto', width: '100%' }}>
      <Space style={{ marginBottom: 16 }} wrap>
        <Button onClick={() => navigate(`/scripts/${currentScript.id}`)}>
          重新编辑
        </Button>
        <span>
          脚本 #{currentScript.id} · {matchResults.length} 段
        </span>
        <Input
          placeholder="产品（如 测试 / 雪莲洗液），留空查通用素材"
          value={product}
          onChange={(e) => setProduct(e.target.value)}
          style={{ width: 320 }}
          allowClear
        />
        <Button type="primary" loading={matching} onClick={onRematch}>
          按此产品重新召回
        </Button>
      </Space>

      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        {matchResults.map((r) => (
          <Card key={r.seg_idx} title={`段 ${r.seg_idx}`}>
            <div style={{ marginBottom: 12, fontSize: 13, color: '#555' }}>
              <div>画面：{r.visual}</div>
              <div>文案：{r.copy}</div>
            </div>
            {r.phase1.length === 0 && r.phase2.length === 0 && (
              <Empty description="召回为空" />
            )}
            {r.phase1.length > 0 && (
              <>
                <div
                  style={{ fontSize: 12, color: '#888', marginBottom: 8 }}
                >
                  产品专属（默认勾选）
                </div>
                <Space wrap>
                  {r.phase1.map((m) => (
                    <MaterialCard
                      key={m.material_id}
                      mat={m}
                      checked={(selectedMaterials[r.seg_idx] ?? []).includes(
                        m.material_id,
                      )}
                      onToggle={() =>
                        toggleMaterial(r.seg_idx, m.material_id)
                      }
                    />
                  ))}
                </Space>
              </>
            )}
            {r.phase2.length > 0 && (
              <>
                <div
                  style={{ fontSize: 12, color: '#888', margin: '12px 0 8px' }}
                >
                  通用兜底
                </div>
                <Space wrap>
                  {r.phase2.map((m) => (
                    <MaterialCard
                      key={m.material_id}
                      mat={m}
                      checked={(selectedMaterials[r.seg_idx] ?? []).includes(
                        m.material_id,
                      )}
                      onToggle={() =>
                        toggleMaterial(r.seg_idx, m.material_id)
                      }
                      dim
                    />
                  ))}
                </Space>
              </>
            )}
          </Card>
        ))}
      </Space>

      <div
        style={{
          position: 'sticky',
          bottom: 0,
          padding: 16,
          background: '#fff',
          borderTop: '1px solid #eee',
          marginTop: 16,
        }}
      >
        <ExportButton scriptId={currentScript.id} tenantKey={TENANT_KEY} />
      </div>
    </div>
  )
}
