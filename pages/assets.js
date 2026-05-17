// ---- 资产管理页 ----
const ASSET_TYPES=[
{id:'cash',icon:'💵',label:'现金/存款',color:'var(--green)'},
{id:'property',icon:'🏠',label:'房产',color:'var(--accent)'},
{id:'car',icon:'🚗',label:'车辆',color:'var(--blue)'},
{id:'insurance',icon:'🛡️',label:'保险',color:'#8B5CF6'},
{id:'other',icon:'📦',label:'其他资产',color:'var(--text2)'},
{id:'liability',icon:'💳',label:'负债/贷款',color:'var(--red)'}];

function renderAssets(){currentPage='assets';renderNav();
// 先渲染骨架 UI（加载中…），然后异步拉取服务端数据
$('#app').innerHTML=`<div class="result-page fade-up">
<div class="pnl-hero" style="margin-bottom:16px">
<div class="pnl-label">🏦 统一净资产 <span style="font-size:11px;color:var(--text2)">(投资+资产-负债)</span></div>
<div class="pnl-total-value" id="assetPageNW">加载中…</div>
<div id="assetPageHealth" style="font-size:12px;color:var(--text2);margin-top:4px"></div>
<div id="assetPageRing" style="display:flex;justify-content:center;margin-top:12px"></div>
<div id="assetPageBuckets" style="display:flex;gap:8px;justify-content:center;margin-top:8px;font-size:11px;flex-wrap:wrap"></div>
</div>

<div id="aiAssetAdvice" style="display:none"></div>
<div id="cashAdviceCard" style="display:none"></div>

<div class="section-title" style="display:flex;justify-content:space-between;align-items:center">📋 我的资产<button style="background:none;border:none;color:var(--accent);font-size:13px;cursor:pointer" onclick="showAddAsset()">+ 添加</button></div>
<div id="assetListContainer"><div style="text-align:center;padding:24px;color:var(--text2);font-size:13px">加载中…</div></div>

<div style="display:flex;gap:10px;margin-top:16px">
<button style="flex:1;padding:14px;border-radius:12px;border:none;background:var(--accent);color:#000;font-size:15px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px" onclick="toggleLedgerPanel()">📝 记账</button>
<button style="flex:1;padding:14px;border-radius:12px;border:none;background:var(--card);color:var(--text);font-size:15px;font-weight:600;cursor:pointer;border:1px solid var(--border);display:flex;align-items:center;justify-content:center;gap:6px" onclick="showAddAsset()">➕ 添加资产</button>
</div>

<div id="ledgerPanelInAssets" style="display:none;margin-top:16px"></div>

<div style="text-align:center;margin-top:16px;font-size:12px;color:var(--text2);line-height:1.8">
💡 投资持仓在「📊 持仓」页管理<br>
所有数据自动汇总到统一净资产</div></div>`;
// 异步加载全部数据
loadAssetPageFull();
}
// 资产页：异步加载全部数据（服务端资产列表 + 统一净资产 + AI 建议）
async function loadAssetPageFull(){
// 1. 同时加载统一净资产 + 服务端资产列表
const [nwData, serverAssets] = await Promise.all([
  fetchUnifiedNetworth(),
  (async()=>{if(!API_AVAILABLE)return null;try{const r=await fetch(`${API_BASE}/assets?userId=${getProfileId()}`,{signal:AbortSignal.timeout(10000)});if(r.ok){const d=await r.json();return d.assets||[]}return null}catch{return null}})()
]);

// 2. 合并资产列表（服务端优先，localStorage 降级）
let assets = serverAssets || loadAssets();
// 如果服务端有数据，同步到 localStorage；如果没有，把 localStorage 上传到服务端
if(serverAssets && serverAssets.length>0){
  saveAssets(serverAssets);
} else if(!serverAssets && loadAssets().length>0 && API_AVAILABLE){
  // localStorage 有但服务端没有 → 上传同步
  const localAssets = loadAssets();
  localAssets.forEach(a=>{fetch(`${API_BASE}/assets`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getProfileId(),asset:a})}).catch(()=>{})});
}

