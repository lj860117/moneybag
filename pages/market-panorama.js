// ---- 市场全景页 ----
async function renderMarketPanorama(){currentPage='market-panorama';renderNav();
$('#app').innerHTML=`<div class="result-page fade-up">
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
<div style="font-size:18px;font-weight:800">🌍 市场全景</div>
<div onclick="navigateTo('landing')" style="font-size:12px;color:var(--accent);cursor:pointer">← 返回首页</div>
</div>
<div id="mpTemperature" class="dashboard-card" style="margin-bottom:12px"><div style="text-align:center;padding:20px;color:var(--text2)">加载中...</div></div>
<div id="mpSectors" class="dashboard-card" style="margin-bottom:12px"><div style="padding:12px;color:var(--text2);font-size:13px">加载热点板块...</div></div>
<div id="mpNews" class="dashboard-card" style="margin-bottom:12px"><div style="padding:12px;color:var(--text2);font-size:13px">加载新闻...</div></div>
<div id="mpAssets" class="dashboard-card" style="margin-bottom:12px"><div style="padding:12px;color:var(--text2);font-size:13px">加载资产判断...</div></div>
<div id="mpBroker" class="dashboard-card" style="margin-bottom:12px"><div style="padding:12px;color:var(--text2);font-size:13px">加载机构观点...</div></div>
<div style="text-align:center;margin-top:16px">
<div onclick="navigateTo('insight')" style="font-size:12px;color:var(--text2);cursor:pointer;padding:8px">想看更多详细数据？进入 📰 资讯页 →</div>
</div>
</div>`;
if(!API_AVAILABLE)return;
try{
const r=await fetch(`${API_BASE}/market-panorama`,{signal:AbortSignal.timeout(12000)});
if(!r.ok)return;
const d=await r.json();

// 1. 市场温度
const t=d.temperature;
if(t){
const bgMap={green:'rgba(16,185,129,.06)',yellow:'rgba(245,158,11,.06)',orange:'rgba(245,158,11,.08)',red:'rgba(239,68,68,.06)'};
const colorMap={green:'var(--green)',yellow:'#F59E0B',orange:'#F59E0B',red:'var(--red)'};
const bg=bgMap[t.icon==='🔥'||t.icon==='🟢'?'green':t.icon==='🟡'?'yellow':t.icon==='🟠'?'orange':'red']||'';
document.getElementById('mpTemperature').innerHTML=`
<div style="background:${bg};border-radius:12px;padding:14px">
<div style="font-size:14px;font-weight:700;margin-bottom:8px">🌡️ 市场温度</div>
<div style="font-size:20px;font-weight:800;margin-bottom:4px">${t.icon} ${t.overall}</div>
<div style="font-size:13px;color:var(--text2);margin-bottom:8px">${t.advice}</div>
<div style="display:flex;gap:12px;font-size:12px;color:var(--text2)">
<span>恐贪: ${t.fear_greed}(${t.fear_greed_level})</span>
<span>估值: ${t.valuation_pct}%分位(${t.valuation_level})</span>
</div>
${t.signal_summary?`<div style="font-size:12px;color:var(--text2);margin-top:6px;padding-top:6px;border-top:1px solid var(--bg3)">${t.signal_summary}</div>`:''}
</div>`;
}

// 2. 热点板块
const sectors=d.hot_sectors||[];
if(sectors.length){
document.getElementById('mpSectors').innerHTML=`
<div style="font-size:14px;font-weight:700;margin-bottom:8px">🔥 今日热点板块</div>
${sectors.map((s,i)=>{const c=s.change_pct>=0?'var(--green)':'var(--red)';return`<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--bg3,rgba(0,0,0,.03));font-size:13px"><span>${i+1}. ${s.name}</span><span style="color:${c};font-weight:600">${s.change_pct>=0?'+':''}${s.change_pct.toFixed(1)}%</span></div>`}).join('')}`;
}else{
document.getElementById('mpSectors').innerHTML=`<div style="font-size:14px;font-weight:700;margin-bottom:8px">🔥 今日热点板块</div><div style="font-size:13px;color:var(--text2)">暂无明显热点板块</div>`;
}

// 3. 重要新闻
const news=d.news||[];
if(news.length){
document.getElementById('mpNews').innerHTML=`
<div style="font-size:14px;font-weight:700;margin-bottom:8px">📰 重要新闻</div>
${news.map(n=>`<div style="font-size:13px;line-height:1.8;padding:4px 0;border-bottom:1px solid var(--bg3,rgba(0,0,0,.03))">• ${n.title}</div>`).join('')}`;
}

// 4. 各资产判断
const aj=d.asset_judgment;
if(aj){
document.getElementById('mpAssets').innerHTML=`
<div style="font-size:14px;font-weight:700;margin-bottom:10px">💰 各资产判断</div>
<div style="margin-bottom:8px;padding:8px 10px;background:var(--bg2,rgba(0,0,0,.02));border-radius:8px">
<div style="font-size:13px;font-weight:600">📈 A股 ${aj.stock.icon} ${aj.stock.label}</div>
<div style="font-size:12px;color:var(--text2);margin-top:2px">${aj.stock.reason}</div>
</div>
<div style="margin-bottom:8px;padding:8px 10px;background:var(--bg2,rgba(0,0,0,.02));border-radius:8px">
<div style="font-size:13px;font-weight:600">💰 基金定投 ${aj.fund.icon} ${aj.fund.label}</div>
<div style="font-size:12px;color:var(--text2);margin-top:2px">${aj.fund.reason}</div>
</div>
<div style="padding:8px 10px;background:var(--bg2,rgba(0,0,0,.02));border-radius:8px">
<div style="font-size:13px;font-weight:600">🥇 黄金 ${aj.gold.icon} ${aj.gold.label}</div>
<div style="font-size:12px;color:var(--text2);margin-top:2px">${aj.gold.reason}</div>
</div>`;
}

// 5. 机构观点
const bv=d.broker_view;
if(bv){
const consensusColor=bv.consensus==='看多'?'var(--green)':bv.consensus==='看空'?'var(--red)':'var(--text2)';
document.getElementById('mpBroker').innerHTML=`
<div style="font-size:14px;font-weight:700;margin-bottom:8px">🏛️ 机构观点</div>
<div style="font-size:13px;margin-bottom:6px">券商共识: <span style="color:${consensusColor};font-weight:700">${bv.consensus}</span></div>
${bv.hot_industries&&bv.hot_industries.length?`<div style="font-size:12px;color:var(--text2)">关注行业: ${bv.hot_industries.join('、')}</div>`:''}`;
}

}catch(e){console.warn('[MP]',e)}}
