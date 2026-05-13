# FlowCut 前端 MVP 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于已确认的 demo.html 设计稿，用 Vite + React + TypeScript + Ant Design 实现 FlowCut 前端 MVP，包含生成、素材库、成片库三个 Tab。

**Architecture:** 单页应用，React Router 管理三个顶级 Tab 路由；Zustand 管理生成流程状态（当前步骤、消息列表、已选脚本等）；素材库和成片库 MVP 阶段用本地 mock 数据，接口层预留但不联调。

**Tech Stack:** Vite 6, React 19, TypeScript 5, Ant Design 5, Zustand 5, React Router 7, Axios

**Design Reference:** `demo.html` 在项目根目录，是所有视觉/交互的 ground truth，实现时以它为准。

---

## ⚠️ 脚手架前置（由用户执行）

在开始 Task 1 前，用户需在项目目录下运行：

```bash
npm create vite@latest flowcut_frontend -- --template react-ts
cd flowcut_frontend
npm install antd @ant-design/icons zustand react-router-dom axios
```

完成后项目根目录为 `flowcut/`，所有后续任务均在此目录内操作。

---

## 文件结构

```
flowcut/src/
  main.tsx                        # 入口，挂载 Router + ConfigProvider
  App.tsx                         # 根布局：Header + Tab 路由出口
  router.tsx                      # React Router 路由定义
  theme.ts                        # Ant Design 主题 token（品牌色 #2563eb）
  
  types/index.ts                  # 全局 TypeScript 类型
  
  stores/
    generateStore.ts              # 生成流程状态（Zustand）
    materialStore.ts              # 素材库状态（Zustand）
    creativeStore.ts              # 成片库状态（Zustand）
  
  components/
    layout/
      Header.tsx                  # 顶部导航（Logo + 三个 Tab + 头像）
    
    generate/
      GenerateTab.tsx             # 生成 Tab 容器（左右分栏布局）
      ChatPanel.tsx               # 左侧对话区（消息流 + 输入框）
      ContentPanel.tsx            # 右侧内容区（StepBar + 步骤内容）
      StepBar.tsx                 # 步骤进度条
      steps/
        UploadStep.tsx            # 步骤 1：上传视频
        ScriptStep.tsx            # 步骤 2：脚本选择
        MatchingStep.tsx          # 步骤 3：素材匹配结果
        ConfirmStep.tsx           # 步骤 4：确认成片
        PlaceholderStep.tsx       # 步骤 5：上架千川占位
    
    material/
      MaterialTab.tsx             # 素材库 Tab 容器（子 tab）
      VideoLibrary.tsx            # 视频子库（网格 + 筛选 + 上传）
      ImageLibrary.tsx            # 图片子库
      AudioLibrary.tsx            # 音频子库（列表 + 波形）
      MaterialCard.tsx            # 视频/图片卡片
      AudioCard.tsx               # 音频行卡片
      UploadCard.tsx              # 上传占位卡片
    
    creative/
      CreativeTab.tsx             # 成片库 Tab 容器（子 tab）
      CreativeVideoLibrary.tsx    # 成片视频库（竖版网格）
      SrtLibrary.tsx              # 字幕文件库（列表）
      CreativeCard.tsx            # 成片竖版卡片
      SrtCard.tsx                 # 字幕文件行卡片
    
    common/
      DateGroup.tsx               # 日期分组（标题 + 子内容 slot）
      FilterChips.tsx             # 筛选 chip 组（全部/人物/产品…）
      StatusBadge.tsx             # 素材状态徽章（READY/处理中/失败）
  
  styles/
    global.css                    # CSS reset + 全局滚动条样式
  
  mocks/
    materials.ts                  # 素材 mock 数据
    creatives.ts                  # 成片 mock 数据
    scripts.ts                    # 脚本 mock 数据
```

---

## Task 1：项目基础配置（主题 + 全局样式 + 类型 + mock 数据）

**Files:**
- Create: `src/theme.ts`
- Create: `src/types/index.ts`
- Create: `src/styles/global.css`
- Create: `src/mocks/materials.ts`
- Create: `src/mocks/creatives.ts`
- Create: `src/mocks/scripts.ts`
- Modify: `src/main.tsx`

- [ ] **Step 1: 创建 Ant Design 主题配置**

```ts
// src/theme.ts
import type { ThemeConfig } from 'antd'

export const theme: ThemeConfig = {
  token: {
    colorPrimary: '#2563eb',
    colorBgContainer: '#ffffff',
    colorBgLayout: '#f1f5f9',
    borderRadius: 10,
    borderRadiusSM: 6,
    borderRadiusLG: 14,
    fontFamily: "'Outfit', 'PingFang SC', -apple-system, sans-serif",
    fontSize: 14,
    colorBorder: '#e2e8f0',
    colorText: '#0f172a',
    colorTextSecondary: '#475569',
    colorTextTertiary: '#94a3b8',
    colorSuccess: '#059669',
    colorWarning: '#d97706',
    colorError: '#dc2626',
  },
  components: {
    Tabs: {
      cardBg: '#f1f5f9',
      itemSelectedColor: '#2563eb',
    },
  },
}
```

- [ ] **Step 2: 创建全局类型**

```ts
// src/types/index.ts

export type MaterialCategory = '人物' | '产品' | '场景' | '氛围' | '字幕板'
export type MaterialStatus = 'PROCESSING' | 'READY' | 'FAILED'
export type MaterialType = 'video' | 'image' | 'audio'

export interface Material {
  id: string
  ossKey: string
  ossUrl: string
  thumbnailUrl?: string
  previewUrl?: string
  name: string
  transcript?: string
  category: MaterialCategory
  duration: number        // seconds, 0 for images/audio
  fileSize: number        // bytes
  status: MaterialStatus
  usageCount: number
  createdAt: string       // ISO date string
  type: MaterialType
}

export interface AudioMaterial extends Material {
  type: 'audio'
  audioDuration: string   // "2:34" format
}

export type CreativeStatus = 'DRAFT' | 'PENDING' | 'ACTIVE'

export interface Creative {
  id: string
  ossKey: string
  ossUrl: string
  thumbnailUrl?: string
  name: string
  duration: number
  status: CreativeStatus
  srtUrl?: string
  srtLineCount?: number
  createdAt: string
}

export interface SceneSegment {
  startSec: number
  endSec: number
  label: string
  description: string
}

export interface Script {
  id: string
  name: string
  hook: string
  segmentCount: number
  durationSec: number
  scenes: SceneSegment[]
}

export type GenerateStep = 1 | 2 | 3 | 4 | 5

export type MessageRole = 'agent' | 'user'
export type MessageType = 'text' | 'progress'

export interface ChatMessage {
  id: string
  role: MessageRole
  type: MessageType
  content: string
  // for progress type
  label?: string
  subLabel?: string
  done?: boolean
}
```

- [ ] **Step 3: 创建全局 CSS**

```css
/* src/styles/global.css */
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@500;600;700&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body, #root {
  height: 100%;
  -webkit-font-smoothing: antialiased;
}

/* Thin scrollbars */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 2px; }
::-webkit-scrollbar-thumb:hover { background: #cbd5e1; }
```

- [ ] **Step 4: 创建 mock 数据**

```ts
// src/mocks/scripts.ts
import type { Script } from '../types'

export const mockScripts: Script[] = [
  {
    id: 's1',
    name: '痛点开场版',
    hook: '「你有没有遇过用了很多护肤品，皮肤还是干的问题…」',
    segmentCount: 12,
    durationSec: 36,
    scenes: [
      { startSec: 0,  endSec: 3,  label: '钩子',   description: '主播正面，口播痛点引入' },
      { startSec: 3,  endSec: 8,  label: '痛点展示', description: '手部特写，演示皮肤干燥状态' },
      { startSec: 8,  endSec: 14, label: '产品引入', description: '包装特写 + 口播成分介绍' },
      { startSec: 14, endSec: 22, label: '使用过程', description: '主播涂抹，展示产品质地' },
      { startSec: 22, endSec: 30, label: '效果对比', description: '使用前后皮肤状态特写' },
      { startSec: 30, endSec: 36, label: 'CTA',    description: '口播促单 + 价格展示' },
    ],
  },
  {
    id: 's2',
    name: '场景代入版',
    hook: '「冬天皮肤干到脱皮，这瓶精华让我找回了光泽感…」',
    segmentCount: 10,
    durationSec: 33,
    scenes: [
      { startSec: 0, endSec: 4, label: '场景', description: '冬日室内，主播坐在窗边，自然光' },
    ],
  },
  {
    id: 's3',
    name: '成分科普版',
    hook: '「为什么同样是保湿，玻尿酸和神经酰胺差这么多…」',
    segmentCount: 11,
    durationSec: 38,
    scenes: [],
  },
  {
    id: 's4',
    name: '产品特写版',
    hook: '「这瓶精华的质地真的绝，滴一滴撑一整个冬天…」',
    segmentCount: 9,
    durationSec: 31,
    scenes: [],
  },
]
```

