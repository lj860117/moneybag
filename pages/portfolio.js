// ---- 持仓盈亏页（V4 交易流水制）----
// 持仓分类判断：股票 vs 基金
function _isStockHolding(h){
  if(h.assetType==='stock')return true;
  if(h.assetType==='fund')return false;
  // v9.5.119: 名称含基金关键词→一定是基金（优先级最高）
  const fundKW=['基金','债券','货币','指数','ETF','QDII','混合','商品','联接','LOF'];
  const nm=(h.name||'')+(h.category||'');
  if(fundKW.some(k=>nm.includes(k)))return false;
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
    <span>📊 股票 <span id="heroStockAmt">¥${fmtMoney(Math.round(stockTotal))}</span>${stockHoldings.length?' ('+stockHoldings.length+'只)':''}</span>
    <span>💼 基金 <span id="heroFundAmt">¥${fmtMoney(Math.round(fundTotal))}</span>${fundHoldings.length?' ('+fundHoldings.length+'只)':''}</span>
  </div>`:''}
  <div style="margin-top:12px;background:rgba(255,255,255,.04);border-radius:var(--radius-sm,8px);padding:8px 12px;display:flex;align-items:center;gap:10px;font-size:11px">
    <span style="color:var(--color-brand-500,#FFB755);font-weight:700" id="portfolioHealthScore">--/100</span>
    <div style="flex:1;height:4px;background:rgba(255,255,255,.06);border-radius:2px;overflow:hidden"><div id="portfolioHealthBar" style="height:100%;width:0%;background:linear-gradient(90deg,var(--color-brand-500,#FFB755),var(--color-bull,#00E5A0));border-radius:2px;transition:width .5s"></div></div>
    <span class="mb-caption">健康分</span>
  </div>
</section>

<!-- 双账户卡（异步加载家庭数据） -->
<section class="mb-card--ghost" style="margin-bottom:14px" id="familyHoldingsCard">
  <div class="mb-flex mb-flex--between mb-mb-3">
    <b style="font-size:12px">👨‍👩 家庭持仓</b>
    <span class="mb-text-tertiary" style="font-size:10px" id="familyStatus"></span>
  </div>
  <div style="display:grid;grid-template-columns:1fr;gap:8px" id="familyMembersGrid">
    <div class="mb-card--ghost" style="padding:10px">
      <div class="mb-flex mb-gap-2 mb-mb-1">
        <div class="mb-avatar mb-avatar--xs mb-avatar--leijiang">L</div>
        <b style="font-size:11px">${getProfileId()||'我'}</b>
      </div>
      <div class="mb-money mb-money--sm">¥${fmtMoney(Math.round(tc))}</div>
      <div class="mb-caption">📊 ${stockHoldings.length} 只股票 · 💼 ${fundHoldings.length} 只基金</div>
    </div>
  </div>
</section>

<!-- 行为风控摘要（动态更新） -->
<div style="background:linear-gradient(90deg,rgba(0,229,160,.06),rgba(0,229,160,.02));border:1px solid rgba(0,229,160,.15);border-radius:var(--radius-md,10px);padding:10px 12px;margin-bottom:14px;display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text-secondary,#9aa1ac)" id="behaviorTip">
  <span class="mb-live-dot"></span>
  <span>${hasHoldings?'持仓 '+(stockHoldings.length+fundHoldings.length)+' 只，正在监控涨跌/止盈/止损...':'录入首笔交易即开启智能监控'}</span>
</div>

<!-- v9.5.43 C3 资产分类折叠卡（基金/股票/现金一眼分布） -->
${hasHoldings?`<section id="categoryAccordion" style="margin-bottom:14px">
${(()=>{
  const totalAll = (typeof loadAssets==='function' ? loadAssets().reduce((s,a)=>s+(a.value||0),0) : 0) + tc;
  const stockPct = totalAll>0?(stockTotal/totalAll*100):0;
  const fundPct = totalAll>0?(fundTotal/totalAll*100):0;
  const cashTotal = totalAll - stockTotal - fundTotal;
  const cashPct = totalAll>0?(cashTotal/totalAll*100):0;
  const sortedFund = [...fundHoldings].sort((a,b)=>(b.totalCost||0)-(a.totalCost||0)).slice(0,3);
  const sortedStock = [...stockHoldings].sort((a,b)=>(b.totalCost||0)-(a.totalCost||0)).slice(0,3);
  const cats = [
    {key:'fund', icon:'💼', label:'基金', total:fundTotal, pct:fundPct, count:fundHoldings.length, top:sortedFund, color:'#A5B4FC'},
    {key:'stock', icon:'📊', label:'股票', total:stockTotal, pct:stockPct, count:stockHoldings.length, top:sortedStock, color:'#FFB755'},
    {key:'cash', icon:'💵', label:'现金/其他', total:cashTotal, pct:cashPct, count:0, top:[], color:'#10B981'},
  ];
  return cats.map(c=>{
    if(c.total <= 0 && c.key!=='cash') return '';
    const topHTML = c.top.length ? `<div style="margin-top:6px;font-size:10px;color:var(--text2);line-height:1.7">TOP3：${c.top.map(h=>`${h.name||h.code}（¥${fmtMoney(Math.round(h.totalCost||0))}）`).join('、')}</div>` : '';
    return `<div onclick="this.querySelector('.cat-detail').style.display=this.querySelector('.cat-detail').style.display==='none'?'block':'none';this.querySelector('.cat-arrow').textContent=this.querySelector('.cat-detail').style.display==='none'?'▶':'▼'" style="cursor:pointer;padding:10px 12px;background:rgba(15,23,42,.4);border:1px solid rgba(148,163,184,.1);border-radius:10px;margin-bottom:6px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
        <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:0">
          <span style="font-size:18px">${c.icon}</span>
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;font-weight:600;color:var(--text-default,#D8DCE5);display:flex;align-items:center;gap:6px">${c.label}${c.count>0?`<span style="font-size:10px;color:var(--text-tertiary)">${c.count}只</span>`:''}<span class="cat-arrow" style="font-size:10px;color:var(--text-tertiary);margin-left:auto">▶</span></div>
            <div style="height:4px;margin-top:5px;background:rgba(148,163,184,.1);border-radius:2px;overflow:hidden"><div style="height:100%;width:${c.pct}%;background:${c.color};border-radius:2px"></div></div>
          </div>
        </div>
        <div style="text-align:right;flex-shrink:0">
          <div class="cat-amt" data-cat="${c.key}" style="font-size:14px;font-weight:700;color:${c.color}">¥${fmtMoney(Math.round(c.total))}</div>
          <div style="font-size:10px;color:var(--text-tertiary)">${c.pct.toFixed(1)}%</div>
        </div>
      </div>
      <div class="cat-detail" style="display:none;margin-top:8px;padding-top:8px;border-top:1px solid rgba(148,163,184,.08)">${topHTML}${c.key==='cash'?'<div style="font-size:10px;color:var(--text-tertiary);margin-top:6px">含活期/定期/货基/其他流动资产</div>':''}</div>
    </div>`;
  }).join('');
})()}
</section>`:''}

<!-- 股票⇄基金 pill 切换 -->
<div class="mb-flex mb-gap-3" style="margin-bottom:14px">
  <a class="mb-pill mb-pill--on" id="tabStockBtn" onclick="showStockHoldings()" style="cursor:pointer">📊 股票</a>
  <a class="mb-pill" id="tabFundBtn" onclick="showFundHoldings()" style="cursor:pointer">💼 基金</a>
  <a class="mb-pill" id="tabTxnBtn" onclick="showTxnHistory()" style="cursor:pointer;margin-left:auto">📋 记录</a>
</div>

<!-- 风险纪律提醒（动态生成） -->
<div class="mb-card--warn" style="margin-bottom:14px">
  <b style="display:block;color:var(--color-bear,#FF6B6B);font-size:12px;margin-bottom:3px">⚠️ 投资纪律</b>
  <span style="font-size:11px;color:var(--text-secondary,#9AA1AC)">${hasHoldings?(displayHoldings.length===1?'⚠️ 仅 1 只持仓，集中度 100%，建议分散到 3-5 只':'单只不超过 30% · 止损 -15% · 止盈 +50% · 已持 '+displayHoldings.length+' 只'):'录入持仓后自动开启纪律监控'}</span>
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

// 初始渲染时立即按当前Tab过滤持仓列表（默认选有持仓的tab）
if(!window._portfolioTab){
  window._portfolioTab = stockHoldings.length > 0 ? 'stock' : fundHoldings.length > 0 ? 'fund' : 'stock';
}
_renderFilteredHoldings();
// 同步 tab 按钮高亮
if(window._portfolioTab==='fund'){
  const sb=document.getElementById('tabStockBtn');const fb=document.getElementById('tabFundBtn');
  if(sb)sb.className='mb-pill';if(fb)fb.className='mb-pill mb-pill--on';
}

// 异步更新实时盈亏
if(API_AVAILABLE&&useV4){
try{
// 传 shares + cost_nav（买入均价净值），接口优先用 shares*current_nav 精确计算市值
const body={holdings:displayHoldings.map(h=>({
  code:h.code,name:h.name,category:h.category,amount:Math.round(h.totalCost),targetPct:0,
  shares:h.shares&&h.shares>0?h.shares:undefined,          // 份额（有则传）
  cost_nav:h.avgPrice&&h.avgPrice>0?h.avgPrice:undefined,  // 买入均价净值（有则传）
  buyDate:''  // 不传今天日期，避免干扰历史净值查找
}))};
const r=await fetch(API_BASE+'/portfolio/pnl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
if(r.ok){const pnl=await r.json();
// 存储单只盈亏数据供列表渲染
window._holdingsPnl={};
// 接口返回 pnl.holdings（不是 pnl.details）
const _pnlList = pnl.holdings||pnl.details||[];
_pnlList.forEach(d=>{window._holdingsPnl[d.code]={marketValue:d.marketValue||d.market_value||0,pnlPct:d.pnlPct||d.pnl_pct||0,pnl:d.pnl||0,nav:d.nav||0,dayChange:d.dayChange||0,navDate:d.navDate||d.nav_date||''}})
// v9.5.21: 自动检测异常巨亏持仓（疑似累计净值/单位净值混淆）
try{ _scanAbnormalCostBasis(); }catch(e){console.warn('[ScanCost]',e)}
// 刷新持仓列表（带盈亏）
_renderFilteredHoldings();
const pe=document.getElementById('pnlSum');
if(pe){const sg=pnl.totalPnl>=0?'+':'';const cls=pnl.totalPnl>=0?'mb-pill--bull':'mb-pill--bear';
pe.innerHTML=`<span class="mb-pill ${cls}">${sg}${fmtFull(Math.round(pnl.totalPnl))}(${sg}${pnl.totalPnlPct.toFixed(2)}%)</span><span class="mb-text-tertiary" style="font-size:var(--fs-sm,11px)">当前市值 ¥${fmtMoney(Math.round(pnl.totalMarket))}</span>`}
// 更新 Hero 金额为市值
const heroVal=document.querySelector('.mb-hero__num');
if(heroVal&&pnl.totalMarket)heroVal.innerHTML=`<span class="mb-money__symbol">¥</span><span class="mb-money__num">${Math.round(pnl.totalMarket).toLocaleString('zh-CN')}</span>`;
}}catch(e){
  const pe=document.getElementById('pnlSum');
  if(pe)pe.innerHTML='<span class="mb-text-tertiary" style="font-size:11px">💤 非交易时段，盈亏将在下个交易日更新</span>';
}}
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
    // v9.5.30: 缓存到 window，让首页和持仓页共用 _showFamilyMemberHoldings 弹窗
    window._familyDataCache=d;

    // v9.8.1 修复：用后端实时市值同步 Hero 总持仓 + 盈亏摘要（解决本地成本与后端市值不一致问题）
    try{
      const me=d.members.find(m=>m.userId===getProfileId());
      if(me&&(me.investTotal||me.netWorth)){
        const mv=Math.round(me.investTotal||me.netWorth||0);
        const myPnl=me.pnl||0;
        const myPnlPct=me.pnlPct||0;
        // ① 更新 Hero 金额为后端市值
        const heroEl=document.getElementById('portfolioHeroValue');
        if(heroEl)heroEl.innerHTML=`<span class="mb-money__symbol">¥</span><span class="mb-money__num">${mv.toLocaleString('zh-CN')}</span>`;
        // ② 更新盈亏摘要
        const pnlEl=document.getElementById('pnlSum');
        if(pnlEl){
          const sg=myPnl>=0?'+':'';
          const cls=myPnl>=0?'mb-pill--bull':'mb-pill--bear';
          pnlEl.innerHTML=`<span class="mb-pill ${cls}">${sg}¥${fmtMoney(Math.abs(Math.round(myPnl)))}(${sg}${myPnlPct.toFixed(2)}%)</span><span class="mb-text-tertiary" style="font-size:var(--fs-sm,11px)">当前市值 ¥${fmtMoney(mv)}</span>`;
        }
        // ③ 同步底部资产分类行金额 + Hero 摘要行（用后端 fundTotal/stockTotal）
        if(me.fundTotal||me.stockTotal){
          const fundAmtEl=document.querySelector('.cat-amt[data-cat="fund"]');
          if(fundAmtEl&&me.fundTotal)fundAmtEl.textContent='¥'+fmtMoney(Math.round(me.fundTotal));
          const stockAmtEl=document.querySelector('.cat-amt[data-cat="stock"]');
          if(stockAmtEl&&me.stockTotal)stockAmtEl.textContent='¥'+fmtMoney(Math.round(me.stockTotal));
          // Hero 摘要行也同步
          const heroFundAmt=document.getElementById('heroFundAmt');
          if(heroFundAmt&&me.fundTotal)heroFundAmt.textContent='¥'+fmtMoney(Math.round(me.fundTotal));
          const heroStockAmt=document.getElementById('heroStockAmt');
          if(heroStockAmt&&me.stockTotal)heroStockAmt.textContent='¥'+fmtMoney(Math.round(me.stockTotal));
        }
      }
    }catch(syncErr){console.warn('[FamilySync] Hero同步失败:',syncErr);}

    const card=document.getElementById('familyHoldingsCard');
    if(!card)return;
    const members=d.members;
    const total=d.familyNetWorth||d.familyTotal||0;
    // v9.5.53 同步首页：对方涨跌 toggle
    const partnerHideKey='moneybag_partner_pnl_hidden';
    const partnerHidden=localStorage.getItem(partnerHideKey)==='1';
    card.innerHTML=`
      <div class="mb-flex mb-flex--between mb-mb-3">
        <b style="font-size:12px">👨‍👩 家庭持仓</b>
        <div style="display:flex;align-items:center;gap:6px">
          <button type="button" onclick="event.stopPropagation();_togglePartnerPnl();return false;" title="${partnerHidden?'点击显示对方涨跌':'点击隐藏对方涨跌'}" style="background:${partnerHidden?'rgba(245,158,11,.15)':'rgba(99,102,241,.12)'};border:1px solid ${partnerHidden?'rgba(245,158,11,.35)':'rgba(99,102,241,.3)'};border-radius:12px;font-size:11px;cursor:pointer;padding:3px 9px;color:${partnerHidden?'#F59E0B':'#A5B4FC'};display:inline-flex;align-items:center;gap:3px;font-weight:600">${partnerHidden?'🙈 已隐藏':'👁️ 显示中'}</button>
          <span class="mb-text-tertiary" style="font-size:10px">${members.length} 人 · ¥${fmtMoney(Math.round(total))}</span>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:${members.length>1?'1fr 1fr':'1fr'};gap:8px">
        ${members.map(m=>{
          const pct=total>0?Math.round((m.netWorth||m.investTotal||0)/total*100):0;
          const initial=m.userId.charAt(0).toUpperCase();
          const isMe=m.userId===getProfileId();
          const isEmpty=(m.netWorth||0)===0 && (m.fundCount||0)===0 && (m.stockCount||0)===0;
          const pnl=m.pnl||0; const pnlPct=m.pnlPct||0;
          const pnlColor=pnl>=0?'var(--color-bull,#FF6B6B)':'var(--color-bear,#00E5A0)';
          const pnlSign=pnl>=0?'+':'-';
          const pnlPctSign=pnlPct>=0?'+':'';
          // v9.5.53: 对方时模糊
          const shouldHidePnl = !isMe && partnerHidden;
          const blurStyle = shouldHidePnl ? ';filter:blur(4px);opacity:.4' : '';
          const pnlHtml = (!isEmpty && Math.abs(pnl)>0.01)
            ? `<div style="font-size:10px;color:${pnlColor};font-weight:600;margin-top:2px;transition:filter .2s${blurStyle}">${pnlSign}¥${fmtMoney(Math.abs(Math.round(pnl)))} (${pnlPctSign}${pnlPct.toFixed(2)}%)</div>`
            : '';
          const holdings = m.holdings || [];
          const clickable = !isEmpty && holdings.length > 0;
          const viewBtn = clickable
            ? `<button type="button" onclick="event.stopPropagation();_showFamilyMemberHoldings('${m.userId}')" style="margin-top:8px;width:100%;padding:7px;border-radius:8px;border:1px solid rgba(99,102,241,.35);background:rgba(99,102,241,.12);color:#818CF8;font-size:11px;font-weight:600;cursor:pointer">📋 查看 ${holdings.length} 只持仓</button>`
            : '';
          const cardClick = clickable ? `onclick="_showFamilyMemberHoldings('${m.userId}')"` : '';
          return`<div class="mb-card--ghost" style="padding:10px;${clickable?'cursor:pointer':''}" ${cardClick}>
            <div class="mb-flex mb-gap-2 mb-mb-1">
              <div class="mb-avatar mb-avatar--xs" style="background:linear-gradient(135deg,${isMe?'#F59E0B,#D97706':'#A855F7,#7C3AED'})">${initial}</div>
              <b style="font-size:11px">${m.userId}</b>
            </div>
            <div class="mb-money mb-money--sm">${isEmpty?'<span style="color:var(--text-tertiary,#7A8499);font-size:12px">待录入</span>':'¥'+fmtMoney(Math.round(m.netWorth||m.investTotal||0))}</div>
            ${pnlHtml}
            ${isEmpty?'':`<div class="mb-caption">📊${m.stockCount||0}股 · 💼${m.fundCount||0}基 · 占比${pct}%</div>`}
            ${viewBtn}
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
    const html=filtered.map(h=>{
      const pnlData=window._holdingsPnl?.[h.code];
      const mvStr=pnlData?`¥${fmtMoney(Math.round(pnlData.marketValue))}`:`¥${fmtMoney(Math.round(h.totalCost))}`;
      const pnlStr=pnlData?`<span style="color:${pnlData.pnlPct>=0?'var(--color-bull,#00E5A0)':'var(--color-bear,#FF6B6B)'};font-size:12px;font-weight:700">${pnlData.pnlPct>=0?'+':''}${pnlData.pnlPct.toFixed(2)}%</span>`:'';
      const dayStr=pnlData&&pnlData.dayChange!=null&&pnlData.dayChange!==0?(()=>{
        // v9.5.115: 显示真实净值日期；盘中标"估值"（与支付宝盘中显示一致）
        const isEst = pnlData.isEstimate;
        const dateLabel = isEst ? '盘中估值' : (pnlData.navDate?pnlData.navDate.slice(5):'净值日');
        return ` <span style="font-size:10px;color:${pnlData.dayChange>=0?'var(--color-bull,#00E5A0)':'var(--color-bear,#FF6B6B)'}" title="${isEst?'天天基金实时估值（与支付宝同源，每分钟刷新）':'当日官方单位净值'}">${dateLabel} ${pnlData.dayChange>=0?'+':''}${pnlData.dayChange.toFixed(2)}%</span>`;
      })():'';
      const navStr=pnlData&&pnlData.nav?`净值 ${pnlData.nav} · `:'';
            return`<div class="mb-card" style="margin-bottom:8px;padding:12px;cursor:pointer" onclick="showHoldingActions('${h.code}')">
<div class="mb-flex mb-flex--between">
<div><div style="font-size:var(--fs-md,14px);font-weight:var(--fw-semibold,600)">${h.name}</div>
<div class="mb-caption">${navStr}${h.category||assetLabel}${h.shares?' · '+h.shares.toFixed(2)+'份':''}${h.avgPrice?' · 成本¥'+h.avgPrice.toFixed(4):''}${dayStr}</div></div>
<div style="text-align:right"><div class="mb-money mb-money--sm">${mvStr}</div>${pnlStr}</div></div></div>`}).join('');
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
// 动态更新健康分
const healthScore = rm.health_score || Math.max(10, 100 - (rm.alerts||[]).length * 15 - (rm.concentration?.hhi > 5000 ? 20 : rm.concentration?.hhi > 3000 ? 10 : 0));
const hsEl=document.getElementById('portfolioHealthScore');
const hbEl=document.getElementById('portfolioHealthBar');
if(hsEl)hsEl.textContent=healthScore+'/100';
if(hbEl)hbEl.style.width=healthScore+'%';
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
<div style="font-size:11px;color:var(--text2);margin-bottom:8px">💡 点击各指标卡片查看详细解释</div>
${alertHtml}
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">
<div style="background:var(--card);border-radius:10px;padding:10px;cursor:pointer;position:relative" onclick="showExplain('risk_hhi')">
<div style="font-size:11px;color:var(--text2)">集中度 HHI <span style="color:var(--accent)">❓</span></div>
<div style="font-size:18px;font-weight:900;color:${concColor};margin-top:2px">${conc.hhi}</div>
<div style="font-size:10px;color:${concColor}">${conc.level}</div>
<div style="font-size:9px;color:var(--text2);margin-top:2px">&lt;3000好</div></div>
<div style="background:var(--card);border-radius:10px;padding:10px;cursor:pointer" onclick="showExplain('risk_dd')">
<div style="font-size:11px;color:var(--text2)">当前回撤 <span style="color:var(--accent)">❓</span></div>
<div style="font-size:18px;font-weight:900;color:${ddColor};margin-top:2px">${dd.current}%</div>
<div style="font-size:10px;color:${ddColor}">${dd.level}</div>
<div style="font-size:9px;color:var(--text2);margin-top:2px">&lt;10%正常</div></div>
<div style="background:var(--card);border-radius:10px;padding:10px;cursor:pointer" onclick="showExplain('risk_corr')">
<div style="font-size:11px;color:var(--text2)">相关性 <span style="color:var(--accent)">❓</span></div>
<div style="font-size:18px;font-weight:900;color:${corrColor};margin-top:2px">${corr.avg}</div>
<div style="font-size:10px;color:${corrColor}">${corr.detail.slice(0,8)}</div>
<div style="font-size:9px;color:var(--text2);margin-top:2px">&lt;0.5分散好</div></div></div>`}catch(e){console.warn('Risk metrics load failed:',e)}}

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
// v9.5.22/v9.5.24: 该 code 全部交易历史改为弹窗按钮，避免持仓详情越拉越长
const codeTxns = txns.filter(t=>t.code===code);
const txnCount = codeTxns.length;
const txnHistoryBtn = txnCount > 0
  ? `<button class="action-btn secondary" onclick="document.querySelector('.modal-overlay')?.remove();_showTxnHistory('${code}')" style="display:flex;align-items:center;justify-content:space-between"><span>📜 交易历史</span><span style="font-size:11px;color:var(--text-tertiary,#7A8499)">${txnCount} 笔 ›</span></button>`
  : '';

const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
// 继承金额隐藏状态
if(document.getElementById('landingRoot')?.classList.contains('money-masked') ||
   document.body.classList.contains('money-masked')){ o.classList.add('money-masked'); }
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">${h?h.name:detail?.fullName||code}</div>
<div class="modal-subtitle">${code}${h?` · ${h.shares.toFixed(2)}份 · 均价¥${h.avgPrice.toFixed(4)}`:''}</div>
${h?`<div class="modal-stat-grid" style="margin-bottom:16px">
<div class="modal-stat"><div class="modal-stat-label">持有份额</div><div class="modal-stat-value">${h.shares.toFixed(2)}</div></div>
<div class="modal-stat"><div class="modal-stat-label">总成本</div><div class="modal-stat-value" data-money>¥${Math.round(h.totalCost)}</div></div>
</div>`:''}
<div style="display:flex;flex-direction:column;gap:10px">
<button class="action-btn green" onclick="document.querySelector('.modal-overlay')?.remove();showAddTxnFor('${code}','BUY')">🟢 加仓买入</button>
${h?`<button class="action-btn primary" style="background:linear-gradient(135deg,var(--red),#DC2626);color:#fff" onclick="document.querySelector('.modal-overlay')?.remove();showAddTxnFor('${code}','SELL')">🔴 卖出</button>`:''}
<button class="action-btn primary" style="background:linear-gradient(135deg,var(--accent),#8B5CF6);color:#fff" onclick="document.querySelector('.modal-overlay')?.remove();showFundHoldingDetail('${code}')">📋 定投建议</button>
${txnHistoryBtn}
<button class="action-btn secondary" onclick="document.querySelector('.modal-overlay')?.remove();showFundDetailModal('${code}','${(h?h.name:detail?.fullName||code).replace(/'/g,"")}')">📊 基金详情</button>
${h?`<button class="action-btn secondary" style="color:var(--red)" onclick="if(confirm('删除所有交易记录？')){document.querySelector('.modal-overlay')?.remove();deleteFundTxns('${code}')}">🗑️ 删除全部持仓</button>`:''}
</div></div>`;
document.body.appendChild(o)}

// v9.5.24: 交易历史独立弹窗（按时间倒序，每笔独立 pnl，可单笔删除）
window._showTxnHistory = function(code){
  const txns = loadTxns();
  const codeTxns = txns.filter(t=>t.code===code).sort((a,b)=>new Date(b.date)-new Date(a.date));
  if(!codeTxns.length){ alert('暂无交易记录'); return; }
  const holding = calcHoldingsFromTxns(txns).find(x=>x.code===code);
  const fundName = holding?.name || codeTxns[0]?.name || code;
  const pnlData = (window._holdingsPnl||{})[code];
  const currentNav = pnlData?.nav || 0;

  const rows = codeTxns.map(t=>{
    const isBuy = t.type === 'BUY';
    const dateStr = (t.date||'').slice(0,10);
    const shares = Number(t.shares||0);
    // v9.5.109: 兼容 OCR 字段 nav 和手动 price
    const price = Number(t.price || t.nav || 0);
    const amount = Number(t.amount || shares*price);
    let pnlHtml = '';
    if(isBuy && currentNav>0 && shares>0 && price>0){
      const mv = currentNav * shares;
      const cost = price * shares;
      const pnl = mv - cost;
      const pnlPct = (currentNav - price) / price * 100;
      const pCol = pnl>=0 ? 'var(--color-bull,#FF6B6B)' : 'var(--color-bear,#00E5A0)';
      const pSign = pnl>=0 ? '+' : '-';
      const pctSign = pnl>=0 ? '+' : '';
      pnlHtml = `<div style="font-size:10px;color:${pCol};font-weight:600;text-align:right" data-money>${pSign}¥${fmtMoney(Math.abs(Math.round(pnl)))}<br>${pctSign}${pnlPct.toFixed(2)}%</div>`;
    } else if(!isBuy){
      pnlHtml = `<div style="font-size:10px;color:var(--text-tertiary,#7A8499);text-align:right">已卖出</div>`;
    }
    return `<div style="padding:10px 12px;border-radius:8px;background:rgba(255,255,255,.03);margin-bottom:6px;display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;font-size:9px;padding:2px 6px;border-radius:4px;background:${isBuy?'rgba(255,107,107,.15)':'rgba(0,229,160,.15)'};color:${isBuy?'#F87171':'#34D399'};flex-shrink:0;font-weight:600">${isBuy?'买入':'卖出'}</span>
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;color:var(--text-default,#D8DCE5)">${dateStr} · <span data-money>¥${fmtMoney(Math.round(amount))}</span></div>
        <div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:2px">${shares.toFixed(2)}份 @ ¥${price.toFixed(4)}</div>
      </div>
      ${pnlHtml}
      <button onclick="event.stopPropagation();_deleteSingleTxn('${t.id}','${code}')" style="background:none;border:none;color:var(--text-tertiary,#7A8499);font-size:14px;cursor:pointer;padding:6px 8px" title="删除此笔">🗑️</button>
    </div>`;
  }).join('');

  const o = document.createElement('div');
  o.className = 'modal-overlay';
  o.onclick = e => { if(e.target===o) o.remove(); };
  if(document.body.classList.contains('money-masked') || document.getElementById('landingRoot')?.classList.contains('money-masked')){
    o.classList.add('money-masked');
  }
  o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:85vh;display:flex;flex-direction:column">
    <div class="modal-handle"></div>
    <div class="modal-title">📜 ${fundName} · 交易历史</div>
    <div style="padding:6px 12px 12px;color:var(--text-secondary,#9AA1AC);font-size:11px">
      共 ${codeTxns.length} 笔${currentNav>0?` · 当前净值 ¥${currentNav.toFixed(4)}`:''}
    </div>
    <div style="flex:1;overflow-y:auto;padding:0 4px">${rows}</div>
    <div style="display:flex;gap:8px;margin-top:8px">
      <button class="mb-btn mb-btn--secondary" style="flex:1" onclick="document.querySelector('.modal-overlay')?.remove();showHoldingActions('${code}')">‹ 返回</button>
      <button class="mb-btn mb-btn--primary" style="flex:1" onclick="document.querySelector('.modal-overlay')?.remove();showAddTxnFor('${code}','BUY')">➕ 加仓买入</button>
    </div>
  </div>`;
  document.body.appendChild(o);
};

// v9.5.22: 删除单笔交易
window._deleteSingleTxn = function(txnId, code){
  if(!confirm('删除这一笔交易？\n（其他买入/卖出记录保留）')) return;
  const txns = loadTxns().filter(t => t.id !== txnId);
  saveTxns(txns);
  if(API_AVAILABLE){
    fetch(API_BASE+'/portfolio/transaction/delete',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({userId:getUserId(),txnId,code})}).catch(()=>{});
  }
  document.querySelector('.modal-overlay')?.remove();
  renderPortfolio();
  // 重新打开交易历史弹窗（如还有记录）
  setTimeout(()=>{
    const remaining = loadTxns().filter(t=>t.code===code);
    if(remaining.length > 0 && typeof _showTxnHistory==='function') _showTxnHistory(code);
    else if(typeof showHoldingActions==='function') showHoldingActions(code);
  }, 150);
};

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
${isFund&&type==='BUY'?`<div style="margin:8px 0 12px">
<button onclick="showReceiptParserInTxn()" style="width:100%;padding:10px;border-radius:10px;border:1px dashed rgba(99,102,241,.4);background:rgba(99,102,241,.06);color:#818CF8;font-size:13px;cursor:pointer">
📷 上传截图 / 📋 粘贴文字 → AI识别填入
</button>
</div>`:''}
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

// 凭证文字识别（新交易弹窗内调用）
window.showReceiptParserInTxn=function(){
const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.id='receiptOverlayTxn';overlay.style.zIndex='10000';
overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
overlay.innerHTML=`<div class="modal-sheet" style="max-height:85vh;overflow-y:auto" onclick="event.stopPropagation()">
  <div class="modal-handle"></div>
  <div class="modal-title">📋 识别买入凭证</div>

  <!-- 方式1：截图识别（推荐） -->
  <div style="margin-bottom:12px">
    <div style="font-size:12px;color:var(--text-secondary,#9AA1AC);margin-bottom:6px;line-height:1.6">
      📸 <b>方式1：上传截图（推荐）</b><br>
      支付宝/天天基金交易详情截图，AI自动识别
    </div>
    <input type="file" id="receiptImageFile" accept="image/*" style="display:none" onchange="doParseReceiptImage(this)">
    <button type="button" id="receiptImageBtn" onclick="document.getElementById('receiptImageFile').click()" style="width:100%;padding:12px;border-radius:10px;border:1px dashed rgba(99,102,241,.4);background:rgba(99,102,241,.06);color:#818CF8;font-size:13px;cursor:pointer">
      📷 选择截图文件
    </button>
    <!-- 截图识别状态区（显眼，紧贴按钮下方） -->
    <div id="receiptImageStatus" style="margin-top:10px"></div>
    <div id="receiptImagePreview" style="margin-top:8px;text-align:center"></div>
  </div>

  <div id="receiptDivider" style="text-align:center;font-size:11px;color:var(--text-tertiary,#7A8499);margin:8px 0;opacity:0.6">— 或 —</div>

  <!-- 方式2：粘贴文字 -->
  <div id="receiptTextSection">
    <div style="font-size:12px;color:var(--text-secondary,#9AA1AC);margin-bottom:6px;line-height:1.6">
      📝 <b>方式2：粘贴文字</b><br>
      长按页面文字 → 全选 → 复制 → 粘贴到下方：
    </div>
    <textarea id="receiptTextTxn" style="width:100%;height:100px;padding:10px;border-radius:10px;border:1px solid rgba(148,163,184,.2);background:var(--bg2);color:var(--text1);font-size:12px;resize:none;box-sizing:border-box" placeholder="买入产品 华夏半导体龙头混合C
确认净值 3.0270
确认份额 33.04份
买入金额 100.00元
确认时间 2026-05-22"></textarea>
    <button id="receiptTextBtn" class="form-submit" onclick="doParseReceiptInTxn()" style="margin-top:12px">🔍 识别文字并填入</button>
    <div id="receiptResultTxn" style="margin-top:8px;font-size:12px;text-align:center"></div>
  </div>
</div>`;
document.body.appendChild(overlay);};

// 图片识别：上传截图 → qwen-vl 识别
window.doParseReceiptImage=async function(input){
const file=input.files&&input.files[0];if(!file)return;
const status=document.getElementById('receiptImageStatus');
const preview=document.getElementById('receiptImagePreview');
const imgBtn=document.getElementById('receiptImageBtn');
const textBtn=document.getElementById('receiptTextBtn');
const textSection=document.getElementById('receiptTextSection');

// 显示预览
const reader=new FileReader();
reader.onload=async function(e){
  const dataUrl=e.target.result;
  preview.innerHTML=`<img src="${dataUrl}" style="max-width:100%;max-height:120px;border-radius:8px;border:1px solid rgba(148,163,184,.15)">`;

  // 显眼的识别中状态（带动画）
  status.innerHTML=`<div style="padding:14px;background:rgba(99,102,241,.1);border:1px solid rgba(99,102,241,.3);border-radius:10px;text-align:center">
    <div class="loading-spinner" style="width:24px;height:24px;border-width:3px;margin:0 auto 8px"></div>
    <div style="font-size:14px;font-weight:700;color:#818CF8;margin-bottom:4px">🤖 AI识别中...</div>
    <div style="font-size:11px;color:var(--text-secondary,#9AA1AC)">通义千问视觉模型分析中（约5-10秒）<br>请勿点击其他按钮</div>
  </div>`;

  // 禁用按钮，避免误触
  if(imgBtn){imgBtn.disabled=true;imgBtn.style.opacity='0.5';imgBtn.textContent='⏳ 处理中...';}
  if(textBtn){textBtn.disabled=true;textBtn.style.opacity='0.5';}
  if(textSection) textSection.style.opacity='0.4';

  const base64=dataUrl.split(',')[1];
  try{
    const r=await fetch(API_BASE+'/fund/parse-receipt',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({image_base64:base64}),
      signal:AbortSignal.timeout(60000)
    });
    const d=await r.json();
    if(!d.ok){
      status.innerHTML=`<div style="padding:12px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.3);border-radius:10px;text-align:center;color:var(--red);font-size:13px">❌ 识别失败：${d.reason}<br><span style="font-size:11px;opacity:0.8">请换文字粘贴方式</span></div>`;
      _restoreReceiptUI();
      return;
    }
    _fillTxnForm(d);
    status.innerHTML=`<div style="padding:14px;background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.3);border-radius:10px;text-align:center">
      <div style="font-size:18px;margin-bottom:4px">✅</div>
      <div style="font-size:14px;font-weight:700;color:#10B981;margin-bottom:4px">识别成功！</div>
      <div style="font-size:11px;color:var(--text-secondary,#9AA1AC)">${d.fund_name||''} 已自动填入表单</div>
    </div>`;
    setTimeout(()=>document.getElementById('receiptOverlayTxn')?.remove(),1500);
  }catch(e){
    status.innerHTML=`<div style="padding:12px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.3);border-radius:10px;text-align:center;color:var(--red);font-size:13px">❌ 识别失败：${e.message}</div>`;
    _restoreReceiptUI();
  }
};
reader.readAsDataURL(file);};

