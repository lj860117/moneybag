// ---- 持仓盈亏页（V4 交易流水制）----
// 持仓分类判断：股票 vs 基金
function _isStockHolding(h){
  if(h.assetType==='stock')return true;
  if(h.assetType==='fund')return false;
  // 向后兼容：category 含基金关键词→基金
  const fundKW=['基金','债券','货币','指数','ETF','QDII','混合','商品','联接'];
  if(h.category&&fundKW.some(k=>h.category.includes(k)))return false;
  // 代码推断：00/30/60开头6位纯数字且不在预设基金列表→股票
  const c=(h.code||'').replace(/^(sh|sz)/i,'');
  if(/^(00|30|60)\d{4}$/.test(c)&&!FUND_DETAILS[c])return true;
  return false; // 默认归基金
}

async function renderPortfolio(){currentPage='portfolio';renderNav();
const txns=loadTxns();const holdings=calcHoldingsFromTxns(txns);
const p=loadPortfolio(); // 兼容旧数据

const useV4=holdings.length>0;
const hasHoldings=holdings.length>0||p.holdings.length>0;
const displayHoldings=useV4?holdings:hasHoldings?p.holdings.map(h=>({code:h.code,name:h.name,category:h.category,shares:0,totalCost:h.amount,avgPrice:0})):[];
window._allHoldings=displayHoldings; // 全局存储供Tab过滤用
const tc=displayHoldings.reduce((s,h)=>s+h.totalCost,0);
const stockHoldings=displayHoldings.filter(h=>_isStockHolding(h));
const fundHoldings=displayHoldings.filter(h=>!_isStockHolding(h));
const stockTotal=stockHoldings.reduce((s,h)=>s+h.totalCost,0);
const fundTotal=fundHoldings.reduce((s,h)=>s+h.totalCost,0);

$('#app').innerHTML=`<div class="portfolio-page fade-up" style="padding-bottom:calc(var(--tabbar-height,76px) + 16px)">

<!-- Hero 总持仓（永远渲染） -->
<section class="mb-hero" style="margin-bottom:14px">
  <div class="mb-hero__label">💰 总持仓资产</div>
  <h2 class="mb-hero__num" id="portfolioHeroValue"><span class="mb-money__symbol">¥</span><span class="mb-money__num">${Math.round(tc).toLocaleString('zh-CN')}</span></h2>
  <div class="mb-hero__delta" id="pnlSum">
    <span class="mb-text-tertiary" style="font-size:var(--fs-sm,11px)">${API_AVAILABLE?(hasHoldings?'正在计算实时盈亏...':'暂无持仓数据'):'后端离线'}</span>
  </div>
  ${hasHoldings?`<div style="font-size:11px;color:var(--text-secondary,#9AA1AC);margin-top:6px;display:flex;gap:12px;justify-content:center">
    <span>📊 股票 ¥${fmtMoney(Math.round(stockTotal))}${stockHoldings.length?' ('+stockHoldings.length+'只)':''}</span>
    <span>💼 基金 ¥${fmtMoney(Math.round(fundTotal))}${fundHoldings.length?' ('+fundHoldings.length+'只)':''}</span>
  </div>`:''}
  <div style="margin-top:12px;background:rgba(255,255,255,.04);border-radius:var(--radius-sm,8px);padding:8px 12px;display:flex;align-items:center;gap:10px;font-size:11px">
    <span style="color:var(--color-brand-500,#FFB755);font-weight:700" id="portfolioHealthScore">75/100</span>
    <div style="flex:1;height:4px;background:rgba(255,255,255,.06);border-radius:2px;overflow:hidden"><div id="portfolioHealthBar" style="height:100%;width:75%;background:linear-gradient(90deg,var(--color-brand-500,#FFB755),var(--color-bull,#00E5A0));border-radius:2px;transition:width .5s"></div></div>
    <span class="mb-caption">健康分</span>
  </div>
</section>

<!-- 双账户卡（异步加载家庭数据） -->
<section class="mb-card--ghost" style="margin-bottom:14px" id="familyHoldingsCard">
  <div class="mb-flex mb-flex--between mb-mb-3">
    <b style="font-size:12px">👨‍👩 家庭持仓</b>
    <span class="mb-text-tertiary" style="font-size:10px">加载中...</span>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px" id="familyMembersGrid">
    <div class="mb-card--ghost" style="padding:10px">
      <div class="mb-flex mb-gap-2 mb-mb-1">
        <div class="mb-avatar mb-avatar--xs mb-avatar--leijiang">L</div>
        <b style="font-size:11px">${getProfileId()||'我'}</b>
      </div>
      <div class="mb-money mb-money--sm">¥${fmtMoney(Math.round(tc))}</div>
      <div class="mb-caption">本机数据</div>
    </div>
    <div class="mb-card--ghost" style="padding:10px;opacity:.5">
      <div style="font-size:11px;color:var(--text-tertiary);text-align:center;padding:8px">加载家庭成员...</div>
    </div>
  </div>
</section>

<!-- 行为风控提示（永远渲染） -->
<div style="background:linear-gradient(90deg,rgba(0,229,160,.06),rgba(0,229,160,.02));border:1px solid rgba(0,229,160,.15);border-radius:var(--radius-md,10px);padding:10px 12px;margin-bottom:14px;display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text-secondary,#9aa1ac)">
  <span class="mb-live-dot"></span>
  <span>行为风控：${hasHoldings?'正常运行中':'等待首笔交易'}</span>
  <span style="margin-left:auto;color:var(--color-bull,#00E5A0);font-weight:600">→</span>
</div>

<!-- 股票⇄基金 pill 切换 -->
<div class="mb-flex mb-gap-3" style="margin-bottom:14px">
  <a class="mb-pill mb-pill--on" id="tabStockBtn" onclick="showStockHoldings()" style="cursor:pointer">📊 股票</a>
  <a class="mb-pill" id="tabFundBtn" onclick="showFundHoldings()" style="cursor:pointer">💼 基金</a>
  <a class="mb-pill" id="tabTxnBtn" onclick="showTxnHistory()" style="cursor:pointer;margin-left:auto">📋 记录</a>
</div>

<!-- 风险纪律提醒（永远渲染） -->
<div class="mb-card--warn" style="margin-bottom:14px">
  <b style="display:block;color:var(--color-bear,#FF6B6B);font-size:12px;margin-bottom:3px">⚠️ 投资纪律提醒</b>
  <span style="font-size:11px;color:var(--text-secondary,#9AA1AC)">单只持仓不超过总资产 30%，止损线 -15%，止盈线 +50%</span>
</div>

<!-- 持仓列表 -->
<div id="holdingsContent">
</div>

<!-- 交易记录（初始隐藏） -->
<div id="txnContent" style="display:none">
${txns.length?`<div style="font-size:12px;font-weight:700;margin-bottom:8px">📋 交易记录 (${txns.length})</div>
<div id="txnList">${txns.slice(-20).reverse().map(t=>{
const isBuy=t.type==='BUY';
return`<div class="mb-card" style="margin-bottom:6px;padding:10px;display:flex;align-items:center;gap:10px">
<div style="font-size:16px">${isBuy?'🟢':'🔴'}</div>
<div style="flex:1"><div style="font-size:12px;font-weight:600">${t.type} ${t.name||t.code}${t.note?' · '+t.note:''}</div>
<div class="mb-caption">${new Date(t.date).toLocaleString('zh-CN')} · ${t.shares?.toFixed(2)||'-'}份 × ¥${t.price?.toFixed(4)||'-'}</div></div>
<div style="font-size:13px;font-weight:700;color:${isBuy?'var(--color-bull,#00E5A0)':'var(--color-bear,#FF6B6B)'}">¥${Math.round(t.amount||t.shares*t.price)}</div></div>`}).join('')}</div>`:'<div class="mb-empty"><div class="mb-empty__icon">📋</div><div class="mb-empty__title">暂无交易记录</div></div>'}
</div>

<div id="riskActionsSection"></div>
<div id="riskMetricsSection"><div style="text-align:center;padding:12px;font-size:12px;color:var(--text-secondary,#9AA1AC)">${API_AVAILABLE?'正在加载风控体检...':''}</div></div>

<div class="mb-flex mb-gap-3" style="margin-top:16px">
<button class="mb-btn mb-btn--secondary mb-btn--block" onclick="startQuiz()">🔄 重新测评</button>
<button class="mb-btn mb-btn--secondary mb-btn--block" style="color:var(--color-bear,#FF6B6B)" onclick="if(confirm('清除所有持仓和交易记录？')){localStorage.removeItem(TXN_KEY);localStorage.removeItem(STORAGE_KEY);renderPortfolio()}">🗑️ 清除</button>
</div></div>`;

// 初始渲染时立即按当前Tab过滤持仓列表
if(!window._portfolioTab)window._portfolioTab='stock';
_renderFilteredHoldings();

// 异步更新实时盈亏
if(API_AVAILABLE&&useV4){
try{
const body={holdings:displayHoldings.map(h=>({code:h.code,name:h.name,category:h.category,amount:Math.round(h.totalCost),targetPct:0,buyDate:new Date().toISOString()}))};
const r=await fetch(API_BASE+'/portfolio/pnl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
if(r.ok){const pnl=await r.json();
const pe=document.getElementById('pnlSum');
if(pe){const sg=pnl.totalPnl>=0?'+':'';const cls=pnl.totalPnl>=0?'mb-pill--bull':'mb-pill--bear';
pe.innerHTML=`<span class="mb-pill ${cls}">${sg}${fmtFull(Math.round(pnl.totalPnl))}(${sg}${pnl.totalPnlPct.toFixed(2)}%)</span><span class="mb-text-tertiary" style="font-size:var(--fs-sm,11px)">当前市值${fmtFull(Math.round(pnl.totalMarket))}</span>`}}}catch{}}
// 异步加载家庭持仓数据
if(API_AVAILABLE){_loadFamilyPortfolio()}
// 异步加载风控指标
if(API_AVAILABLE){loadRiskMetrics();loadRiskActions()}}

// 加载家庭持仓汇总（从后端拉取所有家庭成员数据）
async function _loadFamilyPortfolio(){
  try{
    const r=await fetch(API_BASE+'/family/portfolio-summary?userId='+encodeURIComponent(getProfileId()),{signal:AbortSignal.timeout(10000)});
    if(!r.ok)return;
    const d=await r.json();
    if(!d.available||!d.members)return;
    const card=document.getElementById('familyHoldingsCard');
    if(!card)return;
    const members=d.members;
    const total=d.familyTotal||0;
    card.innerHTML=`
      <div class="mb-flex mb-flex--between mb-mb-3">
        <b style="font-size:12px">👨‍👩 家庭持仓</b>
        <span class="mb-text-tertiary" style="font-size:10px">${members.length} 人 · 合计 ¥${fmtMoney(Math.round(total))}</span>
      </div>
      <div style="display:grid;grid-template-columns:${members.length>1?'1fr 1fr':'1fr'};gap:8px">
        ${members.map(m=>{
          const pct=total>0?Math.round(m.investTotal/total*100):0;
          const initial=m.userId.charAt(0).toUpperCase();
          return`<div class="mb-card--ghost" style="padding:10px">
            <div class="mb-flex mb-gap-2 mb-mb-1">
              <div class="mb-avatar mb-avatar--xs" style="background:linear-gradient(135deg,${m.userId===getProfileId()?'#F59E0B,#D97706':'#A855F7,#7C3AED'})">${initial}</div>
              <b style="font-size:11px">${m.userId}</b>
            </div>
            <div class="mb-money mb-money--sm">¥${fmtMoney(Math.round(m.investTotal))}</div>
            <div class="mb-caption">📊${m.stockCount||0}只股票 · 💼${m.fundCount||0}只基金 · 占比${pct}%</div>
          </div>`}).join('')}
      </div>`;
  }catch(e){console.warn('[Family]',e)}}

// Tab 切换辅助
function showStockHoldings(){
  window._portfolioTab='stock';
  _renderFilteredHoldings();
  document.getElementById('holdingsContent').style.display='';
  document.getElementById('txnContent').style.display='none';
  document.getElementById('tabStockBtn').className='mb-pill mb-pill--on';
  document.getElementById('tabFundBtn').className='mb-pill';
  document.getElementById('tabTxnBtn').className='mb-pill';
}
function showFundHoldings(){
  window._portfolioTab='fund';
  _renderFilteredHoldings();
  document.getElementById('holdingsContent').style.display='';
  document.getElementById('txnContent').style.display='none';
  document.getElementById('tabStockBtn').className='mb-pill';
  document.getElementById('tabFundBtn').className='mb-pill mb-pill--on';
  document.getElementById('tabTxnBtn').className='mb-pill';
}
function _renderFilteredHoldings(){
  const all=window._allHoldings||[];
  const isStock=(window._portfolioTab||'stock')==='stock';
  const filtered=all.filter(h=>isStock?_isStockHolding(h):!_isStockHolding(h));
  const listEl=document.getElementById('holdList');
  const contentEl=document.getElementById('holdingsContent');
  if(!contentEl)return;
  const assetLabel=isStock?'股票':'基金';
  if(filtered.length){
    const html=filtered.map(h=>`<div class="mb-card" style="margin-bottom:8px;padding:12px;cursor:pointer" onclick="showHoldingActions('${h.code}')">
<div class="mb-flex mb-flex--between">
<div><div style="font-size:var(--fs-md,14px);font-weight:var(--fw-semibold,600)">${h.name}</div>
<div class="mb-caption">${h.category||assetLabel}${h.shares?' · '+h.shares.toFixed(2)+'份':''}${h.avgPrice?' · 均价¥'+h.avgPrice.toFixed(4):''}</div></div>
<div style="text-align:right"><div class="mb-money mb-money--sm">${fmtFull(Math.round(h.totalCost))}</div></div></div></div>`).join('');
    contentEl.innerHTML=`<div id="holdList">${html}</div>
<div class="mb-flex mb-gap-3" style="margin-top:14px">
<button class="mb-btn mb-btn--primary mb-btn--block" onclick="showAddTxn()">➕ 新交易</button>
<button class="mb-btn mb-btn--secondary mb-btn--block" onclick="showAddCustomFund()">🔍 添加自选</button>
</div>`;
  }else{
    contentEl.innerHTML=`<div class="mb-empty">
  <div class="mb-empty__icon">${isStock?'📊':'💼'}</div>
  <div class="mb-empty__title">还没有${assetLabel}持仓</div>
  <div class="mb-empty__desc">点击下方按钮录入${assetLabel}交易</div>
  <div class="mb-flex mb-flex--center mb-gap-3" style="flex-wrap:wrap">
    <button class="mb-btn mb-btn--primary" onclick="showAddTxn()">➕ 添加${assetLabel}</button>
  </div>
</div>`;
  }
}
function showTxnHistory(){
  document.getElementById('holdingsContent').style.display='none';
  document.getElementById('txnContent').style.display='';
  document.getElementById('tabStockBtn').className='mb-pill';
  document.getElementById('tabFundBtn').className='mb-pill';
  document.getElementById('tabTxnBtn').className='mb-pill mb-pill--on';
}

async function loadRiskMetrics(){
try{const r=await fetch(API_BASE+'/risk-metrics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId()}),signal:AbortSignal.timeout(15000)});
if(!r.ok)return;const rm=await r.json();
const el=document.getElementById('riskMetricsSection');if(!el)return;
const conc=rm.concentration||{};const dd=rm.drawdown||{};const corr=rm.correlation||{};const alerts=rm.alerts||[];
const concColor=conc.level==='高度集中'?'var(--red)':conc.level==='适度集中'?'var(--accent)':'var(--green)';
const ddColor=dd.level==='严重回撤'?'var(--red)':dd.level==='中度回撤'?'var(--accent)':'var(--green)';
const corrColor=corr.avg>0.6?'var(--red)':corr.avg>0.4?'var(--accent)':'var(--green)';
setExplain('risk_hhi','持仓集中度(HHI)','HHI(赫芬达尔指数) = 每只基金占比的平方和 × 10000\n\n📊 当前HHI：'+conc.hhi+'\n📊 最大单品占比：'+conc.max_single+'%\n📊 评级：'+conc.level+'\n\n🔍 怎么看：\n• HHI < 3000 → 分散良好 ✅\n• 3000-5000 → 适度集中 ⚠️\n• > 5000 → 高度集中 🔴\n\n💡 "不要把鸡蛋放在一个篮子里"——分散投资是最基本的风控。');
setExplain('risk_dd','回撤监控','回撤 = 从最高点跌了多少。\n\n📊 当前回撤：'+dd.current+'%\n📊 评级：'+dd.level+'\n\n🔍 怎么看：\n• < 10% → 正常波动\n• 10-20% → 需要注意，检查基本面\n• > 20% → 严重回撤，要认真审视持仓\n\n⚠️ 最大回撤是投资中最重要的风险指标之一。\n💡 控制回撤的关键是分散配置+止盈纪律。');
setExplain('risk_corr','相关性分析','相关性 = 持仓基金之间的涨跌联动程度。\n\n📊 平均相关性：'+corr.avg+'\n📊 分析：'+corr.detail+'\n\n🔍 怎么看：\n• < 0.3 → 低相关，对冲效果好 ✅\n• 0.3-0.6 → 中等相关\n• > 0.6 → 高相关，涨跌同步 ⚠️\n\n💡 股+债+黄金 是经典低相关组合。\n全买股票型基金 = 高相关 = 风险集中。');
let alertHtml='';
if(alerts.length){alertHtml=alerts.map(a=>{
const ic=a.severity==='danger'?'🔴':a.severity==='warning'?'⚠️':'💡';
const bg=a.severity==='danger'?'rgba(239,68,68,.1)':a.severity==='warning'?'rgba(245,158,11,.1)':'rgba(59,130,246,.08)';
return`<div style="background:${bg};border-radius:8px;padding:8px 10px;margin-bottom:6px;font-size:12px">${ic} ${a.message}</div>`}).join('')}
el.innerHTML=`<div class="section-title" style="margin-top:20px">🛡️ 风控体检 <span style="font-size:11px;color:var(--accent);font-weight:400">借鉴幻方CVaR</span></div>
${alertHtml}
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">
<div style="background:var(--card);border-radius:10px;padding:10px;cursor:pointer" onclick="showExplain('risk_hhi')">
<div style="font-size:11px;color:var(--text2)">集中度 HHI</div>
<div style="font-size:18px;font-weight:900;color:${concColor};margin-top:2px">${conc.hhi}</div>
<div style="font-size:10px;color:${concColor}">${conc.level}</div></div>
<div style="background:var(--card);border-radius:10px;padding:10px;cursor:pointer" onclick="showExplain('risk_dd')">
<div style="font-size:11px;color:var(--text2)">当前回撤</div>
<div style="font-size:18px;font-weight:900;color:${ddColor};margin-top:2px">${dd.current}%</div>
<div style="font-size:10px;color:${ddColor}">${dd.level}</div></div>
<div style="background:var(--card);border-radius:10px;padding:10px;cursor:pointer" onclick="showExplain('risk_corr')">
<div style="font-size:11px;color:var(--text2)">相关性</div>
<div style="font-size:18px;font-weight:900;color:${corrColor};margin-top:2px">${corr.avg}</div>
<div style="font-size:10px;color:${corrColor}">${corr.detail.slice(0,8)}</div></div></div>`}catch(e){console.warn('Risk metrics load failed:',e)}}

// 风控硬阈值执行建议（借鉴豆包方案+幻方量化）
async function loadRiskActions(){
try{const r=await fetch(API_BASE+'/risk-actions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId()}),signal:AbortSignal.timeout(15000)});
if(!r.ok)return;const data=await r.json();
const el=document.getElementById('riskActionsSection');if(!el)return;
const actions=data.actions||[];const summary=data.summary||'';const level=data.risk_level||'safe';
if(!actions.length){el.innerHTML=`<div style="margin-top:16px;padding:12px 14px;border-radius:12px;background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.2)">
<div style="font-size:13px;font-weight:700;color:var(--green)">🟢 风控指令</div>
<div style="font-size:12px;color:var(--green);margin-top:4px">${summary}</div></div>`;return}
const borderColor=level==='danger'?'rgba(239,68,68,.3)':level==='warning'?'rgba(245,158,11,.3)':'rgba(34,197,94,.2)';
const bgColor=level==='danger'?'rgba(239,68,68,.06)':level==='warning'?'rgba(245,158,11,.06)':'rgba(34,197,94,.06)';
const headerColor=level==='danger'?'var(--red)':level==='warning'?'var(--accent)':'var(--green)';
const actionsHtml=actions.map(a=>{
const bg=a.level==='danger'?'rgba(239,68,68,.1)':a.level==='warning'?'rgba(245,158,11,.1)':'rgba(59,130,246,.08)';
const border=a.level==='danger'?'rgba(239,68,68,.2)':a.level==='warning'?'rgba(245,158,11,.2)':'rgba(59,130,246,.15)';
return`<div style="background:${bg};border:1px solid ${border};border-radius:8px;padding:10px 12px;margin-top:6px">
<div style="font-size:13px;font-weight:600;line-height:1.5">${a.action}</div>
<div style="font-size:11px;color:var(--text2);margin-top:3px">📋 ${a.rule}｜${a.detail}</div></div>`}).join('');
el.innerHTML=`<div style="margin-top:16px;padding:14px;border-radius:12px;background:${bgColor};border:1px solid ${borderColor}">
<div style="display:flex;align-items:center;justify-content:space-between">
<div style="font-size:14px;font-weight:800;color:${headerColor}">⚡ 风控执行指令</div>
<div style="font-size:11px;color:${headerColor};font-weight:600">${summary}</div></div>
${actionsHtml}</div>`}catch(e){console.warn('Risk actions load failed:',e)}}

// 大类资产配置建议（总览页）
async function loadAllocationAdvice(){
try{const r=await fetch(API_BASE+'/allocation-advice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId()}),signal:AbortSignal.timeout(15000)});
if(!r.ok)return;const data=await r.json();
if(!data||!data.target){const el=document.getElementById('allocationSection');if(el)el.innerHTML='<div class="dashboard-card-title">🥧 资产配置</div><div style="padding:12px;font-size:12px;color:var(--text2)">暂无配置数据，请先录入持仓</div>';return}
const el=document.getElementById('allocationSection');if(!el)return;
const t=data.target||{};const c=data.current||{};const dev=data.deviation||{};
const advice=data.advice||[];const zone=data.valuation_zone||'适中';const valPct=data.valuation_pct||50;
const zoneColor=zone==='低估'?'var(--green)':zone==='高估'?'var(--red)':'var(--accent)';
// 配置饼图（简化CSS饼图）
const stockC=c.stock||0;const bondC=c.bond||0;const cashC=c.cash||0;
const stockT=t.stock||65;const bondT=t.bond||25;const cashT=t.cash||10;
// 生成偏离度指示
function devBar(label,icon,cur,tgt,devVal){
const color=Math.abs(devVal)>8?(devVal>0?'var(--red)':'var(--accent)'):'var(--green)';
const sign=devVal>0?'+':'';
return`<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.06)">
<div style="font-size:16px">${icon}</div>
<div style="flex:1">
<div style="display:flex;justify-content:space-between;font-size:12px"><span style="color:var(--text2)">${label}</span><span style="font-weight:700">${cur.toFixed(0)}% <span style="color:var(--text2);font-weight:400">/ 目标${tgt}%</span></span></div>
<div style="height:4px;background:rgba(148,163,184,.1);border-radius:2px;margin-top:4px;overflow:hidden">
<div style="height:100%;width:${Math.min(cur/Math.max(tgt,1)*100,150)}%;background:${color};border-radius:2px;transition:width .3s"></div></div>
</div>
<div style="font-size:12px;font-weight:700;color:${color};min-width:45px;text-align:right">${sign}${devVal}%</div></div>`}
const adviceHtml=advice.length?advice.map(a=>{
const bg=a.direction==='reduce'?'rgba(239,68,68,.08)':'rgba(34,197,94,.08)';
const border=a.direction==='reduce'?'rgba(239,68,68,.15)':'rgba(34,197,94,.15)';
return`<div style="background:${bg};border:1px solid ${border};border-radius:8px;padding:8px 10px;margin-top:6px;font-size:12px;line-height:1.5">${a.message}</div>`}).join(''):'<div style="font-size:12px;color:var(--green);margin-top:6px">✅ 各资产类别偏离度在合理范围内</div>';
el.innerHTML=`<div class="dashboard-card-title">🎯 资产配置建议 <span style="font-size:11px;color:${zoneColor};font-weight:600">估值${zone}(${valPct}%)</span></div>
<div style="font-size:12px;color:var(--text2);margin-bottom:10px">${data.summary||''}</div>
${devBar('股票类','📊',stockC,stockT,dev.stock||0)}
${devBar('债券类','🏦',bondC,bondT,dev.bond||0)}
${devBar('现金类','💵',cashC,cashT,dev.cash||0)}
<div style="margin-top:8px;font-size:11px;color:var(--text2);padding:6px 8px;background:rgba(148,163,184,.04);border-radius:6px">📐 目标比例根据估值水平动态调整：低估→股票${ALLOCATION_PROFILES?.low?.stock*100||75}% / 高估→股票${ALLOCATION_PROFILES?.high?.stock*100||45}%</div>
${adviceHtml}`}catch(e){console.warn('Allocation advice load failed:',e);const el=document.getElementById('allocationSection');if(el)el.innerHTML=''}}
const ALLOCATION_PROFILES={low:{stock:0.75,bond:0.15,cash:0.10},mid:{stock:0.65,bond:0.25,cash:0.10},high:{stock:0.45,bond:0.35,cash:0.20}};

// 持仓操作弹窗（加仓/卖出/删除）
function showHoldingActions(code){
const txns=loadTxns();const holdings=calcHoldingsFromTxns(txns);
const h=holdings.find(x=>x.code===code);
const detail=FUND_DETAILS[code];
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">${h?h.name:detail?.fullName||code}</div>
<div class="modal-subtitle">${code}${h?` · ${h.shares.toFixed(2)}份 · 均价¥${h.avgPrice.toFixed(4)}`:''}</div>
${h?`<div class="modal-stat-grid" style="margin-bottom:16px">
<div class="modal-stat"><div class="modal-stat-label">持有份额</div><div class="modal-stat-value">${h.shares.toFixed(2)}</div></div>
<div class="modal-stat"><div class="modal-stat-label">总成本</div><div class="modal-stat-value">¥${Math.round(h.totalCost)}</div></div>
</div>`:''}
<div style="display:flex;flex-direction:column;gap:10px">
<button class="action-btn green" onclick="document.querySelector('.modal-overlay')?.remove();showAddTxnFor('${code}','BUY')">🟢 加仓买入</button>
${h?`<button class="action-btn primary" style="background:linear-gradient(135deg,var(--red),#DC2626);color:#fff" onclick="document.querySelector('.modal-overlay')?.remove();showAddTxnFor('${code}','SELL')">🔴 卖出</button>`:''}
<button class="action-btn secondary" onclick="document.querySelector('.modal-overlay')?.remove();showFundDetailModal('${code}','${(h?h.name:detail?.fullName||code).replace(/'/g,"")}')">📋 基金详情</button>
${h?`<button class="action-btn secondary" style="color:var(--red)" onclick="if(confirm('删除所有交易记录？')){document.querySelector('.modal-overlay')?.remove();deleteFundTxns('${code}')}">🗑️ 删除持仓</button>`:''}
</div></div>`;
document.body.appendChild(o)}

// 添加交易弹窗
function showAddTxn(){showAddTxnFor('','BUY')}

function showAddTxnFor(code,type){
const isFund=(window._portfolioTab||'stock')==='fund';
const assetLabel=isFund?'基金':'股票';
const detail=code?FUND_DETAILS[code]:null;
const allCodes=Object.keys(FUND_DETAILS);
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">${type==='BUY'?'🟢 买入':'🔴 卖出'}${assetLabel}</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
<div class="form-row"><div class="form-label">${assetLabel}代码</div>
<input class="form-input" type="text" id="txnCode" placeholder="输入${isFund?'6位基金代码 如 110020':'股票代码 如 600519'}" value="${code}" ${code?'readonly':''} inputmode="numeric"></div>
<div class="form-row"><div class="form-label">${assetLabel}名称</div>
<input class="form-input" type="text" id="txnName" placeholder="${assetLabel}名称（输入代码自动填充）" value="${detail?.fullName||''}" readonly style="opacity:.7"></div>
<div id="txnLookupHint" style="font-size:11px;color:var(--accent);padding:0 0 8px;display:none"></div>
<div class="form-row"><div class="form-label">${isFund?'买入/卖出金额(¥)':'买入/卖出金额(¥)'}</div>
<input class="form-input" type="number" id="txnAmt" placeholder="0" inputmode="decimal"></div>
<div class="form-row"><div class="form-label">${isFund?'净值(每份价格)':'股价(每股价格)'}</div>
<input class="form-input" type="number" id="txnPrice" placeholder="${isFund?'如 1.2345':'如 25.60'}" step="0.0001" inputmode="decimal" value="${liveNavData[code]?.nav||''}"></div>
<div style="font-size:10px;color:var(--text-tertiary,#7A8499);padding:0 0 6px">💡 自动填充当前价，历史交易可手动修改</div>
<div class="form-row"><div class="form-label">${isFund?'份额(自动计算)':'股数(自动计算)'}</div>
<input class="form-input" type="number" id="txnShares" placeholder="金额÷${isFund?'净值':'股价'}" readonly style="opacity:.6"></div>
<div class="form-row"><div class="form-label">备注</div>
<input class="form-input" type="text" id="txnNote" placeholder="可选"></div>
<button class="form-submit" style="background:${type==='BUY'?'var(--green)':'var(--red)'}" onclick="confirmAddTxn('${type}')">确认${type==='BUY'?'买入':'卖出'}</button>
</div></div>`;
document.body.appendChild(o);
// 自动算份额
const amtIn=document.getElementById('txnAmt');const priceIn=document.getElementById('txnPrice');const sharesIn=document.getElementById('txnShares');
const calcShares=()=>{const a=parseFloat(amtIn?.value);const p=parseFloat(priceIn?.value);if(a>0&&p>0)sharesIn.value=(a/p).toFixed(2)};
amtIn?.addEventListener('input',calcShares);priceIn?.addEventListener('input',calcShares);
// 代码输入自动查询名称和价格
if(!code){const codeIn=document.getElementById('txnCode');const nameIn=document.getElementById('txnName');const hintEl=document.getElementById('txnLookupHint');
let _lookupTimer=null;
codeIn?.addEventListener('input',()=>{
  clearTimeout(_lookupTimer);
  const c=codeIn.value.trim();
  // 本地先查
  const d=FUND_DETAILS[c];if(d){nameIn.value=d.fullName;if(liveNavData[c]){priceIn.value=liveNavData[c].nav;calcShares()}return}
  // 代码够长才查 API
  if(c.length>=5){
    hintEl.textContent='🔍 查询中...';hintEl.style.display='';
    _lookupTimer=setTimeout(async()=>{
      try{
        if(isFund){
          const r=await fetch(API_BASE+'/fund/detail/'+c,{signal:AbortSignal.timeout(8000)});
          if(r.ok){const fd=await r.json();nameIn.value=fd.name||'';if(fd.nav){priceIn.value=fd.nav;calcShares()}hintEl.textContent='✅ '+fd.name+(fd.fund_type?' · '+fd.fund_type:'');hintEl.style.display=''}
          else{hintEl.textContent='未找到该基金';hintEl.style.display=''}
        }else{
          const r=await fetch(API_BASE+'/stock-basic/'+c,{signal:AbortSignal.timeout(8000)});
          if(r.ok){const sd=await r.json();nameIn.value=sd.name||'';if(sd.price){priceIn.value=sd.price;calcShares()}hintEl.textContent='✅ '+sd.name+(sd.industry?' · '+sd.industry:'');hintEl.style.display=''}
          else{hintEl.textContent='未找到该股票';hintEl.style.display=''}
        }
      }catch(e){hintEl.textContent='查询超时';hintEl.style.display=''}
    },500)}
  else{hintEl.style.display='none'}
})}}

function confirmAddTxn(type){
const code=document.getElementById('txnCode')?.value?.trim();
const name=document.getElementById('txnName')?.value?.trim();
const amt=parseFloat(document.getElementById('txnAmt')?.value);
const price=parseFloat(document.getElementById('txnPrice')?.value);
const shares=parseFloat(document.getElementById('txnShares')?.value);
const note=document.getElementById('txnNote')?.value?.trim()||'';
if(!code){alert('请输入基金代码');return}
if(!amt||amt<=0){alert('请输入金额');return}
if(!price||price<=0){alert('请输入净值');return}
const txns=loadTxns();
txns.push({id:'txn_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,6),
type,code,name:name||code,category:FUND_DETAILS[code]?.type||'',assetType:(window._portfolioTab||'fund'),
shares:shares||amt/price,price,amount:amt,date:new Date().toISOString(),note,source:'manual'});
saveTxns(txns);
// 同步到后端
if(API_AVAILABLE)fetch(API_BASE+'/portfolio/transaction',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({userId:getUserId(),transaction:{type,code,name:name||code,shares:shares||amt/price,nav:price,amount:amt,note}})}).catch(()=>{});
document.querySelector('.modal-overlay')?.remove();
renderPortfolio()}

function deleteFundTxns(code){const txns=loadTxns().filter(t=>t.code!==code);saveTxns(txns);
if(API_AVAILABLE)fetch(API_BASE+'/portfolio/transaction/delete',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({userId:getUserId(),code})}).catch(()=>{});
renderPortfolio()}

// 添加自选基金弹窗
function showAddCustomFund(){
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">🔍 添加自选基金</div>
<div class="modal-subtitle">添加推荐列表之外的基金</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
<div class="form-row"><div class="form-label">基金代码</div>
<input class="form-input" type="text" id="customCode" placeholder="输入6位基金代码" inputmode="numeric"></div>
<div class="form-row"><div class="form-label">基金名称</div>
<input class="form-input" type="text" id="customName" placeholder="基金名称"></div>
<div id="searchResult"></div>
<button class="form-submit" onclick="confirmCustomFund()">确认添加并买入</button>
</div></div>`;
document.body.appendChild(o);
// 搜索功能
const codeIn=document.getElementById('customCode');
codeIn?.addEventListener('blur',async()=>{
const c=codeIn.value.trim();if(!c||c.length<3)return;
if(FUND_DETAILS[c]){document.getElementById('customName').value=FUND_DETAILS[c].fullName;return}
if(!API_AVAILABLE)return;
try{const r=await fetch(API_BASE+'/fund/search?q='+encodeURIComponent(c));if(r.ok){const d=await r.json();
if(d.results?.length){const f=d.results[0];document.getElementById('customName').value=f.name||'';
document.getElementById('searchResult').innerHTML=`<div style="padding:8px;font-size:12px;color:var(--green)">✅ 找到：${f.name} (${f.code})</div>`}}}catch{}})}

function confirmCustomFund(){
const code=document.getElementById('customCode')?.value?.trim();
const name=document.getElementById('customName')?.value?.trim();
if(!code){alert('请输入基金代码');return}
if(!name){alert('请输入基金名称');return}
document.querySelector('.modal-overlay')?.remove();
showAddTxnFor(code,'BUY')}


// --- 03-holdings-ai.js ---
/* =========================================================================
 * V6 欠账 3/6：持仓页 Pro 模式 AI 深度分析按钮
 * 方式：renderStocksContent / renderFundsContent 完成后，注入"AI 深度分析"按钮
 *       点击后调用 /api/stock-holdings/analyze 或 /api/fund-holdings/analyze
 * ========================================================================= */
;(function(){
  'use strict';

  // --- 通用：渲染 AI 分析结果弹窗 ---
  function _showAIAnalysis(title, data){
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };

    let body = '';
    if (data.error) {
      body = `<div style="padding:20px;text-align:center;color:var(--red)">${data.error}</div>`;
    } else if (data.analysis || data.summary) {
      // 结构化输出
      const summary = data.summary || data.analysis || '';
      const sections = data.sections || data.details || [];
      body = `<div style="font-size:14px;line-height:1.8;color:var(--text1);white-space:pre-wrap;margin-bottom:12px">${summary}</div>`;
      if (Array.isArray(sections) && sections.length) {
        sections.forEach(s => {
          body += `<div class="dashboard-card" style="margin-bottom:8px">
            <div style="font-size:13px;font-weight:700;margin-bottom:4px">${s.title || s.name || ''}</div>
            <div style="font-size:12px;color:var(--text2);line-height:1.6">${s.content || s.detail || ''}</div>
          </div>`;
        });
      }
      if (data.risk_warnings && data.risk_warnings.length) {
        body += `<div style="margin-top:8px;padding:10px;background:rgba(239,68,68,.06);border-radius:8px;border-left:3px solid var(--red)">
          <div style="font-size:12px;font-weight:700;color:var(--red);margin-bottom:4px">⚠️ 风险提示</div>
          ${data.risk_warnings.map(w => `<div style="font-size:12px;color:var(--text2);line-height:1.5">• ${w}</div>`).join('')}
        </div>`;
      }
      if (data.suggestions && data.suggestions.length) {
        body += `<div style="margin-top:8px;padding:10px;background:rgba(16,185,129,.06);border-radius:8px;border-left:3px solid var(--green)">
          <div style="font-size:12px;font-weight:700;color:var(--green);margin-bottom:4px">💡 建议</div>
          ${data.suggestions.map(s => `<div style="font-size:12px;color:var(--text2);line-height:1.5">• ${typeof s === 'string' ? s : (s.text || s.content || '')}</div>`).join('')}
        </div>`;
      }
    } else {
      body = `<div style="font-size:13px;color:var(--text2);line-height:1.6;white-space:pre-wrap">${JSON.stringify(data, null, 2)}</div>`;
    }

    overlay.innerHTML = `<div class="modal-sheet" style="max-height:85vh;overflow-y:auto">
      <div class="modal-handle"></div>
      <div class="modal-title">${title}</div>
      <div class="modal-subtitle" style="margin-bottom:12px">🤖 AI 深度分析 · Phase 5</div>
      ${body}
      <button class="form-submit" style="margin-top:16px" onclick="this.closest('.modal-overlay').remove()">关闭</button>
    </div>`;
    document.body.appendChild(overlay);
  }

  // --- 注入按钮到持仓 content 底部 ---
  function _injectStockAIBtn(){
    if (!isProMode()) return;
    const el = document.getElementById('holdingsContent');
    if (!el) return;
    if (el.querySelector('#v6StockAIBtn')) return;

    // 找最后一个 action-btn
    const btns = el.querySelectorAll('.action-btn');
    if (!btns.length) return;
    const lastBtn = btns[btns.length - 1].parentNode;

    const wrap = document.createElement('div');
    wrap.style.cssText = 'margin-top:8px';
    wrap.innerHTML = `<button id="v6StockAIBtn" class="action-btn secondary" style="width:100%;background:linear-gradient(135deg,rgba(59,130,246,.08),rgba(168,85,247,.08));border:1px solid rgba(59,130,246,.2)" onclick="window._v6AnalyzeStocks()">
      🧠 AI 深度分析（Pro）
    </button>`;
    lastBtn.parentNode.insertBefore(wrap, lastBtn.nextSibling);
  }

  function _injectFundAIBtn(){
    if (!isProMode()) return;
    const el = document.getElementById('holdingsContent');
    if (!el) return;
    if (el.querySelector('#v6FundAIBtn')) return;

    const btns = el.querySelectorAll('.action-btn');
    if (!btns.length) return;
    const lastBtn = btns[btns.length - 1].parentNode;

    const wrap = document.createElement('div');
    wrap.style.cssText = 'margin-top:8px';
    wrap.innerHTML = `<button id="v6FundAIBtn" class="action-btn secondary" style="width:100%;background:linear-gradient(135deg,rgba(16,185,129,.08),rgba(168,85,247,.08));border:1px solid rgba(16,185,129,.2)" onclick="window._v6AnalyzeFunds()">
      🧠 AI 深度分析（Pro）
    </button>`;
    lastBtn.parentNode.insertBefore(wrap, lastBtn.nextSibling);
  }

  // --- 全局分析函数（按钮 onclick 调用）---
  window._v6AnalyzeStocks = async function(){
    const btn = document.getElementById('v6StockAIBtn');
    if (btn) { btn.disabled = true; btn.innerHTML = '🧠 正在分析...请稍候（30s）'; }
    const d = await _v6Fetch('/stock-holdings/analyze?' + getProfileParam(), { timeout: 60000 });
    if (btn) { btn.disabled = false; btn.innerHTML = '🧠 AI 深度分析（Pro）'; }
    if (d) _showAIAnalysis('📊 股票持仓 AI 深度分析', d);
    else alert('分析请求失败，请稍后重试');
  };

  window._v6AnalyzeFunds = async function(){
    const btn = document.getElementById('v6FundAIBtn');
    if (btn) { btn.disabled = true; btn.innerHTML = '🧠 正在分析...请稍候（30s）'; }
    const d = await _v6Fetch('/fund-holdings/analyze?' + getProfileParam(), { timeout: 60000 });
    if (btn) { btn.disabled = false; btn.innerHTML = '🧠 AI 深度分析（Pro）'; }
    if (d) _showAIAnalysis('💰 基金持仓 AI 深度分析', d);
    else alert('分析请求失败，请稍后重试');
  };

  // --- 劫持 renderStocksContent / renderFundsContent ---
  function _install(){
    let ok = true;
    if (typeof renderStocksContent === 'function') {
      _v6Hijack('renderStocksContent', () => setTimeout(_injectStockAIBtn, 100));
    } else { ok = false; }
    if (typeof renderFundsContent === 'function') {
      _v6Hijack('renderFundsContent', () => setTimeout(_injectFundAIBtn, 100));
    } else { ok = false; }
    return ok;
  }

  if (!_install()) {
    const t = setInterval(() => { if (_install()) clearInterval(t); }, 200);
    setTimeout(() => clearInterval(t), 5000);
  }

  console.log('[V6-3] holdings AI analysis patch installed');
})();

