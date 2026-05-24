// ---- 资讯页 ----
let insightTab='overview';
function _insightTabs(){
const all=[
['overview','📊 总览'],['fundpick','🔍 选基'],['stockpick','🧠 选股'],['recommend','💎 推荐'],['decisions','🎯 决策复盘'],['sector','🔥 行业'],['broker','🏛️ 研报'],['scenario','🎭 情景'],['news','📰 新闻'],['policy','🏛️ 政策'],['tech','📈 技术'],['macro','📊 宏观'],['global','🌐 全球'],['signals','📡 信号'],['scorecard','📊 成绩单'],['doctor','🏥 体检'],['steward','🤖 管家'],['factorictest','🔬 因子检验'],['montecarlo','🎲 蒙特卡洛'],['geneticfactor','🧬 遗传因子'],['optimizer','⚡ 组合优化'],['altdata','📡 另类数据'],['rlposition','🎮 RL仓位'],['llmfactor','🧠 LLM因子'],['weekly','📋 周报']];
const simple=['overview','fundpick','stockpick','recommend','decisions','news','doctor','steward'];
return isProMode()?all:all.filter(t=>simple.includes(t[0]))}
async function renderInsight(){currentPage='insight';renderNav();const tabs=_insightTabs();
$('#app').innerHTML=`<div class="insight-page fade-up"><div class="insight-header"><h2>📰 市场资讯</h2><p>${API_AVAILABLE?'实时数据更新中':'后端离线'} <button onclick="runDataAudit()" style="background:rgba(245,158,11,.15);border:1px solid rgba(245,158,11,.3);border-radius:6px;padding:2px 8px;font-size:11px;color:#F59E0B;cursor:pointer;margin-left:4px" id="auditBtn">🔍 数据体检</button></p></div><div class="section-tab-bar" id="insightTabBar">${tabs.map(t=>`<button class="section-tab ${insightTab===t[0]?'active':''}" data-tab="${t[0]}" onclick="insightTab='${t[0]}';renderInsight()">${t[1]}</button>`).join('')}</div><div id="insightContent"><div style="text-align:center;padding:40px;color:var(--text2)"><div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 12px;border-width:3px"></div><div id="loadingMsg" style="margin-top:8px">正在加载市场数据...</div><div style="font-size:12px;color:var(--text3,#94a3b8);margin-top:8px">☁️ 免费云服务器，首次加载可能需要 10~30 秒</div></div></div></div>`;
// Tab栏自动滚动到选中位置
setTimeout(()=>{const bar=document.getElementById('insightTabBar');const active=bar&&bar.querySelector('.section-tab.active');if(active&&bar){active.scrollIntoView({behavior:'smooth',inline:'center',block:'nearest'})}},50);
if(!API_AVAILABLE){document.getElementById('insightContent').innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">后端离线，请启动后端服务获取实时数据</div>';return}
// 独立 tab 不需要 dashboard 数据，秒开
if(insightTab==='sector'){const el=document.getElementById('insightContent');if(el)renderSectorHot(el);return}
if(insightTab==='broker'){const el=document.getElementById('insightContent');if(el)renderBrokerView(el);return}
if(insightTab==='scenario'){const el=document.getElementById('insightContent');if(el)renderScenarioView(el);return}
if(insightTab==='recommend'){const el=document.getElementById('insightContent');if(el)renderRecommendTab(el);return}
if(insightTab==='decisions'){const el=document.getElementById('insightContent');if(el)renderDecisionsTab(el);return}
if(insightTab==='fundpick'){const el=document.getElementById('insightContent');if(el)renderFundPick(el);return}
if(insightTab==='stockpick'){const el=document.getElementById('insightContent');if(el)renderStockPick(el);return}
if(insightTab==='factorictest'){const el=document.getElementById('insightContent');if(el)renderFactorIC(el);return}
if(insightTab==='montecarlo'){const el=document.getElementById('insightContent');if(el)renderMonteCarlo(el);return}
if(insightTab==='geneticfactor'){const el=document.getElementById('insightContent');if(el)renderGeneticFactor(el);return}
if(insightTab==='optimizer'){const el=document.getElementById('insightContent');if(el)renderOptimizer(el);return}
if(insightTab==='altdata'){const el=document.getElementById('insightContent');if(el)renderAltData(el);return}
if(insightTab==='rlposition'){const el=document.getElementById('insightContent');if(el)renderRLPosition(el);return}
if(insightTab==='llmfactor'){const el=document.getElementById('insightContent');if(el)renderLLMFactor(el);return}
if(insightTab==='signals'){const el=document.getElementById('insightContent');if(el)renderSignalScout(el);return}
if(insightTab==='scorecard'){const el=document.getElementById('insightContent');if(el)renderScorecard(el);return}
if(insightTab==='doctor'){const el=document.getElementById('insightContent');if(el)renderDoctor(el);return}
if(insightTab==='steward'){const el=document.getElementById('insightContent');if(el)renderSteward(el);return}
if(insightTab==='weekly'){const el=document.getElementById('insightContent');if(el)renderWeeklyReport(el);return}
if(insightTab==='policy'){const el=document.getElementById('insightContent');if(el)renderInsightPolicy(el);return}
if(insightTab==='news'){const el=document.getElementById('insightContent');if(el){const cached=getCached('news');if(cached){renderInsightNews(el,{news:cached.news||[]});return}el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载新闻中...</div>';try{const r=await fetch(API_BASE+'/news',{signal:AbortSignal.timeout(15000)});if(r.ok){const d=await r.json();setCached('news',d);renderInsightNews(el,{news:d.news||[]});}else{el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">新闻加载失败<br><button onclick="renderInsight()" style="margin-top:8px;padding:6px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer">🔄 重试</button></div>';}}catch(e){el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">新闻加载超时<br><button onclick="renderInsight()" style="margin-top:8px;padding:6px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer">🔄 重试</button></div>';}}return}
// 需要 dashboard 的 tab: overview / tech / macro
const loadStart=Date.now();const loadTimer=setInterval(()=>{const el=document.getElementById('loadingMsg');if(!el){clearInterval(loadTimer);return}const sec=Math.round((Date.now()-loadStart)/1000);if(sec>=5&&sec<15)el.textContent='正在从数据源抓取实时行情...';else if(sec>=15&&sec<25)el.textContent='数据量较大，还在努力加载中...';else if(sec>=25)el.textContent='快好了，感谢耐心等待 🙏'},3000);
const dash=await fetchDashboard();clearInterval(loadTimer);if(!dash){document.getElementById('insightContent').innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">数据加载失败，请稍后再试<br><button onclick="renderInsight()" style="margin-top:12px;padding:8px 20px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer">🔄 重新加载</button></div>';return}
const el=document.getElementById('insightContent');if(!el)return;
if(insightTab==='overview')renderInsightOverview(el,dash);
else if(insightTab==='tech')renderInsightTech(el,dash);
else if(insightTab==='macro')renderInsightMacro(el,dash);
else if(insightTab==='global')renderInsightGlobal(el)}

function renderV45FactorCards(d){
const nb=d.northbound||{};const mg=d.margin||{};const tr=d.treasury||{};const sh=d.shibor||{};const dv=d.dividend||{};
const cards=[];
if(nb.available){
const nbColor=nb.net_flow_5d>30?'var(--green)':nb.net_flow_5d<-30?'var(--red)':'var(--accent)';
setExplain('northbound','北向资金','北向资金 = 外资通过沪股通/深股通买入A股的钱，被称为"聪明钱"。\n\n📊 今日净流入：'+(nb.net_flow_today||0)+'亿\n📊 5日累计：'+(nb.net_flow_5d||0)+'亿\n📊 20日累计：'+(nb.net_flow_20d||0)+'亿\n📊 趋势：'+nb.trend+'\n\n🔍 怎么看：\n• 连续大幅流入（>100亿/5日）→ 外资看好A股\n• 连续大幅流出（<-100亿/5日）→ 外资避险\n• 短期波动不用太在意，看5日/20日趋势更有意义\n\n💡 北向资金占A股成交额约5-8%，影响力不小。');
cards.push(`<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="showExplain('northbound')">
<div style="font-size:12px;font-weight:700">💰 北向资金</div>
<div style="font-size:18px;font-weight:900;color:${nbColor};margin-top:4px">${nb.net_flow_today>0?'+':''}${nb.net_flow_today}亿</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">5日 ${nb.net_flow_5d>0?'+':''}${nb.net_flow_5d}亿 · ${nb.trend}</div></div>`)}
if(mg.available){
const mgColor=mg.trend.includes('上升')?'var(--red)':mg.trend.includes('下降')?'var(--green)':'var(--accent)';
setExplain('margin_factor','融资融券','融资融券 = 借钱/借股票来炒股，反映市场的杠杆情绪。\n\n📊 融资余额：'+mg.margin_balance+'亿\n📊 5日变化：'+(mg.margin_change_5d>0?'+':'')+mg.margin_change_5d+'%\n📊 趋势：'+mg.trend+'\n\n🔍 怎么看：\n• 融资余额快速上升（>3%/5日）→ 散户加杠杆，市场过热\n• 融资余额快速下降（<-3%/5日）→ 去杠杆，可能恐慌\n• 温和变化属于正常市场波动\n\n⚠️ 杠杆是双刃剑：放大收益也放大亏损。');
cards.push(`<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="showExplain('margin_factor')">
<div style="font-size:12px;font-weight:700">📊 融资融券</div>
<div style="font-size:18px;font-weight:900;color:${mgColor};margin-top:4px">${mg.margin_balance}亿</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">5日 ${mg.margin_change_5d>0?'+':''}${mg.margin_change_5d}% · ${mg.trend}</div></div>`)}
if(sh.available){
const shColor=sh.trend.includes('收紧')?'var(--red)':sh.trend.includes('宽松')?'var(--green)':'var(--accent)';
setExplain('shibor_factor','SHIBOR 利率','SHIBOR = 上海银行间同业拆放利率，反映银行之间借钱的成本，是流动性"温度计"。\n\n📊 隔夜利率：'+sh.overnight+'%\n📊 趋势：'+sh.trend+'\n\n🔍 怎么看：\n• 利率下降/宽松 → 市场钱多，利好股市\n• 利率上升/收紧 → 市场缺钱，股市承压\n• 隔夜利率通常在1-2%之间波动\n\n💡 央行货币政策最先反映在 SHIBOR 上。');
cards.push(`<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="showExplain('shibor_factor')">
<div style="font-size:12px;font-weight:700">🏦 SHIBOR</div>
<div style="font-size:18px;font-weight:900;color:${shColor};margin-top:4px">${sh.overnight}%</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">隔夜 · ${sh.trend}</div></div>`)}
if(dv.available){
const dvColor=dv.level.includes('高')?'var(--green)':dv.level.includes('低')?'var(--red)':'var(--accent)';
setExplain('dividend_factor','股息率','股息率 = 每年分红 ÷ 股价，衡量"持有这个指数每年能拿多少分红"。\n\n📊 当前股息率：'+dv.dividend_yield+'%\n📊 水平：'+dv.level+(dv.percentile!=null?'\n📊 百分位：'+dv.percentile+'%':'')+'\n\n🔍 怎么看：\n• 股息率高（百分位>70%）→ 价值凸显，适合长期持有\n• 股息率低（百分位<40%）→ 成长偏好期，市场追涨\n• 高股息策略在熊市特别有保护作用\n\n💡 巴菲特说："如果你不打算持有一只股票10年，那就不要持有10分钟。"');
cards.push(`<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="showExplain('dividend_factor')">
<div style="font-size:12px;font-weight:700">📈 股息率</div>
<div style="font-size:18px;font-weight:900;color:${dvColor};margin-top:4px">${dv.dividend_yield}%</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">${dv.level}${dv.percentile!=null?' · P'+dv.percentile+'%':''}</div></div>`)}
if(tr.available){
const trColor=tr.equity_premium.includes('极有')?'var(--green)':tr.equity_premium.includes('债券更')?'var(--red)':'var(--accent)';
setExplain('treasury_factor','国债收益率 / 股债性价比','10年期国债收益率是"无风险回报率"——不承担任何风险就能拿到的收益。\n\n📊 10Y国债：'+tr.yield_10y+'%\n📊 变动：'+(tr.yield_change>0?'+':'')+tr.yield_change+'%\n📊 股债性价比：'+tr.equity_premium+'\n\n🔍 怎么看：\n• 股票盈利收益率(1/PE) 远 > 国债 → 股市更有吸引力\n• 股票盈利收益率 ≈ 国债 → 股债差不多\n• 国债收益率 > 股票 → 买债更划算\n\n💡 巴菲特经常用这个指标判断"股票是否值得买"。');
cards.push(`<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="showExplain('treasury_factor')">
<div style="font-size:12px;font-weight:700">🔗 国债/股债比</div>
<div style="font-size:18px;font-weight:900;color:${trColor};margin-top:4px">${tr.yield_10y}%</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${tr.equity_premium||'数据计算中'}</div></div>`)}
if(!cards.length)return'';
return`<div class="dashboard-card"><div class="dashboard-card-title">🔬 V5.0 多因子数据 <span style="font-size:11px;color:var(--accent);font-weight:400">借鉴幻方量化</span></div>
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px">${cards.join('')}</div></div>`}

function renderInsightOverview(el,d){
const fgi=d.fearGreed||{};const val=d.valuation||{};const tech=d.technical||{};const news=(d.news||[]).slice(0,5);const macro=(d.macro||[]).slice(0,3);
const fgiScore=Math.max(0,Math.min(100,fgi.score||50));
const valPct=Math.min(Math.max(val.percentile||50,0),100);
const valColor=valPct<30?'var(--color-bull,#00E5A0)':valPct>70?'var(--color-bear,#FF6B6B)':'var(--color-brand-500,#FFB755)';
const dims=fgi.dimensions||{};

// SVG 仪表盘计算
const dashoffset=(251*(1-fgiScore/100)).toFixed(1);
const angle=-180+(fgiScore/100)*180;
const rad=angle*Math.PI/180;
const cx=(100+80*Math.cos(rad)).toFixed(1);
const cy=(90+80*Math.sin(rad)).toFixed(1);
const fgiLabel=fgiScore<=20?'极度恐慌':fgiScore<=40?'恐慌':fgiScore<=60?'中性':fgiScore<=80?'贪婪':'极度贪婪';

setExplain('fgi','恐惧贪婪指数','这个指数衡量市场情绪——大家是"怕得要死"还是"贪得无厌"。\n\n📊 怎么理解：\n• 0~25 = 极度恐惧（别人恐惧时贪婪？）\n• 25~45 = 恐惧\n• 45~55 = 中性\n• 55~75 = 贪婪\n• 75~100 = 极度贪婪（别人贪婪时恐惧？）\n\n🎯 当前：'+fgiScore.toFixed(0)+' - '+(fgi.level||fgiLabel)+'\n\n🔍 怎么用：\n• 巴菲特说"别人恐惧我贪婪"\n• 极度恐惧(<25)往往是好的买入时机\n• 极度贪婪(>75)要小心追高\n\n💡 但这只是参考，不是万能的买卖信号。');
setExplain('valuation','估值水平','估值就是看"这个市场现在贵不贵"。\n\n📊 核心指标 PE（市盈率）：\n• PE = 股价 ÷ 每股收益\n• PE 低 → 相对便宜\n• PE 高 → 相对贵\n\n🔍 百分位怎么看：\n• 当前：'+valPct+'%\n• 意思是"历史上只有 '+valPct+'% 的时候比现在便宜"\n• <30% → 便宜区间，适合加仓\n• 30~70% → 正常区间\n• >70% → 偏贵，谨慎追高\n\n🎯 PE: '+(val.current_pe||'-')+'\n\n💡 一句话：估值越低，安全边际越高，长期赚钱概率越大。');

// 资讯 tag 匹配
const tagMap=[{kw:['降息','降准','宽松','利好','上涨','增持','反弹'],tag:'利好',cls:'mb-tag--bull'},{kw:['加息','收紧','利空','下跌','减持','暴跌'],tag:'利空',cls:'mb-tag--bear'},{kw:['关税','制裁','贸易战','中美','战争','冲突'],tag:'警示',cls:'mb-tag--warn'}];
function getNewsTag(title){for(const t of tagMap){if(t.kw.some(k=>title.includes(k)))return t}return{tag:'中性',cls:'mb-tag--warn'}}

el.innerHTML=`
<!-- 恐慌贪婪指数 · SVG 半圆仪表盘 -->
<div class="dashboard-card" onclick="showExplain('fgi')" style="cursor:pointer;position:relative;overflow:hidden">
  <div style="position:absolute;top:-30px;right:-30px;width:120px;height:120px;background:radial-gradient(circle,rgba(255,183,85,.15),transparent 70%);filter:blur(15px)"></div>
  <div class="dashboard-card-title">🌡 恐慌贪婪指数 <span style="font-size:11px;color:var(--color-brand-500,#FFB755)">点击了解 ›</span></div>
  <div style="position:relative;text-align:center;padding:8px 0">
    <svg viewBox="0 0 200 100" style="width:100%;max-width:220px;margin:0 auto;display:block">
      <defs>
        <linearGradient id="fgGrad" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#FF6B6B"/>
          <stop offset="50%" stop-color="#FFB755"/>
          <stop offset="100%" stop-color="#00E5A0"/>
        </linearGradient>
      </defs>
      <path d="M20,90 A80,80 0 0,1 180,90" fill="none" stroke="rgba(255,255,255,.06)" stroke-width="10"/>
      <path d="M20,90 A80,80 0 0,1 180,90" fill="none" stroke="url(#fgGrad)"
            stroke-width="10" stroke-linecap="round" stroke-dasharray="251" stroke-dashoffset="${dashoffset}"/>
      <circle r="6" cx="${cx}" cy="${cy}" fill="var(--color-brand-500,#FFB755)" stroke="#fff" stroke-width="2"/>
    </svg>
    <div style="margin-top:8px">
      <span class="mb-money mb-money--lg" style="color:var(--color-brand-500,#FFB755)">${fgiScore.toFixed(0)}</span>
      <div class="mb-caption" style="margin-top:2px;letter-spacing:var(--ls-widest,2px)">${fgi.level||fgiLabel}</div>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--text-tertiary,#7A8499);padding:4px 20px 0">
      <span>恐慌</span><span>中性</span><span>贪婪</span>
    </div>
  </div>
  ${Object.keys(dims).length?`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px;background:rgba(0,0,0,.2);border-radius:var(--radius-md,10px);padding:10px">
    ${Object.values(dims).map(dm=>`<div style="text-align:center"><div style="font-size:9px;color:var(--text-tertiary,#7A8499);letter-spacing:.5px;margin-bottom:3px">${dm.label}</div><div style="font-size:16px;font-weight:700;color:var(--text-primary,#F0F2F7)">${dm.value}</div></div>`).join('')}
  </div>`:''}
</div>

<!-- 估值水平 · 渐变色条 -->
<div class="dashboard-card" onclick="showExplain('valuation')" style="cursor:pointer">
  <div class="dashboard-card-title">💎 估值水平 <span style="font-size:11px;color:var(--color-brand-500,#FFB755)">点击了解 ›</span></div>
  <div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:10px">
    <div>
      <div class="mb-money mb-money--md" style="color:${valColor}">${valPct}%</div>
      <div class="mb-caption">${val.index||'沪深300'} · ${val.level||'适中'}</div>
    </div>
    <div style="text-align:right;font-size:var(--fs-sm,11px);color:var(--text-tertiary,#7A8499)">PE: ${val.current_pe||'-'}${val.date?'<br>'+val.date:''}</div>
  </div>
  <div style="position:relative;height:6px;background:linear-gradient(90deg,var(--color-bull,#00E5A0) 0%,var(--color-bull,#00E5A0) 30%,var(--color-brand-500,#FFB755) 50%,var(--color-bear,#FF6B6B) 100%);border-radius:3px;margin-bottom:6px">
    <div style="position:absolute;top:-3px;left:${valPct}%;width:2px;height:12px;background:#fff;border-radius:1px;transform:translateX(-50%)"></div>
  </div>
  <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--text-tertiary,#7A8499)"><span>低估</span><span>适中</span><span>高估</span></div>
</div>

<!-- 技术 + 宏观 2x2 网格 -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px">
  <div class="dashboard-card" style="margin-bottom:0;cursor:pointer" onclick="insightTab='tech';renderInsight()">
    <div style="display:flex;align-items:center;gap:5px;font-size:10px;color:var(--text-tertiary,#7A8499);margin-bottom:5px">📈 RSI(14)</div>
    <div class="mb-money mb-money--sm" style="color:${tech.rsi>70?'var(--color-bear,#FF6B6B)':tech.rsi<30?'var(--color-bull,#00E5A0)':'var(--text-primary,#F0F2F7)'}">${tech.rsi||'-'}</div>
    <div style="font-size:9px;color:var(--text-tertiary,#7A8499)">${tech.rsi_signal||'—'}</div>
  </div>
  <div class="dashboard-card" style="margin-bottom:0;cursor:pointer" onclick="insightTab='tech';renderInsight()">
    <div style="display:flex;align-items:center;gap:5px;font-size:10px;color:var(--text-tertiary,#7A8499);margin-bottom:5px">📊 MACD</div>
    <div style="font-size:var(--fs-base,13px);font-weight:600;color:${tech.macd?.trend?.includes('多')?'var(--color-bull,#00E5A0)':tech.macd?.trend?.includes('空')?'var(--color-bear,#FF6B6B)':'var(--text-primary,#F0F2F7)'};line-height:1.4;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical">${tech.macd?.trend||'—'}</div>
    <div style="font-size:9px;color:var(--text-tertiary,#7A8499)">趋势</div>
  </div>
  <div class="dashboard-card" style="margin-bottom:0;cursor:pointer" onclick="insightTab='macro';renderInsight()">
    <div style="display:flex;align-items:center;gap:5px;font-size:10px;color:var(--text-tertiary,#7A8499);margin-bottom:5px">🏛 宏观</div>
    <div style="font-size:var(--fs-xl,22px);font-weight:700;color:var(--text-primary,#F0F2F7)">${macro.length||0}</div>
    <div style="font-size:9px;color:var(--text-tertiary,#7A8499)">近期事件</div>
  </div>
  <div class="dashboard-card" style="margin-bottom:0;cursor:pointer" onclick="insightTab='news';renderInsight()">
    <div style="display:flex;align-items:center;gap:5px;font-size:10px;color:var(--text-tertiary,#7A8499);margin-bottom:5px">📰 资讯</div>
    <div style="font-size:var(--fs-xl,22px);font-weight:700;color:var(--text-primary,#F0F2F7)">${(d.news||[]).length}</div>
    <div style="font-size:9px;color:var(--text-tertiary,#7A8499)">今日新闻</div>
  </div>
</div>

<!-- 资讯流 -->
${news.length?`<div class="dashboard-card">
  <div class="dashboard-card-title">📰 最新资讯</div>
  ${news.map(n=>{const t=getNewsTag(n.title);return`<div style="display:flex;align-items:flex-start;gap:10px;padding:10px 0;border-bottom:1px solid var(--border-subtle,rgba(255,255,255,.04))${n.url?';cursor:pointer':''}" ${n.url?`onclick="window.open('${n.url}','_blank')"`:''}><span class="mb-tag ${t.cls}" style="margin-top:2px;flex-shrink:0">${t.tag}</span><div style="flex:1"><div style="font-size:12px;font-weight:600;line-height:1.4;margin-bottom:3px">${n.title}</div><div style="font-size:10px;color:var(--text-tertiary,#7A8499)">${n.source||''}${n.time?' · '+n.time:''}</div></div></div>`}).join('')}
</div>`:''}

${renderV45FactorCards(d)}
<div id="allocationSection" class="dashboard-card" style="display:none"></div>
<div id="impactSection" class="dashboard-card" style="display:none"></div>
<div style="text-align:center;font-size:11px;color:var(--text-tertiary,#7A8499);margin-top:16px">更新于 ${new Date(d.updatedAt).toLocaleString('zh-CN')}</div>`;
// 异步加载资产配置建议
loadAllocationAdvice();
// 异步加载事件影响分析
fetch(API_BASE+'/news/impact',{signal:AbortSignal.timeout(30000)}).then(r=>r.json()).then(data=>{const sec=document.getElementById('impactSection');if(!sec||!data.impacts||!data.impacts.length)return;sec.style.display='';sec.innerHTML='<div class="dashboard-card-title">🔗 事件对你持仓的影响</div>'+data.impacts.map(imp=>{const bull=imp.bullish.length?'<span style="color:var(--color-bull,#00E5A0);font-size:11px">📈'+imp.bullish.join(',')+'</span>':'';const bear=imp.bearish.length?'<span style="color:var(--color-bear,#FF6B6B);font-size:11px">📉'+imp.bearish.join(',')+'</span>':'';return'<div style="padding:8px 0;border-bottom:1px solid var(--border-subtle,rgba(255,255,255,.04))"><div style="display:flex;align-items:center;gap:6px"><span class="mb-tag mb-tag--warn">'+imp.tag+'</span>'+bull+' '+bear+'</div><div style="font-size:12px;color:var(--text-secondary,#9AA1AC);margin-top:4px">'+imp.impact+'</div></div>'}).join('')}).catch(()=>{})}

function renderInsightNews(el,d){
const news=d.news||[];
// 情绪/影响标签匹配（增强版7类）
const tagMap=[{kw:['降息','降准','宽松','LPR','利好','上涨','增持','加仓','反弹'],tag:'🟢 利好',color:'var(--green)'},{kw:['加息','收紧','缩表','利空','下跌','减持','暴跌','回调'],tag:'🔴 利空',color:'var(--red)'},{kw:['关税','制裁','贸易战','中美'],tag:'⚠️ 贸易',color:'#F59E0B'},{kw:['战争','冲突','地缘','中东','俄乌'],tag:'🛡️ 地缘',color:'#F59E0B'},{kw:['半导体','芯片','AI','科技','人工智能'],tag:'🚀 科技',color:'var(--blue)'},{kw:['房地产','楼市','房价','限购'],tag:'🏠 地产',color:'#A78BFA'},{kw:['央行','货币','MLF','逆回购'],tag:'🏦 央行',color:'#06B6D4'}];
function getTag(title){for(const t of tagMap){if(t.kw.some(k=>title.includes(k)))return t}return null}
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">📰 市场新闻（${news.length}条）</div>${news.length?news.map(n=>{const t=getTag(n.title);const tagHtml=t?`<span style="font-size:10px;padding:1px 6px;border-radius:3px;background:rgba(255,255,255,.06);color:${t.color};margin-left:4px;white-space:nowrap">${t.tag}</span>`:'';return`<div class="news-item" onclick="${n.url?`window.open('${n.url}','_blank')`:''}"${n.url?'':' style="cursor:default"'}><div class="news-icon">📰</div><div class="news-content"><div class="news-title">${n.title}${tagHtml}</div><div class="news-meta">${n.source||''}${n.time?' · '+n.time:''}</div></div>${n.url?'<div class="news-arrow">›</div>':''}</div>`}).join(''):'<div style="text-align:center;padding:20px;color:var(--text2)">暂无新闻</div>'}</div>
<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="renderInsight()">🔄 刷新</button></div>`}

async function renderInsightPolicy(el){
const _policyCache=getCached('policy');if(_policyCache){el.innerHTML=_policyCache;return}
el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)"><div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 12px;border-width:3px"></div>正在加载政策新闻...</div>';
// 并行加载：传统政策新闻 + 分主题政策新闻 + AI影响分析（allSettled 防止一个超时拖垮全部）
const results = await Promise.allSettled([
  fetchPolicyNews(),
  API_AVAILABLE ? fetch(API_BASE+'/policy/all-topics',{signal:AbortSignal.timeout(45000)}).then(r=>r.ok?r.json():{}).catch(()=>({})) : Promise.resolve({}),
  API_AVAILABLE ? fetch(API_BASE+'/policy/impact',{signal:AbortSignal.timeout(30000)}).then(r=>r.ok?r.json():{}).catch(()=>({})) : Promise.resolve({})
]);
const news = results[0].status==='fulfilled' ? results[0].value : [];
const topicsResp = results[1].status==='fulfilled' ? results[1].value : {};
const impactResp = results[2].status==='fulfilled' ? results[2].value : {};
const topics = topicsResp.topics || topicsResp || {};
if(!news.length&&!Object.keys(topics).length){el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">暂无政策新闻</div>';return}
const policyNews=news.filter(n=>n.category==='policy');
const intlNews=news.filter(n=>n.category==='international');
// 主题分类
const topicMap=[['房地产','🏠','realestate'],['科技','🚀','tech'],['公积金','🏦','gongjijin'],['经济','📊','economy'],['房改','🏗️','fanggai']];
let topicHtml='';
topicMap.forEach(([label,icon,key])=>{
const items=Array.isArray(topics[key])?topics[key]:(topics[key]?.news||[]);
if(items.length){topicHtml+=`<div class="dashboard-card"><div class="dashboard-card-title">${icon} ${label}政策（${items.length}条）</div>${items.slice(0,5).map(n=>`<div class="news-item" onclick="${n.url?`window.open('${n.url}','_blank')`:''}"><div class="news-icon">${icon}</div><div class="news-content"><div class="news-title">${n.title}</div><div class="news-meta">${n.source||''}${n.time?' · '+n.time:''}</div></div>${n.url?'<div class="news-arrow">›</div>':''}</div>`).join('')}</div>`}});
// AI 影响分析卡
let impactHtml='';
if(impactResp.analysis&&impactResp.source==='ai'){
impactHtml=`<div class="dashboard-card" style="border:1px solid rgba(245,158,11,.2)"><div class="dashboard-card-title">🤖 AI 政策影响分析</div><div style="font-size:13px;color:var(--text1);line-height:1.7;white-space:pre-wrap;padding:8px 0">${impactResp.analysis}</div><div style="font-size:11px;color:#475569;margin-top:8px">分析了 ${impactResp.newsCount||0} 条政策新闻 · DeepSeek</div></div>`}
el.innerHTML=`
${impactHtml}
${topicHtml}
${policyNews.length?`<div class="dashboard-card"><div class="dashboard-card-title">🇨🇳 国内政策</div>${policyNews.map(n=>`<div class="news-item" onclick="${n.url?`window.open('${n.url}','_blank')`:''}"${n.url?'':' style="cursor:default"'}><div class="news-icon">📜</div><div class="news-content"><div class="news-title">${n.title}</div><div class="news-meta">${n.source||''}${n.time?' · '+n.time:''}</div></div>${n.url?'<div class="news-arrow">›</div>':''}</div>`).join('')}</div>`:''}
${intlNews.length?`<div class="dashboard-card"><div class="dashboard-card-title">🌍 国际动态</div>${intlNews.map(n=>`<div class="news-item" onclick="${n.url?`window.open('${n.url}','_blank')`:''}"${n.url?'':' style="cursor:default"'}><div class="news-icon">🌐</div><div class="news-content"><div class="news-title">${n.title}</div><div class="news-meta">${n.source||''}${n.time?' · '+n.time:''}</div></div>${n.url?'<div class="news-arrow">›</div>':''}</div>`).join('')}</div>`:''}
${!policyNews.length&&!intlNews.length&&!topicHtml?news.map(n=>`<div class="news-item"><div class="news-icon">📰</div><div class="news-content"><div class="news-title">${n.title}</div></div></div>`).join(''):''}
<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="insightTab='policy';renderInsight()">🔄 刷新</button></div>
<div style="text-align:center;font-size:11px;color:#475569;margin-top:8px">关键词: 政策/央行/关税/中美/美联储/地缘/半导体等</div>
<div id="policyBeneficiaryArea"></div>`;setCached('policy',el.innerHTML);
// 异步加载政策受益标的区域
_renderPolicyBeneficiaryCards();}

async function _renderPolicyBeneficiaryCards(){
const area=document.getElementById('policyBeneficiaryArea');
if(!area)return;
area.innerHTML='<div class="dashboard-card" style="margin-top:16px"><div class="dashboard-card-title">🏷️ 政策受益标的</div><div style="text-align:center;padding:16px;color:var(--text2)"><div class="loading-spinner" style="width:20px;height:20px;margin:0 auto 8px;border-width:2px"></div>正在分析热门政策受益方向...</div></div>';
try{
const r=await fetch(API_BASE+'/policy/tags',{signal:AbortSignal.timeout(20000)});
if(!r.ok)throw new Error('fetch failed');
const data=await r.json();
if(!data.available){area.innerHTML='';return}
const topics=data.topics||[];
const codeTags=data.code_tags||{};
// 按政策主题分组展示
let html='<div class="dashboard-card" style="margin-top:16px"><div class="dashboard-card-title">🏷️ 政策受益标的 <span style="font-size:10px;color:var(--text2);font-weight:400">'+data.total_codes+'只标的覆盖'+topics.length+'个主题</span></div>';
// 主题选择pills
html+='<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">';
topics.forEach((t,i)=>{html+='<button class="section-tab '+(i===0?'active':'')+'" onclick="_switchPolicyTopic(\''+t+'\')" data-policy-topic="'+t+'" style="font-size:11px;padding:4px 10px">'+t+'</button>'});
html+='</div>';
// 默认显示第一个主题
html+='<div id="policyTopicContent"></div></div>';
area.innerHTML=html;
// 加载第一个主题详情
if(topics.length)_switchPolicyTopic(topics[0]);
}catch(e){console.warn('Policy beneficiary cards failed:',e);area.innerHTML=''}}

async function _switchPolicyTopic(topic){
// 高亮选中pill
document.querySelectorAll('[data-policy-topic]').forEach(btn=>{btn.classList.toggle('active',btn.dataset.policyTopic===topic)});
const container=document.getElementById('policyTopicContent');
if(!container)return;
container.innerHTML='<div style="text-align:center;padding:12px;color:var(--text2)"><div class="loading-spinner" style="width:16px;height:16px;margin:0 auto 6px;border-width:2px"></div></div>';
try{
const r=await fetch(API_BASE+'/policy/beneficiaries?topic='+encodeURIComponent(topic),{signal:AbortSignal.timeout(20000)});
if(!r.ok)throw new Error('fetch failed');
const d=await r.json();
if(!d.available){container.innerHTML='<div style="text-align:center;padding:12px;color:var(--text2)">暂无数据</div>';return}
let h='';
if(d.summary)h+='<div style="font-size:12px;color:var(--text1);margin-bottom:10px;padding:8px;background:rgba(245,158,11,.06);border-radius:8px">💡 '+d.summary+'</div>';
if(d.industries&&d.industries.length)h+='<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:10px">'+d.industries.map(i=>'<span style="font-size:10px;padding:2px 8px;border-radius:10px;background:rgba(99,102,241,.1);color:#818CF8">'+i+'</span>').join('')+'</div>';
// 基金列表
if(d.funds&&d.funds.length){h+='<div style="font-size:11px;color:var(--text2);font-weight:600;margin-bottom:6px">📊 受益基金</div>';
d.funds.forEach(f=>{h+='<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.06);cursor:pointer" onclick="showFundDetailModal(\''+f.code+'\',\''+((f.name||'').replace(/'/g,''))+'\')">'
+'<div style="font-size:12px;font-weight:600;flex:1">'+f.name+'<span style="font-size:10px;color:var(--text2);margin-left:4px">'+f.code+'</span></div>'
+'<div style="font-size:10px;color:#FBBF24;background:rgba(245,158,11,.1);padding:2px 6px;border-radius:4px">'+f.reason+'</div></div>'})}
// 股票列表
if(d.stocks&&d.stocks.length){h+='<div style="font-size:11px;color:var(--text2);font-weight:600;margin:10px 0 6px">📈 受益个股</div>';
d.stocks.forEach(s=>{h+='<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.06);cursor:pointer" onclick="showFundChart(\''+s.code+'\')">'
+'<div style="font-size:12px;font-weight:600;flex:1">'+s.name+'<span style="font-size:10px;color:var(--text2);margin-left:4px">'+s.code+'</span></div>'
+'<div style="font-size:10px;color:#FBBF24;background:rgba(245,158,11,.1);padding:2px 6px;border-radius:4px">'+s.reason+'</div></div>'})}
container.innerHTML=h||'<div style="text-align:center;padding:12px;color:var(--text2)">暂无受益标的数据</div>';
}catch(e){container.innerHTML='<div style="text-align:center;padding:12px;color:var(--text2)">加载失败，请稍后重试</div>'}}

function renderInsightTech(el,d){
const tech=d.technical||{};const m=tech.macd||{};const b=tech.bollinger||{};
setExplain('rsi','RSI 相对强弱指标','RSI 就像温度计，测量市场的"热度"。\n\n📊 数值含义：\n• 50 = 中性，多空力量平衡\n• 超过 70 = 市场过热（大家都在买，可能快到顶了）\n• 低于 30 = 市场过冷（大家都在卖，可能快到底了）\n\n🎯 当前 RSI = '+(tech.rsi||50)+'\n'+(tech.rsi>70?'⚠️ 偏高，短期可能回调，不宜追涨':tech.rsi<30?'💡 偏低，可能被超卖，可以关注抄底机会':'✅ 中性区间，市场情绪正常')+'\n\n💡 小贴士：RSI 不能单独使用，要结合其他指标一起看。');
setExplain('macd','MACD 指标','MACD 就像两条"均线赛跑"——快线(DIF)和慢线(DEA)。\n\n📊 核心概念：\n• DIF(快线) 和 DEA(慢线) 是两条趋势线\n• MACD柱 = 两线差值，代表动能强弱\n\n🔍 怎么看：\n• 金叉（快线上穿慢线）→ 可能要涨\n• 死叉（快线下穿慢线）→ 可能要跌\n• 柱子变长 = 趋势在加强\n• 柱子变短 = 趋势在减弱\n\n🎯 当前：'+(m.trend||'—')+'\nDIF='+(m.dif?.toFixed(2)||'—')+' DEA='+(m.dea?.toFixed(2)||'—')+'\n\n💡 小贴士：MACD 反应较慢，适合看中长期趋势，不适合抓短线。');
setExplain('bollinger','布林带','布林带就像给股价画了一条"通道"，有上中下三条线。\n\n📊 三条线：\n• 上轨('+(b.upper||'—')+') = 压力线，价格碰到容易回落\n• 中轨('+(b.middle||'—')+') = 移动平均线，大趋势方向\n• 下轨('+(b.lower||'—')+') = 支撑线，价格碰到容易反弹\n\n🔍 怎么看：\n• 现价在上轨附近 → 偏贵，可能回调\n• 现价在下轨附近 → 偏便宜，可能反弹\n• 通道变窄 → 即将有大波动（变盘信号）\n• 通道变宽 → 波动剧烈，注意风险\n\n🎯 当前：现价 '+(b.current||'—')+'，'+(b.position||'')+'\n\n💡 小贴士：布林带帮你判断价格"贵不贵"，但不能预测方向。');
el.innerHTML=`
<div class="dashboard-card" onclick="showExplain('rsi')" style="cursor:pointer"><div class="dashboard-card-title">📊 RSI 相对强弱指标 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div style="text-align:center"><div style="font-size:48px;font-weight:900;color:${tech.rsi>70?'var(--red)':tech.rsi<30?'var(--green)':'var(--accent)'}">${tech.rsi||50}</div>
<div style="font-size:14px;color:var(--text2);margin-top:4px">${tech.rsi_signal||'中性'}</div>
<div class="val-bar" style="margin:12px 0"><div class="val-bar-fill" style="width:${tech.rsi||50}%;background:${tech.rsi>70?'var(--red)':tech.rsi<30?'var(--green)':'var(--accent)'}"></div></div>
<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text2)"><span>超卖 (&lt;30)</span><span>中性</span><span>超买 (&gt;70)</span></div></div></div>
<div class="dashboard-card" onclick="showExplain('macd')" style="cursor:pointer"><div class="dashboard-card-title">📈 MACD 指数平滑移动平均 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div class="tech-grid"><div class="tech-item"><div class="tech-label">趋势</div><div class="tech-value" style="font-size:13px;color:${m.trend?.includes('金叉')||m.trend?.includes('多头')?'var(--green)':'var(--red)'}">${m.trend||'—'}</div></div>
<div class="tech-item"><div class="tech-label">DIF</div><div class="tech-value">${m.dif?.toFixed(2)||'—'}</div></div>
<div class="tech-item"><div class="tech-label">DEA</div><div class="tech-value">${m.dea?.toFixed(2)||'—'}</div></div>
<div class="tech-item"><div class="tech-label">MACD柱</div><div class="tech-value" style="color:${m.macd>0?'var(--green)':'var(--red)'}">${m.macd?.toFixed(2)||'—'}</div></div></div></div>
<div class="dashboard-card" onclick="showExplain('bollinger')" style="cursor:pointer"><div class="dashboard-card-title">📐 布林带 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div class="tech-grid"><div class="tech-item"><div class="tech-label">上轨</div><div class="tech-value">${b.upper||'—'}</div></div>
<div class="tech-item"><div class="tech-label">中轨</div><div class="tech-value">${b.middle||'—'}</div></div>
<div class="tech-item"><div class="tech-label">下轨</div><div class="tech-value">${b.lower||'—'}</div></div>
<div class="tech-item"><div class="tech-label">现价</div><div class="tech-value">${b.current||'—'}</div></div></div>
<div style="text-align:center;margin-top:12px;font-size:13px;color:var(--text2)">${b.position||''}</div></div>
<div style="text-align:center;padding:16px;font-size:12px;color:#475569;line-height:1.6">💡 点击任意卡片查看白话解释 · 技术指标是辅助参考，需结合估值和基本面综合判断</div>`}

async function renderInsightMacro(el,d){
let macro=d.macro||[];
// dashboard 走缓存时没有 macro，独立调 /api/macro 拉取
if(!macro.length){
  el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)"><div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 12px;border-width:3px"></div>正在加载宏观数据...</div>';
  try{
    const r=await fetch(API_BASE+'/macro',{signal:AbortSignal.timeout(15000)});
    const j=await r.json();
    macro=j.events||[];
  }catch(e){console.warn('[macro] fetch failed:',e);macro=[]}
}
const macroKeyMap={'CPI':'cpi','PMI':'pmi','M2':'m2','PPI':'ppi'};
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">🏛️ 宏观经济数据 <span style="font-size:11px;color:var(--accent)">点击查看白话解释</span></div>
${macro.length?macro.map((e,i)=>{const mkey=Object.keys(macroKeyMap).find(k=>e.name.includes(k));const explainKey=mkey?macroKeyMap[mkey]:'macro_'+i;if(!mkey){setExplain(explainKey,e.name,'📊 '+e.name+'\n\n'+e.impact+'\n\n点击查看更多：可在百度搜索「'+e.name+' 最新数据」了解详情。')}return`<div class="macro-item" onclick="showExplain('${explainKey}')" style="cursor:pointer"><div class="macro-icon">${e.icon||'📅'}</div><div class="macro-info"><div class="macro-name">${e.name}</div><div class="macro-value">${e.value||'—'}</div><div class="macro-date">${e.date||''}</div><div class="macro-impact">${e.impact||''}</div></div><div class="news-arrow">›</div></div>`}).join(''):'<div style="text-align:center;padding:20px;color:var(--text2)">暂无数据</div>'}
</div>
<div style="padding:16px;font-size:12px;color:#475569;line-height:1.6">💡 点击任意数据查看白话解释 · 宏观数据影响市场整体方向</div>`}

// 全球市场数据页
async function renderInsightGlobal(el){
const _gc=getCached('global');if(_gc){el.innerHTML=_gc;return}
el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)"><div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 12px;border-width:3px"></div>正在加载全球市场数据...</div>';
try{
const [snap,impact]=await Promise.all([
fetch(API_BASE+'/global/snapshot').then(r=>r.json()),
fetch(API_BASE+'/global/impact').then(r=>r.json()).catch(()=>({analysis:'暂不可用',source:'none'}))
]);
const us=snap.us_indices||{};const fx=snap.forex||{};const fed=snap.fed_rate||{};const gpe=snap.global_pe||{};
let h='<div class="dashboard-card"><div class="dashboard-card-title">🌐 全球市场实时</div>';
// 美股三大指数
const idxArr=[['dji','道琼斯'],['spx','标普500'],['ixic','纳斯达克']];
h+=idxArr.map(([k,n])=>{const d=us[k];if(!d)return'';const c=d.change_pct>0?'var(--green)':d.change_pct<0?'var(--red)':'var(--text2)';return`<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--bg3)"><div style="font-size:13px;font-weight:600">${d.change_pct>0?'📈':'📉'} ${n}</div><div style="text-align:right"><div style="font-size:14px;font-weight:700">${d.close.toLocaleString()}</div><div style="font-size:12px;color:${c};font-weight:600">${d.change_pct>0?'+':''}${d.change_pct}%</div></div></div>`}).join('');
// 外汇
if(fx.usdcny)h+=`<div style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--bg3)"><div style="font-size:13px">💱 美元/人民币</div><div style="font-size:14px;font-weight:700">${fx.usdcny.rate.toFixed(4)}</div></div>`;
// 美联储
if(fed.available)h+=`<div style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--bg3)"><div style="font-size:13px">🏛️ 美联储利率</div><div style="text-align:right"><div style="font-size:14px;font-weight:700">${fed.current_rate}%</div><div style="font-size:11px;color:var(--text2)">${fed.trend==='hiking'?'⬆️加息周期':fed.trend==='cutting'?'⬇️降息周期':'按兵不动'}</div></div></div>`;
// PE 对比
if(gpe.available&&gpe.us_pe&&gpe.cn_pe)h+=`<div style="display:flex;justify-content:space-between;padding:10px 0"><div style="font-size:13px">📊 PE 估值对比</div><div style="text-align:right"><div style="font-size:12px">🇺🇸 ${gpe.us_pe} vs 🇨🇳 ${gpe.cn_pe}</div><div style="font-size:11px;color:var(--text2)">${gpe.assessment||''}</div></div></div>`;
h+='</div>';
// DeepSeek 影响分析（markdown → HTML）
if(impact.analysis){const analysisHtml=typeof mdLite==='function'?mdLite(impact.analysis):impact.analysis.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\n/g,'<br>');h+=`<div class="dashboard-card"><div class="dashboard-card-title">🤖 AI 全球→A股影响分析 <span style="font-size:10px;color:var(--text-tertiary,#7A8499)">${impact.source==='ai'?'DeepSeek':'数据'}</span></div><div style="font-size:13px;line-height:1.8">${analysisHtml}</div></div>`}
el.innerHTML=h;setCached('global',h);
}catch(e){el.innerHTML=typeof renderFetchError==='function'?renderFetchError('全球数据加载失败',"renderInsightGlobal(document.getElementById('insightContent'))"):'<div class="mb-empty"><div class="mb-empty__icon">🌐</div><div class="mb-empty__title">全球数据暂不可用</div></div>'}}

