import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button, Card, Input, Space, message, Modal, Tag } from 'antd'
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons'
import { useScriptStore } from '../../stores/scriptStore'
import { scriptApi } from '../../api/script'
import type { ScriptSegment } from '../../types/script'

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return '未知错误'
}

export default function ScriptEditor() {
  const { scriptId } = useParams<{ scriptId: string }>()
  const navigate = useNavigate()
  const { currentScript, setScript, updateSegments } = useScriptStore()
  const [loading, setLoading] = useState(true)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!scriptId) return
    setLoading(true)
    scriptApi
      .get(Number(scriptId))
      .then((s) => {
        setScript(s)
        setLoading(false)
      })
      .catch((e: unknown) => {
        message.error(getErrorMessage(e))
        setLoading(false)
      })
  }, [scriptId, setScript])

  useEffect(() => {
    if (!dirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  if (loading || !currentScript) return <div>加载中...</div>

  const segments = currentScript.segments
  const isConfirmed = currentScript.status === 'CONFIRMED'

  const onChange = (idx: number, field: 'visual' | 'copy', value: string) => {
    const next = segments.map((s, i) =>
      i === idx ? { ...s, [field]: value } : s,
    )
    updateSegments(next)
    setDirty(true)
  }

  const onAdd = () => {
    const next: ScriptSegment[] = [
      ...segments,
      {
        idx: segments.length,
        start_time: 0,
        end_time: 0,
        visual: '',
        copy: '',
      },
    ]
    updateSegments(next)
    setDirty(true)
  }

  const onDelete = (idx: number) => {
    const next = segments
      .filter((_, i) => i !== idx)
      .map((s, i) => ({ ...s, idx: i }))
    updateSegments(next)
    setDirty(true)
  }

  const onSave = async () => {
    try {
      await scriptApi.update(currentScript.id, segments)
      setDirty(false)
      message.success('已保存草稿')
    } catch (e: unknown) {
      message.error(getErrorMessage(e))
    }
  }

  const onConfirmAndMatch = async () => {
    try {
      if (dirty) {
        await scriptApi.update(currentScript.id, segments)
        setDirty(false)
      }
      await scriptApi.confirm(currentScript.id)
      navigate(`/scripts/${currentScript.id}/preview`)
    } catch (e: unknown) {
      message.error(getErrorMessage(e))
    }
  }

  const onReopen = () => {
    Modal.confirm({
      title: '重新编辑会清空召回结果，确定吗？',
      onOk: async () => {
        try {
          await scriptApi.reopen(currentScript.id)
          const refreshed = await scriptApi.get(currentScript.id)
          setScript(refreshed)
        } catch (e: unknown) {
          message.error(getErrorMessage(e))
        }
      },
    })
  }

  return (
    <div style={{ padding: 24, flex: 1, overflow: 'auto', width: '100%' }}>
      <Card
        title={`脚本编辑 #${currentScript.id}`}
        extra={
          <Tag color={isConfirmed ? 'green' : 'orange'}>
            {currentScript.status}
          </Tag>
        }
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          {segments.map((seg, i) => (
            <Card
              key={i}
              type="inner"
              title={`段 ${seg.idx}`}
              extra={
                !isConfirmed && (
                  <Button
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={() => onDelete(i)}
                  />
                )
              }
            >
              <Space
                direction="vertical"
                size="small"
                style={{ width: '100%' }}
              >
                <div>
                  <div style={{ fontSize: 12, color: '#888' }}>
                    画面（visual）
                  </div>
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
                  时间：{seg.start_time.toFixed(2)}s -{' '}
                  {seg.end_time.toFixed(2)}s
                </div>
              </Space>
            </Card>
          ))}
          {!isConfirmed && (
            <Button icon={<PlusOutlined />} onClick={onAdd} block>
              加一段
            </Button>
          )}
        </Space>
      </Card>

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
        <Space>
          {isConfirmed ? (
            <Button onClick={onReopen}>重新编辑</Button>
          ) : (
            <>
              <Button onClick={onSave}>保存草稿</Button>
              <Button type="primary" onClick={onConfirmAndMatch}>
                确认脚本并匹配
              </Button>
            </>
          )}
        </Space>
      </div>
    </div>
  )
}
