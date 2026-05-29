const $ = (id) => document.getElementById(id);
const APP_META = {
  chartSymbol: document.body?.dataset?.chartSymbol || 'XAUUSD',
  appVersion: document.body?.dataset?.appVersion || '1.0.0'
};

const state = {
  currentStrategy: null,
  currentRun: null,
  selectedSetupId: null,
  templates: [],
  runs: [],
  strategies: [],
  strategyTemplates: [],
  selectedTemplateId: null,
  activeWorkspace: 'researchWorkspace',
  annotation: {
    candles: [],
    items: [],
    mode: 'line',
    drawing: false,
    start: null,
    preview: null,
    geometry: null,
    symbol: APP_META.chartSymbol,
    timeframe: '3m',
    startTime: null,
    endTime: null
  }
};

function toast(title, message, type='info'){
  const wrap = $('toastWrap');
  const div = document.createElement('div');
  div.className = `toast ${type}`;
  div.innerHTML = `<strong>${title}</strong><div>${message}</div>`;
  wrap.appendChild(div);
  setTimeout(() => div.remove(), 4200);
}

function showStatus(show){ $('loadingStatus').classList.toggle('show', !!show); }
function fmtObj(obj){ return JSON.stringify(obj ?? {}, null, 2); }
function scrollToSection(id){ document.getElementById(id)?.scrollIntoView({behavior:'smooth', block:'start'}); }
function applyQuickPrompt(text){ $('prompt').value = text; toast('تم تعبئة الأمر', 'تم وضع الأمر السريع داخل Composer.', 'info'); }

function selectedTemplate(){
  return state.strategyTemplates.find(item => item.id === state.selectedTemplateId) || null;
}

function currentTemplateRules(){
  return {
    fresh_only: $('templateFreshOnly')?.checked ?? true,
    max_level_touches: Number($('templateMaxTouches')?.value || 1),
    reaction_required: $('templateReactionRequired')?.checked ?? false,
    reaction_mode: $('templateReactionMode')?.value || 'wick_or_displacement',
    fvg_required: $('templateFvgRequired')?.checked ?? false,
    poi_mode: $('templatePoiMode')?.value || 'fvg_midpoint',
    allow_countertrend: false,
    notes: []
  };
}

function prefillTemplateFromStrategy(){
  const strategy = state.currentStrategy;
  if (!strategy) {
    toast('لا توجد استراتيجية', 'افهم الاستراتيجية أولًا ثم ارجع إلى Template Lab.', 'error');
    return;
  }
  $('templateName').value = $('templateName').value || `${strategy.market_label || strategy.symbol} ${strategy.primary_timeframe} ${strategy.strategy_key}`;
  $('templateDescription').value = $('templateDescription').value || `School: ${strategy.school}
Strategy key: ${strategy.strategy_key}
Execution: ${strategy.execution_timeframe}`;
  switchWorkspace('templateWorkspace');
  toast('تمت التعبئة', 'تم نقل الفهم الحالي إلى Template Builder.', 'success');
}

async function loadStrategyTemplates(){
  const data = await apiFetch('/api/strategy-templates');
  state.strategyTemplates = Array.isArray(data.items) ? data.items : [];
  renderTemplateList(state.strategyTemplates);
}

function filterStrategyTemplates(term){
  const q = term.trim().toLowerCase();
  const items = !q ? state.strategyTemplates : state.strategyTemplates.filter(item => {
    const hay = [item.name, item.description, item.school, item.strategy_key, item.primary_timeframe, item.execution_timeframe].join(' ').toLowerCase();
    return hay.includes(q);
  });
  renderTemplateList(items);
}

function renderTemplateList(items){
  const list = $('templateList');
  if (!list) return;
  list.innerHTML = '';
  if (!(items || []).length) {
    list.innerHTML = '<div class="list-card"><strong>لا توجد Templates بعد</strong><span>احفظ الاستراتيجية الحالية كقالب مسمّى لتعيد استخدامها في scan/backtest.</span></div>';
    return;
  }
  items.forEach(item => {
    const div = document.createElement('div');
    div.className = 'list-card' + (item.id === state.selectedTemplateId ? ' active' : '');
    div.innerHTML = `<strong>${item.name}</strong><span>${item.school} • ${item.strategy_key}</span><span>${item.primary_timeframe} → ${item.execution_timeframe} | examples ${item.examples_count || 0}</span>`;
    div.onclick = async () => {
      state.selectedTemplateId = item.id;
      document.querySelectorAll('#templateList .list-card').forEach(x => x.classList.remove('active'));
      div.classList.add('active');
      const detail = await apiFetch('/api/strategy-templates/' + item.id);
      $('templateInspector').textContent = fmtObj(detail);
      $('templateName').value = detail.name || '';
      $('templateDescription').value = detail.description || '';
      $('templateMaxTouches').value = detail.rules?.max_level_touches ?? 1;
      $('templateReactionMode').value = detail.rules?.reaction_mode || 'wick_or_displacement';
      $('templatePoiMode').value = detail.rules?.poi_mode || 'fvg_midpoint';
      $('templateFreshOnly').checked = !!detail.rules?.fresh_only;
      $('templateReactionRequired').checked = !!detail.rules?.reaction_required;
      $('templateFvgRequired').checked = !!detail.rules?.fvg_required;
      if (detail.strategy) state.currentStrategy = detail.strategy;
      if (detail.examples?.length) {
        const lastExample = detail.examples[detail.examples.length - 1];
        $('exampleTitle').value = lastExample.title || '';
        $('exampleJson').value = JSON.stringify(lastExample.annotations || [], null, 2);
        $('annotationTimeframe').value = lastExample.execution_timeframe || detail.strategy?.execution_timeframe || '3m';
        state.annotation.items = Array.isArray(lastExample.annotations) ? lastExample.annotations.map(normalizeAnnotationItem) : [];
        state.annotation.symbol = lastExample.symbol || detail.strategy?.symbol || APP_META.chartSymbol;
        state.annotation.timeframe = lastExample.execution_timeframe || detail.strategy?.execution_timeframe || '3m';
      }
    };
    list.appendChild(div);
  });
}

async function saveTemplateFromCurrentStrategy(){
  if (!state.currentStrategy) await interpretStrategy();
  if (!state.currentStrategy) return;
  const name = ($('templateName').value || '').trim();
  if (!name) {
    toast('اسم مطلوب', 'أدخل اسمًا واضحًا للـ Template.', 'error');
    return;
  }
  const payload = {
    name,
    description: $('templateDescription').value || '',
    strategy: state.currentStrategy,
    rules: currentTemplateRules(),
    examples: []
  };
  const data = await apiFetch('/api/strategy-templates/save', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
  });
  state.selectedTemplateId = data.saved?.id || null;
  toast('تم حفظ الـ Template', `تم حفظ ${name} وإتاحته للمسح لاحقًا.`, 'success');
  await loadStrategyTemplates();
}

async function saveTemplateExample(){
  const current = selectedTemplate();
  if (!current) {
    toast('اختر Template', 'احفظ أو اختر Template أولًا ثم أضف المثال المشروح.', 'error');
    return;
  }
  let annotations = state.annotation.items || [];
  if (!annotations.length) {
    try {
      annotations = JSON.parse($('exampleJson').value || '[]');
    } catch (err) {
      toast('JSON غير صالح', 'صيغة الـ annotations يجب أن تكون JSON صالحًا.', 'error');
      return;
    }
  }
  if (!annotations.length) {
    toast('لا توجد annotations', 'ارسم أو أدخل annotations أولًا قبل الحفظ.', 'error');
    return;
  }
  const payload = {
    title: $('exampleTitle').value || 'Annotated example',
    symbol: state.annotation.symbol || state.currentStrategy?.symbol || APP_META.chartSymbol,
    primary_timeframe: state.currentStrategy?.primary_timeframe || '30m',
    execution_timeframe: state.annotation.timeframe || state.currentStrategy?.execution_timeframe || '3m',
    note: $('templateDescription').value || '',
    annotations
  };
  const data = await apiFetch(`/api/strategy-templates/${current.id}/examples`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
  });
  $('templateInspector').textContent = fmtObj(data.saved || data);
  toast('تم حفظ المثال', 'أضيف المثال المشروح إلى الـ Template المحدد.', 'success');
  await loadStrategyTemplates();
}

async function runSelectedTemplate(){
  const current = selectedTemplate();
  if (!current) {
    toast('اختر Template', 'احفظ أو اختر Template أولًا.', 'error');
    return;
  }
  showStatus(true);
  try {
    const payload = { backtest_range: currentBacktestRange(), symbol: state.currentStrategy?.symbol || APP_META.chartSymbol };
    const data = await apiFetch(`/api/strategy-templates/${current.id}/scan`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
    });
    updateSummaryFromRun(data);
    switchWorkspace('runsWorkspace');
    await loadRunHistory(true);
    toast('تم تشغيل الـ Template', `تم مسح السوق باستخدام ${current.name}.`, 'success');
  } finally { showStatus(false); }
}

async function apiFetch(url, options={}){
  let res;
  try {
    res = await fetch(url, options);
  } catch (err) {
    toast('تعذر الاتصال', 'فشل الوصول إلى الـ API. تأكد أن الخادم يعمل.', 'error');
    throw err;
  }
  const contentType = res.headers.get('content-type') || '';
  let payload = null;
  if (contentType.includes('application/json')) {
    payload = await res.json();
  } else {
    const text = await res.text();
    payload = { ok:false, error:{ code:'INVALID_RESPONSE', message:text || 'Non-JSON response', details:{} } };
  }
  if (!res.ok || payload?.ok === false) {
    const error = payload?.error || { code:'UNKNOWN', message:'Unknown error', details:{} };
    toast(error.code || 'Error', error.message || 'Request failed', 'error');
    throw new Error(error.message || 'API error');
  }
  return payload;
}

function setTheme(theme){
  document.body.setAttribute('data-theme', theme);
  localStorage.setItem('xau-ui-theme', theme);
  $('themeToggle').textContent = theme === 'light' ? '🌙' : '☀️';
}
function initTheme(){ setTheme(localStorage.getItem('xau-ui-theme') || 'dark'); }

