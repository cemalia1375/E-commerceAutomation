import { useEffect } from 'react'
import { Spin } from 'antd'
import AppRouter from './router'
import { useAuthStore } from './stores/authStore'

export default function App() {
  const status = useAuthStore((s) => s.status)

  // 启动时探测登录态；并监听 API 层 401 派发的强制登出事件。
  useEffect(() => {
    void useAuthStore.getState().checkAuth()
    const onUnauthorized = () => useAuthStore.getState().setAnon()
    window.addEventListener('auth:unauthorized', onUnauthorized)
    return () => window.removeEventListener('auth:unauthorized', onUnauthorized)
  }, [])

  if (status === 'loading') {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Spin size="large" />
      </div>
    )
  }

  return <AppRouter />
}
