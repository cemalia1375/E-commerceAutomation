import { Outlet } from 'react-router-dom'
import Header from './Header'
import ChatPanel from '../generate/ChatPanel'
import styles from '../../App.module.css'

/** 登录后的应用外壳：顶栏 + 全局对话面板 + 路由出口。 */
export default function AppShell() {
  return (
    <div className={styles.app}>
      <Header />
      <main className={styles.main}>
        <ChatPanel />
        <div className={styles.routerArea}>
          <Outlet />
        </div>
      </main>
    </div>
  )
}