function setThisWeek(){
  const now = new Date();
  const start = new Date(now); start.setDate(now.getDate()-7);
  $('toDate').value = now.toISOString().slice(0,10);
  $('fromDate').value = start.toISOString().slice(0,10);
}
function currentBacktestRange(){ return { start: $('fromDate').value, end: $('toDate').value }; }

function switchWorkspace(id){
  state.activeWorkspace = id;
  document.querySelectorAll('.workspace').forEach(el => el.classList.toggle('active', el.id === id));
  document.querySelectorAll('.workspace-tab').forEach(btn => btn.classList.toggle('active', btn.dataset.workspace === id));
}

document.querySelectorAll('.workspace-tab').forEach(btn => {
  btn.addEventListener('click', () => switchWorkspace(btn.dataset.workspace));
});

function renderHighlights(lines){
  const box = $('highlights');
  box.innerHTML = '';
  (lines || []).forEach(line => {
    const div = document.createElement('div');
    div.className = 'pill';
    div.textContent = line;
    box.appendChild(div);
  });
  if (!(lines || []).length) box.innerHTML = '<div class="list-card"><strong>لا توجد Highlights</strong><span>شغّل البحث لعرض أبرز النقاط هنا.</span></div>';
}

function summarizeSetup(setup){
  const sourceZone = setup.source_direction === 'bullish'
    ? `من low ${setup.source_low} إلى open ${setup.source_open}`
    : `من open ${setup.source_open} إلى high ${setup.source_high}`;
  return [
    `النوع: ${setup.source_direction} ${setup.source_type}`,
    `الجودة: ${setup.quality_label} (${setup.quality_score})`,
    `النتيجة: ${setup.outcome_label} | الحركة: ${setup.outcome_move_points} | adverse: ${setup.adverse_move_points}`,
    `منطقة التحقق اليدوي: ${sourceZone}`,
    `سلسلة التأكيد: ${(setup.confirmation_chain||[]).join(' → ') || '-'}`,
    '',
    ...(setup.explanation || [])
  ].join('\n');
}


function renderMiniEmpty(svgId, metaId, message){
  $(svgId).innerHTML = '';
  $(metaId).textContent = message;
}

function renderEquityChart(setups){
  const svg = $('equityChart');
  if (!setups.length) return renderMiniEmpty('equityChart', 'equityMeta', 'شغّل بحثًا لعرض منحنى الأداء التراكمي.');
  const width = 860, height = 240, padX = 38, padY = 20;
  let cumulative = 0;
  const points = setups.map((s, i) => { cumulative += Number(s.outcome_move_points || 0); return {i, v:cumulative}; });
  const minV = Math.min(0, ...points.map(p => p.v));
  const maxV = Math.max(1, ...points.map(p => p.v));
  const span = Math.max(1, maxV - minV);
  const plotW = width - padX * 2, plotH = height - padY * 2;
  const xFor = i => padX + (plotW * i / Math.max(1, points.length - 1));
  const yFor = v => padY + (maxV - v) / span * plotH;
  let html = '';
  for (let i=0;i<4;i++){ const y = padY + (plotH * i / 3); html += `<line x1="${padX}" y1="${y}" x2="${width-padX}" y2="${y}" stroke="#28406a" stroke-width="1" />`; }
  const line = points.map((p, i) => `${i===0?'M':'L'} ${xFor(i)} ${yFor(p.v)}`).join(' ');
  const zeroY = yFor(0);
  html += `<line x1="${padX}" y1="${zeroY}" x2="${width-padX}" y2="${zeroY}" stroke="#64748b" stroke-width="1.2" stroke-dasharray="5 5" />`;
  html += `<path d="${line}" fill="none" stroke="#22c55e" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />`;
  points.forEach((p, i) => { html += `<circle cx="${xFor(i)}" cy="${yFor(p.v)}" r="3.8" fill="${p.v >= 0 ? '#22c55e' : '#ef4444'}" />`; });
  svg.innerHTML = html;
  $('equityMeta').textContent = `آخر قيمة تراكمية: ${points.at(-1).v.toFixed(2)} نقطة حركة تقريبية عبر ${setups.length} فرصة.`;
}

function renderQualityHistogram(setups){
  const svg = $('qualityHistogram');
  if (!setups.length) return renderMiniEmpty('qualityHistogram', 'qualityMeta', 'سيظهر توزيع الجودة هنا.');
  const buckets = [0,20,40,60,80,100];
  const labels = ['0-19','20-39','40-59','60-79','80-100'];
  const counts = new Array(labels.length).fill(0);
  setups.forEach(s => {
    const q = Number(s.quality_score || 0);
    let idx = labels.length - 1;
    for (let i=0;i<buckets.length-1;i++){ if (q >= buckets[i] && q < buckets[i+1]) { idx = i; break; } }
    counts[idx] += 1;
  });
  const width = 860, height = 240, padX = 38, padY = 20;
  const maxY = Math.max(1, ...counts);
  const plotW = width - padX*2, plotH = height - padY*2, barW = plotW / counts.length * 0.62;
  let html='';
  for (let i=0;i<4;i++){ const y = padY + (plotH * i / 3); html += `<line x1="${padX}" y1="${y}" x2="${width-padX}" y2="${y}" stroke="#28406a" stroke-width="1" />`; }
  counts.forEach((count, i) => {
    const x = padX + (plotW / counts.length) * i + (plotW / counts.length - barW) / 2;
    const h = (count / maxY) * plotH;
    const y = height - padY - h;
    html += `<rect x="${x}" y="${y}" width="${barW}" height="${h}" rx="12" fill="${i >= 3 ? '#8b5cf6' : '#38bdf8'}" opacity="0.9" />`;
    html += `<text x="${x + barW/2}" y="${height - 6}" fill="#94a3b8" font-size="12" text-anchor="middle">${labels[i]}</text>`;
    html += `<text x="${x + barW/2}" y="${Math.max(y-8, 16)}" fill="#e2e8f0" font-size="12" text-anchor="middle">${count}</text>`;
  });
  svg.innerHTML = html;
  $('qualityMeta').textContent = 'يوضح توزيع quality_score على خمس شرائح لقراءة سرعة جودة النتائج.';
}

function renderSessionChart(setups){
  const svg = $('sessionChart');
  if (!setups.length) return renderMiniEmpty('sessionChart', 'sessionMeta', 'ستظهر الجلسات الأكثر نشاطًا بمجرد توفر setups.');
  const counts = {};
  setups.forEach(s => { const k = s.session_label || 'unknown'; counts[k] = (counts[k] || 0) + 1; });
  const entries = Object.entries(counts).sort((a,b)=>b[1]-a[1]);
  const width = 860, height = 240, padX = 38, padY = 20;
  const maxY = Math.max(1, ...entries.map(e=>e[1]));
  const plotW = width - padX*2, plotH = height - padY*2, barW = plotW / Math.max(entries.length,1) * 0.55;
  let html='';
  for (let i=0;i<4;i++){ const y = padY + (plotH * i / 3); html += `<line x1="${padX}" y1="${y}" x2="${width-padX}" y2="${y}" stroke="#28406a" stroke-width="1" />`; }
  entries.forEach(([label,count], i) => {
    const x = padX + (plotW / entries.length) * i + (plotW / entries.length - barW) / 2;
    const h = (count / maxY) * plotH;
    const y = height - padY - h;
    html += `<rect x="${x}" y="${y}" width="${barW}" height="${h}" rx="12" fill="#f59e0b" opacity="0.88" />`;
    html += `<text x="${x + barW/2}" y="${height - 6}" fill="#94a3b8" font-size="12" text-anchor="middle">${label}</text>`;
    html += `<text x="${x + barW/2}" y="${Math.max(y-8, 16)}" fill="#e2e8f0" font-size="12" text-anchor="middle">${count}</text>`;
  });
  svg.innerHTML = html;
  $('sessionMeta').textContent = 'توزيع الـ setups على الجلسات للمقارنة البصرية السريعة.';
}

function updatePerformancePanels(data){
  const stats = data.stats || {};
  const setups = data.setups || [];
  $('metricTrades').textContent = stats.trades_count ?? setups.length ?? '-';
  $('metricWinRate').textContent = stats.win_rate != null ? `${stats.win_rate}%` : '-';
  $('metricAverageR').textContent = stats.average_r != null ? stats.average_r : '-';
  $('metricProfitFactor').textContent = stats.profit_factor != null ? stats.profit_factor : '-';
  const warnings = [ ...(data.warnings || []), ...(data.highlights || []), ...(stats.warnings || []) ];
  const box = $('warningsList');
  box.innerHTML = '';
  if (!warnings.length) {
    box.innerHTML = '<div class="pill">لا توجد warnings إضافية في هذا التشغيل.</div>';
  } else {
    warnings.slice(0,8).forEach(line => {
      const div = document.createElement('div');
      div.className = 'pill';
      div.textContent = line;
      box.appendChild(div);
    });
  }
  renderEquityChart(setups);
  renderQualityHistogram(setups);
  renderSessionChart(setups);
}

function updateSummaryFromRun(data){
  state.currentRun = data;
  $('statQualified').textContent = data.total_qualified ?? '-';
  $('statCandidates').textContent = data.total_candidates ?? '-';
  $('statQuality').textContent = data.stats?.avg_quality_score ?? '-';
  $('statMove').textContent = data.setups?.[0]?.outcome_move_points ?? '-';
  $('humanSummary').textContent = data.human_summary || data.strategy_summary || '-';
  $('scopeBox').textContent = fmtObj(data.search_scope || currentBacktestRange());
  $('snapshotBox').textContent = `Run: ${data.run_id || '-'}\nSummary: ${data.strategy_summary || '-'}\nQualified: ${data.total_qualified ?? '-'} / ${data.total_candidates ?? '-'}\nAvg quality: ${data.stats?.avg_quality_score ?? '-'}\nWarnings: ${[...(data.warnings||[]), ...(data.highlights||[])].slice(0,4).join(' | ') || '-'}`;
  $('currentRunSummary').textContent = `Strategy: ${data.strategy_summary || '-'}\nCandidates: ${data.total_candidates ?? '-'}\nQualified: ${data.total_qualified ?? '-'}\nAvg quality: ${data.stats?.avg_quality_score ?? '-'}\nProfit factor: ${data.stats?.profit_factor ?? '-'}\nMax drawdown: ${data.stats?.max_drawdown ?? '-'}`;
  renderHighlights(data.highlights || []);
  renderSetupTable(data.setups || []);
  updatePerformancePanels(data);
  if (data.run_id) loadRunHistory(true);
}

