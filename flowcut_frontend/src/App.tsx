import { useEffect, useState } from 'react'
import { Spin } from 'antd'
import AppRouter from './router'
import { useAuthStore } from './stores/authStore'
import { updateApiPort } from './api/client'
import SplashScreen from './components/setup/SplashScreen'

const isSetupRoute = () => window.location.hash === '#/setup' || window.location.pathname === '/setup'

export default function App() {
  const status = useAuthStore((s) => s.status)
  // setup 路由不需要等后台；其余 Electron 页面等 backend-ready；web 模式直接就绪
  const [backendReady, setBackendReady] = useState(!window.electronAPI || isSetupRoute())
  const [backendError, setBackendError] = useState<string | null>(null)

  useEffect(() => {
    if (!window.electronAPI) return
    window.electronAPI.onBackendReady((port) => {
      updateApiPort(port)
      setBackendReady(true)
    })
    window.electronAPI.onBackendError((msg) => setBackendError(msg))
  }, [])

  // 启动时探测登录态；并监听 API 层 401 派发的强制登出事件。
  useEffect(() => {
    if (!backendReady) return
    void useAuthStore.getState().checkAuth()
    const onUnauthorized = () => useAuthStore.getState().setAnon()
    window.addEventListener('auth:unauthorized', onUnauthorized)
    return () => window.removeEventListener('auth:unauthorized', onUnauthorized)
  }, [backendReady])

  // 同一页面多个视频/音频时，播放某个媒体即暂停其余，避免音轨重叠。
  // play 事件不冒泡，需在捕获阶段于 document 层监听。
  useEffect(() => {
    const handlePlay = (event: Event) => {
      const target = event.target
      if (!(target instanceof HTMLMediaElement)) return
      document
        .querySelectorAll<HTMLMediaElement>('video, audio')
        .forEach((el) => {
          if (el !== target) el.pause()
        })
    }
    document.addEventListener('play', handlePlay, true)
    return () => document.removeEventListener('play', handlePlay, true)
  }, [])


  if (!backendReady) {
    return <SplashScreen error={backendError} />
  }

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
