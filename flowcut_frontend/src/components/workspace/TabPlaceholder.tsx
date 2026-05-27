interface TabPlaceholderProps {
  name: string
}

export default function TabPlaceholder({ name }: TabPlaceholderProps) {
  return (
    <div style={{ padding: 24, color: '#888' }}>
      Tab: {name}（Task 8 实现）
    </div>
  )
}
