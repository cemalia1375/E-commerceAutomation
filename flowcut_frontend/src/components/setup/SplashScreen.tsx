import { Spin, Alert } from 'antd'
import styles from './SplashScreen.module.css'

interface Props {
  error?: string | null
}

export default function SplashScreen({ error }: Props) {
  return (
    <div className={styles.container}>
      <div className={styles.logo}>FlowCut</div>
      {error ? (
        <Alert type="error" message={error} style={{ maxWidth: 400 }} />
      ) : (
        <>
          <Spin size="large" />
          <p className={styles.hint}>正在启动后台服务…</p>
        </>
      )}
    </div>
  )
}
