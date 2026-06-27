import { useState } from 'react'
import { Form, Input, Button, Typography, Divider, message } from 'antd'
import styles from './SetupWizard.module.css'

const { Title, Text } = Typography

interface ConfigValues {
  GOOGLE_API_KEY: string
  GOOGLE_MODEL: string
  MYSQL_HOST: string
  MYSQL_PORT: string
  MYSQL_USER: string
  MYSQL_PASSWORD: string
  MYSQL_DB: string
  QDRANT_URL: string
}

export default function SetupWizard() {
  const [saving, setSaving] = useState(false)

  async function handleFinish(values: ConfigValues) {
    if (!window.electronAPI) {
      message.error('仅在 Electron 环境中可用')
      return
    }
    setSaving(true)
    try {
      await window.electronAPI.saveConfig(values as unknown as Record<string, string>)
    } catch {
      message.error('保存配置失败，请重试')
      setSaving(false)
    }
  }

  return (
    <div className={styles.container}>
      <div className={styles.card}>
        <Title level={3} style={{ marginBottom: 4 }}>FlowCut 初始配置</Title>
        <Text type="secondary">首次启动需要填写以下信息，保存后自动重启。</Text>

        <Divider />

        <Form
          layout="vertical"
          onFinish={handleFinish}
          initialValues={{ GOOGLE_MODEL: 'gemini-2.5-flash', MYSQL_PORT: '3306', QDRANT_URL: '' }}
        >
          <Form.Item label="Google API Key" name="GOOGLE_API_KEY" rules={[{ required: true, message: '必填' }]}>
            <Input.Password placeholder="AIza..." />
          </Form.Item>

          <Form.Item label="Google Model" name="GOOGLE_MODEL">
            <Input placeholder="gemini-2.5-flash" />
          </Form.Item>

          <Divider orientation="left" plain>MySQL</Divider>

          <Form.Item label="Host" name="MYSQL_HOST" rules={[{ required: true, message: '必填' }]}>
            <Input placeholder="127.0.0.1" />
          </Form.Item>

          <Form.Item label="Port" name="MYSQL_PORT">
            <Input placeholder="3306" />
          </Form.Item>

          <Form.Item label="User" name="MYSQL_USER" rules={[{ required: true, message: '必填' }]}>
            <Input />
          </Form.Item>

          <Form.Item label="Password" name="MYSQL_PASSWORD" rules={[{ required: true, message: '必填' }]}>
            <Input.Password />
          </Form.Item>

          <Form.Item label="Database" name="MYSQL_DB" rules={[{ required: true, message: '必填' }]}>
            <Input />
          </Form.Item>

          <Divider orientation="left" plain>Qdrant（可选）</Divider>

          <Form.Item label="Qdrant URL" name="QDRANT_URL">
            <Input placeholder="留空使用默认云端实例" />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 8 }}>
            <Button type="primary" htmlType="submit" block loading={saving}>
              保存并启动
            </Button>
          </Form.Item>
        </Form>
      </div>
    </div>
  )
}
