// ---- 资产管理页 ----
const ASSET_TYPES=[
{id:'cash',icon:'💵',label:'现金/存款',color:'var(--green)'},
{id:'property',icon:'🏠',label:'房产',color:'var(--accent)'},
{id:'car',icon:'🚗',label:'车辆',color:'var(--blue)'},
{id:'insurance',icon:'🛡️',label:'保险',color:'#8B5CF6'},
{id:'other',icon:'📦',label:'其他资产',color:'var(--text2)'},
{id:'liability',icon:'💳',label:'负债/贷款',color:'var(--red)'}];

function renderAssets(){currentPage='assets';renderNav();
// 先渲染骨架 UI，然后异步拉取服务端数据
$('#app').innerHTML=`<div class="result-page fade-up" style="padding-bottom:calc(var(--tabbar-height,76px) + 16px)">

<!-- Hero 净资产（与首页风格一致但色调略偏紫，区分） -->
<section class="mb-hero" style="margin-bottom:14px;background:linear-gradient(140deg,#0F0E1B 0%,#0E1019 60%,#0A0C14 100%);border-color:rgba(139,111,230,.18)">
  <div class="mb-hero__label" style="display:flex;align-items:center;gap:6px">🏦 统一净资产 <span class="mb-pill mb-pill--ai" style="font-size:9px;padding:2px 6px" id="assetHealthPill">加载中</span></div>
  <h2 class="mb-hero__num" id="assetPageNW"><span class="mb-money__symbol">¥</span><span class="mb-money__num">0</span></h2>
  <div id="assetPageHealth" style="font-size:var(--fs-sm,11px);color:var(--text-tertiary,#7A8499);margin-top:4px"></div>
  <div class="mb-hero__splits" id="assetPageBuckets">
    <div class="mb-hero__split"><div class="mb-hero__split-label">📈 投资</div><div class="mb-hero__split-value">¥0</div></div>
    <div class="mb-hero__split"><div class="mb-hero__split-label">🏠 实物</div><div class="mb-hero__split-value">¥0</div></div>
    <div class="mb-hero__split"><div class="mb-hero__split-label">💳 负债</div><div class="mb-hero__split-value mb-hero__split-value--dn">-¥0</div></div>
  </div>
</section>

<!-- 6 类资产网格 -->
<section style="margin-bottom:14px">
  <div style="font-size:12px;font-weight:700;margin-bottom:10px">📋 资产分类</div>
  <div class="mb-cat-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">
    <a class="mb-card--ghost" style="padding:14px 8px;text-align:center;cursor:pointer;text-decoration:none" onclick="showAddAssetOfType('property')">
      <div style="width:36px;height:36px;border-radius:10px;margin:0 auto 6px;display:grid;place-items:center;font-size:16px;background:rgba(255,183,85,.12)">🏠</div>
      <div style="font-size:11px;font-weight:600;color:var(--text-primary,#F0F2F7)">房产</div>
      <div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:2px" id="catValProperty">未添加</div>
    </a>
    <a class="mb-card--ghost" style="padding:14px 8px;text-align:center;cursor:pointer;text-decoration:none" onclick="showAddAssetOfType('cash')">
      <div style="width:36px;height:36px;border-radius:10px;margin:0 auto 6px;display:grid;place-items:center;font-size:16px;background:rgba(0,229,160,.12)">💵</div>
      <div style="font-size:11px;font-weight:600;color:var(--text-primary,#F0F2F7)">现金</div>
      <div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:2px" id="catValCash">未添加</div>
    </a>
    <a class="mb-card--ghost" style="padding:14px 8px;text-align:center;cursor:pointer;text-decoration:none" onclick="showAddAssetOfType('insurance')">
      <div style="width:36px;height:36px;border-radius:10px;margin:0 auto 6px;display:grid;place-items:center;font-size:16px;background:rgba(139,111,230,.12)">🛡️</div>
      <div style="font-size:11px;font-weight:600;color:var(--text-primary,#F0F2F7)">保险</div>
      <div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:2px" id="catValInsurance">未添加</div>
    </a>
    <a class="mb-card--ghost" style="padding:14px 8px;text-align:center;cursor:pointer;text-decoration:none" onclick="showAddAssetOfType('car')">
      <div style="width:36px;height:36px;border-radius:10px;margin:0 auto 6px;display:grid;place-items:center;font-size:16px;background:rgba(59,130,246,.12)">🚗</div>
      <div style="font-size:11px;font-weight:600;color:var(--text-primary,#F0F2F7)">车辆</div>
      <div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:2px" id="catValCar">未添加</div>
    </a>
    <a class="mb-card--ghost" style="padding:14px 8px;text-align:center;cursor:pointer;text-decoration:none" onclick="showAddAssetOfType('other')">
      <div style="width:36px;height:36px;border-radius:10px;margin:0 auto 6px;display:grid;place-items:center;font-size:16px;background:rgba(255,138,177,.12)">📦</div>
      <div style="font-size:11px;font-weight:600;color:var(--text-primary,#F0F2F7)">收藏品</div>
      <div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:2px" id="catValOther">未添加</div>
    </a>
    <a class="mb-card--ghost" style="padding:14px 8px;text-align:center;cursor:pointer;text-decoration:none" onclick="showAddAssetOfType('liability')">
      <div style="width:36px;height:36px;border-radius:10px;margin:0 auto 6px;display:grid;place-items:center;font-size:16px;background:rgba(255,107,107,.12)">💳</div>
      <div style="font-size:11px;font-weight:600;color:var(--text-primary,#F0F2F7)">负债</div>
      <div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:2px" id="catValLiability">未添加</div>
    </a>
  </div>
</section>

<div id="aiAssetAdvice" style="display:none"></div>
<div id="cashAdviceCard" style="display:none"></div>

<!-- 资产明细列表 -->
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
  <span style="font-size:12px;font-weight:700">📋 我的资产</span>
  <button class="mb-btn mb-btn--ghost mb-btn--sm" onclick="showAddAsset()">+ 添加</button>
</div>
<div id="assetListContainer"><div style="text-align:center;padding:24px;color:var(--text-secondary,#9AA1AC);font-size:13px">加载中…</div></div>

<div class="mb-flex mb-gap-3" style="margin-top:14px">
<button class="mb-btn mb-btn--primary mb-btn--block" onclick="toggleLedgerPanel()">📝 记账</button>
<button class="mb-btn mb-btn--secondary mb-btn--block" onclick="showAddAsset()">➕ 添加资产</button>
</div>

<div id="ledgerPanelInAssets" style="display:none;margin-top:16px"></div>

<!-- 我的记录 -->
<div style="margin-top:20px">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
<span style="font-size:13px;font-weight:700">📋 我的记录</span>
<a onclick="navigateTo('history')" style="font-size:11px;color:var(--color-brand-500,#FFB755);cursor:pointer">全部 →</a>
</div>
<div id="assetPageHistory" style="font-size:12px;color:var(--text-secondary,#9AA1AC)">加载中…</div>
</div>

<div style="text-align:center;margin-top:16px;font-size:12px;color:var(--text-tertiary,#7A8499);line-height:1.8">
💡 投资持仓在「📊 持仓」页管理<br>
所有数据自动汇总到统一净资产</div></div>`;
// 异步加载全部数据
loadAssetPageFull();
// 异步加载最近分析记录（我的记录）
loadAssetPageHistory();
}