function renderSvgChart(candles, setup, allSetups=[]){
  const svg = $('svgChart');
  const width = 920, height = 480, padX = 50, padY = 24;
  if(!candles.length){ chartEmptyState('لا توجد بيانات شارت', 'لم تصل شموع لهذا النطاق، لذلك لا يمكن رسم المستوى أو الصفقة.'); $('chartLegend').textContent='لا توجد بيانات شارت ضمن هذا النطاق.'; return; }
  const candidates = [setup.source_low, setup.source_open, setup.source_high, setup.zone_low, setup.zone_high, setup.entry_price, setup.stop_loss, setup.take_profit].filter(x => typeof x === 'number');
  const minP = Math.min(...candles.map(c=>c.low), ...candidates);
  const maxP = Math.max(...candles.map(c=>c.high), ...candidates);
  const span = Math.max(0.0001, maxP - minP), plotW = width - padX*2, plotH = height - padY*2;
  const xFor = i => padX + (plotW * i / Math.max(1, candles.length-1));
  const yFor = p => padY + (maxP - p) / span * plotH;
  const candleW = Math.max(4, plotW / Math.max(candles.length * 2, 24));
  const sourceTs = setup.chart_focus?.source_time ? Math.floor(new Date(setup.chart_focus.source_time).getTime()/1000) : null;
  const retestTs = setup.chart_focus?.retest_time ? Math.floor(new Date(setup.chart_focus.retest_time).getTime()/1000) : null;
  const sourceIndex = sourceTs ? candles.findIndex(c => Number(c.time) >= sourceTs) : -1;
  const retestIndex = retestTs ? candles.findIndex(c => Number(c.time) >= retestTs) : -1;
  let html = `<defs>
      <linearGradient id="greenCandle" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#7dd3fc" /><stop offset="100%" stop-color="#22c55e" /></linearGradient>
      <linearGradient id="redCandle" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#fda4af" /><stop offset="100%" stop-color="#fb7185" /></linearGradient>
    </defs><rect x="0" y="0" width="${width}" height="${height}" rx="18" fill="transparent" />`;
  for (let i=0;i<5;i++) {
    const y = padY + (plotH * i / 4);
    html += `<line x1="${padX}" y1="${y}" x2="${width-padX}" y2="${y}" stroke="#183154" stroke-width="1" />`;
  }
  candles.forEach((c,i) => {
    const x = xFor(i), yH = yFor(c.high), yL = yFor(c.low), yO = yFor(c.open), yC = yFor(c.close);
    const top = Math.min(yO, yC), body = Math.max(3, Math.abs(yC - yO)), isBull = c.close >= c.open;
    html += `<line x1="${x}" y1="${yH}" x2="${x}" y2="${yL}" stroke="${isBull ? '#5eead4' : '#fca5a5'}" stroke-width="1.4" />`;
    html += `<rect x="${x-candleW/2}" y="${top}" width="${candleW}" height="${body}" rx="2" fill="${isBull ? 'url(#greenCandle)' : 'url(#redCandle)'}" opacity="0.95" />`;
  });
  const lower = typeof setup.zone_low === 'number' ? setup.zone_low : null;
  const upper = typeof setup.zone_high === 'number' ? setup.zone_high : null;
  if (typeof lower === 'number' && typeof upper === 'number') {
    const y1 = yFor(lower), y2 = yFor(upper), top = Math.min(y1,y2), zoneH = Math.max(6, Math.abs(y2-y1));
    html += `<rect x="${padX}" y="${top}" width="${width-padX*2}" height="${zoneH}" fill="rgba(56,189,248,.10)" stroke="#38bdf8" stroke-width="1.6" stroke-dasharray="8 5" />`;
  }
  [['entry_price','#22c55e','Entry / POI'],['stop_loss','#ef4444','Stop'],['take_profit','#eab308','Target']].forEach(([key,color,label], idx) => {
    const value = setup[key];
    if (typeof value === 'number') {
      const y = yFor(value);
      html += `<line x1="${padX}" y1="${y}" x2="${width-padX}" y2="${y}" stroke="${color}" stroke-width="2.2" stroke-dasharray="${key==='entry_price' ? '4 4' : '8 5'}" />`;
      html += `<text x="${width-padX-6}" y="${Math.max(18,y-8-idx*2)}" fill="${color}" font-size="12" text-anchor="end">${label}: ${fmtPrice(value)}</text>`;
    }
  });
  allSetups.forEach(other => {
    const ts = other.chart_focus?.retest_time || other.ltf_retest_time || other.htf_time;
    const t = Math.floor(new Date(ts).getTime()/1000);
    const idx = candles.findIndex(c => Number(c.time) >= t);
    const price = other.entry_price || other.entry_reference_price;
    if (idx >= 0 && typeof price === 'number') {
      const x = xFor(idx), y = yFor(price);
      const fill = other.setup_id === setup.setup_id ? '#8b5cf6' : '#38bdf8';
      html += `<circle cx="${x}" cy="${y}" r="${other.setup_id === setup.setup_id ? 6.5 : 4.5}" fill="${fill}" opacity="0.95" />`;
    }
  });
  if (sourceIndex >= 0) {
    const x = xFor(sourceIndex);
    html += `<line x1="${x}" y1="${padY}" x2="${x}" y2="${height-padY}" stroke="#f59e0b" stroke-width="2" /><text x="${x+6}" y="${padY+16}" fill="#fcd34d" font-size="12">Source</text>`;
  }
  if (retestIndex >= 0) {
    const x = xFor(retestIndex);
    html += `<line x1="${x}" y1="${padY}" x2="${x}" y2="${height-padY}" stroke="#38bdf8" stroke-width="2" /><text x="${x+6}" y="${padY+34}" fill="#7dd3fc" font-size="12">Reaction / retest</text>`;
  }
  svg.innerHTML = html;
  $('chartLegend').textContent = 'الفرصة المختارة مرسومة الآن مع نقاط الدخول والوقف والهدف، والنقاط الزرقاء الصغيرة تمثل باقي الفرص من نفس التشغيل.';
}

async function drawSetupOnChart(setup){
  const focus = setup.chart_focus || {};
  const timeframe = state.currentStrategy?.execution_timeframe || setup.execution_timeframe || $('annotationTimeframe')?.value || '5m';
  const symbol = state.currentStrategy?.symbol || APP_META.chartSymbol;
  const window = ensureChartWindow(focus, timeframe);
  let items = [];
  try {
    const data = await apiFetch('/api/chart/context', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        symbol,
        timeframe,
        start_time: window.start,
        end_time: window.end
      })
    });
    items = data.items || [];
  } catch (err) {
    chartEmptyState('تعذر جلب الشموع', 'فشل طلب chart context لهذا الـ setup.');
    throw err;
  }
  if (!items.length) {
    chartEmptyState('لا توجد شموع ضمن النطاق', `${symbol} • ${timeframe} — جرّب نافذة أوسع أو مزوّد بيانات آخر.`);
    $('chartLegend').textContent = 'لم تصل أي شموع ضمن نطاق الرسم، لذلك لم يُعرض setup بصريًا.';
    return;
  }
  renderSvgChart(items, setup, state.currentRun?.setups || []);
  scrollToSection('resultsSection');
}


function fmtPrice(value){
  const num = Number(value);
  if (!Number.isFinite(num)) return '-';
  return num.toFixed(Math.abs(num) >= 100 ? 2 : 3);
}

function chartEmptyState(title, message){
  const svg = $('svgChart');
  if (!svg) return;
  svg.innerHTML = `
    <rect x="0" y="0" width="920" height="480" rx="18" fill="#0b1220" />
    <rect x="22" y="22" width="876" height="436" rx="14" fill="rgba(15,23,42,.92)" stroke="#23324d" />
    <text x="460" y="214" text-anchor="middle" fill="#e2e8f0" font-size="24" font-weight="700">${title}</text>
    <text x="460" y="246" text-anchor="middle" fill="#94a3b8" font-size="15">${message}</text>`;
}

function renderTradePlan(setup){
  const box = $('tradePlanBox');
  if (!box) return;
  if (!setup) {
    box.textContent = 'اختر فرصة لعرض الدخول ووقف الخسارة والهدف بشكل بشري وواضح.';
    return;
  }
  const rr = setup.rr_ratio != null ? setup.rr_ratio : '-';
  const notes = (setup.execution_notes || []).slice(0,3).map(line => `<li>${line}</li>`).join('');
  box.innerHTML = `
    <div class="trade-plan-grid">
      <div><span>الاتجاه</span><strong>${setup.source_direction || '-'}</strong></div>
      <div><span>الدخول</span><strong>${fmtPrice(setup.entry_price ?? setup.entry_reference_price)}</strong></div>
      <div><span>الوقف</span><strong>${fmtPrice(setup.stop_loss)}</strong></div>
      <div><span>الهدف</span><strong>${fmtPrice(setup.take_profit)}</strong></div>
      <div><span>المخاطرة</span><strong>${fmtPrice(setup.risk_points)}</strong></div>
      <div><span>العائد</span><strong>${fmtPrice(setup.reward_points)}</strong></div>
      <div><span>RR</span><strong>${rr}</strong></div>
      <div><span>النموذج</span><strong>${setup.execution_model || '-'}</strong></div>
    </div>
    ${notes ? `<ul class="trade-plan-notes">${notes}</ul>` : ''}`;
}

