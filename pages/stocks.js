// ---- 📈 持仓盯盘页（股票+基金统一） ----
let _stockScanData=null;let _fundScanData=null;let _holdingsSubTab='stock';let _overviewData=null;
async function renderStocks(){currentPage='stocks';renderNav();
$('#app').innerHTML=`<div class="insight-page fade-up"><div id="overviewHero"><div style="text-align:center;padding:20px"><div class="loading-spinner"></div></div></div><div id="behaviorGuardBar"></div><div style="display:flex;gap:8px;margin-bottom:16px"><button id="subTabStock" class="action-btn ${_holdingsSubTab==='stock'?'primary':'secondary'}" onclick="_holdingsSubTab='stock';renderStocksContent()" style="flex:1">📊 股票</button><button id="subTabFund" class="action-btn ${_holdingsSubTab==='fund'?'primary':'secondary'}" onclick="_holdingsSubTab='fund';renderFundsContent()" style="flex:1">💰 基金</button></div><div id="holdingsContent"><div style="text-align:center;padding:40px"><div class="loading-spinner"></div><div style="color:var(--text2);margin-top:12px">加载持仓数据...</div></div></div></div>`;
if(!API_AVAILABLE){document.getElementById('holdingsContent').innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">后端离线</div>';return}
// 加载总览 + 子页面 + 行为风控状态并行
loadOverviewHero();loadBehaviorGuardBar();
if(_holdingsSubTab==='fund')renderFundsContent();else renderStocksContent()}

async function loadOverviewHero(){
try{const ov=await fetch(API_BASE+'/portfolio/overview?'+getProfileParam()).then(r=>r.json());_overviewData=ov;
const el=document.getElementById('overviewHero');if(!el)return;
const pnlC=ov.totalPnl>=0?'var(--green)':'var(--red)';
const hC=ov.healthScore>=80?'var(--green)':ov.healthScore>=60?'#F59E0B':'var(--red)';
// 环形图 SVG（股/债/现 三段）
const eq=ov.allocation?.equity||0;const bd=ov.allocation?.bond||0;const ca=ov.allocation?.cash||0;
const r=36;const c=2*Math.PI*r;
const eqLen=c*eq/100;const bdLen=c*bd/100;const caLen=c*(ca||100-eq-bd)/100;
const eqOff=0;const bdOff=-(eqLen);const caOff=-(eqLen+bdLen);
const ringSvg=ov.totalMarketValue>0?`<svg width="90" height="90" viewBox="0 0 90 90" style="transform:rotate(-90deg)">
<circle cx="45" cy="45" r="${r}" fill="none" stroke="var(--bg3)" stroke-width="10"/>
<circle cx="45" cy="45" r="${r}" fill="none" stroke="var(--accent)" stroke-width="10" stroke-dasharray="${eqLen} ${c-eqLen}" stroke-dashoffset="${eqOff}"/>
<circle cx="45" cy="45" r="${r}" fill="none" stroke="#60A5FA" stroke-width="10" stroke-dasharray="${bdLen} ${c-bdLen}" stroke-dashoffset="${bdOff}"/>
<circle cx="45" cy="45" r="${r}" fill="none" stroke="#A78BFA" stroke-width="10" stroke-dasharray="${caLen} ${c-caLen}" stroke-dashoffset="${caOff}"/>
</svg>`:'';
const legendHtml=ov.totalMarketValue>0?`<div style="display:flex;gap:12px;justify-content:center;margin-top:8px;font-size:11px;color:var(--text2)">
<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--accent);margin-right:3px"></span>股票 ${eq}%</span>
<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#60A5FA;margin-right:3px"></span>债券 ${bd}%</span>
<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#A78BFA;margin-right:3px"></span>现金 ${ca}%</span>
</div>`:'';
const devHtml=ov.totalMarketValue>0&&ov.deviation?Object.entries({equity:'股票',bond:'债券',cash:'现金'}).map(([k,label])=>{
const d=ov.deviation[k]||0;const dc=Math.abs(d)>15?'var(--red)':Math.abs(d)>5?'#F59E0B':'var(--green)';
return`<span style="font-size:11px;color:${dc}">${label}${d>0?'+':''}${d}%</span>`}).join(' · '):'';
el.innerHTML=`<div class="pnl-hero" style="position:relative">
<div style="display:flex;align-items:center;gap:16px;justify-content:center">
<div>${ringSvg}</div>
<div><div class="pnl-label">总持仓资产 <span style="font-size:10px;color:var(--text2);font-weight:400">仅股票+基金</span></div>
<div class="pnl-total-value">¥${ov.totalMarketValue>0?ov.totalMarketValue.toLocaleString():'0'}</div>
${ov.totalCost>0?`<div class="pnl-change ${ov.totalPnl>=0?'pos':'neg'}" style="color:${pnlC}">盈亏 ${ov.totalPnl>=0?'+':''}¥${ov.totalPnl.toFixed(0)} (${ov.totalPnlPct>=0?'+':''}${ov.totalPnlPct.toFixed(1)}%)</div>`:''}</div></div>
${legendHtml}
${devHtml?`<div style="text-align:center;margin-top:4px">偏离: ${devHtml}</div>`:''}
<div style="display:flex;justify-content:center;gap:16px;margin-top:10px;font-size:12px">
<span>📊 股票 ${ov.stockCount}只</span><span>💰 基金 ${ov.fundCount}只</span>
<span style="color:${hC};font-weight:600">${ov.healthGrade} ${ov.healthScore}分</span>
</div>
${ov.healthIssues&&ov.healthIssues.length?`<div style="margin-top:8px;padding:8px 12px;background:rgba(245,158,11,.08);border-radius:8px;font-size:11px;color:#F59E0B">${ov.healthIssues.join(' · ')}</div>`:''}
</div>`;
}catch(e){console.warn('Overview load error:',e)}}

async function loadBehaviorGuardBar(){
const el=document.getElementById('behaviorGuardBar');if(!el)return;
try{const r=await fetch(API_BASE+'/behavior/guard-status?'+getProfileParam(),{signal:AbortSignal.timeout(5000)});
if(!r.ok){el.innerHTML='';return}
const d=await r.json();
const icon=d.enabled?'🟢':'🔴';const color=d.enabled?'rgba(16,185,129,.1)':'rgba(239,68,68,.1)';
const border=d.enabled?'rgba(16,185,129,.3)':'rgba(239,68,68,.3)';
const countBadge=d.active_count>0?` · <span style="color:#F59E0B;font-weight:600">${d.active_count} 项干预中</span>`:'';
el.innerHTML=`<div onclick="showBehaviorGuardPanel()" style="background:${color};border:1px solid ${border};border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:12px;cursor:pointer;display:flex;align-items:center;justify-content:space-between"><span>${icon} ${d.tip}${countBadge}</span><span style="color:var(--text2);font-size:11px">设置 ›</span></div>`;
}catch(e){el.innerHTML=''}}

function showBehaviorGuardPanel(){
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div><div class="modal-title">🛡️ 行为风控</div><div class="modal-subtitle">检测交易偏差，提供冷静期提醒</div><div id="guardContent" style="padding:12px 0"><div class="loading-spinner"></div></div></div>`;
document.body.appendChild(o);_loadGuardPanel()}

async function _loadGuardPanel(){
const el=document.getElementById('guardContent');if(!el)return;
try{const[statusRes,intRes]=await Promise.all([
fetch(API_BASE+'/behavior/guard-status?'+getProfileParam(),{signal:AbortSignal.timeout(5000)}),
fetch(API_BASE+'/behavior/active-interventions?'+getProfileParam(),{signal:AbortSignal.timeout(5000)})]);
const status=await statusRes.json();const intData=await intRes.json();
const toggleColor=status.enabled?'var(--green)':'var(--red)';
const toggleText=status.enabled?'已启用':'已关闭';
const toggleAction=status.enabled?'false':'true';
let intHtml='<div style="font-size:12px;color:var(--text2);padding:12px 0">暂无活跃干预</div>';
if(intData.interventions&&intData.interventions.length>0){
intHtml=intData.interventions.map((inv,i)=>`<div style="background:var(--bg2,#1e293b);border-radius:8px;padding:10px;margin-bottom:8px"><div style="display:flex;justify-content:space-between;align-items:center"><span style="font-size:13px;font-weight:600">${inv.pattern}</span><span style="font-size:11px;color:var(--text2)">${inv.status}</span></div><div style="font-size:12px;color:var(--text2);margin-top:4px">${inv.message}</div><div style="font-size:11px;color:var(--text2);margin-top:4px">触发: ${inv.triggered_at?.slice(0,16)||'--'}${inv.expires_at?' · 过期: '+inv.expires_at.slice(0,16):''}</div>${inv.status==='active'?`<button class="action-btn secondary" onclick="_overrideIntervention(${i})" style="margin-top:6px;padding:3px 10px;font-size:11px">确认覆盖</button>`:''}</div>`).join('')}
el.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--bg3,#334155)"><span style="font-size:14px">总开关</span><button class="action-btn ${status.enabled?'primary':'secondary'}" onclick="_toggleGuard(${toggleAction})" style="padding:4px 12px;font-size:12px"><span style="color:${toggleColor}">${toggleText}</span></button></div><div style="margin-top:12px"><div style="font-size:13px;font-weight:600;margin-bottom:8px">活跃干预 (${intData.total})</div>${intHtml}</div>`;
}catch(e){el.innerHTML='<div style="color:var(--red);font-size:12px">加载失败</div>'}}

