import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { Button, Card, Form, Input, message } from 'antd'
import { useAuthStore } from '../../stores/authStore'

interface LoginValues {
  username: string
  password: string
}

export default function LoginPage() {
  const navigate = useNavigate()
  const status = useAuthStore((s) => s.status)
  const login = useAuthStore((s) => s.login)
  const [submitting, setSubmitting] = useState(false)

  if (status === 'authed') {
    return <Navigate to="/" replace />
  }

  const onFinish = async (values: LoginValues) => {
    setSubmitting(true)
    try {
      await login(values.username.trim(), values.password)
      message.success('登录成功')
      navigate('/', { replace: true })
    } catch (err) {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
      message.error(detail || '用户名或密码错误')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f5f6f8',
      }}
    >
      <Card style={{ width: 360 }} title="登录 FlowCut">
        <Form layout="vertical" onFinish={onFinish} disabled={submitting}>
          <Form.Item
            label="用户名"
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input autoFocus placeholder="用户名" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password placeholder="密码" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  )
}