function renderSetupCards(items){
  const wrap = $('setupCards');
  if (!wrap) return;
  wrap.innerHTML = '';
  if (!(items || []).length) {
    wrap.innerHTML = '<div class="list-card"><strong>لا توجد فرص</strong><span>عندما يجد النظام setups ستظهر هنا كبطاقات صفقة مفهومة بدل JSON خام.</span></div>';
    return;
  }
  items.slice(0, 12).forEach((setup, idx) => {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'trade-card';
    card.dataset.setupId = setup.setup_id;
    card.innerHTML = `
      <div class="trade-card-top">
        <strong>${setup.source_direction} ${setup.source_type}</strong>
        <span class="badge">${setup.quality_label} • ${setup.quality_score}</span>
      </div>
      <div class="trade-card-metrics">
        <span>Entry ${fmtPrice(setup.entry_price ?? setup.entry_reference_price)}</span>
        <span>SL ${fmtPrice(setup.stop_loss)}</span>
        <span>TP ${fmtPrice(setup.take_profit)}</span>
      </div>
      <div class="trade-card-meta">${(setup.htf_time || '').slice(0,16).replace('T',' ')} • ${setup.session_label || '-'} • ${setup.outcome_label || '-'}</div>`;
    card.onclick = () => selectSetup(setup, document.querySelector(`#setupTable tr[data-setup-id="${setup.setup_id}"]`), card);
    wrap.appendChild(card);
    if (idx === 0 && !state.selectedSetupId) setTimeout(() => card.click(), 0);
  });
}

function ensureChartWindow(focus, timeframe){
  const minutesMap = { '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240 };
  const tfMinutes = minutesMap[timeframe] || 5;
  const padBeforeBars = 40;
  const padAfterBars = 20;
  const fallbackEnd = focus.end_time || focus.retest_time || focus.source_time || new Date().toISOString();
  const fallbackStart = focus.start_time || focus.source_time || fallbackEnd;
  const endDate = new Date(fallbackEnd);
  const startDate = new Date(fallbackStart);
  if (!Number.isFinite(endDate.getTime()) || !Number.isFinite(startDate.getTime())) {
    const now = new Date();
    return { start: new Date(now.getTime() - 48*3600*1000).toISOString(), end: now.toISOString() };
  }
  const startPad = new Date(startDate.getTime() - padBeforeBars * tfMinutes * 60 * 1000);
  const endPadBase = endDate.getTime() <= startDate.getTime() ? startDate : endDate;
  const endPad = new Date(endPadBase.getTime() + padAfterBars * tfMinutes * 60 * 1000);
  return { start: startPad.toISOString(), end: endPad.toISOString() };
}

async function selectSetup(setup, rowEl=null, cardEl=null){
  state.selectedSetupId = setup.setup_id;
  document.querySelectorAll('.setup-row').forEach(el => el.classList.toggle('active', el.dataset.setupId === setup.setup_id));
  document.querySelectorAll('#setupCards .trade-card').forEach(el => el.classList.toggle('active', el.dataset.setupId === setup.setup_id));
  if (rowEl) rowEl.classList.add('active');
  if (cardEl) cardEl.classList.add('active');
  $('setupDetails').textContent = summarizeSetup(setup);
  renderTradePlan(setup);
  try {
    await drawSetupOnChart(setup);
  } catch (err) {
    chartEmptyState('تعذر رسم الشارت', 'تم العثور على setup لكن لم تصل بيانات الشموع أو فشل الرسم.');
    $('chartLegend').textContent = 'تعذر رسم الشموع لهذا الـ setup. جرّب setup آخر أو نافذة زمنية أوسع.';
  }
}

function renderSetupTable(items){
  const tbody = $('setupTable');
  tbody.innerHTML = '';
  state.selectedSetupId = null;
  renderSetupCards(items || []);
  (items || []).slice(0, 14).forEach((setup, idx) => {
    const tr = document.createElement('tr');
    tr.className = 'setup-row';
    tr.dataset.setupId = setup.setup_id;
    tr.innerHTML = `<td>${setup.htf_time?.slice(0,16).replace('T',' ') || '-'}</td><td>${setup.source_direction} ${setup.source_type}</td><td>${setup.quality_label} (${setup.quality_score})</td><td>${setup.outcome_label}</td><td>${setup.outcome_move_points}</td><td>${setup.session_label}</td>`;
    tr.onclick = () => selectSetup(setup, tr, document.querySelector(`#setupCards .trade-card:nth-child(${idx+1})`));
    tbody.appendChild(tr);
    if (idx === 0) setTimeout(() => tr.click(), 0);
  });
  if (!(items || []).length) {
    $('setupDetails').textContent = 'لا توجد فرص لعرضها.';
    $('chartLegend').textContent = 'لا توجد توصية محددة بعد.';
    $('svgChart').innerHTML = '';
  }
}

async function interpretStrategy(){
  showStatus(true);
  try {
    const text = $('prompt').value;
    const data = await apiFetch('/api/strategy/interpret', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text})
    });
    state.currentStrategy = data.understood;
    $('preview').textContent = data.preview_text;
    $('warnings').textContent = [...(data.warnings||[]), ...(data.follow_up_questions||[])].join('\n') || '-';
    $('scopeBox').textContent = 'سيتم اعتماد الفترة التي تختارها من الحقول عند تشغيل الباك تست.\n\n' + fmtObj({ runtime_backtest_range: currentBacktestRange() });
    $('strategyInspector').textContent = fmtObj(data.understood);
    toast('تم الفهم', 'تم تفسير الاستراتيجية بنجاح.', 'success');
  } finally { showStatus(false); }
}

async function applyCorrection(){
  if (!state.currentStrategy) await interpretStrategy();
  showStatus(true);
  try {
    const correction_text = $('correctionText').value;
    const data = await apiFetch('/api/strategy/correct', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ strategy: state.currentStrategy, correction_text })
    });
    state.currentStrategy = data.understood;
    $('preview').textContent = data.preview_text;
    $('warnings').textContent = [...(data.warnings||[]), ...(data.follow_up_questions||[])].join('\n') || '-';
    $('strategyInspector').textContent = fmtObj(data.understood);
    toast('تم التعديل', 'تم تطبيق التصحيح على الاستراتيجية.', 'success');
    await loadPrefs();
  } finally { showStatus(false); }
}

async function runResearch(){
  if (!state.currentStrategy) await interpretStrategy();
  showStatus(true);
  try {
    const payload = { strategy: state.currentStrategy, backtest_range: currentBacktestRange() };
    const data = await apiFetch('/api/research/run', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
    });
    updateSummaryFromRun(data);
    switchWorkspace('runsWorkspace');
    await loadRunHistory(true);
    toast('اكتمل التشغيل', `تمت معالجة ${data.total_candidates ?? 0} مرشحًا.`, 'success');
  } finally { showStatus(false); }
}

async function optimizeStrategy(){
  if (!state.currentStrategy) await interpretStrategy();
  showStatus(true);
  try {
    const data = await apiFetch('/api/presets/optimize', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ strategy: state.currentStrategy, backtest_range: currentBacktestRange() })
    });
    const box = $('optimizerBox');
    box.innerHTML = '';
    (data.variants || []).forEach((item, idx) => {
      const div = document.createElement('div');
      div.className = 'list-card' + (idx === 0 ? ' active' : '');
      div.innerHTML = `<strong>${item.primary_timeframe} → ${item.execution_timeframe} <span class="muted">| score ${item.score}</span></strong><span>${item.total_qualified} setups | avg quality ${item.avg_quality_score}</span><span>${item.summary}</span>`;
      div.onclick = () => {
        document.querySelectorAll('#optimizerBox .list-card').forEach(x => x.classList.remove('active'));
        div.classList.add('active');
        updateSummaryFromRun(item.response);
        if (state.currentStrategy) {
          state.currentStrategy.primary_timeframe = item.primary_timeframe;
          state.currentStrategy.execution_timeframe = item.execution_timeframe;
          state.currentStrategy.timeframe = item.primary_timeframe;
        }
      };
      box.appendChild(div);
    });
    if (data.best?.response) updateSummaryFromRun(data.best.response);
    toast('تم التحسين', 'تم تقييم بدائل الفريمات وعرض الأفضل.', 'success');
  } finally { showStatus(false); }
}

async function saveStrategy(){
  if (!state.currentStrategy) await interpretStrategy();
  const name = prompt('اسم الاستراتيجية؟', 'pro-strategy') || 'pro-strategy';
  await apiFetch('/api/strategy/save', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ name, strategy: state.currentStrategy })
  });
  toast('تم الحفظ', `تم حفظ الاستراتيجية باسم ${name}.`, 'success');
  await loadStrategies();
  await loadPrefs();
}

function filterStrategies(term){
  const q = term.trim().toLowerCase();
  const items = !q ? state.strategies : state.strategies.filter(item => {
    const hay = [item.name, item.primary_timeframe, item.execution_timeframe, item.direction, ...(item.steps || [])].join(' ').toLowerCase();
    return hay.includes(q);
  });
  renderStrategies(items);
}

function renderStrategies(items){
  const list = $('savedList');
  list.innerHTML = '';
  if (!(items || []).length) {
    list.innerHTML = '<div class="list-card"><strong>لا توجد نتائج</strong><span>جرّب بحثًا آخر أو احفظ استراتيجية جديدة.</span></div>';
    return;
  }
  items.forEach(item => {
    const div = document.createElement('div');
    div.className = 'strategy-card';
    div.innerHTML = `<strong>${item.name}</strong><span>${item.primary_timeframe} → ${item.execution_timeframe} | ${item.direction}</span><span>${(item.steps || []).join(' • ') || 'No steps'}</span>`;
    div.onclick = async () => {
      document.querySelectorAll('.strategy-card').forEach(x => x.classList.remove('active'));
      div.classList.add('active');
      const full = await apiFetch('/api/strategy/' + item.id);
      state.currentStrategy = full.strategy;
      $('strategyInspector').textContent = fmtObj(full.strategy);
      $('preview').textContent = fmtObj(full.strategy);
      $('prompt').value = full.name + ' preset';
      toast('تم التحميل', `تم تحميل ${full.name}.`, 'success');
      switchWorkspace('researchWorkspace');
      scrollToSection('workspaceSection');
    };
    list.appendChild(div);
  });
}