// 政策标签缓存（选基/选股列表徽章用）
let _policyTagsCache=null;let _policyTagsLoading=false;
async function _loadPolicyTags(){
if(_policyTagsCache)return _policyTagsCache;
if(_policyTagsLoading)return null;
_policyTagsLoading=true;
try{const r=await fetch(API_BASE+'/policy/tags',{signal:AbortSignal.timeout(15000)});
if(!r.ok)throw new Error('policy tags fetch failed');
const data=await r.json();
if(data.available){_policyTagsCache=data.code_tags||{};
// 标签加载完后，如果正在选基/选股页面，刷新列表展示徽章
if(insightTab==='fundpick'){const el=document.getElementById('fundPickList');if(el&&el.children.length>1)renderFundPickResult()}
if(insightTab==='stockpick'){const el=document.getElementById('stockPickList');if(el&&el.children.length>1){const cached=getCached('stock_screen');if(cached)_fillStockList(cached)}}
}}catch(e){console.warn('Policy tags load failed:',e)}
_policyTagsLoading=false;
return _policyTagsCache}
// 启动时异步预加载政策标签（不阻塞渲染）
setTimeout(()=>_loadPolicyTags(),2000);

// 政策关键词→主题映射（用于基金/股票名称匹配）
const _POLICY_KEYWORD_MAP={
'数字基建':['5G','数据中心','云计算','物联网','工业互联','数字','通信','信息'],
'AI算力':['人工智能','AI','算力','智能','机器人','大模型','芯片','科技','创新','计算机'],
'新能源':['新能源','光伏','风电','储能','锂电','碳中和','绿电','电力','能源'],
'半导体':['半导体','芯片','集成电路','IC','封测','电子'],
'国产替代':['国产','自主可控','信创','安全','国芯','华为','军工']
};

