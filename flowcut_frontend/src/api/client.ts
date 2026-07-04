import axios from 'axios'

const DEFAULT_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8001'

// 动态 base URL：axios 用 apiClient，原生 fetch 用 getApiBase()
let _currentBase = DEFAULT_BASE

export function getApiBase(): string {
  return _currentBase
}

export function updateApiPort(port: number): void {
  _currentBase = `http://localhost:${port}`
  apiClient.defaults.baseURL = _currentBase
}

export const apiClient = axios.create({
  baseURL: _currentBase,
  withCredentials: true, // 携带登录会话 cookie
})

// 受保护接口返回 401 时，广播强制登出事件（/auth/* 自身的 401 不触发，避免登录探测循环）。
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const url: string = error?.config?.url ?? ''
    if (error?.response?.status === 401 && !url.startsWith('/auth/')) {
      window.dispatchEvent(new Event('auth:unauthorized'))
    }
    return Promise.reject(error)
  },
)
