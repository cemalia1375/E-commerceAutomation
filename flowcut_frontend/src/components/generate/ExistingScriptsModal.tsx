import { useEffect, useState } from 'react'
import { Modal, Table, Tag, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useNavigate } from 'react-router-dom'
import { scriptApi, type ScriptListItem } from '../../api/script'

interface ExistingScriptsModalProps {
  open: boolean
  tenantKey: string
  onClose: () => void
}

const SOURCE_LABEL: Record<string, string> = {
  decomposed: '拆镜',
  uploaded: '上传',
}

const STATUS_COLOR: Record<string, string> = {
  DRAFT: 'blue',
  CONFIRMED: 'green',
  PROCESSING: 'gold',
  FAILED: 'red',
}

function formatTime(s: string): string {
  if (!s) return ''
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  return d.toLocaleString('zh-CN', { hour12: false })
}

export default function ExistingScriptsModal({
  open,
  tenantKey,
  onClose,
}: ExistingScriptsModalProps) {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [items, setItems] = useState<ScriptListItem[]>([])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    const load = async (): Promise<void> => {
      setLoading(true)
      try {
        const resp = await scriptApi.list(tenantKey)
        if (cancelled) return
        setItems(resp.scripts ?? [])
      } catch (err: unknown) {
        if (cancelled) return
        const msg = err instanceof Error ? err.message : '加载失败'
        message.error(`加载脚本列表失败：${msg}`)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [open, tenantKey])

  const columns: ColumnsType<ScriptListItem> = [
    {
      title: 'ID',
      dataIndex: 'id',
      width: 80,
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 80,
      render: (s: string) => SOURCE_LABEL[s] ?? s,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (s: string) => (
        <Tag color={STATUS_COLOR[s] ?? 'default'}>{s}</Tag>
      ),
    },
    {
      title: '产品',
      dataIndex: 'product',
      width: 120,
      render: (p: string | null) => p || '—',
    },
    {
      title: '段数',
      key: 'segCount',
      width: 80,
      render: (_v, r) => r.segments?.length ?? 0,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      render: formatTime,
    },
  ]

  return (
    <Modal
      open={open}
      onCancel={onClose}
      title="已有脚本"
      footer={null}
      width={760}
      destroyOnHidden
    >
      <Table<ScriptListItem>
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={{ pageSize: 10, showSizeChanger: false }}
        onRow={(record) => ({
          onClick: () => {
            onClose()
            navigate(`/workspace/${record.id}`)
          },
          style: { cursor: 'pointer' },
        })}
        size="small"
      />
    </Modal>
  )
}