async function _toggleGuard(enabled){
try{await fetch(API_BASE+'/behavior/guard-toggle?'+getProfileParam(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled,reason:'用户手动切换'})});
_loadGuardPanel();loadBehaviorGuardBar()}catch(e){alert('切换失败')}}

async function _overrideIntervention(idx){
if(!confirm('确认覆盖此干预？覆盖后该限制立即解除。'))return;
try{await fetch(API_BASE+'/behavior/override/'+idx+'?'+getProfileParam(),{method:'POST'});
_loadGuardPanel();loadBehaviorGuardBar()}catch(e){alert('覆盖失败')}}

async function renderStocksContent(){
_holdingsSubTab='stock';
document.getElementById('subTabStock')?.classList.replace('secondary','primary');
document.getElementById('subTabFund')?.classList.replace('primary','secondary');
const el=document.getElementById('holdingsContent');
el.innerHTML='<div style="text-align:center;padding:40px"><div class="loading-spinner"></div><div style="color:var(--text2);margin-top:12px">加载股票持仓...</div></div>';
try{const[hRes,scanRes]=await Promise.all([fetch(API_BASE+'/stock-holdings?'+getProfileParam()).then(r=>r.json()),fetch(API_BASE+'/stock-holdings/scan?'+getProfileParam()).then(r=>r.json())]);
_stockScanData=scanRes;const holdings=scanRes.holdings||[];const signals=scanRes.signals||[];const discipline=scanRes.discipline||[];
const el=document.getElementById('holdingsContent');if(!el)return;
// ── 风险纪律卡（精简版，一眼扫完）──
let riskCardHtml='';
const riskIssues=[];
discipline.forEach(d=>{if(d.level==='danger'||d.level==='warning')riskIssues.push(d.msg)});
if(_overviewData&&_overviewData.healthIssues)_overviewData.healthIssues.forEach(h=>{if(!riskIssues.includes(h))riskIssues.push(h)});
if(riskIssues.length>0){
riskCardHtml=`<div style="background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:12px;padding:12px 14px;margin-bottom:12px"><div style="font-size:13px;font-weight:700;margin-bottom:6px">🛡️ 风险纪律</div>${riskIssues.slice(0,4).map(i=>`<div style="font-size:12px;line-height:1.8;color:#92400E">• ${i}</div>`).join('')}</div>`
}else if(holdings.length>0){
riskCardHtml=`<div style="background:rgba(16,185,129,.06);border:1px solid rgba(16,185,129,.2);border-radius:12px;padding:10px 14px;margin-bottom:12px;font-size:12px;color:var(--green)">🛡️ 风险纪律：一切正常，没有需要注意的问题 ✅</div>`}
// 异动信号汇总
let signalHtml='';if(signals.length>0){const dangerS=signals.filter(s=>s.level==='danger'||s.level==='warning');const opS=signals.filter(s=>s.level==='opportunity');
signalHtml=`<div class="dashboard-card" style="border-left:3px solid ${dangerS.length?'var(--red)':'var(--green)'}"><div class="dashboard-card-title">⚡ 盯盘信号 (${signals.length})</div>${signals.map(s=>{const c=s.level==='danger'?'var(--red)':s.level==='warning'?'#F59E0B':s.level==='opportunity'?'var(--green)':'var(--text2)';return`<div style="padding:6px 0;font-size:13px;border-bottom:1px solid var(--bg3);color:${c}">${s.msg}</div>`}).join('')}</div>`}
// 纪律检查面板
let disciplineHtml='';if(discipline.length>0){
disciplineHtml=`<div class="dashboard-card" style="border-left:3px solid #F59E0B;margin-top:8px"><div class="dashboard-card-title">📏 纪律检查 (${discipline.length})</div>${discipline.map(d=>{const c=d.level==='warning'?'#F59E0B':d.level==='danger'?'var(--red)':'var(--text2)';return`<div style="padding:6px 0;font-size:13px;border-bottom:1px solid var(--bg3);color:${c}">${d.msg}</div>`}).join('')}</div>`}
// 持仓列表
let listHtml='';if(holdings.length===0){listHtml=`<div style="text-align:center;padding:40px;color:var(--text2)"><div style="font-size:48px;margin-bottom:16px">📈</div><div style="font-size:16px;margin-bottom:8px">还没有持仓股票</div><div style="font-size:13px">点击下方按钮添加你的第一只股票</div></div>`}else{
listHtml=holdings.map(h=>{const pctC=h.changePct>=0?'var(--green)':'var(--red)';const pnlC=(h.pnlPct||0)>=0?'var(--green)':'var(--red)';const weightTag=h.weight?` · 仓位${h.weight}%`:'';const industryTag=h.industry&&h.industry!=='未知'?` · ${h.industry}`:'';
// V7.2 FIX: 数据新鲜度标签
const freshTag = h.is_snapshot
  ? `<span class="data-stale-badge" title="非交易日/盘后数据">📅 ${h.data_date||'收盘快照'}</span>`
  : (h.price!=null ? `<span class="data-fresh-badge" title="实时行情">⚡ 实时</span>` : '');
return`<div class="holding-card" onclick="showStockDetail('${h.code}')"><div class="holding-top"><div class="holding-info"><div class="holding-name">${h.name||h.code}${freshTag}</div><div class="holding-meta">${h.code}${industryTag}${weightTag}</div></div><div class="holding-amount"><button class="action-btn secondary" onclick="event.stopPropagation();showFundChart('${h.code}')" style="padding:3px 8px;font-size:11px;margin-right:6px">K线</button><div><div class="holding-money" style="color:${pctC}">${h.price?'¥'+h.price.toFixed(2):'--'}</div><div class="holding-pct" style="color:${pctC}">${h.changePct!=null?(h.changePct>=0?'+':'')+h.changePct.toFixed(2)+'%':'--'}</div></div></div></div>${h.costPrice&&h.shares?`<div class="holding-pnl-row"><div class="holding-pnl-item"><div class="holding-pnl-label">持仓市值</div><div class="holding-pnl-val">¥${(h.marketValue||0).toLocaleString()}</div></div><div class="holding-pnl-item"><div class="holding-pnl-label">盈亏</div><div class="holding-pnl-val ${(h.pnlPct||0)>=0?'pos':'neg'}" style="color:${pnlC}">${h.pnl!=null?((h.pnl>=0?'+':'')+h.pnl.toFixed(0)):''} ${h.pnlPct!=null?'('+((h.pnlPct>=0?'+':'')+h.pnlPct.toFixed(1))+'%)':''}</div></div><div class="holding-pnl-item"><div class="holding-pnl-label">成本价</div><div class="holding-pnl-val">¥${h.costPrice}</div></div></div>`:''}</div>`}).join('')}
// 汇总
let totalMV=holdings.reduce((s,h)=>s+(h.marketValue||0),0);let totalPnl=holdings.reduce((s,h)=>s+(h.pnl||0),0);
let heroHtml='';if(holdings.length>0&&totalMV>0){const pnlC=totalPnl>=0?'var(--green)':'var(--red)';
heroHtml=`<div class="pnl-hero"><div class="pnl-label">股票持仓总市值</div><div class="pnl-total-value">¥${totalMV.toLocaleString()}</div><div class="pnl-change ${totalPnl>=0?'pos':'neg'}" style="color:${pnlC}">${totalPnl>=0?'+':''}${totalPnl.toFixed(0)}</div><div class="pnl-sub">${holdings.length} 只股票 · ${scanRes.scannedAt?'更新于 '+scanRes.scannedAt.slice(11,16):''}</div></div>`}
el.innerHTML=heroHtml+riskCardHtml+signalHtml+disciplineHtml+listHtml+`<div style="margin-top:16px"><button class="action-btn primary" onclick="showAddStockModal()" style="width:100%">➕ 添加股票</button></div><div style="margin-top:8px"><button class="action-btn secondary" onclick="renderStocksContent()" style="width:100%">🔄 刷新行情</button></div>`;
}catch(e){console.error('Stock load error:',e);document.getElementById('holdingsContent').innerHTML='<div style="text-align:center;padding:40px;color:var(--red)">加载失败: '+e.message+'</div>'}}

