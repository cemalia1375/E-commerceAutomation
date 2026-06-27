import { app, BrowserWindow, ipcMain, shell } from 'electron'
import { spawn, ChildProcess } from 'child_process'
import * as path from 'path'
import * as net from 'net'
import * as fs from 'fs'
import * as http from 'http'
import { fileURLToPath } from 'url'
import { dirname } from 'path'

// CJS 输出时 __dirname 原生可用；ESM 输出时用此 polyfill
const __filename = typeof __filename !== 'undefined' ? __filename : fileURLToPath(import.meta.url)
const __dirname = typeof __dirname !== 'undefined' ? __dirname : dirname(__filename)

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
  return process.platform === 'win32' ? 'uv.exe' : 'uv'
}

function pythonArgs(): string[] {
  if (app.isPackaged) return []
  return ['run', 'python', '-m', 'uvicorn', 'Flowcut.api.server:app', '--port', String(apiPort), '--host', '127.0.0.1']
}

function buildEnv(cfg: AppConfig): NodeJS.ProcessEnv {
  return {
    ...process.env,
    PORT: String(apiPort),
    GOOGLE_API_KEY: cfg.GOOGLE_API_KEY,
    GOOGLE_MODEL: cfg.GOOGLE_MODEL ?? 'gemini-2.5-flash',
    MYSQL_HOST: cfg.MYSQL_HOST,
    MYSQL_USER: cfg.MYSQL_USER,
    MYSQL_PASSWORD: cfg.MYSQL_PASSWORD,
    MYSQL_DB: cfg.MYSQL_DB,
    MYSQL_PORT: cfg.MYSQL_PORT ?? '3306',
    QDRANT_URL: cfg.QDRANT_URL ?? 'http://localhost:6333',
    FFMPEG_PATH: app.isPackaged
      ? path.join(process.resourcesPath, 'ffmpeg.exe')
      : (process.env.FFMPEG_PATH ?? 'ffmpeg'),
  }
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
    if (app.isQuitting) return
    restartCount++
    if (restartCount <= MAX_RESTARTS) {
      setTimeout(() => startPython(cfg), 1500)
    } else {
      mainWindow?.webContents.send('backend-error', `后台服务异常退出（code=${code}），已尝试重启 ${MAX_RESTARTS} 次。请重启应用。`)
    }
  })
}

// ── /health 轮询（坑C）───────────────────────────────────────────────────────

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
  apiPort = await findFreePort(8001)
  mainWindow = createMainWindow()
  startPython(cfg)
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
    win.loadFile(path.join(__dirname, '../dist/index.html'))
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
    win.loadFile(path.join(__dirname, '../dist/index.html'), { hash: '/setup' })
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
})

app.whenReady().then(async () => {
  const cfg = loadConfig()

  if (!cfg) {
    mainWindow = createSetupWindow()
    return
  }

  apiPort = await findFreePort(8001)
  mainWindow = createMainWindow()
  startPython(cfg)
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
