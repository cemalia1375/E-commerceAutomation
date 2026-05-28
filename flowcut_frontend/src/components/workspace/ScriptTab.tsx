import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Input, Space, Spin, Tag, message } from 'antd'
import { CopyOutlined } from '@ant-design/icons'
import { useScriptStore } from '../../stores/scriptStore'
import { scriptApi } from '../../api/script'
import type { ScriptSegment } from '../../types/script'

const SAVE_DEBOUNCE_MS = 600

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return '未知错误'
}

function buildMarkdown(segments: ScriptSegment[]): string {
  return segments
    .map(
      (seg) =>
        `# 段 ${seg.idx + 1} (${seg.start_time}s-${seg.end_time}s)\n画面：${seg.visual}\n文案：${seg.copy}\n`,
    )
    .join('\n')
}

export default function ScriptTab() {
  const { currentScript, updateSegments, setScript } = useScriptStore()
  const [busy, setBusy] = useState(false)
  const saveTimerRef = useRef<number | null>(null)

  // 卸载时清掉 timer，但保持已 schedule 的请求自然触发
  useEffect(() => {
    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
      }
    }
  }, [])

  if (!currentScript) {
    return (
      <div style={{ padding: 24 }}>
        <Spin /> <span style={{ marginLeft: 8 }}>加载脚本…</span>
      </div>
    )
  }

  const { status, segments, id: scriptId } = currentScript

  if (status === 'PROCESSING') {
    return (
      <div style={{ padding: 24, color: '#666' }}>
        <Spin /> <span style={{ marginLeft: 8 }}>拆镜中，请稍候…</span>
      </div>
    )
  }

  if (status === 'FAILED') {
    return (
      <Alert
        type="error"
        showIcon
        style={{ margin: 24 }}
        message="拆镜失败"
        description={
          <span>
            该脚本拆镜失败，请返回 <a href="/">入口页</a> 重新发起。
          </span>
        }
      />
    )
  }

  const isConfirmed = status === 'CONFIRMED'

  const scheduleSave = (next: ScriptSegment[]): void => {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current)
    }
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null
      scriptApi.update(scriptId, next).catch((e: unknown) => {
        message.error(`保存失败：${getErrorMessage(e)}`)
      })
    }, SAVE_DEBOUNCE_MS)
  }

  const onChange = (idx: number, field: 'visual' | 'copy', value: string): void => {
    const next = segments.map((s, i) =>
      i === idx ? { ...s, [field]: value } : s,
    )
    updateSegments(next)
    scheduleSave(next)
  }

  const onCopyMarkdown = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(buildMarkdown(segments))
      message.success('已复制')
    } catch (e: unknown) {
      message.error(`复制失败：${getErrorMessage(e)}`)
    }
  }

  const onConfirm = async (): Promise<void> => {
    setBusy(true)
    try {
      // 先 flush 一次保存，确保草稿落库
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
        await scriptApi.update(scriptId, segments)
      }
      await scriptApi.confirm(scriptId)
      const refreshed = await scriptApi.get(scriptId)
      setScript(refreshed)
      message.success('已确认')
    } catch (e: unknown) {
      message.error(getErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const onReopen = async (): Promise<void> => {
    setBusy(true)
    try {
      await scriptApi.reopen(scriptId)
      const refreshed = await scriptApi.get(scriptId)
      setScript(refreshed)
      message.success('已切回草稿')
    } catch (e: unknown) {
      message.error(getErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 16 }}>
        <Tag color={isConfirmed ? 'green' : 'orange'}>{status}</Tag>
        <Button icon={<CopyOutlined />} onClick={onCopyMarkdown}>
          复制为 Markdown
        </Button>
        {isConfirmed ? (
          <Button onClick={onReopen} loading={busy}>
            取消确认
          </Button>
        ) : (
          <Button type="primary" onClick={onConfirm} loading={busy}>
            确认脚本
          </Button>
        )}
      </Space>

      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {segments.map((seg, i) => (
          <Card key={i} type="inner" title={`段 ${seg.idx + 1}`}>
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <div>
                <div style={{ fontSize: 12, color: '#888' }}>画面（visual）</div>
                <Input.TextArea
                  value={seg.visual}
                  onChange={(e) => onChange(i, 'visual', e.target.value)}
                  disabled={isConfirmed}
                  autoSize={{ minRows: 2 }}
                />
              </div>
              <div>
                <div style={{ fontSize: 12, color: '#888' }}>文案（copy）</div>
                <Input.TextArea
                  value={seg.copy}
                  onChange={(e) => onChange(i, 'copy', e.target.value)}
                  disabled={isConfirmed}
                  autoSize={{ minRows: 2 }}
                />
              </div>
              <div style={{ fontSize: 12, color: '#888' }}>
                时间：{seg.start_time.toFixed(2)}s - {seg.end_time.toFixed(2)}s
              </div>
            </Space>
          </Card>
        ))}
      </Space>
    </div>
  )
}
