import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Empty, Space, Tag, message } from 'antd'
import { composeHighlightCreative, getHighlightCreativeByScript } from '../../api/qianchuan'
import { scriptApi } from '../../api/script'
import { useCreativeStore } from '../../stores/creativeStore'
import { useScriptStore } from '../../stores/scriptStore'
import { useAuthStore } from '../../stores/authStore'
import type { Creative } from '../../types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8001'

function scoreText(value: number | undefined): string {
  return typeof value === 'number' ? value.toFixed(1) : '-'
}

function isHookCandidate(value: string | undefined): boolean {
  return value === 'primary_hook' || value === 'secondary_hook'
}

function SegmentPreviewVideo({ src }: { src: string }) {
  const [failed, setFailed] = useState(false)

  return (
    <div style={{ marginBottom: 12 }}>
      <video
        src={src}
        controls
        preload="metadata"
        onLoadedMetadata={() => setFailed(false)}
        onError={() => setFailed(true)}
        style={{
          width: '100%',
          maxWidth: 520,
          borderRadius: 8,
          background: '#0f172a',
        }}
      />
      {failed && (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 8, maxWidth: 520 }}
          message="片段预览加载失败"
          description="后端需要从原视频截取该时间段。请检查 ffmpeg 是否已安装，以及后端日志中 preview.mp4 接口的错误信息。"
        />
      )}
    </div>
  )
}