```ts
// src/mocks/materials.ts
import type { Material } from '../types'

export const mockMaterials: Material[] = [
  { id: 'm1', ossKey: '', ossUrl: '', name: '人物素材-3s-主播开瓶介绍产品',   category: '人物', duration: 3,  fileSize: 12_000_000, status: 'READY',      usageCount: 12, createdAt: '2026-05-11', type: 'video', thumbnailUrl: '' },
  { id: 'm2', ossKey: '', ossUrl: '', name: '产品素材-5s-精华瓶旋转特写',     category: '产品', duration: 5,  fileSize: 28_000_000, status: 'READY',      usageCount: 8,  createdAt: '2026-05-11', type: 'video', thumbnailUrl: '' },
  { id: 'm3', ossKey: '', ossUrl: '', name: '场景素材-8s-室外自然光氛围',     category: '场景', duration: 8,  fileSize: 45_000_000, status: 'READY',      usageCount: 3,  createdAt: '2026-05-10', type: 'video', thumbnailUrl: '' },
  { id: 'm4', ossKey: '', ossUrl: '', name: '人物素材-4s-主播使用涂抹过程',   category: '人物', duration: 4,  fileSize: 19_000_000, status: 'PROCESSING', usageCount: 0,  createdAt: '2026-05-11', type: 'video', thumbnailUrl: '' },
  { id: 'm5', ossKey: '', ossUrl: '', name: '氛围素材-2s-白光转场过渡',       category: '氛围', duration: 2,  fileSize: 8_000_000,  status: 'READY',      usageCount: 21, createdAt: '2026-05-10', type: 'video', thumbnailUrl: '' },
  { id: 'm6', ossKey: '', ossUrl: '', name: '产品素材-6s-整套护肤品包装展示', category: '产品', duration: 6,  fileSize: 33_000_000, status: 'READY',      usageCount: 5,  createdAt: '2026-05-10', type: 'video', thumbnailUrl: '' },
  { id: 'm7', ossKey: '', ossUrl: '', name: '场景素材-3s-睡前护肤桌面特写',   category: '场景', duration: 3,  fileSize: 16_000_000, status: 'READY',      usageCount: 7,  createdAt: '2026-05-10', type: 'video', thumbnailUrl: '' },
  { id: 'm8', ossKey: '', ossUrl: '', name: '产品素材-4s-精华液滴落慢动作',   category: '产品', duration: 4,  fileSize: 22_000_000, status: 'FAILED',     usageCount: 0,  createdAt: '2026-05-10', type: 'video', thumbnailUrl: '' },
  // images
  { id: 'i1', ossKey: '', ossUrl: '', name: '产品主图-精华瓶正面白底', category: '产品', duration: 0, fileSize: 2_100_000, status: 'READY', usageCount: 4, createdAt: '2026-05-11', type: 'image', thumbnailUrl: '' },
  { id: 'i2', ossKey: '', ossUrl: '', name: '背景图-米白色纹理简约',   category: '场景', duration: 0, fileSize: 1_400_000, status: 'READY', usageCount: 2, createdAt: '2026-05-11', type: 'image', thumbnailUrl: '' },
  { id: 'i3', ossKey: '', ossUrl: '', name: '字幕板-限时折扣贴片',     category: '字幕板', duration: 0, fileSize: 300_000,  status: 'READY', usageCount: 9, createdAt: '2026-05-11', type: 'image', thumbnailUrl: '' },
]

export const mockAudioMaterials = [
  { id: 'a1', name: '轻快活力-电商BGM-01',  category: 'BGM',  audioDuration: '2:34', fileSize: 3_200_000, status: 'READY' as const, createdAt: '2026-05-11' },
  { id: 'a2', name: '温柔治愈-护肤氛围-02', category: 'BGM',  audioDuration: '3:12', fileSize: 4_500_000, status: 'READY' as const, createdAt: '2026-05-11' },
  { id: 'a3', name: '转场音效-闪光-01',     category: '音效', audioDuration: '0:02', fileSize: 100_000,   status: 'READY' as const, createdAt: '2026-05-11' },
]
```

```ts
// src/mocks/creatives.ts
import type { Creative } from '../types'

export const mockCreatives: Creative[] = [
  { id: 'c1', ossKey: '', ossUrl: '', name: '护肤品-痛点版-v1', duration: 36, status: 'ACTIVE',   srtUrl: '',  srtLineCount: 18, createdAt: '2026-05-11T14:32:00' },
  { id: 'c2', ossKey: '', ossUrl: '', name: '护肤品-场景版-v1', duration: 33, status: 'PENDING',  srtUrl: '',  srtLineCount: 15, createdAt: '2026-05-11T11:05:00' },
  { id: 'c3', ossKey: '', ossUrl: '', name: '面膜-科普版-v2',   duration: 38, status: 'ACTIVE',   srtUrl: '',  srtLineCount: 22, createdAt: '2026-05-10T16:40:00' },
  { id: 'c4', ossKey: '', ossUrl: '', name: '口红-特写版-v1',   duration: 31, status: 'DRAFT',    srtUrl: '',  srtLineCount: 12, createdAt: '2026-05-10T09:18:00' },
  { id: 'c5', ossKey: '', ossUrl: '', name: '面霜-对比版-v1',   duration: 34, status: 'ACTIVE',   srtUrl: '',  srtLineCount: 16, createdAt: '2026-05-10T08:55:00' },
]
```

- [ ] **Step 5: 修改 main.tsx，挂载主题和全局样式**

```tsx
// src/main.tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import { theme } from './theme'
import './styles/global.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ConfigProvider theme={theme} locale={zhCN}>
        <App />
      </ConfigProvider>
    </BrowserRouter>
  </React.StrictMode>
)
```

- [ ] **Step 6: 运行验证**

```bash
cd flowcut_frontend && npm run dev
```

期望：Vite 启动成功，浏览器打开显示默认 Vite 页面（无报错）。

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: project setup — theme, types, mock data"
```

---

## Task 2：Zustand Store

**Files:**
- Create: `src/stores/generateStore.ts`
- Create: `src/stores/materialStore.ts`
- Create: `src/stores/creativeStore.ts`

- [ ] **Step 1: 创建 generateStore**

```ts
// src/stores/generateStore.ts
import { create } from 'zustand'
import type { GenerateStep, ChatMessage, Script } from '../types'
import { mockScripts } from '../mocks/scripts'

interface GenerateState {
  step: GenerateStep
  messages: ChatMessage[]
  scripts: Script[]
  selectedScriptId: string | null
  isAgentTyping: boolean

  setStep: (step: GenerateStep) => void
  addMessage: (msg: Omit<ChatMessage, 'id'>) => void
  setScripts: (scripts: Script[]) => void
  selectScript: (id: string) => void
  setAgentTyping: (typing: boolean) => void
  sendUserMessage: (text: string) => void
}

let msgCounter = 0
const newId = () => `msg-${++msgCounter}`

export const useGenerateStore = create<GenerateState>((set, get) => ({
  step: 2,  // demo starts at step 2 (script selection)
  messages: [
    { id: newId(), role: 'agent', type: 'text', content: '你好！请上传一条爆款视频（30-40 秒），我来帮你拆解分镜、生成差异化脚本。' },
    { id: newId(), role: 'user',  type: 'text', content: '已上传：护肤品爆款-38s.mp4' },
    { id: newId(), role: 'agent', type: 'progress', content: '', label: '分镜拆解完成', subLabel: '识别 12 个分镜段 · 耗时 8s', done: true },
    { id: newId(), role: 'agent', type: 'text', content: '已生成 4 条差异化脚本，右侧选一条继续。' },
    { id: newId(), role: 'user',  type: 'text', content: '选脚本 1，继续。' },
    { id: newId(), role: 'agent', type: 'progress', content: '', label: '正在匹配素材库…', subLabel: '画面主匹配 + 口播二排', done: false },
  ],
  scripts: mockScripts,
  selectedScriptId: 's1',
  isAgentTyping: true,

  setStep: (step) => set({ step }),
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, { ...msg, id: newId() }] })),
  setScripts: (scripts) => set({ scripts }),
  selectScript: (id) => set({ selectedScriptId: id }),
  setAgentTyping: (typing) => set({ isAgentTyping: typing }),

  sendUserMessage: (text) => {
    const { addMessage, setAgentTyping } = get()
    addMessage({ role: 'user', type: 'text', content: text })
    setAgentTyping(true)
    // Simulate agent response after 1.5s
    setTimeout(() => {
      addMessage({ role: 'agent', type: 'text', content: '收到，正在处理…' })
      setAgentTyping(false)
    }, 1500)
  },
}))
```

- [ ] **Step 2: 创建 materialStore**

```ts
// src/stores/materialStore.ts
import { create } from 'zustand'
import type { Material, MaterialCategory, MaterialType } from '../types'
import { mockMaterials, mockAudioMaterials } from '../mocks/materials'

interface MaterialState {
  materials: Material[]
  audioMaterials: typeof mockAudioMaterials
  activeSubTab: MaterialType
  activeCategory: MaterialCategory | '全部'

  setSubTab: (tab: MaterialType) => void
  setCategory: (cat: MaterialCategory | '全部') => void
  filteredMaterials: () => Material[]
}

export const useMaterialStore = create<MaterialState>((set, get) => ({
  materials: mockMaterials,
  audioMaterials: mockAudioMaterials,
  activeSubTab: 'video',
  activeCategory: '全部',

  setSubTab: (tab) => set({ activeSubTab: tab, activeCategory: '全部' }),
  setCategory: (cat) => set({ activeCategory: cat }),

  filteredMaterials: () => {
    const { materials, activeSubTab, activeCategory } = get()
    return materials
      .filter((m) => m.type === activeSubTab)
      .filter((m) => activeCategory === '全部' || m.category === activeCategory)
  },
}))
```

- [ ] **Step 3: 创建 creativeStore**

```ts
// src/stores/creativeStore.ts
import { create } from 'zustand'
import type { Creative, CreativeStatus } from '../types'
import { mockCreatives } from '../mocks/creatives'

interface CreativeState {
  creatives: Creative[]
  activeSubTab: 'video' | 'srt'
  activeStatus: CreativeStatus | '全部'

