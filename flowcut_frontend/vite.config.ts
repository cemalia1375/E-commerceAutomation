import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import electron from 'vite-plugin-electron'
import { copyFileSync, mkdirSync } from 'fs'
import * as path from 'path'

// ── Electron API shim ──────────────────────────────────────────────────
// Electron 35 在 Windows Insider build 上无法拦截 require("electron")。
// 在 dist-electron/node_modules/electron/ 放置一个 shim 模块，
// 用 process._linkedBinding 获取可用的 C++ 绑定，其余 API 用 mock 替代。
// dist-electron/ 下的 node_modules 优先于项目根 node_modules，
// 因此 require("electron") 会命中 shim 而非 npm 包的 index.js。

const SHIM_CONTENT = `
const { EventEmitter } = require('events');

// BrowserWindow: 唯一确认安全可用的 C++ 绑定
let BrowserWindow;
try {
  BrowserWindow = process._linkedBinding('electron_browser_window').BrowserWindow;
} catch (e) {
  console.warn('[electron-shim] BrowserWindow 不可用:', e.message);
  BrowserWindow = function() { throw new Error('BrowserWindow unavailable'); };
}

// app mock
const app = new EventEmitter();
app.isPackaged = false;
app.isReady = () => false;
app.whenReady = () => Promise.resolve();
app.getPath = (name) => name === 'userData'
  ? require('path').join(process.env.APPDATA || process.env.HOME || '.', 'flowcut')
  : process.cwd();
app.getAppPath = () => process.cwd();
app.getName = () => 'FlowCut';
app.quit = () => { app.isQuitting = true; app.emit('before-quit'); process.exit(0); };
app.exit = (c) => process.exit(c || 0);
app.disableHardwareAcceleration = () => {};
app.commandLine = { appendSwitch: () => {} };
app.isQuitting = false;

// ipcMain mock
const ipcMain = new EventEmitter();
ipcMain.handle = (ch, fn) => { ipcMain._h = ipcMain._h || {}; ipcMain._h[ch] = fn; };
ipcMain.removeHandler = (ch) => { if (ipcMain._h) delete ipcMain._h[ch]; };

// shell mock
const shell = {
  openExternal: (url) => require('child_process').exec(
    process.platform === 'win32' ? 'start "" "' + url + '"' :
    process.platform === 'darwin' ? 'open "' + url + '"' : 'xdg-open "' + url + '"'
  ),
};

module.exports = {
  app, BrowserWindow, ipcMain, shell,
  dialog: {},
  screen: {},
  powerMonitor: new EventEmitter(),
  Menu: { buildFromTemplate: () => ({ popup: () => {} }) },
  Tray: function() { this.destroy = () => {}; },
  nativeImage: { createFromPath: () => ({}), createEmpty: () => ({}) },
  session: { defaultSession: {} },
  webContents: { getAllWebContents: () => [] },
};
`.trim()

function createElectronShimPlugin() {
  const root = path.resolve('.')  // 固定在配置加载时，避免 CWD 漂移
  return {
    name: 'create-electron-shim',
    buildStart() {
      const fs = require('fs')

      // dist-electron/package.json
      const distPkg = path.join(root, 'dist-electron/package.json')
      if (!fs.existsSync(distPkg)) {
        mkdirSync(path.dirname(distPkg), { recursive: true })
        fs.writeFileSync(distPkg, JSON.stringify({ type: 'commonjs' }))
      }

      // dist-electron/node_modules/electron/ shim
      const dir = path.join(root, 'dist-electron/node_modules/electron')
      const pkgFile = path.join(dir, 'package.json')
      const indexFile = path.join(dir, 'index.js')
      mkdirSync(dir, { recursive: true })
      if (!fs.existsSync(pkgFile)) {
        fs.writeFileSync(pkgFile, JSON.stringify({ name: 'electron', main: 'index.js', type: 'commonjs' }))
      }
      fs.writeFileSync(indexFile, SHIM_CONTENT)
    },
  }
}

// ── CJS transform ─────────────────────────────────────────────────────
// Rolldown (Vite 8) 无法输出真正的 CJS，需在 generateBundle 中将
// ESM import 语法转为 CJS require()。

function cjsTransformPlugin() {
  return {
    name: 'cjs-transform',
    generateBundle(_options: any, bundle: any) {
      const mainChunk = bundle['main.js']
      if (!mainChunk || mainChunk.type !== 'chunk') return

      let code = mainChunk.code

      // 命名导入:  import { a, b } from "mod"  →  const { a, b } = require("mod")
      code = code.replace(
        /^import\s*\{([^}]+)\}\s*from\s*"([^"]+)";?$/gm,
        (_: string, names: string, mod: string) => `const {${names}} = require("${mod}");`,
      )

      // 命名空间导入:  import * as x from "mod"  →  const x = require("mod")
      code = code.replace(
        /^import\s*\*\s+as\s+(\w+)\s+from\s*"([^"]+)";?$/gm,
        (_: string, name: string, mod: string) => `const ${name} = require("${mod}");`,
      )

      // import.meta.url → undefined（CJS 中 typeof __filename !== "undefined" 恒 true）
      code = code.replace(/import\.meta\.url/g, 'undefined')

      mainChunk.code = code
    },
  }
}

// 复制手写 CJS preload
function copyPreloadCjsPlugin() {
  const root = path.resolve('.')
  return {
    name: 'copy-preload-cjs',
    closeBundle() {
      const src = path.join(root, 'electron/preload.cjs')
      const dest = path.join(root, 'dist-electron/preload.cjs')
      mkdirSync(path.dirname(dest), { recursive: true })
      copyFileSync(src, dest)
    },
  }
}

// ── 主配置 ────────────────────────────────────────────────────────────

export default defineConfig({
  plugins: [
    react(),
    createElectronShimPlugin(),
    copyPreloadCjsPlugin(),
    electron([
      {
        entry: 'electron/main.ts',
        vite: {
          build: {
            outDir: 'dist-electron',
            minify: false,  // 关闭 minify：否则 minifier 会添加 import { x as e } 别名，
                            // CJS transform 照搬后产出 const { x as e } = require()
                            // 这是非法 JS 语法（CJS 解构用 : 而非 as）
            rollupOptions: {
              external: ['electron'],
              output: { format: 'es', entryFileNames: 'main.js' },
            },
          },
          plugins: [cjsTransformPlugin()],
        },
      },
      {
        entry: 'electron/preload.ts',
        vite: {
          build: {
            outDir: 'dist-electron',
            minify: false,
            rollupOptions: {
              external: ['electron'],
              output: { format: 'cjs' },
            },
          },
        },
        onstart(options) {
          options.reload()
        },
      },
    ]),
  ],
  build: {
    outDir: 'dist',
  },
})
