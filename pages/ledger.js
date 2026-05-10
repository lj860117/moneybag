// ---- 记账页 ----
function renderLedger(){currentPage='ledger';renderNav();const entries=loadLedger();const sources=loadSources();
const totalExpense=entries.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
const totalIncome=entries.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
const netFlow=totalIncome-totalExpense;
// 本月统计
const now=new Date();const monthStart=new Date(now.getFullYear(),now.getMonth(),1).toISOString();
const monthEntries=entries.filter(e=>e.date>=monthStart);
const monthIncome=monthEntries.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
const monthExpense=monthEntries.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
// 按方向+分类汇总
const expCat={},incCat={};
entries.forEach(e=>{const c=e.category||'其他';const a=e.amount||0;if(e.direction==='income'){incCat[c]=(incCat[c]||0)+a}else{expCat[c]=(expCat[c]||0)+a}});
const curIcons=ledgerDirection==='income'?INCOME_ICONS:EXPENSE_ICONS;
// 收入源本月入账情况
const monthSourceIds=new Set(monthEntries.filter(e=>e.sourceId).map(e=>e.sourceId));

$('#app').innerHTML=`<div class="ledger-page fade-up">
<div class="ledger-header">
<div style="display:flex;gap:16px;justify-content:center;margin-bottom:8px">
<div style="text-align:center"><div style="font-size:12px;color:var(--text2)">总收入</div><div style="font-size:18px;font-weight:700;color:var(--green)">+${fmtFull(Math.round(totalIncome))}</div></div>
<div style="text-align:center"><div style="font-size:12px;color:var(--text2)">总支出</div><div style="font-size:18px;font-weight:700;color:var(--red)">-${fmtFull(Math.round(totalExpense))}</div></div>
<div style="text-align:center"><div style="font-size:12px;color:var(--text2)">结余</div><div style="font-size:18px;font-weight:700;color:${netFlow>=0?'var(--green)':'var(--red)'}">${netFlow>=0?'+':''}${fmtFull(Math.round(netFlow))}</div></div>
</div>
<div style="display:flex;gap:12px;justify-content:center;font-size:12px;color:var(--text2);padding:6px 0;border-top:1px solid var(--border)">
<span>本月收入 <b style="color:var(--green)">+¥${Math.round(monthIncome)}</b></span>
<span>本月支出 <b style="color:var(--red)">-¥${Math.round(monthExpense)}</b></span>
</div>
<div style="font-size:13px;color:var(--text2)">${entries.length}笔记录</div></div>

${sources.length?`<div class="section-title" style="display:flex;align-items:center;justify-content:space-between">📋 我的收入源<button style="background:none;border:none;color:var(--accent);font-size:13px;cursor:pointer" onclick="showAddSource()">+ 添加</button></div>
<div style="display:flex;flex-direction:column;gap:8px;margin-bottom:16px">
${sources.map(s=>{
const icon=SOURCE_TYPE_ICONS[s.type]||'💵';
const recorded=monthSourceIds.has(s.id);
const lastT=s.lastRecordAt?new Date(s.lastRecordAt).toLocaleDateString('zh-CN'):'从未入账';
return`<div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px;display:flex;align-items:center;gap:10px">
<div style="font-size:24px;width:36px;text-align:center">${icon}</div>
<div style="flex:1;min-width:0">
<div style="font-weight:600;font-size:14px;display:flex;align-items:center;gap:6px">${s.name}${recorded?'<span style="font-size:10px;background:rgba(16,185,129,.15);color:var(--green);padding:1px 6px;border-radius:4px">本月已入账</span>':''}</div>
<div style="font-size:12px;color:var(--text2)">${s.type} · 预期¥${s.expectedAmt}/月 · ${lastT}</div>
${s.recordCount?`<div style="font-size:11px;color:var(--text2)">累计${s.recordCount}次 · ¥${Math.round(s.totalRecorded)}</div>`:''}
</div>
<div style="display:flex;gap:6px;flex-shrink:0">
<button style="background:${recorded?'var(--border)':'var(--green)'};border:none;color:${recorded?'var(--text2)':'#fff'};padding:6px 12px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer" onclick="quickRecord('${s.id}')">${recorded?'再记一笔':'💰 入账'}</button>
<button style="background:none;border:none;color:var(--text2);font-size:16px;cursor:pointer;padding:4px" onclick="if(confirm('删除收入源「${s.name}」？')){deleteSource('${s.id}');renderLedger()}">🗑️</button>
</div></div>`}).join('')}
</div>`:`<div class="section-title" style="display:flex;align-items:center;justify-content:space-between">📋 我的收入源<button style="background:none;border:none;color:var(--accent);font-size:13px;cursor:pointer" onclick="showAddSource()">+ 添加</button></div>
<div style="background:var(--card);border:1px dashed var(--border);border-radius:12px;padding:20px;text-align:center;margin-bottom:16px;cursor:pointer" onclick="showAddSource()">
<div style="font-size:24px;margin-bottom:4px">🏡💻🔧</div>
<div style="font-size:13px;color:var(--text2)">登记你的收入来源（民宿/出租房/外包/兼职…）<br>登记一次，以后每月一键入账</div>
</div>`}

<div id="addSourceForm" style="display:none"></div>

${Object.keys(expCat).length||Object.keys(incCat).length?`
<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
${Object.entries(incCat).map(([c,a])=>`<div class="ledger-cat" style="border-color:rgba(16,185,129,.3);background:rgba(16,185,129,.06)"><div class="ledger-cat-icon">${INCOME_ICONS[c]||'💵'}</div><div class="ledger-cat-name">${c}</div><div class="ledger-cat-amt" style="color:var(--green)">+¥${Math.round(a)}</div></div>`).join('')}
${Object.entries(expCat).map(([c,a])=>`<div class="ledger-cat"><div class="ledger-cat-icon">${EXPENSE_ICONS[c]||'📌'}</div><div class="ledger-cat-name">${c}</div><div class="ledger-cat-amt">¥${Math.round(a)}</div></div>`).join('')}
</div>`:''
}
<div class="section-title">📸 拍照/截图记账</div>
<div class="upload-area" onclick="document.getElementById('rcptFile').click()"><div class="icon">📷</div><div class="text">拍照或上传截图，AI自动识别入账<br><span style="font-size:12px;color:var(--text2)">支持：支付宝/微信账单、银行流水截图、消费小票</span></div><input type="file" id="rcptFile" accept="image/*" style="display:none" onchange="handleReceipt(this)"></div><div id="ocrRes"></div>
<div class="section-title">✏️ 手动记一笔</div>
<div class="manual-form">
<div style="display:flex;gap:0;margin-bottom:12px;border-radius:8px;overflow:hidden;border:1px solid var(--border)">
<button id="btnExpense" style="flex:1;padding:10px;border:none;font-size:14px;font-weight:600;cursor:pointer;transition:all .2s;background:${ledgerDirection==='expense'?'var(--red)':'transparent'};color:${ledgerDirection==='expense'?'#fff':'var(--text2)'}" onclick="switchLedgerDir('expense')">💸 支出</button>
<button id="btnIncome" style="flex:1;padding:10px;border:none;font-size:14px;font-weight:600;cursor:pointer;transition:all .2s;background:${ledgerDirection==='income'?'var(--green)':'transparent'};color:${ledgerDirection==='income'?'#fff':'var(--text2)'}" onclick="switchLedgerDir('income')">💰 收入</button>
</div>
<div class="form-row"><div class="form-label">金额</div><input class="form-input" type="number" id="ldgAmt" placeholder="0.00" inputmode="decimal"></div>
<div class="form-row"><div class="form-label">分类</div><select class="form-select" id="ldgCat">${Object.keys(curIcons).map(c=>`<option value="${c}">${curIcons[c]} ${c}</option>`).join('')}</select></div>
<div class="form-row"><div class="form-label">备注</div><input class="form-input" type="text" id="ldgNote" placeholder="${ledgerDirection==='income'?'收入来源...':'买了什么...'}"></div>
<button class="form-submit" style="background:${ledgerDirection==='income'?'var(--green)':'var(--accent)'}" onclick="addEntry()">${ledgerDirection==='income'?'💰 记一笔收入':'💸 记一笔支出'}</button></div>
${entries.length?`<div class="section-title">📋 最近记录</div>${entries.slice(-30).reverse().map(e=>{
const isInc=e.direction==='income';
const icon=isInc?(INCOME_ICONS[e.category]||'💵'):(EXPENSE_ICONS[e.category]||'📌');
return`<div class="ledger-entry"><div class="ledger-entry-icon">${icon}</div><div class="ledger-entry-info"><div class="ledger-entry-note">${e.note||e.category}${isInc?' <span style="font-size:11px;background:rgba(16,185,129,.15);color:var(--green);padding:1px 6px;border-radius:4px">收入</span>':''}${e.source==='income_source'?' <span style="font-size:11px;background:rgba(99,102,241,.15);color:#818cf8;padding:1px 6px;border-radius:4px">定期</span>':''}</div><div class="ledger-entry-date">${new Date(e.date).toLocaleString('zh-CN')}</div></div><div class="ledger-entry-amt" style="color:${isInc?'var(--green)':'var(--red)'}">${isInc?'+':'-'}¥${e.amount.toFixed(2)}</div></div>`}).join('')}`:'<div style="text-align:center;color:var(--text2);padding:32px">还没有记录，拍张小票试试📸</div>'}
${entries.length?'<div class="bottom-actions" style="margin-top:20px"><button class="action-btn secondary" onclick="clearLedger()">🗑️清除记录</button></div>':''}</div>`}

