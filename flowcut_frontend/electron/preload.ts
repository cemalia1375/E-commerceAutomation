import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  getApiPort: (): Promise<number> => ipcRenderer.invoke('get-api-port'),
  onBackendReady: (cb: (port: number) => void) => {
    ipcRenderer.on('backend-ready', (_event, port: number) => cb(port))
  },
  onBackendError: (cb: (msg: string) => void) => {
    ipcRenderer.on('backend-error', (_event, msg: string) => cb(msg))
  },
  saveConfig: (cfg: Record<string, string>): Promise<void> =>
    ipcRenderer.invoke('save-config', cfg),
  openExternal: (url: string) => ipcRenderer.send('open-external', url),
})
