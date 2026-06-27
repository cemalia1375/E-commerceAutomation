import React from 'react'
import ReactDOM from 'react-dom/client'
import { HashRouter as BrowserRouter } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import { theme } from './theme'
import ErrorBoundary from './components/common/ErrorBoundary'
import './styles/global.css'

// 全局 JS 白屏兜底：未捕获的 JS 错误和 Promise 拒绝也显示在页面上
window.addEventListener('error', (e) => {
  document.getElementById('root')!.innerHTML =
    `<div style="padding:40px;font-family:system-ui,sans-serif">
      <h1 style="color:#dc2626;font-size:20px;margin-bottom:12px">JS 运行时异常</h1>
      <pre style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:16px;font-size:13px;overflow:auto;white-space:pre-wrap">${e.error?.stack ?? e.message ?? e}</pre>
      <button onclick="location.reload()" style="margin-top:16px;padding:8px 20px;cursor:pointer;background:#2563eb;color:#fff;border:none;border-radius:6px">刷新页面</button>
    </div>`
})
window.addEventListener('unhandledrejection', (e) => {
  document.getElementById('root')!.innerHTML =
    `<div style="padding:40px;font-family:system-ui,sans-serif">
      <h1 style="color:#dc2626;font-size:20px;margin-bottom:12px">Promise 异常</h1>
      <pre style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:16px;font-size:13px;overflow:auto;white-space:pre-wrap">${e.reason?.stack ?? e.reason?.message ?? e.reason}</pre>
      <button onclick="location.reload()" style="margin-top:16px;padding:8px 20px;cursor:pointer;background:#2563eb;color:#fff;border:none;border-radius:6px">刷新页面</button>
    </div>`
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ConfigProvider theme={theme} locale={zhCN}>
        <ErrorBoundary>
          <App />
        </ErrorBoundary>
      </ConfigProvider>
    </BrowserRouter>
  </React.StrictMode>
)