async function loadStrategies(){
  const data = await apiFetch('/api/strategy/list');
  state.strategies = data.items || [];
  $('strategyCountHero').textContent = state.strategies.length;
  $('railStrategyCount').textContent = state.strategies.length;
  renderStrategies(state.strategies);
}

function renderRunTrend(items){
  const svg = $('runTrendChart');
  if (!items.length) {
    svg.innerHTML = '';
    $('runTrendMeta').textContent = 'لا توجد بيانات runs بعد لعرض اتجاه الجودة.';
    return;
  }
  const width = 860, height = 240, padX = 38, padY = 20;
  const scores = items.map((r, i) => ({ x: i, score: Number(r.avg_quality_score || 0), setups: Number(r.total_qualified || 0) }));
  const maxY = Math.max(10, ...scores.map(x => x.score), ...scores.map(x => x.setups * 10));
  const plotW = width - padX * 2, plotH = height - padY * 2;
  const xFor = i => padX + (plotW * i / Math.max(1, scores.length - 1));
  const yFor = v => padY + (maxY - v) / maxY * plotH;
  const line1 = scores.map((p, i) => `${i===0?'M':'L'} ${xFor(i)} ${yFor(p.score)}`).join(' ');
  const line2 = scores.map((p, i) => `${i===0?'M':'L'} ${xFor(i)} ${yFor(p.setups * 10)}`).join(' ');
  let html = `<defs>
      <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(139,92,246,.45)" /><stop offset="100%" stop-color="rgba(139,92,246,0)" /></linearGradient>
    </defs>`;
  for(let i=0;i<4;i++){
    const y = padY + (plotH * i / 3);
    html += `<line x1="${padX}" y1="${y}" x2="${width-padX}" y2="${y}" stroke="#28406a" stroke-width="1" />`;
  }
  html += `<path d="${line1} L ${xFor(scores.length-1)} ${height-padY} L ${xFor(0)} ${height-padY} Z" fill="url(#areaGrad)" opacity="0.55" />`;
  html += `<path d="${line1}" fill="none" stroke="#8b5cf6" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />`;
  html += `<path d="${line2}" fill="none" stroke="#38bdf8" stroke-width="2.5" stroke-dasharray="6 6" stroke-linecap="round" stroke-linejoin="round" />`;
  scores.forEach((p, i) => {
    html += `<circle cx="${xFor(i)}" cy="${yFor(p.score)}" r="4.2" fill="#8b5cf6" />`;
  });
  svg.innerHTML = html;
  $('runTrendMeta').textContent = 'البنفسجي = avg quality score، والأزرق المتقطع = عدد الـ setups × 10 للمقارنة البصرية.';
}

function filterRuns(term){
  const q = term.trim().toLowerCase();
  const items = !q ? state.runs : state.runs.filter(run => {
    const hay = [run.id, run.summary, run.created_at, run.total_qualified, run.avg_quality_score].join(' ').toLowerCase();
    return hay.includes(q);
  });
  renderRunHistory(items);
  renderRunTrend(items.slice().reverse());
}

function renderRunHistory(items){
  const target = $('historyRuns');
  target.innerHTML = '';
  if (!(items || []).length) {
    target.innerHTML = '<div class="list-card"><strong>لا توجد تشغيلات</strong><span>شغّل backtest أو scan ليظهر السجل هنا.</span></div>';
    return;
  }
  items.forEach(run => {
    const div = document.createElement('div');
    div.className = 'run-card';
    div.innerHTML = `<strong>${run.id || 'Run'}</strong><span>${run.created_at || '-'} | ${run.total_qualified ?? 0} setups</span><span>${run.summary || 'سجل تشغيل محفوظ.'}</span>`;
    div.onclick = async () => {
      document.querySelectorAll('.run-card').forEach(x => x.classList.remove('active'));
      div.classList.add('active');
      const data = await apiFetch('/api/research/runs/' + run.id);
      const payload = data.response || data;
      if (payload.request?.strategy) state.currentStrategy = payload.request.strategy;
      updateSummaryFromRun(payload.response || payload);
      $('runInspector').textContent = fmtObj(payload);
      toast('تم تحميل run', 'تمت استعادة تفاصيل تشغيل محفوظ.', 'success');
      scrollToSection('resultsSection');
    };
    target.appendChild(div);
  });
}

async function loadRunHistory(silent=false){
  const runs = await apiFetch('/api/research/runs');
  state.runs = Array.isArray(runs.items) ? runs.items : [];
  $('runCountHero').textContent = state.runs.length;
  $('railRunCount').textContent = state.runs.length;
  renderRunHistory(state.runs);
  renderRunTrend(state.runs.slice().reverse());
  if (!silent) toast('تم التحديث', 'تم تحديث سجل التشغيلات.', 'success');
}

function setAnnotationDrawMode(mode){
  state.annotation.mode = mode;
  document.querySelectorAll('.tool-btn').forEach(btn => btn.classList.remove('active'));
  const map = { line: 'toolLine', zone: 'toolZone', point: 'toolPoint' };
  const target = $(map[mode]);
  if (target) target.classList.add('active');
  const labels = {
    line: 'Level: اضغط واسحب أفقيًا لتحديد مستوى سعري واحد.',
    zone: 'Zone: اضغط واسحب عموديًا لتحديد نطاق سعري.',
    point: 'Point: اضغط على المكان المطلوب لتسجيل entry أو stop أو poi.'
  };
  $('annotationHint').textContent = labels[mode] || 'ارسم فوق الشارت ثم احفظ المثال.';
}

function annotationBounds(){
  return { width: 920, height: 480, padX: 50, padY: 24 };
}

function buildAnnotationGeometry(candles){
  const { width, height, padX, padY } = annotationBounds();
  const minP = Math.min(...candles.map(c => c.low));
  const maxP = Math.max(...candles.map(c => c.high));
  const span = Math.max(0.0001, maxP - minP);
  const plotW = width - padX * 2;
  const plotH = height - padY * 2;
  return {
    width, height, padX, padY, minP, maxP, span, plotW, plotH,
    xFor: (i) => padX + (plotW * i / Math.max(1, candles.length - 1)),
    yFor: (p) => padY + ((maxP - p) / span * plotH),
    priceForY: (y) => maxP - ((y - padY) / plotH) * span,
  };
}

