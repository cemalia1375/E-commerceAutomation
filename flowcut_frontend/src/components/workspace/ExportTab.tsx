import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Space, Spin, Statistic, message } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import { useScriptStore } from '../../stores/scriptStore'
import { useAuthStore } from '../../stores/authStore'
import { scriptApi, taskApi } from '../../api/script'
import type { TaskStatus } from '../../types/script'

const POLL_INTERVAL_MS = 2000
const MAX_CONSECUTIVE_POLL_FAILURES = 5

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

function isTerminal(status: string): boolean {
  return status === 'succeeded' || status === 'failed'
}

interface MissingItem {
  seg_idx: number
  material_id: number
}

interface TaskResult extends TaskStatus {
  // 后端 result_url 后可能附带元信息（约定走 result_url 直链），missing 经由后端日志 — 此处暂留口子
  missing_materials?: MissingItem[]
}

export default function ExportTab() {
  const TENANT_KEY = useAuthStore((s) => s.user?.tenantKey) ?? 'flowcut'
  const { currentScript, selectedMaterials } = useScriptStore()
  const [submitting, setSubmitting] = useState(false)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [task, setTask] = useState<TaskResult | null>(null)
  const [pollAborted, setPollAborted] = useState(false)
  const timerRef = useRef<number | null>(null)
  const failuresRef = useRef(0)

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }
  }, [])

  if (!currentScript) {
    return <div style={{ padding: 24 }}>加载中…</div>
  }

  const totalSegs = currentScript.segments.length
  const totalSelected = Object.values(selectedMaterials).reduce(
    (acc, ids) => acc + ids.length,
    0,
  )

  const startPolling = (tid: string): void => {
    const tick = async (): Promise<void> => {
      try {
        const t = (await taskApi.get(tid)) as TaskResult
        failuresRef.current = 0
        setTask(t)
        if (!isTerminal(t.status)) {
          timerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS)
        }
      } catch (e: unknown) {
        failuresRef.current += 1
        if (failuresRef.current >= MAX_CONSECUTIVE_POLL_FAILURES) {
          message.error(
            `轮询多次失败，请检查后端日志：${getErrorMessage(e)}`,
          )
          setPollAborted(true)
          if (timerRef.current !== null) {
            window.clearTimeout(timerRef.current)
            timerRef.current = null
          }
          return
        }
        timerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS)
      }
    }
    tick()
  }

  const onExport = async (): Promise<void> => {
    if (totalSelected === 0) {
      message.warning('至少选一个素材')
      return
    }
    setSubmitting(true)
    setTask(null)
    setTaskId(null)
    setPollAborted(false)
    failuresRef.current = 0
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
    try {
      const resp = await scriptApi.export(
        currentScript.id,
        selectedMaterials,
        TENANT_KEY,
      )
      setTaskId(resp.task_id)
      startPolling(resp.task_id)
    } catch (e: unknown) {
      message.error(getErrorMessage(e))
    } finally {
      setSubmitting(false)
    }
  }

  const running =
    taskId !== null &&
    !pollAborted &&
    (!task || !isTerminal(task.status))
  const succeeded = task?.status === 'succeeded' && task.result_url
  const failed = task?.status === 'failed'

  return (
    <div style={{ padding: 24 }}>
      <Card>
        <Space size="large">
          <Statistic title="总段数" value={totalSegs} />
          <Statistic title="已选素材数" value={totalSelected} />
        </Space>
        <div style={{ marginTop: 24 }}>
          <Button
            type="primary"
            size="large"
            icon={<DownloadOutlined />}
            disabled={totalSelected === 0 || submitting || running}
            loading={submitting || running}
            onClick={onExport}
          >
            导出 zip 包
          </Button>
          {totalSelected === 0 && (
            <span style={{ marginLeft: 12, color: '#faad14' }}>
              请先在素材匹配 Tab 勾选素材
            </span>
          )}
        </div>
      </Card>

      {running && (
        <Alert
          style={{ marginTop: 16 }}
          type="info"
          showIcon
          icon={<Spin size="small" />}
          message="任务执行中…"
          description={`Task ID: ${taskId}`}
        />
      )}

      {pollAborted && !succeeded && !failed && (
        <Alert
          style={{ marginTop: 16 }}
          type="warning"
          showIcon
          message="轮询已停止"
          description="连续多次未能获取任务状态，请检查后端日志后重新点击「导出 zip 包」。"
        />
      )}

      {succeeded && task.result_url && (
        <Alert
          style={{ marginTop: 16 }}
          type="success"
          showIcon
          message="导出成功"
          description={
            <Space direction="vertical">
              <a href={task.result_url} target="_blank" rel="noreferrer">
                下载链接
              </a>
              <Button
                type="primary"
                onClick={() => window.open(task.result_url ?? undefined)}
              >
                打开下载
              </Button>
              {task.missing_materials && task.missing_materials.length > 0 && (
                <div style={{ color: '#faad14' }}>
                  缺失素材：
                  {task.missing_materials
                    .map((m) => `段${m.seg_idx + 1}#${m.material_id}`)
                    .join('、')}
                </div>
              )}
            </Space>
          }
        />
      )}

      {failed && (
        <Alert
          style={{ marginTop: 16 }}
          type="error"
          showIcon
          message="导出失败"
          description={task?.last_error || '未知错误'}
        />
      )}
    </div>
  )
}
