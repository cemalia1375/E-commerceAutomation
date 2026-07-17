"""Scenario replay admin page."""

from __future__ import annotations


SCENARIO_HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mojing Agent Replay Lab</title>
<style>
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: #101114;
  color: #e6e8ee;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
header {
  height: 54px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  background: #191b21;
  border-bottom: 1px solid #2b2f3a;
}
h1 { margin: 0; font-size: 16px; color: #c8b4f8; }
button {
  border: 0;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 13px;
  cursor: pointer;
  color: #fff;
  background: #2d3442;
}
button.primary { background: #7c3aed; }
button.secondary { background: #2563eb; }
button.ghost { background: #262b35; color: #b9c0d0; }
button.danger { background: #9f1d1d; }
button:disabled { opacity: .45; cursor: not-allowed; }
input, textarea, select {
  width: 100%;
  border: 1px solid #303646;
  border-radius: 6px;
  background: #101218;
  color: #e6e8ee;
  padding: 8px 9px;
  font-size: 13px;
  outline: none;
}
textarea {
  min-height: 76px;
  resize: vertical;
  line-height: 1.55;
}
label { display: block; margin: 0 0 5px; font-size: 12px; color: #7d8598; }
pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.55;
  color: #b7c0d4;
}
.toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.shell { display: grid; grid-template-rows: auto minmax(0, 1fr); height: calc(100vh - 54px); }
.config {
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  border-bottom: 1px solid #2b2f3a;
  background: #14161b;
}
.config-fields {
  display: grid;
  grid-template-columns: 150px minmax(300px, 1.35fr) 230px 210px 180px 110px;
  gap: 12px;
  align-items: end;
}
.config-device {
  display: grid;
  grid-template-columns: 190px 190px minmax(0, 1fr);
  gap: 12px;
  align-items: end;
}
.config-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  align-items: center;
}
.config-actions button { min-width: 90px; }
.device-only { display: none; }
.workspace {
  display: grid;
  grid-template-columns: 520px minmax(0, 1fr);
  min-height: 0;
}
.left, .right { overflow: auto; padding: 16px; }
.left { border-right: 1px solid #2b2f3a; background: #14161b; }
.section, .turn-card {
  border: 1px solid #2b2f3a;
  background: #181b22;
  border-radius: 8px;
}
.section { padding: 14px; margin-bottom: 14px; }
.section h2 { margin: 0 0 10px; font-size: 13px; color: #9da6ba; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.turn-list { display: grid; gap: 10px; }
.turn-input {
  border: 1px solid #303646;
  border-radius: 8px;
  padding: 10px;
  background: #11141a;
}
.turn-input-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font-size: 12px;
  color: #7d8598;
}
.turn-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 82px;
  gap: 8px;
  align-items: end;
}
.media-input {
  margin-top: 8px;
  font-family: "JetBrains Mono", "Fira Code", monospace;
  font-size: 12px;
}
.annotation-panel {
  margin-top: 9px;
  border-color: #283044;
  background: #0f131a;
}
.annotation-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 110px;
  gap: 8px;
  margin-bottom: 8px;
}
.annotation-panel textarea {
  min-height: 68px;
  margin-bottom: 8px;
}
.score-card-output {
  font-family: "JetBrains Mono", "Fira Code", monospace;
  min-height: 120px;
}
.small { font-size: 11px; color: #737d91; line-height: 1.5; }
.status { font-size: 12px; color: #8b95a8; }
.annotation-summary {
  margin-bottom: 10px;
  padding: 10px 12px;
  border: 1px solid #2a3140;
  border-radius: 8px;
  background: #11151d;
  color: #aab3c5;
  font-size: 13px;
  line-height: 1.6;
}
.annotation-summary b { color: #edf2ff; }
.annotation-summary .turn-links { color: #d4bcff; word-break: break-word; }
.summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.metric {
  border: 1px solid #2b2f3a;
  background: #181b22;
  border-radius: 8px;
  padding: 11px;
}
.metric .k { font-size: 11px; color: #778196; text-transform: uppercase; }
.metric .v { margin-top: 5px; font-size: 15px; color: #dfe6f3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.turn-card { overflow: hidden; margin-bottom: 12px; }
.turn-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: #20242d;
  border-bottom: 1px solid #2b2f3a;
  font-size: 13px;
}
.badge { color: #b9f2c8; }
.badge.fail { color: #ffb4b4; }
.turn-body { padding: 12px; display: grid; gap: 10px; }
.block {
  border: 1px solid #2c3342;
  background: #11141a;
  border-radius: 7px;
  padding: 10px;
}
.block h3 { margin: 0 0 7px; font-size: 12px; color: #8d98ae; }
.text {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
  font-size: 13px;
}
.kv {
  display: grid;
  grid-template-columns: 118px minmax(0, 1fr);
  gap: 5px 10px;
  font-family: "JetBrains Mono", "Fira Code", monospace;
  font-size: 12px;
}
.kv div:nth-child(odd) { color: #758197; }
.kv2 {
  display: grid;
  grid-template-columns: 86px minmax(0, 1fr);
  gap: 8px 10px;
  align-items: start;
  padding-top: 8px;
}
.kv2 > div { color: #758197; font-size: 12px; }
.list { display: grid; gap: 6px; font-family: "JetBrains Mono", "Fira Code", monospace; font-size: 12px; }
.item {
  padding: 7px 8px;
  border-radius: 6px;
  background: #0d1016;
  border: 1px solid #252b38;
  white-space: pre-wrap;
  word-break: break-word;
}
.preview-grid {
  display: grid;
  grid-template-columns: 70px minmax(0, 1fr) 130px minmax(180px, .65fr);
  gap: 8px;
  align-items: start;
}
.preview-head {
  color: #778196;
  font-size: 11px;
  text-transform: uppercase;
}
.preview-cell {
  border-top: 1px solid #252b38;
  padding-top: 8px;
  min-width: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.empty { color: #606b7d; font-style: italic; font-size: 12px; }
details {
  border: 1px solid #2c3342;
  border-radius: 7px;
  background: #101319;
  padding: 8px 10px;
}
summary { cursor: pointer; color: #aeb8ca; font-size: 12px; }
details > div { margin-top: 9px; }
.log-panel {
  border-color: #2b3140;
  background: #12161d;
}
.log-panel > summary {
  color: #8f9ab0;
}
.check-panel {
  margin-top: 10px;
  border: 1px solid #31405c;
  border-radius: 6px;
  background: #101723;
  padding: 10px;
}
.check-panel.pass { border-color: #1f6f4a; }
.check-panel.warn { border-color: #9a6a1c; }
.check-panel.fail { border-color: #9b2c2c; }
.check-line {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  color: #dbe4f5;
  font-size: 13px;
  margin-bottom: 8px;
}
.check-badge {
  border-radius: 999px;
  padding: 3px 8px;
  background: #253044;
  color: #e7edf8;
  font-size: 12px;
}
.check-badge.pass { background: #14532d; }
.check-badge.warn { background: #713f12; }
.check-badge.fail { background: #7f1d1d; }
.check-summary-list { margin-top: 8px; color: #aab3c5; font-size: 12px; line-height: 1.6; }
.log-body {
  display: grid;
  gap: 10px;
}
.log-group {
  border-top: 1px solid #252b38;
  padding-top: 10px;
}
.log-group h3 {
  margin: 0 0 7px;
  font-size: 12px;
  color: #8d98ae;
}
.log-item {
  background: #0d1016;
  padding: 7px 8px;
}
input[type="file"] { padding: 7px; }
@media (max-width: 1500px) {
  .config-fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .config-device { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .config-actions { justify-content: flex-start; }
  .workspace { grid-template-columns: 460px minmax(0, 1fr); }
  .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
</head>
<body>
<header>
  <h1>Mojing Agent Replay Lab</h1>
  <div class="toolbar">
    <button class="ghost" onclick="loadExample()">示例</button>
    <button class="ghost" onclick="location.href='/admin/editor'">Prompt Admin</button>
    <button class="ghost" onclick="location.href='/admin/lab'">Lab</button>
  </div>
</header>
<div class="shell">
  <div class="config">
    <div class="config-fields">
      <div>
        <label>源 Tenant</label>
        <input id="source-tenant" placeholder="395">
      </div>
      <div>
        <label>源 Session Key</label>
        <input id="source-session" placeholder="main:session_395_1780476981576_T3X2ls">
      </div>
      <div>
        <label>测试入口</label>
        <select id="replay-surface" onchange="toggleReplaySurface()">
          <option value="app_chat">App /agent/chat</option>
          <option value="v1_device">硬件 V1 /v1/chat/completions</option>
        </select>
      </div>
      <div>
        <label>快照时间（取该时间前）</label>
        <input id="snapshot-at" placeholder="2026/6/10 0:59:24">
      </div>
      <div>
        <label>测试 Tenant（可空）</label>
        <input id="test-tenant" placeholder="留空自动 test_xxx">
      </div>
      <div>
        <label>默认等待秒</label>
        <input id="default-wait" type="number" min="0" max="600" value="0">
      </div>
    </div>
    <div class="config-device device-only">
      <div>
        <label>Device ID</label>
        <input id="device-id" placeholder="可空">
      </div>
      <div>
        <label>Device Code</label>
        <input id="device-code" placeholder="可空">
      </div>
    </div>
    <div class="config-actions">
      <button class="ghost" onclick="previewSnapshot()">预览快照</button>
      <button id="run" class="primary" onclick="runReplay()">运行</button>
      <button id="check" class="secondary" onclick="runCheck()" disabled>开始 Check</button>
      <button class="secondary" onclick="downloadExcel()" id="download" disabled>导出 Excel</button>
    </div>
  </div>
  <div class="workspace">
    <div class="left">
      <div class="section">
        <h2>导入产品测试样本</h2>
        <div class="grid2">
          <div>
            <label>Excel 文件</label>
            <input id="xlsx-file" type="file" accept=".xlsx,.xlsm">
          </div>
          <div class="toolbar" style="align-items:end;">
            <button class="secondary" onclick="importXlsx()">导入 Excel</button>
            <button class="ghost" onclick="parseBulkText()">解析粘贴文本</button>
            <button class="ghost" onclick="restoreDraft()">恢复草稿</button>
          </div>
        </div>
        <label style="margin-top:10px;">批量粘贴</label>
        <textarea id="bulk-text" placeholder="一行一轮；也支持 tab 分隔：发生时间[TAB]用户消息[TAB]图片URL[TAB]等待秒"></textarea>
        <div class="small">导入后会自动填充下方可编辑消息列表；Excel 可加「标注类型」「产品反馈」两列。页面会自动保存草稿。</div>
      </div>

      <div class="section">
        <div class="toolbar" style="justify-content:space-between;margin-bottom:10px;">
          <h2 style="margin:0;">主 Agent 回放消息（可编辑）</h2>
          <div class="toolbar">
            <button class="secondary" onclick="generateImproveCards()">批量生成提升评分卡</button>
            <button class="ghost" onclick="addTurn()">加一轮</button>
            <button class="danger" onclick="clearTurns()">清空</button>
          </div>
        </div>
        <div id="annotation-summary" class="annotation-summary">评分标注概览：尚未导入或编辑。</div>
        <div id="turns" class="turn-list"></div>
        <div class="toolbar" style="margin-top:12px;">
          <span id="status" class="status"></span>
        </div>
      </div>

      <div class="section">
        <h2>Seed 补充 JSON（可选）</h2>
        <textarea id="extra-seed" spellcheck="false" placeholder='{"docs":{"USER.md":"..."}}'></textarea>
        <div class="small">页面会自动构造 seed.from_snapshot；这里仅用于追加或覆盖手写 seed。</div>
      </div>
    </div>

    <div class="right">
      <div id="results">
        <div class="section">
          <h2>结果</h2>
          <div class="empty">运行后展示每轮用户输入、Agent 回复、工具调用、runtime task、持久化会话和 prompt capture。</div>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
let lastReport = null;
let importedMeta = null;
let draftTimer = null;
const DRAFT_KEY = 'mojing_replay_lab_draft_v1';

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function turnElements() {
  return [...document.querySelectorAll('#turns .turn-input')];
}

function addTurn(user = '', media = [], waitAfter = null, occurredAt = '', baselineReply = '', reviewMode = 'stable', productFeedback = '', scoreCard = '', reviewWeight = '1') {
  const box = document.getElementById('turns');
  const idx = box.children.length + 1;
  if (idx > 200) return alert('最多 200 轮');
  const wait = waitAfter === null || waitAfter === undefined || waitAfter === ''
    ? document.getElementById('default-wait').value
    : waitAfter;
  const mediaText = Array.isArray(media) ? media.join('\n') : String(media || '');
  const div = document.createElement('div');
  div.className = 'turn-input';
  div.innerHTML = `
    <div class="turn-input-head">
      <span>Turn ${idx}${occurredAt ? ' · ' + esc(occurredAt) : ''}</span>
      <button class="ghost" onclick="this.closest('.turn-input').remove(); renumberTurns()">删除</button>
    </div>
    <div class="turn-row">
      <div>
        <label>User Query</label>
        <textarea class="turn-user" placeholder="输入用户 query">${esc(user)}</textarea>
      </div>
      <div>
        <label>等待秒</label>
        <input class="turn-wait" type="number" min="0" max="600" value="${esc(wait)}">
      </div>
    </div>
    <input class="media-input turn-media" placeholder="图片 URL，可多行或逗号分隔" value="${esc(mediaText)}">
    <details class="annotation-panel">
      <summary>评分标注（可选）</summary>
      <div>
        <div class="annotation-grid">
          <div>
            <label>标注类型</label>
            <select class="turn-review-mode">
              <option value="none">不计分</option>
              <option value="stable">保持稳定</option>
              <option value="improve">需要改进</option>
            </select>
          </div>
          <div>
            <label>权重</label>
            <select class="turn-review-weight">
              <option value="1">普通 1.0</option>
              <option value="1.5">重要 1.5</option>
              <option value="2">关键 2.0</option>
            </select>
          </div>
        </div>
        <label>产品测试 Agent 原回复 / baseline_reply</label>
        <textarea class="turn-baseline-reply" placeholder="Excel 里的 agent 原输出；好回复保持稳定时直接作为 baseline">${esc(baselineReply)}</textarea>
        <label>产品反馈（需要改进时填写）</label>
        <textarea class="turn-product-feedback" placeholder="例如：这里不应该说已经完成；应该先说明正在调研。"></textarea>
        <div class="toolbar" style="margin-bottom:8px;">
          <button class="ghost turn-generate-card" onclick="generateScoreCard(this)">生成评分卡</button>
          <span class="small turn-card-status"></span>
        </div>
        <label>评分卡 JSON</label>
        <textarea class="turn-score-card score-card-output" spellcheck="false" placeholder="点击生成评分卡后写入，可手动调整。"></textarea>
      </div>
    </details>
    <input class="turn-time" type="hidden" value="${esc(occurredAt)}">
  `;
  box.appendChild(div);
  div.querySelector('.turn-review-mode').value = reviewMode || 'stable';
  div.querySelector('.turn-review-weight').value = String(reviewWeight || '1');
  div.querySelector('.turn-product-feedback').value = productFeedback || '';
  div.querySelector('.turn-score-card').value = scoreCard || '';
  div.addEventListener('input', onTurnEdited);
  div.addEventListener('change', onTurnEdited);
  scheduleDraftSave();
  renderAnnotationSummary();
}

function renumberTurns() {
  turnElements().forEach((el, i) => {
    const time = el.querySelector('.turn-time').value;
    el.querySelector('.turn-input-head span').textContent = `Turn ${i + 1}${time ? ' · ' + time : ''}`;
  });
}

function clearTurns() {
  document.getElementById('turns').innerHTML = '';
  scheduleDraftSave();
  renderAnnotationSummary();
}

function readExtraSeed() {
  const raw = document.getElementById('extra-seed').value.trim();
  if (!raw) return {};
  return JSON.parse(raw);
}

function toggleReplaySurface() {
  const surface = document.getElementById('replay-surface').value;
  document.querySelectorAll('.device-only').forEach(el => {
    el.style.display = surface === 'v1_device' ? '' : 'none';
  });
  scheduleDraftSave();
}

function buildReplayEntryPayload() {
  const surface = document.getElementById('replay-surface').value || 'app_chat';
  if (surface === 'v1_device') {
    return {
      replay_surface: 'v1_device',
      protocol: 'v1_chat_completions',
      endpoint: '/v1/chat/completions',
      prompt_surface: 'device',
      device_id: document.getElementById('device-id').value.trim(),
      device_code: document.getElementById('device-code').value.trim(),
    };
  }
  return {
    replay_surface: 'app_chat',
    protocol: 'agent_chat',
    endpoint: '/agent/chat',
    prompt_surface: 'app',
  };
}

function mergeSeed(base, extra) {
  const seed = { ...extra };
  seed.from_snapshot = {
    ...(extra.from_snapshot || {}),
    ...base.from_snapshot,
  };
  return seed;
}

function readTurns() {
  return turnElements().map(el => {
    const user = el.querySelector('.turn-user').value.trim();
    const mediaRaw = el.querySelector('.turn-media').value.trim();
    const media = mediaRaw ? mediaRaw.split(/[\n,，]/).map(s => s.trim()).filter(Boolean) : [];
    const wait = el.querySelector('.turn-wait').value;
    return {
      user,
      media,
      wait_after_s: wait === '' ? 0 : Number(wait),
    };
  }).filter(t => t.user || t.media.length);
}

function readDraftTurns() {
  return turnElements().map(el => ({
    user: el.querySelector('.turn-user').value,
    media: el.querySelector('.turn-media').value,
    wait_after_s: el.querySelector('.turn-wait').value,
    occurred_at: el.querySelector('.turn-time').value,
    baseline_reply: el.querySelector('.turn-baseline-reply').value,
    review_mode: el.querySelector('.turn-review-mode').value,
    weight: el.querySelector('.turn-review-weight').value,
    product_feedback: el.querySelector('.turn-product-feedback').value,
    score_card: el.querySelector('.turn-score-card').value,
  }));
}

function readCheckAnnotations() {
  return turnElements().map((el, i) => {
    const mediaRaw = el.querySelector('.turn-media').value.trim();
    return {
      turn: i + 1,
      user: el.querySelector('.turn-user').value.trim(),
      media: mediaRaw ? mediaRaw.split(/[\n,，]/).map(s => s.trim()).filter(Boolean) : [],
      review_mode: el.querySelector('.turn-review-mode').value,
      weight: Number(el.querySelector('.turn-review-weight').value || 1),
      baseline_reply: el.querySelector('.turn-baseline-reply').value.trim(),
      product_feedback: el.querySelector('.turn-product-feedback').value.trim(),
      score_card: el.querySelector('.turn-score-card').value.trim(),
    };
  });
}

function onTurnEdited() {
  scheduleDraftSave();
  renderAnnotationSummary();
}

function collectAnnotationSummary() {
  const rows = turnElements().map((el, i) => ({
    el,
    turn: i + 1,
    mode: el.querySelector('.turn-review-mode')?.value || 'stable',
    feedback: el.querySelector('.turn-product-feedback')?.value.trim() || '',
    baseline: el.querySelector('.turn-baseline-reply')?.value.trim() || '',
    scoreCard: el.querySelector('.turn-score-card')?.value.trim() || '',
  }));
  const byMode = {
    stable: rows.filter(x => x.mode === 'stable'),
    improve: rows.filter(x => x.mode === 'improve'),
    none: rows.filter(x => x.mode === 'none'),
  };
  return {
    total: rows.length,
    stable: byMode.stable,
    improve: byMode.improve,
    none: byMode.none,
    improveWithFeedback: byMode.improve.filter(x => x.feedback),
    improveReady: byMode.improve.filter(x => x.feedback && x.baseline),
    improveMissingFeedback: byMode.improve.filter(x => !x.feedback),
    improveMissingBaseline: byMode.improve.filter(x => x.feedback && !x.baseline),
    improveWithScoreCard: byMode.improve.filter(x => x.scoreCard),
  };
}

function turnList(items) {
  if (!items.length) return '-';
  const values = items.map(x => `Turn ${x.turn}`);
  if (values.length <= 30) return values.join('、');
  return `${values.slice(0, 30).join('、')} 等 ${values.length} 个`;
}

function renderAnnotationSummary(label = '') {
  const box = document.getElementById('annotation-summary');
  if (!box) return collectAnnotationSummary();
  const s = collectAnnotationSummary();
  if (!s.total) {
    box.innerHTML = '评分标注概览：尚未导入或编辑。';
    return s;
  }
  const title = label ? `评分标注概览 · ${esc(label)}` : '评分标注概览';
  box.innerHTML = `
    ${title}：总 <b>${esc(s.total)}</b> 轮 · 保持稳定 <b>${esc(s.stable.length)}</b> · 提升 <b>${esc(s.improve.length)}</b> · 不计分 <b>${esc(s.none.length)}</b> · 可生成提升评分卡 <b>${esc(s.improveReady.length)}</b><br>
    提升位置：<span class="turn-links">${esc(turnList(s.improve))}</span><br>
    ${s.improveMissingFeedback.length ? `缺产品反馈：<span class="turn-links">${esc(turnList(s.improveMissingFeedback))}</span><br>` : ''}
    ${s.improveMissingBaseline.length ? `缺 baseline_reply：<span class="turn-links">${esc(turnList(s.improveMissingBaseline))}</span><br>` : ''}
    ${s.improveWithScoreCard.length ? `已有评分卡：<span class="turn-links">${esc(turnList(s.improveWithScoreCard))}</span>` : ''}
  `;
  return s;
}

function scheduleDraftSave() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(saveDraft, 250);
}

function saveDraft() {
  const payload = {
    source_tenant: document.getElementById('source-tenant').value,
    source_session: document.getElementById('source-session').value,
    snapshot_at: document.getElementById('snapshot-at').value,
    test_tenant: document.getElementById('test-tenant').value,
    default_wait: document.getElementById('default-wait').value,
    replay_surface: document.getElementById('replay-surface').value,
    device_id: document.getElementById('device-id').value,
    device_code: document.getElementById('device-code').value,
    extra_seed: document.getElementById('extra-seed').value,
    imported_meta: importedMeta,
    turns: readDraftTurns(),
    saved_at: new Date().toISOString(),
  };
  localStorage.setItem(DRAFT_KEY, JSON.stringify(payload));
}

function restoreDraft() {
  const raw = localStorage.getItem(DRAFT_KEY);
  if (!raw) return alert('没有本地草稿');
  try {
    const data = JSON.parse(raw);
    document.getElementById('source-tenant').value = data.source_tenant || '';
    document.getElementById('source-session').value = data.source_session || '';
    document.getElementById('snapshot-at').value = data.snapshot_at || '';
    document.getElementById('test-tenant').value = data.test_tenant || '';
    document.getElementById('default-wait').value = data.default_wait || '0';
    document.getElementById('replay-surface').value = data.replay_surface || 'app_chat';
    document.getElementById('device-id').value = data.device_id || '';
    document.getElementById('device-code').value = data.device_code || '';
    toggleReplaySurface();
    document.getElementById('extra-seed').value = data.extra_seed || '';
    importedMeta = data.imported_meta || null;
    clearTurns();
    (data.turns || []).forEach(t => addTurn(
      t.user || '',
      t.media ? String(t.media).split(/[\n,，]/).map(x => x.trim()).filter(Boolean) : [],
      t.wait_after_s || 0,
      t.occurred_at || '',
      t.baseline_reply || '',
      t.review_mode || 'stable',
      t.product_feedback || '',
      t.score_card || '',
      t.weight || '1'
    ));
    if (importedMeta) renderImportPreview(importedMeta);
    renderAnnotationSummary('已恢复草稿');
    document.getElementById('status').textContent = `已恢复草稿 · ${data.saved_at || ''}`;
  } catch (err) {
    alert('草稿恢复失败：' + err.message);
  }
}

async function generateScoreCard(button) {
  const el = button.closest('.turn-input');
  const status = el.querySelector('.turn-card-status');
  const mode = el.querySelector('.turn-review-mode').value;
  const baselineReply = el.querySelector('.turn-baseline-reply').value.trim();
  const productFeedback = el.querySelector('.turn-product-feedback').value.trim();
  if (mode === 'none') {
    alert('请先选择“保持稳定”或“需要改进”');
    return false;
  }
  if (!baselineReply) {
    alert('请先填写或导入产品测试 Agent 原回复');
    return false;
  }
  if (mode === 'improve' && !productFeedback) {
    alert('需要改进时请填写产品反馈原话');
    return false;
  }

  const mediaRaw = el.querySelector('.turn-media').value.trim();
  const payload = {
    mode,
    weight: Number(el.querySelector('.turn-review-weight').value || 1),
    user: el.querySelector('.turn-user').value.trim(),
    media: mediaRaw ? mediaRaw.split(/[\n,，]/).map(s => s.trim()).filter(Boolean) : [],
    baseline_reply: baselineReply,
    product_feedback: productFeedback,
  };
  button.disabled = true;
  status.textContent = 'Kimi 生成中...';
  try {
    const res = await fetch('/admin/scenario/stability-card', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    el.querySelector('.turn-score-card').value = JSON.stringify(data.card || {}, null, 2);
    status.textContent = '评分卡已生成';
    scheduleDraftSave();
    return true;
  } catch (err) {
    status.textContent = '生成失败';
    alert(err.message);
    return false;
  } finally {
    button.disabled = false;
  }
}

async function generateImproveCards() {
  const summary = renderAnnotationSummary('批量生成前检查');
  const targets = summary.improveReady;
  const existingCards = targets.filter(x => x.scoreCard).length;
  const status = document.getElementById('status');
  status.textContent = `批量检查：总 ${summary.total} · 提升 ${summary.improve.length}（${turnList(summary.improve)}）· 保持稳定 ${summary.stable.length} · 不计分 ${summary.none.length} · 可生成 ${targets.length}`;
  if (!targets.length) {
    return alert(`没有可生成的提升项：提升 ${summary.improve.length} 条，有产品反馈 ${summary.improveWithFeedback.length} 条，有 baseline ${targets.length} 条`);
  }
  if (existingCards) {
    status.textContent += ` · 将覆盖已有 ${existingCards} 条评分卡`;
  }
  let ok = 0;
  let failed = 0;
  for (let i = 0; i < targets.length; i += 1) {
    status.textContent = `批量生成评分卡 ${i + 1}/${targets.length} · ${turnList([targets[i]])}${existingCards ? ` · 覆盖已有 ${existingCards} 条` : ''}`;
    const btn = targets[i].el.querySelector('.turn-generate-card');
    const result = await generateScoreCard(btn);
    if (result) ok += 1;
    else failed += 1;
    renderAnnotationSummary('批量生成中');
  }
  status.textContent = `批量生成完成：成功 ${ok} 条，失败/跳过 ${failed} 条`;
  renderAnnotationSummary('批量生成完成');
  saveDraft();
}


function buildSnapshotSeed() {
  const tenant = document.getElementById('source-tenant').value.trim();
  const session = normalizeSessionKey(document.getElementById('source-session').value.trim());
  const snapshotAt = document.getElementById('snapshot-at').value.trim();
  if (!tenant || !session || !snapshotAt) return {};
  return {
    from_snapshot: {
      tenant,
      session,
      snapshot_at: snapshotAt,
      include_snapshot_message: false,
      profile_limit: 3,
      diary_limit: 2,
      image_limit: 3,
      force: false,
    }
  };
}

function normalizeSessionKey(value) {
  const session = String(value || '').trim();
  if (session && !session.includes(':')) return 'main:' + session;
  return session;
}

async function previewSnapshot() {
  const status = document.getElementById('status');
  const tenant = document.getElementById('source-tenant').value.trim();
  const session = normalizeSessionKey(document.getElementById('source-session').value.trim());
  const snapshotAt = document.getElementById('snapshot-at').value.trim();
  if (!tenant || !session || !snapshotAt) {
    return alert('请先填写源 Tenant、源 Session Key 和快照时间');
  }
  status.textContent = '快照预览中...';
  document.getElementById('results').innerHTML = '<div class="section"><h2>快照预览</h2><div class="empty">正在读取数据库状态，不会写入或克隆数据。</div></div>';
  try {
    const res = await fetch('/admin/scenario/snapshot-preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tenant_key: tenant, session_key: session, snapshot_at: snapshotAt }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    lastReport = data;
    renderSnapshotPreview(data);
    document.getElementById('download').disabled = false;
    status.textContent = `快照预览完成：cutoff=${data.source?.msg_seq_cutoff ?? '-'}`;
  } catch (err) {
    status.textContent = '快照预览失败';
    document.getElementById('results').innerHTML = `<div class="section"><h2>错误</h2><pre>${esc(err.message)}</pre></div>`;
  }
}

function renderSnapshotPreview(data) {
  const source = data.source || {};
  document.getElementById('results').innerHTML = `
    <div class="summary">
      <div class="metric"><div class="k">Tenant</div><div class="v">${esc(source.tenant_key)}</div></div>
      <div class="metric"><div class="k">Session</div><div class="v">${esc(source.session_key)}</div></div>
      <div class="metric"><div class="k">Snapshot</div><div class="v">${esc(source.snapshot_at)}</div></div>
      <div class="metric"><div class="k">Cutoff Seq</div><div class="v">${esc(source.msg_seq_cutoff)}</div></div>
      <div class="metric"><div class="k">Messages</div><div class="v">${esc(data.messages?.count_before_cutoff ?? 0)}</div></div>
    </div>
    <div class="section"><h2>Session / Journey</h2>${renderJson({ session: data.session, tenant_state: data.tenant_state })}</div>
    <div class="section"><h2>历史消息（展示最后 ${esc(data.messages?.showing_last ?? 0)} 条）</h2>${renderPreviewMessages(data.messages?.items || [])}</div>
    <div class="section"><h2>USER.md / SOUL.md 等文档</h2>${renderNamedJsonList(data.documents || [], 'doc_name')}</div>
    <div class="section"><h2>Memory</h2>${renderNamedJsonList(data.memories || [], 'topic')}</div>
    <div class="section"><h2>皮肤画像</h2>${renderNamedJsonList(data.skin_profiles || [], 'profile_id')}</div>
    <div class="section"><h2>肌肤日记</h2>${renderNamedJsonList(data.skin_diaries || [], 'id')}</div>
    <div class="section"><h2>护肤柜产品</h2>${renderNamedJsonList(data.cabinet_products || [], 'product_name')}</div>
    <div class="section"><h2>深度报告</h2>${renderNamedJsonList(data.deep_reports || [], 'report_id')}</div>
    <div class="section"><h2>图片分析任务</h2>${renderNamedJsonList(data.image_jobs || [], 'job_id')}</div>
  `;
}

function renderPreviewMessages(items) {
  if (!items.length) return '<div class="empty">no message before snapshot</div>';
  return `<div class="list">${items.map(m => `
    <div class="item">#${esc(m.seq)} · ${esc(m.role)}${m.tool_name ? ' · ' + esc(m.tool_name) : ''} · ${esc(m.created_at || '')}
${esc(m.content || '')}</div>
  `).join('')}</div>`;
}

function renderNamedJsonList(items, key) {
  if (!items.length) return '<div class="empty">empty</div>';
  return `<div class="list">${items.map(item => `
    <details>
      <summary>${esc(item[key] ?? '(row)')}</summary>
      <div>${renderJson(item)}</div>
    </details>
  `).join('')}</div>`;
}

function renderImportPreview(data) {
  const turns = data.turns || [];
  const detected = data.detected || {};
  const skipped = data.skipped_rows || [];
  const rawRows = data.raw_rows || [];
  const reviewCounts = data.review_counts || {};
  const feedbackCount = data.feedback_count || 0;
  const scoreReadyCount = data.score_ready_count || 0;
  const turnRows = turns.slice(0, 80).map((t, i) => `
    <div class="preview-cell">#${i + 1}<br><span class="small">row ${esc(t.row || '-')}</span></div>
    <div class="preview-cell">${esc(t.user || '(仅图片/附件)')}${t.media?.length ? '\n\n' + esc(t.media.join('\n')) : ''}</div>
    <div class="preview-cell">${esc(t.occurred_at || '-')}${Number(t.wait_after_s || 0) ? '\nwait ' + esc(t.wait_after_s) + 's' : ''}</div>
    <div class="preview-cell">${esc(t.review_mode || 'stable')}${t.product_feedback ? '\n' + esc(t.product_feedback) : ''}</div>
  `).join('');
  const rawPreview = rawRows.slice(0, 12).map(r => `row ${r.row}: ${r.values.map(v => v || '(空)').join(' | ')}`).join('\n');
  const skippedPreview = skipped.slice(0, 12).map(r => `row ${r.row} · ${r.reason}: ${r.values.map(v => v || '(空)').join(' | ')}`).join('\n');
  document.getElementById('results').innerHTML = `
    <div class="summary">
      <div class="metric"><div class="k">Sheet</div><div class="v">${esc(data.sheet || '-')}</div></div>
      <div class="metric"><div class="k">Imported</div><div class="v">${esc(turns.length)}</div></div>
      <div class="metric"><div class="k">Rows</div><div class="v">${esc(data.row_count ?? 0)}</div></div>
      <div class="metric"><div class="k">Header Row</div><div class="v">${esc(data.header_row || '-')}</div></div>
      <div class="metric"><div class="k">Skipped</div><div class="v">${esc(skipped.length)}</div></div>
      <div class="metric"><div class="k">Improve Ready</div><div class="v">${esc(scoreReadyCount)}</div></div>
    </div>
    <div class="section">
      <h2>评分标注统计</h2>
      <div class="empty">stable=${esc(reviewCounts.stable || 0)} · improve=${esc(reviewCounts.improve || 0)} · none=${esc(reviewCounts.none || 0)} · feedback=${esc(feedbackCount)} · 可批量生成=${esc(scoreReadyCount)}</div>
    </div>
    <div class="section">
      <h2>Excel 解析命中</h2>
      ${renderJson({
        headers: data.headers || [],
        detected,
      })}
    </div>
    <div class="section">
      <h2>导入消息预览（左侧可逐条修改）</h2>
      ${turnRows ? `<div class="preview-grid">
        <div class="preview-head">Turn</div>
        <div class="preview-head">User Query / Media</div>
        <div class="preview-head">Time</div>
        <div class="preview-head">评分标注 / 反馈</div>
        ${turnRows}
      </div>` : '<div class="empty">没有识别到可回放消息。请看下方原始行和跳过原因，通常是列名或角色列没有命中。</div>'}
    </div>
    <div class="section">
      <h2>原始行样例</h2>
      <pre>${esc(rawPreview || 'empty')}</pre>
    </div>
    ${skippedPreview ? `<div class="section"><h2>跳过行样例</h2><pre>${esc(skippedPreview)}</pre></div>` : ''}
  `;
}

function parseBulkText() {
  const raw = document.getElementById('bulk-text').value;
  const lines = raw.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
  if (!lines.length) return;
  clearTurns();
  for (const line of lines) {
    const parts = line.split('\t').map(x => x.trim());
    let occurredAt = '';
    let user = line;
    let media = [];
    let wait = document.getElementById('default-wait').value;
    if (parts.length >= 2) {
      occurredAt = parts[0];
      user = parts[1] || '';
      media = extractUrls(parts.slice(2).join(' '));
      const maybeWait = parts[3];
      if (maybeWait && !Number.isNaN(Number(maybeWait))) wait = Number(maybeWait);
    } else {
      media = extractUrls(line);
      user = line.replace(/https?:\/\/\S+/g, '').trim();
    }
    addTurn(user, media, wait, occurredAt);
  }
  const data = { sheet: '粘贴文本', row_count: lines.length, turns: readTurns(), headers: ['发生时间', '用户消息', '图片URL', '等待秒'], raw_rows: lines.slice(0, 30).map((line, i) => ({ row: i + 1, values: [line] })), skipped_rows: [] };
  importedMeta = data;
  renderImportPreview(data);
  renderAnnotationSummary('粘贴文本解析结果');
  document.getElementById('status').textContent = `已解析 ${turnElements().length} 轮，可在下方逐条编辑`;
}

function extractUrls(text) {
  return [...String(text || '').matchAll(/https?:\/\/[^\s,，;；)）]+/g)].map(m => m[0]);
}

async function importXlsx() {
  const input = document.getElementById('xlsx-file');
  const file = input.files && input.files[0];
  if (!file) return alert('请选择 Excel 文件');
  const status = document.getElementById('status');
  status.textContent = 'Excel 解析中...';
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/admin/scenario/import-xlsx', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    importedMeta = data;
    clearTurns();
    (data.turns || []).forEach(t => addTurn(
      t.user || '',
      t.media || [],
      t.wait_after_s || 0,
      t.occurred_at || '',
      t.baseline_reply || '',
      t.review_mode || 'stable',
      t.product_feedback || ''
    ));
    renderImportPreview(data);
    renderAnnotationSummary('Excel 导入结果');
    status.textContent = `已导入 ${turnElements().length} 轮 · sheet=${data.sheet || '-'}，可在下方逐条编辑`;
    saveDraft();
  } catch (err) {
    status.textContent = '导入失败';
    alert(err.message);
  }
}

function loadExample() {
  document.getElementById('source-tenant').value = '395';
  document.getElementById('source-session').value = 'main:session_395_1780476981576_T3X2ls';
  document.getElementById('snapshot-at').value = '2026/6/10 0:59:24';
  document.getElementById('test-tenant').value = '';
  document.getElementById('default-wait').value = '0';
  document.getElementById('replay-surface').value = 'app_chat';
  document.getElementById('device-id').value = '';
  document.getElementById('device-code').value = '';
  toggleReplaySurface();
  clearTurns();
  addTurn(
    '最近618我想换防晒，帮我看看这一款适不适合我',
    ['https://mongjing-v1.tos-cn-guangzhou.volces.com/chat/images/session_395_1780476981576_T3X2ls/20260610/tmp_c86639cda0993ce5b151068e46e3cbb64d02b07b0adda024_1781024362235.jpg'],
    0,
    '2026/6/10 0:59:24',
    '收到啦，我这就帮你看看~这是欧莱雅小金管防晒对吗？它的防晒力是足够的，而且质地相对清爽，对于你混合性肤质来说，日常通勤用不会太闷，也不会加重T区出油和闭口的问题，整体是比较适配的哦。如果想要更详细的成分和适配性分析，我也可以帮你调研这款产品的完整资料~',
    'stable',
    ''
  );
  renderAnnotationSummary('示例数据');
  saveDraft();
}

async function runReplay() {
  const btn = document.getElementById('run');
  const status = document.getElementById('status');
  const turns = readTurns();
  if (!turns.length) return alert('至少需要一轮用户消息或图片 URL');

  let seed = {};
  try {
    seed = mergeSeed(buildSnapshotSeed(), readExtraSeed());
  } catch (err) {
    alert('Seed JSON 不合法：' + err.message);
    return;
  }
  if (!seed.from_snapshot) {
    return alert('请填写源 Tenant、源 Session Key 和快照时间');
  }

  const payload = {
    ...buildReplayEntryPayload(),
    tenant_key: document.getElementById('test-tenant').value.trim(),
    wait_side_effects_s: Number(document.getElementById('default-wait').value || 0),
    seed,
    windows: {
      main: { turns },
      skin_diary: { turns: [] },
      deep_report: { turns: [] },
    },
  };

  btn.disabled = true;
  document.getElementById('check').disabled = true;
  lastReport = null;
  status.textContent = '运行中...';
  document.getElementById('results').innerHTML = '<div class="section"><h2>结果</h2><div class="empty">回放运行中，请等待每轮 Agent 回复和后台等待窗口结束。</div></div>';
  try {
    const res = await fetch('/admin/scenario/suite/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    lastReport = data;
    renderReport(data);
    document.getElementById('download').disabled = false;
    document.getElementById('check').disabled = false;
    status.textContent = `完成：${data.verdict || 'DONE'} · tenant=${data.tenant_key || ''}`;
  } catch (err) {
    status.textContent = '运行失败';
    document.getElementById('results').innerHTML = `<div class="section"><h2>错误</h2><pre>${esc(err.message)}</pre></div>`;
  } finally {
    btn.disabled = false;
  }
}

async function runCheck() {
  if (!lastReport) return alert('请先运行回放');
  const btn = document.getElementById('check');
  const status = document.getElementById('status');
  btn.disabled = true;
  status.textContent = 'Check 评分中...';
  try {
    const res = await fetch('/admin/scenario/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        report: lastReport,
        annotations: readCheckAnnotations(),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    lastReport.check = data;
    renderReport(lastReport);
    const avg = data.summary?.weighted_average;
    status.textContent = `Check 完成：加权均分 ${avg === null || avg === undefined ? '-' : fmtScore(avg)} · 低分 ${data.summary?.low_turns?.length || 0} 条`;
  } catch (err) {
    status.textContent = 'Check 失败';
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
}

function renderReport(report) {
  const main = report.windows?.main || {};
  const turns = measuredTurns(report);
  const replay = report.replay || {};
  const entryLabel = replay.replay_surface === 'v1_device'
    ? `硬件 V1 · ${replay.endpoint || '/v1/chat/completions'} · surface=${replay.prompt_surface || 'device'}`
    : `App · ${replay.endpoint || '/agent/chat'} · surface=${replay.prompt_surface || 'app'}`;
  document.getElementById('results').innerHTML = `
    <div class="section">
      <h2>Measured 对话</h2>
      <div class="small">tenant=${esc(report.tenant_key || '-')} · ${esc(turns.length)} 轮 · ${esc(main.verdict || report.verdict || 'DONE')} · ${esc(entryLabel)}</div>
    </div>
    ${renderCheckSummary(report.check)}
    ${turns.map(renderMeasuredTurn).join('') || '<div class="section"><div class="empty">没有 measured 运行结果</div></div>'}
  `;
}

function measuredTurns(report) {
  const main = report?.windows?.main || {};
  return (main.turns || []).filter(t => !t.phase || t.phase === 'measured' || t.measure === true);
}

function turnUserExportText(t) {
  const media = (t.media || []).filter(Boolean);
  const user = String(t.user || '').trim();
  return [user, ...media].filter(Boolean).join('\n');
}

function renderMeasuredTurn(t) {
  const userText = turnUserExportText(t) || '(empty)';
  const replyText = t.reply_preview || '';
  const observability = renderTurnObservability(t, replyText);
  const check = checkResultForTurn(t);
  return `
    <div class="turn-card">
      <div class="turn-title">
        <span>Turn ${esc(t.turn)} · measured${t.prompt_surface ? ' · ' + esc(t.prompt_surface) : ''}</span>
        <span>${esc(fmtMs(t.timing?.ttft_ms))}</span>
      </div>
      <div class="turn-body">
        <div class="block"><h3>User Query</h3><div class="text">${esc(userText)}</div></div>
        <div class="block"><h3>Agent 输出</h3><div class="text">${esc(replyText || '(empty)')}</div></div>
        ${renderTurnCheck(check)}
        ${observability}
      </div>
    </div>
  `;
}

function checkResultForTurn(t) {
  const turn = Number(t.turn);
  return (lastReport?.check?.results || []).find(x => Number(x.turn) === turn) || null;
}

function renderCheckSummary(check) {
  if (!check) return '';
  const s = check.summary || {};
  const low = s.low_turns || [];
  return `
    <div class="section">
      <h2>Check 评分</h2>
      <div class="summary">
        <div class="metric"><div class="k">Weighted Avg</div><div class="v">${esc(fmtScore(s.weighted_average))}</div></div>
        <div class="metric"><div class="k">Plain Avg</div><div class="v">${esc(fmtScore(s.plain_average))}</div></div>
        <div class="metric"><div class="k">Stable Avg</div><div class="v">${esc(fmtScore(s.stable_average))}</div></div>
        <div class="metric"><div class="k">Improve Avg</div><div class="v">${esc(fmtScore(s.improve_average))}</div></div>
        <div class="metric"><div class="k">Low Turns</div><div class="v">${esc(low.length)}</div></div>
      </div>
      <div class="check-summary-list">
        ${low.length ? `低分 Turn：${esc(low.map(x => `Turn ${x.turn}(${fmtScore(x.score)}, ${x.mode})`).join('、'))}` : '暂无低分 Turn'}
      </div>
    </div>
  `;
}

function renderTurnCheck(check) {
  if (!check) return '';
  if (check.verdict === 'skip') {
    return `<div class="check-panel"><div class="check-line"><span>Check · ${esc(check.mode || '-')}</span><span class="check-badge">skip</span></div><div class="small">${esc(check.reason || '')}</div></div>`;
  }
  const verdict = check.verdict || 'warn';
  const issues = Array.isArray(check.issues) ? check.issues : [];
  const evidence = Array.isArray(check.evidence) ? check.evidence : [];
  return `
    <div class="check-panel ${esc(verdict)}">
      <div class="check-line">
        <span>Check · ${esc(check.mode || '-')} · weight ${esc(check.weight || 1)}</span>
        <span class="check-badge ${esc(verdict)}">${esc(verdict)} · ${esc(fmtScore(check.score))}</span>
      </div>
      <div class="text">${esc(check.reason || '')}</div>
      ${issues.length ? `<div class="check-summary-list">问题：${esc(issues.join('；'))}</div>` : ''}
      ${evidence.length ? `<div class="check-summary-list">证据：${esc(evidence.join('；'))}</div>` : ''}
      ${check.suggested_fix ? `<div class="check-summary-list">建议：${esc(check.suggested_fix)}</div>` : ''}
    </div>
  `;
}

function renderTurnObservability(t, replyText) {
  const tools = t.tools || [];
  const providerPackets = collectProviderPackets(t);
  const pushedMessages = collectPushedMessages(t, replyText);
  const runtimeTasks = t.runtime_tasks_created || [];
  const businessJobs = collectBusinessJobs(t.business_jobs || {});
  const total = tools.length + providerPackets.length + pushedMessages.length + runtimeTasks.length + businessJobs.length;
  if (!total) return '';
  return `
    <details class="log-panel">
      <summary>关键日志 · tools ${tools.length} · provider ${providerPackets.length} · push ${pushedMessages.length} · tasks ${runtimeTasks.length + businessJobs.length}</summary>
      <div class="log-body">
        ${tools.length ? `<div class="log-group"><h3>工具调用 / 返回</h3>${renderToolLog(tools)}</div>` : ''}
        ${providerPackets.length ? `<div class="log-group"><h3>Provider 注入</h3>${renderProviderLog(providerPackets)}</div>` : ''}
        ${pushedMessages.length ? `<div class="log-group"><h3>等待窗口新增消息 / Event Hub 推送</h3>${renderMessageLog(pushedMessages)}</div>` : ''}
        ${runtimeTasks.length ? `<div class="log-group"><h3>Runtime Tasks</h3>${renderCompactRows(runtimeTasks)}</div>` : ''}
        ${businessJobs.length ? `<div class="log-group"><h3>Business Jobs</h3>${renderCompactRows(businessJobs)}</div>` : ''}
      </div>
    </details>
  `;
}

function collectProviderPackets(t) {
  const packets = [];
  for (const capture of (t.attention || [])) {
    for (const packet of (capture.packets || [])) {
      packets.push({ call: capture.call, ...packet });
    }
  }
  for (const capture of (t.prompt || [])) {
    for (const packet of (capture.attention_packets || [])) {
      if (packet.emitted) packets.push({ call: capture.call, ...packet });
    }
  }
  const groups = new Map();
  for (const packet of packets) {
    const source = packet.source || packet.provider || '-';
    const preview = String(packet.content_preview || '').trim();
    const key = `${source}|${preview}`;
    const occurrence = `call=${packet.call || '-'} placement=${packet.placement || '-'}`;
    if (!groups.has(key)) {
      groups.set(key, {
        ...packet,
        source,
        content_preview: preview,
        occurrences: [],
        placements: new Set(),
      });
    }
    const group = groups.get(key);
    if (!group.occurrences.includes(occurrence)) group.occurrences.push(occurrence);
    if (packet.placement) group.placements.add(packet.placement);
    if (!group.metadata || !Object.keys(group.metadata).length) group.metadata = packet.metadata || {};
  }
  return [...groups.values()].map(group => ({
    ...group,
    placements: [...group.placements],
  }));
}

function collectPushedMessages(t, replyText) {
  const out = [];
  const delta = t.state_changes?.session_delta || {};
  for (const msg of (delta.new_messages || [])) {
    const content = String(msg.content || '').trim();
    if (!content || msg.role === 'user') continue;
    if (replyText && replyText.includes(content)) continue;
    out.push({ lane: 'main', ...msg });
  }
  const sub = t.state_changes?.subagent_session_delta || {};
  for (const [lane, info] of Object.entries(sub)) {
    for (const msg of (info?.new_messages || [])) {
      const content = String(msg.content || '').trim();
      if (!content || msg.role === 'user') continue;
      out.push({ lane, ...msg });
    }
  }
  return out;
}

function collectBusinessJobs(jobs) {
  const out = [];
  for (const [kind, items] of Object.entries(jobs || {})) {
    for (const item of (items || [])) out.push({ kind, ...item });
  }
  return out;
}

function renderToolLog(tools) {
  return `<div class="list">${tools.map((x, i) => `
    <details class="log-item">
      <summary>#${i + 1} · ${esc(x.tool_name || 'tool')} · ${esc(x.status || x.action || (x.ok === false ? 'failed' : 'ok'))} · ${esc(x.duration_ms ?? '-')}ms</summary>
      <div class="kv2">
        <div>arguments</div><pre>${esc(prettyJson(x.arguments || {}))}</pre>
        <div>result</div><pre>${esc(prettyJson(x.result || x.error || {}))}</pre>
      </div>
    </details>
  `).join('')}</div>`;
}

function renderProviderLog(packets) {
  return `<div class="list">${packets.map(p => `
    <div class="item">
source=${esc(p.source || p.provider || '-')} · placements=${esc((p.placements || []).join(', ') || p.placement || '-')} · priority=${esc(p.priority ?? '-')} · occurrences=${esc((p.occurrences || []).length || 1)}
${p.occurrences?.length ? esc(p.occurrences.join(' | ')) + '\n' : ''}${esc(p.content_preview || '(empty)')}
${Object.keys(p.metadata || {}).length ? '\nmetadata=' + esc(prettyJson(p.metadata)) : ''}</div>
  `).join('')}</div>`;
}

function renderMessageLog(messages) {
  return `<div class="list">${messages.map(m => `
    <div class="item">${esc(m.lane || 'main')} · #${esc(m.seq ?? '-')} · ${esc(m.role || '-')} ${m.tool_name ? '· ' + esc(m.tool_name) : ''}
${esc(m.created_at || '')}
${esc(m.content || '')}</div>
  `).join('')}</div>`;
}

function renderCompactRows(items) {
  return `<div class="list">${items.map(item => `<div class="item">${esc(prettyJson(item))}</div>`).join('')}</div>`;
}

function prettyJson(value) {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function renderTiming(timing, firstTokenReply) {
  return `<div class="kv">
    <div>TTFT</div><div>${fmtMs(timing.ttft_ms)}</div>
    <div>Total</div><div>${fmtMs(timing.total_ms)}</div>
    <div>First Token</div><div>${esc(timing.first_token_status || '-')}</div>
    <div>Opener</div><div>${esc(firstTokenReply || '-')}</div>
  </div>`;
}

function renderTools(tools) {
  if (!tools.length) return '<div class="empty">no tool called</div>';
  return `<div class="list">${tools.map(x => `
    <div class="item">- ${esc(x.tool_name)} | ok=${esc(x.ok)} | action=${esc(x.action || '-')} | status=${esc(x.status || '-')} | ${esc(x.duration_ms)}ms
arguments=${esc(JSON.stringify(x.arguments || {}, null, 2))}
result=${esc(JSON.stringify(x.result || {}, null, 2))}
${x.error ? 'error=' + esc(x.error) : ''}</div>
  `).join('')}</div>`;
}

function renderPrompt(captures) {
  if (!captures.length) return '<div class="empty">no prompt captured</div>';
  return captures.map(c => `
    <details open>
      <summary>LLM Call ${esc(c.call)} · messages=${esc(c.message_count)}</summary>
      <div class="list">${(c.messages || []).map(renderPromptMessage).join('')}</div>
    </details>
  `).join('');
}

function renderPromptMessage(m) {
  const stable = m.stable_prefix_preview ? `\n\n[stable_prefix]\n${m.stable_prefix_preview}` : '';
  const dynamic = m.dynamic_tail_preview ? `\n\n[dynamic_tail]\n${m.dynamic_tail_preview}` : '';
  return `<div class="item">#${esc(m.idx)} ${esc(m.role)} · ${esc(m.content_kind)} · chars=${esc(m.text_chars ?? '')}
${esc(m.text_preview || '')}${esc(stable)}${esc(dynamic)}</div>`;
}

function renderJson(value) {
  return `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
}

function fmtMs(v) {
  if (v === null || v === undefined) return '-';
  return `${(Number(v) / 1000).toFixed(2)}s`;
}

function fmtScore(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '-';
  return Number(v).toFixed(2);
}

function downloadExcel() {
  if (!lastReport) return;
  const rows = measuredTurns(lastReport).map(t => ({
    user: turnUserExportText(t),
    agent: t.reply_preview || '',
    ttft: t.timing?.ttft_ms ?? '',
  }));
  const xml = buildExcelXml(rows);
  const blob = new Blob([xml], { type: 'application/vnd.ms-excel;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${lastReport.scenario || 'scenario-replay-report'}_measured.xls`;
  a.click();
  URL.revokeObjectURL(url);
}

function buildExcelXml(rows) {
  const body = [
    ['user query', 'agent输出', '首token(ms)'],
    ...rows.map(r => [r.user, r.agent, r.ttft]),
  ].map(row => `<Row>${row.map(cell => `<Cell><Data ss:Type="${typeof cell === 'number' ? 'Number' : 'String'}">${escXml(cell)}</Data></Cell>`).join('')}</Row>`).join('');
  return `<?xml version="1.0" encoding="UTF-8"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="measured">
  <Table>${body}</Table>
 </Worksheet>
</Workbook>`;
}

function escXml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;'
  }[c]));
}

['source-tenant', 'source-session', 'snapshot-at', 'test-tenant', 'default-wait', 'replay-surface', 'device-id', 'device-code', 'extra-seed'].forEach(id => {
  const el = document.getElementById(id);
  if (el) {
    el.addEventListener('input', scheduleDraftSave);
    el.addEventListener('change', scheduleDraftSave);
  }
});

if (localStorage.getItem(DRAFT_KEY)) {
  restoreDraft();
} else {
  toggleReplaySurface();
  addTurn();
}
</script>
</body>
</html>
"""