// 3. 渲染资产列表
const listEl = document.getElementById('assetListContainer');
if(listEl){
  if(assets.length){
    listEl.innerHTML = assets.map(a=>{
      const t=ASSET_TYPES.find(x=>x.id===a.type)||ASSET_TYPES[4];
      const isLiab=a.type==='liability';
      return`<div class="holding-card" style="border-left:3px solid ${t.color}">
<div class="holding-top"><div class="holding-info">
<div class="holding-name">${t.icon} ${a.name}</div>
<div class="holding-meta">${t.label}${a.note?' · '+a.note:''}</div></div>
<div class="holding-amount" style="display:flex;align-items:center;gap:8px">
<div class="holding-money" style="color:${isLiab?'var(--red)':'var(--accent)'}">${isLiab?'-':''}¥${fmtMoney(Math.round(a.value||0))}</div>
<button style="background:none;border:none;color:var(--text2);font-size:14px;cursor:pointer;padding:4px" onclick="event.stopPropagation();showEditAsset('${a.id}')">✏️</button>
<button style="background:none;border:none;color:var(--text2);font-size:14px;cursor:pointer;padding:4px" onclick="event.stopPropagation();if(confirm('删除「${a.name}」？')){deleteAsset('${a.id}')}">🗑️</button>
</div></div></div>`}).join('');
  } else {
    listEl.innerHTML=`<div style="text-align:center;padding:32px;color:var(--text2)">
<div style="font-size:48px;margin-bottom:12px">🏦</div>
<div>还没有资产记录</div>
<div style="font-size:12px;margin-top:8px">添加现金、房产、车辆、保险等</div></div>`;
  }
}

// 4. 更新统一净资产（后端数据）
if(nwData){
  const el=document.getElementById('assetPageNW');if(el)el.textContent=`¥${fmtMoney(Math.round(nwData.netWorth))}`;
  const hel=document.getElementById('assetPageHealth');
  if(hel)hel.innerHTML=`${nwData.healthGrade||''} · ${nwData.healthScore||0}分${(nwData.healthIssues||[]).length?` · <span style="color:var(--red);font-size:11px">${nwData.healthIssues[0]}</span>`:''}`;
  // SVG 环形图
  const ring=document.getElementById('assetPageRing');
  if(ring&&nwData.allocation){const al=nwData.allocation;const segs=[
    {pct:al.investment||0,color:'#F59E0B',label:'投资'},{pct:al.cash||0,color:'#10B981',label:'现金'},
    {pct:al.property||0,color:'#3B82F6',label:'房产'},{pct:(al.car||0)+(al.insurance||0)+(al.other||0),color:'#6B7280',label:'其他'}
  ].filter(s=>s.pct>0);
  let offset=0;const r=50,cx=60,cy=60,C=2*Math.PI*r;
  let paths='';segs.forEach(s=>{const len=s.pct/100*C;paths+=`<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${s.color}" stroke-width="12" stroke-dasharray="${len} ${C-len}" stroke-dashoffset="${-offset}" transform="rotate(-90 ${cx} ${cy})"/>`;offset+=len});
  ring.innerHTML=`<svg width="120" height="120" viewBox="0 0 120 120">${paths}<text x="${cx}" y="${cy-4}" text-anchor="middle" fill="var(--text)" font-size="11" font-weight="700">¥${nwData.netWorth>10000?(nwData.netWorth/10000).toFixed(1)+'万':Math.round(nwData.netWorth)}</text><text x="${cx}" y="${cy+10}" text-anchor="middle" fill="var(--text2)" font-size="9">净资产</text></svg>`}
  // 分桶标签
  const bk=document.getElementById('assetPageBuckets');
  if(bk&&nwData.breakdown){const b=nwData.breakdown;const items=[
    {icon:'📈',label:'投资',val:(b.investment||{}).total||0,color:'#F59E0B'},
    {icon:'💵',label:'现金',val:(b.cash||{}).total||0,color:'#10B981'},
    {icon:'🏠',label:'房产',val:(b.property||{}).total||0,color:'#3B82F6'},
    {icon:'💳',label:'负债',val:(b.liability||{}).total||0,color:'#EF4444'}
  ].filter(i=>i.val>0);
  bk.innerHTML=items.map(i=>`<span style="background:${i.color}15;color:${i.color};padding:2px 8px;border-radius:6px;border:1px solid ${i.color}30">${i.icon} ${i.label} ¥${fmtMoney(Math.round(i.val))}</span>`).join('')}
} else {
  // 后端不可用 → 本地计算
  const assetTotal=assets.filter(a=>a.type!=='liability').reduce((s,a)=>s+(a.value||0),0);
  const liabTotal=assets.filter(a=>a.type==='liability').reduce((s,a)=>s+(a.value||0),0);
  const el=document.getElementById('assetPageNW');if(el)el.textContent=`¥${fmtMoney(Math.round(assetTotal-liabTotal))}`;
  const hel=document.getElementById('assetPageHealth');if(hel)hel.textContent='⚠️ 离线模式（不含投资持仓）';
}

