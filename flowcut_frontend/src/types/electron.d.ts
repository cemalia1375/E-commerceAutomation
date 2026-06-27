interface ElectronAPI {
  getApiPort: () => Promise<number>
  onBackendReady: (cb: (port: number) => void) => void
  onBackendError: (cb: (msg: string) => void) => void
  saveConfig: (cfg: Record<string, string>) => Promise<void>
  openExternal: (url: string) => void
}

interface Window {
  electronAPI?: ElectronAPI
}
