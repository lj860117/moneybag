// ---- 落地页（家庭 CFO 今日面板）----
function renderLanding(){currentPage='landing';const p=loadPortfolio();const txns=loadTxns();const assets=loadAssets();const ledger=loadLedger();
// 已登录用户直接进首页，不走问卷
const hasProfile=!!getProfileId()&&getProfileId()!=='default';
const hasServerHoldings=localStorage.getItem(_uk('moneybag_has_holdings'))==='1';
const hasLocalData=txns.length>0||p.transactions?.length>0||assets.length>0||ledger.length>0||hasServerHoldings;
if(!hasProfile&&!hasLocalData){
$('#app').innerHTML=`<div class="landing stagger"><div class="landing-icon">💰</div><h1>你的钱，该怎么放？</h1><p class="subtitle">回答5个问题，AI帮你出一份<br>专属资产配置方案</p><button class="cta-btn" onclick="startQuiz()">开始测评</button><div class="trust-badges"><span class="trust-badge">不收费</span><span class="trust-badge">不推销</span><span class="trust-badge">不注册</span></div></div>`;renderNav();return}

// ── 家庭 CFO 今日面板 ──
const nw=calcNetWorth();
$('#app').innerHTML=`<div class="result-page fade-up">

<!-- A. 家庭净资产 -->
<div class="pnl-hero" style="margin-bottom:16px">
<div class="pnl-label">💰 家庭净资产</div>
<div class="pnl-total-value" id="heroNetWorth">${fmtFull(Math.round(nw.netWorth))}</div>
<div id="heroBreakdown" style="display:flex;gap:12px;justify-content:center;margin-top:12px;font-size:12px;flex-wrap:wrap">
<div style="text-align:center"><div style="color:var(--text2)">📈 投资</div><div style="font-weight:700;color:var(--accent)">¥${fmtMoney(Math.round(nw.fundValue))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">💵 现金</div><div style="font-weight:700;color:var(--green)">¥${fmtMoney(Math.round(nw.assetTotal))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">💳 负债</div><div style="font-weight:700;color:var(--red)">-¥${fmtMoney(Math.round(nw.liabilities))}</div></div>
</div>
</div>

<!-- B. 今日提醒 -->
<div id="cfoAlerts" class="dashboard-card" style="border-left:3px solid #6366F1;margin-bottom:12px">
<div class="dashboard-card-title">📋 今日提醒</div>
<div style="font-size:13px;color:var(--text2);padding:8px 0">加载中...</div>
</div>

<!-- C. 资产配置 -->
<div id="cfoAllocation" class="dashboard-card" style="margin-bottom:12px">
<div class="dashboard-card-title">🥧 资产配置</div>
<div style="font-size:13px;color:var(--text2);padding:8px 0">加载中...</div>
</div>

<!-- D. 情绪提醒 -->
<div id="cfoEmotion" class="dashboard-card" style="margin-bottom:12px">
<div class="dashboard-card-title">🧠 情绪提醒</div>
<div style="font-size:13px;color:var(--text2);padding:8px 0">加载中...</div>
</div>

<!-- E. 本周待办 -->
<div id="cfoTodos" class="dashboard-card" style="margin-bottom:12px">
<div class="dashboard-card-title">✅ 本周待办</div>
<div style="font-size:13px;color:var(--text2);padding:8px 0">加载中...</div>
</div>

<!-- 底部操作 -->
<div class="bottom-actions" style="margin-top:16px">
<button class="action-btn primary" onclick="navigateTo('stocks')">📊 查看持仓</button>
<button class="action-btn secondary" onclick="navigateTo('assets')">🏦 管理资产</button>
<button class="action-btn secondary" onclick="navigateTo('chat')">💬 问管家</button>
<div style="display:flex;gap:6px;margin-top:8px">
<div onclick="navigateTo('todos')" style="flex:1;text-align:center;padding:6px 0;font-size:11px;color:var(--text2);background:var(--bg2,rgba(0,0,0,.03));border-radius:8px;cursor:pointer">📌 待办</div>
<div onclick="navigateTo('behavior-history')" style="flex:1;text-align:center;padding:6px 0;font-size:11px;color:var(--text2);background:var(--bg2,rgba(0,0,0,.03));border-radius:8px;cursor:pointer">📊 行为</div>
<div onclick="navigateTo('monthly-rebalance')" style="flex:1;text-align:center;padding:6px 0;font-size:11px;color:var(--text2);background:var(--bg2,rgba(0,0,0,.03));border-radius:8px;cursor:pointer">🔄 再平衡</div>
<div onclick="navigateTo('market-panorama')" style="flex:1;text-align:center;padding:6px 0;font-size:11px;color:var(--text2);background:var(--bg2,rgba(0,0,0,.03));border-radius:8px;cursor:pointer">🌍 市场</div>
</div>
</div>
</div>`;renderNav();loadUnifiedHero();_loadCfoSummary()}