function _restoreReceiptUI(){
  const imgBtn=document.getElementById('receiptImageBtn');
  const textBtn=document.getElementById('receiptTextBtn');
  const textSection=document.getElementById('receiptTextSection');
  if(imgBtn){imgBtn.disabled=false;imgBtn.style.opacity='';imgBtn.textContent='📷 重新选择截图';}
  if(textBtn){textBtn.disabled=false;textBtn.style.opacity='';}
  if(textSection) textSection.style.opacity='';
}

// 公共填入逻辑
function _fillTxnForm(d){
  if(d.fund_code) document.getElementById('txnCode').value=d.fund_code;
  if(d.amount) document.getElementById('txnAmt').value=d.amount;
  if(d.nav) document.getElementById('txnPrice').value=d.nav;
  if(d.shares) document.getElementById('txnShares').value=d.shares;
  if(d.fund_name) document.getElementById('txnName').value=d.fund_name;
  if(d.date) document.getElementById('txnNote').value=d.date;
  const codeIn=document.getElementById('txnCode');
  if(codeIn) codeIn.dispatchEvent(new Event('input',{bubbles:true}));
}

window.doParseReceiptInTxn=async function(){
const text=document.getElementById('receiptTextTxn')?.value?.trim();
if(!text){alert('请先粘贴凭证文字或上传截图');return;}
const result=document.getElementById('receiptResultTxn');
result.innerHTML='<span style="color:var(--accent)">识别中...</span>';
try{
  const r=await fetch(API_BASE+'/fund/parse-receipt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text}),signal:AbortSignal.timeout(15000)});
  const d=await r.json();
  if(!d.ok){result.innerHTML='<span style="color:var(--red)">❌ '+d.reason+'</span>';return;}
  _fillTxnForm(d);
  result.innerHTML='<span style="color:var(--color-bull,#10B981)">✅ 识别成功！已自动填入</span>';
  setTimeout(()=>document.getElementById('receiptOverlayTxn')?.remove(),1000);
}catch(e){
  result.innerHTML='<span style="color:var(--red)">❌ 识别失败: '+e.message+'</span>';
}};

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
renderPortfolio();
// v9.5.21: 录入后自动校验净值合理性（异步，不阻塞）
if(type==='BUY' && (window._portfolioTab||'fund')==='fund'){
  setTimeout(()=>_validateTxnNav(code,price),300);
}
}

