# FlowCut Windows 打包交接文档

## 概述

FlowCut 是一个 Electron 桌面应用，前端为 React + Vite，后端为 Python（FastAPI + uvicorn）。
Windows 安装包通过 GitHub Actions 自动构建，产物为 NSIS 安装器（`.exe`）。

---

## 仓库结构

| 仓库 | 地址 | 分支 |
|---|---|---|
| 主仓库（前端 + CI） | `github.com/Marcoskk7/E-commerceAutomation` | `main` |
| 子模块（Python 后端） | `github.com/LongAILab/mojing_agent` | `Flowcut` |

本地目录对应关系：
```
E-commerceAutomation/
  flowcut_frontend/    # Electron + React 前端
  SimpleClaw/          # Python 后端（git submodule，指向 mojing_agent@Flowcut）
  .github/workflows/   # CI 配置
```

---

## 触发打包

### 方式一：打 git tag（推荐，会自动创建 GitHub Release）

```bash
git tag v1.x.x
git push origin v1.x.x
```

### 方式二：手动触发

GitHub → `E-commerceAutomation` → Actions → `Build Windows Installer` → `Run workflow`

构建耗时约 **10~15 分钟**，产物在 Actions Artifacts 或对应的 GitHub Release 页面下载。

---

## CI 密钥配置（必须提前设置，否则 CI 报错）

CI 需要读取私有子模块 `mojing_agent`，必须配置 SSH Deploy Key：

### 1. 生成密钥对

```bash
ssh-keygen -t ed25519 -C "flowcut-ci" -f ~/.ssh/flowcut_ci -N ""
```

### 2. 公钥 → 子模块仓库

地址：`https://github.com/LongAILab/mojing_agent/settings/keys`

点击 **Add deploy key**，粘贴 `~/.ssh/flowcut_ci.pub` 内容，只需 **Read** 权限。

### 3. 私钥 → 主仓库 Secret

地址：`https://github.com/Marcoskk7/E-commerceAutomation/settings/secrets/actions`

点击 **New repository secret**：
- Name：`SUBMODULE_SSH_KEY`
- Value：`~/.ssh/flowcut_ci` 的内容

> 注意：密钥一旦离职需吊销重建，否则存在安全风险。

---

## 打包产物说明

| 文件 | 说明 |
|---|---|
| `FlowCut-Setup-x.x.x.exe` | NSIS 安装器，用户双击安装即用 |

安装后目录结构（用户电脑）：
```
C:\Program Files\FlowCut\
  FlowCut.exe              # Electron 主程序
  resources/
    backend/
      flowcut_server.exe   # Python 后端（PyInstaller 打包）
    ffmpeg.exe             # 视频合成工具
```

用户配置文件存储在：`C:\Users\<用户名>\AppData\Roaming\flowcut\config.json`
（首次启动弹出配置向导填写，填错了删掉此文件重新填写）

---

## 本地开发启动（macOS / Windows 均可）

```bash
# 前端（含 Electron）
cd flowcut_frontend
pnpm install
pnpm run dev

# 后端（另开终端）
cd SimpleClaw
uv run python -m uvicorn Flowcut.api.server:app --reload --port 8001
```

环境变量复制 `SimpleClaw/.env.example` 为 `.env` 并填写。

---

## 常见问题

**Q：CI 报 `repository not found`**
A：`SUBMODULE_SSH_KEY` 未配置，或 Deploy Key 已过期/被删除，按上方步骤重新配置。

**Q：安装包启动后 backend 崩溃**
A：查看 Electron 的 stderr 日志（开发者工具 Console），找 `ModuleNotFoundError: No module named 'xxx'`，把 `xxx` 加到 `SimpleClaw/flowcut_server.spec` 的 `hiddenimports` 列表，重新触发 CI。

**Q：需要更新版本号**
A：修改 `flowcut_frontend/package.json` 的 `version` 字段，打 tag 触发即可。

**Q：配置了新的环境变量需要打包进去**
A：在 `flowcut_frontend/electron/main.ts` 的 `buildEnv()` 函数里补充，同时在 `SetupWizard.tsx` 里加对应的表单项。
