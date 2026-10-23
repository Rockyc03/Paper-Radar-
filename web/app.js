const SIXQ = [
  ['problem', '解决了什么问题', '①'],
  ['data_method', '用了什么数据与方法', '②'],
  ['key_result', '最关键的结果', '③'],
  ['innovation', '创新点落在哪个位置', '④'],
  ['open_problems', '遗留的问题', '⑤'],
  ['next_steps', '之后的研究方向与思路', '⑥'],
];

const STATE = { summary: null, report: null, stem: null, view: 'report', topic: '', q: '', runTimer: null };

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function toast(msg, ms = 3200) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add('hidden'), ms);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error((await res.text()).slice(0, 200));
  return res.json();
}

/* ---------------- 初始化 ---------------- */
async function init() {
  bindNav();
  $('#btn-run').onclick = () => runPipeline({});
  $('#btn-open').onclick = () => {
    if (STATE.stem) window.open('/reports/' + STATE.stem + '.html', '_blank');
  };
  $('#report-select').onchange = (e) => loadReport(e.target.value);
  await refreshSummary();
  if (STATE.summary.reports.length) {
    await loadReport(STATE.summary.reports[0].stem);
  } else {
    renderEmptyReport();
  }
}

function bindNav() {
  document.querySelectorAll('.nav-item').forEach((b) => {
    b.onclick = () => switchView(b.dataset.view);
  });
}

function switchView(view) {
  STATE.view = view;
  document.querySelectorAll('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  ['report', 'archive', 'library', 'deepread', 'weekly', 'settings'].forEach((v) => {
    $('#view-' + v).classList.toggle('hidden', v !== view);
  });
  const titles = {
    report: ['每日汇报', '每日汇报'],
    archive: ['历史归档', '历史归档'],
    library: ['论文库', '论文库'],
    deepread: ['精读长文', '精读长文'],
    weekly: ['每周综述', '每周综述'],
    settings: ['设置与诊断', '设置与诊断'],
  };
  $('#kicker').textContent = titles[view][0];
  if (view === 'archive') renderArchive();
  if (view === 'library') renderLibrary();
  if (view === 'deepread') renderDeepread();
  if (view === 'weekly') renderWeekly();
  if (view === 'settings') renderSettings();
  if (view === 'report' && STATE.report) renderReportTitle(STATE.report);
}

async function refreshSummary() {
  STATE.summary = await api('/api/summary');
  const s = STATE.summary;
  $('#field-name').textContent = s.field;
  $('#field-note').textContent = s.scope_note || '';
  $('#badge-report').textContent = s.reports.length ? '' : '';
  $('#badge-archive').textContent = s.reports.length || '';
  $('#badge-library').textContent = '';
  $('#badge-deepread').textContent = s.deepreads.length || '';
  $('#badge-weekly').textContent = s.weekly.length || '';
  $('#report-select').innerHTML = s.reports
    .map((r) => `<option value="${esc(r.stem)}">${esc(r.date)} · ${esc(r.scope)} · ${r.count} 篇</option>`)
    .join('');
  if (STATE.stem) $('#report-select').value = STATE.stem;
  syncRunButton(s.run);
}

/* ---------------- 日报 ---------------- */
async function loadReport(stem) {
  STATE.stem = stem;
  const rec = await api('/api/report?stem=' + encodeURIComponent(stem));
  STATE.report = rec;
  STATE.topic = '';
  STATE.q = '';
  $('#report-select').value = stem;
  renderReportTitle(rec);
  renderReport(rec);
  if (STATE.view !== 'report') switchView('report');
}

function renderReportTitle(rec) {
  $('#kicker').textContent = rec.scope || '每日汇报';
  $('#page-title').textContent = rec.date + ' 论文调研';
  const dr = rec.deepread || {};
  $('#page-sub').textContent =
    `${rec.field} ｜ 共 ${(rec.papers || []).length} 篇 ｜ 时间窗 ${(rec.stats || {}).window_days || '-'} 天 ｜ ` +
    `生成于 ${rec.generated_at || '-'} ｜ ${dr.ok ? '精读已生成' : '本期无精读'}`;
}

function renderEmptyReport() {
  $('#view-report').innerHTML = `<div class="empty">
    还没有任何日报。点击右上角「生成本期」，或等每天 09:00 的定时任务自动产出。<br>
    <span class="small">首次生成约需 3–6 分钟（10 篇速读 + 1 篇精读）。</span></div>`;
}

