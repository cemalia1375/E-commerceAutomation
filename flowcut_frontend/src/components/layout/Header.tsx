import { useEffect, useState } from 'react'
import { useNavigate, useLocation, matchPath } from 'react-router-dom'
import styles from './Header.module.css'

const TABS = [
  { path: '/',         label: '生成' },
  { path: '/material', label: '素材库' },
  { path: '/creative', label: '成片库' },
  { path: '/dashboard', label: '数据看板' },
]

const WORKSPACE_ID_KEY = 'flowcut.workspace.activeId'
const WORKSPACE_TAB_KEY = 'flowcut.workspace.activeTab'
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

export default function Header() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(
    () => readActiveWorkspaceId(),
  )

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
              navigate(`/workspace/${activeWorkspaceId}?tab=${tab}`)
            }}
          >
            工作台
          </button>
        )}
      </div>
      <div className={styles.right}>
        <div className={styles.avatar}>运</div>
      </div>
    </header>
  )
}