// 快捷添加指定类型资产 — 传入 type 后通过 URL 参数打开，避免 setTimeout 时机问题
function showAddAssetOfType(type){
  showAddAsset(type);
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
      // 上次更新时间（显示几天前）
      let ageStr='';
      if(a.updatedAt||a.createdAt){
        const dt=new Date(a.updatedAt||a.createdAt);const now=new Date();
        const days=Math.floor((now-dt)/86400000);
        ageStr=days===0?' · 今天更新':days===1?' · 昨天更新':days<=30?' · '+days+'天前更新':' · '+Math.floor(days/30)+'个月前更新';
      }
      return`<div class="holding-card" style="border-left:3px solid ${t.color}">
<div class="holding-top"><div class="holding-info">
<div class="holding-name">${t.icon} ${a.name}</div>
<div class="holding-meta">${t.label}${a.note?' · '+a.note:''}${ageStr}</div></div>
<div class="holding-amount" style="display:flex;align-items:center;gap:8px">
<div class="holding-money" style="color:${isLiab?'var(--red)':'var(--accent)'}">${isLiab?'-':''}¥${fmtMoney(Math.round(a.value||0))}</div>
<button style="background:none;border:none;color:var(--text2);font-size:14px;cursor:pointer;padding:4px" onclick="event.stopPropagation();showEditAsset('${a.id}')">✏️</button>
<button style="background:none;border:none;color:var(--text2);font-size:14px;cursor:pointer;padding:4px" onclick="event.stopPropagation();if(confirm('删除「${a.name}」？')){deleteAsset('${a.id}')}">🗑️</button>
</div></div></div>`}).join('');
  } else {
    // 空资产时加引导卡（新用户引导）
    listEl.innerHTML=`<div style="text-align:center;padding:24px;color:var(--text2)">
<div style="font-size:40px;margin-bottom:12px">🏦</div>
<div style="font-size:13px;font-weight:600;color:var(--text-primary,#F0F2F7)">还没有资产记录</div>
<div style="font-size:12px;margin-top:8px;line-height:1.6">添加现金、房产、车辆、保险等<br><span style="color:var(--color-brand-500,#FFB755)">录入 2 条以上后 AI 给你做健康诊断 ↓</span></div>
<div style="margin-top:12px;display:flex;gap:8px;justify-content:center">
<button class="mb-btn mb-btn--primary mb-btn--sm" onclick="showAddAssetOfType('cash')">💰 存款/现金</button>
<button class="mb-btn mb-btn--secondary mb-btn--sm" onclick="showAddAssetOfType('property')">🏠 房产</button>
</div></div>`;
  }
}

