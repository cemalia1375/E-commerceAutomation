// Electron preload script — plain CommonJS for maximum compatibility.
// (Do not convert to ESM — the Electron preload sandbox resolves CJS `require` most reliably.)
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getApiPort: () => ipcRenderer.invoke('get-api-port'),
  onBackendReady: (cb) => {
    ipcRenderer.on('backend-ready', (_event, port) => cb(port));
  },
  onBackendError: (cb) => {
    ipcRenderer.on('backend-error', (_event, msg) => cb(msg));
  },
  saveConfig: (cfg) => ipcRenderer.invoke('save-config', cfg),
  openExternal: (url) => ipcRenderer.send('open-external', url),
});