  setSubTab: (tab: 'video' | 'srt') => void
  setStatus: (status: CreativeStatus | '全部') => void
  filteredCreatives: () => Creative[]
}

export const useCreativeStore = create<CreativeState>((set, get) => ({
  creatives: mockCreatives,
  activeSubTab: 'video',
  activeStatus: '全部',

  setSubTab: (tab) => set({ activeSubTab: tab }),
  setStatus: (status) => set({ activeStatus: status }),

  filteredCreatives: () => {
    const { creatives, activeStatus } = get()
    if (activeStatus === '全部') return creatives
    const map: Record<string, CreativeStatus> = { '投放中': 'ACTIVE', '待上架': 'PENDING', '草稿': 'DRAFT' }
    return creatives.filter((c) => c.status === (map[activeStatus] ?? activeStatus))
  },
}))
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: zustand stores for generate, material, creative"
```

---

## Task 3：App Shell（Header + 路由 + 布局）

**Files:**
- Create: `src/components/layout/Header.tsx`
- Create: `src/router.tsx`
- Modify: `src/App.tsx`

- [ ] **Step 1: 创建 Header**

样式参考 demo.html 中的 `.hd`、`.logo`、`.tabs`、`.tb`。

```tsx
// src/components/layout/Header.tsx
import { useNavigate, useLocation } from 'react-router-dom'
import styles from './Header.module.css'

const TABS = [
  { path: '/',         label: '生成' },
  { path: '/material', label: '素材库' },
  { path: '/creative', label: '成片库' },
]

