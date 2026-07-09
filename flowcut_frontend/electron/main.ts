import { app, BrowserWindow, ipcMain, shell } from 'electron'
import { spawn, ChildProcess } from 'child_process'
import * as path from 'path'
import * as net from 'net'
import * as fs from 'fs'
import * as http from 'http'
import { fileURLToPath } from 'url'
import { dirname } from 'path'

// CJS 输出时 __dirname 原生可用；ESM 输出时用此 polyfill
// 注意：必须用 var 不能 const——CJS 中 __filename/__dirname 是函数参数，const 不可重声明
/* eslint-disable-next-line @typescript-eslint/no-explicit-any */
declare var __filename: any
/* eslint-disable-next-line @typescript-eslint/no-explicit-any */
declare var __dirname: any
var __filename = typeof __filename !== 'undefined' ? __filename : fileURLToPath(import.meta.url)
var __dirname = typeof __dirname !== 'undefined' ? __dirname : dirname(__filename)

// ── 生产模式静态文件服务（替代 loadFile，解决 SameSite cookie 问题）────────────

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
}

function startStaticServer(staticDir: string): Promise<{ port: number; server: http.Server }> {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const url = new URL(req.url ?? '/', `http://localhost`)
      let filePath = path.join(staticDir, url.pathname === '/' ? 'index.html' : url.pathname)

      // SPA fallback: 非文件路径全部返回 index.html（HashRouter 理论上不需要，兜底）
      if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
        filePath = path.join(staticDir, 'index.html')
      }

      const ext = path.extname(filePath).toLowerCase()
      res.writeHead(200, { 'Content-Type': MIME[ext] ?? 'application/octet-stream' })
      fs.createReadStream(filePath).pipe(res)
    })

    server.listen(0, '127.0.0.1', () => {
      const port = (server.address() as net.AddressInfo).port
      resolve({ port, server })
    })
    server.on('error', reject)
  })
}

let staticServer: http.Server | null = null
let frontendOrigin = ''  // http://localhost:<port>，前后端同 host，SameSite cookie 正常工作

// ── 配置文件 ──────────────────────────────────────────────────────────────────

interface AppConfig {
  GOOGLE_API_KEY: string
  MYSQL_HOST: string
  MYSQL_USER: string
  MYSQL_PASSWORD: string
  MYSQL_DB: string
  MYSQL_PORT: string
  QDRANT_URL?: string
  GOOGLE_MODEL?: string
  // LLM 提供方
  FLOWCUT_LLM_PROVIDER?: string        // gemini | volcengine
  VOLCENGINE_API_KEY?: string
  VOLCENGINE_API_BASE?: string
  GEMINI_PROXY?: string                // Gemini API 代理地址（国内直连被墙，如 Clash http://127.0.0.1:7890）
  GEMINI_BASE_URL?: string             // Gemini API 中转地址（第三方代理如 moyu.info）
  // OSS 对象存储
  FLOWCUT_OSS_ENDPOINT?: string
  FLOWCUT_OSS_ACCESS_KEY_ID?: string
  FLOWCUT_OSS_ACCESS_KEY_SECRET?: string
  FLOWCUT_OSS_BUCKET?: string
  FLOWCUT_OSS_REGION?: string
}

function configPath(): string {
  return path.join(app.getPath('userData'), 'config.json')
}

function loadConfig(): AppConfig | null {
  try {
    return JSON.parse(fs.readFileSync(configPath(), 'utf-8'))
  } catch {
    return null
  }
}

function saveConfig(cfg: AppConfig): void {
  fs.writeFileSync(configPath(), JSON.stringify(cfg, null, 2))
}

// ── 空闲端口查找（坑D）────────────────────────────────────────────────────────

function findFreePort(startPort = 8001): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.listen(startPort, '127.0.0.1', () => {
      const port = (server.address() as net.AddressInfo).port
      server.close(() => resolve(port))
    })
    server.on('error', () => findFreePort(startPort + 1).then(resolve, reject))
  })
}