function renderReport(rec) {
  const papers = rec.papers || [];
  const st = rec.stats || {};
  const kinds = { journal: 0, conference: 0, preprint: 0 };
  papers.forEach((p) => { kinds[p.kind] = (kinds[p.kind] || 0) + 1; });
  const topics = [...new Set(papers.map((p) => p.topic_name).filter(Boolean))];

  const dist = Object.entries(st.by_topic || {})
    .filter(([, v]) => v)
    .map(([k, v]) => `<span class="chip">${esc(k)} ${v} 篇</span>`)
    .join(' ');

  const dr = rec.deepread || {};
  const hero = dr.ok
    ? `<section class="hero">
        <div>
          <div class="tag">今日精读 · 影响力最高</div>
          <h2>${esc(dr.venue)} ｜ ${esc(dr.title_zh || dr.title)}</h2>
          <p>${esc(dr.gist || '')}</p>
          <p>${esc(dr.fulltext_note || '')} ｜ 影响力 ${esc(dr.influence)}/10</p>
        </div>
        <a href="/deepread/${esc(dr.href.split('/').pop())}" target="_blank">打开精读长文 →</a>
      </section>`
    : dr.error
      ? `<section class="hero" style="background:#5a3a12"><div><div class="tag">今日精读</div><h2>精读未生成</h2><p>${esc(dr.error)}</p></div></section>`
      : '';

  const filters = [`<button class="filter ${STATE.topic ? '' : 'on'}" data-topic="">全部 ${papers.length}</button>`]
    .concat(topics.map((t) => `<button class="filter ${STATE.topic === t ? 'on' : ''}" data-topic="${esc(t)}">${esc(t)} ${papers.filter((p) => p.topic_name === t).length}</button>`))
    .join('');

  $('#view-report').innerHTML = `
    <div class="stats">
      <div class="stat"><b>${papers.length}</b><span>本期论文</span></div>
      <div class="stat"><b>${kinds.journal}</b><span>期刊</span></div>
      <div class="stat"><b>${kinds.conference}</b><span>会议</span></div>
      <div class="stat"><b>${kinds.preprint}</b><span>预印本</span></div>
      <div class="stat"><b>${st.pool || '-'}</b><span>相关候选</span></div>
      <div class="stat"><b>${(rec.usage || {}).total_tokens || '-'}</b><span>消耗 token</span></div>
    </div>
    ${hero}
    <div class="toolbar">${filters}<span style="flex:1"></span>
      <input class="search" id="q" placeholder="搜索标题 / 关键词…" value="${esc(STATE.q)}"></div>
    <div class="toolbar small">${dist}</div>
    <div id="cards"></div>`;

  document.querySelectorAll('#view-report .filter').forEach((b) => {
    b.onclick = () => { STATE.topic = b.dataset.topic; renderReport(rec); };
  });
  const qi = $('#q');
  qi.oninput = () => {
    STATE.q = qi.value.trim().toLowerCase();
    paintCards(rec);
  };
  paintCards(rec);
}

function paintCards(rec) {
  const papers = (rec.papers || []).filter((p) => {
    if (STATE.topic && p.topic_name !== STATE.topic) return false;
    if (!STATE.q) return true;
    const a = p.analysis || {};
    return (p.title + ' ' + (a.title_zh || '') + ' ' + (a.gist || '') + ' ' + (a.tags || []).join(' '))
      .toLowerCase().includes(STATE.q);
  });
  $('#cards').innerHTML = papers.length ? papers.map(cardHTML).join('') : '<div class="empty">这个筛选下没有论文</div>';
}

function cardHTML(p, i) {
  const a = p.analysis || {};
  const qa = SIXQ.map(([k, label, n]) => {
    const v = (a[k] || '').trim();
    return `<div class="q"><div class="n">${n}</div><div><span class="k">${esc(label)}</span>
      <div class="v">${v ? esc(v) : '<i style="color:#9aa2a8">生成失败，可重跑</i>'}</div></div></div>`;
  }).join('');
  const venueChip = p.kind === 'preprint'
    ? '<span class="chip pre">arXiv 预印本</span>'
    : `<span class="chip venue">${esc(p.venue)}</span>`;
  return `<article class="card">
    <div class="toolbar" style="margin-bottom:8px">
      ${venueChip}<span class="chip date">${esc(p.date)}</span>
      <span class="chip">${esc(p.topic_name || '')}</span>
      ${p.citations ? `<span class="chip">引用 ${p.citations}</span>` : ''}
      <span style="flex:1"></span>
      <span class="infl">影响力 ${esc(a.influence || '-')}/10</span>
    </div>
    <h3>${esc(p.title)}</h3>
    <p class="zh">${esc(a.title_zh || '')}</p>
    <p class="gist">${esc(a.gist || '')}</p>
    <div class="qa">${qa}</div>
    <div class="foot">
      <div class="tags">${(a.tags || []).map((t) => `<span>${esc(t)}</span>`).join('')}</div>
      <div>${p.url ? `<a href="${esc(p.url)}" target="_blank">原文</a>` : ''}
        ${p.doi ? ' · ' + esc(p.doi) : ''}</div>
    </div>
  </article>`;
}