function parseAnnotationFromJson(raw){
  try {
    const parsed = JSON.parse(raw || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    return null;
  }
}

function syncAnnotationTextarea(){
  $('exampleJson').value = JSON.stringify(state.annotation.items, null, 2);
}

function annotationTypeColor(type){
  const palette = {
    snr: { line: '#f59e0b', fill: 'rgba(245,158,11,.18)' },
    reaction: { line: '#38bdf8', fill: 'rgba(56,189,248,.18)' },
    fvg: { line: '#8b5cf6', fill: 'rgba(139,92,246,.18)' },
    poi: { line: '#22c55e', fill: 'rgba(34,197,94,.18)' },
    entry: { line: '#14b8a6', fill: 'rgba(20,184,166,.18)' },
    stop_loss: { line: '#ef4444', fill: 'rgba(239,68,68,.18)' },
    target: { line: '#eab308', fill: 'rgba(234,179,8,.18)' },
  };
  return palette[type] || { line: '#cbd5e1', fill: 'rgba(203,213,225,.18)' };
}

function normalizeAnnotationItem(item){
  return {
    type: item.type || $('annotationType')?.value || 'snr',
    label: item.label || $('annotationLabel')?.value || 'annotation',
    timeframe: item.timeframe || $('annotationTimeframe')?.value || state.annotation.timeframe || '3m',
    price_start: item.price_start != null ? Number(item.price_start) : null,
    price_end: item.price_end != null ? Number(item.price_end) : null,
    timestamp_start: item.timestamp_start || null,
    timestamp_end: item.timestamp_end || null,
    note: item.note || null,
  };
}

function drawAnnotationCandles(candles, geom){
  const candleW = Math.max(4, geom.plotW / Math.max(candles.length * 2, 24));
  let html = `
    <defs>
      <linearGradient id="annotationBg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#0c1830" /><stop offset="100%" stop-color="#091220" /></linearGradient>
      <linearGradient id="annotationGreen" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#7dd3fc" /><stop offset="100%" stop-color="#22c55e" /></linearGradient>
      <linearGradient id="annotationRed" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#fda4af" /><stop offset="100%" stop-color="#fb7185" /></linearGradient>
    </defs>
    <rect x="0" y="0" width="${geom.width}" height="${geom.height}" rx="18" fill="url(#annotationBg)" />`;
  for (let i = 0; i < 5; i++) {
    const y = geom.padY + (geom.plotH * i / 4);
    html += `<line x1="${geom.padX}" y1="${y}" x2="${geom.width-geom.padX}" y2="${y}" stroke="#183154" stroke-width="1" />`;
  }
  candles.forEach((c, i) => {
    const x = geom.xFor(i), yH = geom.yFor(c.high), yL = geom.yFor(c.low), yO = geom.yFor(c.open), yC = geom.yFor(c.close);
    const top = Math.min(yO, yC), body = Math.max(3, Math.abs(yC - yO)), isBull = c.close >= c.open;
    html += `<line x1="${x}" y1="${yH}" x2="${x}" y2="${yL}" stroke="${isBull ? '#5eead4' : '#fca5a5'}" stroke-width="1.4" />`;
    html += `<rect x="${x-candleW/2}" y="${top}" width="${candleW}" height="${body}" rx="2" fill="${isBull ? 'url(#annotationGreen)' : 'url(#annotationRed)'}" opacity="0.95" />`;
  });
  return html;
}

function drawAnnotationItems(items, geom, preview=null){
  let html = '';
  const all = [...(items || [])];
  if (preview) all.push({ ...preview, __preview: true });
  all.forEach((item, idx) => {
    const ann = normalizeAnnotationItem(item);
    if (ann.price_start == null) return;
    const colors = annotationTypeColor(ann.type);
    const opacity = item.__preview ? 0.55 : 1;
    const y1 = geom.yFor(ann.price_start);
    const y2 = ann.price_end != null ? geom.yFor(ann.price_end) : y1;
    const label = `${ann.type}: ${ann.label}`;
    if (ann.price_end != null && Math.abs(ann.price_end - ann.price_start) > 0.00001) {
      const top = Math.min(y1, y2);
      const height = Math.max(6, Math.abs(y2 - y1));
      html += `<rect x="${geom.padX}" y="${top}" width="${geom.width - geom.padX * 2}" height="${height}" fill="${colors.fill}" stroke="${colors.line}" stroke-width="2" opacity="${opacity}" />`;
      html += `<text x="${geom.padX + 8}" y="${Math.max(18, top - 8)}" fill="${colors.line}" font-size="12" font-weight="700">${label}</text>`;
    } else if (ann.timestamp_start && ann.timestamp_end && ann.timestamp_start !== ann.timestamp_end) {
      html += `<line x1="${geom.padX}" y1="${y1}" x2="${geom.width - geom.padX}" y2="${y1}" stroke="${colors.line}" stroke-width="2.4" stroke-dasharray="8 5" opacity="${opacity}" />`;
      html += `<text x="${geom.padX + 8}" y="${Math.max(18, y1 - 8)}" fill="${colors.line}" font-size="12" font-weight="700">${label}</text>`;
    } else {
      const x = ann.timestamp_start ? annotationXForTimestamp(ann.timestamp_start, geom) : geom.width / 2;
      html += `<circle cx="${x}" cy="${y1}" r="6" fill="${colors.line}" opacity="${opacity}" />`;
      html += `<text x="${x + 10}" y="${Math.max(18, y1 - 8)}" fill="${colors.line}" font-size="12" font-weight="700">${label}</text>`;
    }
  });
  return html;
}

function annotationXForTimestamp(timestamp, geom){
  const candles = state.annotation.candles || [];
  const ts = Math.floor(new Date(timestamp).getTime() / 1000);
  if (!candles.length || !ts) return geom.width / 2;
  let bestIndex = 0;
  let bestDiff = Infinity;
  candles.forEach((c, idx) => {
    const diff = Math.abs(Number(c.time) - ts);
    if (diff < bestDiff) { bestDiff = diff; bestIndex = idx; }
  });
  return geom.xFor(bestIndex);
}

function renderAnnotationBoard(){
  const svg = $('annotationChart');
  if (!svg) return;
  const candles = state.annotation.candles || [];
  if (!candles.length) {
    svg.innerHTML = '';
    $('annotationHint').textContent = 'حمّل الشارت أولًا، أو استخدم آخر setup معروض داخل Results Hub.';
    return;
  }
  const geom = state.annotation.geometry || buildAnnotationGeometry(candles);
  state.annotation.geometry = geom;
  const html = drawAnnotationCandles(candles, geom) + drawAnnotationItems(state.annotation.items, geom, state.annotation.preview);
  svg.innerHTML = html;
}

function annotationEventPoint(evt){
  const svg = $('annotationChart');
  const rect = svg.getBoundingClientRect();
  const { width, height } = annotationBounds();
  const x = ((evt.clientX - rect.left) / Math.max(rect.width, 1)) * width;
  const y = ((evt.clientY - rect.top) / Math.max(rect.height, 1)) * height;
  return { x, y };
}

function snapAnnotationPoint(point){
  const candles = state.annotation.candles || [];
  const geom = state.annotation.geometry;
  if (!candles.length || !geom) return null;
  let bestIndex = 0;
  let bestDiff = Infinity;
  candles.forEach((c, idx) => {
    const diff = Math.abs(geom.xFor(idx) - point.x);
    if (diff < bestDiff) { bestDiff = diff; bestIndex = idx; }
  });
  const candle = candles[bestIndex];
  const price = Number(geom.priceForY(Math.min(geom.height - geom.padY, Math.max(geom.padY, point.y))).toFixed(3));
  return {
    index: bestIndex,
    candle,
    timestamp: new Date(Number(candle.time) * 1000).toISOString(),
    price,
    x: geom.xFor(bestIndex),
    y: geom.yFor(price)
  };
}

function buildAnnotationItem(startSnap, endSnap=null){
  const mode = state.annotation.mode;
  const type = $('annotationType')?.value || 'snr';
  const label = ($('annotationLabel')?.value || type).trim();
  const timeframe = $('annotationTimeframe')?.value || state.annotation.timeframe || '3m';
  const item = { type, label, timeframe, note: null, price_start: startSnap.price, timestamp_start: startSnap.timestamp };
  if (mode === 'zone' && endSnap) {
    item.price_end = endSnap.price;
    item.timestamp_end = endSnap.timestamp;
  } else if (mode === 'line') {
    item.timestamp_end = state.annotation.endTime || endSnap?.timestamp || startSnap.timestamp;
  }
  return normalizeAnnotationItem(item);
}

function pointerDownAnnotation(evt){
  if (!(state.annotation.candles || []).length) return;
  const snap = snapAnnotationPoint(annotationEventPoint(evt));
  if (!snap) return;
  if (state.annotation.mode === 'point') {
    state.annotation.items.push(buildAnnotationItem(snap));
    syncAnnotationTextarea();
    renderAnnotationBoard();
    return;
  }
  state.annotation.drawing = true;
  state.annotation.start = snap;
  state.annotation.preview = buildAnnotationItem(snap, snap);
}

function pointerMoveAnnotation(evt){
  if (!state.annotation.drawing || !state.annotation.start) return;
  const snap = snapAnnotationPoint(annotationEventPoint(evt));
  if (!snap) return;
  state.annotation.preview = buildAnnotationItem(state.annotation.start, snap);
  renderAnnotationBoard();
}

function pointerUpAnnotation(evt){
  if (!state.annotation.drawing || !state.annotation.start) return;
  const snap = snapAnnotationPoint(annotationEventPoint(evt)) || state.annotation.start;
  state.annotation.items.push(buildAnnotationItem(state.annotation.start, snap));
  state.annotation.drawing = false;
  state.annotation.start = null;
  state.annotation.preview = null;
  syncAnnotationTextarea();
  renderAnnotationBoard();
}

function bindAnnotationBoard(){
  const svg = $('annotationChart');
  if (!svg || svg.dataset.bound === '1') return;
  svg.addEventListener('pointerdown', pointerDownAnnotation);
  svg.addEventListener('pointermove', pointerMoveAnnotation);
  svg.addEventListener('pointerup', pointerUpAnnotation);
  svg.addEventListener('pointerleave', pointerUpAnnotation);
  svg.dataset.bound = '1';
}

function inferAnnotationWindow(){
  const from = $('fromDate')?.value;
  const to = $('toDate')?.value;
  const end = to ? new Date(`${to}T23:59:59Z`) : new Date();
  const start = from ? new Date(`${from}T00:00:00Z`) : new Date(end.getTime() - 7 * 24 * 3600 * 1000);
  return { start: start.toISOString(), end: end.toISOString() };
}

async function loadAnnotationChart(){
  bindAnnotationBoard();
  const strategy = state.currentStrategy || {};
  const tf = $('annotationTimeframe')?.value || strategy.execution_timeframe || '3m';
  const symbol = strategy.symbol || APP_META.chartSymbol;
  const window = inferAnnotationWindow();
  showStatus(true);
  try {
    const data = await apiFetch('/api/chart/context', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, timeframe: tf, start_time: window.start, end_time: window.end })
    });
    state.annotation.candles = data.items || [];
    state.annotation.symbol = symbol;
    state.annotation.timeframe = tf;
    state.annotation.startTime = window.start;
    state.annotation.endTime = window.end;
    state.annotation.geometry = buildAnnotationGeometry(state.annotation.candles);
    const parsed = parseAnnotationFromJson($('exampleJson').value);
    if (parsed) state.annotation.items = parsed.map(normalizeAnnotationItem);
    renderAnnotationBoard();
    toast('تم تحميل الشارت', `تم تجهيز ${symbol} على ${tf} للرسم اليدوي.`, 'success');
  } finally {
    showStatus(false);
  }
}

async function loadSelectedSetupIntoAnnotation(){
  const run = state.currentRun;
  const setupId = state.selectedSetupId;
  if (!run || !setupId) {
    toast('لا يوجد setup محدد', 'اختر setup من جدول النتائج أولًا أو حمّل الشارت يدويًا.', 'error');
    return;
  }
  const setup = (run.setups || []).find(item => item.setup_id === setupId) || run.setups?.[0];
  if (!setup) {
    toast('تعذر التحميل', 'لم أجد setup صالحًا لتحميله داخل لوحة annotation.', 'error');
    return;
  }
  const focus = setup.chart_focus || {};
  const symbol = state.currentStrategy?.symbol || APP_META.chartSymbol;
  const timeframe = $('annotationTimeframe')?.value || state.currentStrategy?.execution_timeframe || '3m';
  bindAnnotationBoard();
  showStatus(true);
  try {
    const data = await apiFetch('/api/chart/context', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol,
        timeframe,
        start_time: focus.start_time || setup.htf_time,
        end_time: focus.end_time || setup.ltf_retest_time || setup.htf_time
      })
    });
    state.annotation.candles = data.items || [];
    state.annotation.symbol = symbol;
    state.annotation.timeframe = timeframe;
    state.annotation.startTime = focus.start_time || setup.htf_time;
    state.annotation.endTime = focus.end_time || setup.ltf_retest_time || setup.htf_time;
    state.annotation.geometry = buildAnnotationGeometry(state.annotation.candles);
    state.annotation.items = [
      normalizeAnnotationItem({ type:'snr', label:'HTF level', timeframe: state.currentStrategy?.primary_timeframe || '30m', price_start: setup.zone_low, price_end: setup.zone_high, timestamp_start: focus.source_time || setup.htf_time, timestamp_end: focus.end_time || setup.ltf_retest_time || setup.htf_time }),
      normalizeAnnotationItem({ type:'reaction', label:'Reaction', timeframe, price_start: setup.entry_reference_price, timestamp_start: focus.retest_time || setup.ltf_retest_time || setup.htf_time }),
      normalizeAnnotationItem({ type:'fvg', label:'FVG', timeframe, price_start: focus.fvg_low || setup.zone_low, price_end: focus.fvg_high || setup.zone_high, timestamp_start: focus.retest_time || setup.ltf_retest_time || setup.htf_time, timestamp_end: focus.end_time || setup.ltf_retest_time || setup.htf_time }),
      normalizeAnnotationItem({ type:'poi', label:'POI', timeframe, price_start: setup.entry_price || setup.entry_reference_price, timestamp_start: focus.retest_time || setup.ltf_retest_time || setup.htf_time }),
      normalizeAnnotationItem({ type:'stop_loss', label:'Stop', timeframe, price_start: setup.stop_loss, timestamp_start: focus.retest_time || setup.ltf_retest_time || setup.htf_time }),
      normalizeAnnotationItem({ type:'target', label:'Target', timeframe, price_start: setup.take_profit, timestamp_start: focus.retest_time || setup.ltf_retest_time || setup.htf_time })
    ].filter(item => item.price_start != null);
    syncAnnotationTextarea();
    renderAnnotationBoard();
    toast('تم تحميل setup', 'تم وضع المستوى وFVG وPOI والـ Stop/Target تلقائيًا فوق الشارت لتبدأ من شيء فعلي، لا من لوحة فارغة.', 'success');
    switchWorkspace('templateWorkspace');
  } finally {
    showStatus(false);
  }
}

