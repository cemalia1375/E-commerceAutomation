import Header from './components/layout/Header'
import AppRouter from './router'
import ChatPanel from './components/generate/ChatPanel'
import styles from './App.module.css'

export default function App() {
  return (
    <div className={styles.app}>
      <Header />
      <main className={styles.main}>
        <ChatPanel />
        <div className={styles.routerArea}>
          <AppRouter />
        </div>
      </main>
    </div>
  )
}