function showAddSource(){
const f=document.getElementById('addSourceForm');if(!f)return;
const types=Object.keys(SOURCE_TYPE_ICONS);
f.style.display='block';
f.innerHTML=`<div style="background:var(--card);border:1px solid var(--accent);border-radius:12px;padding:16px;margin-bottom:16px">
<div style="font-weight:700;font-size:14px;margin-bottom:12px">➕ 登记新收入源</div>
<div class="form-row"><div class="form-label">名称</div><input class="form-input" type="text" id="srcName" placeholder="如：朝阳区民宿、张三外包、滴滴兼职..."></div>
<div class="form-row"><div class="form-label">类型</div><select class="form-select" id="srcType">${types.map(t=>`<option value="${t}">${SOURCE_TYPE_ICONS[t]} ${t}</option>`).join('')}</select></div>
<div class="form-row"><div class="form-label">预期月收入</div><input class="form-input" type="number" id="srcAmt" placeholder="每月大概赚多少" inputmode="decimal"></div>
<div class="form-row"><div class="form-label">备注</div><input class="form-input" type="text" id="srcNote" placeholder="可选"></div>
<div style="display:flex;gap:8px;margin-top:12px">
<button class="form-submit" style="flex:1;background:var(--green)" onclick="confirmAddSource()">✅ 登记</button>
<button class="form-submit" style="flex:1;background:var(--border);color:var(--text2)" onclick="document.getElementById('addSourceForm').style.display='none'">取消</button>
</div></div>`}