function _policyBadgesHTML(code, name){
// 精确代码匹配（需要 API 数据加载完）
let topics=(_policyTagsCache&&code)?_policyTagsCache[code]:null;
// 名称关键词匹配（不需要等 API）
if((!topics||!topics.length)&&name){
const matched=[];
for(const[topic,keywords]of Object.entries(_POLICY_KEYWORD_MAP)){
if(keywords.some(kw=>name.includes(kw)))matched.push(topic)}
if(matched.length)topics=matched}
if(!topics||!topics.length)return '';
return topics.map(t=>'<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(245,158,11,.12);color:#FBBF24">🏷️'+t+'</span>').join('')}

// 基金智能筛选页
let fundPickType='all';let fundPickSort='score';
function _fundTagsHTML(f){const r=f.returns;const tags=[];if(r['1y']!=null&&r['3m']!=null&&r['6m']!=null&&r['1y']>0&&r['3m']>0&&r['6m']>0)tags.push('📈稳定上涨');if(r['1y']!=null&&r['1y']>15)tags.push('\u{1F525}高收益');if(f.fee&&parseFloat(f.fee)<0.5)tags.push('💰低费率');if(r['3y']!=null&&r['3y']>30)tags.push('⭐长期优秀');const policyBadges=_policyBadgesHTML(f.code,f.name||'');let h='';if(tags.length||policyBadges)h+='<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:4px">'+tags.map(t=>'<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(16,185,129,.1);color:#6EE7B7">'+t+'</span>').join('')+policyBadges+'</div>';if(f.aiComment){let cmt=f.aiComment.replace(/^[\s\S]*?(?:逐只思考[：:]?\s*|思考[：:]?\s*|分析[：:]?\s*)/,'').trim();if(cmt&&cmt.length>2)h+='<div style="font-size:12px;color:#E0E7FF;padding:6px 10px;background:rgba(99,102,241,.08);border-radius:8px;line-height:1.5">\u{1F916} '+cmt+'</div>';}return h?'<div style="padding:4px 12px 8px 32px">'+h+'</div>':''}
function _fundPickBtnsHTML(){
return `<div id="fundPickTypeBar" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
${[['all','全部'],['stock','股票型'],['bond','债券型'],['index','指数型'],['qdii','QDII']].map(([k,l])=>`<button class="section-tab ${fundPickType===k?'active':''}" onclick="fundPickType='${k}';_updateFundPickBtns();renderFundPickResult()" style="font-size:12px;padding:5px 10px">${l}</button>`).join('')}
</div>
<div id="fundPickSortBar" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">
${[['score','📊 综合评分'],['1y','📈 近1年'],['3y','📈 近3年'],['ytd','📈 今年来']].map(([k,l])=>`<button class="section-tab ${fundPickSort===k?'active':''}" onclick="fundPickSort='${k}';_updateFundPickBtns();renderFundPickResult()" style="font-size:11px;padding:4px 8px">${l}</button>`).join('')}
</div>`}
function _updateFundPickBtns(){
const tb=document.getElementById('fundPickTypeBar');const sb=document.getElementById('fundPickSortBar');
if(tb)tb.innerHTML=[['all','全部'],['stock','股票型'],['bond','债券型'],['index','指数型'],['qdii','QDII']].map(([k,l])=>`<button class="section-tab ${fundPickType===k?'active':''}" onclick="fundPickType='${k}';_updateFundPickBtns();renderFundPickResult()" style="font-size:12px;padding:5px 10px">${l}</button>`).join('');
if(sb)sb.innerHTML=[['score','📊 综合评分'],['1y','📈 近1年'],['3y','📈 近3年'],['ytd','📈 今年来']].map(([k,l])=>`<button class="section-tab ${fundPickSort===k?'active':''}" onclick="fundPickSort='${k}';_updateFundPickBtns();renderFundPickResult()" style="font-size:11px;padding:4px 8px">${l}</button>`).join('')}
async function renderFundPick(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🔍 基金智能筛选</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:12px">多维度打分：近1年(30%)+近3年(20%)+近6月(15%)+近3月(10%)+稳定性(15%)+费率(10%)</div>
${_fundPickBtnsHTML()}
<div id="fundPickList"><div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在筛选基金...</div></div>
</div>`;
renderFundPickResult()}

async function renderFundPickResult(){
const listEl=document.getElementById('fundPickList');
if(!listEl)return;
const cacheKey='fund_screen_'+fundPickType+'_'+fundPickSort;
const cached=getCached(cacheKey);
if(cached){_showFundData(listEl,cached);return}
listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在筛选基金...</div>';
try{
const r=await fetch(API_BASE+'/fund-screen?fund_type='+fundPickType+'&sort_by='+fundPickSort+'&top_n=20',{signal:AbortSignal.timeout(30000)});
if(!r.ok)throw new Error('fetch failed');
const data=await r.json();
setCached(cacheKey,data);
_showFundData(listEl,data);
}catch(e){console.warn('Fund pick failed:',e);listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">📡 数据源加载中，请稍后重试<br><span style="font-size:11px;opacity:0.6">（首次加载可能需要 10-30 秒）</span><br><button onclick="renderFundPickResult()" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>'}}
function _showFundData(listEl,data){
const funds=data.funds||[];
if(!funds.length){listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">暂无符合条件的基金</div>';return}
// 大盘时机横幅
const mt=data.market_timing||{};
const timingBanner=mt.signal?`<div style="display:flex;align-items:center;gap:8px;padding:10px 12px;margin-bottom:12px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.12);border-radius:10px"><span style="font-size:18px">${mt.signal}</span><div><div style="font-size:12px;font-weight:700;color:var(--text1)">大盘时机: ${mt.verdict}</div><div style="font-size:11px;color:var(--text2)">${mt.detail}</div></div></div>`:'';
listEl.innerHTML=`${timingBanner}<div style="font-size:11px;color:var(--text2);margin-bottom:8px">共筛选 ${data.total} 只基金，显示 TOP ${funds.length}</div>
${funds.map((f,i)=>{
const scoreColor=f.score>15?'var(--green)':f.score>5?'var(--accent)':'var(--red)';
const r1y=f.returns['1y'];const r3y=f.returns['3y'];const rytd=f.returns.ytd;
const r1yColor=r1y>0?'var(--green)':'var(--red)';
return`<div style="display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid rgba(148,163,184,.06);cursor:pointer" onclick="showFundDetailModal('${f.code}','${(f.name||'').replace(/'/g,'')}')">
<div style="font-size:12px;color:var(--text2);min-width:20px;text-align:center;font-weight:700">${i+1}</div>
<div style="flex:1;min-width:0">
<div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${f.name}</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">${f.code} · 费率${f.fee||'-'}${f.timing_label?' · <b>'+f.timing_label+'</b>':''}</div></div>
<div style="text-align:right;min-width:70px">
<div style="font-size:14px;font-weight:800;color:${r1yColor}">${r1y!=null?(r1y>0?'+':'')+r1y+'%':'—'}</div>
<div style="font-size:10px;color:var(--text2)">近1年</div></div>
<div style="min-width:40px;text-align:right">
<div style="font-size:12px;font-weight:700;color:${scoreColor}">${f.score}</div>
<div style="font-size:10px;color:var(--text2)">评分</div></div>
<button onclick="event.stopPropagation();showFundChart('${f.code}')" style="padding:3px 6px;font-size:10px;border:1px solid var(--accent);border-radius:4px;background:transparent;color:var(--accent);cursor:pointer;white-space:nowrap">K线</button></div>${_fundTagsHTML(f)}`}).join('')}
<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="renderFundPickResult()">🔄 刷新</button></div>`;
// 注册每只基金的白话弹窗
funds.forEach(f=>{
const r=f.returns;
setExplain('fund_'+f.code,f.name+' ('+f.code+')',
'📊 综合评分：'+f.score+'\n\n📈 收益表现：\n• 近3月：'+(r['3m']!=null?r['3m']+'%':'—')+'\n• 近6月：'+(r['6m']!=null?r['6m']+'%':'—')+'\n• 近1年：'+(r['1y']!=null?r['1y']+'%':'—')+'\n• 近3年：'+(r['3y']!=null?r['3y']+'%':'—')+'\n• 今年来：'+(r.ytd!=null?r.ytd+'%':'—')+'\n\n💰 费率：'+(f.fee||'—')+'\n\n💡 评分方法：近1年35%+近3年25%+近6月20%+近3月10%+费率加减分。仅供参考，不构成投资建议。',
{type:'fund',code:f.code,name:f.name,score:f.score,fee:f.fee||'',returns:r})
})}

// AI 多因子选股页
function _stockTagsHTML(s){const sc=s.scores||{};const tags=[];if(sc.value>=70)tags.push('💰低估值');if(sc.momentum>=70)tags.push('📈强动量');if(sc.liquidity>=70)tags.push('🏦高流动');if(sc.risk>=80)tags.push('🛡️低风险');if(sc.quality>=75)tags.push('⭐高质量');if(sc.growth>=70)tags.push('🚀高成长');if(s.roe&&s.roe>20)tags.push('💎ROE>20%');if(s.gross_margin&&s.gross_margin>50)tags.push('🏆高毛利');const policyBadges=_policyBadgesHTML(s.code?s.code.replace(/^(sh|sz)/i,''):'',s.name||'');let h='';if(tags.length||policyBadges)h+='<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:4px">'+tags.map(t=>'<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(99,102,241,.1);color:#818CF8">'+t+'</span>').join('')+policyBadges+'</div>';if(s.aiComment)h+='<div style="font-size:12px;color:#E0E7FF;padding:6px 10px;background:rgba(99,102,241,.08);border-radius:8px;line-height:1.5">\u{1F916} '+s.aiComment+'</div>';return h?'<div style="padding:4px 0 8px 34px;border-bottom:1px solid rgba(148,163,184,.04)">'+h+'</div>':''}
async function renderStockPick(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🧠 AI 多因子选股</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">30因子7维打分 V3：AI 动态权重 + LLM 舆情 + 因子生成器加分</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">⚠️ 含真实财务数据（ROE/毛利率/净利率/现金流/负债率），DeepSeek 根据市场环境动态调权重。仅供参考，不构成投资建议。</div>
<div id="stockScreenMeta" style="display:none;font-size:11px;color:var(--accent);margin-bottom:8px;padding:6px 8px;background:rgba(59,130,246,.06);border-radius:6px"></div>
<div id="stockPickList"><div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在从 5000+ A股中筛选（AI 动态调权中）...</div></div>
</div>`;
const _stockCache=getCached('stock_screen');
if(_stockCache){_fillStockList(_stockCache);return}
try{
const r=await fetch(API_BASE+'/stock-screen?top_n=50',{signal:AbortSignal.timeout(60000)});
if(!r.ok)throw new Error('fetch failed');
const data=await r.json();
setCached('stock_screen',data);
_fillStockList(data);
}catch(e){console.warn('Stock pick failed:',e);
const listEl=document.getElementById('stockPickList');
if(listEl)listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">📡 选股数据加载中<br><span style="font-size:11px;opacity:0.6">需分析5000+只A股，首次约30秒</span><br><span style="font-size:11px;opacity:0.5">非交易时段数据源可能不稳定</span><br><button onclick="insightTab=\'stockpick\';renderInsight()" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>'}}
function _fillStockList(data){
const stocks=data.stocks||[];
const listEl=document.getElementById('stockPickList');if(!listEl)return;
// 大盘时机横幅
const mt=data.market_timing||{};
const timingBanner=mt.signal?`<div style="display:flex;align-items:center;gap:8px;padding:10px 12px;margin-bottom:10px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.12);border-radius:10px"><span style="font-size:18px">${mt.signal}</span><div><div style="font-size:12px;font-weight:700;color:var(--text1)">大盘时机: ${mt.verdict}</div><div style="font-size:11px;color:var(--text2)">${mt.detail}</div></div></div>`:'';
// 展示 V3 动态权重元信息
const metaEl=document.getElementById('stockScreenMeta');
if(metaEl&&(data.regime||data.weights)){
const regime=data.regime||'未知';
const _regimeMap={'trending_bull':'趋势牛市','trending_bear':'趋势熊市','volatile':'震荡市','neutral':'中性','recovery':'修复期','overheated':'过热','panic':'恐慌'};
const regimeZh=_regimeMap[regime]||regime;
const weights=data.weights||{};
const _wMap={'value':'价值','growth':'成长','quality':'质量','momentum':'动量','risk':'风险','liquidity':'流动性','sentiment':'舆情'};
const wText=Object.entries(weights).map(([k,v])=>`${_wMap[k]||k}:${v}%`).join(' · ');
metaEl.innerHTML=`🧠 市场判断: <b>${regimeZh}</b> | 动态权重: ${wText}`;
metaEl.style.display='block';
}
if(!stocks.length){listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">'+(data.error||'暂无数据')+'</div>';return}
window._stockScreenData=stocks;
listEl.innerHTML=`${timingBanner}<div style="font-size:11px;color:var(--text2);margin-bottom:8px">从 ${data.total} 只股票中筛选 TOP ${stocks.length}</div>
<div style="display:grid;grid-template-columns:30px 1fr 70px 50px 32px;gap:4px;font-size:11px;color:var(--text2);font-weight:600;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.1)">
<div>#</div><div>股票</div><div style="text-align:right">涨跌</div><div style="text-align:right">评分</div><div></div></div>
${stocks.map((s,i)=>{
const chgColor=s.change_pct>0?'var(--green)':s.change_pct<0?'var(--red)':'var(--text2)';
const scoreColor=s.score>65?'var(--green)':s.score>50?'var(--accent)':'var(--red)';
return`<div style="display:grid;grid-template-columns:30px 1fr 70px 50px 32px;gap:4px;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.04);align-items:center;cursor:pointer" onclick="showStockDetailModal(window._stockScreenData[${i}])">
<div style="font-size:11px;color:var(--text2);font-weight:700">${i+1}</div>
<div><div style="font-size:13px;font-weight:600">${s.name}</div>
<div style="font-size:10px;color:var(--text2)">${s.code.replace(/^(sh|sz)/i,'')} · PE ${s.pe!=null?s.pe:'暂无'} · ${s.market_cap?s.market_cap+'亿':'-'}${s.roe?' · ROE'+s.roe+'%':''}${s.timing_label?' · <b>'+s.timing_label+'</b>':''}</div></div>
<div style="text-align:right;font-size:13px;font-weight:700;color:${chgColor}">${s.change_pct!=null?(s.change_pct>0?'+':'')+s.change_pct+'%':'—'}</div>
<div style="text-align:right;font-size:13px;font-weight:800;color:${scoreColor}">${s.score}</div>
<button onclick="event.stopPropagation();showFundChart('${s.code.replace(/^(sh|sz)/i,'')}')" style="padding:2px 4px;font-size:9px;border:1px solid var(--accent);border-radius:3px;background:transparent;color:var(--accent);cursor:pointer">K线</button></div>${_stockTagsHTML(s)}`}).join('')}
<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="insightTab='stockpick';renderInsight()">🔄 刷新</button></div>
<div style="font-size:11px;color:#475569;margin-top:8px;line-height:1.5">${data.method||''}<br>${data.note||''}</div>`;
stocks.forEach(s=>{
const sc=s.scores||{};
setExplain('stock_'+s.code,s.name+' ('+s.code+')',
'💰 价格：¥'+s.price+' · 涨跌：'+(s.change_pct!=null?s.change_pct+'%':'—')+'\n📊 PE：'+(s.pe||'—')+' · PB：'+(s.pb||'—')+' · 换手率：'+(s.turnover||'—')+'%\n📈 市值：'+(s.market_cap?s.market_cap+'亿':'—')+'\n\n📋 财务指标：\n• ROE：'+(s.roe||'—')+'%\n• 毛利率：'+(s.gross_margin||'—')+'%\n• 净利率：'+(s.net_margin||'—')+'%\n• 负债率：'+(s.debt_ratio||'—')+'%\n• 营收增速：'+(s.revenue_growth||'—')+'%\n• EPS：'+(s.eps||'—')+'\n\n🎯 综合评分：'+s.score+'/100\n\n7维30因子详情：\n• 价值(20%)：'+sc.value+' (PE/PB/股息率/ROE-PB/EPS/低PE高ROE)\n• 成长(15%)：'+sc.growth+' (营收增速/ROE/EPS/60日动量/PEG)\n• 质量(18%)：'+sc.quality+' (ROE/毛利率/净利率/负债率/现金流/市值)\n• 动量(15%)：'+sc.momentum+' (5日/20日/60日/今日)\n• 风险(12%)：'+sc.risk+' (振幅/负债率/现金流/PE极端)\n• 流动性(10%)：'+sc.liquidity+' (换手率/市值/成交额)\n• 舆情(10%)：'+sc.sentiment+' (新闻情绪/LLM评分)\n\n⚠️ 仅供参考，不构成投资建议。',
{type:'stock',code:s.code,name:s.name,score:s.score,pe:s.pe||0,roe:s.roe||0,gross_margin:s.gross_margin||0})
})}


// --- 02-insight-protabs.js ---
/* =========================================================================
 * V6 欠账 2/6：insight 页 deep-impact + risk-assess 两个 Pro Tab
 * 方式：劫持 _insightTabs()，在 Pro 模式下追加两个 tab
 *       劫持 renderInsight()，拦截新 tab 渲染
 * 依赖 API：/api/news/impact, /api/risk-metrics, /api/risk-actions
 * ========================================================================= */
;(function(){
  'use strict';

  const NEW_TABS = [
    ['deepimpact', '💥 深度影响'],
    ['riskassess', '🛡️ 风险评估']
  ];

  // --- 劫持 _insightTabs：Pro 模式下追加两个 tab ---
  function _patchTabs(){
    if (typeof _insightTabs !== 'function') return false;
    if (_insightTabs.__v6Patched) return true;
    const orig = _insightTabs;
    window._insightTabs = function(){
      const tabs = orig();
      if (isProMode()) {
        // 加在 weekly 之后
        NEW_TABS.forEach(t => {
          if (!tabs.find(x => x[0] === t[0])) tabs.push(t);
        });
      }
      return tabs;
    };
    window._insightTabs.__v6Patched = true;
    return true;
  }

  // --- deep-impact 渲染 ---
  async function renderDeepImpact(el){
    el.innerHTML = _v6Skeleton('正在分析新闻深度影响...');
    // 优先用 deep-impact（AI 深度分析），fallback 到 news/impact（规则分析）
    let d = await _v6Fetch('/news/deep-impact');
    // API 返回字段：impacts (数组), 每项: {title, sectors, direction, magnitude, impact}
    let items = (d && d.impacts) ? d.impacts : null;
    if (!items || !items.length) {
      // fallback：news/impact 端点，字段: impacts[{title, fund_code, impact_level, analysis}]
      const d2 = await _v6Fetch('/news/impact');
      items = (d2 && d2.impacts) ? d2.impacts : null;
    }
    if (!items || !items.length) {
      el.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text2)"><div style="font-size:24px;margin-bottom:12px">💥</div><div style="font-size:14px;font-weight:600;margin-bottom:8px">新闻深度影响分析</div><div style="font-size:13px;line-height:1.6">当前没有检测到对持仓有显著影响的新闻事件。<br><br>录入持仓后，系统会自动分析新闻对你的资产的影响。</div></div>';
      return;
    }
    let html = `<div class="section-title">💥 新闻深度影响分析 <span style="font-size:11px;color:var(--accent);font-weight:400">Phase 5 · AI 驱动</span></div>`;
    items.forEach(item => {
      // deep-impact 端点用 direction 字段；news/impact 端点用 impact_level
      const lvl = item.direction || item.impact_level || item.level || 'neutral';
      const c = lvl === 'bullish' || lvl === 'positive' ? 'var(--green)'
              : lvl === 'bearish' || lvl === 'negative' ? 'var(--red)' : '#F59E0B';
      const tag = lvl === 'bullish' || lvl === 'positive' ? '📈 利好'
               : lvl === 'bearish' || lvl === 'negative' ? '📉 利空' : '➖ 中性';
      // deep-impact 用 sectors（数组）；news/impact 用 affected_sectors
      const sectorsArr = item.sectors || item.affected_sectors || [];
      const sectors = Array.isArray(sectorsArr) ? sectorsArr.join(' · ') : String(sectorsArr);
      // deep-impact 用 magnitude 字段表示强度；news/impact 用 impact_score
      const mag = item.magnitude || '';
      const magLabel = mag === 'high' ? '高' : mag === 'medium' ? '中' : mag === 'low' ? '低' : '';
      const score = item.impact_score != null ? item.impact_score : '';
      // 影响描述：deep-impact 用 impact；news/impact 用 analysis/summary
      const desc = item.impact || item.analysis || item.summary || '';
      html += `<div class="dashboard-card" style="border-left:3px solid ${c};margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;align-items:flex-start">
          <div style="flex:1">
            <div style="font-size:14px;font-weight:700;line-height:1.5">${item.title || ''}</div>
            <div style="font-size:12px;color:var(--text2);margin-top:4px;line-height:1.6">${desc}</div>
          </div>
          <div style="text-align:right;min-width:60px;margin-left:12px">
            <div style="font-size:12px;font-weight:700;color:${c}">${tag}</div>
            ${magLabel ? `<div style="font-size:11px;color:var(--text2);margin-top:2px">强度：${magLabel}</div>` : ''}
            ${score !== '' ? `<div style="font-size:18px;font-weight:900;color:${c};margin-top:2px">${score}</div>` : ''}
          </div>
        </div>
        ${sectors ? `<div style="font-size:11px;color:var(--text2);margin-top:6px;padding-top:6px;border-top:1px solid var(--bg3)">影响板块：${sectors}</div>` : ''}
        ${item.duration ? `<div style="font-size:11px;color:var(--text2);margin-top:2px">影响周期：${item.duration}</div>` : ''}
      </div>`;
    });
    el.innerHTML = html;
  }

  // --- risk-assess 渲染 ---
  async function renderRiskAssess(el){
    el.innerHTML = _v6Skeleton('正在评估风险...');
    const [metrics, actions] = await Promise.all([
      _v6Fetch('/risk-metrics?' + getProfileParam()),
      _v6Fetch('/risk-actions?' + getProfileParam())
    ]);
    if (!metrics && !actions) {
      el.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text2)">暂无风险数据（需要先添加持仓）</div>';
      return;
    }

    let html = `<div class="section-title">🛡️ 组合风险评估 <span style="font-size:11px;color:var(--accent);font-weight:400">Phase 5 · Pro</span></div>`;

    // 风险指标
    if (metrics) {
      const riskColor = (metrics.risk_level || '') === 'high' ? 'var(--red)'
                       : (metrics.risk_level || '') === 'medium' ? '#F59E0B' : 'var(--green)';
      const riskLabel = (metrics.risk_level || '') === 'high' ? '⚠️ 高风险'
                       : (metrics.risk_level || '') === 'medium' ? '🟡 中等' : '🟢 低风险';

      html += `<div class="dashboard-card" style="border-left:3px solid ${riskColor}">
        <div class="dashboard-card-title">📊 风险指标</div>
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
          <div style="font-size:20px;font-weight:900;color:${riskColor}">${riskLabel}</div>
          ${metrics.risk_score != null ? `<div style="font-size:13px;color:var(--text2)">综合风险分 ${metrics.risk_score}</div>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px">`;

      const metricItems = [
        { k: 'max_drawdown', label: '最大回撤', fmt: v => (v * 100).toFixed(1) + '%', warn: v => v > 0.2 },
        { k: 'sharpe_ratio', label: '夏普比率', fmt: v => v.toFixed(2), warn: v => v < 0.5 },
        { k: 'volatility', label: '波动率', fmt: v => (v * 100).toFixed(1) + '%', warn: v => v > 0.25 },
        { k: 'concentration', label: '集中度', fmt: v => (v * 100).toFixed(0) + '%', warn: v => v > 0.4 },
        { k: 'beta', label: 'Beta', fmt: v => v.toFixed(2), warn: v => v > 1.3 },
        { k: 'var_95', label: 'VaR 95%', fmt: v => (v * 100).toFixed(1) + '%', warn: v => v > 0.03 }
      ];
      metricItems.forEach(m => {
        let val = metrics[m.k];
        if (val == null) return;
        // 处理对象格式（如 concentration 返回 {hhi, max_single, level}）
        if (typeof val === 'object') {
          val = val.max_single || val.hhi || val.current || 0;
        }
        if (typeof val !== 'number' || isNaN(val)) return;
        const isWarn = m.warn(val);
        html += `<div style="background:var(--bg3);border-radius:8px;padding:8px 10px">
          <div style="font-size:11px;color:var(--text2)">${m.label}</div>
          <div style="font-size:16px;font-weight:800;color:${isWarn ? 'var(--red)' : 'var(--green)'};margin-top:2px">${m.fmt(val)}</div>
        </div>`;
      });
      html += '</div></div>';
    }

    // 风险行动建议
    if (actions && actions.actions && actions.actions.length) {
      html += `<div class="dashboard-card" style="margin-top:8px">
        <div class="dashboard-card-title">🎯 风险调整建议</div>`;
      actions.actions.forEach(a => {
        const urgencyColor = a.urgency === 'high' ? 'var(--red)' : a.urgency === 'medium' ? '#F59E0B' : 'var(--green)';
        html += `<div style="padding:8px 0;border-bottom:1px solid var(--bg3)">
          <div style="font-size:13px;font-weight:600">${a.action || a.title || ''}</div>
          <div style="font-size:12px;color:var(--text2);margin-top:2px">${a.reason || a.detail || ''}</div>
          ${a.urgency ? `<div style="font-size:10px;color:${urgencyColor};margin-top:2px;font-weight:600">紧急度：${a.urgency}</div>` : ''}
        </div>`;
      });
      html += '</div>';
    }

    el.innerHTML = html || '<div style="text-align:center;padding:40px;color:var(--text2)">风险数据计算中...</div>';
  }

  // --- 劫持 renderInsight：拦截新 tab ---
  function _patchRender(){
    if (typeof renderInsight !== 'function') return false;
    _v6Hijack('renderInsight', async function(){
      // renderInsight 最后可能 return 了，我们在后面检查 insightTab
      await new Promise(r => setTimeout(r, 50));
      if (typeof insightTab === 'undefined') return;
      const el = document.getElementById('insightContent');
      if (!el) return;
      if (insightTab === 'deepimpact') renderDeepImpact(el);
      else if (insightTab === 'riskassess') renderRiskAssess(el);
    });
    return true;
  }

  function _install(){
    const a = _patchTabs();
    const b = _patchRender();
    return a && b;
  }
  if (!_install()) {
    const t = setInterval(() => { if (_install()) clearInterval(t); }, 200);
    setTimeout(() => clearInterval(t), 5000);
  }

  console.log('[V6-2] insight pro-tabs patch installed');
})();