function showAddStockModal(){const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
overlay.innerHTML=`<div class="modal-sheet"><div class="modal-handle"></div><div class="modal-title">➕ 添加股票</div><div class="modal-subtitle">输入A股代码（如 600519、002594）</div><div class="form-row"><div class="form-label">股票代码 *</div><input class="form-input" id="addStockCode" placeholder="600519" inputmode="numeric"></div><div class="form-row"><div class="form-label">成本价（选填）</div><input class="form-input" id="addStockCost" type="number" placeholder="0" step="0.01" inputmode="decimal"></div><div class="form-row"><div class="form-label">持有股数（选填）</div><input class="form-input" id="addStockShares" type="number" placeholder="0" inputmode="numeric"></div><div class="form-row"><div class="form-label">备注（选填）</div><input class="form-input" id="addStockNote" placeholder=""></div><button class="form-submit" onclick="doAddStock()">添加</button></div>`;
document.body.appendChild(overlay)}

async function doAddStock(){const code=$('#addStockCode')?.value?.trim();if(!code){alert('请输入股票代码');return}
const cost=parseFloat($('#addStockCost')?.value)||0;const shares=parseInt($('#addStockShares')?.value)||0;const note=$('#addStockNote')?.value||'';
try{const r=await fetch(API_BASE+'/stock-holdings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,costPrice:cost,shares,note,userId:getProfileId()})});
const d=await r.json();if(d.error){alert(d.error);return}
// 显示纪律检查警告
if(d.warnings&&d.warnings.length>0){const warnMsg=d.warnings.map(w=>w.msg).join('\n');setTimeout(()=>alert('⚠️ 纪律提醒\n\n'+warnMsg),200)}
document.querySelector('.modal-overlay')?.remove();renderStocksContent()}catch(e){alert('添加失败: '+e.message)}}

function _calcStockAdvice(pnlPct, rsi14, changePct){
// 规则引擎：股票操作建议（加仓/持有/观望/减仓/止损）
const p = pnlPct ?? 0;
const rsi = rsi14 || 50;
const chg = changePct || 0;

let level, action, hint, tag;

if(p <= -15){
  level='red'; action='⛔ 考虑止损'; tag='止损线';
  hint='亏损超过 -15%（你设置的止损线），建议复核持仓逻辑，若基本面未变化可继续持有，否则应果断止损';
} else if(p <= -8){
  level='yellow'; action='🟡 谨慎持有'; tag='深度回调';
  hint='中度亏损，建议暂缓加仓，等待企稳信号（如 RSI < 35 或放量止跌）再考虑补仓';
} else if(p <= 0){
  level='yellow'; action='🟡 持有观望'; tag='小幅回撤';
  hint='轻微亏损，不急于操作，可设置价格提醒，跌破关键支撑再止损';
} else if(p <= 20){
  level='green'; action='✅ 继续持有'; tag='盈利中';
  hint='持仓盈利，无需操作，按原有止盈目标持有';
} else if(p <= 40){
  level='yellow'; action='🔆 考虑减仓'; tag='高盈利';
  hint='盈利较高，可考虑减仓 1/3 锁定收益，剩余仓位继续持有博更高收益';
} else {
  level='red'; action='⚠️ 接近止盈线'; tag='止盈线';
  hint='盈利超过 +40%（接近止盈 +50%），建议分批减仓，至少兑现一半利润';
}

// RSI 叠加修正
const extras = [];
if(rsi > 75 && level !== 'red'){
  extras.push(`RSI ${rsi} 进入超买区（>75），短期涨幅透支，可减少加仓频率`);
} else if(rsi < 30 && (level === 'yellow' || level === 'green')){
  extras.push(`RSI ${rsi} 进入超卖区（<30），短期下跌过度，可少量补仓`);
}
if(chg < -5){
  extras.push(`今日大跌 ${chg.toFixed(1)}%，注意确认是否有利空消息，非系统性下跌才适合补仓`);
}

return {level, action, hint, tag, extras};
}

async function showStockDetail(code){
let h=(_stockScanData?.holdings||[]).find(x=>x.code===code);
if(!h){
  try{
    const r=await fetch(API_BASE+'/stock-holdings/scan?'+getProfileParam(),{signal:AbortSignal.timeout(10000)});
    if(r.ok){const d=await r.json();_stockScanData=d;h=d.holdings?.find(x=>x.code===code);}
  }catch(e){}
}
if(!h)return;
const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
const ind=h.indicators||{};const sigs=h.signals||[];

// 操作建议
const pnlPct = h.pnlPct ?? null;
const adv = pnlPct != null ? _calcStockAdvice(pnlPct, ind.rsi14, h.changePct) : null;
const advColor = adv?.level==='red'?'var(--red)':adv?.level==='yellow'?'#F59E0B':'var(--green)';
const advBg = adv?.level==='red'?'rgba(239,68,68,.06)':adv?.level==='yellow'?'rgba(245,158,11,.06)':'rgba(16,185,129,.06)';
const advBorder = adv?.level==='red'?'rgba(239,68,68,.2)':adv?.level==='yellow'?'rgba(245,158,11,.2)':'rgba(16,185,129,.2)';
const advHtml = adv ? `
<div style="margin-top:16px;padding:14px;background:${advBg};border:1px solid ${advBorder};border-radius:12px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
    <div style="font-size:13px;font-weight:700">📋 持仓建议</div>
    <div style="font-size:10px;color:var(--text2)">规则引擎 · 非投资建议</div>
  </div>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
    <div style="font-size:18px;font-weight:800;color:${advColor}">${adv.action}</div>
    <div style="padding:2px 10px;background:${advColor}20;border-radius:20px;font-size:11px;color:${advColor};font-weight:600">${adv.tag}</div>
  </div>
  <div style="font-size:12px;color:var(--text2);line-height:1.7">${adv.hint}</div>
  ${adv.extras.length ? '<div style="margin-top:8px">'+adv.extras.map(e=>`<div style="font-size:11px;color:#F59E0B;margin-top:4px">⚡ ${e}</div>`).join('')+'</div>' : ''}
  <div style="font-size:10px;color:var(--text3,#475569);margin-top:8px;border-top:1px solid rgba(148,163,184,.1);padding-top:6px">当前盈亏 ${pnlPct>=0?'+':''}${pnlPct.toFixed(1)}% · 止损 -15% · 止盈 +50%</div>
</div>` : '';

overlay.innerHTML=`<div class="modal-sheet" style="max-height:90vh;overflow-y:auto"><div class="modal-handle"></div>
<div class="modal-title">${h.name||h.code}</div>
<div class="modal-subtitle">${h.code} · ${h.changePct!=null?(h.changePct>=0?'+':'')+h.changePct.toFixed(2)+'%':'--'}</div>
<div class="modal-stat-grid">
<div class="modal-stat"><div class="modal-stat-label">当前价</div><div class="modal-stat-value">${h.price?'¥'+h.price.toFixed(2):'--'}</div></div>
<div class="modal-stat"><div class="modal-stat-label">RSI14</div><div class="modal-stat-value" style="color:${ind.rsi14>70?'var(--red)':ind.rsi14<30?'var(--green)':'var(--text)'}">${ind.rsi14||'--'}</div></div>
<div class="modal-stat"><div class="modal-stat-label">MACD</div><div class="modal-stat-value">${ind.macd_trend||'--'}</div></div>
<div class="modal-stat"><div class="modal-stat-label">量比</div><div class="modal-stat-value" style="color:${ind.volume_ratio>2?'var(--red)':'var(--text)'}">${ind.volume_ratio||'--'}</div></div>
</div>
${advHtml}
${sigs.length?'<div style="margin-top:16px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">📡 信号</div>'+sigs.map(s=>`<div style="padding:6px 0;font-size:13px;border-bottom:1px solid var(--bg3)">${s.msg}</div>`).join('')+'</div>':''}
<div id="stockIntel_${code}" style="margin-top:16px"><div style="text-align:center;padding:12px;color:var(--text2);font-size:12px">📰 加载个股情报...</div></div>
<div style="margin-top:16px;display:flex;gap:8px"><button class="action-btn secondary" style="flex:1" onclick="if(confirm('删除 ${h.name}？'))deleteStock('${h.code}')">🗑️ 删除</button></div>
</div>`;
document.body.appendChild(overlay);
// 异步加载持仓关联智能
if(API_AVAILABLE){fetch(API_BASE+'/holding-intelligence/'+code+'?'+getProfileParam(),{signal:AbortSignal.timeout(15000)}).then(r=>r.json()).then(d=>{
const el=document.getElementById('stockIntel_'+code);if(!el)return;
let h2='';
if(d.news&&d.news.length){h2+=`<div style="font-size:13px;font-weight:700;margin-bottom:6px">📰 个股新闻</div>`;h2+=d.news.slice(0,3).map(n=>`<div style="padding:4px 0;font-size:12px;border-bottom:1px solid var(--bg3)">${n.title}</div>`).join('')}
if(d.fund_flow){const ff=d.fund_flow;h2+=`<div style="font-size:13px;font-weight:700;margin-top:10px;margin-bottom:4px">💰 主力资金</div><div style="font-size:12px;color:${ff.net_amount>0?'var(--green)':'var(--red)'}">今日主力净${ff.net_amount>0?'流入':'流出'} ${Math.abs(ff.net_amount||0).toFixed(0)}万</div>`}
if(d.industry){h2+=`<div style="font-size:12px;color:var(--text2);margin-top:8px">🏭 所属行业：${d.industry}</div>`}
if(d.unlock_risk){h2+=`<div style="font-size:12px;color:var(--red);margin-top:6px;padding:6px;background:rgba(239,68,68,.06);border-radius:6px">🔓 解禁预警：${d.unlock_risk}</div>`}
el.innerHTML=h2||'<div style="font-size:12px;color:var(--text2)">暂无关联情报</div>'
}).catch(()=>{const el=document.getElementById('stockIntel_'+code);if(el)el.innerHTML=''})}}

async function deleteStock(code){try{await fetch(API_BASE+'/stock-holdings/'+code+'?'+getProfileParam(),{method:'DELETE'});document.querySelector('.modal-overlay')?.remove();renderStocksContent()}catch(e){alert('删除失败')}}

// ---- 💰 基金持仓板块 ----
async function renderFundsContent(){
_holdingsSubTab='fund';
document.getElementById('subTabFund')?.classList.replace('secondary','primary');
document.getElementById('subTabStock')?.classList.replace('primary','secondary');
const el=document.getElementById('holdingsContent');
el.innerHTML='<div style="text-align:center;padding:40px"><div class="loading-spinner"></div><div style="color:var(--text2);margin-top:12px">加载基金持仓...</div></div>';
try{const[hRes,scanRes]=await Promise.all([fetch(API_BASE+'/fund-holdings?'+getProfileParam()).then(r=>r.json()),fetch(API_BASE+'/fund-holdings/scan?'+getProfileParam()).then(r=>r.json())]);
_fundScanData=scanRes;const holdings=scanRes.holdings||[];
const fromCache=scanRes.from_cache;
// 信号汇总
let signalHtml='';const alerts=scanRes.alerts||[];
if(alerts.length){signalHtml='<div class="signal-summary"><div style="font-size:13px;font-weight:700;margin-bottom:8px">⚡ 基金异动信号</div>'+alerts.map(a=>{const bg=a.level==='warning'?'rgba(239,68,68,.08)':'rgba(34,197,94,.08)';return`<div style="background:${bg};border-radius:8px;padding:8px 10px;margin-bottom:4px;font-size:12px">${a.fund||''} ${a.msg}</div>`}).join('')+'</div>'}
// 列表
let listHtml='';
if(!holdings.length){listHtml='<div style="text-align:center;padding:40px;color:var(--text2)"><div style="font-size:48px;margin-bottom:16px">💰</div><div style="font-size:16px;margin-bottom:8px">还没有基金持仓</div><div style="font-size:13px">点击底部"+ 新交易"添加你的第一只基金<br><span style="opacity:0.7">支持粘贴支付宝/天天基金凭证自动识别</span></div></div>'}
else{listHtml=holdings.map(h=>{const rt=h.realtime||{};const risk=h.risk||{};
const estRate=rt.estRate;const rateColor=estRate==null?'var(--text2)':estRate>=0?'var(--green)':'var(--red)';
const pnlColor=h.pnlPct==null?'var(--text2)':h.pnlPct>=0?'var(--green)':'var(--red)';
const ddStr=risk.maxDrawdown!=null?(risk.maxDrawdown*100).toFixed(1)+'%':'--';
const ddColor=risk.maxDrawdown!=null&&risk.maxDrawdown>0.03?'var(--red)':'var(--text2)';
return`<div class="stock-card" onclick="showFundHoldingDetail('${h.code}')" style="cursor:pointer;border:1px solid rgba(148,163,184,.08)">
<div style="display:flex;justify-content:space-between;align-items:center">
<div><div style="font-size:14px;font-weight:700">${h.name||h.code}</div><div style="font-size:11px;color:var(--text2)">${h.code}</div></div>
<div style="display:flex;align-items:center;gap:8px"><button class="action-btn secondary" onclick="event.stopPropagation();showFundChart('${h.code}')" style="padding:3px 8px;font-size:11px">K线</button><div style="text-align:right"><div style="font-size:14px;font-weight:600">${rt.estNav||'--'}</div>
<div style="font-size:12px;color:${rateColor}">${estRate!=null?(estRate>=0?'+':'')+estRate.toFixed(2)+'%':'--'}</div></div></div></div>
<div style="display:flex;gap:12px;margin-top:8px;font-size:11px;color:var(--text2)">
<span>净值 ${rt.nav||'--'}</span><span style="color:${ddColor}">回撤 ${ddStr}</span>
<span>连跌 ${risk.downDays||0}天</span>
${h.pnlPct!=null?`<span style="color:${pnlColor};font-weight:600">盈亏 ${h.pnlPct>=0?'+':''}${h.pnlPct.toFixed(1)}%</span>`:''}
</div>
${h.alerts&&h.alerts.length?'<div style="margin-top:6px;font-size:11px;color:var(--accent)">'+h.alerts.map(a=>a.msg).join(' · ')+'</div>':''}
<div style="margin-top:8px;display:flex;align-items:center;justify-content:space-between;padding-top:6px;border-top:1px solid rgba(148,163,184,.06)">
<span style="font-size:11px;color:var(--accent)">📋 点击查看定投建议 ›</span>
${h.pnlPct!=null?`<span style="font-size:11px;padding:2px 8px;background:${h.pnlPct>=-2&&h.pnlPct<=15?'rgba(16,185,129,.08)':h.pnlPct>15?'rgba(245,158,11,.08)':'rgba(239,68,68,.08)'};border-radius:4px;color:${h.pnlPct>=-2&&h.pnlPct<=15?'var(--green)':h.pnlPct>15?'#F59E0B':'var(--red)'}">${h.pnlPct>15?'考虑减半定投':h.pnlPct<=-8?'加倍定投时机':'正常定投'}</span>`:''}
</div></div>`}).join('')}
// Hero
let heroHtml='';const totalPnl=holdings.reduce((s,h)=>s+(h.pnl||0),0);
if(holdings.length>0){heroHtml=`<div class="pnl-hero"><div class="pnl-label">基金持仓 ${holdings.length} 只${fromCache?' <span style="font-size:10px;opacity:0.6">· 缓存</span>':''}</div><div class="pnl-change ${totalPnl>=0?'pos':'neg'}" style="color:${totalPnl>=0?'var(--green)':'var(--red)'}">总盈亏 ${totalPnl>=0?'+':''}¥${totalPnl.toFixed(0)}</div><div class="pnl-sub">${scanRes.scannedAt?'更新于 '+scanRes.scannedAt.slice(11,16):(fromCache?'已缓存':'')}</div></div>`}
el.innerHTML=heroHtml+signalHtml+listHtml+(holdings.length?`<div style="margin-top:16px"><button class="action-btn secondary" onclick="forceScanFunds()" style="width:100%">🔄 重新计算估值</button></div>`:'');
}catch(e){console.error('Fund load error:',e);el.innerHTML='<div style="text-align:center;padding:40px;color:var(--red)">加载失败: '+e.message+'</div>'}}

async function forceScanFunds(){
// 强制跳过缓存，实时重新计算
const el=document.getElementById('holdingsContent');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:40px"><div class="loading-spinner"></div><div style="color:var(--text2);margin-top:12px">重新计算估值（约15-30秒）...</div></div>';
try{
const scanRes=await fetch(API_BASE+'/fund-holdings/scan?'+getProfileParam()+'&force=true',{signal:AbortSignal.timeout(60000)}).then(r=>r.json());
_fundScanData=scanRes;
renderFundsContent();  // 再正常渲染（此时缓存已更新）
}catch(e){el.innerHTML='<div style="text-align:center;padding:20px;color:var(--red)">重新计算失败: '+e.message+'<br><button onclick="renderFundsContent()" style="margin-top:8px;padding:6px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">返回</button></div>'}}

function showAddFundModal(){const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
overlay.innerHTML=`<div class="modal-sheet"><div class="modal-handle"></div><div class="modal-title">添加基金持仓</div>
<div style="margin-bottom:12px">
  <button onclick="showReceiptParser()" style="width:100%;padding:10px;border-radius:10px;border:1px dashed rgba(99,102,241,.4);background:rgba(99,102,241,.06);color:#818CF8;font-size:13px;cursor:pointer">
    📋 粘贴买入凭证文字 → 自动识别填入
  </button>
</div>
<div class="input-group"><label>基金代码</label><input id="addFundCode" placeholder="如 016501" class="input-field"></div>
<div id="addFundNameHint" style="font-size:11px;color:var(--accent);margin:-8px 0 8px 0;padding-left:2px"></div>
<div class="input-group"><label>成本净值（选填）</label><input id="addFundCost" type="number" step="0.0001" placeholder="买入确认净值" class="input-field"></div>
<div class="input-group"><label>持有份额（选填）</label><input id="addFundShares" type="number" step="0.01" placeholder="确认份额" class="input-field"></div>
<div class="input-group"><label>备注（选填）</label><input id="addFundNote" placeholder="如：定投 2026-05-22" class="input-field"></div>
<button class="action-btn primary" onclick="doAddFund()" style="width:100%;margin-top:16px">确认添加</button></div>`;
document.body.appendChild(overlay)}

// 凭证文字解析弹窗
function showReceiptParser(){
const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.id='receiptOverlay';overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
overlay.innerHTML=`<div class="modal-sheet" style="max-height:80vh;overflow-y:auto">
  <div class="modal-handle"></div>
  <div class="modal-title">📋 粘贴买入凭证</div>
  <div style="font-size:12px;color:var(--text2);margin-bottom:10px;line-height:1.6">
    在支付宝/天天基金 → 交易记录 → 长按复制页面文字，粘贴到下方：
  </div>
  <textarea id="receiptText" style="width:100%;height:140px;padding:10px;border-radius:10px;border:1px solid rgba(148,163,184,.2);background:var(--bg2);color:var(--text1);font-size:12px;resize:none;box-sizing:border-box" placeholder="买入产品 华夏半导体龙头混合C&#10;确认净值 3.0270&#10;确认份额 33.04份&#10;买入金额 100.00元&#10;确认时间 2026-05-22"></textarea>
  <button class="action-btn primary" onclick="doParseReceipt()" style="width:100%;margin-top:12px">🔍 识别并填入</button>
  <div id="receiptResult" style="margin-top:8px;font-size:12px;color:var(--accent)"></div>
</div>`;
document.body.appendChild(overlay);}

async function doParseReceipt(){
const text=document.getElementById('receiptText')?.value?.trim();
if(!text){alert('请先粘贴凭证文字');return;}
const btn=document.querySelector('#receiptOverlay .action-btn');
if(btn){btn.textContent='识别中...';btn.disabled=true;}
try{
  const r=await fetch(API_BASE+'/fund/parse-receipt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text}),signal:AbortSignal.timeout(15000)});
  const d=await r.json();
  if(!d.ok){
    document.getElementById('receiptResult').textContent='❌ '+d.reason;
    if(btn){btn.textContent='🔍 识别并填入';btn.disabled=false;}
    return;
  }
  // 填入主表单
  if(d.fund_code) document.getElementById('addFundCode').value=d.fund_code;
  if(d.nav) document.getElementById('addFundCost').value=d.nav;
  if(d.shares) document.getElementById('addFundShares').value=d.shares;
  if(d.fund_name||d.date){
    document.getElementById('addFundNote').value=[d.fund_name||'',d.date||''].filter(Boolean).join(' ').trim();
  }
  // 显示基金名提示
  if(d.fund_name){
    const hint=document.getElementById('addFundNameHint');
    if(hint) hint.textContent='✅ '+d.fund_name+(d.fund_code?' ('+d.fund_code+')':'');
  }
  document.getElementById('receiptResult').innerHTML='<span style="color:#10B981">✅ 识别成功！请确认后点击添加</span>';
  setTimeout(()=>document.getElementById('receiptOverlay')?.remove(),1200);
}catch(e){
  document.getElementById('receiptResult').textContent='❌ 识别失败: '+e.message;
  if(btn){btn.textContent='🔍 识别并填入';btn.disabled=false;}
}}

async function doAddFund(){
const code=$('#addFundCode')?.value?.trim();if(!code){alert('请输入基金代码');return}
const cost=parseFloat($('#addFundCost')?.value)||0;const shares=parseFloat($('#addFundShares')?.value)||0;const note=$('#addFundNote')?.value||'';
try{
  const r=await fetch(API_BASE+'/fund-holdings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,costNav:cost,shares,note,userId:getProfileId()})});
  const d=await r.json();
  if(d.error){
    // 重复基金：提示是否合并（加权平均）
    if(d.error.includes('已在持仓中')){
      const merge=confirm(`${code} 已在持仓中。\n\n是否合并本次买入（加权平均成本）？\n\n【确定】= 合并（推荐，适合定投）\n【取消】= 不添加`);
      if(merge){
        // 先拉现有数据
        const hr=await fetch(API_BASE+'/fund-holdings?userId='+getProfileId());
        const hd=await hr.json();
        const existing=(hd.holdings||hd||[]).find(h=>h.code===code);
        if(existing){
          const oldShares=existing.shares||0;const oldCost=existing.costNav||0;
          const newShares=oldShares+shares;
          // 加权平均成本 = (旧份额×旧净值 + 新份额×新净值) / 总份额
          const newCost=newShares>0?((oldShares*oldCost+shares*cost)/newShares):cost;
          const ur=await fetch(API_BASE+'/fund-holdings/'+code,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({costNav:parseFloat(newCost.toFixed(4)),shares:parseFloat(newShares.toFixed(4)),note:(existing.note||'')+(note?` + ${note}`:''),userId:getProfileId()})});
          const ud=await ur.json();
          if(ud.ok||ud.error===undefined){alert(`✅ 合并成功！\n总份额：${newShares.toFixed(2)}\n加权成本净值：${newCost.toFixed(4)}`);document.querySelector('.modal-overlay')?.remove();renderFundsContent();}
          else{alert('合并失败: '+(ud.error||'未知错误'));}
        }
      }
    } else {
      alert(d.error);
    }
    return;
  }
  document.querySelector('.modal-overlay')?.remove();renderFundsContent();
}catch(e){alert('添加失败: '+e.message);}}

function _calcDcaAdvice(pnlPct, downDays, maxDrawdown){
// 规则引擎：基于盈亏%、连跌天数、最大回撤，生成定投建议
// 返回 {level:'red'|'yellow'|'green', action:'...', hint:'...', multiplier:'...'}
const p = pnlPct ?? 0;
const dd = downDays || 0;
const mdd = (maxDrawdown || 0) * 100; // 转为 %

let level, action, hint, multiplier;

if(p <= -15){
  level='red'; action='⛔ 已达止损线'; multiplier='暂停定投';
  hint='持仓亏损超过 -15%（你设置的止损线），建议冷静审视基金逻辑，考虑止损或减仓';
} else if(p <= -8){
  level='yellow'; action='📉 加倍定投'; multiplier='2× 倍率';
  hint='中等亏损区间，适合加大定投摊低成本，下跌越多可适当加仓';
} else if(p <= -2){
  level='green'; action='🟢 加码定投'; multiplier='1.5× 倍率';
  hint='轻度回撤，是小幅加仓的好时机，按 1.5 倍定投额买入';
} else if(p <= 15){
  level='green'; action='✅ 正常定投'; multiplier='1× 标准';
  hint='盈利区间，按原计划正常定投即可，不追涨也不减仓';
} else if(p <= 35){
  level='yellow'; action='🔆 减半定投'; multiplier='0.5× 倍率';
  hint='盈利较高，建议定投减半，等待回调后再恢复标准额';
} else {
  level='red'; action='⚠️ 接近止盈线'; multiplier='暂停/分批减仓';
  hint='盈利已超 +35%（接近止盈线 +50%），可考虑分批兑现，锁定部分收益';
}

// 修正信号：连跌叠加
const extras = [];
if(dd >= 3 && level !== 'red'){
  extras.push(`连跌 ${dd} 天，短期承压，可额外加购一次`);
}
if(mdd > 20 && level === 'green'){
  extras.push(`该基金历史最大回撤 ${mdd.toFixed(0)}%，波动较大，建议单次金额不超过月计划的 30%`);
}

return {level, action, hint, multiplier, extras};
}

async function showFundHoldingDetail(code){
// 优先从已有 scan 数据取，没有则直接拉 API
let h=(_fundScanData?.holdings||[]).find(x=>x.code===code);
if(!h){
  try{
    const r=await fetch(API_BASE+'/fund-holdings/scan?'+getProfileParam(),{signal:AbortSignal.timeout(10000)});
    if(r.ok){const d=await r.json();_fundScanData=d;h=d.holdings?.find(x=>x.code===code);}
  }catch(e){}
}
// v9.5.12 兜底：scan API 没拿到该基金（持仓未同步到服务器），从本地 portfolio + window._holdingsPnl 自造
if(!h){
  try{
    const txns=(typeof loadTxns==='function')?loadTxns():[];
    const localHoldings=(typeof calcHoldingsFromTxns==='function')?calcHoldingsFromTxns(txns):[];
    const lh=localHoldings.find(x=>x.code===code);
    const pn=(window._holdingsPnl||{})[code]||{};
    const fd=(typeof FUND_DETAILS!=='undefined'&&FUND_DETAILS)?FUND_DETAILS[code]:null;
    if(lh||pn.marketValue){
      h={
        code,
        name: lh?.name || fd?.fullName || code,
        shares: lh?.shares || 0,
        totalCost: lh?.totalCost || 0,
        avgPrice: lh?.avgPrice || 0,
        pnlPct: pn.pnlPct ?? null,
        pnl: pn.pnl || 0,
        realtime: { nav: pn.nav || '', estNav: '', estRate: pn.dayChange || null, estDeviation: null },
        risk: {},
        alerts: [],
        _localFallback: true,
      };
    }
  }catch(e){console.warn('local fallback failed:',e)}
}
if(!h){
  // 还是没拿到 → 给可见反馈，而不是静默 return
  const o2=document.createElement('div');o2.className='modal-overlay';o2.onclick=e=>{if(e.target===o2)o2.remove()};
  o2.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
    <div class="modal-title">📋 定投建议</div>
    <div style="padding:20px;text-align:center;color:var(--text2);font-size:13px;line-height:1.7">
      暂无 <b>${code}</b> 的实时数据<br>
      <span style="font-size:11px;color:var(--text3,#7A8499)">该基金尚未同步到服务器，请稍后再试，或先在「基金详情」里查看</span>
    </div>
    <button class="action-btn" onclick="document.querySelector('.modal-overlay')?.remove()" style="width:100%;margin-top:8px">知道了</button>
  </div>`;
  document.body.appendChild(o2);
  return;
}
const rt=h.realtime||{};const risk=h.risk||{};const alerts=h.alerts||[];

// 定投建议
const pnlPct = h.pnlPct ?? null;
const dca = pnlPct != null ? _calcDcaAdvice(pnlPct, risk.downDays, risk.maxDrawdown) : null;
const dcaColor = dca?.level==='red'?'var(--red)':dca?.level==='yellow'?'#F59E0B':'var(--green)';
const dcaBg = dca?.level==='red'?'rgba(239,68,68,.06)':dca?.level==='yellow'?'rgba(245,158,11,.06)':'rgba(16,185,129,.06)';
const dcaBorder = dca?.level==='red'?'rgba(239,68,68,.2)':dca?.level==='yellow'?'rgba(245,158,11,.2)':'rgba(16,185,129,.2)';
const dcaHtml = dca ? `
<div style="margin-top:16px;padding:14px;background:${dcaBg};border:1px solid ${dcaBorder};border-radius:12px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
    <div style="font-size:13px;font-weight:700">📋 定投建议</div>
    <div style="font-size:11px;color:var(--text2)">基于当前盈亏自动计算</div>
  </div>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
    <div style="font-size:18px;font-weight:800;color:${dcaColor}">${dca.action}</div>
    <div style="padding:2px 10px;background:${dcaColor}20;border-radius:20px;font-size:11px;color:${dcaColor};font-weight:600">${dca.multiplier}</div>
  </div>
  <div style="font-size:12px;color:var(--text2);line-height:1.7">${dca.hint}</div>
  ${dca.extras.length ? '<div style="margin-top:8px">'+dca.extras.map(e=>`<div style="font-size:11px;color:#F59E0B;margin-top:4px">⚡ ${e}</div>`).join('')+'</div>' : ''}
  <div style="font-size:10px;color:var(--text3,#475569);margin-top:8px;border-top:1px solid rgba(148,163,184,.1);padding-top:6px">当前盈亏 ${pnlPct>=0?'+':''}${pnlPct.toFixed(1)}% · 止损 -15% · 止盈 +50%</div>
</div>` : '';

const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
overlay.innerHTML=`<div class="modal-sheet" style="max-height:90vh;overflow-y:auto"><div class="modal-handle"></div>
<div class="modal-title">${h.name||h.code}</div>
<div class="modal-subtitle">${h.code} · 估算 ${rt.estRate!=null?(rt.estRate>=0?'+':'')+rt.estRate.toFixed(2)+'%':'--'}</div>
<div class="modal-stat-grid">
<div class="modal-stat"><div class="modal-stat-label">估算净值</div><div class="modal-stat-value">${rt.estNav||'--'}</div></div>
<div class="modal-stat"><div class="modal-stat-label">最新净值</div><div class="modal-stat-value">${rt.nav||'--'}</div></div>
<div class="modal-stat"><div class="modal-stat-label">估算偏差</div><div class="modal-stat-value">${rt.estDeviation!=null?rt.estDeviation.toFixed(2)+'%':'--'}</div></div>
<div class="modal-stat"><div class="modal-stat-label">最大回撤</div><div class="modal-stat-value" style="color:${risk.maxDrawdown>0.03?'var(--red)':'var(--text)'}">${risk.maxDrawdown!=null?(risk.maxDrawdown*100).toFixed(1)+'%':'--'}</div></div>
<div class="modal-stat"><div class="modal-stat-label">年化波动</div><div class="modal-stat-value">${risk.volatility!=null?(risk.volatility*100).toFixed(1)+'%':'--'}</div></div>
<div class="modal-stat"><div class="modal-stat-label">连跌天数</div><div class="modal-stat-value" style="color:${risk.downDays>=3?'var(--red)':'var(--text)'}">${risk.downDays||0}天</div></div>
</div>
${dcaHtml}
${alerts.length?'<div style="margin-top:16px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">⚡ 信号</div>'+alerts.map(a=>`<div style="background:rgba(239,68,68,.06);border-radius:8px;padding:8px;margin-bottom:4px;font-size:12px">${a.msg}</div>`).join('')+'</div>':''}
<button class="action-btn" onclick="deleteFund('${h.code}')" style="width:100%;margin-top:16px;color:var(--red);border-color:var(--red)">🗑️ 删除此基金</button>
</div>`;
document.body.appendChild(overlay)}

async function deleteFund(code){try{await fetch(API_BASE+'/fund-holdings/'+code+'?'+getProfileParam(),{method:'DELETE'});document.querySelector('.modal-overlay')?.remove();renderFundsContent()}catch(e){alert('删除失败')}}


// --- 08-watchlist-poll.js ---
// ==== V6 Patch 08: 盯盘预警前端轮询 ====
// 功能：交易时段每 15 秒轮询 /api/watchlist/alerts，有预警时 toast 弹出
// 条件：Pro 模式 + 有持仓 + 交易时段（9:30-15:00 工作日）
// 空仓时返回 idle 状态，不弹预警

(function _v6_watchlist_poll() {
  let _watchTimer = null;
  let _shownAlerts = new Set(); // 避免重复弹同一条

  function isTradeHours() {
    const now = new Date();
    const day = now.getDay();
    if (day === 0 || day === 6) return false; // 周末
    const h = now.getHours(), m = now.getMinutes();
    const t = h * 60 + m;
    return t >= 9 * 60 + 25 && t <= 15 * 60 + 5; // 9:25~15:05（提前5分钟开始，延后5分钟收尾）
  }

  function showAlertToast(alert) {
    const key = `${alert.type}_${alert.code}`;
    if (_shownAlerts.has(key)) return;
    _shownAlerts.add(key);
    // 5 分钟后允许再次弹出同一预警
    setTimeout(() => _shownAlerts.delete(key), 5 * 60 * 1000);

    const colors = {
      danger: { bg: 'rgba(239,68,68,.15)', border: 'rgba(239,68,68,.4)', text: '#EF4444', icon: '🚨' },
      warning: { bg: 'rgba(245,158,11,.15)', border: 'rgba(245,158,11,.4)', text: '#F59E0B', icon: '⚠️' },
      info: { bg: 'rgba(59,130,246,.15)', border: 'rgba(59,130,246,.4)', text: '#3B82F6', icon: 'ℹ️' },
    };
    const c = colors[alert.level] || colors.info;

    const toast = document.createElement('div');
    toast.className = 'watchlist-alert-toast';
    toast.innerHTML = `
      <div style="display:flex;align-items:flex-start;gap:8px">
        <span style="font-size:18px;flex-shrink:0">${c.icon}</span>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:13px;color:${c.text};margin-bottom:2px">${alert.name || alert.code}</div>
          <div style="font-size:12px;color:var(--text-secondary,#94A3B8);line-height:1.4">${alert.message}</div>
        </div>
        <button onclick="this.parentElement.parentElement.remove()" style="background:none;border:none;color:var(--text-muted,#64748B);cursor:pointer;font-size:16px;padding:0;line-height:1">×</button>
      </div>
    `;
    toast.style.cssText = `
      position:fixed;top:${60 + document.querySelectorAll('.watchlist-alert-toast').length * 75}px;right:16px;z-index:10001;
      background:${c.bg};border:1px solid ${c.border};border-radius:12px;padding:12px 14px;
      max-width:320px;min-width:240px;backdrop-filter:blur(12px);
      box-shadow:0 4px 20px rgba(0,0,0,.15);
      animation:watchToastIn .3s ease-out;
      transition:opacity .3s,transform .3s;
    `;
    document.body.appendChild(toast);

    // 8 秒后自动消失
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(100%)';
      setTimeout(() => toast.remove(), 300);
    }, 8000);
  }

  async function pollAlerts() {
    if (!window._proMode) return; // Simple 模式不轮询
    if (!isTradeHours()) return; // 非交易时段不轮询

    const userId = localStorage.getItem('moneybag_current_profile') || 'default';
    try {
      const r = await fetch(`/api/watchlist/alerts?userId=${userId}`);
      if (!r.ok) return;
      const data = await r.json();

      // 空仓：total_holdings=0，不弹预警
      if (!data.total_holdings || data.total_holdings === 0) return;

      const alerts = data.alerts || [];
      if (alerts.length === 0) return;

      alerts.forEach(a => showAlertToast(a));
    } catch (e) {
      console.warn('[Watchlist] poll error:', e);
    }
  }

  function startPolling() {
    if (_watchTimer) return;
    pollAlerts(); // 立即查一次
    _watchTimer = setInterval(pollAlerts, 15000); // 每 15 秒
    console.log('[Watchlist] polling started (15s interval, trade hours only)');
  }

  function stopPolling() {
    if (_watchTimer) {
      clearInterval(_watchTimer);
      _watchTimer = null;
      console.log('[Watchlist] polling stopped');
    }
  }

  // 注入 CSS 动画
  const style = document.createElement('style');
  style.textContent = `
    @keyframes watchToastIn {
      from { opacity:0; transform:translateX(100%) }
      to { opacity:1; transform:translateX(0) }
    }
  `;
  document.head.appendChild(style);

  // 劫持 toggleUIMode：Pro 模式开轮询，Simple 模式停
  if (typeof window.toggleUIMode === 'function') {
    const _origToggle08 = window.toggleUIMode;
    window.toggleUIMode = function() {
      _origToggle08.apply(this, arguments);
      if (window._proMode) startPolling();
      else stopPolling();
    };
  }

  // 启动：如果当前是 Pro 模式就开始轮询
  if (window._proMode) {
    startPolling();
  }

  // 页面不可见时暂停，可见时恢复
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopPolling();
    } else if (window._proMode) {
      startPolling();
    }
  });
})();