// ---- 首页：加载 CFO 聚合数据 ----
async function _loadCfoSummary(){
if(!API_AVAILABLE)return;
try{
const r=await fetch(`${API_BASE}/cfo-summary?userId=${getProfileId()}`,{signal:AbortSignal.timeout(12000)});
if(!r.ok)return;
const d=await r.json();

// B. 今日提醒
const alertsEl=document.getElementById('cfoAlerts');
const marketLink=`<div onclick="showMarketPanoramaModal()" style="margin-top:8px;padding:6px 10px;background:rgba(99,102,241,.06);border-radius:8px;font-size:12px;color:var(--accent);cursor:pointer;display:flex;align-items:center;gap:4px">🌍 看市场全景 →</div>`;
if(alertsEl&&d.alerts&&d.alerts.length){
const levelIcon={danger:'🔴',warning:'⚠️',opportunity:'🟢',info:'💡'};
alertsEl.innerHTML=`<div class="dashboard-card-title">📋 今日提醒</div>`+
d.alerts.map(a=>`<div style="font-size:13px;line-height:1.8;padding:6px 0;border-bottom:1px solid var(--bg3,rgba(0,0,0,.05))">${levelIcon[a.level]||'📌'} ${a.text}</div>`).join('')+marketLink;
}else if(alertsEl){
// 判断是否空数据用户（净资产=0 且无持仓）
const isEmptyUser=!d.allocation||(!d.allocation.current)||(d.allocation.total_market===0);
if(isEmptyUser){
alertsEl.innerHTML=`<div class="dashboard-card-title">📋 今日提醒</div><div style="font-size:13px;color:var(--accent);padding:8px 0">📝 还没有录入资产数据，<span onclick="navigateTo('portfolio')" style="text-decoration:underline;cursor:pointer">去录入持仓</span> 或 <span onclick="navigateTo('assets')" style="text-decoration:underline;cursor:pointer">添加资产</span> 后，这里会显示个性化提醒。</div>`+marketLink;
}else{
alertsEl.innerHTML=`<div class="dashboard-card-title">📋 今日提醒</div><div style="font-size:13px;color:var(--green);padding:8px 0">✅ 今天一切正常，没有需要特别注意的事项。</div>`+marketLink;
}
}

// C. 资产配置
const allocEl=document.getElementById('cfoAllocation');
if(allocEl&&d.allocation&&d.allocation.current){
const c=d.allocation.current;const t=d.allocation.target||{};
const items=[
{label:'股票',key:'stock',color:'#6366F1'},
{label:'债券',key:'bond',color:'#22C55E'},
{label:'现金',key:'cash',color:'#F59E0B'}
];
let html=`<div class="dashboard-card-title">🥧 资产配置 <span style="font-size:11px;color:var(--text2);font-weight:400">${d.allocation.zone||''}</span></div>`;
items.forEach(item=>{
const cur=Math.round(c[item.key]||0);const tgt=Math.round(t[item.key]||0);
const dev=cur-tgt;const devColor=Math.abs(dev)>10?'var(--red)':Math.abs(dev)>5?'#F59E0B':'var(--green)';
html+=`<div style="display:flex;align-items:center;gap:8px;margin:8px 0">
<div style="width:48px;font-size:12px;color:var(--text2)">${item.label}</div>
<div style="flex:1;height:8px;background:var(--bg3,rgba(0,0,0,.05));border-radius:4px;overflow:hidden"><div style="height:100%;width:${Math.min(cur,100)}%;background:${item.color};border-radius:4px"></div></div>
<div style="width:90px;font-size:11px;text-align:right">${cur}% <span style="color:var(--text2)">目标${tgt}%</span> <span style="color:${devColor};font-weight:600">${dev>0?'+':''}${dev}%</span></div>
</div>`;});
allocEl.innerHTML=html;
}else if(allocEl){
allocEl.innerHTML=`<div class="dashboard-card-title">🥧 资产配置</div><div style="font-size:13px;color:var(--text2);padding:8px 0">暂无配置数据，<span onclick="navigateTo('quiz')" style="color:var(--accent);text-decoration:underline;cursor:pointer">做个风险测评</span> 开始吧</div>`;
}

// D. 情绪提醒
const emotionEl=document.getElementById('cfoEmotion');
if(emotionEl&&d.emotion){
const bgMap={caution:'rgba(239,68,68,.06)',reassure:'rgba(59,130,246,.06)',calm:'rgba(148,163,184,.04)',neutral:'rgba(148,163,184,.04)'};
emotionEl.innerHTML=`<div class="dashboard-card-title">🧠 情绪提醒</div>
<div style="padding:10px;background:${bgMap[d.emotion.tone]||''};border-radius:8px;margin-top:6px">
<div style="font-size:14px;font-weight:700;margin-bottom:4px">${d.emotion.icon||''} ${d.emotion.title||''}</div>
<div style="font-size:13px;color:var(--text2);line-height:1.6">${d.emotion.body||''}</div>
</div>`;
}

// E. 本周待办
const todosEl=document.getElementById('cfoTodos');
if(todosEl&&d.todos&&d.todos.length){
todosEl.innerHTML=`<div class="dashboard-card-title">✅ 本周待办</div>`+
d.todos.map(t=>`<div style="font-size:13px;line-height:2;padding-left:4px">☐ ${t}</div>`).join('');
}else if(todosEl){
todosEl.innerHTML=`<div class="dashboard-card-title">✅ 本周待办</div><div style="font-size:13px;color:var(--green);padding:8px 0">👍 本周暂无待办事项。</div>`;
}

}catch(e){console.warn('[CFO]',e)}}