export default function Header() {
  const navigate = useNavigate()
  const { pathname } = useLocation()

  return (
    <header className={styles.header}>
      <a className={styles.logo} href="/">
        <div className={styles.logoIcon}>✦</div>
        <span className={styles.logoText}>FlowCut</span>
      </a>
      <div className={styles.tabs}>
        {TABS.map((t) => (
          <button
            key={t.path}
            className={`${styles.tab} ${pathname === t.path ? styles.active : ''}`}
            onClick={() => navigate(t.path)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className={styles.right}>
        <div className={styles.avatar}>运</div>
      </div>
    </header>
  )
}
```

```css
/* src/components/layout/Header.module.css */
.header {
  height: 54px;
  background: #fff;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  padding: 0 20px;
  flex-shrink: 0;
  z-index: 10;
}
.logo { display: flex; align-items: center; gap: 8px; margin-right: 28px; text-decoration: none; }
.logoIcon { width: 30px; height: 30px; background: linear-gradient(135deg, #2563eb, #6366f1); border-radius: 8px; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 15px; }
.logoText { font-size: 17px; font-weight: 700; color: #0f172a; letter-spacing: -0.3px; }
.tabs { display: flex; gap: 2px; background: #f1f5f9; padding: 3px; border-radius: 10px; }
.tab { padding: 5px 15px; border-radius: 7px; border: none; background: transparent; color: #475569; font-size: 13px; font-weight: 500; cursor: pointer; transition: all 160ms; font-family: inherit; }
.tab:hover { background: #fff; color: #0f172a; }
.tab.active { background: #fff; color: #2563eb; font-weight: 600; box-shadow: 0 1px 2px rgba(0,0,0,.05); }
.right { margin-left: auto; }
.avatar { width: 30px; height: 30px; border-radius: 50%; background: linear-gradient(135deg, #f59e0b, #ef4444); display: flex; align-items: center; justify-content: center; color: #fff; font-size: 12px; font-weight: 700; cursor: pointer; }
```

- [ ] **Step 2: 创建路由**

```tsx
// src/router.tsx
import { Routes, Route } from 'react-router-dom'
import GenerateTab from './components/generate/GenerateTab'
import MaterialTab from './components/material/MaterialTab'
import CreativeTab from './components/creative/CreativeTab'

export default function AppRouter() {
  return (
    <Routes>
      <Route path="/"         element={<GenerateTab />} />
      <Route path="/material" element={<MaterialTab />} />
      <Route path="/creative" element={<CreativeTab />} />
    </Routes>
  )
}
```

- [ ] **Step 3: 修改 App.tsx**

```tsx
// src/App.tsx
import Header from './components/layout/Header'
import AppRouter from './router'
import styles from './App.module.css'

export default function App() {
  return (
    <div className={styles.app}>
      <Header />
      <main className={styles.main}>
        <AppRouter />
      </main>
    </div>
  )
}
```

```css
/* src/App.module.css */
.app { display: flex; flex-direction: column; height: 100vh; overflow: hidden; background: #f1f5f9; }
.main { flex: 1; overflow: hidden; display: flex; }
```

- [ ] **Step 4: 创建空壳页面（防止路由报错）**

为 GenerateTab、MaterialTab、CreativeTab 各创建最小实现：

```tsx
// src/components/generate/GenerateTab.tsx
export default function GenerateTab() {
  return <div style={{ padding: 24, flex: 1 }}>生成 Tab（待实现）</div>
}
```

```tsx
// src/components/material/MaterialTab.tsx
export default function MaterialTab() {
  return <div style={{ padding: 24, flex: 1 }}>素材库 Tab（待实现）</div>
}
```

```tsx
// src/components/creative/CreativeTab.tsx
export default function CreativeTab() {
  return <div style={{ padding: 24, flex: 1 }}>成片库 Tab（待实现）</div>
}
```

- [ ] **Step 5: 运行验证**

```bash
npm run dev
```

期望：三个 Tab 可点击切换，Header 样式与 demo.html 一致（Logo + tab 胶囊 + 头像）。

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: app shell — header, routing, layout"
```

---

## Task 4：Common 公共组件

**Files:**
- Create: `src/components/common/DateGroup.tsx`
- Create: `src/components/common/FilterChips.tsx`
- Create: `src/components/common/StatusBadge.tsx`

- [ ] **Step 1: StatusBadge**

```tsx
// src/components/common/StatusBadge.tsx
import type { MaterialStatus } from '../../types'

const CONFIG: Record<MaterialStatus, { label: string; bg: string; color: string }> = {
  READY:      { label: 'READY',  bg: '#d1fae5', color: '#059669' },
  PROCESSING: { label: '处理中', bg: '#fef3c7', color: '#d97706' },
  FAILED:     { label: '失败',   bg: '#fee2e2', color: '#dc2626' },
}

export default function StatusBadge({ status }: { status: MaterialStatus }) {
  const c = CONFIG[status]
  return (
    <span style={{ fontSize: 10, padding: '2px 5px', borderRadius: 3, fontWeight: 600, background: c.bg, color: c.color }}>
      {c.label}
    </span>
  )
}
```

- [ ] **Step 2: FilterChips**

```tsx
// src/components/common/FilterChips.tsx
import styles from './FilterChips.module.css'

interface Props {
  options: string[]
  active: string
  onChange: (val: string) => void
}

export default function FilterChips({ options, active, onChange }: Props) {
  return (
    <div className={styles.chips}>
      {options.map((opt) => (
        <button
          key={opt}
          className={`${styles.chip} ${active === opt ? styles.active : ''}`}
          onClick={() => onChange(opt)}
        >
          {opt}
        </button>
      ))}
    </div>
  )
}
```

```css
/* src/components/common/FilterChips.module.css */
.chips { display: flex; gap: 5px; flex-wrap: wrap; }
.chip { padding: 5px 11px; border-radius: 20px; border: 1px solid #e2e8f0; background: #fff; font-size: 12px; font-weight: 500; color: #475569; cursor: pointer; transition: all 140ms; font-family: inherit; }
.chip:hover { border-color: #2563eb; color: #2563eb; }
.chip.active { border-color: #2563eb; background: #eff6ff; color: #2563eb; }
```

- [ ] **Step 3: DateGroup**

```tsx
// src/components/common/DateGroup.tsx
import type { ReactNode } from 'react'
import styles from './DateGroup.module.css'

interface Props {
  label: string
  children: ReactNode
}

export default function DateGroup({ label, children }: Props) {
  return (
    <div className={styles.group}>
      <div className={styles.label}>{label}</div>
      {children}
    </div>
  )
}
```

```css
/* src/components/common/DateGroup.module.css */
.group { margin-bottom: 24px; }
.label { font-size: 13px; font-weight: 600; color: #475569; margin-bottom: 10px; }
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: common components — StatusBadge, FilterChips, DateGroup"
```

---

## Task 5：Generate Tab — Chat Panel

**Files:**
- Create: `src/components/generate/ChatPanel.tsx`
- Create: `src/components/generate/ChatPanel.module.css`

- [ ] **Step 1: 实现 ChatPanel**

参考 demo.html 中 `.chat`、`.msgs`、`.bubble`、`.prog`、`.typing`、`.inp-wrap`。

```tsx
// src/components/generate/ChatPanel.tsx
import { useRef, useEffect, useState } from 'react'
import { useGenerateStore } from '../../stores/generateStore'
import type { ChatMessage } from '../../types'
import styles from './ChatPanel.module.css'

function ProgressCard({ msg }: { msg: ChatMessage }) {
  return (
    <div className={styles.progCard}>
      <div className={`${styles.progIcon} ${msg.done ? styles.done : styles.running}`}>
        {msg.done ? '✅' : <span className={styles.spinner} />}
      </div>
      <div>
        <div className={styles.progLabel}>{msg.label}</div>
        <div className={styles.progSub}>{msg.subLabel}</div>
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className={styles.typing}>
      <span /><span /><span />
    </div>
  )
}

export default function ChatPanel() {
  const { messages, isAgentTyping, sendUserMessage } = useGenerateStore()
  const [input, setInput] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isAgentTyping])

  const handleSend = () => {
    const text = input.trim()
    if (!text) return
    sendUserMessage(text)
    setInput('')
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = `${Math.min(e.target.scrollHeight, 72)}px`
  }

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span className={styles.title}>当前任务</span>
        <button className={styles.newBtn}>＋ 新任务</button>
      </div>

      <div className={styles.messages}>
        {messages.map((msg) => (
          <div key={msg.id} className={`${styles.msg} ${msg.role === 'user' ? styles.user : styles.agent}`}>
            {msg.role === 'agent' && msg.type === 'text' && <span className={styles.who}>Agent</span>}
            {msg.role === 'user' && msg.type === 'text' && <span className={styles.who}>我</span>}
            {msg.type === 'progress'
              ? <ProgressCard msg={msg} />
              : <div className={styles.bubble}>{msg.content}</div>
            }
          </div>
        ))}
        {isAgentTyping && (
          <div className={`${styles.msg} ${styles.agent}`}>
            <TypingIndicator />
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className={styles.inputArea}>
        <div className={styles.inputBox}>
          <textarea
            ref={taRef}
            rows={1}
            placeholder="输入指令，或直接确认当前步骤…"
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
          />
          <button className={styles.sendBtn} onClick={handleSend}>↑</button>
        </div>
      </div>
    </div>
  )
}
```

```css
/* src/components/generate/ChatPanel.module.css */
.panel { width: 340px; flex-shrink: 0; background: #fff; border-right: 1px solid #e2e8f0; display: flex; flex-direction: column; height: 100%; }
.header { padding: 12px 16px; border-bottom: 1px solid #e2e8f0; display: flex; align-items: center; justify-content: space-between; }
.title { font-size: 13px; font-weight: 600; color: #0f172a; }
.newBtn { font-size: 12px; color: #2563eb; background: #eff6ff; border: none; padding: 4px 10px; border-radius: 5px; cursor: pointer; font-family: inherit; font-weight: 500; }
.newBtn:hover { background: #dbeafe; }
.messages { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.msg { display: flex; flex-direction: column; gap: 3px; animation: fadeUp 200ms ease; }
@keyframes fadeUp { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
.user { align-items: flex-end; }
.agent { align-items: flex-start; }
.who { font-size: 11px; color: #94a3b8; padding: 0 3px; font-weight: 500; }
.bubble { max-width: 90%; padding: 8px 11px; border-radius: 10px; font-size: 13px; line-height: 1.6; }
.user .bubble { background: #2563eb; color: #fff; border-radius: 10px 10px 2px 10px; }
.agent .bubble { background: #eef2ff; color: #0f172a; border-radius: 10px 10px 10px 2px; border: 1px solid #e0e4ff; }
.progCard { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 9px 11px; font-size: 12px; max-width: 94%; display: flex; align-items: center; gap: 9px; box-shadow: 0 1px 2px rgba(0,0,0,.05); }
.progIcon { width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex-shrink: 0; font-size: 13px; }
.done { background: #d1fae5; }
.running { background: #eff6ff; }
.progLabel { font-weight: 600; color: #0f172a; margin-bottom: 1px; }
.progSub { color: #94a3b8; font-size: 11px; }
.spinner { width: 13px; height: 13px; border: 2px solid #dbeafe; border-top-color: #2563eb; border-radius: 50%; animation: spin 700ms linear infinite; display: block; }
@keyframes spin { to { transform: rotate(360deg); } }
.typing { display: flex; gap: 4px; align-items: center; padding: 9px 13px; background: #eef2ff; border-radius: 10px 10px 10px 2px; border: 1px solid #e0e4ff; }
.typing span { width: 6px; height: 6px; background: #6366f1; border-radius: 50%; animation: dot 1.2s ease-in-out infinite; opacity: 0.4; }
.typing span:nth-child(2) { animation-delay: .2s; }
.typing span:nth-child(3) { animation-delay: .4s; }
@keyframes dot { 0%,60%,100% { opacity:.4; transform:scale(1); } 30% { opacity:1; transform:scale(1.2); } }
.inputArea { padding: 10px 14px; border-top: 1px solid #e2e8f0; background: #fff; }
.inputBox { background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 10px; padding: 9px 10px; display: flex; align-items: flex-end; gap: 7px; transition: border-color 150ms; }
.inputBox:focus-within { border-color: #2563eb; background: #fff; }
.inputBox textarea { flex: 1; border: none; background: transparent; resize: none; font-size: 13px; color: #0f172a; font-family: inherit; outline: none; line-height: 1.5; max-height: 72px; }
.inputBox textarea::placeholder { color: #94a3b8; }
.sendBtn { width: 30px; height: 30px; background: #2563eb; border: none; border-radius: 7px; color: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; font-size: 13px; transition: background 150ms; }
.sendBtn:hover { background: #1d4ed8; }
```

- [ ] **Step 2: 运行验证**

将 GenerateTab 临时改为只渲染 `<ChatPanel />`（需要加 `display:flex` 包装），确认消息流、进度卡片、typing 动画、发送功能均正常。

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "feat: generate tab — chat panel"
```

---

## Task 6：Generate Tab — StepBar + ContentPanel + 所有 Steps

**Files:**
- Create: `src/components/generate/StepBar.tsx`
- Create: `src/components/generate/StepBar.module.css`
- Create: `src/components/generate/ContentPanel.tsx`
- Create: `src/components/generate/steps/UploadStep.tsx`
- Create: `src/components/generate/steps/ScriptStep.tsx`
- Create: `src/components/generate/steps/MatchingStep.tsx`
- Create: `src/components/generate/steps/ConfirmStep.tsx`
- Create: `src/components/generate/steps/PlaceholderStep.tsx`
- Modify: `src/components/generate/GenerateTab.tsx`

- [ ] **Step 1: StepBar**

参考 demo.html `.sbar`、`.sdot`、`.sline`、`.slabel`。

```tsx
// src/components/generate/StepBar.tsx
import { useGenerateStore } from '../../stores/generateStore'
import type { GenerateStep } from '../../types'
import styles from './StepBar.module.css'

const STEPS: { n: GenerateStep; label: string }[] = [
  { n: 1, label: '上传视频' },
  { n: 2, label: '选脚本' },
  { n: 3, label: '素材匹配' },
  { n: 4, label: '确认成片' },
  { n: 5, label: '上架千川' },
]

export default function StepBar() {
  const { step } = useGenerateStore()
  return (
    <div className={styles.bar}>
      {STEPS.map((s, i) => {
        const isDone    = step > s.n
        const isActive  = step === s.n
        const isLast    = s.n === 5
        const isDisabled = s.n === 5
        return (
          <div key={s.n} className={styles.item} style={{ flex: isLast ? 'none' : 1, minWidth: 0 }}>
            <div className={`${styles.dot} ${isDone ? styles.ok : isActive ? styles.on : styles.off} ${isDisabled ? styles.disabled : ''}`}>
              {isDone ? '✓' : s.n}
            </div>
            <span className={`${styles.label} ${isDone ? styles.labelOk : isActive ? styles.labelOn : ''} ${isDisabled ? styles.labelDisabled : ''}`}>
              {s.label}
            </span>
            {i < STEPS.length - 1 && (
              <div className={`${styles.line} ${isDone ? styles.lineOk : ''}`} />
            )}
          </div>
        )
      })}
    </div>
  )
}
```

```css
/* src/components/generate/StepBar.module.css */
.bar { padding: 12px 22px; background: #fff; border-bottom: 1px solid #e2e8f0; display: flex; align-items: center; flex-shrink: 0; }
.item { display: flex; align-items: center; gap: 5px; }
.dot { width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; flex-shrink: 0; }
.ok { background: #059669; color: #fff; }
.on { background: #2563eb; color: #fff; box-shadow: 0 0 0 3px #dbeafe; }
.off { background: #e2e8f0; color: #94a3b8; }
.disabled { background: #f1f5f9; color: #cbd5e1; }
.label { font-size: 11px; color: #94a3b8; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.labelOk { color: #059669; }
.labelOn { color: #2563eb; font-weight: 600; }
.labelDisabled { color: #cbd5e1; }
.line { flex: 1; height: 1px; background: #e2e8f0; margin: 0 6px; flex-shrink: 0; min-width: 10px; }
.lineOk { background: #059669; }
```

- [ ] **Step 2: 五个 Step 组件**

```tsx
// src/components/generate/steps/UploadStep.tsx
import { Upload } from 'antd'
import { useGenerateStore } from '../../../stores/generateStore'
import styles from './Step.module.css'

export default function UploadStep() {
  const { setStep, addMessage, setAgentTyping } = useGenerateStore()

  const handleUpload = () => {
    addMessage({ role: 'user', type: 'text', content: '已上传：爆款视频.mp4' })
    setAgentTyping(true)
    setTimeout(() => {
      addMessage({ role: 'agent', type: 'progress', content: '', label: '分镜拆解完成', subLabel: '识别 12 个分镜段', done: true })
      addMessage({ role: 'agent', type: 'text', content: '已生成脚本，请在右侧选择。' })
      setAgentTyping(false)
      setStep(2)
    }, 2000)
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.title}>上传爆款视频</div>
      <div className={styles.sub}>上传一条 30-40 秒的爆款视频，Agent 将自动拆解分镜并生成差异化脚本。</div>
      <Upload.Dragger
        accept="video/*"
        beforeUpload={() => { handleUpload(); return false }}
        style={{ borderRadius: 10 }}
      >
        <p style={{ fontSize: 32 }}>🎬</p>
        <p style={{ fontSize: 14, fontWeight: 600, color: '#0f172a', margin: '8px 0 4px' }}>拖拽视频文件到此处，或点击上传</p>
        <p style={{ fontSize: 12, color: '#94a3b8' }}>支持 MP4、MOV，建议 30-40 秒</p>
      </Upload.Dragger>
    </div>
  )
}
```

```tsx
// src/components/generate/steps/ScriptStep.tsx
import { useState } from 'react'
import { useGenerateStore } from '../../../stores/generateStore'
import styles from './Step.module.css'

export default function ScriptStep() {
  const { scripts, selectedScriptId, selectScript, addMessage, setAgentTyping, setStep } = useGenerateStore()
  const [expanded, setExpanded] = useState<string | null>(selectedScriptId)

  const handleConfirm = () => {
    const sel = scripts.find((s) => s.id === selectedScriptId)
    if (!sel) return
    addMessage({ role: 'user', type: 'text', content: `确认脚本：${sel.name}` })
    addMessage({ role: 'agent', type: 'progress', content: '', label: '正在匹配素材库…', subLabel: '画面主匹配 + 口播二排', done: false })
    setAgentTyping(true)
    setTimeout(() => {
      addMessage({ role: 'agent', type: 'text', content: '匹配完成：已匹配 8 段，低匹配 3 段，缺失 1 段，右侧查看详情。' })
      setAgentTyping(false)
      setStep(3)
    }, 2000)
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.title}>选择脚本</div>
      <div className={styles.sub}>已基于爆款视频生成 {scripts.length} 条差异化脚本，点击展开分镜详情，选择一条继续。</div>
      <div className={styles.scriptList}>
        {scripts.map((sc) => {
          const isSel = selectedScriptId === sc.id
          const isExp = expanded === sc.id
          return (
            <div
              key={sc.id}
              className={`${styles.scriptCard} ${isSel ? styles.selected : ''}`}
              onClick={() => { selectScript(sc.id); setExpanded(sc.id) }}
            >
              <div className={styles.scHead}>
                <div className={`${styles.scNum} ${isSel ? styles.scNumSel : ''}`}>{sc.id.replace('s', '')}</div>
                <div className={styles.scInfo}>
                  <div className={styles.scName}>{sc.name}</div>
                  <div className={styles.scHook}>{sc.hook}</div>
                </div>
                <div className={styles.scRight}>
                  {isSel && <span className={styles.selBadge}>已选</span>}
                  <div className={styles.tags}>
                    <span className={styles.tagBlue}>{sc.segmentCount}段</span>
                    <span className={styles.tagGray}>{sc.durationSec}s</span>
                  </div>
                </div>
              </div>
              {isExp && sc.scenes.length > 0 && (
                <div className={styles.scDetail}>
                  {sc.scenes.map((scene, i) => (
                    <div key={i} className={styles.scene}>
                      <span className={styles.sceneTime}>{scene.startSec}s – {scene.endSec}s</span>
                      <span className={styles.sceneDesc}><strong>{scene.label}</strong>：{scene.description}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <button className={styles.actionBtn} onClick={handleConfirm} disabled={!selectedScriptId}>
        确认脚本，开始匹配素材 →
      </button>
    </div>
  )
}
```

```tsx
// src/components/generate/steps/MatchingStep.tsx
import { useGenerateStore } from '../../../stores/generateStore'
import styles from './Step.module.css'

const MOCK_RESULTS = [
  { idx: 1, label: '钩子', source: 'library', matchStatus: 'matched' as const, materialName: '人物素材-3s-主播开瓶' },
  { idx: 2, label: '痛点展示', source: 'library', matchStatus: 'low' as const, materialName: '产品素材-4s-精华液（低匹配）' },
  { idx: 3, label: '产品引入', source: 'library', matchStatus: 'matched' as const, materialName: '产品素材-5s-精华瓶旋转' },
  { idx: 4, label: '使用过程', source: 'library', matchStatus: 'missing' as const, materialName: '—' },
  { idx: 5, label: '效果对比', source: 'library', matchStatus: 'matched' as const, materialName: '人物素材-4s-涂抹过程' },
  { idx: 6, label: 'CTA',   source: 'library', matchStatus: 'matched' as const, materialName: '场景素材-3s-桌面特写' },
]

const STATUS_MAP = {
  matched: { label: '已匹配', color: '#059669', bg: '#d1fae5' },
  low:     { label: '低匹配', color: '#d97706', bg: '#fef3c7' },
  missing: { label: '缺失',   color: '#dc2626', bg: '#fee2e2' },
}

export default function MatchingStep() {
  const { addMessage, setAgentTyping, setStep } = useGenerateStore()

  const handleConfirm = () => {
    addMessage({ role: 'user', type: 'text', content: '确认匹配结果，开始合成初剪。' })
    addMessage({ role: 'agent', type: 'progress', content: '', label: '正在合成初剪…', subLabel: 'Agent 评估中（第 1/3 轮）', done: false })
    setAgentTyping(true)
    setTimeout(() => {
      addMessage({ role: 'agent', type: 'text', content: '初剪评估通过，成片已生成，请在右侧确认。' })
      setAgentTyping(false)
      setStep(4)
    }, 2000)
  }

  const matched = MOCK_RESULTS.filter((r) => r.matchStatus === 'matched').length
  const low     = MOCK_RESULTS.filter((r) => r.matchStatus === 'low').length
  const missing = MOCK_RESULTS.filter((r) => r.matchStatus === 'missing').length

  return (
    <div className={styles.wrap}>
      <div className={styles.title}>素材匹配结果</div>
      <div className={styles.sub}>已为脚本 1 匹配素材，共 {MOCK_RESULTS.length} 段。可手动替换后确认。</div>
      <div className={styles.matchSummary}>
        <div className={styles.matchStat} style={{ background: '#d1fae5', color: '#059669' }}>✓ 已匹配 {matched}</div>
        <div className={styles.matchStat} style={{ background: '#fef3c7', color: '#d97706' }}>△ 低匹配 {low}</div>
        <div className={styles.matchStat} style={{ background: '#fee2e2', color: '#dc2626' }}>✗ 缺失 {missing}</div>
      </div>
      <div className={styles.matchList}>
        {MOCK_RESULTS.map((r) => {
          const s = STATUS_MAP[r.matchStatus]
          return (
            <div key={r.idx} className={styles.matchRow}>
              <span className={styles.matchIdx}>{r.idx}</span>
              <span className={styles.matchLabel}>{r.label}</span>
              <span className={styles.matchMaterial}>{r.materialName}</span>
              <span className={styles.matchBadge} style={{ background: s.bg, color: s.color }}>{s.label}</span>
            </div>
          )
        })}
      </div>
      <button className={styles.actionBtn} onClick={handleConfirm}>确认匹配，开始合成 →</button>
    </div>
  )
}
```

```tsx
// src/components/generate/steps/ConfirmStep.tsx
import { useGenerateStore } from '../../../stores/generateStore'
import styles from './Step.module.css'

export default function ConfirmStep() {
  const { addMessage, setStep } = useGenerateStore()

  const handleConfirm = () => {
    addMessage({ role: 'user', type: 'text', content: '确认成片，可以上架。' })
    setStep(5)
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.title}>确认成片</div>
      <div className={styles.sub}>初剪已完成，请预览并确认。SRT 字幕已自动生成。</div>
      <div className={styles.videoPreview}>
        <div style={{ width: '100%', aspectRatio: '16/9', background: 'linear-gradient(135deg, #fde68a, #f59e0b, #ef4444)', borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 40 }}>
          🎬
        </div>
        <div style={{ marginTop: 8, fontSize: 12, color: '#475569' }}>护肤品-痛点版-v1 · 36s · 18 条字幕</div>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        <button className={styles.actionBtn} onClick={handleConfirm} style={{ flex: 2 }}>确认成片 →</button>
        <button className={styles.actionBtnSecondary} style={{ flex: 1 }}>重新合成</button>
      </div>
    </div>
  )
}
```

```tsx
// src/components/generate/steps/PlaceholderStep.tsx
import styles from './Step.module.css'

export default function PlaceholderStep() {
  return (
    <div className={styles.wrap} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: 1, gap: 10 }}>
      <div style={{ fontSize: 40 }}>🚀</div>
      <div className={styles.title}>千川一键上架</div>
      <div className={styles.sub} style={{ textAlign: 'center', maxWidth: 280 }}>该功能正在开发中，即将上线。成片已保存至成片库，可手动上架千川。</div>
    </div>
  )
}
```

- [ ] **Step 3: 共享 Step 样式**

```css
/* src/components/generate/steps/Step.module.css */
.wrap { padding: 22px; display: flex; flex-direction: column; height: 100%; }
.title { font-size: 15px; font-weight: 700; color: #0f172a; margin-bottom: 3px; }
.sub { font-size: 13px; color: #475569; margin-bottom: 18px; }
.scriptList { display: flex; flex-direction: column; gap: 9px; margin-bottom: 18px; overflow-y: auto; flex: 1; }
.scriptCard { background: #fff; border: 2px solid #e2e8f0; border-radius: 14px; overflow: hidden; cursor: pointer; transition: border-color 180ms, box-shadow 180ms; }
.scriptCard:hover { border-color: #dbeafe; box-shadow: 0 4px 6px -1px rgba(0,0,0,.08); }
.selected { border-color: #2563eb; box-shadow: 0 0 0 3px #dbeafe; }
.scHead { padding: 12px 14px; display: flex; align-items: flex-start; gap: 9px; }
.scNum { width: 26px; height: 26px; border-radius: 7px; background: #f1f5f9; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; color: #475569; flex-shrink: 0; margin-top: 1px; }
.scNumSel { background: #eff6ff; color: #2563eb; }
.scInfo { flex: 1; min-width: 0; }
.scName { font-size: 13px; font-weight: 600; color: #0f172a; margin-bottom: 2px; }
.scHook { font-size: 12px; color: #475569; line-height: 1.4; }
.scRight { display: flex; flex-direction: column; align-items: flex-end; gap: 5px; flex-shrink: 0; }
.selBadge { font-size: 11px; padding: 2px 8px; background: #2563eb; color: #fff; border-radius: 4px; font-weight: 600; }
.tags { display: flex; gap: 3px; }
.tagBlue { font-size: 11px; padding: 2px 6px; border-radius: 4px; background: #eff6ff; color: #2563eb; font-weight: 500; }
.tagGray { font-size: 11px; padding: 2px 6px; border-radius: 4px; background: #f1f5f9; color: #475569; font-weight: 500; }
.scDetail { padding: 0 14px 12px; display: flex; flex-direction: column; gap: 5px; }
.scene { display: flex; gap: 9px; align-items: flex-start; padding: 7px 9px; background: #f8fafc; border-radius: 6px; }
.sceneTime { font-size: 11px; color: #94a3b8; white-space: nowrap; min-width: 60px; margin-top: 1px; }
.sceneDesc { font-size: 12px; color: #475569; line-height: 1.5; }
.sceneDesc strong { color: #0f172a; font-weight: 600; }
.actionBtn { width: 100%; padding: 11px; background: #2563eb; color: #fff; border: none; border-radius: 10px; font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit; transition: background 150ms; }
.actionBtn:hover { background: #1d4ed8; }
.actionBtn:disabled { background: #e2e8f0; color: #94a3b8; cursor: not-allowed; }
.actionBtnSecondary { padding: 11px; background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0; border-radius: 10px; font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit; }
.matchSummary { display: flex; gap: 8px; margin-bottom: 14px; }
.matchStat { flex: 1; padding: 8px; border-radius: 8px; font-size: 12px; font-weight: 600; text-align: center; }
.matchList { display: flex; flex-direction: column; gap: 6px; margin-bottom: 16px; overflow-y: auto; flex: 1; }
.matchRow { display: grid; grid-template-columns: 24px 80px 1fr auto; gap: 8px; align-items: center; padding: 8px 10px; background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 12px; }
.matchIdx { font-weight: 700; color: #94a3b8; text-align: center; }
.matchLabel { font-weight: 600; color: #0f172a; }
.matchMaterial { color: #475569; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.matchBadge { font-size: 10px; padding: 2px 6px; border-radius: 3px; font-weight: 600; white-space: nowrap; }
.videoPreview { display: flex; flex-direction: column; }
```

- [ ] **Step 4: ContentPanel + GenerateTab**

```tsx
// src/components/generate/ContentPanel.tsx
import { useGenerateStore } from '../../stores/generateStore'
import StepBar from './StepBar'
import UploadStep from './steps/UploadStep'
import ScriptStep from './steps/ScriptStep'
import MatchingStep from './steps/MatchingStep'
import ConfirmStep from './steps/ConfirmStep'
import PlaceholderStep from './steps/PlaceholderStep'
import styles from './ContentPanel.module.css'

const STEP_MAP = {
  1: UploadStep,
  2: ScriptStep,
  3: MatchingStep,
  4: ConfirmStep,
  5: PlaceholderStep,
}

export default function ContentPanel() {
  const { step } = useGenerateStore()
  const StepComponent = STEP_MAP[step]
  return (
    <div className={styles.panel}>
      <StepBar />
      <div className={styles.content}>
        <StepComponent />
      </div>
    </div>
  )
}
```

```css
/* src/components/generate/ContentPanel.module.css */
.panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.content { flex: 1; overflow-y: auto; }
```

```tsx
// src/components/generate/GenerateTab.tsx
import ChatPanel from './ChatPanel'
import ContentPanel from './ContentPanel'
import styles from './GenerateTab.module.css'

export default function GenerateTab() {
  return (
    <div className={styles.layout}>
      <ChatPanel />
      <ContentPanel />
    </div>
  )
}
```

```css
/* src/components/generate/GenerateTab.module.css */
.layout { display: flex; height: 100%; width: 100%; }
```

- [ ] **Step 5: 运行验证**

```bash
npm run dev
```

期望：生成 Tab 完整可用，5 步可通过点击走通，对话同步更新，步骤指示器正确高亮。

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: generate tab — step bar, all steps, content panel"
```

---

## Task 7：Material Tab（视频 + 图片 + 音频）

**Files:**
- Create: `src/components/material/MaterialCard.tsx`
- Create: `src/components/material/MaterialCard.module.css`
- Create: `src/components/material/UploadCard.tsx`
- Create: `src/components/material/AudioCard.tsx`
- Create: `src/components/material/AudioCard.module.css`
- Create: `src/components/material/VideoLibrary.tsx`
- Create: `src/components/material/ImageLibrary.tsx`
- Create: `src/components/material/AudioLibrary.tsx`
- Modify: `src/components/material/MaterialTab.tsx`

- [ ] **Step 1: MaterialCard**

参考 demo.html `.mcard`、`.mthumb`、`.minfo`。缩略图区域 MVP 用渐变色 + emoji 替代真实截图。

```tsx
// src/components/material/MaterialCard.tsx
import type { Material } from '../../types'
import StatusBadge from '../common/StatusBadge'
import styles from './MaterialCard.module.css'

// Gradient palettes by category
const PALETTE: Record<string, string> = {
  '人物': 'linear-gradient(135deg,#fce7f3,#f9a8d4)',
  '产品': 'linear-gradient(135deg,#a7f3d0,#34d399)',
  '场景': 'linear-gradient(135deg,#bfdbfe,#60a5fa)',
  '氛围': 'linear-gradient(135deg,#ede9fe,#a78bfa)',
  '字幕板': 'linear-gradient(135deg,#fef9c3,#fde047)',
}
const EMOJI: Record<string, string> = { '人物': '🧴', '产品': '✨', '场景': '🌿', '氛围': '🫙', '字幕板': '📝' }

interface Props {
  material: Material
  aspectRatio?: string
}

export default function MaterialCard({ material, aspectRatio = '16/9' }: Props) {
  return (
    <div className={styles.card}>
      <div className={styles.thumb} style={{ aspectRatio }}>
        <div className={styles.thumbBg} style={{ background: PALETTE[material.category] ?? '#f1f5f9' }}>
          {EMOJI[material.category] ?? '📁'}
        </div>
        {material.duration > 0 && <div className={styles.dur}>{material.duration}s</div>}
        <div className={styles.stat}><StatusBadge status={material.status} /></div>
      </div>
      <div className={styles.info}>
        <div className={styles.name}>{material.name}</div>
        <div className={styles.meta}>
          <span>{material.category}</span>
          <span>{(material.fileSize / 1_000_000).toFixed(1)} MB</span>
        </div>
      </div>
    </div>
  )
}
```

```css
/* src/components/material/MaterialCard.module.css */
.card { background: #fff; border-radius: 10px; border: 1px solid #e2e8f0; overflow: hidden; cursor: pointer; transition: transform 180ms ease, box-shadow 180ms ease; }
.card:hover { transform: translateY(-2px); box-shadow: 0 10px 15px -3px rgba(0,0,0,.08); border-color: #cbd5e1; }
.thumb { position: relative; overflow: hidden; }
.thumbBg { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; font-size: 28px; }
.dur { position: absolute; bottom: 5px; right: 5px; background: rgba(0,0,0,.6); color: #fff; font-size: 10px; padding: 1px 5px; border-radius: 3px; }
.stat { position: absolute; top: 5px; left: 5px; }
.info { padding: 8px 10px; }
.name { font-size: 12px; font-weight: 500; color: #0f172a; line-height: 1.4; margin-bottom: 3px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.meta { font-size: 11px; color: #94a3b8; display: flex; justify-content: space-between; }
```

- [ ] **Step 2: UploadCard**

```tsx
// src/components/material/UploadCard.tsx
import styles from './UploadCard.module.css'

export default function UploadCard({ onClick }: { onClick?: () => void }) {
  return (
    <div className={styles.card} onClick={onClick}>
      <span className={styles.icon}>⊕</span>
      <span className={styles.label}>拖拽或点击上传</span>
    </div>
  )
}
```

```css
/* src/components/material/UploadCard.module.css */
.card { background: #fff; border-radius: 10px; border: 2px dashed #e2e8f0; display: flex; flex-direction: column; align-items: center; justify-content: center; cursor: pointer; transition: border-color 180ms, background 180ms; aspect-ratio: 16/9; gap: 5px; color: #94a3b8; }
.card:hover { border-color: #2563eb; background: #eff6ff; color: #2563eb; }
.icon { font-size: 20px; }
.label { font-size: 12px; font-weight: 500; }
```

- [ ] **Step 3: AudioCard**

```tsx
// src/components/material/AudioCard.tsx
import styles from './AudioCard.module.css'
import type { MaterialStatus } from '../../types'

interface Props {
  id: string
  name: string
  category: string
  audioDuration: string
  fileSize: number
  status: MaterialStatus
}

// Randomish bar heights for waveform visual
const BARS = [40, 70, 55, 85, 45, 65, 90, 50, 75, 35, 60, 80]

export default function AudioCard({ name, category, audioDuration, fileSize }: Props) {
  return (
    <div className={styles.card}>
      <div className={styles.wave}>
        {BARS.map((h, i) => (
          <div key={i} className={styles.bar} style={{ height: `${h}%` }} />
        ))}
      </div>
      <div className={styles.info}>
        <div className={styles.name}>{name}</div>
        <div className={styles.meta}>
          <span>{category}</span>
          <span>{audioDuration}</span>
          <span>{(fileSize / 1_000_000).toFixed(1)} MB</span>
        </div>
      </div>
      <button className={styles.play}>▶</button>
    </div>
  )
}
```

```css
/* src/components/material/AudioCard.module.css */
.card { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px 14px; display: flex; align-items: center; gap: 12px; cursor: pointer; transition: box-shadow 160ms; }
.card:hover { box-shadow: 0 4px 6px -1px rgba(0,0,0,.08); }
.wave { display: flex; gap: 3px; align-items: flex-end; height: 32px; width: 80px; flex-shrink: 0; }
.bar { width: 4px; background: #dbeafe; border-radius: 2px; transition: background 150ms; }
.card:hover .bar { background: #2563eb; }
.info { flex: 1; min-width: 0; }
.name { font-size: 13px; font-weight: 500; color: #0f172a; margin-bottom: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.meta { font-size: 11px; color: #94a3b8; display: flex; gap: 10px; }
.play { width: 30px; height: 30px; border-radius: 50%; background: #eff6ff; border: 1px solid #dbeafe; color: #2563eb; font-size: 11px; cursor: pointer; flex-shrink: 0; transition: background 150ms; }
.play:hover { background: #dbeafe; }
```

- [ ] **Step 4: VideoLibrary + ImageLibrary + AudioLibrary**

```tsx
// src/components/material/VideoLibrary.tsx
import { useMaterialStore } from '../../stores/materialStore'
import FilterChips from '../common/FilterChips'
import DateGroup from '../common/DateGroup'
import MaterialCard from './MaterialCard'
import UploadCard from './UploadCard'
import styles from './Library.module.css'

const CATEGORIES = ['全部', '人物', '产品', '场景', '氛围', '字幕板']

function groupByDate(materials: ReturnType<typeof useMaterialStore.getState>['materials']) {
  const groups: Record<string, typeof materials> = {}
  for (const m of materials) {
    const d = m.createdAt.split('T')[0]
    const label = d === new Date().toISOString().split('T')[0] ? '今天' : d
    if (!groups[label]) groups[label] = []
    groups[label].push(m)
  }
  return groups
}

export default function VideoLibrary() {
  const { activeCategory, setCategory, filteredMaterials } = useMaterialStore()
  const materials = filteredMaterials()
  const groups = groupByDate(materials)

  return (
    <div className={styles.layout}>
      <div className={styles.topBar}>
        <FilterChips options={CATEGORIES} active={activeCategory} onChange={(v) => setCategory(v as any)} />
        <div className={styles.spacer} />
        <button className={styles.uploadBtn}>↑ 上传视频</button>
      </div>
      <div className={styles.grid}>
        {Object.entries(groups).map(([label, items]) => (
          <DateGroup key={label} label={label}>
            <div className={styles.cardGrid}>
              {label === Object.keys(groups)[0] && <UploadCard />}
              {items.map((m) => <MaterialCard key={m.id} material={m} />)}
            </div>
          </DateGroup>
        ))}
      </div>
    </div>
  )
}
```

```tsx
// src/components/material/ImageLibrary.tsx
import { useMaterialStore } from '../../stores/materialStore'
import FilterChips from '../common/FilterChips'
import DateGroup from '../common/DateGroup'
import MaterialCard from './MaterialCard'
import UploadCard from './UploadCard'
import styles from './Library.module.css'

const CATEGORIES = ['全部', '产品图', '背景图', '字幕板']

export default function ImageLibrary() {
  const { filteredMaterials } = useMaterialStore()
  const materials = filteredMaterials()
  return (
    <div className={styles.layout}>
      <div className={styles.topBar}>
        <FilterChips options={CATEGORIES} active="全部" onChange={() => {}} />
        <div className={styles.spacer} />
        <button className={styles.uploadBtn}>↑ 上传图片</button>
      </div>
      <div className={styles.grid}>
        <DateGroup label="今天">
          <div className={styles.cardGrid}>
            <UploadCard />
            {materials.map((m) => <MaterialCard key={m.id} material={m} aspectRatio="1/1" />)}
          </div>
        </DateGroup>
      </div>
    </div>
  )
}
```

```tsx
// src/components/material/AudioLibrary.tsx
import { useMaterialStore } from '../../stores/materialStore'
import FilterChips from '../common/FilterChips'
import DateGroup from '../common/DateGroup'
import AudioCard from './AudioCard'
import styles from './Library.module.css'

const CATEGORIES = ['全部', 'BGM', '音效']

export default function AudioLibrary() {
  const { audioMaterials } = useMaterialStore()
  return (
    <div className={styles.layout}>
      <div className={styles.topBar}>
        <FilterChips options={CATEGORIES} active="全部" onChange={() => {}} />
        <div className={styles.spacer} />
        <button className={styles.uploadBtn}>↑ 上传音频</button>
      </div>
      <div className={styles.grid}>
        <DateGroup label="今天">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {audioMaterials.map((a) => (
              <AudioCard key={a.id} {...a} status={a.status} />
            ))}
          </div>
        </DateGroup>
      </div>
    </div>
  )
}
```

```css
/* src/components/material/Library.module.css */
.layout { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.topBar { padding: 14px 22px; background: #fff; border-bottom: 1px solid #e2e8f0; display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap; }
.spacer { flex: 1; }
.uploadBtn { padding: 6px 14px; background: #2563eb; color: #fff; border: none; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit; white-space: nowrap; transition: background 150ms; }
.uploadBtn:hover { background: #1d4ed8; }
.grid { flex: 1; overflow-y: auto; padding: 20px 22px; }
.cardGrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(175px, 1fr)); gap: 12px; }
```

- [ ] **Step 5: MaterialTab**

```tsx
// src/components/material/MaterialTab.tsx
import { useMaterialStore } from '../../stores/materialStore'
import type { MaterialType } from '../../types'
import VideoLibrary from './VideoLibrary'
import ImageLibrary from './ImageLibrary'
import AudioLibrary from './AudioLibrary'
import styles from './MaterialTab.module.css'

const SUB_TABS: { key: MaterialType; label: string }[] = [
  { key: 'video', label: '视频' },
  { key: 'image', label: '图片' },
  { key: 'audio', label: '音频' },
]

const LIB_MAP = { video: VideoLibrary, image: ImageLibrary, audio: AudioLibrary }

export default function MaterialTab() {
  const { activeSubTab, setSubTab } = useMaterialStore()
  const Lib = LIB_MAP[activeSubTab]
  return (
    <div className={styles.tab}>
      <div className={styles.subBar}>
        {SUB_TABS.map((t) => (
          <button
            key={t.key}
            className={`${styles.stb} ${activeSubTab === t.key ? styles.active : ''}`}
            onClick={() => setSubTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className={styles.body}><Lib /></div>
    </div>
  )
}
```

```css
/* src/components/material/MaterialTab.module.css */
.tab { display: flex; flex-direction: column; height: 100%; width: 100%; overflow: hidden; }
.subBar { display: flex; padding: 0 22px; background: #fff; border-bottom: 1px solid #e2e8f0; flex-shrink: 0; }
.stb { padding: 10px 16px; border: none; border-bottom: 2px solid transparent; background: transparent; font-size: 13px; font-weight: 500; color: #475569; cursor: pointer; font-family: inherit; transition: color 150ms, border-color 150ms; margin-bottom: -1px; }
.stb:hover { color: #0f172a; }
.stb.active { color: #2563eb; border-bottom-color: #2563eb; font-weight: 600; }
.body { flex: 1; overflow: hidden; display: flex; }
```

- [ ] **Step 6: 运行验证**

切换到素材库 Tab，三个子 tab（视频/图片/音频）均可正常切换，卡片、筛选、日期分组展示正确。

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: material library tab — video, image, audio"
```

---

## Task 8：Creative Tab（成片视频 + 字幕文件）

**Files:**
- Create: `src/components/creative/CreativeCard.tsx`
- Create: `src/components/creative/CreativeCard.module.css`
- Create: `src/components/creative/SrtCard.tsx`
- Create: `src/components/creative/SrtCard.module.css`
- Create: `src/components/creative/CreativeVideoLibrary.tsx`
- Create: `src/components/creative/SrtLibrary.tsx`
- Modify: `src/components/creative/CreativeTab.tsx`

- [ ] **Step 1: CreativeCard（竖版卡片）**

参考 demo.html `.ccard`、`.cthumb`（9/16 比例）。

```tsx
// src/components/creative/CreativeCard.tsx
import type { Creative } from '../../types'
import styles from './CreativeCard.module.css'

const GRADIENTS = [
  'linear-gradient(160deg,#fde68a,#f59e0b,#ef4444)',
  'linear-gradient(160deg,#a7f3d0,#059669,#064e3b)',
  'linear-gradient(160deg,#bfdbfe,#3b82f6,#1e3a8a)',
  'linear-gradient(160deg,#ede9fe,#8b5cf6,#4c1d95)',
  'linear-gradient(160deg,#fce7f3,#ec4899,#9d174d)',
]

const STATUS_MAP = {
  ACTIVE:  { label: '投放中', bg: '#d1fae5', color: '#059669' },
  PENDING: { label: '待上架', bg: '#f1f5f9', color: '#475569' },
  DRAFT:   { label: '草稿',   bg: '#f1f5f9', color: '#475569' },
}

interface Props {
  creative: Creative
  index: number
}

export default function CreativeCard({ creative, index }: Props) {
  const s = STATUS_MAP[creative.status]
  const date = new Date(creative.createdAt)
  const dateStr = `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  return (
    <div className={styles.card}>
      <div className={styles.thumb} style={{ background: GRADIENTS[index % GRADIENTS.length] }}>
        <div className={styles.overlay}><div className={styles.play}>▶</div></div>
        <div className={styles.dur}>{creative.duration}s</div>
      </div>
      <div className={styles.info}>
        <div className={styles.name}>{creative.name}</div>
        <div className={styles.meta}>
          <span>{dateStr}</span>
          <span className={styles.badge} style={{ background: s.bg, color: s.color }}>{s.label}</span>
        </div>
      </div>
    </div>
  )
}
```

```css
/* src/components/creative/CreativeCard.module.css */
.card { background: #fff; border-radius: 10px; border: 1px solid #e2e8f0; overflow: hidden; cursor: pointer; transition: transform 180ms ease, box-shadow 180ms ease; }
.card:hover { transform: translateY(-2px); box-shadow: 0 10px 15px -3px rgba(0,0,0,.08); }
.thumb { aspect-ratio: 9/16; position: relative; overflow: hidden; }
.overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: rgba(0,0,0,0); transition: background 200ms; }
.card:hover .overlay { background: rgba(0,0,0,.22); }
.play { width: 34px; height: 34px; background: rgba(255,255,255,.9); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 13px; opacity: 0; transition: opacity 200ms; }
.card:hover .play { opacity: 1; }
.dur { position: absolute; bottom: 5px; right: 5px; background: rgba(0,0,0,.6); color: #fff; font-size: 10px; padding: 1px 5px; border-radius: 3px; }
.info { padding: 8px 10px; }
.name { font-size: 12px; font-weight: 500; color: #0f172a; margin-bottom: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.meta { font-size: 11px; color: #94a3b8; display: flex; justify-content: space-between; align-items: center; }
.badge { font-size: 10px; padding: 1px 5px; border-radius: 3px; font-weight: 600; }
```

- [ ] **Step 2: SrtCard**

```tsx
// src/components/creative/SrtCard.tsx
import type { Creative } from '../../types'
import styles from './SrtCard.module.css'

export default function SrtCard({ creative }: { creative: Creative }) {
  const date = new Date(creative.createdAt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  return (
    <div className={styles.card}>
      <div className={styles.icon}>SRT</div>
      <div className={styles.info}>
        <div className={styles.name}>{creative.name}.srt</div>
        <div className={styles.meta}>{creative.duration}s · {creative.srtLineCount ?? 0} 条字幕 · {date}</div>
      </div>
      <button className={styles.dl}>↓ 下载</button>
    </div>
  )
}
```

```css
/* src/components/creative/SrtCard.module.css */
.card { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px 14px; display: flex; align-items: center; gap: 12px; transition: box-shadow 160ms; }
.card:hover { box-shadow: 0 4px 6px -1px rgba(0,0,0,.08); }
.icon { width: 36px; height: 36px; background: #eef2ff; border-radius: 7px; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; color: #6366f1; flex-shrink: 0; }
.info { flex: 1; min-width: 0; }
.name { font-size: 13px; font-weight: 500; color: #0f172a; margin-bottom: 2px; }
.meta { font-size: 11px; color: #94a3b8; }
.dl { padding: 5px 12px; background: #eff6ff; border: 1px solid #dbeafe; color: #2563eb; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; font-family: inherit; white-space: nowrap; transition: background 150ms; }
.dl:hover { background: #dbeafe; }
```

- [ ] **Step 3: CreativeVideoLibrary + SrtLibrary**

```tsx
// src/components/creative/CreativeVideoLibrary.tsx
import { useCreativeStore } from '../../stores/creativeStore'
import FilterChips from '../common/FilterChips'
import DateGroup from '../common/DateGroup'
import CreativeCard from './CreativeCard'
import styles from './CreativeLibrary.module.css'

const STATUS_OPTIONS = ['全部', '投放中', '待上架', '草稿']

function groupByDate(creatives: ReturnType<typeof useCreativeStore.getState>['creatives']) {
  const groups: Record<string, { creative: typeof creatives[0]; idx: number }[]> = {}
  creatives.forEach((c, idx) => {
    const d = c.createdAt.split('T')[0]
    const today = new Date().toISOString().split('T')[0]
    const label = d === today ? '今天' : d
    if (!groups[label]) groups[label] = []
    groups[label].push({ creative: c, idx })
  })
  return groups
}

export default function CreativeVideoLibrary() {
  const { filteredCreatives, activeStatus, setStatus } = useCreativeStore()
  const creatives = filteredCreatives()
  const groups = groupByDate(creatives)
  return (
    <div className={styles.layout}>
      <div className={styles.topBar}>
        <FilterChips options={STATUS_OPTIONS} active={activeStatus === '全部' ? '全部' : { ACTIVE: '投放中', PENDING: '待上架', DRAFT: '草稿' }[activeStatus] ?? '全部'} onChange={(v) => setStatus(v as any)} />
      </div>
      <div className={styles.grid}>
        {Object.entries(groups).map(([label, items]) => (
          <DateGroup key={label} label={label}>
            <div className={styles.cardGrid}>
              {items.map(({ creative, idx }) => <CreativeCard key={creative.id} creative={creative} index={idx} />)}
            </div>
          </DateGroup>
        ))}
      </div>
    </div>
  )
}
```

```tsx
// src/components/creative/SrtLibrary.tsx
import { useCreativeStore } from '../../stores/creativeStore'
import DateGroup from '../common/DateGroup'
import SrtCard from './SrtCard'
import styles from './CreativeLibrary.module.css'

function groupByDate(creatives: ReturnType<typeof useCreativeStore.getState>['creatives']) {
  const groups: Record<string, typeof creatives> = {}
  creatives.forEach((c) => {
    const d = c.createdAt.split('T')[0]
    const today = new Date().toISOString().split('T')[0]
    const label = d === today ? '今天' : d
    if (!groups[label]) groups[label] = []
    groups[label].push(c)
  })
  return groups
}

export default function SrtLibrary() {
  const { creatives } = useCreativeStore()
  const groups = groupByDate(creatives.filter((c) => c.srtUrl !== undefined))
  return (
    <div className={styles.layout}>
      <div className={styles.topBar} />
      <div className={styles.grid}>
        {Object.entries(groups).map(([label, items]) => (
          <DateGroup key={label} label={label}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {items.map((c) => <SrtCard key={c.id} creative={c} />)}
            </div>
          </DateGroup>
        ))}
      </div>
    </div>
  )
}
```

```css
/* src/components/creative/CreativeLibrary.module.css */
.layout { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.topBar { padding: 14px 22px; background: #fff; border-bottom: 1px solid #e2e8f0; display: flex; align-items: center; gap: 10px; flex-shrink: 0; min-height: 54px; }
.grid { flex: 1; overflow-y: auto; padding: 20px 22px; }
.cardGrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }
```

- [ ] **Step 4: CreativeTab**

```tsx
// src/components/creative/CreativeTab.tsx
import { useCreativeStore } from '../../stores/creativeStore'
import CreativeVideoLibrary from './CreativeVideoLibrary'
import SrtLibrary from './SrtLibrary'
import styles from './CreativeTab.module.css'

const SUB_TABS = [
  { key: 'video' as const, label: '成片视频' },
  { key: 'srt'   as const, label: '字幕文件' },
]

export default function CreativeTab() {
  const { activeSubTab, setSubTab } = useCreativeStore()
  return (
    <div className={styles.tab}>
      <div className={styles.subBar}>
        {SUB_TABS.map((t) => (
          <button
            key={t.key}
            className={`${styles.stb} ${activeSubTab === t.key ? styles.active : ''}`}
            onClick={() => setSubTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className={styles.body}>
        {activeSubTab === 'video' ? <CreativeVideoLibrary /> : <SrtLibrary />}
      </div>
    </div>
  )
}
```

```css
/* src/components/creative/CreativeTab.module.css */
.tab { display: flex; flex-direction: column; height: 100%; width: 100%; overflow: hidden; }
.subBar { display: flex; padding: 0 22px; background: #fff; border-bottom: 1px solid #e2e8f0; flex-shrink: 0; }
.stb { padding: 10px 16px; border: none; border-bottom: 2px solid transparent; background: transparent; font-size: 13px; font-weight: 500; color: #475569; cursor: pointer; font-family: inherit; transition: color 150ms, border-color 150ms; margin-bottom: -1px; }
.stb:hover { color: #0f172a; }
.stb.active { color: #2563eb; border-bottom-color: #2563eb; font-weight: 600; }
.body { flex: 1; overflow: hidden; display: flex; }
```

- [ ] **Step 5: 运行验证**

```bash
npm run dev
```

期望：成片库 Tab 可正常切换成片视频/字幕文件，卡片按日期分组展示，hover 效果正确。

- [ ] **Step 6: 最终全流程验证**

手动走一遍：
1. 生成 Tab → 模拟上传 → 脚本选择 → 素材匹配 → 确认成片 → 占位页面
2. 素材库 → 视频/图片/音频 三个子 tab
3. 成片库 → 成片视频/字幕 两个子 tab

无控制台报错，布局与 demo.html 一致。

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: creative library tab — videos, srt files"
```

---

## 自查清单

- [x] 所有步骤包含完整代码，无 TBD/TODO
- [x] 类型定义（types/index.ts）与各组件使用保持一致
- [x] mock 数据覆盖三种 type（video/image/audio）
- [x] 无依赖未定义的函数或类型
- [x] 每个 Task 均有独立 commit
- [x] demo.html 是视觉 ground truth，所有颜色/间距均对齐