function isFlowcutBackendHealthy(
  port: number,
  expectedConfig?: AppConfig,
  timeoutMs = 800,
): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false
    const finish = (healthy: boolean) => {
      if (settled) return
      settled = true
      resolve(healthy)
    }
    const req = http.get(`http://127.0.0.1:${port}/health`, (res) => {
      let body = ''
      res.setEncoding('utf8')
      res.on('data', (chunk: string) => { body += chunk })
      res.on('end', () => {
        try {
          const payload = JSON.parse(body) as {
            service?: string
            runtime_config?: {
              llm_provider?: string
              gemini_transport?: string
              gemini_base_host?: string
            }
          }
          let compatible = res.statusCode === 200 && payload.service === 'flowcut'
          if (compatible && expectedConfig) {
            const expectedBase = expectedConfig.GEMINI_BASE_URL?.trim()
            const expectedTransport = expectedBase
              ? 'base_url'
              : expectedConfig.GEMINI_PROXY?.trim()
                ? 'proxy'
                : 'direct'
            const expectedHost = expectedBase ? new URL(expectedBase).host.toLowerCase() : ''
            const runtime = payload.runtime_config
            compatible = Boolean(
              runtime &&
              runtime.gemini_transport === expectedTransport &&
              runtime.gemini_base_host === expectedHost
            )
          }
          finish(compatible)
        } catch {
          finish(false)
        }
      })
    })
    req.setTimeout(timeoutMs, () => {
      req.destroy()
      finish(false)
    })
    req.on('error', () => finish(false))
  })
}

// ── Python 子进程管理（坑C/F/J）──────────────────────────────────────────────

let pythonProcess: ChildProcess | null = null
let apiPort = 8001
let restartCount = 0
const MAX_RESTARTS = 3
let mainWindow: BrowserWindow | null = null
let backendIsReady = false

function pythonExePath(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend', 'flowcut_server.exe')
  }
  if (process.platform === 'win32') {
    // PATH 可能还没包含 uv，按常见安装路径回退查找
    const candidates = [
      path.join(process.env.USERPROFILE ?? '', '.local', 'bin', 'uv.exe'),
      path.join(process.env.USERPROFILE ?? '', '.cargo', 'bin', 'uv.exe'),
      'uv.exe',
    ]
    for (const p of candidates) {
      if (fs.existsSync(p)) return p
    }
  }
  return 'uv'
}

function pythonArgs(): string[] {
  if (app.isPackaged) return []
  return ['run', 'python', '-m', 'uvicorn', 'Flowcut.api.server:app', '--port', String(apiPort), '--host', '127.0.0.1']
}