// 5. AI 存款建议（有现金时自动触发）
const cashAmt=assets.filter(a=>a.type==='cash').reduce((s,a)=>s+(a.value||a.balance||0),0);
if(API_AVAILABLE && cashAmt>0){
  const advEl=document.getElementById('cashAdviceCard');if(advEl){advEl.style.display='block';
  advEl.innerHTML='<div class="dashboard-card" style="border-left:3px solid var(--accent);padding:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">💡 AI 存款建议</div><div style="font-size:12px;color:var(--text2)">DeepSeek 分析中...</div></div>';
  const ledger=loadLedger();const ledgerExpense=ledger.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
  fetch(`${API_BASE}/assets/advice`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cashAmount:cashAmt,monthlyExpense:ledgerExpense/Math.max(1,Math.ceil(ledger.length/30))*30,riskProfile:localStorage.getItem('moneybag_profile')||'稳健型'})}).then(r=>r.json()).then(d=>{
    advEl.innerHTML=`<div class="dashboard-card" style="border-left:3px solid var(--accent);padding:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">💡 AI 存款建议 <span style="font-size:10px;color:var(--text2);font-weight:400">${d.source==='ai'?'🤖 DeepSeek':'📐 规则'}</span></div><div style="font-size:12px;line-height:1.6">${d.advice}</div><div style="font-size:11px;color:var(--text2);margin-top:8px">应急储备 ¥${fmtMoney(d.emergencyFund||0)} | 闲置 ¥${fmtMoney(d.idleCash||0)} | 年损失 ¥${fmtMoney(d.annualLoss||0)}</div></div>`;
  }).catch(()=>{advEl.style.display='none'})}}

// 6. AI 资产诊断（有资产时触发）
if(API_AVAILABLE && assets.length>=2){
  const aiEl=document.getElementById('aiAssetAdvice');if(aiEl){aiEl.style.display='block';
  aiEl.innerHTML='<div class="dashboard-card" style="border-left:3px solid #8B5CF6;padding:12px;margin-bottom:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">🤖 AI 资产诊断</div><div style="font-size:12px;color:var(--text2)">DeepSeek 全量分析中...</div></div>';
  fetch(`${API_BASE}/ds/asset-diagnosis`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getProfileId()}),signal:AbortSignal.timeout(30000)}).then(r=>r.ok?r.json():null).then(d=>{
    if(d&&d.advice){aiEl.innerHTML=`<div class="dashboard-card" style="border-left:3px solid #8B5CF6;padding:12px;margin-bottom:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">🤖 AI 资产诊断 <span style="font-size:10px;color:var(--text2);font-weight:400">🤖 DeepSeek</span></div><div style="font-size:12px;line-height:1.8">${d.advice}</div></div>`}else{aiEl.style.display='none'}
  }).catch(()=>{aiEl.style.display='none'})}}
}

