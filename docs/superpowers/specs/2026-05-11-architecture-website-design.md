# SimpleClaw 架构与 Mojing 集成讲解网站 — 设计文档

## 概述

制作一个单 HTML 文件的交互式网站，用于讲解 SimpleClaw Agent 框架的架构以及 Mojing 应用如何集成该框架。目标受众是开发者和技术团队成员。

## 内容范围

- **SimpleClaw 框架层（宏观模块图 A1）**：ReactLoop / ContextBuilder / LLMProvider / ToolRegistry / Memory / Runtime / ToolLifecycle / Compressor
- **Mojing 应用层（中等粒度 B2）**：启动流程（build_container）、请求处理链路、工具注册、后台 Worker 体系、Subagent 调度

## 页面结构

从上到下分为三个区域：

### 1. 系统总览图（第一屏）

SVG 绘制的三层架构交互式总览图：

| 层级 | 颜色 | 包含模块 |
|------|------|----------|
| SimpleClaw 框架层 | 蓝色 | ReactLoop, ContextBuilder, LLMProvider, ToolRegistry, Memory, Runtime, ToolLifecycle, Compressor |
| Mojing 应用层 | 红色 | FastAPI Server, AppContainer, MainAgent, SessionStore, SubagentStore, Mojing Tools, Context Providers, Subagents, Repos, Task Workers, Tool Gates, Journey |
| 外部依赖 | 绿色 | MySQL, Volcengine LLM, Image API, Deep Research API, Device API |

交互方式：点击任一模块弹出详情面板，显示该模块的职责、关键类/接口、依赖关系和代码路径。

### 2. 流程图区域

三个关键流程，每个配有步骤式文字说明：

#### 流程 1：请求处理链路
```
用户 → POST /agent/chat → FastAPI Router →
SessionStore (get_or_create / 冷启动) →
ReactLoop.run (ContextBuilder → LLM.stream → 工具执行 → 循环) →
SSE 流式响应 → 完成 → 异步后处理
```

#### 流程 2：后台 Worker 体系
```
任务来源 (解耦工具 / Cron / Trigger) →
Task Queue (Redis/InMemory) →
TaskWorker × 8 (按 Stream 隔离) →
Executor 执行 (ScopeLock 防并发) →
RuntimeTaskRepository (持久化)
```

#### 流程 3：Subagent 调度
```
主 Agent 调用 Subagent 工具 →
SubagentStore.dispatch →
SkinDiarySubagent / DeepReportSubagent (独立 ReactLoop) →
结果持久化 →
Postprocess / ColdPath / MemoryExtract 后处理
```

### 3. 导航和交互元素

- 固定右侧锚点导航
- 响应式设计（桌面 + 移动端适配）
- 每个模块/流程配有文字说明卡片

## 技术要求

- 单 HTML 文件，零外部依赖
- 使用 SVG 绘制架构图，支持可点击区域
- 纯 CSS 实现交互效果（hover 高亮、点击展开）
- 嵌入式 JavaScript 处理交互逻辑
- 无框架，无 CDN 引用

## 存放位置

- 文件路径：`/Users/shengxingou-1/电商自动化运营/E-commerceAutomation/flowcut_backend/SimpleClaw/architecture.html`

## 实现计划

1. 编写 HTML 骨架 + CSS 样式
2. 绘制 SVG 三层架构总览图
3. 实现模块点击弹出详情面板
4. 编写三个流程图
5. 添加导航和响应式
6. 测试和调优