function buildEnv(_cfg?: AppConfig): NodeJS.ProcessEnv {
  // 生产模式：config.json 所有字段透传为环境变量，供 Python load_dotenv() 之后使用。
  // load_dotenv 不会覆盖已有的环境变量，因此 Electron 传入的值优先级最高。
  const env: NodeJS.ProcessEnv = { ...process.env }

  // 运行时端口
  env.PORT = String(apiPort)

  // FFmpeg 路径
  env.FFMPEG_PATH = app.isPackaged
    ? path.join(process.resourcesPath, 'ffmpeg.exe')
    : (process.env.FFMPEG_PATH
      ?? (() => {
        const local = path.join(__dirname, '../ffmpeg.exe')
        if (fs.existsSync(local)) return local
        return 'ffmpeg'
      })())

  // CORS（生产模式前端端口是动态的）
  if (app.isPackaged && frontendOrigin) {
    env.FLOWCUT_CORS_ORIGINS = frontendOrigin
  }

  // ── config.json 全部字段透传 ──
  if (_cfg) {
    if (_cfg.GOOGLE_API_KEY)           env.GOOGLE_API_KEY = _cfg.GOOGLE_API_KEY
    if (_cfg.GOOGLE_MODEL)             env.GOOGLE_MODEL = _cfg.GOOGLE_MODEL
    if (_cfg.FLOWCUT_LLM_PROVIDER)     env.FLOWCUT_LLM_PROVIDER = _cfg.FLOWCUT_LLM_PROVIDER
    if (_cfg.VOLCENGINE_API_KEY)       env.VOLCENGINE_API_KEY = _cfg.VOLCENGINE_API_KEY
    if (_cfg.VOLCENGINE_API_BASE)      env.VOLCENGINE_API_BASE = _cfg.VOLCENGINE_API_BASE
    if (_cfg.GEMINI_PROXY)             env.GEMINI_PROXY = _cfg.GEMINI_PROXY
    if (_cfg.GEMINI_BASE_URL)          env.GEMINI_BASE_URL = _cfg.GEMINI_BASE_URL
    if (_cfg.MYSQL_HOST)               env.MYSQL_HOST = _cfg.MYSQL_HOST
    if (_cfg.MYSQL_USER)               env.MYSQL_USER = _cfg.MYSQL_USER
    if (_cfg.MYSQL_PASSWORD)           env.MYSQL_PASSWORD = _cfg.MYSQL_PASSWORD
    if (_cfg.MYSQL_DB)                 env.MYSQL_DB = _cfg.MYSQL_DB
    if (_cfg.MYSQL_PORT)               env.MYSQL_PORT = _cfg.MYSQL_PORT
    if (_cfg.QDRANT_URL)               env.QDRANT_URL = _cfg.QDRANT_URL
    if (_cfg.FLOWCUT_OSS_ENDPOINT)        env.FLOWCUT_OSS_ENDPOINT = _cfg.FLOWCUT_OSS_ENDPOINT
    if (_cfg.FLOWCUT_OSS_ACCESS_KEY_ID)   env.FLOWCUT_OSS_ACCESS_KEY_ID = _cfg.FLOWCUT_OSS_ACCESS_KEY_ID
    if (_cfg.FLOWCUT_OSS_ACCESS_KEY_SECRET) env.FLOWCUT_OSS_ACCESS_KEY_SECRET = _cfg.FLOWCUT_OSS_ACCESS_KEY_SECRET
    if (_cfg.FLOWCUT_OSS_BUCKET)          env.FLOWCUT_OSS_BUCKET = _cfg.FLOWCUT_OSS_BUCKET
    if (_cfg.FLOWCUT_OSS_REGION)          env.FLOWCUT_OSS_REGION = _cfg.FLOWCUT_OSS_REGION
  }
  return env
}

function startPython(cfg: AppConfig): void {
  const exe = pythonExePath()
  const args = pythonArgs()
  const cwd = app.isPackaged ? path.dirname(exe) : path.join(__dirname, '../../SimpleClaw')

  pythonProcess = spawn(exe, args, {
    cwd,
    env: buildEnv(cfg),
    stdio: 'pipe',
  })

  pythonProcess.stdout?.on('data', (d: Buffer) => process.stdout.write(`[py] ${d}`))
  pythonProcess.stderr?.on('data', (d: Buffer) => process.stderr.write(`[py] ${d}`))

  pythonProcess.on('error', (err) => {
    const msg = `后台进程启动失败：${err.message}（确认 uv 已安装且在 PATH 中）`
    process.stderr.write(`[py-error] ${msg}\n`)
    mainWindow?.webContents.send('backend-error', msg)
  })

  pythonProcess.on('exit', (code) => {
    if ((app as any).isQuitting) return
    restartCount++
    if (restartCount <= MAX_RESTARTS) {
      setTimeout(() => startPython(cfg), 1500)
    } else {
      mainWindow?.webContents.send('backend-error', `后台服务异常退出（code=${code}），已尝试重启 ${MAX_RESTARTS} 次。请重启应用。`)
    }
  })
}

// ── /health 轮询（坑C）───────────────────────────────────────────────────────