/* ---------------- 归档 ---------------- */
function renderArchive() {
  const rows = (STATE.summary.reports || []).map((r) => `<tr>
    <td><a href="#" data-stem="${esc(r.stem)}">${esc(r.date)}</a></td>
    <td>${esc(r.scope)}</td>
    <td>${r.count}</td>
    <td>${Object.entries(r.by_topic).map(([k, v]) => `${esc(k)} ${v}`).join(' · ')}</td>
    <td>${r.preprints}</td>
    <td>${r.window_days || '-'} 天</td>
    <td>${r.deepread ? '<span class="chip">有精读</span>' : '—'}</td>
    <td>${esc(r.generated_at || '')}</td>
  </tr>`).join('');
  $('#view-archive').innerHTML = rows
    ? `<table><thead><tr><th>日期</th><th>范围</th><th>篇数</th><th>方向分布</th><th>预印本</th>
       <th>时间窗</th><th>精读</th><th>生成时间</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<div class="empty">还没有归档。生成第一期后会出现在这里。</div>';
  $('#view-archive').querySelectorAll('a[data-stem]').forEach((a) => {
    a.onclick = (e) => { e.preventDefault(); loadReport(a.dataset.stem); };
  });
}

/* ---------------- 论文库 ---------------- */
async function renderLibrary() {
  const box = $('#view-library');
  box.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const data = await api('/api/library');
    const items = data.items || [];
    const rows = items.map((x) => `<tr>
      <td>${esc(x.date)}</td>
      <td><span class="chip ${x.kind === 'preprint' ? 'pre' : 'venue'}">${esc(x.venue)}</span></td>
      <td><div>${esc(x.title)}</div><div class="small">${esc(x.title_zh || '')}</div></td>
      <td>${esc(x.topic || '')}</td>
      <td>${esc(x.influence || '-')}</td>
      <td>${x.url ? `<a href="${esc(x.url)}" target="_blank">原文</a>` : ''}</td>
      <td><a href="#" data-stem="${esc(x.report)}">${esc(x.report)}</a></td>
    </tr>`).join('');
    box.innerHTML = items.length
      ? `<div class="toolbar"><input class="search" id="libq" placeholder="搜索论文…"></div>
         <table><thead><tr><th>日期</th><th>来源</th><th>标题</th><th>方向</th><th>影响力</th><th>链接</th><th>出自</th></tr></thead>
         <tbody id="libbody">${rows}</tbody></table>`
      : '<div class="empty">论文库是空的。</div>';
    const lq = $('#libq');
    if (lq) lq.oninput = () => {
      const q = lq.value.toLowerCase();
      $('#libbody').querySelectorAll('tr').forEach((tr) => {
        tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
      });
    };
    box.querySelectorAll('a[data-stem]').forEach((a) => {
      a.onclick = (e) => { e.preventDefault(); loadReport(a.dataset.stem); };
    });
  } catch (e) {
    box.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

/* ---------------- 精读 ---------------- */
function renderDeepread() {
  const items = STATE.summary.deepreads || [];
  $('#view-deepread').innerHTML = items.length
    ? items.map((d) => `<article class="card">
        <div class="toolbar" style="margin-bottom:6px">
          <span class="chip venue">${esc(d.venue)}</span>
          <span class="chip date">${esc(d.stem)}</span>
          <span class="chip">${esc(d.topic || '')}</span>
          <span style="flex:1"></span>
          <a href="/deepread/${esc(d.stem)}.html" target="_blank" style="color:#1f5c4d">阅读全文 →</a>
        </div>
        <h3>${esc(d.title_zh || d.title)}</h3>
        <p class="zh">${esc(d.title)}</p>
        <p class="small">${esc(d.note || '')}</p>
      </article>`).join('')
    : '<div class="empty">还没有精读。每天会从 10 篇里挑影响力最高的一篇生成。</div>';
}

/* ---------------- 周报 ---------------- */
function renderWeekly() {
  const items = STATE.summary.weekly || [];
  $('#view-weekly').innerHTML = items.length
    ? items.map((w) => `<article class="card">
        <div class="toolbar" style="margin-bottom:6px">
          <span class="chip date">${esc(w.stem)}</span><span class="chip">${esc(w.count || 0)} 篇</span>
          <span style="flex:1"></span>
          <a href="/weekly/${esc(w.stem)}.html" target="_blank" style="color:#1f5c4d">阅读全文 →</a>
        </div>
        <h3>${esc(w.title || '周报')}</h3>
        <p class="small">${esc(w.overview || '')}</p>
      </article>`).join('')
    : '<div class="empty">周报每周日 09:00 生成，也可以在这里手动触发。</div>';
}

/* ---------------- 设置 ---------------- */
async function renderSettings() {
  const s = STATE.summary;
  let llm = {};
  let wx = { enabled: false, bound: false, ready: false, reason: '读取中', logs: [] };
  try { llm = await api('/api/llm'); } catch (e) { llm = { error: e.message }; }
  try { wx = await api('/api/wecom'); } catch (e) { wx.reason = e.message; }
  const chatName = wx.chat_label || (wx.chat_type === 2 ? '群聊' : '单聊');
  $('#view-settings').innerHTML = `
  <div class="grid2">
    <div class="panel">
      <h4>方向（并列，命中任一即入选）</h4>
      ${s.configured === false ? `<p class="small" style="color:#b45309">还没有配置研究方向。<br>在项目目录运行 <code>python setup.py</code> 回答几个问题即可。</p>` : ''}
      <dl class="kv">
        ${(s.topics || []).map((t) => `<dt>${esc(t.name)}</dt><dd>${esc(t.key)}</dd>`).join('')}
        ${s.audience ? `<dt>读者</dt><dd>${esc(s.audience)}</dd>` : ''}
      </dl>
      <p class="small" style="margin-top:10px">${esc(s.scope_note || '')}</p>
    </div>
    <div class="panel">
      <h4>取文范围</h4>
      <dl class="kv">
        ${(s.venues || []).map((v) => `<dt>${esc(v.kind === 'conference' ? '会议' : '期刊')}</dt><dd>${esc(v.short)} · ${esc(v.name)}</dd>`).join('')}
      </dl>
    </div>
    <div class="panel">
      <h4>模型</h4>
      <dl class="kv">
        <dt>接口</dt><dd>${esc(llm.base_url || '-')}</dd>
        <dt>Key</dt><dd>${esc(llm.key_masked || '-')}</dd>
        ${Object.entries(llm.models || {}).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}
      </dl>
      <div style="margin-top:12px"><button class="btn" id="btn-llm">测试连通</button></div>
      <p class="small" id="llm-result" style="margin-top:8px"></p>
    </div>
    <div class="panel">
      <h4>企业微信推送 <span class="small" style="font-weight:400">（可选）</span></h4>
      <dl class="kv">
        <dt>开关</dt><dd>${wx.enabled ? '已开启（日报' + (wx.send_daily ? '' : '关') + ' / 周报' + (wx.send_weekly ? '' : '关') + '）' : '未启用'}</dd>
        <dt>凭证</dt><dd>${esc(wx.bot_id_masked || '未配置')}</dd>
        <dt>会话</dt><dd>${wx.bound ? esc(chatName) : '未绑定'}</dd>
        <dt>可发送</dt><dd>${wx.ready ? '是' : (wx.bot_id_masked ? esc(wx.reason || '否') : '不推送（可选功能）')}</dd>
        <dt>附件</dt><dd>${[
          wx.attach_report !== false ? '每日十篇泛读_MM.DD.html' : '',
          wx.attach_deepread !== false ? '每日精读_MM.DD.html' : '',
        ].filter(Boolean).join(' + ') || '不发附件'}</dd>
      </dl>
      <div class="toolbar" style="margin-top:12px">
        <button class="btn" id="btn-wx-test">发测试消息</button>
        <button class="btn" id="btn-wx-send">重发本期</button>
        <button class="btn" id="btn-wx-toggle">${wx.enabled ? '关闭自动推送' : '开启自动推送'}</button>
      </div>
      <p class="small" id="wx-result" style="margin-top:8px"></p>
      <p class="small">未配置不影响日报生成。想启用：命令行绑定一次会话（需在群里 @ 机器人发一句话）：
      <br><code>wecom_bind.py --bot-id 你的BotID --secret 你的Secret</code>
      <br>详见 README「推送到企业微信（可选）」；推送记录：<code>data/wecom_log.jsonl</code></p>
      <div class="log" id="wx-log">${
        (wx.logs || []).length
          ? wx.logs.map((l) => `${esc(l.ts)} ${esc(l.event)} ${esc(l.error || '')}`).join('\n')
          : '（还没有推送记录）'
      }</div>
    </div>
    <div class="panel">
      <h4>手动任务</h4>
      <div class="toolbar">
        <button class="btn primary" id="btn-run2">生成今日 10 篇</button>
        <button class="btn" id="btn-weekly">生成周报</button>
      </div>
      <p class="small">单方向出报也可命令行执行：<br>
      <code>pipeline.py --topic fusion</code> / <code>safety</code> / <code>game</code></p>
      <div class="log" id="logbox">（任务日志会显示在这里）</div>
    </div>
  </div>`;
  $('#btn-llm').onclick = async () => {
    $('#llm-result').textContent = '测试中…';
    try {
      const r = await api('/api/llm/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      $('#llm-result').textContent = r.ok ? '连通正常：' + r.reply : '失败：' + r.error;
    } catch (e) { $('#llm-result').textContent = '失败：' + e.message; }
  };
  $('#btn-run2').onclick = () => runPipeline({});
  $('#btn-weekly').onclick = () => runPipeline({ kind: 'weekly' });

  const wxMsg = (t) => { const el = $('#wx-result'); if (el) el.textContent = t; };
  $('#btn-wx-test').onclick = async () => {
    wxMsg('发送中…（要建一次长连接，约 3 秒）');
    try {
      const r = await api('/api/wecom/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      wxMsg(r.ok ? '已发送 ' + r.sent + ' 条，去企业微信看看' : '失败：' + (r.error || '未知'));
      if (r.ok) toast('测试消息已发送');
    } catch (e) { wxMsg('失败：' + e.message); }
  };
  $('#btn-wx-send').onclick = async () => {
    const stem = STATE.stem || (STATE.summary.reports[0] || {}).stem;
    if (!stem) { wxMsg('还没有可发的日报'); return; }
    wxMsg('发送中…');
    try {
      const r = await api('/api/wecom/send', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ date: stem }),
      });
      wxMsg(r.ok ? '已发送 ' + r.sent + ' 条（含精读附件）' : '失败：' + (r.error || '未知'));
      if (r.ok) { toast('已推送到企业微信'); renderSettings(); }
    } catch (e) { wxMsg('失败：' + e.message); }
  };
  $('#btn-wx-toggle').onclick = async () => {
    try {
      const r = await api('/api/wecom/config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !wx.enabled }),
      });
      toast(r.status.enabled ? '自动推送已开启' : '自动推送已关闭');
      renderSettings();
    } catch (e) { toast('保存失败：' + e.message); }
  };
  pollRun();
}

/* ---------------- 任务 ---------------- */
async function runPipeline(opts) {
  try {
    const r = await api('/api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ force: true }, opts)),
    });
    if (!r.ok) { toast(r.error || '启动失败'); return; }
    toast('任务已启动，约 3–6 分钟（可在「设置」看日志）');
    if (STATE.view !== 'settings') switchView('settings');
    pollRun();
  } catch (e) { toast('启动失败：' + e.message); }
}

async function pollRun() {
  clearInterval(STATE.runTimer);
  const tick = async () => {
    let st;
    try { st = await api('/api/run/status'); } catch (e) { return; }
    const box = $('#logbox');
    if (box) box.textContent = (st.log || []).join('\n') || '（等待输出…）';
    if (box) box.scrollTop = box.scrollHeight;
    syncRunButton({ running: st.running });
    if (!st.running && st.exit !== null) {
      clearInterval(STATE.runTimer);
      toast(st.exit === 0 ? '任务完成，已刷新' : '任务结束，退出码 ' + st.exit);
      await refreshSummary();
      if (STATE.summary.reports.length) await loadReport(STATE.summary.reports[0].stem);
    }
  };
  STATE.runTimer = setInterval(tick, 2000);
  tick();
}

function syncRunButton(run) {
  const running = run && run.running;
  ['#btn-run', '#btn-run2'].forEach((sel) => {
    const b = $(sel);
    if (!b) return;
    b.disabled = !!running;
    b.textContent = running ? '生成中…' : (sel === '#btn-run' ? '生成本期' : '生成今日 10 篇');
  });
}

init().catch((e) => {
  document.body.innerHTML = `<div style="padding:40px;font-family:sans-serif">
    <h2>看板加载失败</h2><p>${esc(e.message)}</p>
    <p>确认服务已启动：<code>python server.py</code></p></div>`;
});
