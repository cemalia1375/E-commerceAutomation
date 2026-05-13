# SimpleClaw 架构讲解网站 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成一个单 HTML 文件的交互式架构讲解网站，展示 SimpleClaw 框架核心模块和 Mojing 应用集成方式。

**Architecture:** 单 HTML 文件，SVG 绘制三层架构图 + 三个 CSS 流程图。使用嵌入式 CSS/JS，零外部依赖。采用由顶到底的渐进式构建——先骨架再填充内容。

**Tech Stack:** HTML5, CSS3 (flexbox/grid), SVG, Vanilla JS

**File:** `flowcut_backend/SimpleClaw/architecture.html`

---

### Task 1: HTML 骨架 + 全局 CSS 框架

**Files:**
- Create: `flowcut_backend/SimpleClaw/architecture.html`

- [ ] **Step 1: 创建 HTML 骨架**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SimpleClaw 架构详解</title>
<style>
/* 全局重置 */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans SC", sans-serif; background: #f8f9fa; color: #333; line-height: 1.6; }

/* 导航 */
.nav-sidebar { position: fixed; right: 20px; top: 50%; transform: translateY(-50%); z-index: 100; display: flex; flex-direction: column; gap: 8px; }
.nav-dot { width: 12px; height: 12px; border-radius: 50%; background: #ccc; border: 2px solid #fff; cursor: pointer; transition: all .2s; }
.nav-dot:hover, .nav-dot.active { background: #1976d2; transform: scale(1.3); }

/* 区块通用 */
.section { min-height: 100vh; padding: 60px 40px; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.section-title { font-size: 28px; font-weight: 700; margin-bottom: 8px; }
.section-subtitle { color: #666; margin-bottom: 32px; font-size: 15px; }
.container { max-width: 1100px; width: 100%; }

/* 模块卡片 - 总览图用 */
.module-layer { width: 100%; border-radius: 12px; padding: 16px; margin-bottom: 8px; }
.layer-sc { background: #e3f2fd; border: 2px solid #1565c0; }
.layer-mj { background: #fce4ec; border: 2px solid #c62828; }
.layer-ext { background: #e8f5e9; border: 2px solid #2e7d32; }
.layer-title { font-weight: 700; font-size: 15px; margin-bottom: 8px; }
.module-grid { display: flex; flex-wrap: wrap; gap: 6px; }
.module-btn { background: #fff; border: 1px solid currentColor; border-radius: 8px; padding: 8px 14px; font-size: 13px; cursor: pointer; transition: all .15s; text-align: left; flex: 1; min-width: 130px; }
.module-btn:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,.1); }
.module-btn .mname { font-weight: 600; display: block; }
.module-btn .mdesc { color: #888; font-size: 11px; }
.mb-sc { color: #1565c0; }
.mb-mj { color: #c62828; }
.mb-ext { color: #2e7d32; }
.arrow-down { text-align: center; font-size: 22px; color: #999; line-height: 1; margin: 2px 0; }

/* 流程图步进 */
.flow-step { background: #fff; border: 2px solid #e0e0e0; border-radius: 10px; padding: 14px 18px; margin-bottom: 6px; }
.flow-step .step-label { font-weight: 600; font-size: 14px; margin-bottom: 4px; }
.flow-step .step-detail { font-size: 13px; color: #555; line-height: 1.7; }
.flow-connector { text-align: center; font-size: 18px; color: #999; line-height: 1; margin: 2px 0; }

/* 详情弹窗（Modal） */
.modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 200; justify-content: center; align-items: center; }
.modal-overlay.open { display: flex; }
.modal { background: #fff; border-radius: 16px; padding: 28px 32px; max-width: 520px; width: 90%; max-height: 80vh; overflow-y: auto; position: relative; }
.modal h3 { font-size: 20px; margin-bottom: 6px; }
.modal .modal-path { color: #888; font-size: 12px; font-family: monospace; margin-bottom: 14px; }
.modal .modal-deps { margin-top: 14px; padding-top: 14px; border-top: 1px solid #eee; }
.modal .modal-deps strong { font-size: 13px; }
.modal .modal-deps ul { margin-top: 4px; padding-left: 18px; font-size: 13px; color: #555; }
.modal-close { position: absolute; top: 16px; right: 20px; font-size: 24px; cursor: pointer; color: #999; background: none; border: none; }
.modal-close:hover { color: #333; }

/* 响应式 */
@media (max-width: 768px) {
  .section { padding: 40px 20px; }
  .nav-sidebar { right: 10px; }
  .module-btn { min-width: 100px; }
}
</style>
</head>
<body>

<nav class="nav-sidebar" id="navSidebar">
  <a class="nav-dot active" href="#overview" title="系统总览"></a>
  <a class="nav-dot" href="#flow-request" title="请求处理链路"></a>
  <a class="nav-dot" href="#flow-worker" title="后台 Worker"></a>
  <a class="nav-dot" href="#flow-subagent" title="Subagent 调度"></a>
</nav>

<div class="modal-overlay" id="modalOverlay"><div class="modal" id="modalContent"></div></div>

<script>
// Modal helper
const overlay = document.getElementById('modalOverlay');
const modal = document.getElementById('modalContent');
function showModal(html) { modal.innerHTML = html; overlay.classList.add('open'); }
overlay.addEventListener('click', e => { if (e.target === overlay) overlay.classList.remove('open'); });

// Nav dot active tracking
const sections = document.querySelectorAll('.section');
const dots = document.querySelectorAll('.nav-dot');
const observer = new IntersectionObserver(entries => {
  entries.forEach(e => { if (e.isIntersecting) { dots.forEach(d => d.classList.toggle('active', d.getAttribute('href') === '#' + e.target.id)); } });
}, { threshold: .5 });
sections.forEach(s => observer.observe(s));
</script>
</body>
</html>
```

- [ ] **Step 2: 验证骨架结构正确**

Run: `cat flowcut_backend/SimpleClaw/architecture.html | head -5`
Expected: DOCTYPE html，style 标签存在

---

### Task 2: 第一屏 — 系统总览图（SVG 三层架构）

**Files:**
- Modify: `flowcut_backend/SimpleClaw/architecture.html`（在 `<nav>` 后插入第一个 section）

- [ ] **Step 1: 添加总览图 Section（SVG 架构图 + 可点击模块按钮）**

在 `<nav>` 后面、`<script>` 前面插入：

```html
<section class="section" id="overview">
  <div class="container">
    <h2 class="section-title">SimpleClaw 系统架构总览</h2>
    <p class="section-subtitle">三层架构：框架层 → 应用层 → 外部依赖</p>

    <div class="module-layer layer-sc">
      <div class="layer-title">🔷 SimpleClaw 框架层</div>
      <div class="module-grid">
        <div class="module-btn mb-sc" onclick='showModal(moduleDetails["reactloop"])'><span class="mname">ReactLoop</span><span class="mdesc">ReAct 执行引擎</span></div>
        <div class="module-btn mb-sc" onclick='showModal(moduleDetails["context"])'><span class="mname">ContextBuilder</span><span class="mdesc">系统提示组装</span></div>
        <div class="module-btn mb-sc" onclick='showModal(moduleDetails["llm"])'><span class="mname">LLMProvider</span><span class="mdesc">模型调用抽象</span></div>
        <div class="module-btn mb-sc" onclick='showModal(moduleDetails["toolreg"])'><span class="mname">ToolRegistry</span><span class="mdesc">工具注册/执行</span></div>
        <div class="module-btn mb-sc" onclick='showModal(moduleDetails["memory"])'><span class="mname">Memory</span><span class="mdesc">会话/长期记忆</span></div>
        <div class="module-btn mb-sc" onclick='showModal(moduleDetails["runtime"])'><span class="mname">Runtime</span><span class="mdesc">任务队列/服务</span></div>
        <div class="module-btn mb-sc" onclick='showModal(moduleDetails["lifecycle"])'><span class="mname">ToolLifecycle</span><span class="mdesc">预执行 Hook 链</span></div>
        <div class="module-btn mb-sc" onclick='showModal(moduleDetails["compressor"])'><span class="mname">Compressor</span><span class="mdesc">上下文压缩</span></div>
      </div>
    </div>

    <div class="arrow-down">⬇️</div>

    <div class="module-layer layer-mj">
      <div class="layer-title">🔴 Mojing 应用层</div>
      <div class="module-grid">
        <div class="module-btn mb-mj" onclick='showModal(moduleDetails["api"])'><span class="mname">FastAPI</span><span class="mdesc">API Server</span></div>
        <div class="module-btn mb-mj" onclick='showModal(moduleDetails["container"])'><span class="mname">AppContainer</span><span class="mdesc">DI 组装</span></div>
        <div class="module-btn mb-mj" onclick='showModal(moduleDetails["mainagent"])'><span class="mname">MainAgent</span><span class="mdesc">Context/Tool 装配</span></div>
        <div class="module-btn mb-mj" onclick='showModal(moduleDetails["sessionstore"])'><span class="mname">SessionStore</span><span class="mdesc">会话管理</span></div>
        <div class="module-btn mb-mj" onclick='showModal(moduleDetails["subagentstore"])'><span class="mname">SubagentStore</span><span class="mdesc">子代理调度</span></div>
        <div class="module-btn mb-mj" onclick='showModal(moduleDetails["tools"])'><span class="mname">Mojing Tools</span><span class="mdesc">业务工具</span></div>
        <div class="module-btn mb-mj" onclick='showModal(moduleDetails["providers"])'><span class="mname">Context Providers</span><span class="mdesc">动态上下文</span></div>
        <div class="module-btn mb-mj" onclick='showModal(moduleDetails["subagents"])'><span class="mname">Subagents</span><span class="mdesc">子代理实例</span></div>
        <div class="module-btn mb-mj" onclick='showModal(moduleDetails["repos"])'><span class="mname">Repos</span><span class="mdesc">MySQL 仓储</span></div>
        <div class="module-btn mb-mj" onclick='showModal(moduleDetails["workers"])'><span class="mname">Workers</span><span class="mdesc">后台 Worker</span></div>
        <div class="module-btn mb-mj" onclick='showModal(moduleDetails["gates"])'><span class="mname">Tool Gates</span><span class="mdesc">预执行门控</span></div>
        <div class="module-btn mb-mj" onclick='showModal(moduleDetails["journey"])'><span class="mname">Journey</span><span class="mdesc">阶段策略</span></div>
      </div>
    </div>

    <div class="arrow-down">⬇️</div>

    <div class="module-layer layer-ext">
      <div class="layer-title">🟢 外部依赖</div>
      <div class="module-grid">
        <div class="module-btn mb-ext" onclick='showModal(moduleDetails["mysql"])'><span class="mname">MySQL</span><span class="mdesc">数据持久化</span></div>
        <div class="module-btn mb-ext" onclick='showModal(moduleDetails["volc"])'><span class="mname">Volcengine LLM</span><span class="mdesc">Doubao 模型</span></div>
        <div class="module-btn mb-ext" onclick='showModal(moduleDetails["imageapi"])'><span class="mname">Image API</span><span class="mdesc">图片分析/裁剪</span></div>
        <div class="module-btn mb-ext" onclick='showModal(moduleDetails["deepresearch"])'><span class="mname">Deep Research</span><span class="mdesc">深度研究 API</span></div>
        <div class="module-btn mb-ext" onclick='showModal(moduleDetails["device"])'><span class="mname">Device API</span><span class="mdesc">硬件设备命令</span></div>
      </div>
    </div>

  </div>
</section>
```

- [ ] **Step 2: 添加模块详情数据（JavaScript）**

在已有 `<script>` 标签末尾、`</script>` 前插入：

```js
const moduleDetails = {
  reactloop: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>ReactLoop</h3>
    <div class="modal-path">simpleclaw/core/loop.py</div>
    <p>Agent 的核心 ReAct 执行引擎，采用 <b>思考→行动→观察</b> 循环：LLM 生成文本和工具调用 → 执行耦合工具 → 结果注入历史 → 继续循环直到完成。</p>
    <div class="modal-deps"><strong>依赖</strong><ul>
      <li>LLMProvider — 模型调用</li>
      <li>ToolRegistry — 工具执行</li>
      <li>ContextBuilder — 系统提示词</li>
      <li>ContextCompressor — 上下文压缩</li>
    </ul></div>`,
  
  context: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>ContextBuilder</h3>
    <div class="modal-path">simpleclaw/context/builder.py</div>
    <p>将系统提示词拆分为 <b>稳定前缀</b>（Agent/SOUL/TOOL/compliance）+ <b>动态尾部</b>（记忆/文档/attention），并为 LLM 调用注入 prefix-cache 元数据。</p>
    <div class="modal-deps"><strong>依赖</strong><ul>
      <li>StablePromptProvider — 稳定提示段</li>
      <li>DynamicContextProvider — 动态上下文</li>
      <li>AttentionProvider — 注意力包</li>
    </ul></div>`,

  llm: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>LLMProvider</h3>
    <div class="modal-path">simpleclaw/llm/base.py + volcengine.py</div>
    <p>LLM 调用抽象层。VolcengineLLM 实现了流式调用、自动重试和 Prefix Cache。支持 Pro（主/子 Agent）和 Lite（后处理/冷路径）双模型。</p>
    <div class="modal-deps"><strong>依赖</strong><ul>
      <li>Volcengine API</li>
      <li>LLMCacheRepository — 缓存</li>
    </ul></div>`,

  toolreg: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>ToolRegistry</h3>
    <div class="modal-path">simpleclaw/tools/registry.py</div>
    <p>工具的注册中心和执行网关。管理工具的 schema 生成、耦合/解耦标记、执行调度。支持动态注册和上下文感知工具工厂。</p>
    <div class="modal-deps"><strong>依赖</strong><ul>
      <li>Tool — 工具接口</li>
      <li>RuntimeServices — 后台任务提交</li>
      <li>ToolLifecycle — 预执行钩子</li>
    </ul></div>`,

  memory: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Memory</h3>
    <div class="modal-path">simpleclaw/memory/</div>
    <p>会话记忆和长期记忆管理层。支持 MySQL 持久化的记忆存储，通过 MemoryDynamicContextProvider 注入到 ContextBuilder 的动态部分。</p>
    <div class="modal-deps"><strong>依赖</strong><ul>
      <li>MySQL — 记忆持久化</li>
      <li>ContextBuilder — 上下文注入</li>
    </ul></div>`,

  runtime: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Runtime</h3>
    <div class="modal-path">simpleclaw/runtime/</div>
    <p>后台任务服务体系。包含 TaskQueue（Redis/InMemory）、ScopeLockRegistry（并发控制）、TaskStateStore（状态跟踪）。</p>
    <div class="modal-deps"><strong>依赖</strong><ul>
      <li>Redis 或 InMemory 队列</li>
      <li>MySQL — 任务状态持久化</li>
    </ul></div>`,

  lifecycle: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>ToolLifecycle</h3>
    <div class="modal-path">simpleclaw/harness/lifecycle.py</div>
    <p>工具执行前的生命周期钩子链。BeforeToolHook 可以批准或拒绝工具执行，用于业务前置检查（如 readiness 就绪判断、去重）。</p>
    <div class="modal-deps"><strong>依赖</strong><ul>
      <li>ToolInvocationContext — 调用上下文</li>
      <li>ToolGateDecision — 门控决策</li>
    </ul></div>`,

  compressor: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Compressor</h3>
    <div class="modal-path">simpleclaw/context/compressor.py</div>
    <p>工作窗口压缩器。在 ReactLoop 每轮迭代前检查历史长度，超过阈值时自动压缩以控制 token 消耗。</p>
    <div class="modal-deps"><strong>依赖</strong><ul>
      <li>LLM — 压缩调用</li>
    </ul></div>`,

  api: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>FastAPI Server</h3>
    <div class="modal-path">Mojing/api/server.py</div>
    <p>FastAPI 应用入口。启动时调用 build_container() 完成依赖注入，提供 /agent/chat、/v1/chat/completions、/health、/admin 等路由。</p></div>`,

  container: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>AppContainer</h3>
    <div class="modal-path">Mojing/api/container.py</div>
    <p>应用的依赖注入容器。build_container() 初始化所有存储库、LLM 实例、Agent 装配和后台 Worker 后返回 AppContainer 实例。</p></div>`,

  mainagent: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>MainAgent</h3>
    <div class="modal-path">Mojing/agent/main_agent.py</div>
    <p>主 Agent 的装配层。提供 make_context_builder() 和 make_tool_registry() 两大工厂方法，管理所有 Context Providers、Attention Providers 和工具注册。</p></div>`,

  sessionstore: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>SessionStore</h3>
    <div class="modal-path">Mojing/storage/session_store.py</div>
    <p>会话生命周期管理。冷启动时调用 MainAgent 装配 Context + Tools，热启动时从 MySQL 恢复历史。管理系统提示词缓存和消息持久化。</p></div>`,

  subagentstore: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>SubagentStore</h3>
    <div class="modal-path">Mojing/storage/subagent_store.py</div>
    <p>子代理调度中枢。dispatch() 方法根据任务类型路由到对应 Subagent 实例，管理其独立的 ReactLoop 生命周期和 EventHub 事件发布。</p></div>`,

  tools: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Mojing Tools</h3>
    <div class="modal-path">Mojing/tools/</div>
    <p>业务工具集：AnalyzeImageTool（图片分析）、RetrieveEvidenceTool（证据检索）、CronScheduler 定时工具、DeviceCommandTool（硬件命令）、DeepReportChatTool（深度报告）等。</p></div>`,

  providers: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Context Providers</h3>
    <div class="modal-path">Mojing/context/</div>
    <p>Mojing 自定义的 Context/Attention Provider：DocumentContextProvider（文档注入）、CurrentTimeContextProvider（时间）、TopicAttentionProvider（话题提醒）、EvidenceAttentionProvider（证据感知）等。</p></div>`,

  subagents: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Subagents</h3>
    <div class="modal-path">Mojing/subagent/</div>
    <p>子 Agent 实例：SkinDiarySubagent（皮肤日记生成）、DeepReportSubagent（深度研究报告）。各自拥有独立的 ReactLoop、ContextBuilder 和 ToolRegistry。</p></div>`,

  repos: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Repos</h3>
    <div class="modal-path">Mojing/storage/</div>
    <p>MySQL 数据仓储层：SessionRepository / DocumentRepository / ImageRepository / TopicRepository / MemoryRepository / RuntimeTaskRepository 等 16+ 个仓储。</p></div>`,

  workers: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Workers</h3>
    <div class="modal-path">Mojing/runtime/worker.py</div>
    <p>8 条 TaskWorker 流水线（按 Stream 隔离）：postprocess / topic_tracking / image_analysis / skin_diary / memory_extract / deep_research / subagent_dispatch / background。</p></div>`,

  gates: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Tool Gates</h3>
    <div class="modal-path">Mojing/harness/tool_gates.py</div>
    <p>预执行门控：HistoricalImageGate（历史图片就绪检查）、DeepReportGate（深度报告就绪检查）。附属于 ToolLifecycle 的执行前回调链。</p></div>`,

  journey: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Journey</h3>
    <div class="modal-path">Mojing/journey/</div>
    <p>用户旅程阶段策略：novice → explore → mature。每个阶段有不同的 stable_sections、工具可用性和 Attention Provider 配置。</p></div>`,

  mysql: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>MySQL</h3>
    <div class="modal-path">外部依赖</div>
    <p>核心数据存储：会话 / 记忆 / 文档 / 图片 / 话题 / 皮肤档案 / 深度报告 / 运行时任务 / 工具调用记录。</p></div>`,

  volc: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Volcengine LLM</h3>
    <div class="modal-path">外部依赖</div>
    <p>豆包模型 API：Doubao Pro（主/子 Agent，启用 Prefix Cache）、Doubao Lite（后处理/冷路径/首 token 预测），通过 simpleclaw/llm/volcengine.py 封装。</p></div>`,

  imageapi: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Image API</h3>
    <div class="modal-path">外部依赖</div>
    <p>图片分析服务和皮肤日记裁剪端点。用于 AnalyzeImageTool 的图片分析和 SkinDiarySubagent 的结果裁剪。</p></div>`,

  deepresearch: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Deep Research</h3>
    <div class="modal-path">外部依赖</div>
    <p>深度研究 API 服务。用于 DeepReportSubagent 的研究报告生成。</p></div>`,

  device: `<button class="modal-close" onclick="overlay.classList.remove('open')">×</button>
    <h3>Device API</h3>
    <div class="modal-path">外部依赖</div>
    <p>硬件设备命令 API。用于 DeviceCommandTool，仅在 explore/mature 阶段具备设备上下文时可用。</p></div>`
};
```

- [ ] **Step 3: 验证总览图渲染**

Run: `grep -c 'module-btn' flowcut_backend/SimpleClaw/architecture.html`
Expected: 25（8 个框架层 + 12 个应用层 + 5 个外部依赖）

---

### Task 3: 第二屏 — 请求处理链路流程

**Files:**
- Modify: `flowcut_backend/SimpleClaw/architecture.html`（在 overview section 后插入）

- [ ] **Step 1: 添加请求链路 Section**

```html
<section class="section" id="flow-request">
  <div class="container">
    <h2 class="section-title">请求处理链路</h2>
    <p class="section-subtitle">从用户发消息到收到回复的完整路径</p>

    <div class="flow-step">
      <div class="step-label">👤 ① 用户请求 POST /agent/chat</div>
      <div class="step-detail">小程序/硬件设备发送消息到 FastAPI 端点，携带 session_key、tenant_key、用户输入和可选图片。</div>
    </div>
    <div class="flow-connector">↓</div>

    <div class="flow-step">
      <div class="step-label">📡 ② FastAPI Router 解析</div>
      <div class="step-detail">chat.py 路由解析请求参数，提取 session_key/tenant_key/message，调用 SessionStore.get_or_create()。</div>
    </div>
    <div class="flow-connector">↓</div>

    <div class="flow-step">
      <div class="step-label">🔥 ③ SessionStore 冷/热启动</div>
      <div class="step-detail">冷启动：调用 MainAgent.make_context_builder() + make_tool_registry() 装配 ContextBuilder 和 ToolRegistry。热启动：从 MySQL 恢复消息历史。注入 dynamic_context_sections + attention_packets。</div>
    </div>
    <div class="flow-connector">↓</div>

    <div class="flow-step">
      <div class="step-label">🔄 ④ ReactLoop.run() 执行</div>
      <div class="step-detail">
        a. ContextBuilder.build() → 稳定前缀 + 动态尾部 → system prompt<br>
        b. LLM.stream_with_retry() → 流式获取 TextChunk + ToolCallChunk<br>
        c. 文本 token → TextEvent → SSE 推送给前端<br>
        d. 耦合工具 → 并行执行 → ToolResultEvent → 注入历史 → 回到 b<br>
        e. 解耦工具 → fire-and-forget → 提交后台任务<br>
        f. 无工具调用 → DoneEvent
      </div>
    </div>
    <div class="flow-connector">↓</div>

    <div class="flow-step">
      <div class="step-label">📤 ⑤ SSE 流式响应 → 用户</div>
      <div class="step-detail">通过 Server-Sent Events 逐 token 推送给前端，用户看到实时生成的回复。</div>
    </div>

    <div style="margin-top: 20px; padding: 14px; background: #fff8e1; border: 1px solid #ffe082; border-radius: 10px;">
      <div style="font-size: 13px; color: #666;">
        <b>⚡ 响应完成后异步任务：</b>PostprocessHook（文档后处理）→ ColdPathHook（结构化记忆提取）→ MemoryExtract（长期记忆）→ SkinProfileSync（皮肤档案同步）
      </div>
    </div>
  </div>
</section>
```

- [ ] **Step 2: 验证新 section 存在**

Run: `grep -c 'flow-request' flowcut_backend/SimpleClaw/architecture.html`
Expected: 1

---

### Task 4: 第三屏 — 后台 Worker 体系流程

**Files:**
- Modify: `flowcut_backend/SimpleClaw/architecture.html`（在 flow-request section 后插入）

- [ ] **Step 1: 添加 Worker 体系 Section**

```html
<section class="section" id="flow-worker">
  <div class="container">
    <h2 class="section-title">后台 Worker 体系</h2>
    <p class="section-subtitle">解耦工具的异步执行框架</p>

    <div class="flow-step">
      <div class="step-label">📥 ① 任务来源</div>
      <div class="step-detail">
        ReactLoop 中的 <b>解耦工具调用</b>（needs_followup=false）通过 RuntimeServices.submit_task() 提交任务。<br>
        CronScheduler 定时触发任务到队列。<br>
        TriggeredTaskMonitor 检测状态变更后触发。
      </div>
    </div>
    <div class="flow-connector">↓</div>

    <div class="flow-step">
      <div class="step-label">📋 ② Task Queue</div>
      <div class="step-detail">Redis Stream（配置 REDIS_URL 时）或 InMemoryTaskQueue（本地开发）。支持消费者组和消息确认。<br><span style="color:#888;font-size:12px;">配置位置：Mojing/config.py → make_task_queue()</span></div>
    </div>
    <div class="flow-connector">↓</div>

    <div class="flow-step">
      <div class="step-label">⚙️ ③ TaskWorker × 8（Stream 隔离）</div>
      <div class="step-detail">
        <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px;">
          <span style="background:#e8f5e9;padding:2px 8px;border-radius:4px;font-size:12px;">POSTPROCESS → 后处理</span>
          <span style="background:#e8f5e9;padding:2px 8px;border-radius:4px;font-size:12px;">TOPIC_TRACKING → 冷路径记忆</span>
          <span style="background:#e8f5e9;padding:2px 8px;border-radius:4px;font-size:12px;">IMAGE_ANALYSIS → 图片分析</span>
          <span style="background:#e8f5e9;padding:2px 8px;border-radius:4px;font-size:12px;">SKIN_DIARY → 皮肤日记</span>
          <span style="background:#e8f5e9;padding:2px 8px;border-radius:4px;font-size:12px;">MEMORY_EXTRACT → 记忆提取</span>
          <span style="background:#e8f5e9;padding:2px 8px;border-radius:4px;font-size:12px;">DEEP_RESEARCH → 深度研究</span>
          <span style="background:#e8f5e9;padding:2px 8px;border-radius:4px;font-size:12px;">SUBAGENT_DISPATCH → 子代理分发</span>
          <span style="background:#e8f5e9;padding:2px 8px;border-radius:4px;font-size:12px;">BACKGROUND → 旧版兼容</span>
        </div>
      </div>
    </div>
    <div class="flow-connector">↓</div>

    <div class="flow-step">
      <div class="step-label">🔧 ④ Executor 执行</div>
      <div class="step-detail">
        TaskWorker 消费队列消息 → 按 task_type 路由到对应 Executor。<br>
        <b>ScopeLock</b> 按 (task_id + session_id) 防止并发执行。<br>
        任务结果写入 <b>RuntimeTaskRepository</b>（MySQL 持久化）。<br>
        失败自动重试，超出重试次数记录为失败状态。
      </div>
    </div>

    <div style="margin-top: 20px; padding: 14px; background: #e3f2fd; border: 1px solid #90caf9; border-radius: 10px;">
      <div style="font-size: 13px; color: #1565c0;">
        <b>💡 设计要点：</b>Stream 隔离保证不同类型任务互不阻塞；ScopeLock 防止同一 session 的重复任务；TaskStateStore 提供全链路状态跟踪。
      </div>
    </div>
  </div>
</section>
```

- [ ] **Step 2: 验证**

Run: `grep -c 'flow-worker' flowcut_backend/SimpleClaw/architecture.html`
Expected: 1

---

### Task 5: 第四屏 — Subagent 调度流程

**Files:**
- Modify: `flowcut_backend/SimpleClaw/architecture.html`（在 flow-worker section 后插入）

- [ ] **Step 1: 添加 Subagent 调度 Section**

```html
<section class="section" id="flow-subagent">
  <div class="container">
    <h2 class="section-title">Subagent 调度流程</h2>
    <p class="section-subtitle">主 Agent → 子 Agent → 后处理的完整链路</p>

    <div class="flow-step">
      <div class="step-label">🔵 ① 主 Agent 调用 Subagent 工具</div>
      <div class="step-detail">ReactLoop 中 LLM 选择调用 SkinDiary 或 DeepReport 工具 → 工具执行将任务提交到 <b>SUBAGENT_DISPATCH</b> Stream。</div>
    </div>
    <div class="flow-connector">↓</div>

    <div class="flow-step">
      <div class="step-label">🟠 ② SubagentStore.dispatch()</div>
      <div class="step-detail">
        根据任务类型路由到对应 Subagent 实例：<br>
        • <b>SkinDiarySubagent</b> — 皮肤日记生成，独立 ReactLoop + ContextBuilder + ToolRegistry<br>
        • <b>DeepReportSubagent</b> — 深度研究报告，同上各自独立的记忆和上下文<br>
        子 Agent 共享主 Agent 的合规约束（compliance.md）。
      </div>
    </div>
    <div class="flow-connector">↓</div>

    <div class="flow-step">
      <div class="step-label">🟢 ③ Subagent ReactLoop 执行</div>
      <div class="step-detail">
        子 Agent 独立执行 ReAct 循环，使用轻量化的 context providers。<br>
        执行完成后：<br>
        • 结果写入 MySQL（document_repo / skin_diary_result_repo 等）<br>
        • 提交后处理任务到 POSTPROCESS Stream<br>
        • 通过 EventHub.publish() 发布事件通知
      </div>
    </div>
    <div class="flow-connector">↓</div>

    <div class="flow-step">
      <div class="step-label">🟣 ④ 后处理链路</div>
      <div class="step-detail">
        PostprocessExecutor 消费任务：<br>
        1. <b>PostprocessHook</b> — 文档后处理（格式化/裁剪/校正）<br>
        2. <b>ColdPathHook</b> — 结构化记忆提取（话题跟踪状态更新）<br>
        3. <b>SkinProfileSync</b> — 皮肤档案增量同步<br>
        4. <b>MemoryExtract</b> — 长期记忆提取 → MySQL memory 表
      </div>
    </div>

    <div style="margin-top: 20px; padding: 14px; background: #f3e5f5; border: 1px solid #ce93d8; border-radius: 10px;">
      <div style="font-size: 13px; color: #7b1fa2;">
        <b>💡 设计要点：</b>子 Agent 独立反应周期意味着它们可以并行执行，不阻塞主 Agent 继续处理新消息。后处理全部异步化，通过 EventHub 解耦。
      </div>
    </div>
  </div>
</section>
```

- [ ] **Step 2: 验证**

Run: `grep -c 'flow-subagent' flowcut_backend/SimpleClaw/architecture.html`
Expected: 1

---

### Task 6: 组装、润色与验证

**Files:**
- Modify: `flowcut_backend/SimpleClaw/architecture.html`

- [ ] **Step 1: 确认文件结构完整**
  - 确认所有 section 都闭合
  - 导航 dot 数量 = 4（overview + 3 flows）
  - 确认 `</html>` 在文件末尾

- [ ] **Step 2: 在浏览器中打开确认渲染**

Run: `open flowcut_backend/SimpleClaw/architecture.html`
Expected: 浏览器打开，四个 section 都可见，导航点可点击，模块可点击弹出 modal
