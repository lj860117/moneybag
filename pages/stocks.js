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
return`<div class="holding-card" onclick="showStockDetail('${h.code}')"><div class="holding-top"><div class="holding-info"><div class="holding-name">${h.name||h.code}${freshTag}</div><div class="holding-meta">${h.code}${industryTag}${weightTag}</div></div><div class="holding-amount"><div class="holding-money" style="color:${pctC}">${h.price?'¥'+h.price.toFixed(2):'--'}</div><div class="holding-pct" style="color:${pctC}">${h.changePct!=null?(h.changePct>=0?'+':'')+h.changePct.toFixed(2)+'%':'--'}</div></div></div>${h.costPrice&&h.shares?`<div class="holding-pnl-row"><div class="holding-pnl-item"><div class="holding-pnl-label">持仓市值</div><div class="holding-pnl-val">¥${(h.marketValue||0).toLocaleString()}</div></div><div class="holding-pnl-item"><div class="holding-pnl-label">盈亏</div><div class="holding-pnl-val ${(h.pnlPct||0)>=0?'pos':'neg'}" style="color:${pnlC}">${h.pnl!=null?((h.pnl>=0?'+':'')+h.pnl.toFixed(0)):''} ${h.pnlPct!=null?'('+((h.pnlPct>=0?'+':'')+h.pnlPct.toFixed(1))+'%)':''}</div></div><div class="holding-pnl-item"><div class="holding-pnl-label">成本价</div><div class="holding-pnl-val">¥${h.costPrice}</div></div></div>`:''}</div>`}).join('')}
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

