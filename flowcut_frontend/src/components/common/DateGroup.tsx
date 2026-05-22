import type { ReactNode } from 'react'
import styles from './DateGroup.module.css'

interface Props {
  label: string
  children: ReactNode
}

export default function DateGroup({ label, children }: Props) {
  return (
    <div className={styles.group}>
      <div className={styles.label}>{label}</div>
      {children}
    </div>
  )
}
