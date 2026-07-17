"""Admin HTML page: sidebar building + assembled page template."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Sidebar builder  (mirrors nanobot/_page.py pattern)
# ---------------------------------------------------------------------------

_PROMPT_FILES = [
    {"group": "App 主 Agent",    "key": "agent",              "label": "Agent.md",                 "hot_reload": True},
    {"group": "App 主 Agent",    "key": "soul",               "label": "SOUL.md",                  "hot_reload": True},
    {"group": "App 主 Agent",    "key": "tool",               "label": "TOOL.md",                  "hot_reload": True},
    {"group": "App 主 Agent",    "key": "first_token",        "label": "first_token.md",           "hot_reload": True},
    {"group": "App 主 Agent",    "key": "user_tpl",           "label": "USER.md（格式模板）",        "hot_reload": True},
    {"group": "App Journey",     "key": "novice",             "label": "novice.md",                "hot_reload": True},
    {"group": "App Journey",     "key": "explore",            "label": "explore.md",               "hot_reload": True},
    {"group": "硬件魔镜 Device", "key": "device_agent",       "label": "device/Agent.md",          "hot_reload": True},
    {"group": "硬件魔镜 Device", "key": "device_soul",        "label": "device/SOUL.md",           "hot_reload": True},
    {"group": "硬件魔镜 Device", "key": "device_tool",        "label": "device/TOOL.md",           "hot_reload": True},
    {"group": "硬件魔镜 Device", "key": "device_first_token", "label": "device/first_token.md",    "hot_reload": True},
    {"group": "硬件 Journey",    "key": "device_novice",      "label": "device/journey/novice.md", "hot_reload": True},
    {"group": "硬件 Journey",    "key": "device_explore",     "label": "device/journey/explore.md","hot_reload": True},
    {"group": "冷链路",      "key": "cold_path",          "label": "cold_path.md",          "hot_reload": True},
    {"group": "冷链路",      "key": "compression_memory", "label": "compression_memory.md", "hot_reload": True},
    {"group": "冷链路",      "key": "postprocess",        "label": "postprocess.md",        "hot_reload": True},
    {"group": "肌肤日记",    "key": "skin_diary",         "label": "skin_diary.md",         "hot_reload": False},
    {"group": "肌肤日记",    "key": "skin_diary_tool",    "label": "skin_diary_tool.md",    "hot_reload": False},
    {"group": "深度报告",    "key": "deep_report",        "label": "deep_report.md",        "hot_reload": False},
]

_GROUPS: dict[str, list] = {}
for _e in _PROMPT_FILES:
    _GROUPS.setdefault(_e["group"], []).append(_e)


def build_sidebar(
    filter_groups: list[str] | None = None,
    only_keys: set[str] | None = None,
) -> str:
    out = ""
    for group, entries in _GROUPS.items():
        if filter_groups and group not in filter_groups:
            continue
        group_entries = [e for e in entries if only_keys is None or e["key"] in only_keys]
        if not group_entries:
            continue
        out += f'<div class="group-label">{group}</div>\n'
        for entry in group_entries:
            hot = "" if entry["hot_reload"] else ' <span class="badge-restart">重启生效</span>'
            out += (
                f'<div class="file-item" data-key="{entry["key"]}" onclick="selectFile(\'{entry["key"]}\')">'
                f'{entry["label"]}{hot}</div>\n'
            )
    return out


_SIDEBAR_ALL        = build_sidebar()
_SIDEBAR_AGENT      = build_sidebar(["App 主 Agent", "App Journey", "硬件魔镜 Device", "硬件 Journey"])
_SIDEBAR_POSTPROCESS = build_sidebar(["冷链路"], only_keys={"postprocess"})
_SIDEBAR_COLDPATH   = build_sidebar(["冷链路"], only_keys={"cold_path", "compression_memory"})
_SIDEBAR_SKINDIARY  = build_sidebar(["肌肤日记"])
_SIDEBAR_DEEPREPORT = build_sidebar(["深度报告"])

# ---------------------------------------------------------------------------
# HTML template  (__ placeholders replaced below)
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mojing Admin</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       background: #0f0f11; color: #e8e8ec; height: 100vh; display: flex; flex-direction: column; }

/* ── header / tabs ── */
header { padding: 0 20px; background: #1a1a20; border-bottom: 1px solid #2d2d3a;
         display: flex; align-items: stretch; gap: 0; }
header h1 { font-size: 15px; font-weight: 600; color: #c8b4f8;
            padding: 12px 20px 12px 0; margin-right: 16px;
            border-right: 1px solid #2d2d3a; white-space: nowrap; align-self: center; }
.tab-btn { padding: 14px 20px; font-size: 13px; color: #7070a0; cursor: pointer;
           border-bottom: 2px solid transparent; background: none; border: none;
           border-bottom: 2px solid transparent;
           transition: color 0.15s; white-space: nowrap; }
.tab-btn:hover { color: #b0b0cc; }
.tab-btn.active { color: #c8b4f8; border-bottom-color: #8b5cf6; }

/* ── layout ── */
.main { display: flex; flex: 1; overflow: hidden; }
.sidebar { width: 220px; min-width: 180px; background: #15151b; border-right: 1px solid #2d2d3a;
           overflow-y: auto; padding: 12px 0; flex-shrink: 0; }
.group-label { font-size: 11px; font-weight: 700; color: #6b6b80; text-transform: uppercase;
               letter-spacing: 0.08em; padding: 10px 16px 4px; }
.file-item { padding: 7px 16px; cursor: pointer; font-size: 13px; color: #b0b0c0;
             border-left: 3px solid transparent; line-height: 1.5; }
.file-item:hover { background: #1e1e28; color: #e8e8ec; }
.file-item.active { background: #1e1e2e; color: #c8b4f8; border-left-color: #8b5cf6; }
.badge-restart { font-size: 10px; padding: 1px 5px; border-radius: 3px;
                 background: #3a1e1e; color: #f87171; border: 1px solid #7f1d1d; }

/* ── editor pane ── */
.editor-pane { flex: 1; display: flex; flex-direction: column; overflow: hidden; padding: 16px; gap: 10px; }
#file-title { font-size: 14px; font-weight: 600; color: #9090a8; }
.mono { background: #1a1a22; color: #e8e8ec; border: 1px solid #2d2d3a; border-radius: 6px;
        padding: 14px; font-family: "JetBrains Mono", "Fira Code", monospace;
        font-size: 13px; line-height: 1.7; resize: none; outline: none;
        tab-size: 4; white-space: pre; overflow-wrap: normal; overflow-x: auto; }
.mono:focus { border-color: #8b5cf6; }
.toolbar { display: flex; gap: 10px; align-items: center; flex-shrink: 0; }
button { padding: 7px 18px; border-radius: 6px; border: none; cursor: pointer;
         font-size: 13px; font-weight: 500; transition: opacity 0.15s; }
button:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-purple { background: #8b5cf6; color: #fff; }
.btn-purple:hover:not(:disabled) { background: #7c3aed; }
.btn-red { background: #dc2626; color: #fff; }
.btn-red:hover:not(:disabled) { background: #b91c1c; }
.btn-blue { background: #2563eb; color: #fff; }
.btn-blue:hover:not(:disabled) { background: #1d4ed8; }
#status { font-size: 13px; color: #6b6b80; margin-left: 4px; }
.placeholder { flex: 1; display: flex; align-items: center; justify-content: center;
               color: #3d3d50; font-size: 14px; }

/* ── tab panels ── */
.tab-panel { display: none; flex: 1; overflow: hidden; }
.tab-panel.active { display: flex; }

/* ── debug pane (postprocess / cold path) ── */
.debug-layout { display: flex; flex: 1; overflow: hidden; }
.debug-editor { display: flex; flex-direction: column; flex: 1; padding: 16px; gap: 10px; overflow: hidden; }
.debug-test { width: 420px; min-width: 340px; background: #13131a; border-left: 1px solid #2d2d3a;
              display: flex; flex-direction: column; padding: 16px; gap: 10px; overflow: hidden; }
.debug-test h3 { font-size: 13px; font-weight: 600; color: #9090a8; flex-shrink: 0; }
label { font-size: 12px; color: #7070a0; display: block; margin-bottom: 4px; }
.field-group { display: flex; flex-direction: column; gap: 4px; flex-shrink: 0; }
.debug-inputs { overflow-y: auto; display: flex; flex-direction: column; gap: 10px; flex: 1; }
.debug-output { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 6px; }
.output-label { font-size: 12px; color: #7070a0; flex-shrink: 0; }
.output-box { flex: 1; background: #0d0d14; border: 1px solid #2d2d3a; border-radius: 6px;
              padding: 12px; font-family: "JetBrains Mono", monospace; font-size: 12px;
              line-height: 1.6; color: #a0a0c0; overflow-y: auto; white-space: pre-wrap; }
.divider { border: none; border-top: 1px solid #2d2d3a; margin: 4px 0; flex-shrink: 0; }

/* ── chat tab ── */
.chat-layout { display: flex; flex: 1; overflow: hidden; }
.chat-area { flex: 1; display: flex; flex-direction: column; padding: 16px; gap: 10px; min-width: 0; }
.chat-right { width: 300px; min-width: 260px; flex-shrink: 0; display: flex; flex-direction: column;
              border-left: 1px solid #2d2d3a; background: #0f0f15; }
.chat-log-section { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.chat-log-section + .chat-log-section { border-top: 1px solid #2d2d3a; }
.chat-log-title { font-size: 11px; font-weight: 700; color: #6b6b80; text-transform: uppercase;
                  letter-spacing: 0.08em; padding: 8px 12px 6px; flex-shrink: 0;
                  display: flex; align-items: center; justify-content: space-between; }
.chat-log-title button { font-size: 10px; padding: 2px 7px; border-radius: 4px;
                         background: #1a1a28; color: #6060a0; border: 1px solid #2d2d3a; cursor: pointer; }
.chat-log-box { flex: 1; overflow-y: auto; padding: 8px 12px; font-family: "JetBrains Mono", monospace;
                font-size: 11px; line-height: 1.6; color: #7080a0; white-space: pre-wrap; word-break: break-word; }
.first-token-body { padding: 0 12px 10px; display: flex; flex-direction: column; gap: 8px; }
.first-token-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
.first-token-card { background: #111722; border: 1px solid #243044; border-radius: 6px; padding: 6px 7px; min-width: 0; }
.first-token-label { font-size: 9px; color: #5f7da0; text-transform: uppercase; letter-spacing: 0.06em; }
.first-token-value { margin-top: 3px; font-size: 12px; color: #b8d4f0; font-family: "JetBrains Mono", monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.first-token-preview { background: #0d0d14; border: 1px solid #263044; border-radius: 6px;
                       padding: 7px 8px; font-size: 11px; line-height: 1.5; color: #9fb7d8;
                       min-height: 36px; max-height: 92px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; }
.first-token-note { font-size: 10px; line-height: 1.45; color: #536070; }
.chat-messages { flex: 1; overflow-y: auto; display: flex; flex-direction: column;
                 gap: 10px; padding: 4px 0; }
.msg-row { display: flex; flex-direction: column; gap: 3px; }
.msg-row.row-user { align-self: flex-end; align-items: flex-end; max-width: 80%; }
.msg-row.row-assistant { align-self: flex-start; align-items: flex-start; max-width: 80%; }
.msg-row.row-system { align-self: center; max-width: 100%; }
.msg { padding: 10px 14px; border-radius: 8px; font-size: 13px; line-height: 1.6;
       word-break: break-word; }
.msg-user { background: #2d1f4e; color: #d8ccf8; }
.msg-assistant { background: #1a1f2e; color: #c8d8f0; }
.msg-system { background: #1a1f1a; color: #70a070; font-size: 12px; font-style: italic; }
.msg-meta { font-size: 11px; color: #4a4a60; padding: 0 4px; }
.chat-input-row { display: flex; gap: 10px; flex-shrink: 0; align-items: flex-end; }
#chat-input { flex: 1; min-height: 60px; max-height: 160px; }
#chat-tenant { width: 180px; background: #1a1a22; color: #e8e8ec; border: 1px solid #2d2d3a;
               border-radius: 6px; padding: 8px 10px; font-size: 12px; outline: none; }
#chat-tenant:focus { border-color: #8b5cf6; }

/* scrollbar */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: #0f0f11; }
::-webkit-scrollbar-thumb { background: #2d2d3a; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #3d3d4a; }
</style>
</head>
<body>
<header>
  <h1>Mojing Admin</h1>
  <button class="tab-btn active" onclick="switchTab('editor',this)">Prompt 编辑</button>
  <button class="tab-btn" onclick="switchTab('chat',this)">主 Agent 对话</button>
  <button class="tab-btn" onclick="switchTab('postprocess',this)">Postprocess 调试</button>
  <button class="tab-btn" onclick="switchTab('coldpath',this)">冷链路调试</button>
  <button class="tab-btn" onclick="switchTab('skindiary',this)">肌肤日记调试</button>
  <button class="tab-btn" onclick="switchTab('deepreport',this)">深度报告调试</button>
</header>

<div class="main">

<!-- ══════════════════════════════════════════ Tab 1: Prompt 编辑 ══ -->
<div class="tab-panel active" id="tab-editor">
  <div class="sidebar">__SIDEBAR_ALL__</div>
  <div class="editor-pane">
    <div id="file-title" class="placeholder">← 从左侧选择文件</div>
    <textarea id="editor" class="mono" style="display:none;flex:1" spellcheck="false"></textarea>
    <div class="toolbar" id="toolbar" style="display:none">
      <button class="btn-purple" id="btn-save" onclick="saveFile()">保存</button>
      <button class="btn-red" id="btn-restart" onclick="restartServer()">重启服务</button>
      <span id="status"></span>
    </div>
  </div>
</div>

<!-- ════════════════════════════════════════ Tab 2: 主 Agent 对话 ══ -->
<div class="tab-panel" id="tab-chat">
  <div class="sidebar">__SIDEBAR_AGENT__</div>
  <div class="chat-layout" style="flex:1;overflow:hidden;">

    <!-- 中间：对话区 -->
    <div class="chat-area">
      <div class="chat-messages" id="chat-messages">
        <div class="msg-row row-system"><div class="msg msg-system">选择左侧 prompt 文件可随时编辑；对话使用当前已加载的 prompt（热加载生效）。</div></div>
      </div>
      <div class="chat-input-row">
        <div style="display:flex;flex-direction:column;gap:6px;flex:0 0 200px;">
          <label style="font-size:12px;color:#7070a0;">Tenant Key / User ID</label>
          <input id="chat-tenant" type="text" list="tenant-list" placeholder="选择或输入 user_id"
                 style="background:#1a1a22;color:#e8e8ec;border:1px solid #2d2d3a;border-radius:6px;padding:8px 10px;font-size:12px;outline:none;">
          <datalist id="tenant-list"></datalist>
          <div id="chat-session-label" style="font-size:11px;color:#4a4a60;padding:0 2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"></div>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;gap:6px;">
          <textarea id="chat-input" class="mono" placeholder="输入消息，Cmd+Enter 发送（可仅发图片 URL，文字留空）…" style="min-height:60px;max-height:160px;"></textarea>
          <div style="display:flex;align-items:center;gap:6px;">
            <span style="font-size:11px;color:#6060a0;flex-shrink:0;white-space:nowrap;">图片 URL</span>
            <input id="chat-media" type="text" placeholder="https://oss.xxx.com/… （对应 media 字段，可单独发送）"
              style="flex:1;background:#1a1a22;color:#a0c8e8;border:1px solid #2d3a4a;border-radius:6px;padding:6px 10px;font-size:11px;font-family:monospace;outline:none;">
          </div>
        </div>
        <button class="btn-purple" id="btn-send" onclick="sendChat()" style="align-self:flex-end;">发送</button>
      </div>
    </div>

    <!-- 右侧：档案 + 日志面板 -->
    <div class="chat-right">

      <!-- 首 token 流式观测 -->
      <div class="chat-log-section" id="first-token-section" style="flex:0 0 auto;max-height:210px;">
        <div class="chat-log-title">
          首 token 观测
          <button onclick="resetFirstTokenProbe()">清空</button>
        </div>
        <div id="first-token-body" class="first-token-body">
          <div class="first-token-grid">
            <div class="first-token-card">
              <div class="first-token-label">Source</div>
              <div class="first-token-value" id="ft-source">-</div>
            </div>
            <div class="first-token-card">
              <div class="first-token-label">TTFT</div>
              <div class="first-token-value" id="ft-ttft">-</div>
            </div>
            <div class="first-token-card">
              <div class="first-token-label">FT / Main</div>
              <div class="first-token-value" id="ft-chunks">0 / 0</div>
            </div>
            <div class="first-token-card">
              <div class="first-token-label">Total</div>
              <div class="first-token-value" id="ft-total">-</div>
            </div>
          </div>
          <div class="first-token-preview" id="ft-preview">等待发送一轮对话…</div>
          <div class="first-token-note" id="ft-note">这里会按 SSE node 区分 first_token_llm 和 main_agent。</div>
        </div>
      </div>

      <!-- 用户档案（可折叠） -->
      <div class="chat-log-section" id="profile-section" style="flex:0 0 auto;max-height:300px;">
        <div class="chat-log-title" style="cursor:pointer;" onclick="toggleProfile()">
          用户档案
          <div style="display:flex;gap:4px;">
            <button id="profile-refresh-btn" onclick="event.stopPropagation();refreshProfile()">刷新</button>
            <button onclick="event.stopPropagation();previewMainAgentPrompt()" style="font-size:10px;padding:2px 6px;border-radius:4px;background:#1a2a1a;color:#6ee7b7;border:1px solid #2d4a2d;cursor:pointer;">预览 Prompt</button>
            <button onclick="event.stopPropagation();toggleProfile()" id="profile-toggle-btn">收起</button>
          </div>
        </div>
        <div id="profile-body" style="overflow-y:auto;padding:8px 12px;font-size:11px;line-height:1.6;">
          <div style="color:#4a4a60;">选择 Tenant 后自动加载…</div>
        </div>
      </div>

      <!-- 系统 Prompt 预览（折叠） -->
      <div class="chat-log-section" id="main-prompt-section" style="flex:0 0 auto;display:none;">
        <div class="chat-log-title">
          系统 Prompt 预览
          <button onclick="document.getElementById('main-prompt-section').style.display='none'">关闭</button>
        </div>
        <div id="main-prompt-preview" class="chat-log-box" style="flex:1;max-height:280px;white-space:pre-wrap;font-size:10px;color:#8090a8;"></div>
      </div>

      <!-- 历史上下文 -->
      <div class="chat-log-section" id="ctx-section" style="flex:0 0 auto;max-height:220px;">
        <div class="chat-log-title" style="cursor:pointer;" onclick="toggleChatSection('ctx-section','ctx-body','ctx-toggle')">
          历史上下文
          <div style="display:flex;gap:4px;">
            <button id="ctx-refresh-btn" onclick="event.stopPropagation();refreshContextHistory()">刷新</button>
            <button id="ctx-toggle" onclick="event.stopPropagation();toggleChatSection('ctx-section','ctx-body','ctx-toggle')">收起</button>
          </div>
        </div>
        <div id="ctx-body" style="overflow-y:auto;flex:1;padding:0 12px 8px;font-size:10px;line-height:1.6;">
          <div style="color:#4a4a60;">选择 Tenant 后点刷新…</div>
        </div>
      </div>

      <!-- 指令跟随 & 动态拼接 -->
      <div class="chat-log-section" id="inj-section" style="flex:0 0 auto;max-height:260px;">
        <div class="chat-log-title" style="cursor:pointer;" onclick="toggleChatSection('inj-section','inj-body','inj-toggle')">
          指令跟随 & 动态拼接
          <div style="display:flex;gap:4px;">
            <button id="inj-refresh-btn" onclick="event.stopPropagation();refreshDynamicState()">刷新</button>
            <button id="inj-toggle" onclick="event.stopPropagation();toggleChatSection('inj-section','inj-body','inj-toggle')">收起</button>
          </div>
        </div>
        <div id="inj-body" style="overflow-y:auto;flex:1;padding:0 12px 8px;font-size:11px;line-height:1.6;">
          <div style="color:#4a4a60;">选择 Tenant 后点刷新…</div>
        </div>
      </div>

      <!-- 后台任务 -->
      <div class="chat-log-section" id="jobs-section" style="flex:0 0 auto;max-height:160px;">
        <div class="chat-log-title" style="cursor:pointer;" onclick="toggleChatSection('jobs-section','jobs-body','jobs-toggle')">
          后台任务
          <div style="display:flex;gap:4px;">
            <button onclick="event.stopPropagation();refreshDynamicState()">刷新</button>
            <button id="jobs-toggle" onclick="event.stopPropagation();toggleChatSection('jobs-section','jobs-body','jobs-toggle')">收起</button>
          </div>
        </div>
        <div id="jobs-body" style="overflow-y:auto;flex:1;padding:0 12px 8px;font-size:11px;line-height:1.6;">
          <div style="color:#4a4a60;">暂无后台任务</div>
        </div>
      </div>

    </div><!-- /.chat-right -->
  </div><!-- /.chat-layout -->
</div><!-- /#tab-chat -->

<!-- ═══════════════════════════════════════ Tab 3: Postprocess 调试 ══ -->
<div class="tab-panel" id="tab-postprocess">
  <div class="sidebar">__SIDEBAR_POSTPROCESS__</div>
  <div class="debug-layout">
    <div class="debug-editor">
      <div id="pp-file-title" style="font-size:14px;font-weight:600;color:#9090a8;">← 选择 Prompt 文件</div>
      <textarea id="pp-editor" class="mono" style="display:none;flex:1" spellcheck="false"></textarea>
      <div class="toolbar" id="pp-toolbar" style="display:none">
        <button class="btn-purple" onclick="savePpFile()">保存</button>
        <span id="pp-status"></span>
      </div>
    </div>
    <div class="debug-test">
      <h3>Postprocess 调试</h3>
      <!-- tenant loader -->
      <div style="flex-shrink:0;display:flex;flex-direction:column;gap:6px;">
        <div class="field-group" style="flex-direction:row;align-items:center;gap:8px;">
          <input id="pp-tenant" type="text" list="tenant-list" placeholder="tenant_key"
            style="flex:1;background:#1a1a22;color:#e8e8ec;border:1px solid #2d2d3a;border-radius:6px;padding:6px 10px;font-size:12px;outline:none;">
          <button class="btn-blue" onclick="loadTenantData()" style="flex-shrink:0;padding:6px 12px;font-size:12px;">从 DB 加载</button>
        </div>
        <div id="pp-load-status" style="font-size:11px;color:#6060a0;min-height:14px;"></div>
      </div>
      <!-- scrollable body -->
      <div style="overflow-y:auto;flex:1;display:flex;flex-direction:column;gap:10px;padding:4px 0;">
        <!-- USER.md -->
        <div class="field-group">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
            <label style="color:#c8b4f8;margin:0;">USER.md</label>
            <button class="btn-purple" onclick="saveTenantDoc('user','USER.md','pp-user-md','pp-save-user')"
              id="pp-save-user" style="padding:2px 10px;font-size:11px;">保存</button>
          </div>
          <textarea id="pp-user-md" class="mono" style="height:100px;" placeholder="（DB 中暂无）"></textarea>
        </div>
        <hr class="divider">
        <!-- inputs -->
        <div class="field-group">
          <label>用户消息</label>
          <textarea id="pp-user-msg" class="mono" style="height:55px;" placeholder="用户说了什么…"></textarea>
        </div>
        <div class="field-group">
          <label>主 Agent 回复</label>
          <textarea id="pp-agent-reply" class="mono" style="height:55px;" placeholder="主 Agent 回复内容…"></textarea>
        </div>
        <button class="btn-purple" onclick="runPostprocessLLM()" style="flex-shrink:0;">▶ 实际运行 LLM</button>
        <div id="pp-run-section" style="display:none;flex-direction:column;gap:6px;">
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="font-size:11px;font-weight:600;color:#c8b4f8;">LLM 文本输出</span>
            <span id="pp-run-status" style="font-size:11px;color:#6060a0;"></span>
          </div>
          <div class="output-box" id="pp-run-raw" style="min-height:40px;max-height:120px;"></div>
        </div>
        <div id="pp-toolcalls-section" style="display:none;flex-direction:column;gap:6px;">
          <span style="font-size:11px;font-weight:600;color:#c8b4f8;">Tool Calls（dry-run，不写 DB）</span>
          <div class="output-box" id="pp-toolcalls" style="min-height:60px;max-height:220px;"></div>
        </div>
        <div id="pp-prompt-section" style="display:none;flex-direction:column;gap:6px;">
          <span style="font-size:11px;font-weight:600;color:#9090a8;">完整 Prompt（SYSTEM + USER，即 LLM 真实收到的内容）</span>
          <div class="output-box" id="pp-input-prompt" style="min-height:40px;max-height:260px;font-size:10px;"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════ Tab 4: 冷链路调试 ══ -->
<div class="tab-panel" id="tab-coldpath">
  <div class="sidebar">__SIDEBAR_COLDPATH__</div>
  <div class="debug-layout">
    <div class="debug-editor">
      <div id="cp-file-title" style="font-size:14px;font-weight:600;color:#9090a8;">← 选择 Prompt 文件</div>
      <textarea id="cp-editor" class="mono" style="display:none;flex:1" spellcheck="false"></textarea>
      <div class="toolbar" id="cp-toolbar" style="display:none">
        <button class="btn-purple" onclick="saveCpFile()">保存</button>
        <span id="cp-status"></span>
      </div>
    </div>
    <div class="debug-test">
      <h3>冷链路调试</h3>
      <!-- shared tenant loader -->
      <div style="flex-shrink:0;display:flex;flex-direction:column;gap:6px;">
        <div class="field-group" style="flex-direction:row;align-items:center;gap:8px;">
          <input id="cp-tenant" type="text" list="tenant-list" placeholder="tenant_key"
            style="flex:1;background:#1a1a22;color:#e8e8ec;border:1px solid #2d2d3a;border-radius:6px;padding:6px 10px;font-size:12px;outline:none;">
          <button class="btn-blue" onclick="loadTenantDocsForCp()" style="flex-shrink:0;padding:6px 12px;font-size:12px;">检查状态</button>
        </div>
        <div id="cp-load-status" style="font-size:11px;color:#6060a0;min-height:14px;"></div>
      </div>
      <!-- scrollable body with path sections -->
      <div style="overflow-y:auto;flex:1;display:flex;flex-direction:column;gap:12px;padding:2px 0;">

        <!-- Path 1: obligation extraction -->
        <div style="border:1px solid #2d2d3a;border-radius:6px;overflow:hidden;flex-shrink:0;">
          <div style="background:#1a1a22;padding:7px 12px;font-size:12px;font-weight:600;color:#7dd3fc;">
            路径一 · obligation 抽取（每轮触发）
          </div>
          <div style="padding:10px;display:flex;flex-direction:column;gap:8px;">
            <div class="field-group">
              <label>用户消息</label>
              <textarea id="cp-user-msg" class="mono" style="height:55px;" placeholder="用户说了什么…"></textarea>
            </div>
            <div class="field-group">
              <label>主 Agent 回复</label>
              <textarea id="cp-agent-reply" class="mono" style="height:55px;" placeholder="主 Agent 回复内容…"></textarea>
            </div>
            <div style="display:flex;gap:6px;flex-shrink:0;">
              <button class="btn-purple" onclick="runColdPathLLM()" style="flex:1;">▶ 实际运行 LLM</button>
            </div>
            <div id="cp-run-section" style="display:none;flex-direction:column;gap:6px;">
              <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:11px;font-weight:600;color:#c8b4f8;">LLM 输出</span>
                <span id="cp-run-status" style="font-size:11px;color:#6060a0;"></span>
              </div>
              <div class="output-box" id="cp-run-raw" style="min-height:40px;max-height:80px;"></div>
              <span style="font-size:11px;font-weight:600;color:#c8b4f8;">解析结果 JSON</span>
              <div class="output-box" id="cp-run-parsed" style="min-height:60px;max-height:180px;font-family:monospace;font-size:11px;white-space:pre;"></div>
            </div>
          </div>
        </div>

        <!-- Path 2: 连续执行 -->
        <div style="border:1px solid #3a2d5a;border-radius:6px;overflow:hidden;flex-shrink:0;">
          <div style="background:#1e1428;padding:7px 12px;font-size:12px;font-weight:600;color:#c8b4f8;">
            路径一 · 连续执行（多轮 obligation 抽取）
          </div>
          <div style="padding:10px;display:flex;flex-direction:column;gap:8px;">
            <div style="font-size:11px;color:#6060a0;line-height:1.6;">
              粘入多轮对话，观察每轮是否抽取出明确用户待办或 agent 承诺。
            </div>
            <div class="field-group">
              <label>多轮对话 JSON（数组）</label>
              <textarea id="cp-chain-input" class="mono" style="height:160px;" placeholder='[
  {"user_message": "我最近开始用新的防晒", "assistant_reply": "好消息！防晒换对了很大"},
  {"user_message": "化学防晒，但额头还是出油", "assistant_reply": "额头出油是混合肌常见问题"}
]'></textarea>
            </div>
            <div style="display:flex;gap:6px;align-items:center;flex-shrink:0;">
              <button class="btn-purple" onclick="runColdPathChain()" style="flex:1;">▶ 连续执行全部轮次</button>
              <button onclick="fillChainExample()" style="flex-shrink:0;padding:6px 10px;font-size:11px;background:#1a1a22;color:#7070a0;border:1px solid #2d2d3a;border-radius:6px;cursor:pointer;">填入示例</button>
            </div>
            <div id="cp-chain-status" style="font-size:11px;color:#6060a0;min-height:14px;"></div>
            <div id="cp-chain-output" style="display:none;flex-direction:column;gap:8px;"></div>
          </div>
        </div>

      </div>
    </div>
  </div>
</div>

<!-- ════════════════════════════════════════ Tab 5: 肌肤日记调试 ══ -->
<div class="tab-panel" id="tab-skindiary">
  <div class="sidebar">__SIDEBAR_SKINDIARY__</div>
  <div class="chat-layout" style="flex:1;overflow:hidden;">

    <!-- 中间：对话区 -->
    <div class="chat-area">
      <div class="chat-messages" id="sd-messages">
        <div class="msg-row row-system"><div class="msg msg-system">填写 User ID 后直接发消息，消息将路由到 skin_diary:{user_id} 会话。</div></div>
      </div>
      <div class="chat-input-row">
        <div style="display:flex;flex-direction:column;gap:6px;flex:0 0 200px;">
          <label style="font-size:12px;color:#7070a0;">User ID / Tenant Key</label>
          <input id="sd-tenant" type="text" list="tenant-list" placeholder="user_id"
            style="background:#1a1a22;color:#e8e8ec;border:1px solid #2d2d3a;border-radius:6px;padding:8px 10px;font-size:12px;outline:none;">
          <div style="display:flex;gap:6px;">
            <button class="btn-blue" onclick="initSkinDiarySession(false)" style="flex:1;padding:5px 8px;font-size:11px;">仅查看</button>
            <button class="btn-purple" onclick="initSkinDiarySession(true)" style="flex:1;padding:5px 8px;font-size:11px;">激活进入</button>
          </div>
        </div>
        <textarea id="sd-input" class="mono" placeholder="输入消息，Cmd+Enter 发送…" style="flex:1;min-height:60px;max-height:120px;"></textarea>
        <button class="btn-purple" id="sd-send-btn" onclick="sendSkinDiaryChat()" style="align-self:flex-end;">发送</button>
      </div>
    </div>

    <!-- 右侧：会话信息 + 档案 -->
    <div class="chat-right">
      <div class="chat-log-section" style="flex:0 0 auto;">
        <div class="chat-log-title">会话状态</div>
        <div id="sd-session-info" style="padding:8px 12px;font-size:11px;line-height:1.7;color:#6070a0;">
          <div>session: <span style="color:#c8b4f8;">skin_diary:{user_id}</span></div>
        </div>
      </div>
      <div class="chat-log-section" id="sd-prompt-section" style="flex:0 0 auto;display:none;max-height:55vh;overflow-y:auto;">
        <div class="chat-log-title" style="position:sticky;top:0;background:#0f0f15;z-index:1;">
          Prompt 预览
          <button onclick="document.getElementById('sd-prompt-section').style.display='none'">关闭</button>
        </div>
        <div style="padding:4px 12px 2px;font-size:10px;color:#5a6b5a;font-weight:600;">子 Agent 系统 Prompt</div>
        <div id="sd-agent-prompt-preview" class="chat-log-box" style="max-height:220px;white-space:pre-wrap;font-size:10px;color:#8090a8;margin:0 12px 8px;"></div>
        <div style="padding:4px 12px 2px;font-size:10px;color:#5a6b5a;font-weight:600;">generate_skin_diary 工具 Prompt</div>
        <div id="sd-tool-prompt-preview" class="chat-log-box" style="max-height:220px;white-space:pre-wrap;font-size:10px;color:#8090a8;margin:0 12px 8px;"></div>
      </div>
      <!-- 日记结果 / 工具测试 -->
      <div class="chat-log-section" style="flex:0 0 auto;">
        <div class="chat-log-title">
          生成日记工具测试
          <div style="display:flex;gap:4px;align-items:center;">
            <span id="sd-tool-status" style="font-size:10px;color:#6060a0;"></span>
            <button onclick="loadSkinDiaryResult()" style="font-size:10px;padding:2px 8px;border-radius:4px;background:#2a1a2a;color:#c8b4f8;border:1px solid #4a2d6a;cursor:pointer;">▶ 运行</button>
          </div>
        </div>
        <div style="padding:6px 12px 8px;display:flex;flex-direction:column;gap:5px;">
          <div style="font-size:10px;color:#6060a0;">从 DB 读取该用户最新肌肤日记分析结果</div>
          <div id="sd-tool-run-output" style="display:none;flex-direction:column;gap:5px;">
            <span style="font-size:10px;font-weight:600;color:#c8b4f8;">总结</span>
            <div class="output-box" id="sd-tool-raw" style="min-height:40px;max-height:80px;font-size:10px;"></div>
            <span style="font-size:10px;font-weight:600;color:#c8b4f8;">完整 JSON</span>
            <div class="output-box" id="sd-tool-parsed" style="min-height:60px;max-height:200px;font-family:monospace;font-size:10px;white-space:pre;"></div>
          </div>
        </div>
      </div>
      <div class="chat-log-section" style="flex:0 0 auto;max-height:280px;">
        <div class="chat-log-title">
          用户档案 / 肌肤概况
          <div style="display:flex;gap:4px;">
            <button onclick="previewSkinDiaryPrompt()" style="font-size:10px;padding:2px 6px;border-radius:4px;background:#1a2a1a;color:#6ee7b7;border:1px solid #2d4a2d;cursor:pointer;">预览 Prompt</button>
            <button onclick="refreshSdProfile()">刷新</button>
          </div>
        </div>
        <div id="sd-profile-body" class="chat-log-box" style="font-size:11px;line-height:1.6;"></div>
      </div>

      <!-- 历史上下文 -->
      <div class="chat-log-section" id="sd-ctx-section" style="flex:0 0 auto;max-height:220px;">
        <div class="chat-log-title" style="cursor:pointer;" onclick="toggleChatSection('sd-ctx-section','sd-ctx-body','sd-ctx-toggle')">
          历史上下文
          <div style="display:flex;gap:4px;">
            <button onclick="event.stopPropagation();refreshSdContextHistory()">刷新</button>
            <button id="sd-ctx-toggle" onclick="event.stopPropagation();toggleChatSection('sd-ctx-section','sd-ctx-body','sd-ctx-toggle')">收起</button>
          </div>
        </div>
        <div id="sd-ctx-body" style="overflow-y:auto;flex:1;padding:0 12px 8px;font-size:10px;line-height:1.6;">
          <div style="color:#4a4a60;">载入会话后自动加载…</div>
        </div>
      </div>

      <!-- 指令跟随 & 动态拼接（topic_key=skin_diary:{tenant}） -->
      <div class="chat-log-section" id="sd-inj-section" style="flex:0 0 auto;max-height:240px;">
        <div class="chat-log-title" style="cursor:pointer;" onclick="toggleChatSection('sd-inj-section','sd-inj-body','sd-inj-toggle')">
          指令跟随 & 动态拼接
          <div style="display:flex;gap:4px;">
            <button onclick="event.stopPropagation();refreshSdDynamicState()">刷新</button>
            <button id="sd-inj-toggle" onclick="event.stopPropagation();toggleChatSection('sd-inj-section','sd-inj-body','sd-inj-toggle')">收起</button>
          </div>
        </div>
        <div id="sd-inj-body" style="overflow-y:auto;flex:1;padding:0 12px 8px;font-size:11px;line-height:1.6;">
          <div style="color:#4a4a60;">载入会话后自动加载…</div>
        </div>
      </div>

      <!-- 后台任务（按 tenant_key 过滤） -->
      <div class="chat-log-section" id="sd-jobs-section" style="flex:0 0 auto;max-height:160px;">
        <div class="chat-log-title" style="cursor:pointer;" onclick="toggleChatSection('sd-jobs-section','sd-jobs-body','sd-jobs-toggle')">
          后台任务
          <div style="display:flex;gap:4px;">
            <button onclick="event.stopPropagation();refreshSdDynamicState()">刷新</button>
            <button id="sd-jobs-toggle" onclick="event.stopPropagation();toggleChatSection('sd-jobs-section','sd-jobs-body','sd-jobs-toggle')">收起</button>
          </div>
        </div>
        <div id="sd-jobs-body" style="overflow-y:auto;flex:1;padding:0 12px 8px;font-size:11px;line-height:1.6;">
          <div style="color:#4a4a60;">载入会话后自动加载…</div>
        </div>
      </div>
    </div>

  </div>
</div>

<!-- ════════════════════════════════════════ Tab 6: 深度报告调试 ══ -->
<div class="tab-panel" id="tab-deepreport">
  <div class="sidebar">__SIDEBAR_DEEPREPORT__</div>
  <div class="chat-layout" style="flex:1;overflow:hidden;">

    <!-- 中间：对话区 -->
    <div class="chat-area">
      <div class="chat-messages" id="dr-messages">
        <div class="msg-row row-system"><div class="msg msg-system">填写 User ID 后直接发消息，消息将路由到 deep_report:{user_id} 会话。</div></div>
      </div>
      <div class="chat-input-row">
        <div style="display:flex;flex-direction:column;gap:6px;flex:0 0 200px;">
          <label style="font-size:12px;color:#7070a0;">User ID / Tenant Key</label>
          <input id="dr-tenant" type="text" list="tenant-list" placeholder="user_id"
            style="background:#1a1a22;color:#e8e8ec;border:1px solid #2d2d3a;border-radius:6px;padding:8px 10px;font-size:12px;outline:none;">
          <button class="btn-blue" onclick="initDeepReportSession()" style="padding:5px 8px;font-size:11px;">载入会话</button>
        </div>
        <div style="display:flex;flex-direction:column;gap:6px;flex:0 0 200px;">
          <label style="font-size:12px;color:#7070a0;">Report ID（可选，留空走 latest）</label>
          <input id="dr-report-id" type="text" placeholder="report_id（留空 fallback latest）"
            style="background:#1a1a22;color:#e8e8ec;border:1px solid #2d2d3a;border-radius:6px;padding:8px 10px;font-size:12px;outline:none;">
          <button class="btn-blue" onclick="loadDeepReportList()" style="padding:5px 8px;font-size:11px;background:#22324a;">列出本人报告</button>
        </div>
        <textarea id="dr-input" class="mono" placeholder="输入消息，Cmd+Enter 发送…" style="flex:1;min-height:60px;max-height:120px;"></textarea>
        <button class="btn-purple" id="dr-send-btn" onclick="sendDeepReportChat()" style="align-self:flex-end;">发送</button>
      </div>
    </div>

    <!-- 右侧：会话信息 + 报告原文 -->
    <div class="chat-right">
      <div class="chat-log-section" style="flex:0 0 auto;">
        <div class="chat-log-title">会话状态</div>
        <div id="dr-session-info" style="padding:8px 12px;font-size:11px;line-height:1.7;color:#6070a0;">
          <div>session: <span style="color:#c8b4f8;">deep_report:{user_id}</span></div>
        </div>
      </div>
      <div class="chat-log-section" id="dr-prompt-section" style="flex:0 0 auto;display:none;max-height:55vh;overflow-y:auto;">
        <div class="chat-log-title" style="position:sticky;top:0;background:#0f0f15;z-index:1;">
          Prompt 预览
          <button onclick="document.getElementById('dr-prompt-section').style.display='none'">关闭</button>
        </div>
        <div style="padding:4px 12px 2px;font-size:10px;color:#5a6b5a;font-weight:600;">子 Agent 完整运行时 Prompt</div>
        <div id="dr-agent-prompt-preview" class="chat-log-box" style="max-height:380px;white-space:pre-wrap;font-size:10px;color:#8090a8;margin:0 12px 8px;"></div>
      </div>
      <!-- 最新报告 / 原始 JSON -->
      <div class="chat-log-section" style="flex:0 0 auto;">
        <div class="chat-log-title">
          最新报告（DB 直读）
          <div style="display:flex;gap:4px;align-items:center;">
            <span id="dr-tool-status" style="font-size:10px;color:#6060a0;"></span>
            <button onclick="loadDeepReportResult()" style="font-size:10px;padding:2px 8px;border-radius:4px;background:#2a1a2a;color:#c8b4f8;border:1px solid #4a2d6a;cursor:pointer;">▶ 运行</button>
          </div>
        </div>
        <div style="padding:6px 12px 8px;display:flex;flex-direction:column;gap:5px;">
          <div id="dr-tool-subtitle" style="font-size:10px;color:#6060a0;">V2 三表 JOIN，按 user_id 取最新一条 status=done</div>
          <div id="dr-tool-run-output" style="display:none;flex-direction:column;gap:5px;">
            <span style="font-size:10px;font-weight:600;color:#c8b4f8;">报告概要</span>
            <div class="output-box" id="dr-tool-summary" style="min-height:40px;max-height:80px;font-size:10px;"></div>
            <span style="font-size:10px;font-weight:600;color:#c8b4f8;">JOIN 后完整 JSON</span>
            <div class="output-box" id="dr-tool-parsed" style="min-height:60px;max-height:240px;font-family:monospace;font-size:10px;white-space:pre;"></div>
          </div>
        </div>
      </div>
      <div class="chat-log-section" style="flex:0 0 auto;max-height:280px;">
        <div class="chat-log-title">
          用户档案 / 长期记忆
          <div style="display:flex;gap:4px;">
            <button onclick="previewDeepReportPrompt()" style="font-size:10px;padding:2px 6px;border-radius:4px;background:#1a2a1a;color:#6ee7b7;border:1px solid #2d4a2d;cursor:pointer;">预览 Prompt</button>
            <button onclick="refreshDrProfile()">刷新</button>
          </div>
        </div>
        <div id="dr-profile-body" class="chat-log-box" style="font-size:11px;line-height:1.6;"></div>
      </div>

      <!-- 历史上下文 -->
      <div class="chat-log-section" id="dr-ctx-section" style="flex:0 0 auto;max-height:220px;">
        <div class="chat-log-title" style="cursor:pointer;" onclick="toggleChatSection('dr-ctx-section','dr-ctx-body','dr-ctx-toggle')">
          历史上下文
          <div style="display:flex;gap:4px;">
            <button onclick="event.stopPropagation();refreshDrContextHistory()">刷新</button>
            <button id="dr-ctx-toggle" onclick="event.stopPropagation();toggleChatSection('dr-ctx-section','dr-ctx-body','dr-ctx-toggle')">收起</button>
          </div>
        </div>
        <div id="dr-ctx-body" style="overflow-y:auto;flex:1;padding:0 12px 8px;font-size:10px;line-height:1.6;">
          <div style="color:#4a4a60;">载入会话后自动加载…</div>
        </div>
      </div>

      <!-- 指令跟随 & 动态拼接（topic_key=deep_report:{tenant}） -->
      <div class="chat-log-section" id="dr-inj-section" style="flex:0 0 auto;max-height:240px;">
        <div class="chat-log-title" style="cursor:pointer;" onclick="toggleChatSection('dr-inj-section','dr-inj-body','dr-inj-toggle')">
          指令跟随 & 动态拼接
          <div style="display:flex;gap:4px;">
            <button onclick="event.stopPropagation();refreshDrDynamicState()">刷新</button>
            <button id="dr-inj-toggle" onclick="event.stopPropagation();toggleChatSection('dr-inj-section','dr-inj-body','dr-inj-toggle')">收起</button>
          </div>
        </div>
        <div id="dr-inj-body" style="overflow-y:auto;flex:1;padding:0 12px 8px;font-size:11px;line-height:1.6;">
          <div style="color:#4a4a60;">载入会话后自动加载…</div>
        </div>
      </div>

      <!-- 后台任务 -->
      <div class="chat-log-section" id="dr-jobs-section" style="flex:0 0 auto;max-height:160px;">
        <div class="chat-log-title" style="cursor:pointer;" onclick="toggleChatSection('dr-jobs-section','dr-jobs-body','dr-jobs-toggle')">
          后台任务
          <div style="display:flex;gap:4px;">
            <button onclick="event.stopPropagation();refreshDrDynamicState()">刷新</button>
            <button id="dr-jobs-toggle" onclick="event.stopPropagation();toggleChatSection('dr-jobs-section','dr-jobs-body','dr-jobs-toggle')">收起</button>
          </div>
        </div>
        <div id="dr-jobs-body" style="overflow-y:auto;flex:1;padding:0 12px 8px;font-size:11px;line-height:1.6;">
          <div style="color:#4a4a60;">载入会话后自动加载…</div>
        </div>
      </div>
    </div>

  </div>
</div>

</div><!-- /.main -->

<script>
// ── tab switching ──────────────────────────────────────────────────────────
function switchTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => {
    p.classList.remove('active');
    p.style.display = 'none';
  });
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const panel = document.getElementById('tab-' + name);
  if (panel) { panel.classList.add('active'); panel.style.display = 'flex'; }
  if (btn) btn.classList.add('active');
}

// ── shared file loader ─────────────────────────────────────────────────────
let currentKey = null, currentPpKey = null, currentCpKey = null;

async function selectFile(key) {
  document.querySelectorAll('.file-item').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.file-item[data-key="' + key + '"]').forEach(el => el.classList.add('active'));
  setStatus('加载中…');
  try {
    const data = await fetchPrompt(key);
    currentKey = key;
    document.getElementById('file-title').textContent = data.label;
    document.getElementById('file-title').classList.remove('placeholder');
    const ed = document.getElementById('editor');
    ed.style.display = ''; ed.value = data.content;
    document.getElementById('toolbar').style.display = '';
    setStatus('');
    if (['postprocess'].includes(key)) loadPpEditor(key, data);
    if (['cold_path', 'compression_memory'].includes(key)) loadCpEditor(key, data);
  } catch(e) { setStatus('加载失败: ' + e.message); }
}

async function fetchPrompt(key) {
  const r = await fetch('/admin/prompt?file=' + encodeURIComponent(key));
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ── Tab 1: editor ─────────────────────────────────────────────────────────
async function saveFile() {
  if (!currentKey) return;
  document.getElementById('btn-save').disabled = true;
  setStatus('保存中…');
  try {
    await putPrompt(currentKey, document.getElementById('editor').value);
    setStatus('已保存');
  } catch(e) { setStatus('保存失败: ' + e.message); }
  finally { document.getElementById('btn-save').disabled = false; }
}

async function restartServer() {
  if (!confirm('确定重启？')) return;
  setStatus('发送重启指令…');
  document.getElementById('btn-restart').disabled = true;
  try {
    await fetch('/admin/restart', {method: 'POST'});
    setStatus('重启中，请稍候刷新…');
  } catch(e) { setStatus('重启失败: ' + e.message); document.getElementById('btn-restart').disabled = false; }
}

function setStatus(msg) { document.getElementById('status').textContent = msg; }

// ── putPrompt: SimpleClaw uses raw text body ───────────────────────────────
async function putPrompt(key, content) {
  const r = await fetch('/admin/prompt?file=' + encodeURIComponent(key), {
    method: 'PUT',
    headers: {'Content-Type': 'text/plain; charset=utf-8'},
    body: content,
  });
  if (!r.ok) throw new Error(await r.text());
}

// ── Tab 2: chat ────────────────────────────────────────────────────────────
function ms(t) { return t.toFixed(0) + ' ms'; }
let _chatEventsSource = null;
let _cronBubble = null;
let _cronText = '';

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function resetFirstTokenProbe(note) {
  setText('ft-source', '-');
  setText('ft-ttft', '-');
  setText('ft-chunks', '0 / 0');
  setText('ft-total', '-');
  setText('ft-preview', '等待首个文本 chunk…');
  setText('ft-note', note || '这里会按 SSE node 区分 first_token_llm 和 main_agent。');
}

function eventChunkSource(evt) {
  const node = String((evt && evt.node) || (evt && evt.data && evt.data.source) || '').toLowerCase();
  return node.includes('first_token') ? 'first_token_llm' : 'main_agent';
}

function handleFirstTokenStatus(data) {
  const status = (data && data.status) || '?';
  if (status === 'started') {
    setText('ft-source', 'first_token_llm');
    setText('ft-note', 'first_token_llm 已启动；model=' + (data.model || '?') + '，等待窗口 ' + (data.timeout_ms || '?') + 'ms。');
  } else if (status === 'timeout') {
    setText('ft-note', 'first_token_llm 超时，没有及时产出可注入 opener：' + (data.detail || ''));
  } else if (status === 'failed') {
    setText('ft-note', 'first_token_llm 调用失败：' + (data.detail || 'unknown'));
  } else if (status === 'empty') {
    setText('ft-note', 'first_token_llm 完成但没有产出文本。');
  } else if (status === 'done') {
    setText('ft-note', 'first_token_llm 完成，产出 ' + (data.chars || 0) + ' 个字符。');
  } else if (status === 'disabled') {
    setText('ft-source', 'disabled');
    setText('ft-note', 'first_token_llm 未启用或本轮不适用。');
  } else {
    setText('ft-note', 'first_token_llm status=' + status);
  }
}

function updateFirstTokenProbe(firstTextAt, elapsed, firstSource, ftChunks, mainChunks, fullText) {
  setText('ft-source', firstSource || '-');
  setText('ft-ttft', firstTextAt === null ? '-' : ms(firstTextAt));
  setText('ft-chunks', String(ftChunks) + ' / ' + String(mainChunks));
  setText('ft-total', ms(elapsed));
  const preview = (fullText || '').slice(0, 160);
  setText('ft-preview', preview || '等待首个文本 chunk…');
  if (firstSource === 'first_token_llm') {
    setText('ft-note', '已看到 first_token_llm 参与；首包来自 opener，后续 main agent 会继续接上。');
  } else if (firstSource === 'main_agent') {
    setText('ft-note', '首包来自 main_agent；本轮尚未看到 first_token_llm，可能是关闭、超时或未产出文本。');
  } else {
    setText('ft-note', '还没有收到文本 chunk。');
  }
}

function finishFirstTokenProbe(firstTextAt, total, firstSource, ftChunks, mainChunks, fullText) {
  setText('ft-source', firstSource || '-');
  setText('ft-ttft', firstTextAt === null ? '-' : ms(firstTextAt));
  setText('ft-chunks', String(ftChunks) + ' / ' + String(mainChunks));
  setText('ft-total', ms(total));
  setText('ft-preview', (fullText || '').slice(0, 160) || '(无回复)');
  if (ftChunks > 0) {
    setText('ft-note', '本轮完成；first_token_llm 已参与并流式输出 ' + ftChunks + ' 个 chunk。');
  } else if (firstTextAt === null) {
    setText('ft-note', '本轮没有收到文本 chunk。');
  } else {
    setText('ft-note', '本轮完成；没有看到 first_token_llm chunk。');
  }
}

// session key for main agent in SimpleClaw is always main:{user_id}
function chatSessionKey() {
  const t = (document.getElementById('chat-tenant').value || '').trim() || 'admin_debug';
  return 'main:' + t;
}

function connectChatEvents() {
  const tenant = (document.getElementById('chat-tenant').value || '').trim() || 'admin_debug';
  const sessionKey = chatSessionKey();
  if (_chatEventsSource) {
    _chatEventsSource.close();
    _chatEventsSource = null;
  }
  _cronBubble = null;
  _cronText = '';
  const url = '/events/stream?tenant_key=' + encodeURIComponent(tenant)
    + '&session_key=' + encodeURIComponent(sessionKey);
  _chatEventsSource = new EventSource(url);
  _chatEventsSource.onopen = () => {
    console.debug('[chat-events] connected', {tenant, sessionKey});
  };
  _chatEventsSource.onerror = () => {
    console.debug('[chat-events] disconnected/retrying', {tenant, sessionKey});
  };
  _chatEventsSource.onmessage = e => {
    let evt;
    try { evt = JSON.parse(e.data); } catch(_) { return; }
    if (evt.source !== 'cron') return;
    if (evt.type === 'chunk' && evt.text) {
      if (!_cronBubble) {
        const row = appendMsgRow('assistant');
        _cronBubble = row.bubbleEl;
        if (row.metaEl) row.metaEl.textContent = 'cron reminder';
      }
      _cronText += evt.text;
      _cronBubble.textContent = _cronText;
      document.getElementById('chat-messages').scrollTop = 9999;
    } else if (evt.type === 'done') {
      _cronBubble = null;
      _cronText = '';
      setTimeout(() => { loadChatHistory(); refreshDynamicState(); }, 800);
    } else if (evt.type === 'error') {
      appendMsg('system', 'Cron error: ' + (evt.error || 'unknown'));
    }
  };
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const mediaInput = document.getElementById('chat-media');
  const msg = input.value.trim();
  const mediaUrl = (mediaInput.value || '').trim();
  if (!msg && !mediaUrl) return;
  input.value = '';
  mediaInput.value = '';
  const tenant = (document.getElementById('chat-tenant').value || '').trim() || 'admin_debug';

  const parts = [];
  if (msg) parts.push(msg);
  if (mediaUrl) parts.push('[图片] ' + mediaUrl);
  appendMsg('user', parts.join('\n'));
  document.getElementById('btn-send').disabled = true;
  appendMsg('system', '思考中…');
  resetFirstTokenProbe('请求已发出，等待首个文本 chunk…');

  const start = performance.now();
  let firstTextAt = null;
  let firstSource = null;
  let ftChunks = 0;
  let mainChunks = 0;

  const requestBody = { user_id: tenant, message: msg };
  if (mediaUrl) requestBody.media = [mediaUrl];

  try {
    const r = await fetch('/agent/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(requestBody),
    });
    removeLastSystemMsg();
    if (!r.ok) { appendMsg('system', 'Error: ' + (await r.text())); return; }

    const chatBox = document.getElementById('chat-messages');
    const bubbleRows = [];  // 由 syncAssistantBubbles 按需扩展
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '', text = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;
        let evt;
        try { evt = JSON.parse(raw); } catch(_) { continue; }
        const now = performance.now() - start;

        if (evt.type === 'chunk' && evt.data && evt.data.text) {
          const source = eventChunkSource(evt);
          if (source === 'first_token_llm') ftChunks += 1;
          else mainChunks += 1;
          if (firstTextAt === null) firstTextAt = now;
          if (firstSource === null) firstSource = source;
          text += evt.data.text;
          syncAssistantBubbles(chatBox, bubbleRows, text);
          updateFirstTokenProbe(firstTextAt, now, firstSource, ftChunks, mainChunks, text);
          if (bubbleRows[0]) bubbleRows[0].metaEl.textContent = firstSource + ' · TTFT ' + ms(firstTextAt);
          chatBox.scrollTop = 9999;
        } else if (evt.type === 'done') {
          const total = performance.now() - start;
          finishFirstTokenProbe(firstTextAt, total, firstSource, ftChunks, mainChunks, text);
          const last = bubbleRows[bubbleRows.length - 1];
          if (bubbleRows.length > 1 && last) {
            last.metaEl.textContent = '总耗时 ' + ms(total);
          } else if (last) {
            last.metaEl.textContent = (firstTextAt !== null ? firstSource + ' · TTFT ' + ms(firstTextAt) + '  ·  ' : '') + '总耗时 ' + ms(total);
          }
          setTimeout(() => { refreshContextHistory(); refreshDynamicState(); }, 800);
        } else if (evt.type === 'error') {
          const last = bubbleRows[bubbleRows.length - 1] || makeMsgRow(chatBox, 'assistant');
          last.bubbleEl.textContent = '(错误) ' + ((evt.data && evt.data.error) || raw);
          setText('ft-note', '本轮流式请求返回错误。');
        } else if (evt.type === 'first_token_status') {
          handleFirstTokenStatus(evt.data || {});
        }
      }
    }
    if (!text) {
      const row = bubbleRows[0] || makeMsgRow(chatBox, 'assistant');
      row.bubbleEl.textContent = '(无回复)';
      finishFirstTokenProbe(firstTextAt, performance.now() - start, firstSource, ftChunks, mainChunks, text);
    }
  } catch(e) {
    removeLastSystemMsg();
    appendMsg('system', '请求失败: ' + e.message);
    setText('ft-note', '请求失败: ' + e.message);
  } finally {
    document.getElementById('btn-send').disabled = false;
  }
}

function makeMsgRow(box, role) {
  const row = document.createElement('div');
  row.className = 'msg-row row-' + role;
  const bubbleEl = document.createElement('div');
  bubbleEl.className = 'msg msg-' + role;
  const metaEl = document.createElement('div');
  metaEl.className = 'msg-meta';
  row.appendChild(bubbleEl);
  row.appendChild(metaEl);
  box.appendChild(row);
  box.scrollTop = box.scrollHeight;
  return { bubbleEl, metaEl };
}

// 把累计文本按单个真实换行切成多个气泡，与 rows 数组同步（流式 / 历史共用）
// rows 是外部维护的 [{bubbleEl, metaEl}] 数组，本函数只新增不删除。
function syncAssistantBubbles(box, rows, fullText) {
  const normalized = String(fullText || '').replace(/\r\n/g, '\n').replace(/\n{2,}/g, '\n');
  const parts = normalized.split('\n');
  if (parts.length > 1 && parts[parts.length - 1] === '') parts.pop();
  while (rows.length < parts.length) {
    rows.push(makeMsgRow(box, 'assistant'));
  }
  for (let i = 0; i < parts.length; i++) {
    rows[i].bubbleEl.textContent = parts[i];
  }
  return rows;
}

function appendMsgRow(role) {
  return makeMsgRow(document.getElementById('chat-messages'), role);
}

function appendMsg(role, text) {
  const { bubbleEl } = appendMsgRow(role);
  bubbleEl.textContent = text;
}

function removeLastSystemMsg() {
  const box = document.getElementById('chat-messages');
  const rows = box.querySelectorAll('.row-system');
  const last = rows[rows.length - 1];
  if (last) last.remove();
}

// ── user profile panel ────────────────────────────────────────────────────
let _profileExpanded = true;

function toggleProfile() {
  _profileExpanded = !_profileExpanded;
  const body = document.getElementById('profile-body');
  const btn = document.getElementById('profile-toggle-btn');
  const section = document.getElementById('profile-section');
  body.style.display = _profileExpanded ? '' : 'none';
  btn.textContent = _profileExpanded ? '收起' : '展开';
  section.style.maxHeight = _profileExpanded ? '300px' : '36px';
}

function renderProfileField(label, content, color) {
  if (!content) return '<div style="color:#4a4a60;margin-bottom:6px;">' + label + '：<em>（空）</em></div>';
  return '<div style="margin-bottom:8px;">'
    + '<div style="color:' + color + ';font-weight:600;margin-bottom:2px;">' + label + '</div>'
    + '<pre style="margin:0;white-space:pre-wrap;word-break:break-word;color:#a0a8c0;background:#0d0d14;border-radius:4px;padding:6px;font-size:11px;">' + escHtml(content) + '</pre>'
    + '</div>';
}

function escHtml(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function refreshProfile() {
  const tenant = (document.getElementById('chat-tenant').value || '').trim();
  const body = document.getElementById('profile-body');
  if (!tenant) { body.innerHTML = '<div style="color:#4a4a60;">请先填写 Tenant Key</div>'; return; }
  body.innerHTML = '<div style="color:#4a4a60;">加载中…</div>';
  try {
    const r = await fetch('/admin/tenant/docs?tenant_key=' + encodeURIComponent(tenant));
    const data = await r.json();
    let html = '';
    if (data.journey_stage) {
      const stageCN = { novice: '新手期', explore: '探索期', mature: '成熟期' };
      const stageColor = { novice: '#fbbf24', explore: '#6ee7b7', mature: '#c8b4f8' };
      const sc = stageColor[data.journey_stage] || '#fbbf24';
      html += '<div style="margin-bottom:8px;padding:3px 8px;background:#1a1a14;border-radius:4px;border-left:2px solid ' + sc + ';">'
        + '<span style="color:' + sc + ';font-size:11px;font-weight:600;">Journey：' + (stageCN[data.journey_stage] || data.journey_stage) + '</span>'
        + '</div>';
      highlightActiveJourneyStage(data.journey_stage);
    }
    html += renderProfileField('USER.md', data.user_md, '#c8b4f8');
    if (Array.isArray(data.memory_index) && data.memory_index.length) {
      const memLines = data.memory_index.map(m => '• [' + (m.topic || m.key) + '] ' + (m.description || '')).join('\n');
      html += renderProfileField('记忆（' + data.memory_index.length + ' 条）', memLines, '#fbbf24');
    } else {
      html += '<div style="color:#4a4a60;margin-bottom:6px;">记忆：<em>（空）</em></div>';
    }
    if (data.error) html += '<div style="color:#f87171;">⚠ ' + escHtml(data.error) + '</div>';
    body.innerHTML = html;
  } catch(e) { body.innerHTML = '<div style="color:#f87171;">加载失败: ' + escHtml(e.message) + '</div>'; }
}

// ── chat history loader ───────────────────────────────────────────────────
async function loadChatHistory() {
  const tenant = (document.getElementById('chat-tenant').value || '').trim();
  if (!tenant) return;
  const sessionKey = chatSessionKey();
  try {
    const r = await fetch('/admin/session/history?tenant_key=' + encodeURIComponent(tenant)
      + '&session_key=' + encodeURIComponent(sessionKey) + '&limit=30');
    const data = await r.json();
    const box = document.getElementById('chat-messages');
    const defaultMsg = '<div class="msg-row row-system"><div class="msg msg-system">选择左侧 prompt 文件可随时编辑；对话使用当前已加载的 prompt（热加载生效）。</div></div>';
    if (!Array.isArray(data.messages) || !data.messages.length) {
      box.innerHTML = defaultMsg;
      return;
    }
    box.innerHTML = '<div class="msg-row row-system"><div class="msg msg-system">历史消息（最近 ' + data.messages.length + ' 条）</div></div>';
    const merged = [];
    for (const m of data.messages) {
      if (merged.length > 0 && m.role === 'assistant' && merged[merged.length - 1].role === 'assistant') {
        merged[merged.length - 1].content += '\n' + m.content;
      } else {
        merged.push(Object.assign({}, m));
      }
    }
    merged.forEach(m => {
      const role = m.role === 'user' ? 'user' : 'assistant';
      if (role === 'assistant') {
        // 助手消息按单个真实换行拆成多个气泡
        syncAssistantBubbles(box, [], m.content || '');
      } else {
        const { bubbleEl } = makeMsgRow(box, role);
        bubbleEl.textContent = m.content;
      }
    });
    box.scrollTop = box.scrollHeight;
  } catch(_) {}
}

// ── context history ───────────────────────────────────────────────────────
async function refreshContextHistory() {
  const tenant = (document.getElementById('chat-tenant').value || '').trim();
  const body = document.getElementById('ctx-body');
  if (!tenant) { body.innerHTML = '<div style="color:#4a4a60;">请先填写 Tenant Key</div>'; return; }
  body.innerHTML = '<div style="color:#4a4a60;">加载中…</div>';
  try {
    const sessionKey = chatSessionKey();
    const r = await fetch('/admin/session/history?tenant_key=' + encodeURIComponent(tenant)
      + '&session_key=' + encodeURIComponent(sessionKey) + '&limit=20');
    const data = await r.json();
    const msgs = data.messages || [];
    if (!msgs.length) { body.innerHTML = '<div style="color:#4a4a60;">暂无历史（本 session 尚无消息）</div>'; return; }
    body.innerHTML = '<div style="color:#6060a0;padding:4px 0 6px;font-size:10px;">' + msgs.length + ' 条消息（最近）</div>'
      + msgs.map(m => {
        const isUser = m.role === 'user';
        const color = isUser ? '#d8ccf8' : '#c8d8f0';
        const bg = isUser ? '#2d1f4e' : '#1a1f2e';
        const label = isUser ? 'U' : 'A';
        const preview = (m.content || '').substring(0, 120) + ((m.content || '').length > 120 ? '…' : '');
        return '<div style="margin-bottom:5px;padding:4px 6px;border-radius:4px;background:' + bg + ';">'
          + '<span style="font-size:9px;color:' + color + ';font-weight:700;margin-right:4px;">[' + label + ']</span>'
          + '<span style="color:#8090a8;font-size:10px;">' + escHtml(preview) + '</span>'
          + '</div>';
      }).join('');
  } catch(e) { body.innerHTML = '<div style="color:#f87171;">加载失败: ' + escHtml(e.message) + '</div>'; }
}

// ── dynamic state ─────────────────────────────────────────────────────────
async function refreshDynamicState() {
  const tenant = (document.getElementById('chat-tenant').value || '').trim();
  const injBody = document.getElementById('inj-body');
  const jobsBody = document.getElementById('jobs-body');
  if (!tenant) {
    injBody.innerHTML = '<div style="color:#4a4a60;">请先填写 Tenant Key</div>';
    jobsBody.innerHTML = '<div style="color:#4a4a60;font-size:10px;">请先填写 Tenant Key</div>';
    return;
  }
  injBody.innerHTML = '<div style="color:#4a4a60;">加载中…</div>';
  jobsBody.innerHTML = '<div style="color:#4a4a60;font-size:10px;">加载中…</div>';
  try {
    const r = await fetch('/admin/tenant/dynamic_state?tenant_key=' + encodeURIComponent(tenant));
    const data = await r.json();
    if (data.error) {
      injBody.innerHTML = '<div style="color:#f87171;">错误: ' + escHtml(data.error) + '</div>';
      return;
    }
    let injHtml = '';
    if (data.reminder_text) {
      const displayContent = data.reminder_text
        .replace(/\n*不要提及或复述这条信息。\s*$/, '')
        .trim();
      injHtml += '<div style="margin-bottom:8px;">'
        + '<div style="color:#7dd3fc;font-size:10px;font-weight:700;margin-bottom:3px;">system reminder（冒泡注入到最后一条 user 消息前）</div>'
        + '<pre style="margin:0;white-space:pre-wrap;word-break:break-word;background:#0d0d14;border-radius:4px;padding:8px;font-size:10px;color:#a0c8e0;border:1px solid #1a2a3a;">' + escHtml(displayContent) + '</pre>'
        + '</div>';
    } else {
      injHtml += '<div style="color:#4a4a60;margin-bottom:6px;font-size:10px;">话题提醒：（空，当前无 discussing/pending 话题）</div>';
    }
    // 注：原本展示的"话题状态 JSON"是 DB 原始数据，并不进 prompt，已移除以避免误解。
    injBody.innerHTML = injHtml;
  } catch(e) {
    injBody.innerHTML = '<div style="color:#f87171;">加载失败: ' + escHtml(e.message) + '</div>';
  }

  try {
    const r = await fetch('/admin/runtime/tasks?tenant_key=' + encodeURIComponent(tenant) + '&limit=12');
    const data = await r.json();
    const tasks = Array.isArray(data.tasks) ? data.tasks : [];
    if (!tasks.length) {
      jobsBody.innerHTML = '<div style="color:#4a4a60;font-size:10px;">暂无后台任务</div>';
      return;
    }
    jobsBody.innerHTML = tasks.map(t => {
      const statusColor = t.status === 'failed' ? '#f87171'
        : t.status === 'running' ? '#7dd3fc'
        : t.status === 'queued' ? '#fbbf24'
        : '#6ee7b7';
      const scopeLine = t.scope_key
        ? '<div style="color:#9090a8;font-size:10px;">scope: <span style="color:#c8b4f8;">' + escHtml(t.scope_key) + '</span></div>'
        : '<div style="color:#4a4a60;font-size:10px;">scope: （无）</div>';
      const detailLine = 'attempt ' + escHtml(String(t.attempt || 0)) + '/' + escHtml(String(t.max_attempts || 0))
        + (t.claimed_by ? ' · by ' + escHtml(String(t.claimed_by)) : '')
        + (t.queue_message_id ? ' · queue ' + escHtml(String(t.queue_message_id)) : '');
      const errorLine = t.last_error
        ? '<div style="color:#f87171;font-size:10px;margin-top:2px;">' + escHtml(String(t.last_error).slice(0, 180)) + '</div>'
        : '';
      return '<div style="padding:6px 0;border-bottom:1px solid #1a1a22;">'
        + '<div style="display:flex;align-items:center;gap:6px;">'
        + '<span style="font-size:10px;font-weight:700;color:' + statusColor + ';">' + escHtml(t.status || '?') + '</span>'
        + '<span style="font-size:11px;color:#d0d0e8;">' + escHtml(t.task_type || '?') + '</span>'
        + '</div>'
        + '<div style="color:#6060a0;font-size:10px;">stream: ' + escHtml(t.stream_name || '?') + '</div>'
        + scopeLine
        + '<div style="color:#6060a0;font-size:10px;">' + detailLine + '</div>'
        + errorLine
        + '</div>';
    }).join('');
  } catch(e) {
    jobsBody.innerHTML = '<div style="color:#f87171;font-size:10px;">任务加载失败: ' + escHtml(e.message) + '</div>';
  }
}

function toggleChatSection(sectionId, bodyId, btnId) {
  const section = document.getElementById(sectionId);
  const body = document.getElementById(bodyId);
  const btn = document.getElementById(btnId);
  const collapsed = body.style.display === 'none';
  body.style.display = collapsed ? '' : 'none';
  if (btn) btn.textContent = collapsed ? '收起' : '展开';
  section.style.maxHeight = collapsed ? '' : '36px';
}

// ── tenant list ───────────────────────────────────────────────────────────
function updateSessionLabel() {
  const sk = chatSessionKey();
  const el = document.getElementById('chat-session-label');
  if (el) el.textContent = 'session: ' + sk;
  connectChatEvents();
  refreshProfile();
  loadChatHistory();
  refreshContextHistory();
  refreshDynamicState();
}

async function loadTenantList() {
  try {
    const r = await fetch('/admin/tenants');
    const data = await r.json();
    const dl = document.getElementById('tenant-list');
    if (dl && Array.isArray(data.tenants)) {
      dl.innerHTML = data.tenants.map(t => '<option value="' + escHtml(t) + '">').join('');
      const input = document.getElementById('chat-tenant');
      if (!input.value && data.tenants.length > 0) {
        input.value = data.tenants[0];
        updateSessionLabel();
      }
    }
  } catch(_) {}
}

// ── journey stage highlight ───────────────────────────────────────────────
function highlightActiveJourneyStage(stage) {
  ['novice', 'explore', 'mature'].forEach(s => {
    document.querySelectorAll('.file-item[data-key="' + s + '"]').forEach(el => {
      if (!el.dataset.origLabel) el.dataset.origLabel = el.textContent.replace(/ ◀$/, '');
      if (s === stage) {
        el.style.borderLeftColor = '#6ee7b7';
        el.style.color = '#6ee7b7';
        el.textContent = el.dataset.origLabel + ' ◀';
      } else {
        el.style.borderLeftColor = '';
        el.style.color = '';
        el.textContent = el.dataset.origLabel;
      }
    });
  });
}

// ── main agent prompt preview ─────────────────────────────────────────────
async function previewMainAgentPrompt() {
  const tenant = (document.getElementById('chat-tenant').value || '').trim();
  if (!tenant) { alert('请先填写 Tenant Key'); return; }
  const section = document.getElementById('main-prompt-section');
  const box = document.getElementById('main-prompt-preview');
  section.style.display = '';
  box.textContent = '构建中…';
  try {
    const r = await fetch('/admin/preview/main_agent', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ tenant_key: tenant }),
    });
    const data = await r.json();
    if (data.error) { box.textContent = '错误: ' + data.error; return; }
    const header = '[stage=' + data.stage
                 + '  stable=' + data.stable_parts + '段'
                 + '  dynamic=' + data.dynamic_parts + '段'
                 + '  reminder=' + (data.has_reminder ? '有' : '无')
                 + '  tools=' + (data.tools_count || 0) + '个]\n'
                 + '─'.repeat(60) + '\n\n';
    box.textContent = header + (data.full_prompt || '(空)');
  } catch(e) { box.textContent = '请求失败: ' + e.message; }
}

// ── Tab 3: postprocess editor ─────────────────────────────────────────────
function loadPpEditor(key, data) {
  currentPpKey = key;
  document.getElementById('pp-file-title').textContent = data.label;
  const ed = document.getElementById('pp-editor');
  ed.style.display = ''; ed.value = data.content;
  document.getElementById('pp-toolbar').style.display = '';
  document.getElementById('pp-status').textContent = '';
}

async function selectFileForPp(key) {
  document.querySelectorAll('.file-item').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.file-item[data-key="' + key + '"]').forEach(el => el.classList.add('active'));
  document.getElementById('pp-status').textContent = '加载中…';
  try {
    const data = await fetchPrompt(key);
    loadPpEditor(key, data);
  } catch(e) { document.getElementById('pp-status').textContent = '加载失败: ' + e.message; }
}

async function savePpFile() {
  if (!currentPpKey) return;
  document.getElementById('pp-status').textContent = '保存中…';
  try {
    await putPrompt(currentPpKey, document.getElementById('pp-editor').value);
    document.getElementById('pp-status').textContent = '已保存';
  } catch(e) { document.getElementById('pp-status').textContent = '保存失败: ' + e.message; }
}

async function loadTenantData() {
  const tenant = document.getElementById('pp-tenant').value.trim();
  const status = document.getElementById('pp-load-status');
  if (!tenant) { status.textContent = '请先填写 tenant_key'; return; }
  status.textContent = '加载中…';
  try {
    const r = await fetch('/admin/tenant/docs?tenant_key=' + encodeURIComponent(tenant));
    const data = await r.json();
    document.getElementById('pp-user-md').value = data.user_md || '';
    status.textContent = data.user_md ? 'USER.md ✓' : '（DB 中暂无数据）';
    if (data.error) status.textContent += '  ⚠ ' + data.error;
  } catch(e) { status.textContent = '加载失败: ' + e.message; }
}

async function runPostprocessLLM() {
  const statusEl = document.getElementById('pp-run-status');
  const sectionEl = document.getElementById('pp-run-section');
  const rawEl = document.getElementById('pp-run-raw');
  const toolcallsSec = document.getElementById('pp-toolcalls-section');
  const toolcallsEl = document.getElementById('pp-toolcalls');
  const promptSec = document.getElementById('pp-prompt-section');
  const promptEl = document.getElementById('pp-input-prompt');
  statusEl.textContent = '运行中…';
  sectionEl.style.display = 'flex';
  rawEl.textContent = '';
  toolcallsSec.style.display = 'flex';   // 总是显示（空时也要明确告知"未触发"）
  toolcallsEl.textContent = '';
  const tenant = document.getElementById('pp-tenant').value.trim();
  const body = {
    tenant_key: tenant,
    user_message: document.getElementById('pp-user-msg').value,
    assistant_reply: document.getElementById('pp-agent-reply').value,
    current_user_md: document.getElementById('pp-user-md').value,
  };
  try {
    const r = await fetch('/admin/run/postprocess', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const data = await r.json();
    if (data.error) { statusEl.textContent = '错误: ' + data.error; rawEl.textContent = data.error; return; }
    statusEl.textContent = '完成';
    rawEl.textContent = data.output || '（LLM 无文本输出）';
    if (Array.isArray(data.tool_calls) && data.tool_calls.length > 0) {
      toolcallsEl.textContent = data.tool_calls.map((tc, i) => {
        const header = `# Call ${i+1}: update_doc(doc_key="${tc.doc_key || ''}")`;
        return header + '\n\n' + (tc.content || '(empty content)');
      }).join('\n\n───\n\n');
    } else {
      toolcallsEl.textContent = '（未触发工具调用 — LLM 判断本轮无需更新 USER.md）';
    }
    const fullPrompt = data.full_prompt || data.input_prompt;
    if (fullPrompt) {
      promptSec.style.display = 'flex';
      promptEl.textContent = fullPrompt;
    }
  } catch(e) { statusEl.textContent = '请求失败'; rawEl.textContent = String(e); }
}

async function saveTenantDoc(docType, docName, textareaId, btnId) {
  const tenant = document.getElementById('pp-tenant').value.trim();
  if (!tenant) { alert('请先填写 tenant_key'); return; }
  const content = document.getElementById(textareaId).value;
  const btn = document.getElementById(btnId);
  const origText = btn.textContent;
  btn.disabled = true; btn.textContent = '保存中…';
  try {
    const r = await fetch('/admin/tenant/doc', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ tenant_key: tenant, doc_name: docName, content }),
    });
    if (!r.ok) throw new Error(await r.text());
    btn.textContent = '已保存 ✓';
    setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 1500);
  } catch(e) {
    btn.textContent = '失败';
    setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 2000);
  }
}

// ── Tab 4: cold path editor ───────────────────────────────────────────────
function loadCpEditor(key, data) {
  currentCpKey = key;
  document.getElementById('cp-file-title').textContent = data.label;
  const ed = document.getElementById('cp-editor');
  ed.style.display = ''; ed.value = data.content;
  document.getElementById('cp-toolbar').style.display = '';
  document.getElementById('cp-status').textContent = '';
}

async function saveCpFile() {
  if (!currentCpKey) return;
  document.getElementById('cp-status').textContent = '保存中…';
  try {
    await putPrompt(currentCpKey, document.getElementById('cp-editor').value);
    document.getElementById('cp-status').textContent = '已保存';
  } catch(e) { document.getElementById('cp-status').textContent = '保存失败: ' + e.message; }
}

async function loadTenantDocsForCp() {
  const tenant = document.getElementById('cp-tenant').value.trim();
  const status = document.getElementById('cp-load-status');
  if (!tenant) { status.textContent = '请先填写 tenant_key'; return; }
  status.textContent = '加载中…';
  try {
    const r = await fetch('/admin/tenant/dynamic_state?tenant_key=' + encodeURIComponent(tenant));
    const data = await r.json();
    status.textContent = data.message || 'topic reminder 已停用，cold path 只抽取 obligations';
    if (data.error) status.textContent += '  ⚠ ' + data.error;
  } catch(e) { status.textContent = '加载失败: ' + e.message; }
}

async function runColdPathLLM() {
  const statusEl = document.getElementById('cp-run-status');
  const sectionEl = document.getElementById('cp-run-section');
  const rawEl = document.getElementById('cp-run-raw');
  const parsedEl = document.getElementById('cp-run-parsed');
  statusEl.textContent = '运行中…';
  sectionEl.style.display = 'flex';
  rawEl.textContent = ''; parsedEl.textContent = '';
  const body = {
    user_message: document.getElementById('cp-user-msg').value,
    assistant_reply: document.getElementById('cp-agent-reply').value,
  };
  try {
    const r = await fetch('/admin/run/cold_path', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const data = await r.json();
    if (data.error) { statusEl.textContent = '错误: ' + data.error; rawEl.textContent = data.error; return; }
    statusEl.textContent = 'prompt_len: ' + (data.prompt_len || '?');
    rawEl.textContent = data.raw || '（无输出）';
    parsedEl.textContent = data.parsed ? JSON.stringify(data.parsed, null, 2) : '（解析失败）';
  } catch(e) { statusEl.textContent = '请求失败'; rawEl.textContent = String(e); }
}

const _CHAIN_EXAMPLE = [
  {"user_message": "等图片分析好了，也给我同步一下今天的护肤计划吧", "assistant_reply": "好，等图片分析完成后，我会帮你同步今天的护肤计划。"},
  {"user_message": "只是问问肌肤日记是什么", "assistant_reply": "肌肤日记会把当天肤况和护理建议记录下来，方便你之后回看变化。"},
  {"user_message": "刚刚那个护肤计划不用生成了", "assistant_reply": "好，那我先不生成今日护肤计划。"}
];

function fillChainExample() {
  document.getElementById('cp-chain-input').value = JSON.stringify(_CHAIN_EXAMPLE, null, 2);
}

async function runColdPathChain() {
  const statusEl = document.getElementById('cp-chain-status');
  const outputEl = document.getElementById('cp-chain-output');
  outputEl.innerHTML = '';
  outputEl.style.display = 'none';
  let rounds = [];
  try {
    rounds = JSON.parse(document.getElementById('cp-chain-input').value || '[]');
  } catch(e) { statusEl.textContent = 'JSON 解析失败: ' + e.message; return; }
  if (!Array.isArray(rounds) || !rounds.length) {
    statusEl.textContent = '请先填入多轮对话数组'; return;
  }
  statusEl.textContent = '执行中… (0 / ' + rounds.length + ')';
  try {
    const r = await fetch('/admin/run/cold_path_chain', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ rounds }),
    });
    const data = await r.json();
    if (data.error) { statusEl.textContent = '错误: ' + data.error; return; }
    statusEl.textContent = '完成 ' + data.trace.length + ' 轮';
    outputEl.style.display = 'flex';
    for (const t of data.trace) {
      const roundEl = document.createElement('div');
      roundEl.style.cssText = 'border:1px solid #2a2a3a;border-radius:5px;overflow:hidden;flex-shrink:0;';
      roundEl.innerHTML = '<div style="background:#1a1422;padding:6px 10px;font-size:11px;font-weight:600;color:#c8b4f8;display:flex;align-items:center;gap:8px;cursor:pointer;"'
        + ' onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display===\'none\'?\'flex\':\'none\'">'
        + '<span>第 ' + t.round + ' 轮</span>'
        + '<span style="color:#6060a0;font-weight:400;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">用户：' + escHtml(t.user_message.substring(0, 40)) + (t.user_message.length > 40 ? '…' : '') + '</span>'
        + '</div>'
        + '<div style="display:flex;flex-direction:column;gap:6px;padding:8px 10px;">'
        + (t.error ? '<div style="color:#f87171;font-size:11px;">错误: ' + escHtml(t.error) + '</div>' : '')
        + '<div style="display:flex;flex-direction:column;gap:3px;"><span style="font-size:10px;color:#6060a0;">LLM 原始输出</span><div class="output-box" style="min-height:20px;max-height:60px;font-size:10px;">' + escHtml(t.raw || '（无）') + '</div></div>'
        + (t.parsed ? '<div style="display:flex;flex-direction:column;gap:3px;"><span style="font-size:10px;color:#6060a0;">解析结构</span><div class="output-box" style="min-height:20px;max-height:100px;font-size:10px;font-family:monospace;white-space:pre;">' + escHtml(JSON.stringify(t.parsed, null, 2)) + '</div></div>' : '')
        + '</div>';
      outputEl.appendChild(roundEl);
    }
  } catch(e) { statusEl.textContent = '请求失败: ' + e.message; }
}

// ── Tab 5: 肌肤日记子 Agent ───────────────────────────────────────────────
async function initSkinDiarySession(activate) {
  const tenant = (document.getElementById('sd-tenant').value || '').trim();
  if (!tenant) { alert('请先填写 User ID'); return; }
  const sdSessionKey = 'skin_diary:' + tenant;
  const infoEl = document.getElementById('sd-session-info');
  infoEl.innerHTML = '<div style="color:#4a4a60;">初始化中…</div>';

  // 激活进入：触发 journey 阶段升级
  if (activate) {
    try {
      const r = await fetch('/journey/event', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ tenant_key: tenant, event: 'explore_entered' }),
      });
      const data = await r.json();
      const stageCN = { novice: '新手期', explore: '探索期', mature: '成熟期' };
      const stageColor = { novice: '#fbbf24', explore: '#6ee7b7', mature: '#c8b4f8' };
      const after = data.stage_after || data.current_stage || '';
      const sc = stageColor[after] || '#6ee7b7';
      const promoted = data.promoted ? ' <span style="color:#6ee7b7;font-size:10px;">（已升级）</span>' : '';
      infoEl.innerHTML = '<div style="color:#a0a8c0;margin-bottom:4px;">session: <span style="color:#c8b4f8;word-break:break-all;">' + escHtml(sdSessionKey) + '</span></div>'
        + '<div style="margin-top:4px;padding:3px 8px;background:#1a1a14;border-radius:4px;border-left:2px solid ' + sc + ';display:inline-block;">'
        + '<span style="color:' + sc + ';font-size:11px;font-weight:600;">Journey：' + (stageCN[after] || after || '?') + '</span>' + promoted
        + '</div>';
    } catch(e) {
      infoEl.innerHTML = '<div style="color:#f87171;">Journey 事件失败: ' + escHtml(e.message) + '</div>';
    }
  } else {
    infoEl.innerHTML = '<div style="color:#a0a8c0;">session: <span style="color:#c8b4f8;word-break:break-all;">' + escHtml(sdSessionKey) + '</span></div>';
  }

  // 加载历史消息
  const box = document.getElementById('sd-messages');
  try {
    const r = await fetch('/admin/session/history?tenant_key=' + encodeURIComponent(tenant)
      + '&session_key=' + encodeURIComponent(sdSessionKey) + '&limit=30');
    const data = await r.json();
    box.innerHTML = '';
    if (!Array.isArray(data.messages) || !data.messages.length) {
      box.innerHTML = '<div class="msg-row row-system"><div class="msg msg-system">会话已就绪，暂无历史消息。</div></div>';
    } else {
      box.innerHTML = '<div class="msg-row row-system"><div class="msg msg-system">历史消息（最近 ' + data.messages.length + ' 条）</div></div>';
      const merged = [];
      for (const m of data.messages) {
        if (merged.length > 0 && m.role === 'assistant' && merged[merged.length - 1].role === 'assistant') {
          merged[merged.length - 1].content += '\n' + m.content;
        } else { merged.push(Object.assign({}, m)); }
      }
      merged.forEach(m => {
        const role = m.role === 'user' ? 'user' : 'assistant';
        if (role === 'assistant') {
          syncAssistantBubbles(box, [], m.content || '');
        } else {
          const { bubbleEl } = makeMsgRow(box, role);
          bubbleEl.textContent = m.content;
        }
      });
    }
    box.scrollTop = box.scrollHeight;
  } catch(e) {
    box.innerHTML = '<div class="msg-row row-system"><div class="msg msg-system">加载历史失败: ' + escHtml(e.message) + '</div></div>';
  }
  refreshSdProfile();
  refreshSdContextHistory();
  refreshSdDynamicState();
}

async function loadSkinDiaryResult() {
  const tenant = (document.getElementById('sd-tenant').value || '').trim();
  if (!tenant) { alert('请先填写 User ID'); return; }
  const statusEl = document.getElementById('sd-tool-status');
  const outputEl = document.getElementById('sd-tool-run-output');
  const rawEl = document.getElementById('sd-tool-raw');
  const parsedEl = document.getElementById('sd-tool-parsed');
  statusEl.textContent = '加载中…';
  outputEl.style.display = 'flex';
  rawEl.textContent = ''; parsedEl.textContent = '';
  try {
    const r = await fetch('/admin/tenant/skin_diary_result?tenant_key=' + encodeURIComponent(tenant));
    const data = await r.json();
    if (data.error) { statusEl.textContent = '错误: ' + data.error; rawEl.textContent = data.error; return; }
    if (!data.result) {
      statusEl.textContent = '无日记数据';
      rawEl.textContent = '（该用户暂无肌肤日记数据，需先完成皮肤检测）';
      return;
    }
    const res = data.result;
    statusEl.textContent = 'state=' + (res.state || '?') + (res.analyzed_at ? '  ' + res.analyzed_at : '');
    rawEl.textContent = res.summary || '（无总结）';
    parsedEl.textContent = JSON.stringify(res, null, 2);
  } catch(e) { statusEl.textContent = '请求失败'; rawEl.textContent = String(e); }
}

async function refreshSdProfile() {
  const tenant = (document.getElementById('sd-tenant').value || '').trim();
  const body = document.getElementById('sd-profile-body');
  if (!tenant) { body.innerHTML = '<div style="color:#4a4a60;">填写 user_id 后加载</div>'; return; }
  body.innerHTML = '<div style="color:#4a4a60;">加载中…</div>';
  try {
    const r = await fetch('/admin/tenant/docs?tenant_key=' + encodeURIComponent(tenant));
    const data = await r.json();
    let html = '';
    html += renderProfileField('USER.md', data.user_md, '#c8b4f8');
    if (Array.isArray(data.memory_index) && data.memory_index.length) {
      const memLines = data.memory_index.map(m => '• [' + (m.topic || m.key) + '] ' + (m.description || '')).join('\n');
      html += renderProfileField('肌肤记忆（' + data.memory_index.length + ' 条）', memLines, '#fbbf24');
    }
    body.innerHTML = html || '<div style="color:#4a4a60;">暂无数据</div>';
  } catch(e) { body.innerHTML = '<div style="color:#f87171;">加载失败: ' + escHtml(e.message) + '</div>'; }
}

async function sendSkinDiaryChat() {
  const tenant = (document.getElementById('sd-tenant').value || '').trim();
  if (!tenant) { alert('请先填写 User ID'); return; }
  const input = document.getElementById('sd-input');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  const box = document.getElementById('sd-messages');

  const userRow = document.createElement('div');
  userRow.className = 'msg-row row-user';
  const userBubble = document.createElement('div');
  userBubble.className = 'msg msg-user';
  userBubble.textContent = msg;
  userRow.appendChild(userBubble);
  box.appendChild(userRow);

  const sysRow = document.createElement('div');
  sysRow.className = 'msg-row row-system';
  sysRow.innerHTML = '<div class="msg msg-system">思考中…</div>';
  box.appendChild(sysRow);
  box.scrollTop = box.scrollHeight;
  document.getElementById('sd-send-btn').disabled = true;

  try {
    const r = await fetch('/agent/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ user_id: tenant, message: msg, session_id: 'skin_diary:' + tenant }),
    });
    sysRow.remove();
    if (!r.ok) {
      const errRow = document.createElement('div');
      errRow.className = 'msg-row row-system';
      errRow.innerHTML = '<div class="msg msg-system">Error: ' + escHtml(await r.text()) + '</div>';
      box.appendChild(errRow);
      return;
    }
    const bubbleRows = [];
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '', text = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;
        let evt;
        try { evt = JSON.parse(raw); } catch(_) { continue; }
        if (evt.type === 'chunk' && evt.data && evt.data.text) {
          text += evt.data.text;
          syncAssistantBubbles(box, bubbleRows, text);
          box.scrollTop = box.scrollHeight;
        } else if (evt.type === 'error') {
          const last = bubbleRows[bubbleRows.length - 1] || makeMsgRow(box, 'assistant');
          last.bubbleEl.textContent = '(错误) ' + ((evt.data && evt.data.error) || raw);
        }
      }
    }
    if (!text) {
      const row = bubbleRows[0] || makeMsgRow(box, 'assistant');
      row.bubbleEl.textContent = '(无回复)';
    }
  } catch(e) {
    sysRow.remove();
    const errRow = document.createElement('div');
    errRow.className = 'msg-row row-system';
    errRow.innerHTML = '<div class="msg msg-system">请求失败: ' + escHtml(e.message) + '</div>';
    box.appendChild(errRow);
  } finally {
    document.getElementById('sd-send-btn').disabled = false;
    box.scrollTop = box.scrollHeight;
    // 主 Agent 同款：发完消息后延迟刷新右侧面板
    setTimeout(() => { refreshSdContextHistory(); refreshSdDynamicState(); }, 800);
  }
}

async function previewSkinDiaryPrompt() {
  const tenant = (document.getElementById('sd-tenant').value || '').trim();
  if (!tenant) { alert('请先填写 User ID'); return; }
  const section = document.getElementById('sd-prompt-section');
  const agentBox = document.getElementById('sd-agent-prompt-preview');
  const toolBox = document.getElementById('sd-tool-prompt-preview');
  section.style.display = '';
  agentBox.textContent = '构建中…'; toolBox.textContent = '构建中…';
  try {
    const r = await fetch('/admin/preview/skin_diary_agent', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ tenant_key: tenant }),
    });
    const data = await r.json();
    if (data.error) { agentBox.textContent = '错误: ' + data.error; toolBox.textContent = ''; return; }
    const header = '[stable=' + data.stable_parts + '段'
                 + '  dynamic=' + data.dynamic_parts + '段'
                 + '  reminder=' + (data.has_reminder ? '有' : '无')
                 + '  tools=' + (data.tools_count || 0) + '个]\n'
                 + '─'.repeat(60) + '\n\n';
    agentBox.textContent = header + (data.full_prompt || '(空)');
    toolBox.textContent = '';
    toolBox.style.display = 'none';
  } catch(e) { agentBox.textContent = '请求失败: ' + e.message; toolBox.textContent = ''; }
}

// ── Tab 6: 深度报告子 Agent ───────────────────────────────────────────────
async function initDeepReportSession() {
  const tenant = (document.getElementById('dr-tenant').value || '').trim();
  if (!tenant) { alert('请先填写 User ID'); return; }
  const drSessionKey = 'deep_report:' + tenant;
  const infoEl = document.getElementById('dr-session-info');
  infoEl.innerHTML = '<div style="color:#a0a8c0;">session: <span style="color:#c8b4f8;word-break:break-all;">' + escHtml(drSessionKey) + '</span></div>';

  const box = document.getElementById('dr-messages');
  try {
    const r = await fetch('/admin/session/history?tenant_key=' + encodeURIComponent(tenant)
      + '&session_key=' + encodeURIComponent(drSessionKey) + '&limit=30');
    const data = await r.json();
    box.innerHTML = '';
    if (!Array.isArray(data.messages) || !data.messages.length) {
      box.innerHTML = '<div class="msg-row row-system"><div class="msg msg-system">会话已就绪，暂无历史消息。</div></div>';
    } else {
      box.innerHTML = '<div class="msg-row row-system"><div class="msg msg-system">历史消息（最近 ' + data.messages.length + ' 条）</div></div>';
      const merged = [];
      for (const m of data.messages) {
        if (merged.length > 0 && m.role === 'assistant' && merged[merged.length - 1].role === 'assistant') {
          merged[merged.length - 1].content += '\n' + m.content;
        } else { merged.push(Object.assign({}, m)); }
      }
      merged.forEach(m => {
        const role = m.role === 'user' ? 'user' : 'assistant';
        if (role === 'assistant') {
          syncAssistantBubbles(box, [], m.content || '');
        } else {
          const { bubbleEl } = makeMsgRow(box, role);
          bubbleEl.textContent = m.content;
        }
      });
    }
    box.scrollTop = box.scrollHeight;
  } catch(e) {
    box.innerHTML = '<div class="msg-row row-system"><div class="msg msg-system">加载历史失败: ' + escHtml(e.message) + '</div></div>';
  }
  refreshDrProfile();
  refreshDrContextHistory();
  refreshDrDynamicState();
}

async function loadDeepReportResult() {
  const tenant = (document.getElementById('dr-tenant').value || '').trim();
  if (!tenant) { alert('请先填写 User ID'); return; }
  const reportId = (document.getElementById('dr-report-id').value || '').trim();
  const statusEl = document.getElementById('dr-tool-status');
  const outputEl = document.getElementById('dr-tool-run-output');
  const summaryEl = document.getElementById('dr-tool-summary');
  const parsedEl = document.getElementById('dr-tool-parsed');
  // 同步动态副标题，让用户一眼看到正在直读哪份
  const subEl = document.getElementById('dr-tool-subtitle');
  if (subEl) {
    subEl.textContent = reportId
      ? 'V2 三表 JOIN，按 report_id=' + reportId + ' 命中（不命中返回空）'
      : 'V2 三表 JOIN，按 user_id 取最新一条 status=done';
  }
  statusEl.textContent = '加载中…';
  outputEl.style.display = 'flex';
  summaryEl.textContent = ''; parsedEl.textContent = '';
  try {
    let url = '/admin/tenant/deep_report?tenant_key=' + encodeURIComponent(tenant);
    if (reportId) url += '&report_id=' + encodeURIComponent(reportId);
    const r = await fetch(url);
    const data = await r.json();
    if (data.error) { statusEl.textContent = '错误: ' + data.error; summaryEl.textContent = data.error; return; }
    if (!data.result) {
      statusEl.textContent = '无报告数据 [' + (data.source || '') + ']';
      summaryEl.textContent = reportId
        ? '（按 report_id=' + reportId + ' 未在本租户命中——可能不存在 / 跨用户 / 已删除）'
        : '（该用户暂无深度报告，需先通过外部服务生成）';
      return;
    }
    const res = data.result;
    statusEl.textContent = 'status=' + (res.status || '?')
                        + (res.create_time ? '  ' + res.create_time : '')
                        + (res.report_id ? '  rid=' + res.report_id : '')
                        + '  [' + (data.source || '') + ']';
    summaryEl.textContent = data.formatted || '（无格式化摘要）';
    parsedEl.textContent = JSON.stringify(res, null, 2);
  } catch(e) { statusEl.textContent = '请求失败'; summaryEl.textContent = String(e); }
}

async function refreshDrProfile() {
  const tenant = (document.getElementById('dr-tenant').value || '').trim();
  const body = document.getElementById('dr-profile-body');
  if (!tenant) { body.innerHTML = '<div style="color:#4a4a60;">填写 user_id 后加载</div>'; return; }
  body.innerHTML = '<div style="color:#4a4a60;">加载中…</div>';
  try {
    const r = await fetch('/admin/tenant/docs?tenant_key=' + encodeURIComponent(tenant));
    const data = await r.json();
    let html = '';
    html += renderProfileField('USER.md', data.user_md, '#c8b4f8');
    if (Array.isArray(data.memory_index) && data.memory_index.length) {
      const memLines = data.memory_index.map(m => '• [' + (m.topic || m.key) + '] ' + (m.description || '')).join('\n');
      html += renderProfileField('长期记忆（' + data.memory_index.length + ' 条）', memLines, '#fbbf24');
    }
    body.innerHTML = html || '<div style="color:#4a4a60;">暂无数据</div>';
  } catch(e) { body.innerHTML = '<div style="color:#f87171;">加载失败: ' + escHtml(e.message) + '</div>'; }
}

async function loadDeepReportList() {
  const tenant = (document.getElementById('dr-tenant').value || '').trim();
  if (!tenant) { alert('请先填写 User ID'); return; }
  try {
    const r = await fetch('/admin/tenant/deep_report_list?tenant_key=' + encodeURIComponent(tenant) + '&limit=20');
    const data = await r.json();
    if (data.error) { alert('加载失败: ' + data.error); return; }
    const list = Array.isArray(data.results) ? data.results : [];
    if (list.length === 0) {
      alert('该用户没有完成态报告（status=done）');
      return;
    }
    const lines = list.map(r => `${r.report_id}  ·  ${r.create_time || ''}  ·  ${(r.summary || '').slice(0, 40)}`);
    const picked = prompt('选择 reportId 复制到输入框（Cancel 不变）：\n\n' + lines.join('\n'), list[0].report_id);
    if (picked) document.getElementById('dr-report-id').value = picked.trim();
  } catch (e) { alert('加载失败: ' + e.message); }
}

async function sendDeepReportChat() {
  const tenant = (document.getElementById('dr-tenant').value || '').trim();
  if (!tenant) { alert('请先填写 User ID'); return; }
  const reportId = (document.getElementById('dr-report-id').value || '').trim();
  const input = document.getElementById('dr-input');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  const box = document.getElementById('dr-messages');

  const userRow = document.createElement('div');
  userRow.className = 'msg-row row-user';
  const userBubble = document.createElement('div');
  userBubble.className = 'msg msg-user';
  userBubble.textContent = msg + (reportId ? '  [report_id=' + reportId + ']' : '  [latest fallback]');
  userRow.appendChild(userBubble);
  box.appendChild(userRow);

  const sysRow = document.createElement('div');
  sysRow.className = 'msg-row row-system';
  sysRow.innerHTML = '<div class="msg msg-system">思考中…</div>';
  box.appendChild(sysRow);
  box.scrollTop = box.scrollHeight;
  document.getElementById('dr-send-btn').disabled = true;

  try {
    const payload = { user_id: tenant, message: msg, session_id: 'deep_report:' + tenant };
    if (reportId) payload.report_id = reportId;
    const r = await fetch('/agent/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    sysRow.remove();
    if (!r.ok) {
      const errRow = document.createElement('div');
      errRow.className = 'msg-row row-system';
      errRow.innerHTML = '<div class="msg msg-system">Error: ' + escHtml(await r.text()) + '</div>';
      box.appendChild(errRow);
      return;
    }
    const bubbleRows = [];
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '', text = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;
        let evt;
        try { evt = JSON.parse(raw); } catch(_) { continue; }
        if (evt.type === 'chunk' && evt.data && evt.data.text) {
          text += evt.data.text;
          syncAssistantBubbles(box, bubbleRows, text);
          box.scrollTop = box.scrollHeight;
        } else if (evt.type === 'error') {
          const last = bubbleRows[bubbleRows.length - 1] || makeMsgRow(box, 'assistant');
          last.bubbleEl.textContent = '(错误) ' + ((evt.data && evt.data.error) || raw);
        }
      }
    }
    if (!text) {
      const row = bubbleRows[0] || makeMsgRow(box, 'assistant');
      row.bubbleEl.textContent = '(无回复)';
    }
  } catch(e) {
    sysRow.remove();
    const errRow = document.createElement('div');
    errRow.className = 'msg-row row-system';
    errRow.innerHTML = '<div class="msg msg-system">请求失败: ' + escHtml(e.message) + '</div>';
    box.appendChild(errRow);
  } finally {
    document.getElementById('dr-send-btn').disabled = false;
    box.scrollTop = box.scrollHeight;
    setTimeout(() => { refreshDrContextHistory(); refreshDrDynamicState(); }, 800);
  }
}

async function previewDeepReportPrompt() {
  const tenant = (document.getElementById('dr-tenant').value || '').trim();
  if (!tenant) { alert('请先填写 User ID'); return; }
  const reportId = (document.getElementById('dr-report-id').value || '').trim();
  const section = document.getElementById('dr-prompt-section');
  const agentBox = document.getElementById('dr-agent-prompt-preview');
  section.style.display = '';
  agentBox.textContent = '构建中…';
  try {
    const payload = { tenant_key: tenant };
    if (reportId) payload.report_id = reportId;
    const r = await fetch('/admin/preview/deep_report_agent', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await r.json();
    if (data.error) { agentBox.textContent = '错误: ' + data.error; return; }
    const ridLabel = reportId ? '  report_id=' + reportId : '  [latest fallback]';
    const header = '[stable=' + data.stable_parts + '段'
                 + '  dynamic=' + data.dynamic_parts + '段'
                 + '  reminder=' + (data.has_reminder ? '有' : '无')
                 + '  tools=' + (data.tools_count || 0) + '个'
                 + ridLabel + ']\n'
                 + '─'.repeat(60) + '\n\n';
    agentBox.textContent = header + (data.full_prompt || '(空)');
  } catch(e) { agentBox.textContent = '请求失败: ' + e.message; }
}

// ── 子 Agent 共用的右侧面板渲染（历史上下文 / 指令跟随 / 后台任务） ──
async function _refreshSubagentContext(opts) {
  const { tenant, sessionKey, ctxBodyId } = opts;
  const body = document.getElementById(ctxBodyId);
  if (!tenant) { body.innerHTML = '<div style="color:#4a4a60;">请先填写 User ID</div>'; return; }
  body.innerHTML = '<div style="color:#4a4a60;">加载中…</div>';
  try {
    const r = await fetch('/admin/session/history?tenant_key=' + encodeURIComponent(tenant)
      + '&session_key=' + encodeURIComponent(sessionKey) + '&limit=20');
    const data = await r.json();
    const msgs = data.messages || [];
    if (!msgs.length) { body.innerHTML = '<div style="color:#4a4a60;">暂无历史（本 session 尚无消息）</div>'; return; }
    body.innerHTML = '<div style="color:#6060a0;padding:4px 0 6px;font-size:10px;">' + msgs.length + ' 条消息（最近）</div>'
      + msgs.map(m => {
        const isUser = m.role === 'user';
        const color = isUser ? '#d8ccf8' : '#c8d8f0';
        const bg = isUser ? '#2d1f4e' : '#1a1f2e';
        const label = isUser ? 'U' : 'A';
        const preview = (m.content || '').substring(0, 120) + ((m.content || '').length > 120 ? '…' : '');
        return '<div style="margin-bottom:5px;padding:4px 6px;border-radius:4px;background:' + bg + ';">'
          + '<span style="font-size:9px;color:' + color + ';font-weight:700;margin-right:4px;">[' + label + ']</span>'
          + '<span style="color:#8090a8;font-size:10px;">' + escHtml(preview) + '</span>'
          + '</div>';
      }).join('');
  } catch(e) { body.innerHTML = '<div style="color:#f87171;">加载失败: ' + escHtml(e.message) + '</div>'; }
}

async function _refreshSubagentDynamicState(opts) {
  const { tenant, topicKey, injBodyId, jobsBodyId } = opts;
  const injBody = document.getElementById(injBodyId);
  const jobsBody = document.getElementById(jobsBodyId);
  if (!tenant) {
    injBody.innerHTML = '<div style="color:#4a4a60;">请先填写 User ID</div>';
    jobsBody.innerHTML = '<div style="color:#4a4a60;font-size:10px;">请先填写 User ID</div>';
    return;
  }
  injBody.innerHTML = '<div style="color:#4a4a60;">加载中…</div>';
  jobsBody.innerHTML = '<div style="color:#4a4a60;font-size:10px;">加载中…</div>';
  try {
    const r = await fetch('/admin/tenant/dynamic_state?tenant_key=' + encodeURIComponent(tenant)
      + '&topic_key=' + encodeURIComponent(topicKey));
    const data = await r.json();
    if (data.error) {
      injBody.innerHTML = '<div style="color:#f87171;">错误: ' + escHtml(data.error) + '</div>';
    } else if (data.reminder_text) {
      const displayContent = data.reminder_text.replace(/\n*不要提及或复述这条信息。\s*$/, '').trim();
      injBody.innerHTML = '<div style="margin-bottom:8px;">'
        + '<div style="color:#7dd3fc;font-size:10px;font-weight:700;margin-bottom:3px;">system reminder（topic_key=' + escHtml(topicKey) + '）</div>'
        + '<pre style="margin:0;white-space:pre-wrap;word-break:break-word;background:#0d0d14;border-radius:4px;padding:8px;font-size:10px;color:#a0c8e0;border:1px solid #1a2a3a;">' + escHtml(displayContent) + '</pre>'
        + '</div>';
    } else {
      injBody.innerHTML = '<div style="color:#4a4a60;font-size:10px;">话题提醒：（空，当前无 discussing/pending 话题）</div>';
    }
  } catch(e) {
    injBody.innerHTML = '<div style="color:#f87171;">加载失败: ' + escHtml(e.message) + '</div>';
  }

  try {
    const r = await fetch('/admin/runtime/tasks?tenant_key=' + encodeURIComponent(tenant) + '&limit=12');
    const data = await r.json();
    const tasks = Array.isArray(data.tasks) ? data.tasks : [];
    if (!tasks.length) {
      jobsBody.innerHTML = '<div style="color:#4a4a60;font-size:10px;">暂无后台任务</div>';
      return;
    }
    jobsBody.innerHTML = tasks.map(t => {
      const statusColor = t.status === 'failed' ? '#f87171'
        : t.status === 'running' ? '#7dd3fc'
        : t.status === 'queued' ? '#fbbf24'
        : t.status === 'triggered' ? '#a78bfa'
        : '#6ee7b7';
      const scopeLine = t.scope_key
        ? '<div style="color:#9090a8;font-size:10px;">scope: <span style="color:#c8b4f8;">' + escHtml(t.scope_key) + '</span></div>'
        : '<div style="color:#4a4a60;font-size:10px;">scope: （无）</div>';
      const detailLine = 'attempt ' + escHtml(String(t.attempt || 0)) + '/' + escHtml(String(t.max_attempts || 0))
        + (t.claimed_by ? ' · by ' + escHtml(String(t.claimed_by)) : '');
      const errorLine = t.last_error
        ? '<div style="color:#f87171;font-size:10px;margin-top:2px;">' + escHtml(String(t.last_error).slice(0, 180)) + '</div>'
        : '';
      return '<div style="padding:6px 0;border-bottom:1px solid #1a1a22;">'
        + '<div style="display:flex;align-items:center;gap:6px;">'
        + '<span style="font-size:10px;font-weight:700;color:' + statusColor + ';">' + escHtml(t.status || '?') + '</span>'
        + '<span style="font-size:11px;color:#d0d0e8;">' + escHtml(t.task_type || '?') + '</span>'
        + '</div>'
        + '<div style="color:#6060a0;font-size:10px;">stream: ' + escHtml(t.stream_name || '?') + '</div>'
        + scopeLine
        + '<div style="color:#6060a0;font-size:10px;">' + detailLine + '</div>'
        + errorLine
        + '</div>';
    }).join('');
  } catch(e) {
    jobsBody.innerHTML = '<div style="color:#f87171;font-size:10px;">任务加载失败: ' + escHtml(e.message) + '</div>';
  }
}

async function refreshSdContextHistory() {
  const tenant = (document.getElementById('sd-tenant').value || '').trim();
  return _refreshSubagentContext({
    tenant,
    sessionKey: 'skin_diary:' + tenant,
    ctxBodyId: 'sd-ctx-body',
  });
}

async function refreshSdDynamicState() {
  const tenant = (document.getElementById('sd-tenant').value || '').trim();
  return _refreshSubagentDynamicState({
    tenant,
    topicKey: 'skin_diary:' + tenant,
    injBodyId: 'sd-inj-body',
    jobsBodyId: 'sd-jobs-body',
  });
}

async function refreshDrContextHistory() {
  const tenant = (document.getElementById('dr-tenant').value || '').trim();
  return _refreshSubagentContext({
    tenant,
    sessionKey: 'deep_report:' + tenant,
    ctxBodyId: 'dr-ctx-body',
  });
}

async function refreshDrDynamicState() {
  const tenant = (document.getElementById('dr-tenant').value || '').trim();
  return _refreshSubagentDynamicState({
    tenant,
    topicKey: 'deep_report:' + tenant,
    injBodyId: 'dr-inj-body',
    jobsBodyId: 'dr-jobs-body',
  });
}

// ── keyboard shortcuts ────────────────────────────────────────────────────
document.addEventListener('keydown', function(e) {
  const active = document.activeElement;
  if (active && active.tagName === 'TEXTAREA') {
    if (e.key === 'Tab' && active.id !== 'chat-input' && active.id !== 'sd-input' && active.id !== 'dr-input') {
      e.preventDefault();
      const s = active.selectionStart, end = active.selectionEnd;
      active.value = active.value.substring(0, s) + '    ' + active.value.substring(end);
      active.selectionStart = active.selectionEnd = s + 4;
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
      e.preventDefault();
      if (active.id === 'editor') saveFile();
      else if (active.id === 'pp-editor') savePpFile();
      else if (active.id === 'cp-editor') saveCpFile();
    }
  }
});

// ── DOMContentLoaded ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('chat-input').addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); sendChat(); }
  });
  document.getElementById('chat-media').addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); sendChat(); }
  });
  document.getElementById('sd-input').addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); sendSkinDiaryChat(); }
  });
  document.getElementById('dr-input').addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); sendDeepReportChat(); }
  });
  document.getElementById('chat-tenant').addEventListener('input', updateSessionLabel);
  document.getElementById('chat-tenant').addEventListener('change', updateSessionLabel);
  loadTenantList();
  updateSessionLabel();

  // auto-sync tenant when switching tabs
  document.querySelectorAll('.tab-btn').forEach((btn, i) => {
    btn.addEventListener('click', () => {
      const chatTenant = (document.getElementById('chat-tenant').value || '').trim();
      if (i === 2) {  // postprocess tab
        const ppEl = document.getElementById('pp-tenant');
        if (!ppEl.value && chatTenant) ppEl.value = chatTenant;
        if (!currentPpKey) {
          const first = document.querySelector('#tab-postprocess .file-item');
          if (first) selectFileForPp(first.dataset.key);
        }
      }
      if (i === 3) {  // coldpath tab
        const cpEl = document.getElementById('cp-tenant');
        if (!cpEl.value && chatTenant) cpEl.value = chatTenant;
        if (!currentCpKey) {
          const first = document.querySelector('#tab-coldpath .file-item');
          if (first) {
            first.classList.add('active');
            fetchPrompt(first.dataset.key).then(data => loadCpEditor(first.dataset.key, data));
          }
        }
      }
      if (i === 4) {  // skindiary tab
        const sdEl = document.getElementById('sd-tenant');
        if (!sdEl.value && chatTenant) sdEl.value = chatTenant;
      }
      if (i === 5) {  // deepreport tab
        const drEl = document.getElementById('dr-tenant');
        if (!drEl.value && chatTenant) drEl.value = chatTenant;
      }
    });
  });
});
</script>
</body>
</html>
"""

HTML_PAGE: str = (
    _HTML_TEMPLATE
    .replace("__SIDEBAR_ALL__",        _SIDEBAR_ALL)
    .replace("__SIDEBAR_AGENT__",      _SIDEBAR_AGENT)
    .replace("__SIDEBAR_POSTPROCESS__", _SIDEBAR_POSTPROCESS)
    .replace("__SIDEBAR_COLDPATH__",   _SIDEBAR_COLDPATH)
    .replace("__SIDEBAR_SKINDIARY__",  _SIDEBAR_SKINDIARY)
    .replace("__SIDEBAR_DEEPREPORT__", _SIDEBAR_DEEPREPORT)
)