function syncExampleJsonToBoard(){
  const parsed = parseAnnotationFromJson($('exampleJson').value);
  if (!parsed) {
    toast('JSON غير صالح', 'عدّل JSON أولًا أو أعد نسخه قبل الاستيراد إلى الشارت.', 'error');
    return;
  }
  state.annotation.items = parsed.map(normalizeAnnotationItem);
  renderAnnotationBoard();
  toast('تمت المزامنة', 'تم تحميل annotations من JSON إلى لوحة الرسم.', 'success');
}

function undoAnnotation(){
  if (!state.annotation.items.length) return;
  state.annotation.items.pop();
  syncAnnotationTextarea();
  renderAnnotationBoard();
}

function clearAnnotations(){
  state.annotation.items = [];
  state.annotation.preview = null;
  state.annotation.start = null;
  state.annotation.drawing = false;
  syncAnnotationTextarea();
  renderAnnotationBoard();
}

async function loadTemplates(){
  const data = await apiFetch('/api/templates');
  state.templates = data.items || [];
  const box = $('presetGrid');
  box.innerHTML = '';
  state.templates.forEach(item => {
    const card = document.createElement('div');
    card.className = 'preset-card';
    card.innerHTML = `<strong>${item.label}</strong><span>${item.description}</span><span>${(item.supported_steps || []).join(' • ')}</span>`;
    card.onclick = () => {
      document.querySelectorAll('.preset-card').forEach(x => x.classList.remove('active'));
      card.classList.add('active');
      $('prompt').value = item.example_prompt;
      toast('تم اختيار قالب', 'القالب جاهز الآن داخل Composer.', 'info');
    };
    box.appendChild(card);
  });
}

async function loadPrefs(){
  const data = await apiFetch('/api/preferences');
  $('prefsBox').textContent = fmtObj(data);
}

async function refreshStatusPanel(){
  try {
    const [health, ready, version] = await Promise.all([apiFetch('/health'), apiFetch('/ready'), apiFetch('/version')]);
    const healthLabel = health.status === 'healthy' ? 'Healthy' : (health.status || 'Unknown');
    $('heroHealth').innerHTML = `<span class="status-dot ok">${healthLabel}</span>`;
    $('heroHealthRail').className = 'status-dot ok';
    $('heroHealthRail').textContent = 'Healthy';
    $('diagHealth').textContent = healthLabel;
    $('diagReady').textContent = ready.status || 'ready';
    $('diagVersion').textContent = version.version || APP_META.appVersion;
    $('versionChip').textContent = `v${version.version || APP_META.appVersion}`;
    $('envMetric').textContent = version.env || '-';
    $('envBox').textContent = version.env || '-';
  } catch (_) {
    $('heroHealth').innerHTML = `<span class="status-dot error">Unavailable</span>`;
    $('heroHealthRail').className = 'status-dot error';
    $('heroHealthRail').textContent = 'Unavailable';
    $('diagHealth').textContent = 'Unavailable';
    $('diagReady').textContent = 'Error';
  }
}

async function bootDashboard(silent=true){
  try {
    await Promise.all([loadTemplates(), loadStrategies(), loadStrategyTemplates(), loadPrefs(), refreshStatusPanel(), loadRunHistory(true)]);
    if (!silent) toast('تم التحديث', 'تم تحديث الواجهة والبيانات الأساسية.', 'success');
  } catch (_) { /* apiFetch handles UI */ }
}

$('themeToggle').addEventListener('click', () => setTheme(document.body.getAttribute('data-theme') === 'light' ? 'dark' : 'light'));
$('strategySearch').addEventListener('input', e => filterStrategies(e.target.value));
$('templateSearch')?.addEventListener('input', e => filterStrategyTemplates(e.target.value));
$('runSearch').addEventListener('input', e => filterRuns(e.target.value));
$('annotationTimeframe')?.addEventListener('change', e => { state.annotation.timeframe = e.target.value; });
$('exampleJson')?.addEventListener('change', () => syncExampleJsonToBoard());

initTheme();
setThisWeek();
bindAnnotationBoard();
setAnnotationDrawMode('line');
bootDashboard(true);


// ─── Pattern Teacher ─────────────────────────────────────────────────────────

const ptState = {
  points: [],        // {type: "high"|"low"|"zone", price: number, timestamp: string, label: string, is_zone: bool}
  drawMode: 'high',  // "high", "low", "zone"
  candles: [],
  geometry: null,
  chartLoaded: false,
  savedPatterns: [],
};

function ptSetDrawMode(mode) {
  ptState.drawMode = mode;
  document.querySelectorAll('#patternTeacherWorkspace .annotation-toolbar .tool-btn').forEach(btn => btn.classList.remove('active'));
  const labels = {'high': 'قمة (H)', 'low': 'قاع (L)', 'zone': 'الزون'};
  document.querySelectorAll('#patternTeacherWorkspace .annotation-toolbar .tool-btn').forEach(btn => {
    if (btn.textContent.trim() === labels[mode]) btn.classList.add('active');
  });
  const hint = mode === 'zone' ? 'اضغط على الشارت لتحديد الزون (منطقة الدخول)' : `اضغط على ${mode === 'high' ? 'القمم' : 'القيعان'} بالترتيب`;
  $('ptChartHint').textContent = hint;
}

async function ptLoadChart() {
  const tf = $('ptHTF').value;
  const symbol = APP_META.chartSymbol;
  showStatus(true);
  try {
    const now = new Date();
    const start = new Date(now.getTime() - 30 * 24 * 3600 * 1000);
    const res = await apiFetch('/api/chart/context', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol, timeframe: tf, start_time: start.toISOString(), end_time: now.toISOString()})
    });
    ptState.candles = res.items || [];
    ptState.chartLoaded = true;
    ptState.points = [];
    ptRenderChart();
    ptRenderPoints();
    $('ptChartHint').textContent = `تم تحميل ${ptState.candles.length} شمعة على ${tf}. ابدأ بتحديد النقاط.`;
    toast('الشارت جاهز', `${ptState.candles.length} شمعة على ${tf}`, 'success');
  } catch (e) {
    toast('خطأ', 'فشل تحميل الشارت: ' + e.message, 'error');
  }
  showStatus(false);
}

function ptRenderChart() {
  const svg = $('ptChart');
  if (!svg || !ptState.candles.length) return;
  const candles = ptState.candles.slice(-200); // Show last 200
  const W = 920, H = 400, PAD = 40;
  const highs = candles.map(c => c.high);
  const lows = candles.map(c => c.low);
  const maxP = Math.max(...highs);
  const minP = Math.min(...lows);
  const range = maxP - minP || 1;
  const xStep = (W - PAD * 2) / candles.length;
  const yScale = (p) => PAD + ((maxP - p) / range) * (H - PAD * 2);

  ptState.geometry = {candles, W, H, PAD, maxP, minP, range, xStep, yScale};

  let html = `<rect width="${W}" height="${H}" fill="var(--surface, #141720)" rx="8"/>`;
  // Price grid
  for (let i = 0; i <= 4; i++) {
    const p = minP + (range * i / 4);
    const y = yScale(p);
    html += `<line x1="${PAD}" y1="${y}" x2="${W-PAD}" y2="${y}" stroke="var(--border, #2a2e3a)" stroke-width="0.5"/>`;
    html += `<text x="${PAD-4}" y="${y+4}" text-anchor="end" fill="var(--muted, #888)" font-size="10">${p.toFixed(1)}</text>`;
  }
  // Candles
  candles.forEach((c, i) => {
    const x = PAD + i * xStep + xStep / 2;
    const oY = yScale(c.open), cY = yScale(c.close), hY = yScale(c.high), lY = yScale(c.low);
    const bull = c.close >= c.open;
    const color = bull ? 'var(--green, #22c55e)' : 'var(--red, #ef4444)';
    html += `<line x1="${x}" y1="${hY}" x2="${x}" y2="${lY}" stroke="${color}" stroke-width="1"/>`;
    html += `<rect x="${x - xStep*0.35}" y="${Math.min(oY,cY)}" width="${xStep*0.7}" height="${Math.max(1, Math.abs(oY-cY))}" fill="${color}" rx="1"/>`;
  });

  // Draw marked points
  ptState.points.forEach((pt, idx) => {
    const candleIdx = ptFindCandleIndex(pt.timestamp);
    if (candleIdx < 0) return;
    const x = PAD + candleIdx * xStep + xStep / 2;
    const y = yScale(pt.price);
    const color = pt.type === 'high' ? '#f59e0b' : pt.type === 'low' ? '#3b82f6' : '#a855f7';
    const label = pt.is_zone ? 'Z' : (pt.type === 'high' ? 'H' : 'L');
    html += `<circle cx="${x}" cy="${y}" r="6" fill="${color}" stroke="white" stroke-width="1.5"/>`;
    html += `<text x="${x}" y="${y - 10}" text-anchor="middle" fill="${color}" font-size="11" font-weight="bold">${label}${idx+1}</text>`;
    if (pt.is_zone) {
      const zoneH = (pt.zone_high || pt.price * 1.002);
      html += `<rect x="${PAD}" y="${yScale(zoneH)}" width="${W - PAD*2}" height="${yScale(pt.price) - yScale(zoneH)}" fill="${color}" opacity="0.15" rx="3"/>`;
    }
  });

  svg.innerHTML = html;

  // Click handler
  svg.onclick = (e) => ptHandleChartClick(e);
}

