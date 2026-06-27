import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import electron from 'vite-plugin-electron'
import { copyFileSync } from 'fs'

// 复制原始 CJS preload 到 dist-electron，覆盖打包器输出的版本。
// Vite 8 的 Rolldown 无法输出真正的 CommonJS（始终带 ESM export 语法），
// 而 Electron 的 preload 沙箱要求纯 CJS `require('electron')` 格式。
function copyPreloadCjsPlugin() {
  return {
    name: 'copy-preload-cjs',
    closeBundle() {
      copyFileSync('electron/preload.cjs', 'dist-electron/preload.cjs')
    },
  }
}

export default defineConfig({
  plugins: [
    react(),
    copyPreloadCjsPlugin(),
    electron([
      {
        entry: 'electron/main.ts',
        vite: {
          build: {
            outDir: 'dist-electron',
            rollupOptions: {
              external: ['electron'],
              output: { format: 'cjs' },
            },
          },
        },
      },
      {
        entry: 'electron/preload.ts',
        vite: {
          build: {
            outDir: 'dist-electron',
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