// 4. 更新统一净资产（后端数据）
if(nwData){
  const el=document.getElementById('assetPageNW');if(el)el.textContent=`¥${fmtMoney(Math.round(nwData.netWorth))}`;
  const hel=document.getElementById('assetPageHealth');
  // 如果没有手动资产，加提示
  const hasManualAssets=assets.length>0;
  const noManualHint=!hasManualAssets?` · <span style="color:var(--color-brand-500,#FFB755);cursor:pointer" onclick="document.getElementById('assetListContainer').scrollIntoView({behavior:'smooth'})">⚠️ 未含手动资产，点此补录</span>`:'';
  if(hel)hel.innerHTML=`${nwData.healthGrade||''} · ${nwData.healthScore||0}分${(nwData.healthIssues||[]).length?` · <span style="color:var(--color-bear,#FF6B6B);font-size:11px">${nwData.healthIssues[0]}</span>`:''}${noManualHint}`;
  const pill=document.getElementById('assetHealthPill');
  if(pill)pill.textContent=`${nwData.healthScore||0}分`;
  // 更新三栏 splits
  const bk=document.getElementById('assetPageBuckets');
  if(bk&&nwData.breakdown){const b=nwData.breakdown;
    const inv=(b.investment||{}).total||0;
    const prop=(b.property||{}).total||0;const cash=(b.cash||{}).total||0;const car=(b.car||{}).total||0;
    const ins=(b.insurance||{}).total||0;const other=(b.other||{}).total||0;
    const liab=(b.liability||{}).total||0;
    const realAssets=prop+car+ins+other+cash;
    bk.innerHTML=`
      <div class="mb-hero__split"><div class="mb-hero__split-label">📈 投资</div><div class="mb-hero__split-value">¥${fmtMoney(Math.round(inv))}</div></div>
      <div class="mb-hero__split"><div class="mb-hero__split-label">🏠 实物</div><div class="mb-hero__split-value">¥${fmtMoney(Math.round(realAssets))}</div></div>
      <div class="mb-hero__split"><div class="mb-hero__split-label">💳 负债</div><div class="mb-hero__split-value mb-hero__split-value--dn">-¥${fmtMoney(Math.round(liab))}</div></div>`;
    // 更新 6 类网格的分类金额
    const catMap={property:prop,cash:cash,insurance:ins,car:car,other:other,liability:liab};
    Object.entries(catMap).forEach(([key,val])=>{
      const catEl=document.getElementById('catVal'+key.charAt(0).toUpperCase()+key.slice(1));
      if(catEl)catEl.textContent=val>0?'¥'+fmtMoney(Math.round(val)):'未添加';
    });
  }
} else {
  // 后端不可用 → 本地计算
  const assetTotal=assets.filter(a=>a.type!=='liability').reduce((s,a)=>s+(a.value||0),0);
  const liabTotal=assets.filter(a=>a.type==='liability').reduce((s,a)=>s+(a.value||0),0);
  const el=document.getElementById('assetPageNW');if(el)el.textContent=`¥${fmtMoney(Math.round(assetTotal-liabTotal))}`;
  const hel=document.getElementById('assetPageHealth');if(hel)hel.textContent='⚠️ 离线模式（不含投资持仓）';
  // 本地数据更新 6 类网格
  ASSET_TYPES.forEach(t=>{
    const total=assets.filter(a=>a.type===t.id).reduce((s,a)=>s+(a.value||0),0);
    const catEl=document.getElementById('catVal'+t.id.charAt(0).toUpperCase()+t.id.slice(1));
    if(catEl)catEl.textContent=total>0?'¥'+fmtMoney(Math.round(total)):'未添加';
  });
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

function showAddAsset(preType){
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">➕ 添加资产</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
<div class="form-row"><div class="form-label">类型</div>
<select class="form-select" id="assetType">${ASSET_TYPES.map(t=>`<option value="${t.id}"${preType&&t.id===preType?' selected':''}>${t.icon} ${t.label}</option>`).join('')}</select></div>
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
<div class="form-row"><div class="form-label">类型</div>
<select class="form-select" id="editAssetType">${ASSET_TYPES.map(t=>`<option value="${t.id}"${t.id===a.type?' selected':''}>${t.icon} ${t.label}</option>`).join('')}</select></div>
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
a.type=document.getElementById('editAssetType')?.value||a.type;
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

// 我的记录：异步加载最近 3 条分析历史
async function loadAssetPageHistory(){
  const el=document.getElementById('assetPageHistory');if(!el)return;
  const uid=getProfileId();
  try{
    const r=await fetch(`/api/analysis/history?userId=${uid}&source=&days=30`);
    const d=await r.json();
    if(!d.records||d.records.length===0){
      el.innerHTML='<div style="padding:12px;text-align:center;color:var(--text2);font-size:12px">暂无分析记录</div>';
      return;
    }
    const recent=d.records.slice(0,3);
    el.innerHTML=recent.map(rec=>{
      const srcIcon=rec.source==='deepseek'?'🤖':rec.source==='night_worker'?'🌙':'📝';
      const time=rec.created_at?.slice(0,16).replace('T',' ')||'';
      // 清理 preview：取第一行非空且有意义的文字，去掉 Markdown/emoji/括号
      const rawPreview=rec.preview||'';
      const cleanPreview=rawPreview
        .split('\n').map(l=>l.trim()).filter(l=>l&&l.length>2)
        .map(l=>l.replace(/^[#*\->\s【】\[\]「」]+/,'').replace(/[📊📈📉💡⚠️🔔🎯]+/g,'').trim())
        .filter(l=>l.length>5)[0]||rawPreview.slice(0,50);
      return `<div style="padding:8px 0;border-bottom:1px solid var(--bg3,rgba(255,255,255,.06));cursor:pointer" onclick="navigateTo('history')">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span style="font-size:12px">${srcIcon} ${rec.source_label||rec.source}</span>
          <span style="font-size:10px;color:var(--text2)">${time}</span>
        </div>
        <div style="font-size:11px;color:var(--text2);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${cleanPreview}</div>
      </div>`;
    }).join('');
  }catch(e){
    el.innerHTML='<div style="padding:12px;text-align:center;font-size:12px;color:var(--text-secondary,#9AA1AC)">暂无分析记录<div style="margin-top:8px"><button class="mb-btn mb-btn--ai mb-btn--sm" onclick="navigateTo(\'chat\')">🧠 做你的第一次 AI 分析</button></div></div>';
  }
}