function showAddAsset(){
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">➕ 添加资产</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
<div class="form-row"><div class="form-label">类型</div>
<select class="form-select" id="assetType">${ASSET_TYPES.map(t=>`<option value="${t.id}">${t.icon} ${t.label}</option>`).join('')}</select></div>
<div class="form-row"><div class="form-label">名称</div>
<input class="form-input" type="text" id="assetName" placeholder="如：建行存款、朝阳区房产、花呗"></div>
<div class="form-row"><div class="form-label">金额(¥)</div>
<input class="form-input" type="number" id="assetValue" placeholder="0" inputmode="decimal"></div>
<div class="form-row"><div class="form-label">备注</div>
<input class="form-input" type="text" id="assetNote" placeholder="可选"></div>
<button class="form-submit" onclick="confirmAddAsset()">✅ 添加</button>
</div></div>`;
document.body.appendChild(o)}

function confirmAddAsset(){
const type=document.getElementById('assetType')?.value;
const name=document.getElementById('assetName')?.value?.trim();
const value=parseFloat(document.getElementById('assetValue')?.value);
const note=document.getElementById('assetNote')?.value?.trim()||'';
if(!name){alert('请输入名称');return}
if(!value||value<=0){alert('请输入金额');return}
// 大额预警：>1000万前端先确认
if(Math.abs(value)>10000000){if(!confirm(`⚠️ 金额较大（¥${Math.abs(value).toLocaleString()}），确认无误？`))return}
const assets=loadAssets();
assets.push({id:'ast_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,6),type,name,value,note,createdAt:new Date().toISOString(),updatedAt:new Date().toISOString()});
saveAssets(assets);
if(API_AVAILABLE)fetch(API_BASE+'/assets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),asset:{type,name,value,note}})}).catch(()=>{});
document.querySelector('.modal-overlay')?.remove();renderAssets();
// 资产变更后异步刷新首页净资产（后端缓存已在API层失效）
_refreshNetWorthAfterAssetChange()}

function showEditAsset(id){
const assets=loadAssets();const a=assets.find(x=>x.id===id);if(!a)return;
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">✏️ 编辑资产</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
<div class="form-row"><div class="form-label">名称</div>
<input class="form-input" type="text" id="editAssetName" value="${a.name}"></div>
<div class="form-row"><div class="form-label">金额(¥)</div>
<input class="form-input" type="number" id="editAssetValue" value="${a.value}" inputmode="decimal"></div>
<div class="form-row"><div class="form-label">备注</div>
<input class="form-input" type="text" id="editAssetNote" value="${a.note||''}"></div>
<button class="form-submit" onclick="confirmEditAsset('${id}')">✅ 保存</button>
</div></div>`;
document.body.appendChild(o)}

function confirmEditAsset(id){
const assets=loadAssets();const a=assets.find(x=>x.id===id);if(!a)return;
a.name=document.getElementById('editAssetName')?.value?.trim()||a.name;
a.value=parseFloat(document.getElementById('editAssetValue')?.value)||a.value;
a.note=document.getElementById('editAssetNote')?.value?.trim()||'';
a.updatedAt=new Date().toISOString();
saveAssets(assets);
if(API_AVAILABLE)fetch(API_BASE+'/assets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),asset:{id:a.id,type:a.type,name:a.name,value:a.value,note:a.note}})}).catch(()=>{});
document.querySelector('.modal-overlay')?.remove();renderAssets();
_refreshNetWorthAfterAssetChange()}

function deleteAsset(id){const assets=loadAssets().filter(a=>a.id!==id);saveAssets(assets);
if(API_AVAILABLE)fetch(API_BASE+'/assets/'+id+'?userId='+getUserId(),{method:'DELETE'}).catch(()=>{});
renderAssets();
_refreshNetWorthAfterAssetChange()}