function confirmAddSource(){
const name=document.getElementById('srcName')?.value?.trim();
const type=document.getElementById('srcType')?.value;
const amt=document.getElementById('srcAmt')?.value;
const note=document.getElementById('srcNote')?.value;
if(!name){alert('请输入收入源名称');return}
addSource(name,type,amt,note);
renderLedger()}

function switchLedgerDir(dir){ledgerDirection=dir;renderLedger()}

async function handleReceipt(input){if(!input.files||!input.files[0])return;const file=input.files[0];const re=document.getElementById('ocrRes');if(!re)return;
re.innerHTML='<div style="text-align:center;padding:16px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:3px"></div>AI识别中...</div>';
if(API_AVAILABLE){try{const fd=new FormData();fd.append('file',file);fd.append('userId',getUserId());const r=await fetch(API_BASE+'/receipt/ocr',{method:'POST',body:fd});if(r.ok){const d=await r.json();if(d.amount>0){const es=loadLedger();es.push({date:new Date().toISOString(),amount:d.amount,category:d.category||'其他',note:d.merchant||d.note||'OCR',source:'ocr'});saveLedger(es);re.innerHTML=`<div style="background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.3);border-radius:12px;padding:16px;margin-bottom:16px"><div style="font-weight:700;color:var(--green);margin-bottom:8px">✅ 已入账</div><div style="font-size:13px;color:var(--text2)">¥${d.amount.toFixed(2)} · ${d.category||'其他'}${d.merchant?' · '+d.merchant:''}</div></div>`;setTimeout(()=>renderLedger(),2000);return}}}catch(e){console.error(e)}}
re.innerHTML=`<div style="background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);border-radius:12px;padding:16px"><div style="color:var(--red)">识别失败</div><div style="font-size:13px;color:var(--text2);margin-top:4px">${API_AVAILABLE?'无法识别，请手动输入':'后端离线，请手动输入'}</div></div>`}