// v9.5.21: 录入后净值校验 — 防止把"累计净值"当"单位净值"导致显示巨亏
async function _validateTxnNav(code,inputPrice){
  if(!code||!inputPrice||!API_AVAILABLE)return;
  if(!/^\d{6}$/.test(code))return; // 只校验 6 位数字基金代码
  try{
    const r=await fetch(`${API_BASE}/fund-holdings/realtime/${code}`,{signal:AbortSignal.timeout(8000)});
    if(!r.ok)return;
    const d=await r.json();
    const currentNav = d.nav || d.estNav;
    if(!currentNav||currentNav<=0)return;
    const ratio = inputPrice / currentNav;
    // 偏离阈值：成本是当前净值的 2 倍以上 → 高度怀疑录入累计净值；0.4 倍以下 → 可能录错单位
    if(ratio >= 2.0){
      const pct = ((inputPrice-currentNav)/currentNav*100).toFixed(0);
      const msg = `⚠️ 录入提醒\n\n你录入的成本净值 ¥${inputPrice.toFixed(4)}\n当前最新净值 ¥${currentNav.toFixed(4)}\n相差约 ${pct}%\n\n💡 常见原因：把"累计净值"当成"单位净值"录入了\n（累计净值含历年分红，比单位净值高很多）\n\n👉 请打开支付宝/天天基金，查看该基金的「持有成本价」一栏\n如果不一致，可在持仓页删除这笔交易后重录\n\n如果你确实是高位买入的，忽略此提醒即可。`;
      alert(msg);
    } else if(ratio>0 && ratio<=0.4){
      const pct = ((currentNav-inputPrice)/currentNav*100).toFixed(0);
      alert(`⚠️ 录入提醒\n\n你录入的成本净值 ¥${inputPrice.toFixed(4)}\n当前最新净值 ¥${currentNav.toFixed(4)}\n你的成本比当前低 ${pct}%\n\n💡 请确认：\n• 是不是早年低位买入？\n• 还是录入时少打了小数点？（如把 2.62 录成 0.262）\n\n如果是早年买入，忽略此提醒即可。`);
    }
  }catch(e){console.warn('[validateTxnNav]',e)}
}

