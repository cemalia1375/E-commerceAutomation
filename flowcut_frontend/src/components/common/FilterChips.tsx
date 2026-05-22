import styles from './FilterChips.module.css'

interface Props {
  options: string[]
  active: string
  onChange: (val: string) => void
}

export default function FilterChips({ options, active, onChange }: Props) {
  return (
    <div className={styles.chips}>
      {options.map((opt) => (
        <button
          key={opt}
          className={`${styles.chip} ${active === opt ? styles.active : ''}`}
          onClick={() => onChange(opt)}
        >
          {opt}
        </button>
      ))}
    </div>
  )
}
