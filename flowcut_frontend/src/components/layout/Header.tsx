import { useEffect, useState } from 'react'
import { useNavigate, useLocation, matchPath } from 'react-router-dom'
import { Dropdown, type MenuProps } from 'antd'
import { useAuthStore } from '../../stores/authStore'
import styles from './Header.module.css'

const TABS = [
  { path: '/',         label: '生成' },
  { path: '/material', label: '素材库' },
  { path: '/creative', label: '成片库' },
  { path: '/dashboard', label: '数据看板' },
]

const WORKSPACE_ID_KEY = 'flowcut.workspace.activeId'
const WORKSPACE_TAB_KEY = 'flowcut.workspace.activeTab'
const WORKSPACE_MODE_KEY = 'flowcut.workspace.activeMode'
const WORKSPACE_CHANGED_EVENT = 'workspace-changed'

function readActiveWorkspaceId(): string | null {
  try {
    return localStorage.getItem(WORKSPACE_ID_KEY)
  } catch {
    return null
  }
}

function readActiveWorkspaceTab(): string {
  try {
    return localStorage.getItem(WORKSPACE_TAB_KEY) ?? 'script'
  } catch {
    return 'script'
  }
}

function readActiveWorkspaceMode(): string {
  try {
    return localStorage.getItem(WORKSPACE_MODE_KEY) ?? 'reference'
  } catch {
    return 'reference'
  }
}

export default function Header() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(
    () => readActiveWorkspaceId(),
  )

  const userMenu: MenuProps['items'] = [
    {
      key: 'logout',
      label: '退出登录',
      onClick: () => {
        void logout().then(() => navigate('/login', { replace: true }))
      },
    },
  ]

  useEffect(() => {
    const sync = (): void => {
      setActiveWorkspaceId(readActiveWorkspaceId())
    }
    window.addEventListener('storage', sync)
    window.addEventListener(WORKSPACE_CHANGED_EVENT, sync)
    return () => {
      window.removeEventListener('storage', sync)
      window.removeEventListener(WORKSPACE_CHANGED_EVENT, sync)
    }
  }, [])

  const workspaceMatch = matchPath('/workspace/:scriptId', pathname)
  const isWorkspaceActive = workspaceMatch !== null

  return (
    <header className={styles.header}>
      <a className={styles.logo} href="/">
        <div className={styles.logoIcon}>✦</div>
        <span className={styles.logoText}>FlowCut</span>
      </a>
      <div className={styles.tabs}>
        {TABS.map((t) => (
          <button
            key={t.path}
            className={`${styles.tab} ${pathname === t.path ? styles.active : ''}`}
            onClick={() => navigate(t.path)}
          >
            {t.label}
          </button>
        ))}
        {activeWorkspaceId && (
          <button
            key="workspace"
            className={`${styles.tab} ${isWorkspaceActive ? styles.active : ''}`}
            onClick={() => {
              const tab = readActiveWorkspaceTab()
              const mode = readActiveWorkspaceMode()
              const params = new URLSearchParams()
              if (mode === 'highlight') params.set('mode', 'highlight')
              params.set('tab', mode === 'highlight' && tab !== 'script' ? 'highlight' : tab)
              navigate(`/workspace/${activeWorkspaceId}?${params.toString()}`)
            }}
          >
            工作台
          </button>
        )}
      </div>
      <div className={styles.right}>
        <Dropdown menu={{ items: userMenu }} trigger={['click']} placement="bottomRight">
          <div className={styles.avatar} title={user?.displayName ?? ''} style={{ cursor: 'pointer' }}>
            {(user?.displayName ?? user?.username ?? '运').slice(0, 1)}
          </div>
        </Dropdown>
      </div>
    </header>
  )
}