function addEntry(){const a=parseFloat(document.getElementById('ldgAmt')?.value);const c=document.getElementById('ldgCat')?.value||'其他';const n=document.getElementById('ldgNote')?.value||'';if(!a||a<=0){alert('请输入金额');return}
const es=loadLedger();es.push({date:new Date().toISOString(),amount:a,category:c,note:n,direction:ledgerDirection,source:'manual'});saveLedger(es);
if(API_AVAILABLE)fetch(API_BASE+'/ledger/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),amount:a,category:c,note:n,direction:ledgerDirection})}).catch(()=>{});
renderLedger()}
function clearLedger(){if(confirm('确定清除所有记账记录？')){localStorage.removeItem(LEDGER_KEY);renderLedger()}}

function toggleLedgerPanel(){
const panel=document.getElementById('ledgerPanelInAssets');
if(!panel)return;
if(panel.style.display!=='none'){panel.style.display='none';return}
panel.style.display='block';
const entries=loadLedger();const sources=loadSources();
const totalIncome=entries.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
const totalExpense=entries.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
const curIcons=ledgerDirection==='income'?INCOME_ICONS:EXPENSE_ICONS;
const now=new Date();const monthStart=new Date(now.getFullYear(),now.getMonth(),1).toISOString();
const monthEntries=entries.filter(e=>e.date>=monthStart);
const monthIncome=monthEntries.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
const monthExpense=monthEntries.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
const monthSourceIds=new Set(monthEntries.filter(e=>e.sourceId).map(e=>e.sourceId));

panel.innerHTML=`<div style="background:var(--card);border:1px solid var(--border);border-radius:16px;padding:16px">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
<div style="font-weight:700;font-size:16px">📝 记账</div>
<button style="background:none;border:none;color:var(--text2);font-size:18px;cursor:pointer;padding:4px" onclick="document.getElementById('ledgerPanelInAssets').style.display='none'">✕</button>
</div>
<div style="display:flex;gap:12px;justify-content:center;margin-bottom:12px;font-size:12px;color:var(--text2)">
<span>本月收入 <b style="color:var(--green)">+¥${Math.round(monthIncome)}</b></span>
<span>本月支出 <b style="color:var(--red)">-¥${Math.round(monthExpense)}</b></span>
<span>总结余 <b style="color:${totalIncome-totalExpense>=0?'var(--green)':'var(--red)'}">¥${Math.round(totalIncome-totalExpense)}</b></span>
</div>

${sources.length?`<div style="margin-bottom:12px">
<div style="font-size:13px;font-weight:600;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">📋 收入源<button style="background:none;border:none;color:var(--accent);font-size:12px;cursor:pointer" onclick="showAddSource()">+ 添加</button></div>
${sources.map(s=>{const icon=SOURCE_TYPE_ICONS[s.type]||'💵';const recorded=monthSourceIds.has(s.id);
return`<div style="display:flex;align-items:center;gap:8px;padding:8px;background:var(--bg2);border-radius:8px;margin-bottom:4px">
<span>${icon}</span><span style="flex:1;font-size:13px">${s.name}</span>
<button style="background:${recorded?'var(--border)':'var(--green)'};border:none;color:${recorded?'var(--text2)':'#fff'};padding:4px 10px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer" onclick="quickRecord('${s.id}')">${recorded?'再记':'💰 入账'}</button>
<button style="background:none;border:none;color:var(--text2);font-size:14px;cursor:pointer" onclick="if(confirm('删除收入源「${s.name}」？')){deleteSource('${s.id}');toggleLedgerPanel();toggleLedgerPanel()}">🗑️</button>
</div>`}).join('')}
</div>`:
`<div style="text-align:center;padding:8px;margin-bottom:8px;border:1px dashed var(--border);border-radius:8px;cursor:pointer;font-size:12px;color:var(--text2)" onclick="showAddSource()">🏡💻🔧 登记收入源（一键入账）</div>`}

<div style="margin-bottom:12px">
<div style="font-size:13px;font-weight:600;margin-bottom:8px">📸 拍照/截图记账</div>
<div class="upload-area" style="padding:12px" onclick="document.getElementById('rcptFileAsset').click()"><div class="icon" style="font-size:20px">📷</div><div class="text" style="font-size:12px">拍照或上传截图，AI自动识别<br><span style="font-size:11px;color:var(--text2)">支付宝/微信/银行流水</span></div><input type="file" id="rcptFileAsset" accept="image/*" style="display:none" onchange="handleReceipt(this)"></div><div id="ocrRes"></div>
</div>

<div>
<div style="font-size:13px;font-weight:600;margin-bottom:8px">✏️ 手动记一笔</div>
<div style="display:flex;gap:0;margin-bottom:8px;border-radius:8px;overflow:hidden;border:1px solid var(--border)">
<button id="btnExpenseAsset" style="flex:1;padding:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;background:${ledgerDirection==='expense'?'var(--red)':'transparent'};color:${ledgerDirection==='expense'?'#fff':'var(--text2)'}" onclick="ledgerDirection='expense';toggleLedgerPanel();toggleLedgerPanel()">💸 支出</button>
<button id="btnIncomeAsset" style="flex:1;padding:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;background:${ledgerDirection==='income'?'var(--green)':'transparent'};color:${ledgerDirection==='income'?'#fff':'var(--text2)'}" onclick="ledgerDirection='income';toggleLedgerPanel();toggleLedgerPanel()">💰 收入</button>
</div>
<div class="form-row" style="margin-bottom:8px"><input class="form-input" type="number" id="ldgAmtAsset" placeholder="金额" inputmode="decimal" style="padding:10px"></div>
<div style="display:flex;gap:8px;margin-bottom:8px">
<select class="form-select" id="ldgCatAsset" style="flex:1;padding:10px">${Object.keys(curIcons).map(c=>`<option value="${c}">${curIcons[c]} ${c}</option>`).join('')}</select>
<input class="form-input" type="text" id="ldgNoteAsset" placeholder="备注" style="flex:1;padding:10px">
</div>
<button class="form-submit" style="width:100%;background:${ledgerDirection==='income'?'var(--green)':'var(--accent)'}" onclick="addEntryFromAsset()">${ledgerDirection==='income'?'💰 记一笔收入':'💸 记一笔支出'}</button>
</div>

${entries.length?`<div style="margin-top:12px"><div style="font-size:13px;font-weight:600;margin-bottom:8px">📋 最近记录 (${entries.length}笔)</div>
${entries.slice(-10).reverse().map(e=>{const isInc=e.direction==='income';const icon=isInc?(INCOME_ICONS[e.category]||'💵'):(EXPENSE_ICONS[e.category]||'📌');
return`<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--bg3);font-size:13px">
<span>${icon}</span><span style="flex:1">${e.note||e.category}</span>
<span style="font-weight:700;color:${isInc?'var(--green)':'var(--red)'}">${isInc?'+':'-'}¥${e.amount.toFixed(2)}</span>
<span style="font-size:10px;color:var(--text2)">${new Date(e.date).toLocaleDateString('zh-CN')}</span></div>`}).join('')}
${entries.length>10?`<div style="text-align:center;padding:8px;font-size:12px;color:var(--text2)">还有${entries.length-10}笔...</div>`:''}
<button style="width:100%;margin-top:8px;padding:8px;border-radius:8px;border:1px solid var(--border);background:transparent;color:var(--red);font-size:12px;cursor:pointer" onclick="if(confirm('确定清除所有记账记录？')){localStorage.removeItem('${LEDGER_KEY}');renderAssets()}">🗑️ 清除记录</button>
</div>`:''}
</div>`}

function addEntryFromAsset(){
const a=parseFloat(document.getElementById('ldgAmtAsset')?.value);
const c=document.getElementById('ldgCatAsset')?.value||'其他';
const n=document.getElementById('ldgNoteAsset')?.value||'';
if(!a||a<=0){alert('请输入金额');return}
const es=loadLedger();es.push({date:new Date().toISOString(),amount:a,category:c,note:n,direction:ledgerDirection,source:'manual'});saveLedger(es);
if(API_AVAILABLE)fetch(API_BASE+'/ledger/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),amount:a,category:c,note:n,direction:ledgerDirection})}).catch(()=>{});
renderAssets();setTimeout(()=>{toggleLedgerPanel()},100)}

