function renderHistory(){currentPage='history';renderNav();
$('#app').innerHTML=`<div class="insight-page fade-up"><div class="insight-header"><h2>📋 分析历史</h2><p>AI/Claude/机构 分析记录</p></div>
<div class="section-tab-bar"><button class="section-tab ${historyTab==='all'?'active':''}" onclick="historyTab='all';loadHistory()">全部</button><button class="section-tab ${historyTab==='deepseek'?'active':''}" onclick="historyTab='deepseek';loadHistory()">DeepSeek</button><button class="section-tab ${historyTab==='claude'?'active':''}" onclick="historyTab='claude';loadHistory()">Claude</button><button class="section-tab ${historyTab==='broker'?'active':''}" onclick="historyTab='broker';loadHistory()">机构</button></div>
<div id="historyContent"><div style="text-align:center;padding:40px;color:var(--text2)"><div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 12px;border-width:3px"></div>加载中...</div></div></div>`;
loadHistory()}

async function loadHistory(){const el=document.getElementById('historyContent');if(!el)return;
const uid=getProfileId();const src=historyTab==='all'?'':historyTab;
try{const r=await fetch(`/api/analysis/history?userId=${uid}&source=${src}&days=30`);const d=await r.json();
if(!d.records||d.records.length===0){el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">暂无分析记录<br><span style="font-size:12px">AI分析后会自动存档</span></div>';return}
let html='';let lastDate='';
d.records.forEach(rec=>{const date=rec.created_at?.slice(0,10)||'';if(date!==lastDate){html+=`<div style="font-size:13px;font-weight:700;color:var(--text2);margin:16px 0 8px;padding-left:4px">${date}</div>`;lastDate=date}
const dirDisplay=rec.direction_display||translateDirection(rec.direction)||rec.direction||'';const typeDisplay=rec.type_display||translateAnalysisType(rec.type)||rec.type||'';const dirColor=rec.direction==='看多'||rec.direction==='bullish'||dirDisplay==='看多'?'var(--bull,#22c55e)':rec.direction==='看空'||rec.direction==='bearish'||dirDisplay==='看空'?'var(--bear,#ef4444)':'var(--text2)';
const srcIcon=rec.source==='deepseek'?'🤖':rec.source==='claude'?'🧠':rec.source==='broker'?'🏛️':'📝';
html+=`<div class="card" style="margin-bottom:8px;padding:12px;cursor:pointer" onclick="showAnalysisDetail('${rec.id}')">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
<div style="font-size:14px;font-weight:600">${srcIcon} ${rec.source_label||rec.source} <span style="font-size:11px;font-weight:400;color:var(--text2)">${typeDisplay}</span></div>
<div style="font-size:11px;color:var(--text2)">${rec.created_at?.slice(11,16)||''}</div></div>
<div style="display:flex;gap:8px;margin-bottom:6px"><span style="font-size:12px;color:${dirColor};font-weight:600">${dirDisplay}</span>${rec.confidence?'<span style="font-size:11px;color:var(--text2)">置信度:'+rec.confidence+'%</span>':''}</div>
<div style="font-size:12px;color:var(--text2);line-height:1.5">${rec.preview||''}</div>
</div>`});
el.innerHTML=html+'<div style="text-align:center;padding:16px"><button class="action-btn secondary" onclick="showCompareView()" style="font-size:13px">📊 多源对比</button></div>'}catch(e){el.innerHTML='<div style="text-align:center;padding:20px;color:#ef4444">加载失败: '+e.message+'</div>'}}

async function showAnalysisDetail(id){const uid=getProfileId();
try{const r=await fetch(`/api/analysis/detail/${id}?userId=${uid}`);const d=await r.json();
if(d.error){alert(d.error);return}
const snap=d.market_snapshot||{};
const snapHtml=Object.keys(snap).length?`<div style="margin-top:12px;padding:8px;background:var(--bg2,rgba(0,0,0,.05));border-radius:8px;font-size:11px;color:var(--text2)">📊 分析时市场: 恐贪=${snap.fear_greed||'-'} | 估值=${snap.valuation_pct||'-'}% | 北向5日=${snap.north_5d||'-'}亿</div>`:'';
const modal=document.createElement('div');modal.className='modal-overlay';modal.onclick=e=>{if(e.target===modal)modal.remove()};
modal.innerHTML=`<div class="modal-content" style="max-height:80vh;overflow-y:auto">
<div class="modal-title">${d.source_label||d.source} · ${d.type_display||translateAnalysisType(d.type)||d.type||'分析'} <span style="font-size:12px;font-weight:400;color:var(--text2)">${d.created_at?.slice(0,16)||''}</span></div>
<div style="margin:8px 0;font-size:13px;color:var(--accent)">${d.direction_display||translateDirection(d.direction)||d.direction||''} ${d.confidence?'置信度'+d.confidence+'%':''}</div>
<div style="font-size:13px;line-height:1.8;white-space:pre-wrap">${d.analysis||'无内容'}</div>
${snapHtml}
<button class="form-submit" style="margin-top:16px" onclick="this.closest('.modal-overlay').remove()">关闭</button>
</div>`;document.body.appendChild(modal)}catch(e){alert('加载失败: '+e.message)}}

async function showCompareView(){const uid=getProfileId();
try{const r=await fetch(`/api/analysis/latest?userId=${uid}`);const d=await r.json();
const sources=d.sources||{};const keys=Object.keys(sources);
if(keys.length===0){alert('暂无分析记录');return}
let tabsHtml=keys.map((k,i)=>`<button class="section-tab ${i===0?'active':''}" onclick="switchCompareTab(this,'${k}')">${sources[k].source_label||k}</button>`).join('');
let panelsHtml=keys.map((k,i)=>`<div class="compare-panel" data-source="${k}" style="${i>0?'display:none':''}">
<div style="font-size:13px;color:var(--accent);margin-bottom:8px">${sources[k].direction_display||translateDirection(sources[k].direction)||sources[k].direction||'未知'} · ${sources[k].created_at?.slice(0,16)||''}</div>
<div style="font-size:13px;line-height:1.8">${sources[k].preview||'无预览'}</div>
</div>`).join('');
let summaryHtml='<div style="margin-top:16px;padding:12px;background:var(--bg2,rgba(0,0,0,.05));border-radius:8px"><div style="font-size:13px;font-weight:600;margin-bottom:8px">📊 分歧汇总</div>';
keys.forEach(k=>{summaryHtml+=`<div style="font-size:12px;margin-bottom:4px">${sources[k].source_label||k}: ${sources[k].direction_display||translateDirection(sources[k].direction)||sources[k].direction||'未知'}</div>`});
summaryHtml+='</div>';
const modal=document.createElement('div');modal.className='modal-overlay';modal.onclick=e=>{if(e.target===modal)modal.remove()};
modal.innerHTML=`<div class="modal-content" style="max-height:80vh;overflow-y:auto">
<div class="modal-title">📊 多源分析对比</div>
<div class="section-tab-bar" style="margin-bottom:12px">${tabsHtml}</div>
${panelsHtml}${summaryHtml}
<button class="form-submit" style="margin-top:16px" onclick="this.closest('.modal-overlay').remove()">关闭</button>
</div>`;document.body.appendChild(modal)}catch(e){alert('加载失败: '+e.message)}}

function switchCompareTab(btn,src){btn.parentElement.querySelectorAll('.section-tab').forEach(b=>b.classList.remove('active'));btn.classList.add('active');
btn.closest('.modal-content').querySelectorAll('.compare-panel').forEach(p=>{p.style.display=p.dataset.source===src?'':'none'})}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// V6 补齐：行业热点 + 研报共识 + 情景分析 + 大宗商品
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async function renderSectorHot(el){
el.innerHTML='<div style="text-align:center;padding:20px"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载行业数据...</div>';
// ★ 缓存检查
const cached=getCached('sector');if(cached){el.innerHTML=cached;return}
try{
const r=await fetch('/api/market-factors/all',{signal:AbortSignal.timeout(15000)});const d=await r.json();
// API 实际返回 {commodities, unlock, etf_flow, updatedAt}，不含 sector_rotation
// 用 etf_flow.top_inflow 展示 ETF 资金流向作为行业偏好信号
const etf=d.etf_flow||{};
const inflow=etf.top_inflow||[];
const outflow=etf.top_outflow||[];
const note=d._weekend_note||'';
let html='';
// ETF 资金流向
if(inflow.length||outflow.length){
  html+='<div class="dashboard-card" style="border-left:3px solid var(--accent)"><div class="dashboard-card-title">💹 ETF 资金流向（行业偏好信号）</div><div style="font-size:12px;color:var(--text2);margin-bottom:8px">机构资金净流入 ETF 反映行业配置偏好</div></div>';
  if(inflow.length){
    html+='<div class="dashboard-card"><div class="dashboard-card-title">📈 净流入 TOP ETF</div>';
    inflow.slice(0,10).forEach((s,i)=>{
      const name=s.name||s.fund_name||'';const flow=s.flow||s.net_inflow||0;
      html+=`<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border,rgba(255,255,255,.05))"><span style="font-size:13px">${i+1}. ${name}<span style="font-size:10px;color:var(--text2);margin-left:4px">${s.code||''}</span></span><span style="font-size:13px;font-weight:700;color:var(--bull,#22c55e)">+${typeof flow==='number'?flow.toFixed(2):flow}亿</span></div>`;
    });
    html+='</div>';
  }
  if(outflow.length){
    html+='<div class="dashboard-card"><div class="dashboard-card-title">📉 净流出 ETF</div>';
    outflow.slice(0,5).forEach((s,i)=>{
      const name=s.name||s.fund_name||'';const flow=s.flow||s.net_inflow||0;
      html+=`<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border,rgba(255,255,255,.05))"><span style="font-size:13px">${i+1}. ${name}</span><span style="font-size:13px;font-weight:700;color:var(--bear,#ef4444)">${typeof flow==='number'?flow.toFixed(2):flow}亿</span></div>`;
    });
    html+='</div>';
  }
}else{
  html+='<div style="text-align:center;padding:40px;color:var(--text2)">'+(note?'<div style="font-size:14px;margin-bottom:8px">📅</div><div style="font-size:13px;line-height:1.6">'+note+'</div>':'ETF 资金流向数据暂无更新')+'<br><button onclick="renderSectorHot(document.getElementById(&quot;insightContent&quot;))" style="margin-top:12px;padding:6px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>';
}
// 大宗商品（使用 all 接口已返回的 commodities 数据，不重复请求）
const cd=d.commodities||{};
if(cd.available||cd.gold){
  html+='<div class="dashboard-card"><div class="dashboard-card-title">🛢️ 大宗商品</div>';
  if(cd.gold)html+=`<div style="display:flex;justify-content:space-between;padding:6px 0"><span>🥇 黄金</span><span style="font-weight:700">${cd.gold.price}${cd.gold.unit||''} <span style="color:${(cd.gold.change_pct||0)>=0?'var(--bull)':'var(--bear)'}">${(cd.gold.change_pct||0)>=0?'+':''}${(cd.gold.change_pct||0).toFixed(1)}%</span></span></div>`;
  if(cd.copper)html+=`<div style="display:flex;justify-content:space-between;padding:6px 0"><span>🔶 铜</span><span style="font-weight:700">${cd.copper.price}${cd.copper.unit||''} <span style="color:${(cd.copper.change_pct||0)>=0?'var(--bull)':'var(--bear)'}">${(cd.copper.change_pct||0)>=0?'+':''}${(cd.copper.change_pct||0).toFixed(1)}%</span></span></div>`;
  html+='</div>';
}
html=html||'<div style="text-align:center;padding:20px;color:var(--text2)">暂无行业数据</div>';
// ★ 缓存设置
setCached('sector',html);
el.innerHTML=html;
}catch(e){el.innerHTML='<div style="text-align:center;padding:20px;color:#ef4444">加载失败: '+e.message+'</div>'}}

async function renderBrokerView(el){
el.innerHTML='<div style="text-align:center;padding:20px"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载研报数据...</div>';
// ★ 缓存检查
const cached=getCached('broker');if(cached){el.innerHTML=cached;return}
try{
const[cr,lr]=await Promise.all([fetch('/api/broker/consensus').then(r=>r.json()),fetch('/api/broker/latest?limit=15').then(r=>r.json())]);
let html='';
if(cr.available){
const bc=cr.bullish_count||0,brc=cr.bearish_count||0,nc=cr.neutral_count||0;
const consColor=cr.consensus==='看多'||cr.consensus==='谨慎乐观'?'var(--bull,#22c55e)':cr.consensus==='看空'||cr.consensus==='偏空'?'var(--bear,#ef4444)':'var(--text2)';
html+=`<div class="dashboard-card" style="border-left:3px solid ${consColor}"><div class="dashboard-card-title">🏛️ 机构研报共识</div>
<div style="font-size:20px;font-weight:800;color:${consColor};margin:8px 0">${cr.consensus}</div>
<div style="font-size:12px;color:var(--text2)">看多:${bc} | 看空:${brc} | 中性:${nc} | 共${cr.total_reports||0}篇</div>
${cr.hot_sectors?.length?'<div style="margin-top:8px;font-size:12px">🔥 热门行业：'+cr.hot_sectors.map(s=>s.name).join('、')+'</div>':''}
${cr.key_risks?.length?'<div style="margin-top:4px;font-size:12px;color:var(--bear)">⚠️ 关键风险：'+cr.key_risks.join('、')+'</div>':''}
</div>`}
const reports=lr.reports||[];
if(reports.length){html+='<div class="dashboard-card"><div class="dashboard-card-title">📄 最新研报</div>';
reports.slice(0,10).forEach(r=>{
// 优先用研报自带 url，否则跳转到东方财富搜索
const url=r.url||r.link||(r.title?`https://so.eastmoney.com/web/s?keyword=${encodeURIComponent(r.title)}`:'');
html+=`<div style="padding:8px 0;border-bottom:1px solid var(--border,rgba(255,255,255,.05));cursor:${url?'pointer':'default'}" ${url?`onclick="window.open('${url}','_blank')" title="点击查看研报"`:''}>
<div style="font-size:13px;font-weight:600;display:flex;align-items:center;gap:4px">${r.title||'无标题'}${url?'<span style="font-size:10px;color:var(--accent)">↗</span>':''}</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">${r.org||r.source||''} · ${r.date||''} ${r.rating?'· 评级:'+r.rating:''}</div>
</div>`});
html+='</div>'}
html=html||'<div style="text-align:center;padding:20px;color:var(--text2)">暂无研报数据</div>';
// ★ 缓存设置
setCached('broker',html);
el.innerHTML=html}catch(e){el.innerHTML='<div style="text-align:center;padding:20px;color:#ef4444">加载失败: '+e.message+'</div>'}}

async function renderScenarioView(el){
el.innerHTML='<div style="text-align:center;padding:20px"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载情景...</div>';
try{
const r=await fetch('/api/scenarios');const d=await r.json();
const scenarios=d.scenarios||[];
let html='<div class="dashboard-card"><div class="dashboard-card-title">🎭 情景分析引擎</div><div style="font-size:12px;color:var(--text2);margin-bottom:12px">选择一个假设情景，AI 将推演对 A 股的影响（需 R1 推理，约 30 秒）</div>';
scenarios.forEach(s=>{
html+=`<div class="card" style="margin-bottom:8px;padding:12px;cursor:pointer;border:1px solid var(--border,rgba(255,255,255,.1));border-radius:12px" onclick="runScenario('${s.id}')">
<div style="font-size:14px;font-weight:700;margin-bottom:4px">${s.name}</div>
<div style="font-size:12px;color:var(--text2)">${s.description}</div>
</div>`});
html+='<div style="margin-top:12px"><div style="font-size:12px;color:var(--text2);margin-bottom:8px">💬 或输入自定义情景：</div><div style="display:flex;gap:8px"><input id="customScenarioInput" class="form-input" placeholder="如果美联储降息100BP..." style="flex:1"><button class="action-btn primary" onclick="runCustomScenario()" style="white-space:nowrap">分析</button></div></div></div>';
el.innerHTML=html}catch(e){el.innerHTML='<div style="text-align:center;padding:20px;color:#ef4444">加载失败: '+e.message+'</div>'}}

async function runScenario(id){
const el=document.getElementById('insightContent');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:40px"><div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 12px;border-width:3px"></div><div style="color:var(--text2)">🧠 R1 深度推理中...约需 30 秒</div></div>';
try{const uid=getProfileId();const r=await fetch('/api/scenario/'+id+'?userId='+uid);const d=await r.json();
if(d.error){el.innerHTML='<div style="padding:20px;color:var(--bear)">分析失败: '+d.error+'</div>';return}
const a=d.analysis||{};
let html='<div class="dashboard-card" style="border-left:3px solid var(--accent)"><div class="dashboard-card-title">🎭 '+d.scenario?.name+'</div>';
html+='<div style="font-size:12px;color:var(--text2);margin-bottom:8px">概率: '+a.probability+' | 时间窗口: '+a.timeframe+'</div>';
if(a.transmission_chain)html+='<div style="font-size:13px;line-height:1.7;margin-bottom:12px">'+a.transmission_chain+'</div>';
if(a.sector_winners?.length)html+='<div style="font-size:12px;margin-bottom:4px">📈 受益行业：<span style="color:var(--bull)">'+a.sector_winners.join('、')+'</span></div>';
if(a.sector_losers?.length)html+='<div style="font-size:12px;margin-bottom:4px">📉 受损行业：<span style="color:var(--bear)">'+a.sector_losers.join('、')+'</span></div>';
if(a.portfolio_advice)html+='<div style="font-size:13px;margin-top:8px;padding:8px;background:rgba(59,130,246,.06);border-radius:8px">💡 '+a.portfolio_advice+'</div>';
html+='<div style="font-size:11px;color:var(--text2);margin-top:8px">模型: '+d.model+' | 置信度: '+(a.confidence||0)+'%</div>';
html+='</div><button class="action-btn secondary" onclick="renderScenarioView(document.getElementById(\'insightContent\'))" style="margin-top:12px;width:100%">← 返回情景列表</button>';
el.innerHTML=html}catch(e){el.innerHTML='<div style="padding:20px;color:var(--bear)">请求失败: '+e.message+'</div>'}}


async function renderRecommendTab(el){
el.innerHTML='<div style="text-align:center;padding:20px"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载推荐数据...</div>';
try{
const uid=getProfileId();
const r=await fetch(API_BASE+'/recommend/stocks?userId='+uid+'&topN=10&period=medium',{signal:AbortSignal.timeout(15000)});
if(!r.ok){el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">推荐数据暂不可用'+(r.status===504?' (周末数据源不活跃)':'')+'<br><button onclick="renderInsight()" style="margin-top:8px;padding:6px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer">🔄 重试</button></div>';return}
const d=await r.json();
if(d.from_cache)console.log('[推荐] 来自缓存');
const recs=d.recommendations||[];
if(!recs.length){const wn=d._weekend_note||'';el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">'+(wn?'<div style="font-size:24px;margin-bottom:8px">📅</div><div style="font-size:13px;line-height:1.6">'+wn+'</div>':'暂无推荐'+(d.error?'：'+d.error:''))+'</div>';return}
let html='<div class="dashboard-card" style="border-left:3px solid var(--accent)"><div class="dashboard-card-title">💎 AI 推荐 <span style="font-size:11px;color:var(--text2);font-weight:400">'+
(d.period_label||'中线')+'</span></div><div style="font-size:12px;color:var(--text2);margin-bottom:8px">候选'+d.pool_size+'只 → 评分'+d.scored_count+'只 → Top '+recs.length+'</div></div>';
recs.forEach((r,i)=>{
const ds=r.dimension_scores||{};const sp=r.suggested_position||{};
const scoreColor=r.total_score>=70?'var(--green)':r.total_score>=50?'var(--accent)':'var(--text2)';
html+='<div class="dashboard-card" style="padding:12px;margin-bottom:8px"><div style="display:flex;justify-content:space-between;align-items:center"><div><span style="font-size:14px;font-weight:700">'+(i+1)+'. '+r.name+'</span><span style="font-size:11px;color:var(--text2);margin-left:6px">'+r.code+'</span></div><div style="text-align:right"><div style="font-size:18px;font-weight:900;color:'+scoreColor+'">'+r.total_score+'</div><div style="font-size:10px;color:var(--text2)">'+sp.emoji+' '+sp.action+'</div></div></div>';
html+='<div style="display:flex;gap:4px;margin-top:8px;font-size:11px">'+
['估值','盈利','技术','资金','风险','题材'].map(k=>{
const key=k==='估值'?'valuation':k==='盈利'?'earnings':k==='技术'?'technical':k==='资金'?'capital':k==='风险'?'risk':'theme';
const v=ds[key]||50;const c=v>=70?'#10B981':v<=30?'#EF4444':'#94A3B8';
return '<div style="flex:1;text-align:center;padding:4px;background:var(--bg2);border-radius:6px"><div style="color:var(--text2)">'+k+'</div><div style="font-weight:700;color:'+c+'">'+v+'</div></div>'}).join('')+'</div>';
if(r.reason)html+='<div style="font-size:12px;color:var(--text2);margin-top:6px;padding:6px 8px;background:var(--bg2);border-radius:6px">'+r.reason+'</div>';
if(r.theme_tags&&r.theme_tags.length)html+='<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">'+r.theme_tags.slice(0,4).map(t=>'<span style="font-size:10px;padding:2px 7px;background:rgba(249,115,22,.12);color:#F97316;border-radius:10px;border:1px solid rgba(249,115,22,.2)">🔥 '+t+'</span>').join('')+'</div>';
html+='</div>'});
el.innerHTML=html;
}catch(e){el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">加载超时<br><span style="font-size:12px">'+e.message+'</span><br><button onclick="renderInsight()" style="margin-top:8px;padding:6px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer">🔄 重试</button></div>'}}


async function renderDecisionsTab(el){
el.innerHTML='<div style="text-align:center;padding:20px"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载决策复盘数据...</div>';
try{
const uid=getProfileId();
const r=await fetch(API_BASE+'/decisions/review/'+uid+'?limit=20',{signal:AbortSignal.timeout(15000)});
if(!r.ok){el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">决策复盘数据暂不可用<br><button onclick="renderInsight()" style="margin-top:8px;padding:6px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer">🔄 重试</button></div>';return}
const d=await r.json();
const reviews=d.reviews||[];
if(!reviews.length){el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">暂无决策复盘记录<br><div style="font-size:12px;margin-top:8px">每次交易后提交复盘，系统会分析你的决策模式</div></div>';return}
let html='<div class="dashboard-card" style="border-left:3px solid var(--accent);margin-bottom:12px"><div class="dashboard-card-title">🎯 决策复盘记录</div><div style="font-size:12px;color:var(--text2)">共 '+reviews.length+' 条复盘 · 记录越多，行为模式分析越准确</div></div>';
reviews.forEach(rev=>{
const qs=rev.quality_score||{};
const score=qs.total||0;
const grade=qs.grade||'';
const gradeLabel=grade==='excellent'?'优秀':grade==='good'?'良好':grade==='mediocre'?'一般':'较差';
const gradeColor=score>=80?'#10B981':score>=60?'#3B82F6':score>=40?'#F59E0B':'#EF4444';
const actionMap={buy:'🟢 买入',sell:'🔴 卖出',hold:'⚪ 持有',reduce:'🟠 减仓',add:'🟢 加仓'};
const actionLabel=actionMap[rev.action]||rev.action||'买入';
const time=(rev.time||rev.trade_time||'').slice(0,10);
html+='<div class="dashboard-card" style="padding:12px;margin-bottom:8px"><div style="display:flex;justify-content:space-between;align-items:center"><div><span style="font-size:14px;font-weight:700">'+(rev.asset_name||'')+'</span><span style="font-size:11px;color:var(--text2);margin-left:6px">'+(rev.asset_code||'')+'</span></div><div style="font-size:14px;font-weight:800">'+actionLabel+'</div></div>';
html+='<div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px"><div style="font-size:12px;color:var(--text2)">'+time+'</div><div style="display:flex;align-items:center;gap:6px"><span style="font-size:20px;font-weight:800;color:'+gradeColor+'">'+score+'</span><span style="font-size:11px;color:'+gradeColor+'">'+gradeLabel+'</span></div></div>';
const reasons=rev.reasons||[];
if(reasons.length){
const labels=reasons.map(r=>{const sig=r.signal;const icon=sig==='red'?'🚨':sig==='yellow'?'⚠️':'✅';return icon+(r.reason_id||r.custom_text||'').replace(/_/g,' ')}).join(' · ');
html+='<div style="font-size:11px;margin-top:6px;padding:4px 8px;background:var(--bg2);border-radius:6px;color:var(--text2)">'+labels+'</div>'}
html+='</div>'});
el.innerHTML=html;
}catch(e){el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">加载超时<br><span style="font-size:12px">'+e.message+'</span><br><button onclick="renderInsight()" style="margin-top:8px;padding:6px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer">🔄 重试</button></div>'}}

async function runCustomScenario(){
const input=document.getElementById('customScenarioInput');if(!input||!input.value.trim())return alert('请输入情景描述');
const text=input.value.trim();const el=document.getElementById('insightContent');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:40px"><div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 12px;border-width:3px"></div><div style="color:var(--text2)">🧠 自定义情景推理中...</div></div>';
try{const uid=getProfileId();const r=await fetch('/api/scenario/custom',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,userId:uid})});const d=await r.json();
if(d.error){el.innerHTML='<div style="padding:20px;color:var(--bear)">'+d.error+'</div>';return}
const a=d.analysis||{};
let html='<div class="dashboard-card" style="border-left:3px solid var(--accent)"><div class="dashboard-card-title">🎭 自定义情景</div><div style="font-size:12px;color:var(--text2)">'+text+'</div>';
if(a.transmission_chain)html+='<div style="font-size:13px;line-height:1.7;margin:8px 0">'+a.transmission_chain+'</div>';
if(a.sector_winners?.length)html+='<div style="font-size:12px;margin-bottom:4px">📈 受益：<span style="color:var(--bull)">'+a.sector_winners.join('、')+'</span></div>';
if(a.sector_losers?.length)html+='<div style="font-size:12px;margin-bottom:4px">📉 受损：<span style="color:var(--bear)">'+a.sector_losers.join('、')+'</span></div>';
if(a.portfolio_advice)html+='<div style="font-size:13px;margin-top:8px;padding:8px;background:rgba(59,130,246,.06);border-radius:8px">💡 '+a.portfolio_advice+'</div>';
html+='</div><button class="action-btn secondary" onclick="renderScenarioView(document.getElementById(\'insightContent\'))" style="margin-top:12px;width:100%">← 返回</button>';
el.innerHTML=html}catch(e){el.innerHTML='<div style="padding:20px;color:var(--bear)">'+e.message+'</div>'}}



// ============================================================
// V7.2 前端小白可用性补丁 (2026-04-19)
// - 顶部横幅：接入 /api/market-status，非交易日/盘中提示
// - 术语 tooltip：接入 /api/glossary，专业术语悬浮解释
// - 持仓数据新鲜度：is_snapshot + data_date 显示
// 独立模块，不触碰已有业务代码
// ============================================================
(function _v72PolishPatch(){
  'use strict';

  // ---- A. 顶部横幅：市场状态条 ----
  let _marketStatusCache = null;
  let _marketStatusTs = 0;

  async function fetchMarketStatus(){
    const now = Date.now();
    if (_marketStatusCache && now - _marketStatusTs < 300000) return _marketStatusCache; // 5min 缓存
    try{
      const r = await fetch(API_BASE + '/market-status', {signal: AbortSignal.timeout(3000)});
      if (r.ok) {
        _marketStatusCache = await r.json();
        _marketStatusTs = now;
        return _marketStatusCache;
      }
    }catch(e){}
    return null;
  }

  async function renderMarketBanner(){
    const status = await fetchMarketStatus();
    if (!status) return;
    let bar = document.getElementById('marketStatusBar');
    if (!bar){
      bar = document.createElement('div');
      bar.id = 'marketStatusBar';
      bar.className = 'market-status-bar';
      document.body.appendChild(bar);
    }
    // 2026-04-19 V7.7: paddingTop 由 renderNav 根据 currentPage 统一控制，这里不再写
    // 2026-04-19 V7.7.2 FIX: 初次渲染时 renderNav 可能已跑完但 bar 还没创建，
    // 所以每次 renderMarketBanner 也要按当前页检查一下 display
    if (typeof currentPage !== 'undefined') {
      bar.style.display = currentPage === 'landing' ? '' : 'none';
    }
    const session = status.session || 'closed';
    let tone = 'closed'; // 默认灰色
    if (session === 'morning' || session === 'afternoon') tone = 'trading';
    else if (session === 'pre_open' || session === 'lunch' || session === 'after_close') tone = 'paused';
    bar.className = 'market-status-bar tone-' + tone;
    bar.innerHTML = (status.message || '') + ' · ' + (status.weekday || '');
  }

  // ---- A2. 滚动时自动收起顶栏（向下滚起 / 向上滚出现）----
  function setupAutoHideHeader(){
    let lastScrollY = 0;
    let ticking = false;
    const SCROLL_THRESHOLD = 10; // 小于 10px 的抖动忽略
    const HIDE_THRESHOLD = 60;   // 滚过 60px 后才开始隐藏

    function onScroll(){
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        const y = window.scrollY || window.pageYOffset || 0;
        const delta = y - lastScrollY;
        const bar = document.getElementById('marketStatusBar');
        const hdr = document.getElementById('profileHeader');

        // 只在滚动超过阈值时响应
        if (Math.abs(delta) < SCROLL_THRESHOLD){
          ticking = false;
          return;
        }

        if (y < HIDE_THRESHOLD){
          // 顶部附近：一直显示
          if (bar) bar.classList.remove('is-hidden');
          if (hdr) hdr.classList.remove('is-hidden');
        } else if (delta > 0){
          // 向下滚：收起
          if (bar) bar.classList.add('is-hidden');
          if (hdr) hdr.classList.add('is-hidden');
        } else {
          // 向上滚：出现
          if (bar) bar.classList.remove('is-hidden');
          if (hdr) hdr.classList.remove('is-hidden');
        }

        lastScrollY = y;
        ticking = false;
      });
    }

    window.addEventListener('scroll', onScroll, {passive: true});
  }

  // ---- B. 术语 tooltip ----
  let _glossaryCache = null;
  let _glossaryLoading = null;

  async function loadGlossary(){
    if (_glossaryCache) return _glossaryCache;
    if (_glossaryLoading) return _glossaryLoading;
    _glossaryLoading = (async () => {
      try{
        const r = await fetch(API_BASE + '/glossary', {signal: AbortSignal.timeout(5000)});
        if (r.ok){
          const d = await r.json();
          _glossaryCache = d.glossary || {};
          // 建反向索引：别名/name 都能查
          const alt = {};
          for (const [k, v] of Object.entries(_glossaryCache)){
            alt[k.toUpperCase()] = v;
            if (v.name) alt[v.name] = v;
          }
          Object.assign(_glossaryCache, alt);
          return _glossaryCache;
        }
      }catch(e){}
      _glossaryCache = {};
      return _glossaryCache;
    })();
    return _glossaryLoading;
  }

  // 需要自动加解释的术语关键词列表（跟 glossary.py 对齐）
  // 2026-04-19 v7.2.2 扩充：首页/信号卡/大师辩论高频术语
  const _TERM_PATTERNS = [
    // 英文指标（必须整词匹配）
    /\bPE(-TTM)?\b/gi,
    /\bPB\b/gi,
    /\bPEG\b/gi,
    /\bROE\b/gi,
    /\bRSI\b/gi,
    /\bMACD\b/gi,
    /\bHHI\b/gi,
    /\bCVaR\b/gi,
    /\bBeta\b/gi,
    /\bSHIBOR\b/gi,
    /\bDCF\b/gi,
    /\bEV\b/gi,
    // 中文术语（整词匹配，较长的放前面避免子串冲突）
    /(夏普比率|Sortino|索提诺)/g,
    /(估值百分位|配置偏离度|恐贪指数|股债性价比|美林时钟)/g,
    /(最大回撤|止盈止损|再平衡|定投|北向资金|两融|布林带|量比|凯利|分歧度|置信度)/g,
    // V7.2.2 新增：小白高频词
    /(综合得分|持有观望|新闻情绪|沪深300|净资产)/g,
    /(技术面|基本面|资金面|情绪面|宏观面|地缘面)/g,
    /(巴菲特|格雷厄姆|林奇|塔勒布|仲裁官)/g,
    /(百分位|关键词|分红)/g,
  ];

  // 给容器内的文本自动加 tooltip 下划线
  async function enhanceTerms(root){
    if (!root) return;
    await loadGlossary();
    if (!_glossaryCache || Object.keys(_glossaryCache).length === 0) return;

    // 只处理还没被处理过的容器
    if (root.dataset && root.dataset.termsEnhanced === '1') return;
    if (root.dataset) root.dataset.termsEnhanced = '1';

    // 遍历所有文本节点
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function(node){
        const p = node.parentElement;
        if (!p) return NodeFilter.FILTER_REJECT;
        // 跳过 script/style/input/textarea/已包装的 term-tip
        const tag = p.tagName;
        if (['SCRIPT','STYLE','INPUT','TEXTAREA','BUTTON'].includes(tag)) return NodeFilter.FILTER_REJECT;
        if (p.closest && p.closest('.term-tip')) return NodeFilter.FILTER_REJECT;
        if (!node.nodeValue || node.nodeValue.length < 2) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });

    const textNodes = [];
    let n;
    while ((n = walker.nextNode())) textNodes.push(n);

    textNodes.forEach(tn => {
      const text = tn.nodeValue;
      let hasMatch = false;
      for (const pat of _TERM_PATTERNS){
        pat.lastIndex = 0;
        if (pat.test(text)){ hasMatch = true; break; }
      }
      if (!hasMatch) return;

      // 构建替换后的 HTML
      let html = text;
      for (const pat of _TERM_PATTERNS){
        html = html.replace(pat, (m) => {
          const key = m.toUpperCase();
          const entry = _glossaryCache[key] || _glossaryCache[m];
          if (!entry) return m;
          // FIX 2026-04-19 v7.2.2: 去掉 title（手机不支持悬停），改用 data-term + 点击弹窗
          return '<span class="term-tip" data-term="' + m + '" role="button" tabindex="0">' + m + '</span>';
        });
      }
      if (html !== text){
        const span = document.createElement('span');
        span.innerHTML = html;
        tn.parentNode.replaceChild(span, tn);
      }
    });
  }

  // ---- C. 挂到页面生命周期 ----

  // 初始化：横幅 + 加载 glossary
  function init(){
    renderMarketBanner();
    loadGlossary();
    setupAutoHideHeader();  // 新增：滚动时自动收起顶栏
    // 每 5 分钟刷一次横幅（跨越交易时段）
    setInterval(renderMarketBanner, 300000);
  }

  // MutationObserver 自动给新渲染的内容加术语 tooltip
  function startAutoEnhance(){
    const app = document.getElementById('app');
    if (!app) return;
    const obs = new MutationObserver((mutations) => {
      // 避免抖动：延迟批处理
      if (window._termEnhanceTimer) clearTimeout(window._termEnhanceTimer);
      window._termEnhanceTimer = setTimeout(() => {
        enhanceTerms(app);
      }, 300);
    });
    obs.observe(app, {childList: true, subtree: true, characterData: false});
    // 首次也跑一遍
    setTimeout(() => enhanceTerms(app), 500);
  }

  // ---- D. 点击术语弹出解释浮层（手机也能用） ----
  function showTermPopup(term){
    const key = (term || '').toUpperCase();
    const entry = _glossaryCache[key] || _glossaryCache[term] || {name: term, plain: '暂无解释'};

    // 关闭已有浮层
    const old = document.getElementById('termPopupOverlay');
    if (old) old.remove();

    const overlay = document.createElement('div');
    overlay.id = 'termPopupOverlay';
    overlay.className = 'term-popup-overlay';
    overlay.innerHTML = `
      <div class="term-popup-sheet" onclick="event.stopPropagation()">
        <div class="term-popup-handle"></div>
        <div class="term-popup-title">📖 ${entry.name || term}</div>
        ${entry.short ? `<div class="term-popup-short">${entry.short}</div>` : ''}
        <div class="term-popup-plain">${entry.plain || '暂无解释'}</div>
        ${entry.example ? `<div class="term-popup-example">💡 举例：${entry.example}</div>` : ''}
        <button class="term-popup-close" onclick="document.getElementById('termPopupOverlay').remove()">知道了</button>
      </div>
    `;
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
    document.body.appendChild(overlay);
  }

  // 全局事件委托：点击任何 .term-tip 都弹窗
  document.addEventListener('click', function(e){
    const tip = e.target.closest && e.target.closest('.term-tip');
    if (tip) {
      e.preventDefault();
      e.stopPropagation();
      const term = tip.dataset.term || tip.textContent;
      showTermPopup(term);
    }
  }, true);  // 用 capture 阶段，优先于业务代码的 click 处理

  // 启动
  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', function(){ init(); startAutoEnhance(); });
  } else {
    init();
    startAutoEnhance();
  }

  // 暴露给外部手动调用
  window._v72 = {
    enhanceTerms: enhanceTerms,
    fetchMarketStatus: fetchMarketStatus,
    loadGlossary: loadGlossary,
    showTermPopup: showTermPopup
  };
})();

/* ============================================================
   V7.3 用户画像 + 铁律（2026-04-19）
   触发方式：点击顶部 profileHeader 的用户名
   ============================================================ */
(function() {
  function _uid() {
    try { return (window.getProfileId && window.getProfileId()) || localStorage.getItem('moneybag_profile_name') || 'default'; } catch(e) { return 'default'; }
  }

  async function openProfileEditor() {
    const uid = _uid();
    // 拉现有画像 + 铁律
    let profile = {}, ironies = [];
    try {
      const [r1, r2] = await Promise.all([
        fetch(window.API_BASE + '/agent/profile/' + encodeURIComponent(uid)).then(r => r.json()),
        fetch(window.API_BASE + '/agent/ironies/' + encodeURIComponent(uid)).then(r => r.json())
      ]);
      profile = r1 || {};
      ironies = (r2 && r2.ironies) || [];
    } catch(e) { console.warn('load profile failed', e); }

    const existing = document.getElementById('profileEditorModal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.id = 'profileEditorModal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:10000;display:flex;align-items:flex-start;justify-content:center;overflow-y:auto;padding:20px 0';

    const familyOpts = ['单身', '已婚无娃', '已婚有娃-1个', '已婚有娃-2+个', '空巢/退休'];
    const incomeOpts = ['月薪1万以下', '月薪1-3万', '月薪3-5万', '月薪5万以上'];
    const horizonOpts = ['短期<1年', '中期1-3年', '长期3-10年', '养老10年+'];
    const toleranceOpts = ['-5%就难受', '-10%可接受', '-20%也能扛', '深度回撤无所谓'];
    const goalOpts = ['买房', '养老', '育儿教育', '改善生活', '财务自由'];

    const mkSelect = (name, opts, current) => {
      const opt = opts.map(o => `<option value="${o}" ${current===o?'selected':''}>${o}</option>`).join('');
      return `<select id="pf_${name}" style="width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid var(--bg3,#334155);background:var(--bg,#0f172a);color:var(--text,#f1f5f9);font-size:14px"><option value="">—未设置—</option>${opt}</select>`;
    };
    const mkGoalCheckbox = (current) => {
      const cur = current || [];
      return goalOpts.map(g =>
        `<label style="display:inline-flex;align-items:center;margin:4px 8px 4px 0;font-size:13px;cursor:pointer"><input type="checkbox" class="pf_goal" value="${g}" ${cur.includes(g)?'checked':''} style="margin-right:4px">${g}</label>`
      ).join('');
    };

    const ironiesList = ironies.length
      ? ironies.map(i => `<div style="display:flex;align-items:flex-start;gap:8px;padding:8px;background:var(--bg2,#1e293b);border-radius:6px;margin-bottom:6px">
          <div style="flex:1;font-size:13px;color:var(--text,#f1f5f9);line-height:1.5">${i.text.replace(/</g,'&lt;')}</div>
          <button onclick="window._removeIrony('${i.id}')" style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:14px">×</button>
        </div>`).join('')
      : '<div style="color:var(--text2,#94a3b8);font-size:12px;padding:8px">还没有铁律。告诉 AI 你的原则（如"不买医药"、"每月至少定投 3000"）会让它更懂你</div>';

    modal.innerHTML = `
      <div style="background:var(--bg2,#1e293b);padding:20px;border-radius:12px;max-width:500px;width:90%;max-height:calc(100vh - 40px);overflow-y:auto;position:relative">
        <button onclick="document.getElementById('profileEditorModal').remove()" style="position:absolute;top:12px;right:12px;background:none;border:none;color:var(--text2,#94a3b8);font-size:20px;cursor:pointer">×</button>
        <h3 style="margin:0 0 4px;color:var(--accent,#F59E0B)">🧑 我的画像</h3>
        <p style="color:var(--text2,#94a3b8);font-size:12px;margin:0 0 16px">填写越完整，AI 理解你越准</p>

        <div style="margin-bottom:12px"><label style="font-size:13px;color:var(--text2,#94a3b8);display:block;margin-bottom:4px">昵称</label>
          <input id="pf_nickname" type="text" value="${(profile.nickname||'').replace(/"/g,'&quot;')}" placeholder="怎么称呼你" style="width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid var(--bg3,#334155);background:var(--bg,#0f172a);color:var(--text,#f1f5f9);font-size:14px"></div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
          <div><label style="font-size:13px;color:var(--text2,#94a3b8);display:block;margin-bottom:4px">年龄</label>
            <input id="pf_age" type="number" value="${profile.age||''}" placeholder="如 32" min="16" max="99" style="width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid var(--bg3,#334155);background:var(--bg,#0f172a);color:var(--text,#f1f5f9);font-size:14px"></div>
          <div><label style="font-size:13px;color:var(--text2,#94a3b8);display:block;margin-bottom:4px">家庭</label>
            ${mkSelect('family', familyOpts, profile.family)}</div>
        </div>

        <div style="margin-bottom:12px"><label style="font-size:13px;color:var(--text2,#94a3b8);display:block;margin-bottom:4px">收入水平</label>
          ${mkSelect('income_level', incomeOpts, profile.income_level)}</div>

        <div style="margin-bottom:12px"><label style="font-size:13px;color:var(--text2,#94a3b8);display:block;margin-bottom:4px">投资周期</label>
          ${mkSelect('invest_horizon', horizonOpts, profile.invest_horizon)}</div>

        <div style="margin-bottom:12px"><label style="font-size:13px;color:var(--text2,#94a3b8);display:block;margin-bottom:4px">回撤容忍</label>
          ${mkSelect('drawdown_tolerance', toleranceOpts, profile.drawdown_tolerance)}</div>

        <div style="margin-bottom:12px"><label style="font-size:13px;color:var(--text2,#94a3b8);display:block;margin-bottom:4px">核心目标（可多选）</label>
          <div style="padding:6px;background:var(--bg,#0f172a);border-radius:8px">${mkGoalCheckbox(profile.life_goals)}</div></div>

        <div style="margin-bottom:16px"><label style="font-size:13px;color:var(--text2,#94a3b8);display:block;margin-bottom:4px">补充说明（可选）</label>
          <textarea id="pf_notes" rows="2" placeholder="任何想让 AI 知道的信息" style="width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid var(--bg3,#334155);background:var(--bg,#0f172a);color:var(--text,#f1f5f9);font-size:14px;resize:vertical;font-family:inherit">${(profile.notes||'').replace(/</g,'&lt;')}</textarea></div>

        <div style="border-top:1px solid var(--bg3,#334155);padding-top:14px;margin-bottom:12px">
          <h4 style="margin:0 0 6px;font-size:14px;color:var(--accent,#F59E0B)">🔒 长期铁律</h4>
          <p style="font-size:12px;color:var(--text2,#94a3b8);margin:0 0 10px">告诉 AI 哪些原则不可违反，比如"别推荐医药"、"每月定投 5000"</p>
          <div id="ironiesList" style="margin-bottom:8px">${ironiesList}</div>
          <div style="display:flex;gap:6px">
            <input id="newIronyInput" type="text" placeholder="输入一条铁律..." style="flex:1;padding:8px;border-radius:6px;border:1px solid var(--bg3,#334155);background:var(--bg,#0f172a);color:var(--text,#f1f5f9);font-size:13px">
            <button onclick="window._addIrony()" style="padding:8px 12px;background:var(--accent,#F59E0B);color:#000;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600">添加</button>
          </div>
        </div>

        <button onclick="window._saveProfile()" style="width:100%;padding:12px;border-radius:8px;border:none;background:var(--accent,#F59E0B);color:#000;font-size:15px;font-weight:700;cursor:pointer">💾 保存画像</button>
      </div>`;

    document.body.appendChild(modal);
  }

  window._saveProfile = async function() {
    const uid = _uid();
    const body = {
      userId: uid,
      nickname: (document.getElementById('pf_nickname')||{}).value || '',
      age: parseInt((document.getElementById('pf_age')||{}).value) || null,
      family: (document.getElementById('pf_family')||{}).value || '',
      income_level: (document.getElementById('pf_income_level')||{}).value || '',
      invest_horizon: (document.getElementById('pf_invest_horizon')||{}).value || '',
      drawdown_tolerance: (document.getElementById('pf_drawdown_tolerance')||{}).value || '',
      life_goals: Array.from(document.querySelectorAll('.pf_goal:checked')).map(e => e.value),
      notes: (document.getElementById('pf_notes')||{}).value || '',
    };
    try {
      const r = await fetch(window.API_BASE + '/agent/profile', {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
      });
      if (r.ok) {
        const el = document.getElementById('profileEditorModal');
        if (el) el.remove();
        if (window.showTermPopup) {
          // 借用已有的 popup 做 toast
          alert('✅ 画像已保存，AI 会立刻开始用它');
        } else {
          alert('✅ 画像已保存');
        }
      } else {
        alert('❌ 保存失败：' + r.status);
      }
    } catch(e) {
      alert('❌ 保存失败：' + e.message);
    }
  };

  window._addIrony = async function() {
    const uid = _uid();
    const input = document.getElementById('newIronyInput');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;
    try {
      const r = await fetch(window.API_BASE + '/agent/ironies', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({userId: uid, text, source: 'manual'})
      });
      if (r.ok) {
        input.value = '';
        openProfileEditor(); // 重开以刷新列表
      }
    } catch(e) { alert('添加失败: ' + e.message); }
  };

  window._removeIrony = async function(iron_id) {
    const uid = _uid();
    if (!confirm('确定删除这条铁律？')) return;
    try {
      const r = await fetch(window.API_BASE + '/agent/ironies/' + encodeURIComponent(uid) + '/' + encodeURIComponent(iron_id), {method:'DELETE'});
      if (r.ok) openProfileEditor();
    } catch(e) { alert('删除失败: ' + e.message); }
  };

  // 暴露给全局 + 给 profileHeader 绑定点击
  window.openProfileEditor = openProfileEditor;

  // 2026-04-19 V7.4.1: 应用户要求关闭前端画像 UI 入口
  //   - 画像/铁律/事件后续由 AI 通过对话学习 + 用户口述给开发者修改
  //   - openProfileEditor 函数保留（以后想恢复只需开启下面绑定）
  // const attachClick = () => {
  //   const hdr = document.getElementById('profileHeader');
  //   if (!hdr) return setTimeout(attachClick, 500);
  //   if (hdr.dataset._profileEditorBound) return;
  //   hdr.dataset._profileEditorBound = '1';
  //   hdr.style.cursor = 'pointer';
  //   hdr.title = '点击编辑我的画像';
  //   hdr.addEventListener('click', (e) => {
  //     const rect = hdr.getBoundingClientRect();
  //     if (e.clientX - rect.left < rect.width * 0.6) {
  //       openProfileEditor();
  //     }
  //   });
  //   console.log('[V7.3] profileHeader 点击已绑定 → 我的画像');
  // };
  // setTimeout(attachClick, 800);
})();

/* ============================================================
   V7.4.2 待审记忆红点（2026-04-19）
   V7.4.3: 只对家庭主账号（LeiJiang）显示，配偶/儿童账号完全安静
   - 顶部 profileHeader 右侧自动挂小红点
   - 有待审记忆就亮 + 数字
   - 点一下打开面板，一条条确认/拒绝
   - 不干扰其他 UI
   ============================================================ */
(function() {
  // 家庭主账号白名单（硬编码，和后端 FAMILY_ADMIN 保持一致）
  const FAMILY_ADMIN_WHITELIST = ['LeiJiang'];

  function _uid() {
    try { return (window.getProfileId && window.getProfileId()) || localStorage.getItem('moneybag_profile_name') || 'default'; } catch(e) { return 'default'; }
  }

  function _isAdmin() {
    return FAMILY_ADMIN_WHITELIST.includes(_uid());
  }

  async function fetchPending() {
    const uid = _uid();
    try {
      const r = await fetch(window.API_BASE + '/agent/pending-insights/' + encodeURIComponent(uid));
      if (!r.ok) return {items:[], count:0};
      return await r.json();
    } catch(e) { return {items:[], count:0}; }
  }

  function renderBadge(count) {
    let badge = document.getElementById('pendingBadge');
    if (!badge) {
      badge = document.createElement('div');
      badge.id = 'pendingBadge';
      badge.style.cssText = 'position:fixed;top:6px;right:12px;z-index:101;padding:2px 8px;background:#EF4444;color:#fff;border-radius:10px;font-size:11px;font-weight:700;cursor:pointer;display:none;box-shadow:0 2px 6px rgba(239,68,68,.4)';
      badge.title = '点击查看 AI 学到的新记忆';
      badge.onclick = openPendingPanel;
      document.body.appendChild(badge);
    }
    if (count > 0) {
      badge.textContent = '💡 ' + count;
      badge.style.display = 'block';
    } else {
      badge.style.display = 'none';
    }
  }

  async function openPendingPanel() {
    const { items } = await fetchPending();
    const existing = document.getElementById('pendingPanelModal');
    if (existing) existing.remove();

    if (!items || items.length === 0) {
      alert('暂无新的待审记忆');
      return;
    }

    const modal = document.createElement('div');
    modal.id = 'pendingPanelModal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:10000;display:flex;align-items:flex-start;justify-content:center;overflow-y:auto;padding:20px 0';

    const catLabel = {
      irony: '🔒 铁律',
      preference: '💙 偏好',
      profile_note: '📋 情境',
    };

    const itemsHtml = items.map(i => {
      const cat = catLabel[i.category] || i.category;
      const src = i.source_user_msg ? `<div style="font-size:11px;color:var(--text3,#64748b);margin-top:6px">💬 "${(i.source_user_msg||'').slice(0,60).replace(/</g,'&lt;')}..."</div>` : '';
      return `<div style="padding:12px;background:var(--bg,#0f172a);border-radius:8px;margin-bottom:10px;border-left:3px solid var(--accent,#F59E0B)">
        <div style="font-size:12px;color:var(--text2,#94a3b8);margin-bottom:4px">${cat}</div>
        <div style="font-size:14px;color:var(--text,#f1f5f9);line-height:1.5">${(i.text||'').replace(/</g,'&lt;')}</div>
        ${src}
        <div style="display:flex;gap:8px;margin-top:10px">
          <button onclick="window._approveInsight('${i.id}')" style="flex:1;padding:8px;background:var(--accent,#F59E0B);color:#000;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600">✅ 记下来</button>
          <button onclick="window._rejectInsight('${i.id}')" style="flex:1;padding:8px;background:var(--bg3,#334155);color:var(--text2,#94a3b8);border:none;border-radius:6px;cursor:pointer;font-size:13px">❌ 忽略</button>
        </div>
      </div>`;
    }).join('');

    modal.innerHTML = `
      <div style="background:var(--bg2,#1e293b);padding:20px;border-radius:12px;max-width:500px;width:90%;max-height:calc(100vh - 40px);overflow-y:auto;position:relative">
        <button onclick="document.getElementById('pendingPanelModal').remove()" style="position:absolute;top:12px;right:12px;background:none;border:none;color:var(--text2,#94a3b8);font-size:20px;cursor:pointer">×</button>
        <h3 style="margin:0 0 4px;color:var(--accent,#F59E0B)">💡 AI 学到的新记忆</h3>
        <p style="color:var(--text2,#94a3b8);font-size:12px;margin:0 0 16px">AI 在和你聊天时发现了这些信息。记下来 → 下次对话 AI 就会用；忽略 → 直接丢弃。</p>
        ${itemsHtml}
      </div>`;

    document.body.appendChild(modal);
  }

  window._approveInsight = async function(id) {
    const uid = _uid();
    try {
      const r = await fetch(window.API_BASE + '/agent/insight/approve', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({userId: uid, id})
      });
      if (r.ok) {
        await openPendingPanel(); // 刷新面板
        refreshBadge();
      }
    } catch(e) { alert('批准失败: ' + e.message); }
  };

  window._rejectInsight = async function(id) {
    const uid = _uid();
    try {
      const r = await fetch(window.API_BASE + '/agent/insight/reject', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({userId: uid, id})
      });
      if (r.ok) {
        await openPendingPanel();
        refreshBadge();
      }
    } catch(e) { alert('拒绝失败: ' + e.message); }
  };

  async function refreshBadge() {
    // V7.4.3: 非家庭主账号完全不拉、不显示红点
    if (!_isAdmin()) {
      const badge = document.getElementById('pendingBadge');
      if (badge) badge.style.display = 'none';
      return;
    }
    const { count } = await fetchPending();
    renderBadge(count);
  }

  // 启动后 1 秒首次拉 + 每 5 分钟刷一次
  setTimeout(refreshBadge, 1000);
  setInterval(refreshBadge, 5 * 60 * 1000);
  // 暴露供手动触发
  window.refreshPendingBadge = refreshBadge;
  window.openPendingPanel = openPendingPanel;
})();

