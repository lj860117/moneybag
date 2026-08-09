// ---- 落地页（家庭 CFO 今日面板）----
function renderLanding(){currentPage='landing';const p=loadPortfolio();const txns=loadTxns();const assets=loadAssets();const ledger=loadLedger();
// 已登录用户直接进首页，不走问卷
const hasProfile=!!getProfileId()&&getProfileId()!=='default';
const hasServerHoldings=localStorage.getItem(_uk('moneybag_has_holdings'))==='1';
const hasLocalData=txns.length>0||p.transactions?.length>0||assets.length>0||ledger.length>0||hasServerHoldings;
if(!hasProfile&&!hasLocalData){
$('#app').innerHTML=`<div class="landing stagger"><div class="landing-icon">💰</div><h1>你的钱，该怎么放？</h1><p class="subtitle">回答5个问题，AI帮你出一份<br>专属资产配置方案</p><button class="cta-btn" onclick="startQuiz()">开始测评</button><div class="trust-badges"><span class="trust-badge">不收费</span><span class="trust-badge">不推销</span><span class="trust-badge">不注册</span></div></div>`;renderNav();return}

// ── v9.3.0 家庭 CFO 今日面板 ──
const nw=calcNetWorth();
// v9.5.14: 家庭总资产防抖动
// 问题：本地 nw 只算当前用户(709)，API 异步覆盖成家庭合计(1009)
//      切页签/刷新 API 失败时会回退到本地 709 → 数字跳变
// 修复：用 sessionStorage 缓存上次成功的家庭聚合值，首屏先显示缓存(若有)，
//      API 失败时保留缓存值（而不是回退到 709 只算自己的本地值）
let _cachedFamilyNW=null,_cachedFamilyBreakdown=null;
try{
  const c=sessionStorage.getItem('moneybag_family_nw_cache');
  if(c){const o=JSON.parse(c);if(o&&o.netWorth&&(Date.now()-o.ts)<300000){_cachedFamilyNW=o.netWorth;_cachedFamilyBreakdown=o.breakdown||null}}
}catch(e){}
const initNetWorth = _cachedFamilyNW != null ? _cachedFamilyNW : nw.netWorth;
const initInvest = _cachedFamilyBreakdown?.investment ?? nw.fundValue;
const initCash = _cachedFamilyBreakdown?.cash ?? nw.assetTotal;
const initLiab = _cachedFamilyBreakdown?.liability ?? nw.liabilities;
const hour=new Date().getHours();
const greeting=hour<12?'早上好':hour<18?'下午好':'晚上好';
const now=new Date();
const weekdays=['SUNDAY','MONDAY','TUESDAY','WEDNESDAY','THURSDAY','FRIDAY','SATURDAY'];
const months=['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
const dateStr=weekdays[now.getDay()]+' · '+months[now.getMonth()]+' '+now.getDate();
const isTradeDay=now.getDay()>=1&&now.getDay()<=5;
// v9.5.25: 移除"隐藏金额"按钮，清掉残留 mask 状态
try{
  localStorage.removeItem('moneybag_money_masked');
  document.body.classList.remove('money-masked');
  document.getElementById('app')?.classList.remove('money-masked');
}catch(e){}
const isMasked=false;

$('#app').innerHTML=`<div class="result-page fade-up${isMasked?' money-masked':''}" style="padding-bottom:calc(var(--tabbar-height,76px) + 16px)" id="landingRoot">

<!-- 顶部条 -->
<header class="mb-flex mb-flex--between" style="padding:6px 4px 14px">
  <div class="mb-flex mb-gap-4">
    <div class="mb-avatar mb-avatar--md mb-avatar--leijiang">L</div>
    <div>
      <b style="font-size:13px">${greeting}，${_profileName||'用户'}</b>
      <div class="mb-eyebrow" style="margin-top:2px">${dateStr}${!isTradeDay?' · 非交易日':''}</div>
    </div>
  </div>
  <div class="mb-flex mb-gap-2">
    <button class="mb-btn mb-btn--secondary mb-btn--sm" onclick="cycleTheme();renderLanding()">${getThemeIcon()}</button>
    <button class="mb-pill ${isProMode()?'mb-pill--on':''}" onclick="toggleUIMode()">${isProMode()?'专业':'简洁'}</button>
  </div>
</header>

${!isTradeDay?`<div style="background:linear-gradient(90deg,rgba(255,183,85,.08),rgba(255,183,85,.02));border:1px solid rgba(255,183,85,.12);border-radius:var(--radius-md,10px);padding:7px 12px;font-size:var(--fs-sm,11px);color:var(--color-brand-500,#FFB755);margin-bottom:14px;display:flex;align-items:center;gap:8px"><span class="mb-live-dot" style="background:var(--color-brand-500,#FFB755);box-shadow:0 0 6px var(--color-brand-500,#FFB755)"></span>非交易日 · 数据为最近一次收盘快照</div>`:''}

<!-- Hero 净资产 -->
<section class="mb-hero">
  <div class="mb-flex mb-flex--between">
    <span class="mb-hero__label">💰 家庭净资产</span>
  </div>
  <h1 class="mb-hero__num mb-numeric" id="heroNetWorth"><span class="mb-money__symbol">¥</span><span class="mb-money__num">${Math.round(initNetWorth).toLocaleString('zh-CN')}</span><small>.00</small></h1>
  <div class="mb-hero__delta" id="heroDelta">
    <span class="mb-pill" style="opacity:.5">加载中...</span>
  </div>
  <div class="mb-hero__splits" id="heroBreakdown">
    <div class="mb-hero__split" style="cursor:pointer" onclick="_showInvestBreakdown()"><div class="mb-hero__split-label">📈 投资</div><div class="mb-hero__split-value">¥${fmtMoney(Math.round(initInvest))}</div></div>
    <div class="mb-hero__split"><div class="mb-hero__split-label">💵 现金</div><div class="mb-hero__split-value">¥${fmtMoney(Math.round(initCash))}</div></div>
    <div class="mb-hero__split"><div class="mb-hero__split-label">📋 负债</div><div class="mb-hero__split-value mb-hero__split-value--dn">-¥${fmtMoney(Math.round(initLiab))}</div></div>
  </div>
</section>

<!-- 大盘指数条（异步填充） -->
<section id="cfoIndices" style="display:flex;justify-content:space-between;padding:8px 12px;margin-bottom:14px;background:rgba(255,255,255,.02);border:1px solid var(--border-subtle,rgba(255,255,255,.05));border-radius:var(--radius-md,10px)">
  <span style="font-size:11px;color:var(--text-tertiary,#7A8499)">大盘加载中...</span>
</section>

<!-- 快捷网格 -->
<section style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px">
  <div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);border-radius:14px;padding:12px 4px;text-align:center;cursor:pointer" onclick="navigateTo('portfolio')">
    <div style="width:36px;height:36px;border-radius:10px;margin:0 auto 6px;display:grid;place-items:center;font-size:16px;background:rgba(0,229,160,.18)">📊</div>
    <div style="font-size:11px;font-weight:600;color:#F0F2F7;line-height:1.3">持仓</div>
    <div style="font-size:9px;color:#7A8499;margin-top:2px" id="quickHoldingCount">—</div>
  </div>
  <div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);border-radius:14px;padding:12px 4px;text-align:center;cursor:pointer" onclick="navigateTo('market-panorama')">
    <div style="width:36px;height:36px;border-radius:10px;margin:0 auto 6px;display:grid;place-items:center;font-size:16px;background:rgba(139,111,230,.18)">📈</div>
    <div style="font-size:11px;font-weight:600;color:#F0F2F7;line-height:1.3">大盘</div>
    <div style="font-size:9px;color:#7A8499;margin-top:2px" id="quickMarketStatus">—</div>
  </div>
  <div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);border-radius:14px;padding:12px 4px;text-align:center;cursor:pointer" onclick="navigateTo('insight')">
    <div style="width:36px;height:36px;border-radius:10px;margin:0 auto 6px;display:grid;place-items:center;font-size:16px;background:rgba(255,183,85,.18)">💡</div>
    <div style="font-size:11px;font-weight:600;color:#F0F2F7;line-height:1.3">资讯</div>
    <div style="font-size:9px;color:#7A8499;margin-top:2px">选基选股</div>
  </div>
  <div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);border-radius:14px;padding:12px 4px;text-align:center;cursor:pointer" onclick="navigateTo('assets')">
    <div style="width:36px;height:36px;border-radius:10px;margin:0 auto 6px;display:grid;place-items:center;font-size:16px;background:rgba(255,138,177,.18)">💰</div>
    <div style="font-size:11px;font-weight:600;color:#F0F2F7;line-height:1.3">资产</div>
    <div style="font-size:9px;color:#7A8499;margin-top:2px">管理</div>
  </div>
</section>

<!-- AI 提醒卡 -->
<section class="mb-card--ai-tip" id="cfoAlerts" style="margin-bottom:14px">
  <div class="mb-flex mb-gap-3 mb-mb-3">
    <div class="mb-avatar mb-avatar--xs mb-avatar--ai">✨</div>
    <b style="font-size:12px;color:var(--color-ai-300,#B89DFF)">💡 今日提醒</b>
  </div>
  <p style="font-size:var(--fs-sm,11px);color:var(--text-default,#D8DCE5);line-height:var(--lh-normal,1.6)">加载中...</p>
</section>

<!-- 双账户卡（异步从后端加载家庭数据） -->
<section class="mb-card--ghost" style="margin-bottom:14px" id="landingFamilyCard">
  <div class="mb-flex mb-flex--between mb-mb-3">
    <b style="font-size:12px">👨‍👩 家庭账户</b>
    <span class="mb-text-tertiary" style="font-size:10px;cursor:pointer" onclick="navigateTo('settings')">管理 →</span>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
    <div class="mb-card--ghost" style="padding:10px">
      <div class="mb-flex mb-gap-2 mb-mb-1">
        <div class="mb-avatar mb-avatar--xs mb-avatar--leijiang">L</div>
        <b style="font-size:11px">LeiJiang</b>
      </div>
      <div class="mb-money mb-money--sm">—</div>
      <div class="mb-caption">加载中</div>
    </div>
    <div class="mb-card--ghost" style="padding:10px">
      <div class="mb-flex mb-gap-2 mb-mb-1">
        <div class="mb-avatar mb-avatar--xs mb-avatar--buluogeli">B</div>
        <b style="font-size:11px">BuLuoGeLi</b>
      </div>
      <div class="mb-money mb-money--sm">—</div>
      <div class="mb-caption">加载中</div>
    </div>
  </div>
</section>

<!-- 情绪温度计 -->
<section id="cfoEmotion" style="background:linear-gradient(135deg,rgba(255,183,85,.08),rgba(255,138,177,.05));border:1px solid var(--border-subtle,rgba(255,255,255,.05));border-radius:var(--radius-xl,18px);padding:16px;margin-bottom:14px;display:flex;gap:12px;align-items:center">
  <div style="font-size:32px">🌤</div>
  <div>
    <b style="font-size:13px;display:block;margin-bottom:3px">情绪温度计 · 加载中</b>
    <p style="font-size:11px;color:var(--text-tertiary,#7A8499);line-height:1.5">正在获取市场情绪...</p>
  </div>
</section>

<!-- AI 管家入口 -->
<section class="mb-card--ai" style="padding:16px;margin-bottom:14px;position:relative;overflow:hidden;border-radius:var(--radius-xl,18px)">
  <div style="position:absolute;left:-30px;top:-30px;width:100px;height:100px;background:radial-gradient(circle,rgba(0,229,160,.25),transparent 70%);filter:blur(15px)"></div>
  <div class="mb-flex mb-gap-3" style="margin-bottom:10px;position:relative;z-index:1">
    <div class="mb-avatar mb-avatar--sm mb-avatar--ai">🤖</div>
    <b style="font-size:12px">AI 管家</b>
    <span style="margin-left:auto;font-size:9px;color:var(--color-bull,#00E5A0);display:flex;align-items:center;gap:4px"><span class="mb-live-dot"></span>在线</span>
  </div>
  <div style="font-size:12px;line-height:1.7;color:var(--text-default,#D8DCE5);position:relative;z-index:1">投资问题自动<strong style="color:var(--text-primary,#F0F2F7);background:rgba(0,229,160,.12);padding:1px 4px;border-radius:3px">多视角会诊</strong>，日常问题随时问。</div>
  <div onclick="navigateTo('chat')" style="margin-top:12px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06);border-radius:999px;padding:8px 14px;font-size:11px;color:var(--text-tertiary,#7A8499);display:flex;align-items:center;gap:6px;position:relative;z-index:1;cursor:pointer">试试：现在能入场吗 / 持仓怎么调<span style="margin-left:auto;width:24px;height:24px;border-radius:50%;background:linear-gradient(135deg,#00E5A0,#00B8D9);display:grid;place-items:center;font-size:11px;color:#0a0a0a">→</span></div>
</section>

<!-- 本周待办 + 资产配置：API 有数据时才显示，初始隐藏 -->
<div id="cfoTodos" class="mb-card" style="margin-bottom:14px;display:none"></div>
<div id="cfoAllocation" class="mb-card" style="margin-bottom:14px;display:none"></div>

<!-- v9.5.123: 精简版 — 只放"目标进度"和"DNA弱点提醒"(合并为1个卡片) -->
<!-- 家庭全景已有 landingFamilyCard; 月报走企微推送不在首页; 完整DNA放选基页 -->
<div id="sprintCard" class="mb-card" style="margin-bottom:14px;display:none"></div>

<!-- 今日晨报卡片（loadStewardBriefing 写入内容） -->
<div id="stewardBriefingCard" class="mb-card--ai" style="padding:14px;margin-bottom:14px;display:none;border-radius:var(--radius-xl,18px)">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
    <span style="font-size:16px">📋</span>
    <b style="font-size:12px;color:var(--color-ai-300,#B89DFF)">今日晨报</b>
    <span style="margin-left:auto;font-size:9px;color:var(--text-tertiary,#7A8499)">每日 08:30 更新</span>
  </div>
  <div id="stewardBriefingText" style="font-size:12px;line-height:1.7;color:var(--text-default,#D8DCE5)">加载中...</div>
</div>

<!-- v9.5.43 A2 再平衡检查卡（自动加载） -->
<div id="rebalanceCard" style="margin-bottom:14px;display:none"></div>

<!-- C5 v9.5.46 周复盘入口卡 -->
<div id="weeklyReviewCard" style="margin-bottom:14px"></div>

<!-- 每日要点（loadDailyFocus 写入内容） -->
<div id="dailyFocusSection" style="margin-bottom:14px;display:none"></div>

</div>`;renderNav();_initMoneyMask();loadUnifiedHero();_loadCfoSummary();_loadLandingFamilyData();
// 异步加载晨报卡片 + 每日要点 + 风险提示 + 配置建议（延迟100ms确保DOM就绪）
setTimeout(()=>{loadStewardBriefing();loadDailyFocus();loadHomeRiskAlert();loadHomeAllocationAdvice();if(typeof renderRebalanceCard==='function')renderRebalanceCard();_renderWeeklyReviewCard();_loadSprintCards();},100);}

// ---- 首页：金额隐藏/显示 toggle ----
// v9.5.25: 已移除按钮（家庭只有夫妻两人，不需要隐藏功能）
// 保留空函数兼容性，避免缓存的旧 HTML 调用报错
window._toggleMoneyMask = function(){};
function _initMoneyMask(){}

// C5 v9.5.46: 周复盘入口卡（首页 → 点击跳 insight?tab=weekly）
function _renderWeeklyReviewCard(){
  const el=document.getElementById('weeklyReviewCard');
  if(!el)return;
  // 获取今周日期范围显示
  const now=new Date();
  const day=now.getDay();
  const mon=new Date(now);mon.setDate(now.getDate()-(day===0?6:day-1));
  const sun=new Date(mon);sun.setDate(mon.getDate()+6);
  const fmt=d=>`${d.getMonth()+1}/${d.getDate()}`;
  const weekLabel=`${fmt(mon)}–${fmt(sun)}`;
  // 简单统计本周操作数（从 txns 里算）
  let weekOps=0;
  try{
    const txns=typeof loadTxns==='function'?loadTxns():[];
    const monTs=mon.setHours(0,0,0,0);
    weekOps=txns.filter(t=>{const d=new Date(t.date||t.time||0);return d>=monTs}).length;
  }catch{}
  el.innerHTML=`<div onclick="navigateTo('insight','weekly')" style="display:flex;align-items:center;gap:12px;padding:12px 14px;background:rgba(16,185,129,.06);border:1px solid rgba(16,185,129,.18);border-radius:var(--radius-xl,18px);cursor:pointer;transition:transform .15s" onmouseenter="this.style.transform='scale(1.01)'" onmouseleave="this.style.transform=''">
    <div style="width:38px;height:38px;border-radius:12px;background:rgba(16,185,129,.15);display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0">📆</div>
    <div style="flex:1;min-width:0">
      <div style="font-size:13px;font-weight:600;color:var(--text-primary,#F0F2F7)">本周复盘</div>
      <div style="font-size:11px;color:var(--text-tertiary,#7A8499);margin-top:1px">${weekLabel}${weekOps>0?' · 本周'+weekOps+'笔操作':''}</div>
    </div>
    <span style="font-size:18px;color:rgba(52,211,153,.6)">›</span>
  </div>`;
}

// ---- 首页：加载 CFO 聚合数据 ----
async function _loadCfoSummary(){
if(!API_AVAILABLE)return;
try{
const r=await fetch(`${API_BASE}/cfo-summary?userId=${getProfileId()}`,{signal:AbortSignal.timeout(12000)});
if(!r.ok)return;
const d=await r.json();

// A. 累计盈亏（更新 heroDelta）
const deltaEl=document.getElementById('heroDelta');
if(deltaEl&&d.net_worth){
  const pnl=d.net_worth.total_pnl||0;
  const pnlPct=d.net_worth.total_pnl_pct||0;
  // v9.5.123: 数据时效标注
  const ts=d.timestamp||'';
  const tsLabel=ts?`<span style="font-size:9px;color:var(--text-tertiary,#7A8499);margin-left:8px">数据截至 ${ts.slice(5,16).replace('T',' ')}</span>`:'';
  if(pnl!==0){
    const isUp=pnl>0;
    deltaEl.innerHTML=`<span class="mb-pill ${isUp?'mb-pill--bull':'mb-pill--bear'}">${isUp?'▲':'▼'} ${isUp?'+':''}¥${Math.abs(pnl).toFixed(0)} (${isUp?'+':''}${pnlPct.toFixed(1)}%)</span><span class="mb-text-tertiary">累计盈亏</span>${tsLabel}`;
  }else{
    deltaEl.innerHTML=`<span class="mb-pill" style="opacity:.6">持平</span><span class="mb-text-tertiary">累计盈亏</span>${tsLabel}`;
  }
}

// B. 今日提醒
const alertsEl=document.getElementById('cfoAlerts');
if(alertsEl&&d.alerts&&d.alerts.length){
const levelIcon={danger:'🔴',warning:'⚠️',opportunity:'🟢',info:'💡'};
alertsEl.innerHTML=`<div class="mb-flex mb-gap-3 mb-mb-3"><div class="mb-avatar mb-avatar--xs mb-avatar--ai">✨</div><b style="font-size:12px;color:var(--color-ai-300,#B89DFF)">💡 今日提醒</b></div>`+
`<div style="font-size:var(--fs-sm,11px);color:var(--text-default,#D8DCE5);line-height:var(--lh-normal,1.6)">`+
d.alerts.map(a=>`<div style="padding:4px 0">${levelIcon[a.level]||'📌'} ${a.text}</div>`).join('')+`</div>`;
}else if(alertsEl){
const hasNetWorth=d.net_worth&&d.net_worth.total>0;
const hasAlloc=d.allocation&&d.allocation.current&&d.allocation.total_market>0;
const isEmptyUser=!hasNetWorth&&!hasAlloc;
if(isEmptyUser){
alertsEl.innerHTML=`<div class="mb-flex mb-gap-3 mb-mb-3"><div class="mb-avatar mb-avatar--xs mb-avatar--ai">✨</div><b style="font-size:12px;color:var(--color-ai-300,#B89DFF)">💡 今日提醒</b></div><p style="font-size:var(--fs-sm,11px);color:var(--text-default,#D8DCE5);line-height:var(--lh-normal,1.6)">📝 还没有录入资产数据，<span onclick="navigateTo('portfolio')" style="text-decoration:underline;cursor:pointer;color:var(--color-brand-500,#FFB755)">去录入持仓</span> 或 <span onclick="navigateTo('assets')" style="text-decoration:underline;cursor:pointer;color:var(--color-brand-500,#FFB755)">添加资产</span> 后，这里会显示个性化提醒。</p>`;
}else{
alertsEl.innerHTML=`<div class="mb-flex mb-gap-3 mb-mb-3"><div class="mb-avatar mb-avatar--xs mb-avatar--ai">✨</div><b style="font-size:12px;color:var(--color-ai-300,#B89DFF)">💡 今日提醒</b></div><p style="font-size:var(--fs-sm,11px);color:var(--color-bull,#00E5A0);line-height:var(--lh-normal,1.6)">✅ 今天一切正常，没有需要特别注意的事项。</p>`;
}
}

// C. 资产配置
const allocEl=document.getElementById('cfoAllocation');
if(allocEl&&d.allocation&&d.allocation.current){
const c=d.allocation.current;const t=d.allocation.target||{};
const hasFundOnly = !d.allocation.has_direct_stock;
const actualCash = c.actual_cash_pct || 0;
const fundCash = c.fund_cash_est_pct || 0;
const actualStock = c.actual_stock_pct || 0;
const fundEquity = c.fund_equity_pct || 0;

// 基础行：股权/债券/现金
const items=[
  {label: hasFundOnly?'股权类':'股票', sublabel: hasFundOnly?'基金穿透估算':'', key:'stock', color:'#6366F1'},
  {label:'债券类', sublabel: hasFundOnly?'基金穿透估算':'', key:'bond', color:'#22C55E'},
];
// 现金行：有手录现金和基金估算现金时分开显示
const cashItems=[];
if(actualCash>0) cashItems.push({label:'现金', sublabel:'手录资产', val:actualCash, color:'#F59E0B'});
if(fundCash>0) cashItems.push({label:'现金估算', sublabel:'基金内部仓位', val:fundCash, color:'#FCD34D'});
if(cashItems.length===0 && (c.cash||0)>0) cashItems.push({label:'现金', sublabel:'', val:c.cash||0, color:'#F59E0B'});

let html=`<div style="font-size:12px;font-weight:700;margin-bottom:8px">🥧 资产配置 <span style="font-size:11px;color:var(--text-secondary,#9AA1AC);font-weight:400">${d.allocation.zone||''}</span></div>`;
items.forEach(item=>{
const cur=Math.round(c[item.key]||0);const tgt=Math.round(t[item.key]||0);
const dev=cur-tgt;const devColor=Math.abs(dev)>10?'var(--red)':Math.abs(dev)>5?'#F59E0B':'var(--green)';
html+=`<div style="display:flex;align-items:center;gap:8px;margin:8px 0">
<div style="min-width:52px;font-size:12px;color:var(--text-secondary,#9AA1AC)">${item.label}${item.sublabel?`<div style="font-size:9px;opacity:0.6">${item.sublabel}</div>`:''}</div>
<div style="flex:1;height:8px;background:var(--bg3,rgba(0,0,0,.05));border-radius:4px;overflow:hidden"><div style="height:100%;width:${Math.min(cur,100)}%;background:${item.color};border-radius:4px"></div></div>
<div style="width:90px;font-size:11px;text-align:right">${cur}% <span style="color:var(--text-secondary,#9AA1AC)">目标${tgt}%</span> <span style="color:${devColor};font-weight:600">${dev>0?'+':''}${dev}%</span></div>
</div>`;});
// 现金行（分开显示手录和估算）
cashItems.forEach(item=>{
const cur=Math.round(item.val);const tgt=Math.round((t.cash||0)/Math.max(cashItems.length,1));
const cashTarget=Math.round(t.cash||0);
html+=`<div style="display:flex;align-items:center;gap:8px;margin:8px 0">
<div style="min-width:52px;font-size:12px;color:var(--text-secondary,#9AA1AC)">${item.label}${item.sublabel?`<div style="font-size:9px;opacity:0.6">${item.sublabel}</div>`:''}</div>
<div style="flex:1;height:8px;background:var(--bg3,rgba(0,0,0,.05));border-radius:4px;overflow:hidden"><div style="height:100%;width:${Math.min(cur,100)}%;background:${item.color};border-radius:4px"></div></div>
<div style="width:90px;font-size:11px;text-align:right">${cur}%${cashItems.length===1?` <span style="color:var(--text-secondary,#9AA1AC)">目标${cashTarget}%</span>`:''}</div>
</div>`;});
if(hasFundOnly){html+=`<div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:4px;line-height:1.5;opacity:0.7">📊 基金穿透估算，非直接持股/债/现金 · 录入现金/股票后将单独显示</div>`;}
allocEl.innerHTML=html;
allocEl.style.display='';
}else if(allocEl){
allocEl.style.display='none';
}

// D. 情绪提醒
const emotionEl=document.getElementById('cfoEmotion');
if(emotionEl&&d.emotion){
const emojiMap={caution:'🌧',reassure:'☀️',calm:'🌤',neutral:'🌤'};
emotionEl.innerHTML=`
<div style="font-size:32px">${emojiMap[d.emotion.tone]||d.emotion.icon||'🌤'}</div>
<div>
<b style="font-size:13px;display:block;margin-bottom:3px">${d.emotion.title||'情绪温度计'}</b>
<p style="font-size:11px;color:var(--text-tertiary,#7A8499);line-height:1.5">${d.emotion.body||''}</p>
</div>`;
}

// E. 本周待办
const todosEl=document.getElementById('cfoTodos');
if(todosEl&&d.todos&&d.todos.length){
todosEl.style.display='';
todosEl.innerHTML=`<div style="font-size:12px;font-weight:700;margin-bottom:8px;display:flex;align-items:center;gap:6px">📋 本周待办</div>`+
d.todos.map(t=>`<div style="font-size:13px;line-height:1.8;padding:6px 10px;margin-bottom:4px;background:linear-gradient(135deg,rgba(139,111,230,.06),rgba(255,183,85,.04));border:1px solid var(--border-subtle,rgba(255,255,255,.05));border-radius:var(--radius-md,10px)">❤️ ${t}</div>`).join('');
}else if(todosEl){
todosEl.style.display='none';
}

// F. 大盘指数条
const idxEl=document.getElementById('cfoIndices');
if(idxEl&&d.indices&&d.indices.length){
idxEl.innerHTML=d.indices.map(idx=>{
  const c=idx.pct>=0?'var(--color-bull,#00E5A0)':'var(--color-bear,#FF6B6B)';
  const sign=idx.pct>=0?'+':'';
  return`<span style="font-size:11px;display:flex;align-items:center;gap:4px"><span style="color:var(--text-tertiary,#7A8499)">${idx.name}</span><span style="color:${c};font-weight:600">${sign}${idx.pct.toFixed(2)}%</span></span>`;
}).join('');
}else if(idxEl){
idxEl.innerHTML=`<span style="font-size:11px;color:var(--text-tertiary,#7A8499)">指数数据暂不可用</span>`;
}

// G. 快捷格动态状态
const nwData=d.net_worth||{};
const holdCount=(nwData.fund_count||0)+(nwData.stock_count||0);
const qHold=document.getElementById('quickHoldingCount');
if(qHold)qHold.textContent=holdCount>0?`${holdCount}只持仓`:'未录入';
const qMarket=document.getElementById('quickMarketStatus');
if(qMarket&&d.indices&&d.indices.length){
  const main=d.indices[0];
  const c=main.pct>=0?'#00E5A0':'#FF6B6B';
  qMarket.innerHTML=`<span style="color:${c}">${main.pct>=0?'+':''}${main.pct.toFixed(1)}%</span>`;
}

// v9.5.123: 加载持仓预警
_loadHoldingAlerts();

}catch(e){console.warn('[CFO]',e)}}

// v9.5.123: 持仓异动+行为偏差+关联暴露预警
async function _loadHoldingAlerts(){
  if(!API_AVAILABLE)return;
  try{
    const r=await fetch(`${API_BASE}/fund-holdings/alerts?userId=${getProfileId()}`,{signal:AbortSignal.timeout(10000)});
    if(!r.ok)return;
    const d=await r.json();
    if(!d.total_issues)return;
    
    const el=document.getElementById('cfoAlerts');
    if(!el)return;
    
    // 追加到今日提醒下方
    let html='<div style="margin-top:12px;padding:10px 12px;background:rgba(239,68,68,.04);border:1px solid rgba(239,68,68,.1);border-radius:8px">';
    html+=`<div style="font-size:11px;font-weight:600;color:#F87171;margin-bottom:6px">🛡️ AI 风险监控 · ${d.summary}</div>`;
    
    // 异动预警
    if(d.alerts&&d.alerts.length){
      for(const a of d.alerts.slice(0,3)){
        html+=`<div style="font-size:11px;padding:4px 0;color:var(--text-default,#D8DCE5)">${a.text}<div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:1px">💡 ${a.action}</div></div>`;
      }
    }
    // 行为偏差
    if(d.behavior_warnings&&d.behavior_warnings.length){
      for(const b of d.behavior_warnings.slice(0,2)){
        const bColor=b.level==='danger'?'#F87171':'#F59E0B';
        html+=`<div style="font-size:11px;padding:4px 0;color:${bColor}">${b.text}<div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:1px">💡 ${b.action}</div></div>`;
      }
    }
    // 关联暴露
    if(d.overlap_warnings&&d.overlap_warnings.length){
      for(const o of d.overlap_warnings.slice(0,2)){
        html+=`<div style="font-size:11px;padding:4px 0;color:#F59E0B">${o.text}<div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:1px">💡 ${o.action}</div></div>`;
      }
    }
    html+='</div>';
    el.insertAdjacentHTML('beforeend', html);
  }catch(e){console.warn('[HoldingAlerts]',e)}
}

// v9.5.123: Sprint卡片由独立文件 sprint-cards.js 加载，这里只调用入口
function _loadSprintCards(){if(typeof loadSprintCard==='function')loadSprintCard()}

// ---- 首页：管家简报 ----
async function loadStewardBriefing(){
const card=document.getElementById('stewardBriefingCard');const txt=document.getElementById('stewardBriefingText');
if(!card||!txt||!API_AVAILABLE)return;
try{const r=await fetch(API_BASE+'/steward/briefing?userId='+getProfileId(),{signal:AbortSignal.timeout(15000)});
if(r.ok){const d=await r.json();card.style.display='block';
// v9.5.10: 地缘事件 top3，可点击跳转
const geoEvents = Array.isArray(d.geopolitical_events) ? d.geopolitical_events.filter(e=>e&&e.title) : [];
const hasGeo = geoEvents.length > 0;
const geoHTML = hasGeo ? `<div style="margin-top:8px;padding:8px 10px;background:rgba(239,68,68,.06);border:1px solid rgba(239,68,68,.15);border-radius:8px">
  <div style="font-size:11px;color:#F87171;font-weight:700;margin-bottom:6px;display:flex;align-items:center;justify-content:space-between">
    <span>⚠️ 地缘风险事件 (${d.geopolitical_top_category||'相关'})</span>
    <button onclick="_showGeoImpactMap(${JSON.stringify(geoEvents).replace(/"/g,'&quot;')}, '${d.geopolitical_top_category||''}')" style="font-size:10px;padding:2px 7px;border-radius:4px;border:1px solid rgba(239,68,68,.4);background:transparent;color:#F87171;cursor:pointer">🔍 持仓影响</button>
  </div>
  ${geoEvents.map(ev=>{
    const safeTitle=(ev.title||'').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const tag=ev.category?`<span style="display:inline-block;font-size:9px;padding:1px 5px;border-radius:4px;background:rgba(239,68,68,.15);color:#F87171;margin-right:4px">${ev.category}</span>`:'';
    if(ev.url){
      return `<div style="font-size:11px;line-height:1.6;margin-bottom:3px"><a href="${ev.url}" target="_blank" rel="noopener" style="color:var(--text-default,#D8DCE5);text-decoration:none;border-bottom:1px dashed rgba(148,163,184,.3)">${tag}${safeTitle} <span style="font-size:9px;color:var(--text-tertiary,#7A8499)">›</span></a></div>`;
    } else {
      return `<div style="font-size:11px;line-height:1.6;margin-bottom:3px;color:var(--text-secondary,#9AA1AC)">${tag}${safeTitle}</div>`;
    }
  }).join('')}
</div>` : '';
// C1 v9.5.46: 晨报卡片排版精修 — one_line 大字强调 + regime_desc 格式化 + 分隔线层次
const riskBadgeMap={'warning':'<span style="display:inline-flex;align-items:center;gap:3px;font-size:10px;padding:2px 7px;border-radius:12px;background:rgba(245,158,11,.15);color:#F59E0B;font-weight:600">⚠️ 有风险提示</span>','danger':'<span style="display:inline-flex;align-items:center;gap:3px;font-size:10px;padding:2px 7px;border-radius:12px;background:rgba(239,68,68,.15);color:#F87171;font-weight:600">🔴 风控红灯</span>','blocked':'<span style="display:inline-flex;align-items:center;gap:3px;font-size:10px;padding:2px 7px;border-radius:12px;background:rgba(239,68,68,.2);color:#F87171;font-weight:600">🚫 操作已拦截</span>'};
const riskBadge=(d.risk_level&&d.risk_level!=='normal')?riskBadgeMap[d.risk_level]||'':'';
// one_line 颜色：含"⚠️"→橙，含"📉"→绿（跌），含"📈"→红（涨），默认白
const oneLine=d.one_line||'暂无市场信号';
const oneLineColor=oneLine.includes('⚠️')||oneLine.includes('风险')?'#FBBF24':oneLine.includes('📉')?'var(--color-bear,#00E5A0)':oneLine.includes('📈')?'var(--color-bull,#FF6B6B)':'var(--text-primary,#F0F2F7)';
txt.innerHTML=`
<div style="font-size:15px;font-weight:700;line-height:1.45;color:${oneLineColor};letter-spacing:.01em;margin-bottom:8px">${oneLine}</div>
${riskBadge?`<div style="margin-bottom:8px">${riskBadge}</div>`:''}
${d.regime_description?`<div style="display:flex;align-items:flex-start;gap:6px;padding:7px 10px;background:rgba(255,255,255,.04);border-radius:8px;border-left:3px solid rgba(99,102,241,.5);margin-bottom:8px"><span style="font-size:11px;color:var(--text-tertiary,#7A8499);flex-shrink:0;margin-top:1px">市场</span><span style="font-size:12px;line-height:1.6;color:var(--text-secondary,#9AA1AC)">${d.regime_description}</span></div>`:''}
${d.top_signal?`<div style="display:flex;align-items:flex-start;gap:6px;padding:7px 10px;background:rgba(99,102,241,.08);border-radius:8px;border-left:3px solid rgba(99,102,241,.4);margin-bottom:8px"><span style="font-size:11px;color:var(--text-tertiary,#7A8499);flex-shrink:0;margin-top:1px">信号</span><span style="font-size:12px;line-height:1.6;color:var(--text-default,#D8DCE5)">🎯 ${d.top_signal}</span></div>`:''}
${geoHTML}
<div style="display:flex;gap:8px;margin-top:6px">
  <button onclick="showLatestReview()" style="flex:1;padding:7px 10px;border-radius:8px;border:1px solid rgba(99,102,241,.3);background:rgba(99,102,241,.08);color:#818CF8;font-size:11px;font-weight:600;cursor:pointer">📋 收盘复盘</button>
  <button onclick="navigateTo('insight','weekly')" style="flex:1;padding:7px 10px;border-radius:8px;border:1px solid rgba(16,185,129,.3);background:rgba(16,185,129,.08);color:#34D399;font-size:11px;font-weight:600;cursor:pointer">📆 周复盘</button>
</div>`
// 异步加载深度影响预警（拿缓存，不触发新LLM调用）
loadDeepImpactAlert(card);
}}catch(e){console.warn('briefing:',e)}}

// ---- 首页：深度影响预警行（挂在管家卡片底部） ----
async function loadDeepImpactAlert(parentCard){
if(!API_AVAILABLE)return;
try{
// 拉 deep-impact（30分钟缓存，基本是秒返回）
const r=await fetch(API_BASE+'/news/deep-impact?userId='+getProfileId(),{signal:AbortSignal.timeout(10000)});
if(!r.ok)return;
const d=await r.json();
const impacts=d.impacts||[];
// 过滤高影响
const high=impacts.filter(i=>i.magnitude==='high');
if(!high.length)return;
// 找利空的（更重要）
const bearish=high.filter(i=>i.direction==='bearish');
const count=high.length;
const hasBearish=bearish.length>0;
// 找到或创建预警行容器
let alertEl=document.getElementById('deepImpactAlert');
if(!alertEl){alertEl=document.createElement('div');alertEl.id='deepImpactAlert';parentCard.appendChild(alertEl)}
const borderColor=hasBearish?'rgba(239,68,68,.3)':'rgba(245,158,11,.3)';
const bgColor=hasBearish?'rgba(239,68,68,.06)':'rgba(245,158,11,.06)';
const icon=hasBearish?'📉':'📢';
const textColor=hasBearish?'var(--red)':'#F59E0B';
const firstTitle=(hasBearish?bearish[0]:high[0]).title||'';
alertEl.innerHTML=`<div onclick="navigateToDeepImpact()" style="margin-top:10px;padding:8px 12px;background:${bgColor};border:1px solid ${borderColor};border-radius:10px;cursor:pointer;display:flex;align-items:center;gap:8px">
<span style="font-size:14px">${icon}</span>
<div style="flex:1;min-width:0">
<div style="font-size:11px;font-weight:700;color:${textColor}">今日${count}条高影响新闻命中持仓</div>
<div style="font-size:10px;color:var(--text2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${firstTitle}</div>
</div>
<span style="font-size:12px;color:var(--text2);flex-shrink:0">›</span>
</div>`;
}catch(e){console.warn('deepImpactAlert:',e)}}

// 跳转到资讯→深度影响
function navigateToDeepImpact(){
insightTab='deepimpact';
navigateTo('insight');}

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
// v9.5.14: 此函数只负责更新"我"的视角部分（healthGrade/双账户卡里"我"那条）
//           家庭净资产顶部数字 + breakdown 由 _loadLandingFamilyData 统一负责
async function loadUnifiedHero(){
const d=await fetchUnifiedNetworth();if(!d||!d.netWorth)return;
// 注意：不再覆盖 heroNetWorth/heroBreakdown，避免和 _loadLandingFamilyData 竞争
const hel=document.getElementById('heroHealth');
if(hel&&d.healthGrade)hel.innerHTML=`${d.healthGrade} · ${d.healthScore}分${d.healthIssues?.length?` · <span style="color:var(--color-bear,var(--red))">${d.healthIssues[0]}</span>`:''}`
// 更新双账户卡（如果有家庭成员数据）
if(d.breakdown&&d.breakdown.members){
const members=d.breakdown.members;
const lEl=document.getElementById('heroLeijiangAmt');
const bEl=document.getElementById('heroBuluogeliAmt');
const lPct=document.getElementById('heroLeijiangPct');
const bPct=document.getElementById('heroBuluogeliPct');
const total=d.netWorth||1;
members.forEach(m=>{
  const name=(m.name||'').toLowerCase();
  if(name.includes('leijiang')||name.includes('lei')){
    if(lEl)lEl.textContent='¥'+fmtMoney(Math.round(m.total||m.assets||0));
    if(lPct)lPct.textContent='占比 '+Math.round(((m.total||m.assets||0)/total)*100)+'%';
  }else if(name.includes('buluogeli')||name.includes('bulu')){
    if(bEl)bEl.textContent='¥'+fmtMoney(Math.round(m.total||m.assets||0));
    if(bPct)bPct.textContent='占比 '+Math.round(((m.total||m.assets||0)/total)*100)+'%';
  }
});}
}

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
// v9.5.130: 每次切回首页都刷新（cache-bust 加时间戳每小时变化，防止旧内容滞留）
const focusCacheBust=Math.floor(Date.now()/3600000);
try{const r=await fetch(`${API_BASE}/daily-focus?_t=${focusCacheBust}&userId=${getProfileId()}`,{signal:AbortSignal.timeout(20000)});
if(!r.ok)return;const d=await r.json();
// 过滤掉无效 tip（空字符串/单emoji/长度<=2）
const tips=(d.tips||[]).filter(t=>t&&t.trim().length>4);
if(tips.length){el.style.display='';el.innerHTML=`<div style="background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.15);border-radius:12px;padding:12px 14px;margin-bottom:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">🎯 今日关注 <span style="font-size:10px;color:var(--text2);font-weight:400">${d.source==='ai'?'AI':'默认'}</span></div>${tips.map(t=>`<div style="font-size:12px;line-height:1.8">${t}</div>`).join('')}</div>`}
else{el.style.display='none';}// 如果返回空就隐藏，避免显示旧内容
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
    if (timing && (timing.verdict || timing.signal)) {
      // API 返回 verdict（如"🟠 谨慎入场"）和 signal（emoji 或英文枚举）
      const verdict = timing.verdict || timing.signal || '';
      const timingScore = timing.timingScore || 50;
      const valPct = timing.valuationPct || timing.valuation?.percentile || 0;
      const fgi = timing.fgi || 50;
      // 根据 timingScore 或 verdict 判断颜色
      let c, statusLabel;
      if(timingScore < 30 || verdict.includes('非常适合') || verdict.includes('STRONG_BUY')){c='var(--green)';statusLabel='🟢 非常适合入场';}
      else if(timingScore < 50 || verdict.includes('适合') || verdict.includes('BUY')){c='var(--green)';statusLabel='🟢 适合定投入场';}
      else if(timingScore < 70 || verdict.includes('谨慎') || verdict.includes('HOLD')){c='#F59E0B';statusLabel='🟡 谨慎入场';}
      else{c='var(--red)';statusLabel='🔴 不建议入场';}
      // 展示有意义的依据，不显示低置信度数字
      const detail = timing.detail || timing.reason || timing.summary || '';
      const valDesc = valPct ? `估值${valPct}%分位` : '';
      const fgiDesc = fgi ? `恐惧贪婪${fgi.toFixed?fgi.toFixed(0):fgi}` : '';
      const basis = [valDesc, fgiDesc].filter(Boolean).join('，');
      html += _v6Card('⏰ 入场时机判断', `
        <div style="font-size:18px;font-weight:900;color:${c};margin-bottom:8px">${statusLabel}</div>
        ${basis?`<div style="font-size:11px;color:var(--text2);margin-bottom:6px">依据：${basis}</div>`:''}
        <div style="font-size:12px;color:var(--text2);line-height:1.7">${detail}</div>
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

// 首页加载家庭数据（净资产+双账户卡）
async function _loadLandingFamilyData(){
  if(!API_AVAILABLE)return;
  try{
    const r=await fetch(API_BASE+'/family/portfolio-summary?userId='+encodeURIComponent(getProfileId()),{signal:AbortSignal.timeout(10000)});
    if(!r.ok)return;
    const d=await r.json();
    if(!d.available||!d.members)return;
    // v9.5.16: 缓存到 window，弹窗用
    window._familyDataCache = d;

    // 更新家庭净资产（两人合计）
    const heroEl=document.getElementById('heroNetWorth');
    if(heroEl&&d.familyNetWorth>0){
      heroEl.innerHTML=`<span class="mb-money__symbol">¥</span><span class="mb-money__num">${Math.round(d.familyNetWorth).toLocaleString('zh-CN')}</span><small>.00</small>`;
    }

    // 更新投资/现金/负债 breakdown（两人合计）
    const totalInvest=d.members.reduce((s,m)=>s+(m.investTotal||0),0);
    const totalCash=d.members.reduce((s,m)=>s+(m.cashTotal||0),0);
    const totalLiab=d.members.reduce((s,m)=>s+(m.liabilityTotal||0),0);
    const breakEl=document.getElementById('heroBreakdown');
    if(breakEl&&(totalInvest>0||totalCash>0||totalLiab>0)){
      breakEl.innerHTML=`
        <div class="mb-hero__split" style="cursor:pointer" onclick="_showInvestBreakdown()"><div class="mb-hero__split-label">📈 投资</div><div class="mb-hero__split-value">¥${fmtMoney(Math.round(totalInvest))}</div></div>
        <div class="mb-hero__split"><div class="mb-hero__split-label">💵 现金</div><div class="mb-hero__split-value">¥${fmtMoney(Math.round(totalCash))}</div></div>
        <div class="mb-hero__split"><div class="mb-hero__split-label">📋 负债</div><div class="mb-hero__split-value mb-hero__split-value--dn">-¥${fmtMoney(Math.round(totalLiab))}</div></div>`;
    }

    // v9.5.14: 缓存家庭聚合值到 sessionStorage，让下次切回首页立刻显示正确数字
    try{
      if(d.familyNetWorth>0){
        sessionStorage.setItem('moneybag_family_nw_cache', JSON.stringify({
          netWorth: d.familyNetWorth,
          breakdown: { investment: totalInvest, cash: totalCash, liability: totalLiab },
          ts: Date.now(),
        }));
      }
    }catch(e){}

    // 更新双账户卡
    const card=document.getElementById('landingFamilyCard');
    if(!card)return;
    const total=d.familyNetWorth||d.familyTotal||1;
    // C2 v9.5.46: 对方涨跌 toggle（localStorage 记忆）
    const partnerHideKey='moneybag_partner_pnl_hidden';
    const partnerHidden=localStorage.getItem(partnerHideKey)==='1';
    card.innerHTML=`
      <div class="mb-flex mb-flex--between mb-mb-3">
        <b style="font-size:12px">👨‍👩 家庭账户</b>
        <div style="display:flex;align-items:center;gap:6px">
          <button type="button" id="partnerPnlToggle" onclick="event.stopPropagation();_togglePartnerPnl();return false;" title="${partnerHidden?'点击显示对方涨跌':'点击隐藏对方涨跌'}" style="background:${partnerHidden?'rgba(245,158,11,.15)':'rgba(99,102,241,.12)'};border:1px solid ${partnerHidden?'rgba(245,158,11,.35)':'rgba(99,102,241,.3)'};border-radius:12px;font-size:11px;cursor:pointer;padding:3px 9px;color:${partnerHidden?'#F59E0B':'#A5B4FC'};display:inline-flex;align-items:center;gap:3px;font-weight:600">${partnerHidden?'🙈 已隐藏':'👁️ 显示中'}</button>
          <span class="mb-text-tertiary" style="font-size:10px;cursor:pointer" onclick="navigateTo('settings')">管理 →</span>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        ${d.members.map(m=>{
          const pct=total>0?Math.round(m.netWorth/total*100):0;
          const initial=m.userId.charAt(0).toUpperCase();
          const isMe=m.userId===getProfileId();
          const isEmpty=m.netWorth===0&&(m.fundCount||0)===0&&(m.stockCount||0)===0;
          const pnl=m.pnl||0; const pnlPct=m.pnlPct||0;
          const pnlColor=pnl>=0?'var(--color-bull,#FF6B6B)':'var(--color-bear,#00E5A0)';
          const pnlSign=pnl>=0?'+':'-';
          const pnlPctSign=pnlPct>=0?'+':'';
          // C2: 对方（非我）的盈亏根据 toggle 状态决定显示/隐藏
          const shouldHidePnl = !isMe && partnerHidden;
          const pnlHtml = (!isEmpty && Math.abs(pnl)>0.01)
            ? `<div class="partner-pnl-row" style="font-size:10px;color:${pnlColor};font-weight:600;margin-top:2px;transition:opacity .2s${shouldHidePnl&&!isMe?';filter:blur(4px);opacity:.3':''}" data-money>${pnlSign}¥${fmtMoney(Math.abs(Math.round(pnl)))} (${pnlPctSign}${pnlPct.toFixed(2)}%)</div>`
            : '';
          const holdings = m.holdings || [];
          const clickable = !isEmpty && holdings.length > 0;
          const viewBtn = clickable
            ? `<button type="button" onclick="event.stopPropagation();_showFamilyMemberHoldings('${m.userId}')" style="margin-top:8px;width:100%;padding:7px;border-radius:8px;border:1px solid rgba(99,102,241,.35);background:rgba(99,102,241,.12);color:#818CF8;font-size:11px;font-weight:600;cursor:pointer">📋 查看 ${holdings.length} 只持仓</button>`
            : '';
          const cardClick = clickable ? `onclick="_showFamilyMemberHoldings('${m.userId}')"` : '';
          return`<div class="mb-card--ghost" style="padding:10px;${clickable?'cursor:pointer;transition:transform .15s' : ''}" ${cardClick}>
            <div class="mb-flex mb-gap-2 mb-mb-1">
              <div class="mb-avatar mb-avatar--xs" style="background:linear-gradient(135deg,${isMe?'#F59E0B,#D97706':'#A855F7,#7C3AED'})">${initial}</div>
              <b style="font-size:11px">${m.userId}</b>
            </div>
            <div class="mb-money mb-money--sm">${isEmpty?'<span style="color:var(--text-tertiary,#7A8499);font-size:12px">待录入</span>':'¥'+fmtMoney(Math.round(m.netWorth))}</div>
            ${pnlHtml}
            ${isEmpty?'':'<div class="mb-caption">'+(m.fundCount||0)+'基 '+(m.stockCount||0)+'股 · 占比 '+pct+'%</div>'}
            ${viewBtn}
          </div>`}).join('')}
      </div>`;
    // C2: 全局 toggle 函数（在内部定义，确保 partnerHideKey 闭包可用）
    // v9.5.53: 持仓页也用，所以加 fallback 触发 portfolio 重渲染
    window._togglePartnerPnl = function(){
      const hidden=localStorage.getItem(partnerHideKey)==='1';
      localStorage.setItem(partnerHideKey,hidden?'0':'1');
      // 优先刷新首页（如果数据在 cache）
      if(window._familyDataCache && typeof _loadLandingFamilyData==='function') _loadLandingFamilyData();
      // 持仓页：触发持仓页家庭卡重渲染
      if(typeof _loadFamilyPortfolio==='function' && document.getElementById('familyCard')) _loadFamilyPortfolio();
    };
  }catch(e){console.warn('[Family landing]',e)}}

// v9.5.53: 全局 fallback（如果用户没进首页直接进持仓，按钮也能用）
if(typeof window._togglePartnerPnl!=='function'){
  window._togglePartnerPnl = function(){
    const k='moneybag_partner_pnl_hidden';
    const hidden=localStorage.getItem(k)==='1';
    localStorage.setItem(k,hidden?'0':'1');
    if(typeof _loadFamilyPortfolio==='function') _loadFamilyPortfolio();
    if(typeof _loadLandingFamilyData==='function') _loadLandingFamilyData();
  };
}

// v9.5.16: 家庭成员持仓弹窗（点击家庭账户卡某人触发）
window._showFamilyMemberHoldings = function(userId){
  const d = window._familyDataCache;
  if(!d || !d.members) return;
  const m = d.members.find(x => x.userId === userId);
  if(!m) return;
  const holdings = m.holdings || [];
  const isMe = userId === getProfileId();
  const initial = userId.charAt(0).toUpperCase();
  const pnl = m.pnl||0; const pnlPct = m.pnlPct||0;
  // v9.5.20: bull/bear 颜色 + 修复负数符号丢失（之前 (pnl>=0?'+':'') 负数变空字符串）
  const pnlColor = pnl>=0?'var(--color-bull,#FF6B6B)':'var(--color-bear,#00E5A0)';
  const pnlSign = pnl>=0?'+':'-';
  const pnlPctSignTop = pnlPct>=0?'+':'';

  const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
  // v9.5.20: 如果当前主页开启了金额隐藏，弹窗也继承这个状态（弹窗 appendChild 到 body，CSS 够不到 #landingRoot）
  if(document.getElementById('landingRoot')?.classList.contains('money-masked')){
    o.classList.add('money-masked');
  }
  let body = '';
  if(holdings.length === 0){
    body = '<div style="text-align:center;padding:40px;color:var(--text-tertiary,#7A8499)">暂无持仓数据</div>';
  } else {
    body = holdings.map(h=>{
      const hPnlColor = (h.pnl||0)>=0?'var(--color-bull,#FF6B6B)':'var(--color-bear,#00E5A0)';
      const hPnlSign = (h.pnl||0)>=0?'+':'-';
      const pnlPctSign = (h.pnlPct||0)>=0?'+':'';  // pnlPct 自身保留负号，正数加 +
      const typeBadge = h.type==='stock'
        ? '<span style="display:inline-block;font-size:9px;padding:1px 5px;border-radius:4px;background:rgba(239,68,68,.15);color:#F87171;margin-left:6px">📊 股</span>'
        : '<span style="display:inline-block;font-size:9px;padding:1px 5px;border-radius:4px;background:rgba(59,130,246,.15);color:#60A5FA;margin-left:6px">🏦 基</span>';
      return `<div style="padding:10px 12px;border-bottom:1px solid rgba(148,163,184,.08);display:flex;align-items:center;gap:10px">
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;font-weight:600;display:flex;align-items:center">
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70%">${h.name||h.code}</span>${typeBadge}
          </div>
          <div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:2px">${h.code} · 市值 <span data-money>¥${fmtMoney(Math.round(h.marketValue))}</span></div>
        </div>
        <div style="text-align:right;flex-shrink:0">
          <div style="font-size:13px;font-weight:700;color:${hPnlColor}">${pnlPctSign}${(h.pnlPct||0).toFixed(2)}%</div>
          <div style="font-size:10px;color:${hPnlColor}" data-money>${hPnlSign}¥${fmtMoney(Math.abs(Math.round(h.pnl||0)))}</div>
        </div>
      </div>`;
    }).join('');
  }

  o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:80vh;display:flex;flex-direction:column">
    <div class="modal-handle"></div>
    <div class="modal-title">
      <span class="mb-avatar mb-avatar--xs" style="display:inline-flex;background:linear-gradient(135deg,${isMe?'#F59E0B,#D97706':'#A855F7,#7C3AED'});margin-right:8px;vertical-align:middle">${initial}</span>
      ${userId}${isMe?' (我)':''} 的持仓
    </div>
    <div style="padding:8px 12px 12px;color:var(--text-secondary,#9AA1AC);font-size:12px;line-height:1.6">
      总市值 <b style="color:var(--text-default,#D8DCE5);font-size:14px" data-money>¥${fmtMoney(Math.round(m.netWorth||0))}</b>
      · 累计盈亏 <b style="color:${pnlColor}" data-money>${pnlSign}¥${fmtMoney(Math.abs(Math.round(pnl)))} (${pnlPctSignTop}${pnlPct.toFixed(2)}%)</b>
      · ${m.fundCount||0}基 ${m.stockCount||0}股
    </div>
    <div style="flex:1;overflow-y:auto;border-top:1px solid rgba(148,163,184,.08)">${body}</div>
    <button class="mb-btn mb-btn--secondary mb-btn--block" style="margin-top:8px" onclick="document.querySelector('.modal-overlay')?.remove()">关闭</button>
  </div>`;
  document.body.appendChild(o);
};

// 投资明细弹窗（点击首页"📈 投资"触发）
function _showInvestBreakdown(){
  const nw=calcNetWorth();
  const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
  o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:60vh">
    <div class="modal-handle"></div>
    <div class="modal-title">📈 投资明细</div>
    <div class="modal-subtitle">总投资 ¥${fmtMoney(Math.round(nw.fundValue))}</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0">
      <div style="text-align:center;padding:16px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:12px">
        <div style="font-size:24px;margin-bottom:6px">📊</div>
        <div style="font-size:11px;color:var(--text-tertiary)">股票</div>
        <div style="font-size:18px;font-weight:800;margin-top:4px">¥${fmtMoney(Math.round(nw.stockValue||0))}</div>
      </div>
      <div style="text-align:center;padding:16px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:12px">
        <div style="font-size:24px;margin-bottom:6px">💼</div>
        <div style="font-size:11px;color:var(--text-tertiary)">基金</div>
        <div style="font-size:18px;font-weight:800;margin-top:4px">¥${fmtMoney(Math.round(nw.fundOnlyValue||0))}</div>
      </div>
    </div>
    <button class="mb-btn mb-btn--secondary mb-btn--block" onclick="document.querySelector('.modal-overlay')?.remove();navigateTo('portfolio')">查看持仓详情 →</button>
  </div>`;
  document.body.appendChild(o);
}



// C4 v9.5.48: 地缘事件 -> 持仓影响图谱
// 基于事件类别（军事/能源/科技/贸易等）匹配持仓中可能受影响的标的
window._showGeoImpactMap = function(events, topCategory){
  const categoryImpactMap = {
    '军事冲突': {sectors:['军工','航空','防务','石油','黄金'], reasoning:'避险资产受益，能源价格波动'},
    '能源危机': {sectors:['石油','天然气','新能源','化工','航空'], reasoning:'能源股直接影响，航运/化工成本上升'},
    '科技博弈': {sectors:['半导体','芯片','AI','5G','软件'], reasoning:'供应链限制 + 国产替代加速'},
    '贸易摩擦': {sectors:['出口','航运','纺织','机械','农产品'], reasoning:'关税影响出口企业'},
    '政策调整': {sectors:['银行','地产','基建','医药'], reasoning:'政策方向直接影响相关板块'},
    '自然灾害': {sectors:['农业','保险','航运','水电'], reasoning:'供给端冲击 + 保险赔付'},
  };
  let allHoldings = [];
  try{
    const cache = window._familyDataCache;
    if(cache && cache.members){
      cache.members.forEach(m=>{
        (m.holdings||[]).forEach(h=> allHoldings.push(Object.assign({},h,{owner:m.userId})));
      });
    }
    if(!allHoldings.length && typeof loadTxns==='function'){
      const txns = loadTxns();
      const map = {};
      txns.forEach(t=>{
        const c = t.code||t.fundCode||''; if(!c) return;
        if(!map[c]) map[c] = {code:c, name:t.name||t.fundName||c};
      });
      allHoldings = Object.values(map);
    }
  }catch(e){}

  const impactsByCat = {};
  events.forEach(ev=>{
    const cat = ev.category || topCategory || '其他';
    const info = categoryImpactMap[cat] || {sectors:[], reasoning:'影响范围需评估'};
    if(!impactsByCat[cat]) impactsByCat[cat] = {sectors:info.sectors, reasoning:info.reasoning, events:[], affectedHoldings:[]};
    impactsByCat[cat].events.push(ev);
    allHoldings.forEach(h=>{
      const hint = (h.name||'') + ' ' + (h.industry_tag||'');
      info.sectors.forEach(sec=>{
        if(hint.indexOf(sec)>=0 && !impactsByCat[cat].affectedHoldings.find(x=>x.code===h.code)){
          impactsByCat[cat].affectedHoldings.push(Object.assign({},h,{matchedSector:sec}));
        }
      });
    });
  });

  const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
  const catKeys = Object.keys(impactsByCat);
  let bodyHtml = '';
  if(!catKeys.length){
    bodyHtml = '<div style="text-align:center;padding:20px;color:var(--text2);font-size:13px">暂无可分析的地缘事件</div>';
  } else {
    bodyHtml = catKeys.map(function(cat){
      const info = impactsByCat[cat];
      const sectorPills = info.sectors.map(s=>'<span style="display:inline-block;font-size:10px;padding:2px 7px;border-radius:4px;background:rgba(99,102,241,.1);color:#A5B4FC;margin-right:4px;margin-top:3px">'+s+'</span>').join('');
      const eventsList = info.events.slice(0,3).map(e=>'<div style="font-size:11px;color:var(--text-secondary,#9AA1AC);margin-top:3px">• '+(e.title||'').slice(0,60)+'</div>').join('');
      let affectedHtml = '';
      if(info.affectedHoldings.length){
        affectedHtml = '<div style="margin-top:10px;padding:8px;background:rgba(245,158,11,.08);border-radius:6px;border-left:3px solid #F59E0B"><div style="font-size:11px;color:#F59E0B;font-weight:600;margin-bottom:4px">⚠️ 你的持仓中可能受影响（'+info.affectedHoldings.length+'项）：</div>'+
          info.affectedHoldings.slice(0,5).map(h=>'<div style="font-size:11px;color:var(--text-default,#D8DCE5);padding:2px 0">• <b>'+(h.name||h.code)+'</b> <span style="font-size:10px;color:var(--text-tertiary,#7A8499)">('+h.code+')</span> <span style="font-size:10px;color:#A5B4FC;margin-left:4px">['+h.matchedSector+']</span></div>').join('')+
          (info.affectedHoldings.length>5?'<div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:4px">还有 '+(info.affectedHoldings.length-5)+' 项...</div>':'')+
        '</div>';
      } else if(allHoldings.length){
        affectedHtml = '<div style="margin-top:10px;padding:7px 10px;background:rgba(16,185,129,.06);border-radius:6px;font-size:11px;color:#34D399">✅ 你的持仓在该事件下风险敞口较低</div>';
      }
      return '<div style="margin-bottom:14px;padding:12px;background:rgba(255,255,255,.03);border-radius:10px;border:1px solid rgba(239,68,68,.15)">'+
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px"><span style="font-size:13px;font-weight:600;color:#F87171">'+cat+'</span><span style="font-size:10px;color:var(--text-tertiary,#7A8499)">'+info.events.length+' 起事件</span></div>'+
        '<div style="font-size:11px;color:var(--text-secondary,#9AA1AC);margin-bottom:6px">'+info.reasoning+'</div>'+
        '<div style="margin-bottom:6px">'+sectorPills+'</div>'+
        eventsList+affectedHtml+
      '</div>';
    }).join('');
  }

  o.innerHTML='<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:85vh;overflow-y:auto"><div class="modal-handle"></div><div class="modal-title">🔍 地缘事件 → 持仓影响图谱</div><div style="font-size:11px;color:var(--text-tertiary,#7A8499);margin-bottom:12px">基于事件类别与持仓关键字的粗匹配，仅供参考</div>'+bodyHtml+'<button class="mb-btn mb-btn--secondary mb-btn--block" onclick="document.querySelector(&quot;.modal-overlay&quot;)?.remove();navigateTo(&quot;portfolio&quot;)" style="margin-top:8px">查看完整持仓 →</button></div>';
  document.body.appendChild(o);
};
