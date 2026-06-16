import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuthStore } from '../../stores/authStore'

/** 登录守卫：未登录跳 /login，已登录渲染子内容。loading 由 App 顶层处理。 */
export default function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status)
  if (status === 'anon') {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}
