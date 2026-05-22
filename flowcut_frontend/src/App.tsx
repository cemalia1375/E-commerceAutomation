import Header from './components/layout/Header'
import AppRouter from './router'
import styles from './App.module.css'

export default function App() {
  return (
    <div className={styles.app}>
      <Header />
      <main className={styles.main}>
        <AppRouter />
      </main>
    </div>
  )
}
