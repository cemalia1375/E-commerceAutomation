import { Component } from 'react'

interface Props {
  children: React.ReactNode
}

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div style={{
        padding: 40, maxWidth: 640, margin: '40px auto',
        fontFamily: 'system-ui, sans-serif',
      }}>
        <h1 style={{ color: '#dc2626', fontSize: 20, marginBottom: 12 }}>页面渲染异常</h1>
        <pre style={{
          background: '#fef2f2', border: '1px solid #fecaca',
          borderRadius: 8, padding: 16, fontSize: 13,
          overflow: 'auto', whiteSpace: 'pre-wrap',
        }}>
          {this.state.error.message}
          {'\n\n'}
          {this.state.error.stack}
        </pre>
        <button
          onClick={() => window.location.reload()}
          style={{
            marginTop: 16, padding: '8px 20px', cursor: 'pointer',
            background: '#2563eb', color: '#fff', border: 'none', borderRadius: 6,
          }}
        >
          刷新页面
        </button>
      </div>
    )
  }
}
