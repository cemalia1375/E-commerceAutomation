import { Spin } from 'antd'
import styles from './CenteredLoader.module.css'

interface CenteredLoaderProps {
  label?: string
  fullscreen?: boolean
}

export default function CenteredLoader({
  label,
  fullscreen = false,
}: CenteredLoaderProps) {
  return (
    <div className={`${styles.root} ${fullscreen ? styles.fullscreen : ''}`}>
      <div className={styles.content}>
        <Spin size="large" />
        {label && <span>{label}</span>}
      </div>
    </div>
  )
}
