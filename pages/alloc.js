// ---- 配置资产弹窗（v3.0 动态推荐） ----
let _allocDynamicFunds = null; // 缓存动态推荐结果
let _allocPreference = 'fund'; // fund|stock|mix

function showAllocateAssets(){
const p=loadPortfolio();const profile=p.profile||'稳健型';
_allocPreference=p.preference||'fund';
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">💰 配置新资产</div>
<div class="modal-subtitle">新存款/工资到账？AI 动态推荐最优配置</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
<div class="form-row"><div class="form-label">投入金额</div>
<div class="amount-input-wrap"><span class="amount-prefix">¥</span><input type="number" id="allocAmt" class="amount-input" placeholder="10000" style="padding-left:36px;padding-right:16px;font-size:20px"></div>
<div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">${[1000,3000,5000,10000,20000].map(v=>`<button onclick="document.getElementById('allocAmt').value=${v};updateAllocPreview()" style="background:var(--bg3);border:none;color:var(--text2);padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer">¥${fmtMoney(v)}</button>`).join('')}</div></div>
<div class="form-row"><div class="form-label">配置偏好</div>
<div style="display:flex;gap:6px">
<button id="prefFund" onclick="_allocPreference='fund';_allocDynamicFunds=null;_updatePrefBtns();loadDynamicAlloc()" style="flex:1;padding:8px;border-radius:8px;border:1px solid var(--bg3);font-size:12px;cursor:pointer">💰 纯基金</button>
<button id="prefStock" onclick="_allocPreference='stock';_allocDynamicFunds=null;_updatePrefBtns();loadDynamicAlloc()" style="flex:1;padding:8px;border-radius:8px;border:1px solid var(--bg3);font-size:12px;cursor:pointer">📈 纯股票</button>
<button id="prefMix" onclick="_allocPreference='mix';_allocDynamicFunds=null;_updatePrefBtns();loadDynamicAlloc()" style="flex:1;padding:8px;border-radius:8px;border:1px solid var(--bg3);font-size:12px;cursor:pointer">🔀 混合</button>
</div></div>
<div id="allocPreview" style="margin-top:16px"><div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:20px;height:20px;margin:0 auto 8px;border-width:2px"></div>AI 正在推荐最优配置...</div></div>
<button class="form-submit" onclick="executeAllocate()" id="allocBtn" disabled>✅ 我知道了，去录入持仓</button>
<div id="allocAdjustments" style="margin-top:8px;font-size:11px;color:var(--text2)"></div>
<div style="font-size:11px;color:var(--text2);text-align:center;margin-top:8px">按「${profile}」方案 · AI 动态推荐</div>
</div></div>`;
document.body.appendChild(o);
_updatePrefBtns();
document.getElementById('allocAmt').addEventListener('input',updateAllocPreview);
loadDynamicAlloc()}

function _updatePrefBtns(){
['fund','stock','mix'].forEach(k=>{
const btn=document.getElementById('pref'+k.charAt(0).toUpperCase()+k.slice(1));
if(btn){btn.style.background=_allocPreference===k?'var(--accent)':'transparent';btn.style.color=_allocPreference===k?'#fff':'var(--text2)'}});}

async function loadDynamicAlloc(){
const el=document.getElementById('allocPreview');
if(el)el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:20px;height:20px;margin:0 auto 8px;border-width:2px"></div>AI 正在推荐最优配置...</div>';
if(!API_AVAILABLE){if(el)el.innerHTML='<div style="text-align:center;padding:12px;color:var(--text2)">后端离线，使用默认配置</div>';_allocDynamicFunds=null;updateAllocPreview();return}
const p=loadPortfolio();const profile=p.profile||'稳健型';
try{const r=await fetch(API_BASE+'/recommend-alloc?profile='+encodeURIComponent(profile)+'&with_ai=true&preference='+_allocPreference,{signal:AbortSignal.timeout(30000)});
if(r.ok){const d=await r.json();
if(d.allocations&&d.allocations.length){_allocDynamicFunds=d.allocations;
const adjEl=document.getElementById('allocAdjustments');
if(adjEl&&d.adjustments)adjEl.innerHTML=d.adjustments.map(a=>`<div style="padding:2px 0">${a}</div>`).join('');
updateAllocPreview();return}}}catch(e){console.warn('dynamic alloc:',e)}
_allocDynamicFunds=null;updateAllocPreview()}

function updateAllocPreview(){
const amt=parseFloat(document.getElementById('allocAmt')?.value)||0;
const el=document.getElementById('allocPreview');const btn=document.getElementById('allocBtn');
if(!el)return;
if(!amt||amt<=0){el.innerHTML='';if(btn)btn.disabled=true;return}
if(btn)btn.disabled=false;
const al=_allocDynamicFunds||(ALLOCATIONS[loadPortfolio().profile||'稳健型']||ALLOCATIONS['稳健型']);
const isDynamic=!!_allocDynamicFunds;
el.innerHTML=(isDynamic?`<div style="font-size:11px;color:var(--green);margin-bottom:8px;padding:4px 8px;background:rgba(16,185,129,.08);border-radius:6px">🤖 AI 动态推荐（${_allocPreference==='fund'?'纯基金':_allocPreference==='stock'?'纯股票':'混合'}）</div>`:'')+
al.map(a=>{
const fundAmt=Math.round(amt*a.pct/100);
const reason=a.aiReason?`<div style="font-size:10px;color:var(--accent);margin-top:2px">💡 ${a.aiReason}</div>`:'';
return`<div style="display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--bg3)">
<div style="width:10px;height:10px;border-radius:50%;background:${a.color};flex-shrink:0"></div>
<div style="flex:1"><div style="font-size:13px;font-weight:600">${a.fullName||a.name}</div><div style="font-size:11px;color:var(--text2)">${a.code} · ${a.pct}%${a.category?' · '+a.category:''}</div>${reason}</div>
<div style="font-size:15px;font-weight:700;color:var(--accent)">¥${fmtMoney(fundAmt)}</div></div>`}).join('')}

function executeAllocate(){
const amt=parseFloat(document.getElementById('allocAmt')?.value)||0;
if(!amt||amt<=0)return;
// v3.0: 不写入假数据，只保存偏好+跳转
const p=loadPortfolio();
p.preference=_allocPreference;
savePortfolio(p);
// 保存风险偏好到 agent_memory
if(API_AVAILABLE){
fetch(API_BASE+'/agent/preferences',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({userId:getProfileId(),risk_profile:p.profile||'稳健型',preference:_allocPreference,last_alloc_amount:amt})}).catch(()=>{})}
document.querySelector('.modal-overlay')?.remove();
alert('✅ 配置方案已保存！\n\n请到「持仓」页添加你实际买入的基金/股票。\n配置建议仅供参考，实际买入以你的操作为准。');
navigateTo('stocks')}

// ---- 配比历史弹窗 ----
let _compareSelection=[];
function showAllocHistory(){
const p=loadPortfolio();const hist=(p.history||[]).filter(h=>h.allocations&&h.allocations.length>0);
if(!hist.length){
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">📋 配比历史</div>
<div style="text-align:center;padding:40px;color:var(--text2)"><div style="font-size:48px;margin-bottom:12px">📋</div>
<div style="font-size:14px">还没有配比记录</div>
<div style="font-size:12px;margin-top:8px">完成测评或使用「配置资产」后，这里会显示你的历次配比方案</div></div></div>`;
document.body.appendChild(o);return}
_compareSelection=[];
const sorted=hist.slice().reverse();
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
const actionLabel={'allocate':'💰 配置资产','quiz_buy':'📝 测评配置','buy':'📝 测评配置'};
const prefLabel={'fund':'🏦 基金','stock':'📊 股票','mixed':'🔄 混合'};
let listHtml=sorted.map((h,idx)=>{
const d=new Date(h.date);
const dateStr=`${d.getMonth()+1}/${d.getDate()} ${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}`;
const totalIdx=hist.length-idx;
return`<div class="alloc-history-item" id="allocHistItem${idx}" style="background:var(--card);border-radius:12px;padding:14px;margin-bottom:10px;border:2px solid transparent;cursor:pointer;transition:border-color .2s" onclick="toggleAllocCompare(${idx})">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
<div><span style="font-size:14px;font-weight:700">#${totalIdx}</span> <span style="font-size:12px;color:var(--text2)">${dateStr}</span></div>
<div style="display:flex;gap:6px;align-items:center">
${h.preference?`<span style="font-size:10px;padding:2px 8px;background:rgba(139,92,246,.1);border-radius:4px;color:#A78BFA">${prefLabel[h.preference]||h.preference}</span>`:''}
<span style="font-size:10px;padding:2px 8px;background:rgba(245,158,11,.1);border-radius:4px;color:#F59E0B">${h.profile||'稳健型'}</span>
<span style="font-size:10px;padding:2px 8px;background:var(--bg3);border-radius:4px;color:var(--text2)">${actionLabel[h.action]||h.action}</span>
</div></div>
<div style="font-size:18px;font-weight:800;color:var(--accent);margin-bottom:8px">¥${fmtMoney(h.amount)}</div>
<div style="display:flex;flex-wrap:wrap;gap:4px">${h.allocations.map(a=>`<span style="font-size:11px;padding:3px 8px;background:var(--bg3);border-radius:6px;color:var(--text1)">${a.name} ${a.pct}% ¥${fmtMoney(a.amount)}</span>`).join('')}</div>
</div>`}).join('');
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:85vh;overflow-y:auto"><div class="modal-handle"></div>
<div class="modal-title">📋 配比历史 <span style="font-size:12px;color:var(--text2);font-weight:400">${hist.length}次</span></div>
<div style="font-size:12px;color:var(--text2);margin-bottom:12px">点击选中两条记录可并排对比差异</div>
<div id="allocCompareBar" style="display:none;position:sticky;top:0;background:var(--accent);color:#000;padding:10px 14px;border-radius:10px;margin-bottom:12px;font-size:13px;font-weight:700;text-align:center;cursor:pointer;z-index:10" onclick="showAllocCompare()">🔍 对比选中的 2 条记录</div>
${listHtml}</div>`;
document.body.appendChild(o)}

function toggleAllocCompare(idx){
const item=document.getElementById('allocHistItem'+idx);
const pos=_compareSelection.indexOf(idx);
if(pos>=0){_compareSelection.splice(pos,1);if(item)item.style.borderColor='transparent'}
else{if(_compareSelection.length>=2){const old=_compareSelection.shift();const oldEl=document.getElementById('allocHistItem'+old);if(oldEl)oldEl.style.borderColor='transparent'}
_compareSelection.push(idx);if(item)item.style.borderColor='var(--accent)'}
const bar=document.getElementById('allocCompareBar');
if(bar)bar.style.display=_compareSelection.length===2?'block':'none'}

function showAllocCompare(){
const p=loadPortfolio();const hist=(p.history||[]).filter(h=>h.allocations&&h.allocations.length>0).slice().reverse();
if(_compareSelection.length!==2)return;
const [a,b]=[hist[_compareSelection[0]],hist[_compareSelection[1]]];
if(!a||!b)return;
const fmtDate=d=>{const dt=new Date(d);return`${dt.getMonth()+1}/${dt.getDate()} ${dt.getHours().toString().padStart(2,'0')}:${dt.getMinutes().toString().padStart(2,'0')}`};
const prefLabel={'fund':'🏦 基金','stock':'📊 股票','mixed':'🔄 混合'};
// 合并所有基金/股票 code
const allCodes=[...new Set([...a.allocations.map(x=>x.code),...b.allocations.map(x=>x.code)])];
let diffHtml=allCodes.map(code=>{
const aItem=a.allocations.find(x=>x.code===code);
const bItem=b.allocations.find(x=>x.code===code);
const name=(aItem||bItem).name;
const aPct=aItem?aItem.pct:0;const bPct=bItem?bItem.pct:0;
const aAmt=aItem?aItem.amount:0;const bAmt=bItem?bItem.amount:0;
const pctDiff=bPct-aPct;const amtDiff=bAmt-aAmt;
const diffColor=pctDiff>0?'var(--green)':pctDiff<0?'var(--red)':'var(--text2)';
return`<div style="display:flex;align-items:center;padding:8px 0;border-bottom:1px solid var(--bg3);gap:8px">
<div style="flex:1;font-size:13px;font-weight:600">${name}</div>
<div style="width:70px;text-align:center;font-size:12px">${aPct}%<br><span style="font-size:11px;color:var(--text2)">¥${fmtMoney(aAmt)}</span></div>
<div style="width:50px;text-align:center;font-size:12px;font-weight:700;color:${diffColor}">${pctDiff>0?'+':''}${pctDiff}%</div>
<div style="width:70px;text-align:center;font-size:12px">${bPct}%<br><span style="font-size:11px;color:var(--text2)">¥${fmtMoney(bAmt)}</span></div>
</div>`}).join('');
const amtDiffTotal=b.amount-a.amount;const amtDiffColor=amtDiffTotal>0?'var(--green)':amtDiffTotal<0?'var(--red)':'var(--text2)';
document.querySelector('.modal-overlay')?.remove();
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:85vh;overflow-y:auto"><div class="modal-handle"></div>
<div class="modal-title">🔍 配比对比</div>
<div style="display:flex;gap:12px;margin-bottom:16px">
<div style="flex:1;background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.2);border-radius:10px;padding:10px;text-align:center">
<div style="font-size:11px;color:var(--text2)">旧配比 · ${fmtDate(a.date)}</div>
<div style="font-size:16px;font-weight:800;color:var(--accent)">¥${fmtMoney(a.amount)}</div>
<div style="font-size:10px;color:var(--text2)">${a.profile} ${prefLabel[a.preference]||''}</div></div>
<div style="flex:1;background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.2);border-radius:10px;padding:10px;text-align:center">
<div style="font-size:11px;color:var(--text2)">新配比 · ${fmtDate(b.date)}</div>
<div style="font-size:16px;font-weight:800;color:var(--green)">¥${fmtMoney(b.amount)}</div>
<div style="font-size:10px;color:var(--text2)">${b.profile} ${prefLabel[b.preference]||''}</div></div>
</div>
<div style="text-align:center;margin-bottom:12px;font-size:13px">金额变化: <span style="color:${amtDiffColor};font-weight:700">${amtDiffTotal>0?'+':''}¥${fmtMoney(amtDiffTotal)}</span></div>
<div style="display:flex;padding:6px 0;border-bottom:2px solid var(--bg3);margin-bottom:4px;font-size:11px;color:var(--text2);font-weight:600">
<div style="flex:1">基金/股票</div><div style="width:70px;text-align:center">旧</div><div style="width:50px;text-align:center">差异</div><div style="width:70px;text-align:center">新</div></div>
${diffHtml}
<button class="form-submit" style="margin-top:16px" onclick="document.querySelector('.modal-overlay')?.remove();showAllocHistory()">← 返回历史</button>
</div>`;
document.body.appendChild(o)}