function deleteFundTxns(code){const txns=loadTxns().filter(t=>t.code!==code);saveTxns(txns);
if(API_AVAILABLE)fetch(API_BASE+'/portfolio/transaction/delete',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({userId:getUserId(),code})}).catch(()=>{});
renderPortfolio()}

// v9.5.21: 全量扫描异常成本基础持仓（疑似累计净值/单位净值混淆）
// 触发条件：当前 pnlPct < -50% 且 cost / currentNav > 2 → 弹窗提示并提供"删除重录"快捷
function _scanAbnormalCostBasis(){
  const pnlMap = window._holdingsPnl || {};
  const txns = loadTxns();
  const holdings = calcHoldingsFromTxns(txns);
  const suspects = [];
  for(const h of holdings){
    const code = (h.code||'').replace(/^(sh|sz)/i,'');
    if(!/^\d{6}$/.test(code)) continue;
    const pnlData = pnlMap[code];
    if(!pnlData || !pnlData.nav) continue;
    const cost = h.avgPrice;
    const nav = pnlData.nav;
    if(!cost || !nav) continue;
    const ratio = cost / nav;
    const pnlPct = pnlData.pnlPct || 0;
    // 巨亏 + 成本远高于当前净值 → 高度疑似累计净值录入
    if(pnlPct < -50 && ratio >= 2.0){
      suspects.push({
        code, name: h.name || code,
        cost: cost.toFixed(4),
        currentNav: nav.toFixed(4),
        pnlPct: pnlPct.toFixed(2),
        ratio: ratio.toFixed(1),
      });
    }
  }
  if(suspects.length === 0) return;
  // 避免同一会话反复弹（按 code 列表 hash 标记）
  const sessionKey = '_costBasisAlertShown_' + suspects.map(s=>s.code).sort().join(',');
  if(sessionStorage.getItem(sessionKey)) return;
  sessionStorage.setItem(sessionKey, '1');

  const lines = suspects.map(s =>
    `• ${s.name}（${s.code}）\n  成本 ¥${s.cost} → 当前 ¥${s.currentNav}（${s.pnlPct}%）`
  ).join('\n\n');

  setTimeout(()=>{
    const ok = confirm(`⚠️ 发现 ${suspects.length} 笔疑似异常的持仓\n\n${lines}\n\n💡 常见原因：把"累计净值"当成本录入了\n（累计净值含历年分红，比单位净值高）\n\n📌 建议核对支付宝/天天基金里的「持有成本价」\n\n点击「确定」查看可疑持仓 / 「取消」忽略本次提醒`);
    if(ok && suspects[0]){
      // 跳到首只可疑基金的详情，方便用户删除/重录
      if(typeof showFundDetail==='function') showFundDetail(suspects[0].code);
    }
  }, 800);
}
window._scanAbnormalCostBasis = _scanAbnormalCostBasis;

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