export default function HighlightTab() {
  const TENANT_KEY = useAuthStore((s) => s.user?.tenantKey) ?? 'flowcut'
  const navigate = useNavigate()
  const script = useScriptStore((s) => s.currentScript)
  const setCreativeSubTab = useCreativeStore((s) => s.setSubTab)
  const segments = script?.segments ?? []
  const [saving, setSaving] = useState(false)
  const [composing, setComposing] = useState(false)
  const [savedCreativeId, setSavedCreativeId] = useState<number | null>(null)
  const [highlightCreative, setHighlightCreative] = useState<Creative | null>(null)

  useEffect(() => {
    let cancelled = false
    setHighlightCreative(null)
    setSavedCreativeId(null)
    if (!script?.id) return
    getHighlightCreativeByScript(TENANT_KEY, script.id)
      .then((creative) => {
        if (cancelled) return
        setHighlightCreative(creative)
        setSavedCreativeId(creative ? Number(creative.id) : null)
      })
      .catch(() => {
        if (!cancelled) setHighlightCreative(null)
      })
    return () => {
      cancelled = true
    }
  }, [script?.id, TENANT_KEY])

  const saveToCreativeLibrary = async (): Promise<number | null> => {
    if (!script?.id) return null
    if (highlightCreative?.id) {
      const existingId = Number(highlightCreative.id)
      setSavedCreativeId(existingId)
      message.success('已保存到成片库')
      return existingId
    }
    setSaving(true)
    try {
      const result = await scriptApi.saveHighlightCreative(script.id, TENANT_KEY, 'highlight_original')
      setSavedCreativeId(result.creative_id)
      message.success('已保存到成片库')
      return result.creative_id
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存失败')
      return null
    } finally {
      setSaving(false)
    }
  }

  const composeVideo = async () => {
    const creativeId = savedCreativeId ?? (await saveToCreativeLibrary())
    if (!creativeId) return
    setComposing(true)
    try {
      await composeHighlightCreative(creativeId)
      message.success('已开始生成组合视频')
      setCreativeSubTab('highlight')
      navigate('/creative')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '生成组合视频失败')
    } finally {
      setComposing(false)
    }
  }

  const goCreativeHighlight = () => {
    setCreativeSubTab('highlight')
    navigate('/creative')
  }

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Alert
        type="info"
        showIcon
        message="高光判断"
        description={
          highlightCreative?.creativeType === 'highlight_digital_human'
            ? '这里展示分段高光评分、结尾衔接判断和桥接建议。本任务已绑定数字人承接视频，生成组合视频时会使用高光+数字人。'
            : '这里展示分段高光评分、结尾衔接判断和桥接建议。保存后需要继续生成组合视频，才会在成片库得到可播放的高光成片。'
        }
        action={
          <Space>
            <Button
              size="small"
              loading={saving}
              disabled={!script?.id || segments.length === 0 || composing}
              onClick={saveToCreativeLibrary}
            >
              保存分析
            </Button>
            <Button
              type="primary"
              size="small"
              loading={composing}
              disabled={!script?.id || segments.length === 0 || saving}
              onClick={composeVideo}
            >
              保存并生成组合视频
            </Button>
          </Space>
        }
      />

      {highlightCreative?.creativeType === 'highlight_digital_human' && (
        <Card
          title="承接数字人"
          extra={<Tag color="green">{highlightCreative.connectorRole || '数字人'}</Tag>}
        >
          <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            {highlightCreative.connectorAssetOssUrl ? (
              <video
                src={highlightCreative.connectorAssetOssUrl}
                controls
                preload="metadata"
                style={{
                  width: '100%',
                  maxWidth: 360,
                  borderRadius: 8,
                  background: '#0f172a',
                }}
              />
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="数字人视频地址未返回"
              />
            )}
            <div style={{ color: '#475569', lineHeight: 1.8, minWidth: 240 }}>
              <div>
                <strong>文件：</strong>
                {highlightCreative.connectorAssetName || '-'}
              </div>
              <div>
                <strong>资产 ID：</strong>
                {highlightCreative.connectorAssetId ?? '-'}
              </div>
              <div>
                <strong>组合类型：</strong>
                高光 + 数字人
              </div>
            </div>
          </div>
        </Card>
      )}

      {savedCreativeId && (
        <Alert
          type="success"
          showIcon
          message="下一步：组装视频"
          description="当前高光分析已保存。你可以直接生成“高光 + 原片”的组合视频；如果高光本身就在原片开头，后端会直接输出原片，避免重复拼接。"
          action={
            <Space>
              <Button size="small" loading={composing} onClick={composeVideo}>
                生成组合视频
              </Button>
              <Button size="small" onClick={goCreativeHighlight}>
                去成片库查看
              </Button>
            </Space>
          }
        />
      )}

      {segments.length === 0 ? (
        <Empty description="拆镜完成后将在这里显示分段" />
      ) : (
        segments.map((seg, index) => (
          <Card
            key={`${seg.idx}-${index}`}
            title={`段 ${Number(seg.idx ?? index) + 1}`}
            extra={
              <Tag color="blue">
                {Number(seg.start_time || 0).toFixed(2)}s - {Number(seg.end_time || 0).toFixed(2)}s
              </Tag>
            }
          >
            {script?.reference_video_id && (
              <SegmentPreviewVideo
                src={`${API_BASE}/flowcut/scripts/${script.id}/segments/${index}/preview.mp4`}
              />
            )}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
              {seg.narrative_role && <Tag>{seg.narrative_role}</Tag>}
              {seg.candidate_use && <Tag color="purple">{seg.candidate_use}</Tag>}
              <Tag color="red">高光 {scoreText(seg.hook_strength)}</Tag>
              <Tag color="green">衔接 {scoreText(seg.ending_connectability)}</Tag>
              <Tag color="orange">依赖 {scoreText(seg.context_dependency)}</Tag>
              <Tag color="volcano">风险 {scoreText(seg.continuity_risk)}</Tag>
            </div>
            <div style={{ color: '#475569', lineHeight: 1.8 }}>
              <div>
                <strong>画面：</strong>
                {seg.visual || '无'}
              </div>
              <div>
                <strong>文案：</strong>
                {seg.copy || '无'}
              </div>
              {seg.ending_state && (
                <div>
                  <strong>结尾状态：</strong>
                  {seg.ending_state}
                </div>
              )}
              {seg.open_question && (
                <div>
                  <strong>悬念问题：</strong>
                  {seg.open_question}
                </div>
              )}
              {seg.bridge_text && (
                <div>
                  <strong>桥接话术：</strong>
                  {seg.bridge_text}
                </div>
              )}
              {seg.followup_fit && isHookCandidate(seg.candidate_use) && (
                <div>
                  <strong>衔接适配：</strong>
                  原片 {scoreText(seg.followup_fit.original_video)} / 数字人{' '}
                  {scoreText(seg.followup_fit.digital_human)} / 广告{' '}
                  {scoreText(seg.followup_fit.ad)}
                  {seg.followup_fit.reason ? `，${seg.followup_fit.reason}` : ''}
                </div>
              )}
              {seg.followup_fit && !isHookCandidate(seg.candidate_use) && (
                <div>
                  <strong>衔接适配：</strong>
                  非高光候选段，不作为前置衔接推荐。
                  {seg.followup_fit.reason ? ` ${seg.followup_fit.reason}` : ''}
                </div>
              )}
              {seg.reason && (
                <div>
                  <strong>判断理由：</strong>
                  {seg.reason}
                </div>
              )}
            </div>
          </Card>
        ))
      )}
    </div>
  )
}
