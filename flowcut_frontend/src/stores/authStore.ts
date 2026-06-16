import { create } from 'zustand'
import * as authApi from '../api/auth'

export type AuthStatus = 'loading' | 'authed' | 'anon'

export interface CurrentUser {
  username: string
  tenantKey: string
  displayName: string
}

interface AuthState {
  user: CurrentUser | null
  status: AuthStatus

  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
  setAnon: () => void
}

function toCurrentUser(u: authApi.AuthUser): CurrentUser {
  return { username: u.username, tenantKey: u.tenant_key, displayName: u.display_name }
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  status: 'loading',

  login: async (username, password) => {
    const u = await authApi.login(username, password)
    set({ user: toCurrentUser(u), status: 'authed' })
  },

  logout: async () => {
    try {
      await authApi.logout()
    } finally {
      set({ user: null, status: 'anon' })
    }
  },

  checkAuth: async () => {
    try {
      const u = await authApi.getMe()
      set({ user: toCurrentUser(u), status: 'authed' })
    } catch {
      set({ user: null, status: 'anon' })
    }
  },

  setAnon: () => set({ user: null, status: 'anon' }),
}))

/**
 * 非组件场景（API 调用、localStorage 键名）读取当前工作台 tenant_key。
 * 未登录时回落 'flowcut'（后端以 session 为准，此值仅用于兜底/命名空间）。
 */
export function getTenantKey(): string {
  return useAuthStore.getState().user?.tenantKey ?? 'flowcut'
}