// ---- 首页：管家简报 ----
async function loadStewardBriefing(){
const card=document.getElementById('stewardBriefingCard');const txt=document.getElementById('stewardBriefingText');
if(!card||!txt||!API_AVAILABLE)return;
try{const r=await fetch(API_BASE+'/steward/briefing?userId='+getProfileId(),{signal:AbortSignal.timeout(15000)});
if(r.ok){const d=await r.json();card.style.display='block';
txt.innerHTML=`<div style="font-size:14px;font-weight:700;margin-bottom:4px">${d.one_line||'暂无'}</div>
<div style="font-size:12px;color:var(--text2)">${d.regime_description?'📊 '+d.regime_description:''} ${d.risk_level&&d.risk_level!=='normal'?({'warning':'⚠️ 有风险提示','danger':'🔴 风控红灯','blocked':'🚫 操作已拦截'}[d.risk_level]||''):''}</div>
${d.top_signal?`<div style="margin-top:6px;padding:6px 10px;background:rgba(99,102,241,.08);border-radius:8px;font-size:12px">🎯 ${d.top_signal}</div>`:''}
<button onclick="showLatestReview()" style="margin-top:8px;padding:6px 12px;border-radius:8px;border:1px solid rgba(99,102,241,.3);background:transparent;color:#818CF8;font-size:11px;cursor:pointer">📋 查看收盘复盘</button>`}}catch(e){console.warn('briefing:',e)}}