// --- 04-signal-interpret.js ---
/* =========================================================================
 * V6 欠账 4/6：信号页 AI 12 维解读卡片
 * 方式：劫持 loadSignals()，在原始信号卡渲染完后追加 AI 解读区
 * 依赖 API：/api/daily-signal/interpret
 * ========================================================================= */
;(function(){
  'use strict';

  async function _v6InjectSignalInterpret(){
    // 仅 Pro 模式展示完整解读，Simple 模式展示精简版
    const section = document.getElementById('signalsSection');
    if (!section) return;
    if (section.querySelector('#v6SignalInterpret')) return;

    const host = document.createElement('div');
    host.id = 'v6SignalInterpret';
    host.style.cssText = 'margin-top:12px';
    host.innerHTML = isProMode()
      ? _v6Skeleton('🤖 AI 正在解读信号（约10s）...')
      : _v6Skeleton('正在生成信号摘要...');
    section.appendChild(host);

    const d = await _v6Fetch('/daily-signal/interpret', { timeout: 45000 });
    if (!d) {
      host.innerHTML = `<div class="dashboard-card" style="border-left:3px solid var(--bg3)">
        <div class="dashboard-card-title">🤖 AI 信号解读</div>
        <div style="font-size:12px;color:var(--text2)">解读数据暂不可用</div>
      </div>`;
      return;
    }

    let html = '';

    // === Simple 模式：只显示一句话结论 ===
    if (!isProMode()) {
      const conclusion = d.conclusion || d.summary || d.tldr || '';
      if (conclusion) {
        html = _v6Card('🤖 AI 一句话解读', `
          <div style="font-size:14px;line-height:1.8;color:var(--text1)">${conclusion}</div>
          <div style="font-size:11px;color:var(--accent);margin-top:6px">切换到 Pro 模式查看 12 维完整解读 ›</div>
        `);
      }
      host.innerHTML = html;
      return;
    }

    // === Pro 模式：12 维完整解读 ===
    html += `<div class="section-title">🤖 AI 13 维信号深度解读 <span style="font-size:11px;color:var(--accent);font-weight:400">Phase 5 · DeepSeek</span></div>`;

    // 总结论
    if (d.conclusion || d.summary) {
      html += `<div class="dashboard-card" style="background:linear-gradient(135deg,rgba(59,130,246,.06),rgba(16,185,129,.06));border:1px solid rgba(59,130,246,.12)">
        <div style="font-size:14px;font-weight:700;margin-bottom:6px">📋 综合结论</div>
        <div style="font-size:14px;line-height:1.8;color:var(--text1)">${d.conclusion || d.summary}</div>
      </div>`;
    }

    // 12 维度逐条解读
    const dims = d.dimensions || d.factors || d.details || [];
    if (dims.length) {
      const catIcons = {
        '技术面':'📊','基本面':'📈','资金面':'💰','情绪面':'😊','宏观面':'🏛️',
        'technical':'📊','fundamental':'📈','flow':'💰','sentiment':'😊','macro':'🏛️'
      };
      html += `<div style="display:grid;grid-template-columns:1fr;gap:6px;margin-top:8px">`;
      dims.forEach(dim => {
        const score = dim.score != null ? dim.score : '';
        const scoreColor = score > 10 ? 'var(--green)' : score < -10 ? 'var(--red)' : '#F59E0B';
        const icon = catIcons[dim.category || dim.cat || ''] || '📌';
        html += `<div class="dashboard-card" style="margin:0;padding:10px 12px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div style="font-size:13px;font-weight:700">${icon} ${dim.name || dim.title || ''}</div>
            ${score !== '' ? `<div style="font-size:16px;font-weight:900;color:${scoreColor}">${score > 0 ? '+' : ''}${score}</div>` : ''}
          </div>
          <div style="font-size:12px;color:var(--text2);margin-top:4px;line-height:1.6">${dim.interpretation || dim.detail || dim.analysis || ''}</div>
        </div>`;
      });
      html += '</div>';
    }

    // 操作建议
    if (d.action_plan || d.suggestions) {
      const items = d.action_plan || d.suggestions || [];
      if (Array.isArray(items) && items.length) {
        html += `<div class="dashboard-card" style="margin-top:8px;border-left:3px solid var(--accent)">
          <div class="dashboard-card-title">🎯 操作建议</div>
          ${items.map(a => `<div style="padding:6px 0;font-size:13px;border-bottom:1px solid var(--bg3)">${typeof a === 'string' ? a : (a.text || a.action || '')}</div>`).join('')}
        </div>`;
      }
    }

    host.innerHTML = html;
  }

  function _install(){
    if (typeof loadSignals !== 'function') return false;
    _v6Hijack('loadSignals', async function(){
      // 等原函数的 DOM 渲染完
      setTimeout(_v6InjectSignalInterpret, 300);
    });
    return true;
  }

  if (!_install()) {
    const t = setInterval(() => { if (_install()) clearInterval(t); }, 200);
    setTimeout(() => clearInterval(t), 5000);
  }

  console.log('[V6-4] signal-interpret patch installed');
})();


