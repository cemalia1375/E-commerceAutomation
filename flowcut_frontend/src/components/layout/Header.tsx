import { useNavigate, useLocation } from 'react-router-dom'
import styles from './Header.module.css'

const TABS = [
  { path: '/',         label: '生成' },
  { path: '/material', label: '素材库' },
  { path: '/creative', label: '成片库' },
  { path: '/dashboard', label: '数据看板' },
]

export default function Header() {
  const navigate = useNavigate()
  const { pathname } = useLocation()

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
      </div>
      <div className={styles.right}>
        <div className={styles.avatar}>运</div>
      </div>
    </header>
  )
}