// ---- 周度金融小课页 (M6 W3-4) ----
async function renderWeeklyLesson() {
  currentPage = 'weekly-lesson';
  renderNav();

  // Auth check: must be logged in
  const pid = getProfileId();
  if (!pid || pid === 'default') {
    $('#app').innerHTML = `
      <div class="page-container fade-up" style="text-align:center;padding:60px 20px">
        <div style="font-size:48px;margin-bottom:16px">📚</div>
        <div style="font-size:18px;font-weight:700;color:var(--text1);margin-bottom:8px">登录后查看小课</div>
        <div style="font-size:14px;color:var(--text2);margin-bottom:24px">每周金融小课需要登录才能查看</div>
        <button class="cta-btn" onclick="showProfileSettings()">去登录 →</button>
      </div>`;
    return;
  }

  // Compute current ISO week label
  function getWeekLabel() {
    const now = new Date();
    const jan1 = new Date(now.getFullYear(), 0, 1);
    const week = Math.ceil(((now - jan1) / 86400000 + jan1.getDay() + 1) / 7);
    const monday = new Date(now);
    monday.setDate(now.getDate() - ((now.getDay() + 6) % 7));
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    const fmt = d => `${d.getMonth()+1}月${d.getDate()}日`;
    return { label: `${fmt(monday)} — ${fmt(sunday)}`, week: `${now.getFullYear()}-W${String(week).padStart(2,'0')}` };
  }

  const { label: weekLabel, week: weekIso } = getWeekLabel();
  const profileName = _profileName || '你';

  // Render skeleton immediately
  $('#app').innerHTML = `
    <div class="page-container fade-up">
      <div class="wl-header">
        <div class="wl-week-badge">📅 ${weekLabel}</div>
        <div class="wl-greeting">嗨 ${profileName}，本周的金融小课来了 👇</div>
      </div>
      <div id="wl-body">
        <div class="wl-loading">
          <div class="wl-spinner"></div>
          <div style="color:var(--text2);font-size:14px;margin-top:12px">小课加载中...</div>
        </div>
      </div>
      <div id="wl-history-section"></div>
    </div>`;

  if (!API_AVAILABLE) {
    document.getElementById('wl-body').innerHTML = `
      <div class="wl-empty-state">
        <div class="wl-empty-icon">📚</div>
        <div class="wl-empty-title">本周小课准备中</div>
        <div class="wl-empty-sub">连接服务器后可查看个性化金融小课</div>
      </div>`;
    return;
  }

  try {
    // Fetch current week lesson from API
    const r = await fetch(API_BASE + '/decisions/weekly-lesson', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: pid }),
      signal: AbortSignal.timeout(10000),
    });

    if (!r.ok) throw new Error('API error ' + r.status);
    const data = await r.json();

    const bodyEl = document.getElementById('wl-body');
    if (!bodyEl) return;

    if (!data.delivered || !data.lesson) {
      // Empty state
      bodyEl.innerHTML = `
        <div class="wl-empty-state">
          <div class="wl-empty-icon">📚</div>
          <div class="wl-empty-title">本周小课准备中</div>
          <div class="wl-empty-sub">${data.reason || '小课内容正在为你准备，请稍后查看'}</div>
        </div>`;
      return;
    }

    const lesson = data.lesson;
    const articleUrl = `/weekly-lesson?week=${weekIso}&article=${encodeURIComponent(lesson.article_id)}`;

    bodyEl.innerHTML = `
      <div class="wl-card fade-up">
        <div class="wl-card-badge">${lesson.article_category || '金融知识'}</div>
        <div class="wl-card-title" onclick="window._wlOpenArticle('${encodeURIComponent(lesson.article_id)}', '${encodeURIComponent(lesson.article_title)}')" style="cursor:pointer">
          ${lesson.article_title}
          <span style="font-size:14px;color:var(--accent)"> →</span>
        </div>
        <div class="wl-card-intro">${lesson.intro_sentence || ''}</div>
        <div class="wl-card-footer">
          <span class="wl-trigger-tag">${_wlTriggerLabel(lesson.trigger)}</span>
          <button class="wl-read-btn" onclick="window._wlOpenArticle('${encodeURIComponent(lesson.article_id)}', '${encodeURIComponent(lesson.article_title)}')">
            阅读全文 →
          </button>
        </div>
      </div>`;

    // Load history (last 4 weeks, best-effort)
    _wlLoadHistory(pid);

  } catch (e) {
    const bodyEl = document.getElementById('wl-body');
    if (bodyEl) bodyEl.innerHTML = `
      <div class="wl-empty-state">
        <div class="wl-empty-icon">⚠️</div>
        <div class="wl-empty-title">加载失败</div>
        <div class="wl-empty-sub">网络异常，请稍后重试</div>
        <button class="cta-btn" style="margin-top:16px;font-size:13px;padding:8px 20px" onclick="renderWeeklyLesson()">重试</button>
      </div>`;
  }
}