// --- 05-timing-dca.js ---
/* =========================================================================
 * V6 欠账 5/6：首页入场时机卡片 + Pro 智能定投
 * 方式：劫持 renderLanding()，在已有用户（有持仓）首页追加时机卡 + 定投建议
 * 依赖 API：/api/timing, /api/smart-dca
 * ========================================================================= */
;(function(){
  'use strict';

  async function _v6InjectTimingDCA(){
    if (typeof currentPage !== 'undefined' && currentPage !== 'landing') return;

    // 空仓首页已由 patch-01 处理时机卡，这里处理"有持仓"的首页
    if (window._v6IsEmptyHoldings && _v6IsEmptyHoldings()) return;

    // 找锚点：#signalsSection 后面，或 #dailyFocusSection 后面
    const anchor = document.getElementById('signalsSection')
                || document.getElementById('dailyFocusSection');
    if (!anchor) return;
    if (document.getElementById('v6TimingDCA')) return;

    const host = document.createElement('div');
    host.id = 'v6TimingDCA';
    host.style.cssText = 'margin-top:12px';
    host.innerHTML = _v6Skeleton('加载入场时机...');
    // 插到 anchor 后面
    anchor.parentNode.insertBefore(host, anchor.nextSibling);

    const [timing, dca] = await Promise.all([
      _v6Fetch('/timing'),
      isProMode() ? _v6Fetch('/smart-dca?' + getProfileParam()) : null
    ]);

    let html = '';

    // === 入场时机卡 ===
    if (timing && timing.signal) {
      const colorMap = {
        'STRONG_BUY':'var(--green)', 'BUY':'var(--green)',
        'HOLD':'#F59E0B', 'WAIT':'#F59E0B',
        'SELL':'var(--red)', 'STRONG_SELL':'var(--red)'
      };
      const labelMap = {
        'STRONG_BUY':'🔥 绝佳时机', 'BUY':'🟢 适合加仓',
        'HOLD':'🟡 暂观望', 'WAIT':'🟡 不急',
        'SELL':'🟠 谨慎', 'STRONG_SELL':'🔴 建议等待'
      };
      const c = colorMap[timing.signal] || '#F59E0B';
      const label = labelMap[timing.signal] || timing.signal;
      html += _v6Card('⏰ 入场时机判断', `
        <div style="display:flex;align-items:center;gap:12px">
          <div style="font-size:18px;font-weight:900;color:${c}">${label}</div>
          <div style="font-size:11px;color:var(--text2)">置信度 ${Math.round((timing.confidence || 0) * 100)}%</div>
        </div>
        <div style="font-size:13px;color:var(--text2);margin-top:6px;line-height:1.6">${timing.reason || timing.summary || ''}</div>
        ${timing.suggestion ? `<div style="font-size:12px;margin-top:8px;padding:8px;background:var(--bg3);border-radius:8px">💡 ${timing.suggestion}</div>` : ''}
      `, { border: c });
    }

    // === Pro 智能定投建议 ===
    if (dca && isProMode()) {
      let dcaBody = '';

      if (dca.recommendation || dca.plan) {
        const rec = dca.recommendation || dca.plan || '';
        dcaBody += `<div style="font-size:14px;line-height:1.8;margin-bottom:8px">${typeof rec === 'string' ? rec : (rec.summary || JSON.stringify(rec))}</div>`;
      }

      // 定投建议明细
      if (dca.allocations && Array.isArray(dca.allocations)) {
        dcaBody += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:6px">`;
        dca.allocations.forEach(a => {
          dcaBody += `<div style="background:var(--bg3);border-radius:8px;padding:8px 10px">
            <div style="font-size:12px;font-weight:700">${a.name || a.code || ''}</div>
            <div style="font-size:16px;font-weight:900;color:var(--accent);margin-top:2px">¥${a.amount || 0}</div>
            <div style="font-size:11px;color:var(--text2)">${a.reason || a.frequency || ''}</div>
          </div>`;
        });
        dcaBody += '</div>';
      }

      if (dca.total_monthly) {
        dcaBody += `<div style="font-size:12px;color:var(--text2);margin-top:8px">📅 建议月定投总额：¥${dca.total_monthly}</div>`;
      }

      if (dca.risk_note) {
        dcaBody += `<div style="font-size:11px;color:#F59E0B;margin-top:6px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">⚠️ ${dca.risk_note}</div>`;
      }

      if (dcaBody) {
        html += _v6Card('🤖 智能定投建议', dcaBody, { badge: 'Pro', border: 'var(--accent)' });
      }
    }

    host.innerHTML = html || '';
    // 如果完全没内容就移除容器
    if (!html) host.remove();
  }

  function _install(){
    if (typeof renderLanding !== 'function') return false;
    // renderLanding 可能已被 patch-01 劫持，v6Hijack 已处理防重复
    _v6Hijack('renderLanding', async function(){
      setTimeout(_v6InjectTimingDCA, 250);
    });
    return true;
  }

  if (!_install()) {
    const t = setInterval(() => { if (_install()) clearInterval(t); }, 200);
    setTimeout(() => clearInterval(t), 5000);
  }

  console.log('[V6-5] timing-dca patch installed');
})();

