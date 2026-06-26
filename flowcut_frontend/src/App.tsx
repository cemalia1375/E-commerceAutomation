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