function _wlTriggerLabel(trigger) {
  if (trigger === 'holding_event') return '📉 持仓事件触发';
  if (trigger === 'new_article') return '🆕 新文章';
  return '📅 本周推送';
}

window._wlOpenArticle = async function(articleIdEncoded, titleEncoded) {
  const articleId = decodeURIComponent(articleIdEncoded);
  const title = decodeURIComponent(titleEncoded);

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `
    <div class="modal-sheet" style="padding:20px 20px 32px" onclick="event.stopPropagation()">
      <div class="modal-handle"></div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <div style="font-size:16px;font-weight:700;color:var(--text1)">${title}</div>
        <button onclick="this.closest('.modal-overlay').remove()" style="background:transparent;border:none;color:var(--text2);font-size:20px;cursor:pointer">×</button>
      </div>
      <div id="wl-article-body" style="color:var(--text2);font-size:14px;line-height:1.7">
        <div class="wl-spinner" style="margin:40px auto"></div>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  if (!API_AVAILABLE) {
    document.getElementById('wl-article-body').innerHTML = '<div style="color:var(--text2)">需要连接服务器才能加载文章内容。</div>';
    return;
  }

  try {
    const r = await fetch(API_BASE + '/knowledge/article/' + encodeURIComponent(articleId), {
      signal: AbortSignal.timeout(8000),
    });
    const bodyEl = document.getElementById('wl-article-body');
    if (!bodyEl) return;
    if (r.ok) {
      const d = await r.json();
      const content = (d.content || d.text || '').replace(/\n/g, '<br>');
      bodyEl.innerHTML = `<div style="white-space:pre-wrap">${content}</div>`;
    } else {
      bodyEl.innerHTML = '<div style="color:var(--text2)">文章内容暂时无法加载。</div>';
    }
  } catch (e) {
    const bodyEl = document.getElementById('wl-article-body');
    if (bodyEl) bodyEl.innerHTML = '<div style="color:var(--text2)">加载失败，请检查网络后重试。</div>';
  }
};

async function _wlLoadHistory(userId) {
  const section = document.getElementById('wl-history-section');
  if (!section || !API_AVAILABLE) return;

  try {
    const r = await fetch(API_BASE + '/decisions/weekly-lesson/history?user_id=' + encodeURIComponent(userId), {
      signal: AbortSignal.timeout(5000),
    });
    if (!r.ok) return;
    const d = await r.json();
    const records = d.records || [];
    if (!records.length) return;

    section.innerHTML = `
      <div style="margin-top:24px">
        <div style="font-size:13px;font-weight:600;color:var(--text2);margin-bottom:12px;text-transform:uppercase;letter-spacing:.5px">📖 往期回顾</div>
        ${records.slice(0,4).map(rec => `
          <div class="wl-history-item" onclick="window._wlOpenArticle('${encodeURIComponent(rec.article_id)}', '${encodeURIComponent(rec.article_title||rec.article_id)}')">
            <div style="font-size:13px;color:var(--text1)">${rec.article_title || rec.article_id}</div>
            <div style="font-size:11px;color:var(--text3);margin-top:2px">${rec.week_iso}</div>
          </div>`).join('')}
      </div>`;
  } catch (e) {
    // History is optional; silently skip on failure
  }
}