async function prepareBackend(cfg: AppConfig): Promise<void> {
  if (!app.isPackaged && await isFlowcutBackendHealthy(8001, cfg)) {
    apiPort = 8001
    backendIsReady = true
    console.log('[py] reusing healthy FlowCut backend on http://127.0.0.1:8001')
    return
  }

  apiPort = await findFreePort(8001)
  backendIsReady = false
  startPython(cfg)
}

function pollHealth(win: BrowserWindow): void {
  const check = () => {
    http.get(`http://127.0.0.1:${apiPort}/health`, (res) => {
      if (res.statusCode === 200) {
        backendIsReady = true
        win.webContents.send('backend-ready', apiPort)
      } else {
        setTimeout(check, 500)
      }
      res.resume()
    }).on('error', () => setTimeout(check, 500))
  }
  check()
}

// ── IPC handlers ──────────────────────────────────────────────────────────────

ipcMain.handle('get-api-port', () => apiPort)

ipcMain.handle('save-config', async (_event, cfg: AppConfig) => {
  saveConfig(cfg)
  const setupWin = BrowserWindow.getFocusedWindow()
  await prepareBackend(cfg)
  mainWindow = createMainWindow()
  pollHealth(mainWindow)
  setupWin?.close()
})

ipcMain.on('open-external', (_event, url: string) => {
  shell.openExternal(url)
})

// ── 窗口创建 ──────────────────────────────────────────────────────────────────

function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  // Vite optimizer 完成后会强制刷新页面，刷新后补发 backend-ready 避免白屏
  win.webContents.on('did-finish-load', () => {
    if (backendIsReady) {
      win.webContents.send('backend-ready', apiPort)
    }
  })

  if (app.isPackaged) {
    // 生产模式也走 HTTP（不走 file://），确保 SameSite cookie 策略正常工作
    win.loadURL(`${frontendOrigin}/`)
  } else {
    win.loadURL('http://localhost:5173')
    win.webContents.openDevTools()
  }

  return win
}

function createSetupWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 560,
    height: 560,
    resizable: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  if (app.isPackaged) {
    win.loadURL(`${frontendOrigin}/#/setup`)
  } else {
    win.loadURL('http://localhost:5173/#/setup')
  }

  return win
}

// ── 应用启动 ──────────────────────────────────────────────────────────────────

declare global {
  // eslint-disable-next-line no-var
  var isQuitting: boolean
}

app.on('before-quit', () => {
  (app as unknown as { isQuitting: boolean }).isQuitting = true
  pythonProcess?.kill()
  staticServer?.close()
})

// GPU 降级兜底：老旧显卡 / 虚拟机 / 远程桌面可通过 --disable-gpu 参数强制软件渲染
// 用法：FlowCut.exe --disable-gpu
if (process.argv.includes('--disable-gpu')) {
  app.disableHardwareAcceleration()
}

app.whenReady().then(async () => {
  // 生产模式：先启动静态文件 HTTP 服务，再用 loadURL 加载（避免 file:// 导致的 SameSite cookie 问题）
  if (app.isPackaged) {
    const staticDir = path.join(__dirname, '../dist')
    const result = await startStaticServer(staticDir)
    staticServer = result.server
    frontendOrigin = `http://localhost:${result.port}`
    console.log(`[static] serving ${staticDir} on ${frontendOrigin}`)
  }

  // 配置由 .env 提供（打包在 resources/backend/.env），配置向导仅作兜底
  const cfg = loadConfig()

  if (!cfg && !app.isPackaged) {
    // 开发模式：无配置则显示向导
    mainWindow = createSetupWindow()
    return
  }

  await prepareBackend(cfg ?? ({} as AppConfig))
  mainWindow = createMainWindow()
  pollHealth(mainWindow)
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    const cfg = loadConfig()
    if (cfg) {
      mainWindow = createMainWindow()
      pollHealth(mainWindow)
    }
  }
})
