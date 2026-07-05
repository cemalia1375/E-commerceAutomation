import { useState } from 'react'
import { Form, Input, Button, Select, Typography, Divider, message } from 'antd'
import styles from './SetupWizard.module.css'

const { Title, Text } = Typography

interface ConfigValues {
  GOOGLE_API_KEY: string
  GOOGLE_MODEL: string
  FLOWCUT_LLM_PROVIDER: string
  VOLCENGINE_API_KEY: string
  VOLCENGINE_API_BASE: string
  GEMINI_PROXY: string
  MYSQL_HOST: string
  MYSQL_PORT: string
  MYSQL_USER: string
  MYSQL_PASSWORD: string
  MYSQL_DB: string
  QDRANT_URL: string
  FLOWCUT_OSS_ENDPOINT: string
  FLOWCUT_OSS_ACCESS_KEY_ID: string
  FLOWCUT_OSS_ACCESS_KEY_SECRET: string
  FLOWCUT_OSS_BUCKET: string
  FLOWCUT_OSS_REGION: string
}

export default function SetupWizard() {
  const [saving, setSaving] = useState(false)
  const [llmProvider, setLlmProvider] = useState('gemini')

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
          initialValues={{
            FLOWCUT_LLM_PROVIDER: 'gemini',
            GOOGLE_MODEL: 'gemini-2.5-flash',
            MYSQL_PORT: '3306',
            QDRANT_URL: '',
            VOLCENGINE_API_BASE: 'https://ark.cn-beijing.volces.com/api/v3',
          }}
        >
          <Form.Item label="LLM 提供方" name="FLOWCUT_LLM_PROVIDER">
            <Select
              onChange={(v) => setLlmProvider(v)}
              options={[
                { value: 'gemini', label: 'Google Gemini（需 API Key，格式 AIza...）' },
                { value: 'volcengine', label: '火山引擎 / 豆包（需 AK/SK）' },
              ]}
            />
          </Form.Item>

          {llmProvider === 'gemini' && (
            <>
              <Form.Item label="Google API Key" name="GOOGLE_API_KEY" rules={[{ required: true, message: '必填' }]}>
                <Input.Password placeholder="AIza...（Google AI Studio 获取）" />
              </Form.Item>
              <Form.Item label="Model" name="GOOGLE_MODEL">
                <Input placeholder="gemini-2.5-flash" />
              </Form.Item>
              <Form.Item label="代理地址（国内必填）" name="GEMINI_PROXY">
                <Input placeholder="http://127.0.0.1:7890" />
              </Form.Item>
            </>
          )}

          {llmProvider === 'volcengine' && (
            <>
              <Form.Item label="API Key" name="VOLCENGINE_API_KEY" rules={[{ required: true, message: '必填' }]}>
                <Input.Password placeholder="火山引擎 API Key" />
              </Form.Item>
              <Form.Item label="API Base" name="VOLCENGINE_API_BASE">
                <Input placeholder="https://ark.cn-beijing.volces.com/api/v3" />
              </Form.Item>
              <Form.Item label="Model" name="GOOGLE_MODEL">
                <Input placeholder="doubao-seed-2-0-lite-260215" />
              </Form.Item>
            </>
          )}

          <Divider plain>MySQL</Divider>

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

          <Divider plain>Qdrant（可选）</Divider>

          <Form.Item label="Qdrant URL" name="QDRANT_URL">
            <Input placeholder="留空使用默认云端实例" />
          </Form.Item>

          <Divider plain>OSS 对象存储（可选，素材/成片云存储）</Divider>

          <Form.Item label="Endpoint" name="FLOWCUT_OSS_ENDPOINT">
            <Input placeholder="tos-cn-guangzhou.volces.com" />
          </Form.Item>

          <Form.Item label="Access Key ID" name="FLOWCUT_OSS_ACCESS_KEY_ID">
            <Input />
          </Form.Item>

          <Form.Item label="Access Key Secret" name="FLOWCUT_OSS_ACCESS_KEY_SECRET">
            <Input.Password />
          </Form.Item>

          <Form.Item label="Bucket" name="FLOWCUT_OSS_BUCKET">
            <Input placeholder="flowcut" />
          </Form.Item>

          <Form.Item label="Region" name="FLOWCUT_OSS_REGION">
            <Input placeholder="cn-guangzhou" />
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
