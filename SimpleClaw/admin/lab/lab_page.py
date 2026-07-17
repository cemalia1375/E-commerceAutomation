"""/admin/lab 页面 HTML（单文件内嵌，原生 JS，主题对齐 admin/_page.py）。"""

LAB_HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mojing Lab — 图片生成 + 照片回填 + 真实链路聊天</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       background: #0f0f11; color: #e8e8ec; height: 100vh; display: flex; flex-direction: column; }
header { padding: 12px 20px; background: #1a1a20; border-bottom: 1px solid #2d2d3a;
         display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
header h1 { font-size: 15px; font-weight: 600; color: #c8b4f8; white-space: nowrap; }
header label { font-size: 12px; color: #7070a0; }
header input { background: #1a1a22; color: #e8e8ec; border: 1px solid #2d2d3a; border-radius: 6px;
               padding: 6px 10px; font-size: 13px; font-family: "JetBrains Mono", monospace; width: 220px; }
.hint { font-size: 11px; color: #6b6b80; }
.main { display: flex; flex: 1; overflow: hidden; }
.panel { display: flex; flex-direction: column; overflow: hidden; border-right: 1px solid #2d2d3a; }
.panel:last-child { border-right: none; }
.panel-title { padding: 10px 14px; font-size: 12px; font-weight: 700; color: #6b6b80;
               text-transform: uppercase; letter-spacing: 0.08em; background: #15151b;
               border-bottom: 1px solid #2d2d3a; display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.panel-body { flex: 1; overflow-y: auto; padding: 14px; }
#panel-imagegen { width: 400px; min-width: 340px; }
#panel-backfill  { width: 320px; min-width: 280px; }
#panel-chat { flex: 1; min-width: 340px; }
#panel-memory { width: 400px; min-width: 340px; }
button { background: #8b5cf6; color: #fff; border: none; border-radius: 6px; padding: 8px 14px;
         font-size: 13px; cursor: pointer; }
button:hover { background: #7c4ee8; }
button:disabled { background: #3a3a4a; color: #7070a0; cursor: not-allowed; }
button.ghost { background: #1e1e28; color: #b0b0cc; border: 1px solid #2d2d3a; }
input[type=file] { font-size: 12px; color: #b0b0c0; }
input[type=text], input:not([type]) { background: #1a1a22; color: #e8e8ec; border: 1px solid #2d2d3a;
  border-radius: 6px; padding: 5px 8px; font-size: 12px; font-family: "JetBrains Mono", monospace; }
select { background: #1a1a22; color: #e8e8ec; border: 1px solid #2d2d3a;
         border-radius: 6px; padding: 6px 8px; font-size: 12px; width: 100%; cursor: pointer; }
textarea { background: #1a1a22; color: #c8c8d8; border: 1px solid #2d2d3a; border-radius: 6px;
           padding: 8px 10px; font-size: 11px; font-family: inherit; resize: vertical;
           width: 100%; outline: none; }
table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 10px; }
th, td { text-align: left; padding: 5px 6px; border-bottom: 1px solid #232330; }
th { color: #6b6b80; font-weight: 600; }
td { color: #c0c0d0; word-break: break-all; }
.badge { font-size: 10px; padding: 2px 7px; border-radius: 9px; white-space: nowrap; }
.b-pending       { background: #232330; color: #8888a0; }
.b-generating    { background: #2a2440; color: #a78bfa; }
.b-uploading_tos { background: #1e2a3a; color: #60a5fa; }
.b-analyzing     { background: #2a2440; color: #a78bfa; }
.b-waiting_profile { background: #2e2a1e; color: #fbbf24; }
.b-syncing_profile { background: #1e3030; color: #2dd4bf; }
.b-backdating    { background: #29213a; color: #c4b5fd; }
.b-done          { background: #1c2e22; color: #4ade80; }
.b-failed        { background: #3a1e1e; color: #f87171; }
.warn-box { margin-top: 10px; padding: 8px 10px; border: 1px solid #5b4a1e; background: #2a2415;
            color: #fbbf24; font-size: 12px; border-radius: 6px; display: none; }
.err  { color: #f87171; font-size: 12px; margin-top: 8px; white-space: pre-wrap; }
.ok   { color: #4ade80; font-size: 12px; margin-top: 8px; }
/* chat */
#chat-messages { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.msg-row { display: flex; flex-direction: column; max-width: 86%; }
.row-user      { align-self: flex-end; align-items: flex-end; }
.row-assistant { align-self: flex-start; align-items: flex-start; }
.row-system    { align-self: center; align-items: center; }
.msg { padding: 9px 13px; border-radius: 10px; font-size: 13px; line-height: 1.65; white-space: pre-wrap; }
.msg-user      { background: #2a2440; color: #e8e8ec; }
.msg-assistant { background: #1a1a22; border: 1px solid #2d2d3a; color: #d8d8e4; }
.msg-system    { background: none; color: #6b6b80; font-size: 12px; }
.msg-meta { font-size: 10px; color: #5a5a70; margin-top: 3px; }
.chat-input-bar { display: flex; gap: 8px; padding: 12px 14px; border-top: 1px solid #2d2d3a; background: #15151b; flex-shrink: 0; }
.chat-input-bar textarea { flex: 1; height: 44px; }
/* memory */
.mem-section { margin-bottom: 16px; }
.mem-section h3 { font-size: 12px; color: #9090a8; margin-bottom: 6px; }
.mem-entry { background: #1a1a22; border: 1px solid #2d2d3a; border-radius: 6px; padding: 8px 10px; margin-bottom: 6px; }
.mem-entry .topic { font-size: 12px; color: #c8b4f8; font-weight: 600; }
.mem-entry .topic .skin-tag { color: #4ade80; font-weight: 700; margin-right: 5px; }
.mem-entry .meta { font-size: 10px; color: #5a5a70; margin: 2px 0 4px; }
.mem-entry .content { font-size: 12px; color: #b0b0c0; white-space: pre-wrap; }
.change-log { font-family: "JetBrains Mono", monospace; font-size: 11px; line-height: 1.8; }
.change-log .c-create { color: #4ade80; }
.change-log .c-update { color: #fbbf24; }
.change-log .c-delete { color: #f87171; }
.change-log .c-ledger { color: #60a5fa; }
.change-log .c-info   { color: #6b6b80; }
details { margin-bottom: 8px; }
details summary { cursor: pointer; font-size: 12px; color: #9090a8; }
details pre { background: #1a1a22; border: 1px solid #2d2d3a; border-radius: 6px; padding: 10px;
              font-size: 11px; color: #b0b0c0; white-space: pre-wrap; margin-top: 6px;
              max-height: 320px; overflow-y: auto; }
.mem-controls { display: flex; align-items: center; gap: 10px; margin-left: auto; font-size: 11px; color: #7070a0; }
/* imagegen */
.gen-day-card { background: #14141c; border: 1px solid #2d2d3a; border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; }
.gen-day-card .day-header { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.gen-day-card .day-num { font-size: 11px; color: #7070a0; font-family: "JetBrains Mono", monospace; width: 26px; flex-shrink: 0; }
.gen-day-card input.day-label { width: 52px; }
.gen-day-card input.day-refs  { width: 64px; }
.gen-day-card .day-base-label { font-size: 11px; color: #7070a0; white-space: nowrap; }
.gen-day-card .gen-stage-badge { font-size: 10px; }
.gen-day-card button.remove-day { margin-left: auto; padding: 2px 8px; font-size: 11px; }
.gen-gallery-thumb { width: 74px; height: 99px; object-fit: cover; border-radius: 5px;
                     border: 1px solid #2d2d3a; cursor: pointer; transition: border-color .15s; }
.gen-gallery-thumb:hover { border-color: #8b5cf6; }
.section-divider { border-top: 1px solid #2d2d3a; margin: 14px 0 10px; font-size: 11px; color: #6b6b80; padding-top: 10px; }
</style>
</head>
<body>
<header>
  <h1>Mojing Lab</h1>
  <label>user_id <input id="user-id" spellcheck="false"></label>
  <label>session_id <input id="session-id" spellcheck="false" style="width:180px"></label>
  <span class="hint">user_id 用 test_ 前缀可自动隔离（dream 写入门控同此约定）</span>
</header>
<div class="main">

  <!-- ═══ 图片生成 ═══ -->
  <div class="panel" id="panel-imagegen">
    <div class="panel-title">图片生成
      <span class="mem-controls">
        <button class="ghost" id="btn-gen-zip" style="padding:3px 10px;font-size:11px;display:none">↓ ZIP</button>
      </span>
    </div>
    <div class="panel-body">
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
        <label style="font-size:11px;color:#7070a0">模型 <input id="gen-model" style="width:210px" spellcheck="false"></label>
        <label style="font-size:11px;color:#7070a0">尺寸 <input id="gen-size" style="width:90px" spellcheck="false"></label>
      </div>
      <div style="font-size:12px;color:#9090a8;margin-bottom:6px">人物描述（Day1 基准，自然语言）</div>
      <textarea id="gen-base-prompt" style="height:110px;margin-bottom:8px" placeholder="描述人物外貌、拍摄风格（不用写皮肤状态，后端自动拼入 7 天剧本）"></textarea>
      <p class="hint" style="margin-bottom:14px">7 天皮肤变化（重度→轻度→晒红→化脓→收干→近愈→消失）自动拼入每天 prompt，全部并行生成。</p>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px">
        <button id="btn-gen">生成 7 天序列</button>
        <span class="err" id="gen-err" style="margin:0"></span>
      </div>
      <table id="gen-progress-table" style="display:none">
        <thead><tr><th>Day</th><th>标签</th><th>阶段</th></tr></thead>
        <tbody id="gen-progress-body"></tbody>
      </table>
      <div id="gen-gallery" style="display:none;margin-top:12px;border-top:1px solid #2d2d3a;padding-top:10px">
        <div style="font-size:11px;color:#6b6b80;margin-bottom:8px">生成结果（点击查看原图）</div>
        <div id="gen-gallery-inner" style="display:flex;flex-wrap:wrap;gap:6px"></div>
      </div>
    </div>
  </div>

  <!-- ═══ 历史照片回填 ═══ -->
  <div class="panel" id="panel-backfill">
    <div class="panel-title">历史照片回填</div>
    <div class="panel-body">
      <p class="hint" style="margin-bottom:10px">
        上传 zip（jpg/png/webp，按文件名排序：最后一张=今天，往前每张一天）。
        每张走真实链路：TOS 上传 → 外部图片分析 → 画像落库 → USER.md 同步 → 日期回写。
      </p>
      <input type="file" id="zip-file" accept=".zip"><br><br>
      <button id="btn-backfill">开始回填</button>
      <div class="warn-box" id="backfill-warn">回填进行中，请勿在右侧聊天里上传图片（避免画像匹配串扰）。</div>
      <div class="err" id="backfill-err"></div>
      <div class="ok" id="backfill-summary"></div>
      <table id="backfill-table" style="display:none">
        <thead><tr><th>文件</th><th>日期</th><th>阶段</th></tr></thead>
        <tbody></tbody>
      </table>
      <div id="profiles-area" style="display:none">
        <h3 style="font-size:12px;color:#9090a8;margin:16px 0 4px">落库验证（nb_tenant_skin_profiles）</h3>
        <table id="profiles-table">
          <thead><tr><th>profile</th><th>created_at</th><th>状态</th><th>sync</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>

      <div class="section-divider">或使用已生成图片</div>
      <select id="gen-job-select"><option value="">（暂无已完成的生图任务）</option></select>
      <div style="margin-top:8px">
        <button id="btn-imagegen-backfill" disabled>一键回传</button>
        <div class="err" id="imagegen-backfill-err"></div>
      </div>
    </div>
  </div>

  <!-- ═══ 真实链路聊天 ═══ -->
  <div class="panel" id="panel-chat">
    <div class="panel-title">真实链路聊天（POST /agent/chat）</div>
    <div id="chat-messages"></div>
    <div class="chat-input-bar">
      <textarea id="chat-input" placeholder="输入消息，Enter 发送，Shift+Enter 换行"></textarea>
      <button id="btn-send">发送</button>
    </div>
  </div>

  <!-- ═══ Memory 监控 ═══ -->
  <div class="panel" id="panel-memory">
    <div class="panel-title">Memory 监控
      <span class="mem-controls">
        <label><input type="checkbox" id="mem-auto"> 自动刷新(5s)</label>
        <button class="ghost" id="btn-mem-refresh" style="padding:3px 10px;font-size:11px">刷新</button>
      </span>
    </div>
    <div class="panel-body">
      <div class="mem-section">
        <h3>变化记录（轮询 diff，等价 runner 的 memory watch）</h3>
        <div class="change-log" id="mem-changes">（尚未加载）</div>
      </div>
      <div class="mem-section">
        <h3>记忆条目 (nb_memory_entries)</h3>
        <div id="mem-entries">（尚未加载）</div>
      </div>
      <div class="mem-section">
        <h3>Ledgers (nb_memory_ledgers)</h3>
        <div id="mem-ledgers">（尚未加载）</div>
      </div>
      <div class="mem-section">
        <h3>Dream artifacts</h3>
        <div id="mem-artifacts">（尚未加载）</div>
      </div>
      <div class="mem-section">
        <h3>USER.md</h3>
        <details><summary>展开查看</summary><pre id="mem-userdoc">（尚未加载）</pre></details>
      </div>
    </div>
  </div>

</div>
<script>
function pad(n){ return String(n).padStart(2,'0'); }
(function init(){
  const now = new Date();
  const stamp = pad(now.getHours())+pad(now.getMinutes())+pad(now.getSeconds());
  document.getElementById('user-id').value = 'test_lab_' + stamp;
  document.getElementById('session-id').value = 'lab_' + Date.now();
})();
function userId(){ return document.getElementById('user-id').value.trim(); }
function esc(s){ const d=document.createElement('div'); d.textContent=s==null?'':String(s); return d.innerHTML; }

/* ═══════════════════════════════════════════
   图片生成
═══════════════════════════════════════════ */
let genPollTimer = null;
let currentGenJobId = null;
let specDays = [];   // 从 /imagegen/defaults 加载的完整天列表

async function initGenPanel() {
  try {
    const r = await fetch('/admin/lab/imagegen/defaults');
    if (!r.ok) return;
    const data = await r.json();
    document.getElementById('gen-model').value = data.config.model || '';
    document.getElementById('gen-size').value = data.config.size || '';
    document.getElementById('gen-base-prompt').value = data.config.persona || '';
    specDays = data.days || [];  // [{day, label, skin_state}, ...]
  } catch(e) { console.warn('initGenPanel:', e); }
}

function buildGenBody() {
  const persona = document.getElementById('gen-base-prompt').value;
  // 每天 prompt = persona + skin_state，无参考图依赖，全部并行
  const days = specDays.map(d => ({
    day: d.day,
    label: d.label,
    prompt: d.skin_state ? persona + '\n' + d.skin_state : persona,
    refs: [],
    is_base: true,
  }));
  return {
    days,
    model: document.getElementById('gen-model').value.trim(),
    size: document.getElementById('gen-size').value.trim(),
    edit_template: '',
  };
}

document.getElementById('btn-gen').onclick = async () => {
  const btn = document.getElementById('btn-gen');
  if (!specDays.length) { document.getElementById('gen-err').textContent = '规格加载中，请稍候'; return; }
  btn.disabled = true;
  document.getElementById('gen-err').textContent = '';
  document.getElementById('gen-gallery').style.display = 'none';
  document.getElementById('gen-progress-table').style.display = 'none';
  document.getElementById('btn-gen-zip').style.display = 'none';
  try {
    const r = await fetch('/admin/lab/imagegen/generate', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(buildGenBody()),
    });
    const data = await r.json();
    if (!r.ok) { document.getElementById('gen-err').textContent = data.error||('HTTP '+r.status); btn.disabled=false; return; }
    currentGenJobId = data.job_id;
    renderGenProgress(data.items);
    if (genPollTimer) clearInterval(genPollTimer);
    genPollTimer = setInterval(() => pollGen(currentGenJobId), 3000);
  } catch(e) {
    document.getElementById('gen-err').textContent = '请求失败: '+e.message;
    btn.disabled = false;
  }
};

async function pollGen(jobId) {
  try {
    const r = await fetch('/admin/lab/imagegen/status?job_id='+encodeURIComponent(jobId));
    const data = await r.json();
    if (!r.ok) throw new Error(data.error||('HTTP '+r.status));
    renderGenProgress(data.items);
    if (data.state !== 'running') {
      clearInterval(genPollTimer); genPollTimer = null;
      document.getElementById('btn-gen').disabled = false;
      if (data.state === 'done') {
        renderGenGallery(jobId, data.items);
        document.getElementById('btn-gen-zip').style.display = '';
        refreshImagegenJobs();
      } else {
        document.getElementById('gen-err').textContent = '生成失败: '+(data.error||'');
      }
    }
  } catch(e) {
    clearInterval(genPollTimer); genPollTimer = null;
    document.getElementById('btn-gen').disabled = false;
    document.getElementById('gen-err').textContent = '进度查询失败: '+e.message;
  }
}

function renderGenProgress(items) {
  const table = document.getElementById('gen-progress-table');
  table.style.display = '';
  document.getElementById('gen-progress-body').innerHTML = (items||[]).map(it =>
    '<tr><td>Day'+esc(it.day)+'</td><td>'+esc(it.label)+'</td>' +
    '<td><span class="badge b-'+esc(it.stage)+'">'+esc(it.stage)+'</span>' +
    (it.error ? ' <span style="color:#f87171;font-size:10px">'+esc(it.error)+'</span>' : '')+'</td></tr>'
  ).join('');
}

function renderGenGallery(jobId, items) {
  const inner = document.getElementById('gen-gallery-inner');
  inner.innerHTML = (items||[]).filter(it => it.stage==='done').map(it => {
    const url = '/admin/lab/imagegen/image?job_id='+encodeURIComponent(jobId)+'&day='+it.day;
    return '<a href="'+url+'" target="_blank" title="Day'+esc(it.day)+' '+esc(it.label||'')+'">' +
      '<img src="'+url+'" class="gen-gallery-thumb" loading="lazy">' +
    '</a>';
  }).join('');
  document.getElementById('gen-gallery').style.display = inner.innerHTML ? '' : 'none';
}

document.getElementById('btn-gen-zip').onclick = () => {
  if (currentGenJobId)
    window.location.href = '/admin/lab/imagegen/zip?job_id='+encodeURIComponent(currentGenJobId);
};

initGenPanel();

/* ═══════════════════════════════════════════
   历史照片回填
═══════════════════════════════════════════ */
let pollTimer = null;

document.getElementById('btn-backfill').onclick = async () => {
  const fileInput = document.getElementById('zip-file');
  const errEl = document.getElementById('backfill-err');
  errEl.textContent = '';
  document.getElementById('backfill-summary').textContent = '';
  if (!fileInput.files.length) { errEl.textContent = '请先选择 zip 文件'; return; }
  if (!userId()) { errEl.textContent = '请填写 user_id'; return; }

  const fd = new FormData();
  fd.append('file', fileInput.files[0]);
  fd.append('user_id', userId());
  const btn = document.getElementById('btn-backfill');
  btn.disabled = true;
  try {
    const r = await fetch('/admin/lab/backfill', { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok) { errEl.textContent = data.error||('HTTP '+r.status); btn.disabled=false; return; }
    document.getElementById('backfill-warn').style.display = 'block';
    renderBackfillItems(data.items.map(it => ({...it, stage:'pending', error:''})));
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => pollBackfill(data.job_id), 2000);
  } catch(e) {
    errEl.textContent = '请求失败: '+e.message;
    btn.disabled = false;
  }
};

async function pollBackfill(jobId) {
  try {
    const r = await fetch('/admin/lab/backfill/status?job_id='+encodeURIComponent(jobId));
    const data = await r.json();
    if (!r.ok) throw new Error(data.error||('HTTP '+r.status));
    renderBackfillItems(data.items);
    if (data.state !== 'running') {
      clearInterval(pollTimer); pollTimer = null;
      document.getElementById('btn-backfill').disabled = false;
      document.getElementById('btn-imagegen-backfill').disabled = false;
      document.getElementById('backfill-warn').style.display = 'none';
      const ok = data.items.filter(it => it.stage==='done').length;
      document.getElementById('backfill-summary').textContent =
        '回填结束：'+ok+'/'+data.items.length+' 张成功'+(data.error ? ('；批级错误: '+data.error) : '');
      loadProfiles();
    }
  } catch(e) {
    clearInterval(pollTimer); pollTimer = null;
    document.getElementById('btn-backfill').disabled = false;
    document.getElementById('btn-imagegen-backfill').disabled = false;
    document.getElementById('backfill-err').textContent = '进度查询失败: '+e.message;
  }
}

function renderBackfillItems(items) {
  const table = document.getElementById('backfill-table');
  table.style.display = '';
  const tbody = table.querySelector('tbody');
  tbody.innerHTML = items.map(it =>
    '<tr><td>'+esc(it.filename)+'</td><td>'+esc(it.target_date)+'</td>' +
    '<td><span class="badge b-'+esc(it.stage)+'">'+esc(it.stage)+'</span>' +
    (it.error ? '<div class="err">'+esc(it.error)+'</div>' : '')+'</td></tr>'
  ).join('');
}

async function loadProfiles() {
  try {
    const r = await fetch('/admin/lab/profiles?user_id='+encodeURIComponent(userId()));
    const data = await r.json();
    if (!r.ok) return;
    document.getElementById('profiles-area').style.display = '';
    document.querySelector('#profiles-table tbody').innerHTML = (data.profiles||[]).map(p =>
      '<tr><td>'+esc(p.profile_id)+'</td><td>'+esc(p.created_at)+'</td><td>' +
      esc(p.overall_state)+'</td><td>'+esc(p.sync_status)+'</td></tr>'
    ).join('');
  } catch(_) {}
}

/* 生图任务回传 */
async function refreshImagegenJobs() {
  try {
    const r = await fetch('/admin/lab/imagegen/jobs');
    if (!r.ok) return;
    const data = await r.json();
    const sel = document.getElementById('gen-job-select');
    const btn = document.getElementById('btn-imagegen-backfill');
    const jobs = data.jobs || [];
    if (!jobs.length) {
      sel.innerHTML = '<option value="">（暂无已完成的生图任务）</option>';
      btn.disabled = true;
      return;
    }
    sel.innerHTML = jobs.map(j =>
      '<option value="'+esc(j.job_id)+'">'+esc(j.count+'张：'+j.labels.join('、'))+'</option>'
    ).join('');
    btn.disabled = false;
  } catch(_) {}
}
refreshImagegenJobs();

document.getElementById('btn-imagegen-backfill').onclick = async () => {
  const selJobId = document.getElementById('gen-job-select').value;
  const errEl = document.getElementById('imagegen-backfill-err');
  errEl.textContent = '';
  if (!selJobId || !userId()) { errEl.textContent = '请选择生图任务并填写 user_id'; return; }
  const btn = document.getElementById('btn-imagegen-backfill');
  btn.disabled = true;
  try {
    const r = await fetch('/admin/lab/imagegen/backfill', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ job_id: selJobId, user_id: userId() }),
    });
    const data = await r.json();
    if (!r.ok) { errEl.textContent = data.error||('HTTP '+r.status); btn.disabled=false; return; }
    document.getElementById('backfill-err').textContent = '';
    document.getElementById('backfill-summary').textContent = '';
    document.getElementById('backfill-warn').style.display = 'block';
    renderBackfillItems(data.items.map(it => ({...it, stage:'pending', error:''})));
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => pollBackfill(data.job_id), 2000);
  } catch(e) {
    errEl.textContent = '请求失败: '+e.message;
    btn.disabled = false;
  }
};

/* ═══════════════════════════════════════════
   聊天（SSE 消费对齐 admin/_page.py）
═══════════════════════════════════════════ */
function appendMsg(role, text) {
  const box = document.getElementById('chat-messages');
  const row = document.createElement('div'); row.className = 'msg-row row-'+role;
  const bubble = document.createElement('div'); bubble.className = 'msg msg-'+role;
  bubble.textContent = text;
  const meta = document.createElement('div'); meta.className = 'msg-meta';
  row.appendChild(bubble); row.appendChild(meta); box.appendChild(row);
  box.scrollTop = box.scrollHeight;
  return { bubble, meta };
}

document.getElementById('btn-send').onclick = sendChat;
document.getElementById('chat-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
});

async function sendChat() {
  const input = document.getElementById('chat-input');
  const msg = input.value.trim();
  if (!msg || !userId()) return;
  input.value = '';
  appendMsg('user', msg);
  const btn = document.getElementById('btn-send');
  btn.disabled = true;
  const start = performance.now();
  let firstAt = null, firstSource = null, text = '';
  const reply = appendMsg('assistant', '…');
  try {
    const r = await fetch('/agent/chat', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        user_id: userId(),
        session_id: document.getElementById('session-id').value.trim() || undefined,
        message: msg,
      }),
    });
    if (!r.ok) { reply.bubble.textContent = '(错误) HTTP '+r.status+' '+(await r.text()); return; }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
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
        let evt; try { evt = JSON.parse(raw); } catch(_) { continue; }
        if (evt.type === 'chunk' && evt.data && evt.data.text) {
          if (firstAt === null) { firstAt = performance.now()-start; firstSource = (evt.data.source||evt.node||'main'); }
          text += evt.data.text;
          reply.bubble.textContent = text;
          document.getElementById('chat-messages').scrollTop = 999999;
        } else if (evt.type === 'done') {
          const total = Math.round(performance.now()-start);
          reply.meta.textContent = (firstSource ? firstSource+' · TTFT '+Math.round(firstAt)+'ms · ' : '')+'总耗时 '+total+'ms';
        } else if (evt.type === 'error') {
          reply.bubble.textContent = '(错误) '+((evt.data&&evt.data.error)||raw);
        }
      }
    }
    if (!text && reply.bubble.textContent === '…') reply.bubble.textContent = '(无回复)';
  } catch(e) {
    reply.bubble.textContent = '(请求失败) '+e.message;
  } finally {
    btn.disabled = false;
  }
}

/* ═══════════════════════════════════════════
   Memory 监控（轮询 + 客户端 diff）
═══════════════════════════════════════════ */
let memTimer = null;
let prevEntries = null;
let prevLedgers = null;
const changeLog = [];

document.getElementById('mem-auto').onchange = function() {
  if (this.checked) { refreshMemory(); memTimer = setInterval(refreshMemory, 5000); }
  else { clearInterval(memTimer); memTimer = null; }
};
document.getElementById('btn-mem-refresh').onclick = refreshMemory;

function logChange(cls, text) {
  const t = new Date();
  changeLog.unshift({ cls, text: pad(t.getHours())+':'+pad(t.getMinutes())+':'+pad(t.getSeconds())+'  '+text });
  if (changeLog.length > 200) changeLog.pop();
  document.getElementById('mem-changes').innerHTML =
    changeLog.map(c => '<div class="'+c.cls+'">'+esc(c.text)+'</div>').join('');
}

async function refreshMemory() {
  if (!userId()) return;
  let data;
  try {
    const r = await fetch('/admin/lab/memory?user_id='+encodeURIComponent(userId()));
    data = await r.json();
    if (!r.ok) throw new Error(data.error||('HTTP '+r.status));
  } catch(e) { logChange('c-delete', '快照拉取失败: '+e.message); return; }

  const cur = {};
  (data.entries||[]).forEach(e => { cur[e.source+'|'+e.topic] = e; });
  if (prevEntries === null) {
    logChange('c-info', '基线已加载：'+(data.entries||[]).length+' 条记忆');
  } else {
    for (const k in cur) {
      if (!(k in prevEntries)) logChange('c-create', 'CREATE '+k+(cur[k].is_skin?' [skin]':''));
      else if (prevEntries[k].updated_at !== cur[k].updated_at || prevEntries[k].content !== cur[k].content)
        logChange('c-update', 'UPDATE '+k);
    }
    for (const k in prevEntries) if (!(k in cur)) logChange('c-delete', 'DELETE '+k);
  }
  prevEntries = cur;

  const curL = {};
  (data.ledgers||[]).forEach(l => { curL[l.ledger_id] = l.status+'/'+l.dream_status; });
  if (prevLedgers !== null) {
    for (const k in curL) {
      if (!(k in prevLedgers)) logChange('c-ledger', 'LEDGER '+k.slice(0,18)+'… '+curL[k]);
      else if (prevLedgers[k] !== curL[k]) logChange('c-ledger', 'LEDGER '+k.slice(0,18)+'… '+prevLedgers[k]+' → '+curL[k]);
    }
  }
  prevLedgers = curL;

  document.getElementById('mem-entries').innerHTML = (data.entries||[]).length
    ? data.entries.map(e =>
        '<div class="mem-entry"><div class="topic">'+(e.is_skin?'<span class="skin-tag">[skin]</span>':'')+
        esc(e.topic)+'</div><div class="meta">source='+esc(e.source)+' · type='+esc(e.memory_type)+
        ' · updated='+esc(e.updated_at)+'</div>'+
        (e.description?'<div class="meta">'+esc(e.description)+'</div>':'')+
        '<div class="content">'+esc(e.content)+'</div></div>'
      ).join('')
    : '（无记忆条目）';

  document.getElementById('mem-ledgers').innerHTML = (data.ledgers||[]).length
    ? data.ledgers.map(l =>
        '<div class="mem-entry"><div class="topic">'+esc(l.ledger_id)+'</div>' +
        '<div class="meta">status='+esc(l.status)+' · dream='+esc(l.dream_status)+' · '+esc(l.created_at)+'</div>'+
        (l.guardrail?'<div class="content">guardrail: '+esc(JSON.stringify(l.guardrail))+'</div>':'')+
        '</div>'
      ).join('')
    : '（无 ledger）';

  document.getElementById('mem-artifacts').innerHTML = (data.artifacts||[]).length
    ? data.artifacts.map(a => {
        let body = a.content || '';
        try { body = JSON.stringify(JSON.parse(body), null, 2); } catch(_) {}
        if (body.length > 6000) body = body.slice(0, 6000)+'\n…（已截断）';
        return '<details><summary>'+esc(a.artifact_key)+' · '+esc(a.status)+
               (a.applied?' · applied':' · draft')+'</summary><pre>'+esc(body)+'</pre></details>';
      }).join('')
    : '（无 dream artifact）';

  document.getElementById('mem-userdoc').textContent = data.user_doc || '（USER.md 不存在）';
}
</script>
</body>
</html>
"""