// ---- 收盘复盘查看 ----
async function showLatestReview(){
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div><div class="modal-title">📋 收盘复盘</div><div id="reviewContent" style="padding:12px 0"><div style="text-align:center;color:var(--text2)">加载中...</div></div></div>`;
document.body.appendChild(o);
try{const r=await fetch(API_BASE+'/steward/review?userId='+getProfileId(),{signal:AbortSignal.timeout(15000)});
if(r.ok){const d=await r.json();const el=document.getElementById('reviewContent');if(!el)return;
const concl=d.conclusion||d.summary||'暂无复盘数据';
let html=`<div style="font-size:14px;font-weight:700;margin-bottom:12px">${concl}</div>`;
if(d.regime_description)html+=`<div style="font-size:12px;color:var(--text2);margin-bottom:8px">📊 ${d.regime_description||d.regime}</div>`;
if(d.modules_called?.length)html+=`<div style="font-size:12px;color:var(--text2);margin-bottom:8px">📦 综合分析了 ${d.modules_called.length} 个维度的数据</div>`;
if(d.direction){const gateMap={'llm_arbitration':'AI综合研判','rule_based':'规则引擎','manual':'人工判断'};html+=`<div style="font-size:13px;margin-bottom:8px;padding:8px;background:var(--bg2);border-radius:8px">方向: <b>${translateDirection(d.direction)||d.direction}</b> | 置信度: <b>${d.confidence||50}%</b> | 决策依据: ${gateMap[d.gate_decision]||d.gate_decision||'综合判断'}</div>`}
const diagFile=d.diagnosis||'';
if(diagFile)html+=`<div style="margin-bottom:8px;padding:10px;background:rgba(99,102,241,.06);border-radius:10px;font-size:13px;line-height:1.8;border-left:3px solid #6366F1"><div style="font-weight:700;margin-bottom:4px">🤖 R1 深度诊断</div>${diagFile}</div>`;
if(d.risk_level&&d.risk_level!=='normal')html+=`<div style="font-size:12px;color:var(--red)">${{'warning':'⚠️ 有风险提示','danger':'🔴 风控红灯','blocked':'🚫 操作已拦截'}[d.risk_level]||'⚠️ '+d.risk_level}</div>`;
html+=`<div style="font-size:11px;color:var(--text3);margin-top:12px;text-align:center">${d.elapsed?d.elapsed+'s · ':''}${d.timestamp?new Date(d.timestamp).toLocaleString('zh-CN'):''}</div>`;
el.innerHTML=html}}catch(e){const el=document.getElementById('reviewContent');if(el)el.innerHTML=`<div style="color:var(--text2)">加载失败: ${e.message}</div>`}}

// ---- 首页：统一净资产 Hero 更新 ----
async function loadUnifiedHero(){
const d=await fetchUnifiedNetworth();if(!d||!d.netWorth)return;
const el=document.getElementById('heroNetWorth');if(el)el.textContent=fmtFull(Math.round(d.netWorth));
const bd=document.getElementById('heroBreakdown');
if(bd){const b=d.breakdown||{};
bd.innerHTML=`
<div style="text-align:center"><div style="color:var(--text2)">📈 投资</div><div style="font-weight:700;color:var(--accent)">¥${fmtMoney(Math.round((b.investment||{}).total||0))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">💵 现金</div><div style="font-weight:700;color:var(--green)">¥${fmtMoney(Math.round((b.cash||{}).total||0))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">🏠 房产</div><div style="font-weight:700;color:#F59E0B">¥${fmtMoney(Math.round((b.property||{}).total||0))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">💳 负债</div><div style="font-weight:700;color:var(--red)">-¥${fmtMoney(Math.round((b.liability||{}).total||0))}</div></div>`}
const hel=document.getElementById('heroHealth');
if(hel&&d.healthGrade)hel.innerHTML=`${d.healthGrade} · ${d.healthScore}分${d.healthIssues?.length?` · <span style="color:var(--red)">${d.healthIssues[0]}</span>`:''}`}

// ---- 资产变更后异步刷新净资产（供添加/编辑/删除资产后调用）----
async function _refreshNetWorthAfterAssetChange(){
// 等待 500ms 让后端处理完保存（API 是 fire-and-forget）
await new Promise(r=>setTimeout(r,500));
// 刷新后端净资产（后端缓存已在 API 层失效）
const d=await fetchUnifiedNetworth();
// 更新首页 hero（如果 DOM 存在）
if(d&&d.netWorth){
const el=document.getElementById('heroNetWorth');if(el)el.textContent=fmtFull(Math.round(d.netWorth));
}
// 更新资产页的净资产显示
const assetNW=document.getElementById('assetPageNW');
if(assetNW&&d&&d.netWorth)assetNW.textContent=fmtFull(Math.round(d.netWorth));
}

// ---- 首页：今日关注（DeepSeek 个性化）----
async function loadDailyFocus(){
const el=document.getElementById('dailyFocusSection');if(!el||!API_AVAILABLE)return;
try{const r=await fetch(`${API_BASE}/daily-focus`,{signal:AbortSignal.timeout(15000)});
if(!r.ok)return;const d=await r.json();const tips=d.tips||[];
if(tips.length)el.innerHTML=`<div style="background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.15);border-radius:12px;padding:12px 14px;margin-bottom:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">🎯 今日关注 <span style="font-size:10px;color:var(--text2);font-weight:400">${d.source==='ai'?'AI':'默认'}</span></div>${tips.map(t=>`<div style="font-size:12px;line-height:1.8">${t}</div>`).join('')}</div>`
}catch(e){console.warn('dailyFocus:',e)}}

// ---- 首页：风控预警摘要 ----
async function loadHomeRiskAlert(){
const el=document.getElementById('riskAlertSection');if(!el||!API_AVAILABLE)return;
try{
const vp=await fetch(API_BASE+'/dashboard',{signal:AbortSignal.timeout(15000)}).then(r=>r.ok?r.json():null);
if(!vp)return;
const r=await fetch(API_BASE+'/risk-actions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({valuation_percentile:vp.valuation?.percentile||50,fear_greed:vp.fear_greed?.score||50}),signal:AbortSignal.timeout(10000)});
if(!r.ok)return;const data=await r.json();
const actions=(data.actions||[]).filter(a=>a.level==='danger'||a.level==='warning');
if(!actions.length){el.innerHTML='<div style="background:rgba(16,185,129,.06);border:1px solid rgba(16,185,129,.15);border-radius:12px;padding:10px 14px;margin-bottom:12px;font-size:12px;color:var(--green)">✅ 风控状态良好，暂无预警</div>';return}
el.innerHTML=`<div style="margin-bottom:12px">${actions.map(a=>{
const isD=a.level==='danger';
return`<div style="background:${isD?'rgba(239,68,68,.08)':'rgba(245,158,11,.08)'};border:1px solid ${isD?'rgba(239,68,68,.2)':'rgba(245,158,11,.2)'};border-radius:12px;padding:10px 14px;margin-bottom:6px;font-size:13px;color:${isD?'var(--red)':'#F59E0B'}">${isD?'🔴':'⚠️'} ${a.action}</div>`}).join('')}</div>`;
}catch(e){console.warn('Risk alert:',e)}}

// ---- 首页：资产配置建议 ----
async function loadHomeAllocationAdvice(){
const el=document.getElementById('allocationAdviceSection');if(!el||!API_AVAILABLE)return;
try{
const r=await fetch(API_BASE+'/allocation-advice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:getUserId()}),signal:AbortSignal.timeout(10000)});
if(!r.ok)return;const data=await r.json();
if(!data.target)return;
const t=data.target||{};const c=data.current||{};const dev=data.deviation||{};
const advArr=Array.isArray(data.advice)?data.advice:[];
const summaryText=data.summary||'';
el.innerHTML=`<div style="background:var(--bg2);border-radius:var(--radius);padding:16px;margin-bottom:12px">
<div style="font-size:14px;font-weight:700;margin-bottom:10px">🎯 资产配置建议 <span style="font-size:11px;color:var(--text2);font-weight:400">${data.valuation_zone||''}</span></div>
${['stock','bond','cash'].map(k=>{
const label=k==='stock'?'股票类':k==='bond'?'债券类':'现金类';
const cur=Math.round(c[k]||0);const tgt=Math.round(t[k]||0);const d=Math.round(dev[k]||0);
const dColor=Math.abs(d)>15?'var(--red)':Math.abs(d)>5?'#F59E0B':'var(--green)';
return`<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
<div style="width:56px;font-size:12px;color:var(--text2)">${label}</div>
<div style="flex:1;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden"><div style="height:100%;width:${Math.min(cur,100)}%;background:var(--accent);border-radius:3px"></div></div>
<div style="width:80px;font-size:11px;text-align:right">${cur}% <span style="color:var(--text2)">→</span> ${tgt}% <span style="color:${dColor};font-weight:600">${d>0?'+':''}${d}%</span></div>
</div>`}).join('')}
${summaryText?`<div style="font-size:12px;color:var(--text2);margin-top:6px;padding-top:8px;border-top:1px solid var(--bg3)">💡 ${summaryText}</div>`:''}
${advArr.length?advArr.map(a=>{const bg=a.direction==='reduce'?'rgba(239,68,68,.08)':'rgba(34,197,94,.08)';return`<div style="background:${bg};border-radius:8px;padding:8px 10px;margin-top:6px;font-size:12px">${a.message}</div>`}).join(''):''}
</div>`;
}catch(e){console.warn('Allocation advice:',e)}}


// --- 01-empty-landing.js ---
/* =========================================================================
 * V6 欠账 1/6：空仓首页市场概览
 * 目标：持仓为空时，不再是一片空白；展示"市场温度+入场时机+今日焦点"
 * 锚点：#dailyFocusSection（renderLanding 渲染出的每日焦点区域）
 * 依赖 API：/api/timing, /api/daily-signal, /api/news/impact
 * ========================================================================= */
;(function(){
  'use strict';

  async function _v6RenderEmptyLanding(){
    try {
      await _v6RenderEmptyLandingImpl();
    } catch (e) {
      console.error('[V6-1] render failed, clearing skeleton:', e);
      const host = document.getElementById('v6EmptyHome');
      if (host) {
        host.innerHTML = `<div class="dashboard-card"><div style="text-align:center;padding:20px;color:var(--text2);font-size:13px">市场数据渲染异常，请刷新重试<br><span style="font-size:11px;opacity:.6">${(e && e.message) || e}</span></div></div>`;
      }
    }
  }
  async function _v6RenderEmptyLandingImpl(){
    // 只在 landing 页且空仓时生效
    if (typeof currentPage !== 'undefined' && currentPage !== 'landing') return;
    if (!window._v6IsEmptyHoldings || !_v6IsEmptyHoldings()) return;

    // 找插入锚：优先 #dailyFocusSection，否则 #signalsSection
    const anchor = document.getElementById('dailyFocusSection')
                || document.getElementById('signalsSection');
    if (!anchor) return;

    // 已注入过？避免重复
    if (document.getElementById('v6EmptyHome')) return;

    const host = document.createElement('div');
    host.id = 'v6EmptyHome';
    host.style.cssText = 'margin-top:12px';
    host.innerHTML = _v6Skeleton('正在为你加载市场概览...');
    anchor.parentNode.insertBefore(host, anchor);

    // 并行拉三份数据
    // V7: 各自独立请求，不互相阻塞，5秒超时
    const timing = await _v6Fetch('/timing', {timeout: 5000}).catch(()=>null);
    const signal = await _v6Fetch('/daily-signal', {timeout: 5000}).catch(()=>null);
    const impact = await _v6Fetch('/news/impact', {timeout: 5000}).catch(()=>null);

    let html = '';

    // === 欢迎卡 ===
    html += `<div class="pnl-hero" style="background:linear-gradient(135deg,rgba(59,130,246,.08),rgba(16,185,129,.08));border:1px solid rgba(59,130,246,.15)">
      <div style="font-size:18px;font-weight:800;margin-bottom:6px">👋 欢迎来到钱袋子</div>
      <div style="font-size:13px;color:var(--text2);line-height:1.6">
        还没添加持仓？没关系 —— 先看看<span style="color:var(--accent);font-weight:600">今日市场</span>的温度，
        等到合适的时机再出手。
      </div>
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
        <button class="action-btn primary" style="flex:1;min-width:120px" onclick="if(typeof showAddStockModal==='function')showAddStockModal()">➕ 添加股票</button>
        <button class="action-btn secondary" style="flex:1;min-width:120px" onclick="if(typeof _nav==='function')_nav('stocks')">💰 管理持仓</button>
      </div>
    </div>`;

    // === 入场时机卡 ===
    if (timing && timing.signal) {
      const colorMap = {
        'STRONG_BUY':'var(--green)', 'BUY':'var(--green)',
        'HOLD':'#F59E0B', 'WAIT':'#F59E0B',
        'SELL':'var(--red)', 'STRONG_SELL':'var(--red)'
      };
      const labelMap = {
        'STRONG_BUY':'🔥 强烈建议入场', 'BUY':'🟢 适合入场',
        'HOLD':'🟡 可以观望', 'WAIT':'🟡 再等等',
        'SELL':'🟠 暂缓入场', 'STRONG_SELL':'🔴 不宜入场'
      };
      const c = colorMap[timing.signal] || '#F59E0B';
      const label = labelMap[timing.signal] || timing.signal;
      html += _v6Card('⏰ 入场时机', `
        <div style="display:flex;align-items:center;gap:12px">
          <div style="font-size:22px;font-weight:900;color:${c}">${label}</div>
          <div style="font-size:11px;color:var(--text2)">置信度 ${Math.round((timing.confidence||0)*100)}%</div>
        </div>
        <div style="font-size:13px;color:var(--text2);margin-top:6px;line-height:1.6">${timing.reason||timing.summary||''}</div>
        ${timing.suggestion ? `<div style="font-size:12px;margin-top:8px;padding:8px;background:var(--bg3);border-radius:8px">💡 ${timing.suggestion}</div>` : ''}
      `, { border: c });
    }

    // === 市场温度（来自 daily-signal）===
    if (signal && signal.overall) {
      const bgMap = {
        STRONG_BUY:'rgba(16,185,129,.10)', BUY:'rgba(16,185,129,.08)',
        HOLD:'rgba(245,158,11,.08)',
        SELL:'rgba(239,68,68,.08)', STRONG_SELL:'rgba(239,68,68,.10)'
      };
      const labelMap = {
        STRONG_BUY:'市场强势 🔥', BUY:'市场偏多 🟢',
        HOLD:'市场震荡 🟡', SELL:'市场偏空 🟠', STRONG_SELL:'市场疲弱 🔴'
      };
      html += `<div class="dashboard-card" style="background:${bgMap[signal.overall]||''};margin-top:8px">
        <div class="dashboard-card-title">🌡️ 市场温度 <span style="font-size:11px;color:var(--accent);font-weight:400">V${signal.version||'5.0'} · ${(signal.details||[]).length}维</span></div>
        <div style="font-size:16px;font-weight:800;margin-top:4px">${labelMap[signal.overall]||signal.overall}</div>
        <div style="font-size:12px;color:var(--text2);margin-top:4px">综合得分 ${signal.score||0} · 置信度 ${Math.round(signal.confidence||0)}%</div>
        <div style="font-size:13px;margin-top:8px;line-height:1.6">${signal.summary||''}</div>
      </div>`;
    }

    // === 今日要闻（news/impact 前 3 条）===
    if (impact && Array.isArray(impact.items) && impact.items.length) {
      const rows = impact.items.slice(0, 3).map(n => {
        const lvl = n.impact_level || n.level || 'neutral';
        const c = lvl === 'bullish' || lvl === 'positive' ? 'var(--green)'
                : lvl === 'bearish' || lvl === 'negative' ? 'var(--red)' : 'var(--text2)';
        const tag = lvl === 'bullish' || lvl === 'positive' ? '利好' :
                    lvl === 'bearish' || lvl === 'negative' ? '利空' : '中性';
        return `<div style="padding:8px 0;border-bottom:1px solid var(--bg3);font-size:13px">
          <span style="display:inline-block;font-size:10px;padding:2px 6px;border-radius:4px;background:${c};color:#fff;margin-right:6px">${tag}</span>
          ${n.title || ''}
          ${n.affected_sectors ? `<div style="font-size:11px;color:var(--text2);margin-top:2px">影响：${(Array.isArray(n.affected_sectors)?n.affected_sectors:[n.affected_sectors]).join(' · ')}</div>` : ''}
        </div>`;
      }).join('');
      html += _v6Card('📰 今日要闻（AI 影响分析）', rows, { badge: 'Phase 5' });
    }

    if (!html) {
      host.innerHTML = `<div class="dashboard-card"><div style="text-align:center;padding:20px;color:var(--text2);font-size:13px">市场数据暂不可用，请稍后刷新</div></div>`;
    } else {
      host.innerHTML = html;
    }
  }

  // 劫持 renderLanding：原函数执行完后触发
  function _install(){
    if (typeof renderLanding !== 'function') return false;
    _v6Hijack('renderLanding', async function(){
      // 给原函数留点时间把 DOM 渲染稳
      setTimeout(_v6RenderEmptyLanding, 150);
    });
    return true;
  }

  if (!_install()) {
    // 如果 app.js 还没解析完，等一下
    const t = setInterval(() => { if (_install()) clearInterval(t); }, 200);
    setTimeout(() => clearInterval(t), 5000);
  }

  console.log('[V6-1] empty-landing patch installed');
})();


// --- 06-household-hero.js ---
/* =========================================================================
 * V6 欠账 6/6：家庭成员资产汇总 Hero 明细展示
 * 方式：劫持 loadOverviewHero()，在总览 hero 下方追加家庭汇总卡
 * 依赖 API：/api/household/summary
 * 条件：Pro 模式 + 有多个 profile 时显示
 * ========================================================================= */
;(function(){
  'use strict';

  async function _v6InjectHouseholdHero(){
    if (!isProMode()) return;
    const hero = document.getElementById('overviewHero');
    if (!hero) return;
    if (hero.querySelector('#v6HouseholdHero')) return;

    // 拉家庭汇总
    const d = await _v6Fetch('/household/summary');
    if (!d || !d.members || d.members.length < 2) return; // 只有一个人就不展示

    const host = document.createElement('div');
    host.id = 'v6HouseholdHero';
    host.style.cssText = 'margin-top:12px';

    // 家庭总资产 Hero
    const total = d.total_assets || d.totalAssets || 0;
    const totalPnl = d.total_pnl || d.totalPnl || 0;
    const pnlC = totalPnl >= 0 ? 'var(--green)' : 'var(--red)';

    let html = `<div class="pnl-hero" style="background:linear-gradient(135deg,rgba(168,85,247,.08),rgba(59,130,246,.08));border:1px solid rgba(168,85,247,.12)">
      <div class="pnl-label">👨‍👩‍👧‍👦 家庭总资产</div>
      <div class="pnl-total-value">¥${total.toLocaleString()}</div>
      ${totalPnl ? `<div class="pnl-change ${totalPnl >= 0 ? 'pos' : 'neg'}" style="color:${pnlC}">
        总盈亏 ${totalPnl >= 0 ? '+' : ''}¥${totalPnl.toFixed(0)}
      </div>` : ''}
    </div>`;

    // 成员明细
    html += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:8px">`;
    d.members.forEach(m => {
      const mPnl = m.pnl || m.totalPnl || 0;
      const mC = mPnl >= 0 ? 'var(--green)' : 'var(--red)';
      const assets = m.total_assets || m.totalAssets || m.marketValue || 0;
      const pct = total > 0 ? ((assets / total) * 100).toFixed(0) : 0;

      // 头像颜色
      const avatarColors = ['#3B82F6','#10B981','#F59E0B','#EC4899','#8B5CF6','#EF4444'];
      const ci = (m.name || '').charCodeAt(0) % avatarColors.length;

      html += `<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="if(typeof switchProfile==='function')switchProfile('${m.id || m.userId || ''}')">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
          <div style="width:32px;height:32px;border-radius:50%;background:${avatarColors[ci]};display:flex;align-items:center;justify-content:center;color:#fff;font-size:14px;font-weight:700">${(m.name || '?')[0]}</div>
          <div>
            <div style="font-size:13px;font-weight:700">${m.name || m.userId || '家庭成员'}</div>
            <div style="font-size:11px;color:var(--text2)">占比 ${pct}%</div>
          </div>
        </div>
        <div style="font-size:16px;font-weight:800">¥${assets.toLocaleString()}</div>
        ${mPnl ? `<div style="font-size:12px;color:${mC};margin-top:2px">${mPnl >= 0 ? '+' : ''}¥${mPnl.toFixed(0)}</div>` : ''}
        <div style="margin-top:6px;background:var(--bg3);border-radius:4px;height:4px;overflow:hidden">
          <div style="height:100%;width:${pct}%;background:${avatarColors[ci]};border-radius:4px;transition:width .3s"></div>
        </div>
      </div>`;
    });
    html += '</div>';

    // 资产配置对比
    if (d.allocation_comparison) {
      html += `<div class="dashboard-card" style="margin-top:8px">
        <div class="dashboard-card-title">📊 家庭配置对比</div>
        <div style="font-size:12px;color:var(--text2);line-height:1.6">${
          typeof d.allocation_comparison === 'string' ? d.allocation_comparison
          : JSON.stringify(d.allocation_comparison)
        }</div>
      </div>`;
    }

    // 建议
    if (d.suggestions && d.suggestions.length) {
      html += `<div class="dashboard-card" style="margin-top:8px;border-left:3px solid var(--accent)">
        <div class="dashboard-card-title">💡 家庭资产建议</div>
        ${d.suggestions.map(s => `<div style="padding:6px 0;font-size:12px;border-bottom:1px solid var(--bg3);line-height:1.5">${typeof s === 'string' ? s : (s.text || s.content || '')}</div>`).join('')}
      </div>`;
    }

    host.innerHTML = html;
    hero.appendChild(host);
  }

  function _install(){
    if (typeof loadOverviewHero !== 'function') return false;
    _v6Hijack('loadOverviewHero', async function(){
      setTimeout(_v6InjectHouseholdHero, 200);
    });
    return true;
  }

  if (!_install()) {
    const t = setInterval(() => { if (_install()) clearInterval(t); }, 200);
    setTimeout(() => clearInterval(t), 5000);
  }

  console.log('[V6-6] household-hero patch installed');
})();

