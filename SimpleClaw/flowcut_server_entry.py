"""PyInstaller entry point for the packaged FlowCut backend."""
import os
import sys

import uvicorn

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8001'))
    uvicorn.run('Flowcut.api.server:app', host='127.0.0.1', port=port, workers=1)