function showStockDetail(code){const h=(_stockScanData?.holdings||[]).find(x=>x.code===code);if(!h)return;
const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
const ind=h.indicators||{};const sigs=h.signals||[];
overlay.innerHTML=`<div class="modal-sheet"><div class="modal-handle"></div><div class="modal-title">${h.name||h.code}</div><div class="modal-subtitle">${h.code} · ${h.changePct!=null?(h.changePct>=0?'+':'')+h.changePct.toFixed(2)+'%':'--'}</div><div class="modal-stat-grid"><div class="modal-stat"><div class="modal-stat-label">当前价</div><div class="modal-stat-value">${h.price?'¥'+h.price.toFixed(2):'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">RSI14</div><div class="modal-stat-value" style="color:${ind.rsi14>70?'var(--red)':ind.rsi14<30?'var(--green)':'var(--text)'}">${ind.rsi14||'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">MACD</div><div class="modal-stat-value">${ind.macd_trend||'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">量比</div><div class="modal-stat-value" style="color:${ind.volume_ratio>2?'var(--red)':'var(--text)'}">${ind.volume_ratio||'--'}</div></div></div>${sigs.length?'<div style="margin-top:16px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">📡 信号</div>'+sigs.map(s=>`<div style="padding:6px 0;font-size:13px;border-bottom:1px solid var(--bg3)">${s.msg}</div>`).join('')+'</div>':''}<div id="stockIntel_${code}" style="margin-top:16px"><div style="text-align:center;padding:12px;color:var(--text2);font-size:12px">📰 加载个股情报...</div></div><div style="margin-top:16px;display:flex;gap:8px"><button class="action-btn secondary" style="flex:1" onclick="if(confirm('删除 ${h.name}？'))deleteStock('${h.code}')">🗑️ 删除</button></div></div>`;
document.body.appendChild(overlay);
// 异步加载持仓关联智能
if(API_AVAILABLE){fetch(API_BASE+'/holding-intelligence/'+code+'?'+getProfileParam(),{signal:AbortSignal.timeout(15000)}).then(r=>r.json()).then(d=>{
const el=document.getElementById('stockIntel_'+code);if(!el)return;
let h='';
if(d.news&&d.news.length){h+=`<div style="font-size:13px;font-weight:700;margin-bottom:6px">📰 个股新闻</div>`;h+=d.news.slice(0,3).map(n=>`<div style="padding:4px 0;font-size:12px;border-bottom:1px solid var(--bg3)">${n.title}</div>`).join('')}
if(d.fund_flow){const ff=d.fund_flow;h+=`<div style="font-size:13px;font-weight:700;margin-top:10px;margin-bottom:4px">💰 主力资金</div><div style="font-size:12px;color:${ff.net_amount>0?'var(--green)':'var(--red)'}">今日主力净${ff.net_amount>0?'流入':'流出'} ${Math.abs(ff.net_amount||0).toFixed(0)}万</div>`}
if(d.industry){h+=`<div style="font-size:12px;color:var(--text2);margin-top:8px">🏭 所属行业：${d.industry}</div>`}
if(d.unlock_risk){h+=`<div style="font-size:12px;color:var(--red);margin-top:6px;padding:6px;background:rgba(239,68,68,.06);border-radius:6px">🔓 解禁预警：${d.unlock_risk}</div>`}
el.innerHTML=h||'<div style="font-size:12px;color:var(--text2)">暂无关联情报</div>'
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
// 信号汇总
let signalHtml='';const alerts=scanRes.alerts||[];
if(alerts.length){signalHtml='<div class="signal-summary"><div style="font-size:13px;font-weight:700;margin-bottom:8px">⚡ 基金异动信号</div>'+alerts.map(a=>{const bg=a.level==='warning'?'rgba(239,68,68,.08)':'rgba(34,197,94,.08)';return`<div style="background:${bg};border-radius:8px;padding:8px 10px;margin-bottom:4px;font-size:12px">${a.fund||''} ${a.msg}</div>`}).join('')+'</div>'}
// 列表
let listHtml='';
if(!holdings.length){listHtml='<div style="text-align:center;padding:40px;color:var(--text2)">暂无基金持仓<br><span style="font-size:12px">点击下方"添加基金"开始</span></div>'}
else{listHtml=holdings.map(h=>{const rt=h.realtime||{};const risk=h.risk||{};
const estRate=rt.estRate;const rateColor=estRate==null?'var(--text2)':estRate>=0?'var(--green)':'var(--red)';
const pnlColor=h.pnlPct==null?'var(--text2)':h.pnlPct>=0?'var(--green)':'var(--red)';
const ddStr=risk.maxDrawdown!=null?(risk.maxDrawdown*100).toFixed(1)+'%':'--';
const ddColor=risk.maxDrawdown!=null&&risk.maxDrawdown>0.03?'var(--red)':'var(--text2)';
return`<div class="stock-card" onclick="showFundHoldingDetail('${h.code}')" style="cursor:pointer">
<div style="display:flex;justify-content:space-between;align-items:center">
<div><div style="font-size:14px;font-weight:700">${h.name||h.code}</div><div style="font-size:11px;color:var(--text2)">${h.code}</div></div>
<div style="display:flex;align-items:center;gap:8px"><button class="action-btn secondary" onclick="event.stopPropagation();showFundChart('${h.code}')" style="padding:3px 8px;font-size:11px">K线</button><div style="text-align:right"><div style="font-size:14px;font-weight:600">${rt.estNav||'--'}</div>
<div style="font-size:12px;color:${rateColor}">${estRate!=null?(estRate>=0?'+':'')+estRate.toFixed(2)+'%':'--'}</div></div></div></div>
<div style="display:flex;gap:12px;margin-top:8px;font-size:11px;color:var(--text2)">
<span>净值 ${rt.nav||'--'}</span><span style="color:${ddColor}">回撤 ${ddStr}</span>
<span>连跌 ${risk.downDays||0}天</span>
${h.pnlPct!=null?`<span style="color:${pnlColor};font-weight:600">盈亏 ${h.pnlPct>=0?'+':''}${h.pnlPct.toFixed(1)}%</span>`:''}
</div>${h.alerts&&h.alerts.length?'<div style="margin-top:6px;font-size:11px;color:var(--accent)">'+h.alerts.map(a=>a.msg).join(' · ')+'</div>':''}</div>`}).join('')}
// Hero
let heroHtml='';const totalPnl=holdings.reduce((s,h)=>s+(h.pnl||0),0);
if(holdings.length>0){heroHtml=`<div class="pnl-hero"><div class="pnl-label">基金持仓 ${holdings.length} 只</div><div class="pnl-change ${totalPnl>=0?'pos':'neg'}" style="color:${totalPnl>=0?'var(--green)':'var(--red)'}">总盈亏 ${totalPnl>=0?'+':''}¥${totalPnl.toFixed(0)}</div><div class="pnl-sub">${scanRes.scannedAt?'更新于 '+scanRes.scannedAt.slice(11,16):''}</div></div>`}
el.innerHTML=heroHtml+signalHtml+listHtml+`<div style="margin-top:16px"><button class="action-btn primary" onclick="showAddFundModal()" style="width:100%">➕ 添加基金</button></div><div style="margin-top:8px"><button class="action-btn secondary" onclick="renderFundsContent()" style="width:100%">🔄 刷新估值</button></div>`;
}catch(e){console.error('Fund load error:',e);el.innerHTML='<div style="text-align:center;padding:40px;color:var(--red)">加载失败: '+e.message+'</div>'}}

function showAddFundModal(){const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
overlay.innerHTML=`<div class="modal-sheet"><div class="modal-handle"></div><div class="modal-title">添加基金持仓</div><div class="input-group"><label>基金代码</label><input id="addFundCode" placeholder="如 110011" class="input-field"></div><div class="input-group"><label>成本净值（选填）</label><input id="addFundCost" type="number" step="0.0001" placeholder="买入时的净值" class="input-field"></div><div class="input-group"><label>持有份额（选填）</label><input id="addFundShares" type="number" step="0.01" placeholder="持有份额" class="input-field"></div><div class="input-group"><label>备注（选填）</label><input id="addFundNote" placeholder="如：定投" class="input-field"></div><button class="action-btn primary" onclick="doAddFund()" style="width:100%;margin-top:16px">确认添加</button></div>`;
document.body.appendChild(overlay)}

async function doAddFund(){const code=$('#addFundCode')?.value?.trim();if(!code){alert('请输入基金代码');return}
const cost=parseFloat($('#addFundCost')?.value)||0;const shares=parseFloat($('#addFundShares')?.value)||0;const note=$('#addFundNote')?.value||'';
try{const r=await fetch(API_BASE+'/fund-holdings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,costNav:cost,shares,note,userId:getProfileId()})});
const d=await r.json();if(d.error){alert(d.error);return}document.querySelector('.modal-overlay')?.remove();renderFundsContent()}catch(e){alert('添加失败: '+e.message)}}

function showFundHoldingDetail(code){const h=(_fundScanData?.holdings||[]).find(x=>x.code===code);if(!h)return;
const rt=h.realtime||{};const risk=h.risk||{};const alerts=h.alerts||[];
const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
overlay.innerHTML=`<div class="modal-sheet"><div class="modal-handle"></div><div class="modal-title">${h.name||h.code}</div><div class="modal-subtitle">${h.code} · 估算 ${rt.estRate!=null?(rt.estRate>=0?'+':'')+rt.estRate.toFixed(2)+'%':'--'}</div><div class="modal-stat-grid"><div class="modal-stat"><div class="modal-stat-label">估算净值</div><div class="modal-stat-value">${rt.estNav||'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">最新净值</div><div class="modal-stat-value">${rt.nav||'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">估算偏差</div><div class="modal-stat-value">${rt.estDeviation!=null?rt.estDeviation.toFixed(2)+'%':'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">最大回撤</div><div class="modal-stat-value" style="color:${risk.maxDrawdown>0.03?'var(--red)':'var(--text)'}">${risk.maxDrawdown!=null?(risk.maxDrawdown*100).toFixed(1)+'%':'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">年化波动</div><div class="modal-stat-value">${risk.volatility!=null?(risk.volatility*100).toFixed(1)+'%':'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">连跌天数</div><div class="modal-stat-value" style="color:${risk.downDays>=3?'var(--red)':'var(--text)'}">${risk.downDays||0}天</div></div></div>${alerts.length?'<div style="margin-top:16px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">⚡ 信号</div>'+alerts.map(a=>`<div style="background:rgba(239,68,68,.06);border-radius:8px;padding:8px;margin-bottom:4px;font-size:12px">${a.msg}</div>`).join('')+'</div>':''}<button class="action-btn" onclick="deleteFund('${h.code}')" style="width:100%;margin-top:16px;color:var(--red);border-color:var(--red)">🗑️ 删除此基金</button></div>`;
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