function ptFindCandleIndex(timestamp) {
  if (!ptState.geometry) return -1;
  const candles = ptState.geometry.candles;
  const ts = new Date(timestamp).getTime() / 1000;
  let closest = 0, minDiff = Infinity;
  candles.forEach((c, i) => {
    const diff = Math.abs(c.time - ts);
    if (diff < minDiff) { minDiff = diff; closest = i; }
  });
  return closest;
}

function ptHandleChartClick(e) {
  if (!ptState.chartLoaded || !ptState.geometry) return;
  const svg = $('ptChart');
  const rect = svg.getBoundingClientRect();
  const svgX = (e.clientX - rect.left) / rect.width * ptState.geometry.W;
  const svgY = (e.clientY - rect.top) / rect.height * ptState.geometry.H;

  const {candles, PAD, xStep, maxP, range, H} = ptState.geometry;
  const candleIdx = Math.floor((svgX - PAD) / xStep);
  if (candleIdx < 0 || candleIdx >= candles.length) return;

  const candle = candles[candleIdx];
  const price = maxP - ((svgY - PAD) / (H - PAD * 2)) * range;

  // Snap to candle high/low based on mode
  let snapPrice = price;
  if (ptState.drawMode === 'high') snapPrice = candle.high;
  else if (ptState.drawMode === 'low') snapPrice = candle.low;

  const point = {
    type: ptState.drawMode === 'zone' ? (price > (candle.high + candle.low) / 2 ? 'high' : 'low') : ptState.drawMode,
    price: Math.round(snapPrice * 1000) / 1000,
    timestamp: new Date(candle.time * 1000).toISOString(),
    label: ptState.drawMode === 'zone' ? 'zone' : `${ptState.drawMode} ${ptState.points.length + 1}`,
    is_zone: ptState.drawMode === 'zone',
    zone_high: ptState.drawMode === 'zone' ? Math.round(candle.high * 1000) / 1000 : undefined,
  };

  ptState.points.push(point);
  ptRenderChart();
  ptRenderPoints();
}

function ptRenderPoints() {
  const container = $('ptPointsList');
  if (!container) return;
  if (ptState.points.length === 0) {
    container.innerHTML = '<span class="micro muted">لم تحدد أي نقطة بعد.</span>';
    return;
  }
  container.innerHTML = ptState.points.map((pt, i) => {
    const icon = pt.is_zone ? '🟣' : (pt.type === 'high' ? '🟡' : '🔵');
    const label = pt.is_zone ? 'Zone' : (pt.type === 'high' ? `High ${i+1}` : `Low ${i+1}`);
    return `<div class="pill">${icon} ${label}: ${pt.price.toFixed(2)} <small>(${pt.timestamp.slice(0,16)})</small></div>`;
  }).join('');
}

function ptUndo() {
  ptState.points.pop();
  ptRenderChart();
  ptRenderPoints();
}

function ptClearPoints() {
  ptState.points = [];
  ptRenderChart();
  ptRenderPoints();
}

async function ptTeachPattern() {
  const name = $('ptName').value.trim();
  const direction = $('ptDirection').value;
  const description = $('ptDescription').value.trim();
  const htf = $('ptHTF').value;
  const ltf = $('ptLTF').value;

  if (!name) { toast('خطأ', 'لازم تعطي اسم للنمط', 'error'); return; }
  if (ptState.points.length < 2) { toast('خطأ', 'حدد على الأقل نقطتين (قمة وقاع)', 'error'); return; }
  if (!description) { toast('خطأ', 'اكتب شرح بسيط للنمط', 'error'); return; }

  showStatus(true);
  try {
    const res = await apiFetch('/api/patterns/teach', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name,
        direction,
        description,
        htf,
        ltf,
        annotations: ptState.points,
      })
    });
    toast('تم التعليم!', `النمط "${res.name}" محفوظ (${res.swing_count} نقاط)`, 'success');
    ptState.points = [];
    ptRenderChart();
    ptRenderPoints();
    await ptLoadSavedPatterns();
  } catch (e) {
    toast('خطأ', e.message, 'error');
  }
  showStatus(false);
}

async function ptAddExample() {
  const patternId = $('ptSearchPattern').value;
  if (!patternId) { toast('خطأ', 'اختر نمط من القائمة أولاً', 'error'); return; }
  if (ptState.points.length < 2) { toast('خطأ', 'حدد نقاط المثال على الشارت', 'error'); return; }

  showStatus(true);
  try {
    const res = await apiFetch(`/api/patterns/${patternId}/examples`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({annotations: ptState.points, notes: $('ptDescription').value})
    });
    toast('تم', 'المثال الإضافي محفوظ', 'success');
    ptState.points = [];
    ptRenderChart();
    ptRenderPoints();
  } catch (e) {
    toast('خطأ', e.message, 'error');
  }
  showStatus(false);
}

async function ptSearchPattern() {
  const patternId = $('ptSearchPattern').value;
  if (!patternId) { toast('خطأ', 'اختر نمط من القائمة', 'error'); return; }
  const maxResults = parseInt($('ptSearchMax').value) || 50;

  showStatus(true);
  try {
    const res = await apiFetch('/api/patterns/search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pattern_id: patternId, max_matches: maxResults, include_ltf: false})
    });
    ptDisplayResults(res);
  } catch (e) {
    toast('خطأ', e.message, 'error');
  }
  showStatus(false);
}

async function ptSearchWithLTF() {
  const patternId = $('ptSearchPattern').value;
  if (!patternId) { toast('خطأ', 'اختر نمط من القائمة', 'error'); return; }
  const maxResults = parseInt($('ptSearchMax').value) || 50;

  showStatus(true);
  try {
    const res = await apiFetch('/api/patterns/search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pattern_id: patternId, max_matches: maxResults, include_ltf: true})
    });
    ptDisplayResults(res);
  } catch (e) {
    toast('خطأ', e.message, 'error');
  }
  showStatus(false);
}

function ptDisplayResults(res) {
  const pre = $('ptResults');
  const cards = $('ptMatchCards');

  if (!res.matches || res.matches.length === 0) {
    pre.textContent = res.summary || 'لم يتم العثور على تطابقات.';
    cards.innerHTML = '';
    return;
  }

  pre.textContent = `${res.summary}\n\nإجمالي: ${res.total_matches} تطابق | طازج: ${res.fresh} | مُختبر: ${res.tested} | مكسور: ${res.broken}` +
    (res.stats?.win_rate ? `\nWin rate: ${res.stats.win_rate}% | Avg RR: ${res.stats.avg_rr}` : '');

  cards.innerHTML = res.matches.slice(0, 20).map(m => `
    <div class="trade-card">
      <div class="trade-card-head">
        <span class="badge">${m.direction}</span>
        <span class="badge">${m.freshness}</span>
        <span class="micro">${m.similarity_score}% match</span>
      </div>
      <div class="trade-card-body">
        <div><strong>Zone:</strong> ${m.zone_low} → ${m.zone_high}</div>
        <div><strong>Time:</strong> ${(m.timestamp || '').slice(0, 16)}</div>
        <div><strong>Session:</strong> ${m.session_label}</div>
        ${m.reaction ? `<div><strong>LTF:</strong> ${m.reaction.outcome} (${m.reaction.ltf_pattern}) RR=${m.reaction.rr_ratio}</div>` : ''}
      </div>
    </div>
  `).join('');

  toast('نتائج', `${res.total_matches} تطابق للنمط`, 'success');
}

async function ptLoadSavedPatterns() {
  try {
    const res = await apiFetch('/api/patterns/list');
    ptState.savedPatterns = res.patterns || [];
    // Update select dropdown
    const select = $('ptSearchPattern');
    select.innerHTML = '<option value="">-- اختر نمط --</option>' +
      ptState.savedPatterns.map(p => `<option value="${p.pattern_id}">${p.name} (${p.direction}, ${p.examples_count} أمثلة)</option>`).join('');

    // Update saved list display
    const list = $('ptSavedList');
    if (ptState.savedPatterns.length === 0) {
      list.innerHTML = '<div class="micro muted">لسا ما علّمت البوت أي نمط.</div>';
    } else {
      list.innerHTML = ptState.savedPatterns.map(p => `
        <div class="vault-card" onclick="$('ptSearchPattern').value='${p.pattern_id}'">
          <div><strong>${p.name}</strong></div>
          <div class="micro muted">${p.direction} | ${p.swing_count} نقاط | ${p.examples_count} أمثلة</div>
          <div class="micro">${p.description}</div>
        </div>
      `).join('');
    }
  } catch (_) {}
}

// Boot pattern teacher on workspace switch
const origSwitchWorkspace = window.switchWorkspace;
if (typeof origSwitchWorkspace === 'function') {
  window.switchWorkspace = function(ws) {
    origSwitchWorkspace(ws);
    if (ws === 'patternTeacherWorkspace') ptLoadSavedPatterns();
  };
} else {
  // Fallback: load on tab click
  document.querySelectorAll('.workspace-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      if (tab.dataset.workspace === 'patternTeacherWorkspace') ptLoadSavedPatterns();
    });
  });
}
