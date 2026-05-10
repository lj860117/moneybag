// ---- 问卷 ----
function renderQuiz(){const q=QUESTIONS[currentQuestion];$('#app').innerHTML=`<div class="quiz-header fade-up"><div class="progress-bar-bg"><div class="progress-bar-fill" style="width:${currentQuestion/QUESTIONS.length*100}%"></div></div><div class="quiz-step">第${currentQuestion+1}/${QUESTIONS.length}题</div></div><div class="question-card"><div class="question-emoji">${q.emoji}</div><div class="question-text">${q.question}</div><div class="options stagger">${q.options.map((o,i)=>`<button class="option-btn" onclick="selectAnswer(${i},${o.score})">${o.text}</button>`).join('')}</div></div>`}
let _selectedPreference='fund';
function renderAmountInput(){const presets=[100000,200000,300000,500000,1000000];$('#app').innerHTML=`<div class="quiz-header"><div class="progress-bar-bg"><div class="progress-bar-fill" style="width:100%"></div></div><div class="quiz-step">最后一步 ✨</div></div><div class="amount-section"><div class="question-emoji">🎯</div><div class="question-text">你具体想投多少钱？</div><div style="font-size:14px;color:var(--text2);margin-bottom:12px">输入金额，算出每个篮子该放多少</div><div class="amount-input-wrap"><span class="amount-prefix">¥</span><input class="amount-input" type="number" id="amtIn" placeholder="500000" value="${AMOUNT_MAP[answers[0]]||''}" oninput="onAmtChange()" inputmode="numeric"><span class="amount-suffix">元</span></div><div class="amount-quick">${presets.map(a=>`<button class="quick-btn" onclick="setAmt(${a})">${fmtMoney(a)}</button>`).join('')}</div>
<div style="margin:20px 0 16px"><div style="font-size:15px;font-weight:700;margin-bottom:10px">🎨 投资偏好</div>
<div style="display:flex;gap:8px" id="prefBtns">
<button class="pref-btn active" data-pref="fund" onclick="selectPreference('fund')" style="flex:1;padding:14px 8px;border-radius:12px;border:2px solid var(--accent);background:rgba(245,158,11,.1);color:var(--text);font-size:13px;cursor:pointer;text-align:center"><div style="font-size:24px;margin-bottom:4px">🏦</div><div style="font-weight:700">纯基金</div><div style="font-size:10px;color:var(--text2);margin-top:2px">6只经典基金<br>稳健长持</div></button>
<button class="pref-btn" data-pref="stock" onclick="selectPreference('stock')" style="flex:1;padding:14px 8px;border-radius:12px;border:2px solid transparent;background:var(--card);color:var(--text);font-size:13px;cursor:pointer;text-align:center"><div style="font-size:24px;margin-bottom:4px">📊</div><div style="font-weight:700">纯股票</div><div style="font-size:10px;color:var(--text2);margin-top:2px">AI选股TOP6<br>高收益高波动</div></button>
<button class="pref-btn" data-pref="mixed" onclick="selectPreference('mixed')" style="flex:1;padding:14px 8px;border-radius:12px;border:2px solid transparent;background:var(--card);color:var(--text);font-size:13px;cursor:pointer;text-align:center"><div style="font-size:24px;margin-bottom:4px">🔄</div><div style="font-weight:700">混合</div><div style="font-size:10px;color:var(--text2);margin-top:2px">基金+股票<br>攻守兼备</div></button>
</div></div>
<button class="generate-btn" id="genBtn" onclick="genResult()" ${AMOUNT_MAP[answers[0]]?'':'disabled'}>生成我的配置方案 →</button></div>`;onAmtChange()}

function selectPreference(pref){
_selectedPreference=pref;
document.querySelectorAll('#prefBtns button').forEach(b=>{
  const isPref=b.dataset.pref===pref;
  b.style.borderColor=isPref?'var(--accent)':'transparent';
  b.style.background=isPref?'rgba(245,158,11,.1)':'var(--card)';
})}

// ---- 结果页 ----
let _recAdjustments=[];let _recAiComments={};let _recMarketData={};
async function renderResult(){const ts=answers.reduce((s,a)=>s+a,0);const pf=getProfile(ts);let al=ALLOCATIONS[pf.name];const amt=selectedAmount;currentProfile=pf;
const pref=_selectedPreference||'fund';
// 尝试从后端获取动态配置 + 配置理由（替代硬编码）
_recAdjustments=[];_recAiComments={};_recMarketData={};
if(API_AVAILABLE){try{const r=await fetch(API_BASE+'/recommend-alloc?profile='+encodeURIComponent(pf.name)+'&with_ai=true&preference='+pref,{signal:AbortSignal.timeout(30000)});if(r.ok){const d=await r.json();if(d.allocations&&d.allocations.length)al=d.allocations;_recAdjustments=d.adjustments||[];_recAiComments=d.aiComments||{};_recMarketData=d.marketData||{}}}catch(e){console.warn('recommend-alloc fallback:',e)}}
currentAllocs=al;
// 保存偏好到 portfolio
const pp=loadPortfolio();pp.preference=pref;savePortfolio(pp);
const prefLabels={'fund':'🏦 纯基金','stock':'📊 纯股票','mixed':'🔄 混合'};
const gR=calcReturns(al,amt,'good'),mR=calcReturns(al,amt,'mid'),bR=calcReturns(al,amt,'bad');
const adjHtml=_recAdjustments.length?`<div style="background:rgba(139,92,246,.08);border:1px solid rgba(139,92,246,.2);border-radius:12px;padding:12px;margin:8px 0"><div style="font-size:13px;font-weight:700;color:#8B5CF6;margin-bottom:8px">🤖 AI 配置逻辑</div><div style="font-size:12px;line-height:1.8;color:var(--text1)">${_recAdjustments.map(a=>`<div>${a}</div>`).join('')}</div>${_recMarketData.valuationPct?`<div style="font-size:11px;color:var(--text2);margin-top:6px">数据来源：沪深300估值 ${_recMarketData.valuationPct}% · 恐贪指数 ${_recMarketData.fearGreed}</div>`:''}</div>`:'';
$('#app').innerHTML=`<div class="result-page">
<div class="profile-card"><div class="profile-emoji">${pf.emoji}</div><div class="profile-name" style="color:${pf.color}">你是「${pf.name}」投资者</div><div class="profile-desc">${pf.desc}</div><div class="profile-period">建议投资周期：${pf.period} · 偏好：${prefLabels[pref]||pref}</div></div>
${adjHtml}
<div class="section-title">📊 你的${fmtMoney(amt)}这样分</div>
<div class="chart-card"><div class="chart-wrap"><canvas id="allocChart"></canvas></div><div class="alloc-list">${al.map(a=>`<div class="alloc-item" onclick="showFundDetail('${a.code}')"><div class="alloc-dot" style="background:${a.color}"></div><div class="alloc-name">${a.name}</div><div class="alloc-pct">${a.pct}%</div><div class="alloc-money">${fmtFull(Math.round(amt*a.pct/100))}</div></div>`).join('')}</div></div>
<div class="section-title">📋 照着买</div>
<div class="shopping-list">${al.map((a,i)=>{const fd=FUND_DETAILS[a.code];const etfHint=fd&&fd.etfCode?` · 场内ETF:${fd.etfCode}`:'';const aiC=_recAiComments[a.code];const aiR=a.aiReason;const isStock=a.assetType==='stock'||(a.code&&/^(sh|sz)\d{6}$/.test(a.code));const typeTag=isStock?'<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;background:rgba(239,68,68,.15);color:#EF4444;margin-left:6px">📊 个股</span>':a.code==='余额宝'?'<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;background:rgba(16,185,129,.15);color:#10B981;margin-left:6px">💵 货基</span>':'<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;background:rgba(59,130,246,.15);color:#3B82F6;margin-left:6px">🏦 基金</span>';const platformText=a.code==='余额宝'?'留在余额宝/零钱通':isStock?'⚠️ 仅券商APP可买（如东方财富/同花顺/雪球）':'支付宝·天天基金·券商均可买';return`<div class="shop-item" onclick="showFundDetail('${a.code}')"><div class="shop-num">${i+1}</div><div class="shop-detail"><div class="shop-fund-name">${a.fullName||a.name}${typeTag}</div><span class="shop-code">${a.code}${etfHint}</span><span class="shop-platform"${isStock?' style="color:#EF4444"':''}>${platformText}</span>${aiR&&!aiC?`<div style="margin-top:6px;padding:6px 10px;background:rgba(59,130,246,.08);border-radius:8px;font-size:12px;color:#60A5FA;line-height:1.5">🤖 ${aiR}</div>`:''}${aiC?`<div style="margin-top:6px;padding:6px 10px;background:rgba(139,92,246,.08);border-radius:8px;font-size:12px;color:#A78BFA;line-height:1.5">🤖 ${aiC}</div>`:''}${liveNavData[a.code]?`<div class="shop-live-nav">📡 净值:${liveNavData[a.code].nav}(${liveNavData[a.code].date})</div>`:''}</div><div class="shop-amount">${fmtFull(Math.round(amt*a.pct/100))}</div></div>`}).join('')}</div>
<div style="background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);border-radius:12px;padding:12px;margin:8px 0;font-size:12px;color:var(--text2)"><div style="font-weight:600;color:#F59E0B;margin-bottom:6px">💰 各渠道费率对比</div><div style="line-height:1.8">🏆 <b>券商场内买ETF</b>：最便宜，仅收佣金（万2左右），适合大额<br>🥈 <b>支付宝/天天基金/理财通</b>：申购费1折（约0.1-0.15%），适合定投<br>🥉 <b>基金公司官网直销</b>：4-6折，仅限自家产品<br>❌ <b>银行柜台</b>：原价~8折，不推荐<br><span style="color:#F59E0B">👉 点击基金名查看各基金的详细购买渠道建议</span></div></div>
<div class="section-title">📊 数据来源</div>
<div class="data-source-card"><div class="data-source-title">配置依据</div><div class="data-source-item">Markowitz均值-方差模型(1952诺贝尔经济学奖)</div><div class="data-source-item">参考Vanguard Target-Date基金系列配比</div><div class="data-source-item">各类资产过去10-20年历史年化回报</div><div style="margin-top:12px"><div class="data-source-title">实时数据 <span class="api-status ${API_AVAILABLE?'on':'off'}">${API_AVAILABLE?'🟢已连接':'🔴离线'}</span></div></div></div>
<div class="section-title">💰 一年后可能会怎样</div>
<div class="projection-card"><div class="scenario-grid">
<div class="scenario-item"><div>📈</div><div class="scenario-label">乐观</div><div class="scenario-return pos">+${(gR/amt*100).toFixed(1)}%</div><div class="scenario-money">赚${fmtMoney(Math.round(gR))}</div></div>
<div class="scenario-item"><div>📊</div><div class="scenario-label">中性</div><div class="scenario-return pos">+${(mR/amt*100).toFixed(1)}%</div><div class="scenario-money">赚${fmtMoney(Math.round(mR))}</div></div>
<div class="scenario-item"><div>📉</div><div class="scenario-label">悲观</div><div class="scenario-return ${bR>=0?'pos':'neg'}">${bR>=0?'+':''}${(bR/amt*100).toFixed(1)}%</div><div class="scenario-money">${bR>=0?'赚':'亏'}${fmtMoney(Math.abs(Math.round(bR)))}</div></div>
</div><div style="font-size:13px;color:var(--text2);margin-bottom:8px">3年累计预测(中性场景)</div><div class="projection-chart-wrap"><canvas id="projChart"></canvas></div></div>
<div id="signalsSection"></div>
<div class="section-title">⚠️ 三条铁律</div>
<div class="rules-card"><div class="rule-item"><div class="rule-num">1</div><div class="rule-text"><strong>跌了别卖</strong>—越跌越该买</div></div><div class="rule-item"><div class="rule-num">2</div><div class="rule-text"><strong>别看新闻瞎操作</strong>—关掉手机</div></div><div class="rule-item"><div class="rule-num">3</div><div class="rule-text"><strong>至少拿3年</strong>—3年赚钱概率>85%</div></div></div>
<div class="bottom-actions"><button class="action-btn green" onclick="confirmPurchase()">✅ 我知道了，去录入持仓</button><button class="action-btn secondary" onclick="restart()">🔄 重新测评</button></div>
<div class="footer-disclaimer">⚠️ 本工具仅供参考学习，不构成投资建议。投资有风险，入市需谨慎。</div></div>`;
renderNav();setTimeout(()=>drawAllocChart(al),100);setTimeout(()=>drawProjChart(amt,mR/amt),200);loadSignals()}

async function loadSignals(){
const el=document.getElementById('signalsSection');if(!el)return;
if(!API_AVAILABLE){return}
el.innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在分析市场信号...</div>`;
try{
const r=await fetch(API_BASE+'/daily-signal',{signal:AbortSignal.timeout(30000)});
if(!r.ok)throw new Error('signal fetch failed');
const d=await r.json();

// 综合信号大卡片
const bgMap={STRONG_BUY:'rgba(16,185,129,.12)',BUY:'rgba(16,185,129,.08)',HOLD:'rgba(245,158,11,.08)',SELL:'rgba(239,68,68,.08)',STRONG_SELL:'rgba(239,68,68,.12)'};
const borderMap={STRONG_BUY:'rgba(16,185,129,.3)',BUY:'rgba(16,185,129,.2)',HOLD:'rgba(245,158,11,.2)',SELL:'rgba(239,68,68,.2)',STRONG_SELL:'rgba(239,68,68,.3)'};
const labelMap={STRONG_BUY:'强烈买入 🟢',BUY:'建议买入 🟢',HOLD:'持有观望 🟡',SELL:'建议减仓 🟠',STRONG_SELL:'强烈减仓 🔴'};

let html=`<div class="section-title">🤖 今日量化信号 <span style="font-size:11px;color:var(--accent);font-weight:400">V${d.version||'5.0'} · ${(d.details||[]).length}维多因子</span></div>`;
setExplain('signal','量化信号解读','钱袋子 V5.0 多因子信号系统融合了13个维度的数据（借鉴幻方量化）：\n\n📊 技术面(25%)：RSI(8%) + MACD(10%) + 布林带(7%)\n📈 基本面(30%)：估值(18%) + 股息率(5%) + 股债性价比(7%)\n💰 资金面(20%)：北向资金(10%) + 融资融券(5%) + SHIBOR(5%)\n😊 情绪面(15%)：恐惧贪婪(8%) + LLM新闻情绪(7%)\n🏛️ 宏观面(5%)：PMI+M2\n🌍 地缘面(5%)：地缘风险评估\n\n每个维度打分(-100~+100)，加权平均后得出综合信号。\n\n当前综合得分：'+(d.score||0)+'\n置信度：'+Math.round(d.confidence||0)+'%\n\n'+((d.details||[]).map(x=>(x.category||'')+' | '+x.name+'('+x.weight+')：'+x.detail).join('\n'))+'\n\n⚠️ 量化信号仅供参考，不构成投资建议。');
html+=`<div style="background:${bgMap[d.overall]||bgMap.HOLD};border:1px solid ${borderMap[d.overall]||borderMap.HOLD};border-radius:16px;padding:16px;margin-bottom:12px;cursor:pointer" onclick="showExplain('signal')">
<div style="display:flex;justify-content:space-between;align-items:center">
<div><div style="font-size:20px;font-weight:900">${labelMap[d.overall]||'持有观望'}</div><div style="font-size:12px;color:var(--text2);margin-top:4px">${d.date||''} · 综合得分 ${d.score||0} · 置信度 ${Math.round(d.confidence||0)}%</div></div>
<div style="font-size:11px;color:var(--accent)">点击看详情 ›</div></div>
<div style="font-size:13px;margin-top:8px;line-height:1.6">${d.summary||''}</div></div>`;

// V5.0 因子分组展示
const catIcons={'技术面':'📊','基本面':'📈','资金面':'💰','情绪面':'😊','宏观面':'🏛️'};
const catColors={'技术面':'#3B82F6','基本面':'#10B981','资金面':'#F59E0B','情绪面':'#EC4899','宏观面':'#8B5CF6'};
if(d.factorGroups){
html+=`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:6px;margin-bottom:12px">`;
Object.entries(d.factorGroups).forEach(([cat,grp])=>{
const catScore=grp.totalWeight>0?Math.round(grp.weightedScore/grp.totalWeight):0;
const c=catColors[cat]||'var(--text2)';
const barW=Math.min(Math.abs(catScore),100);
const barDir=catScore>=0?'right':'left';
html+=`<div style="background:var(--card);border-radius:10px;padding:8px 10px">
<div style="font-size:11px;font-weight:700;color:${c}">${catIcons[cat]||''} ${cat} <span style="font-weight:400;opacity:.7">${(grp.totalWeight*100).toFixed(0)}%</span></div>
<div style="font-size:16px;font-weight:900;color:${catScore>10?'var(--green)':catScore<-10?'var(--red)':'var(--text1)'};margin-top:2px">${catScore>0?'+':''}${catScore}</div>
<div style="height:3px;background:var(--bg);border-radius:2px;margin-top:4px;overflow:hidden"><div style="height:100%;width:${barW}%;background:${catScore>=0?'var(--green)':'var(--red)'};border-radius:2px;float:${barDir}"></div></div>
<div style="font-size:10px;color:var(--text2);margin-top:3px">${grp.factors.map(f=>'<span style="color:'+(f.score>10?'var(--green)':f.score<-10?'var(--red)':'var(--text2)')+'">'+f.name+(f.score>0?'+':'')+f.score+'</span>').join(' ')}</div></div>`;
});
html+=`</div>`}

// V5.0 新闻情绪卡片
if(d.sentiment&&d.sentiment.available){
const s=d.sentiment;
const sentColor=s.score>20?'var(--green)':s.score<-20?'var(--red)':'var(--accent)';
setExplain('sentiment','LLM新闻情绪分析','🤖 用AI（DeepSeek/GPT）对最新新闻进行情绪打分：\n\n情绪得分：'+s.score+' ('+(s.level||'中性')+')\n分析来源：'+(s.source==='llm'?'AI模型':'关键词匹配')+'\n'+(s.reason||'')+'\n\n📰 分析的新闻标题：\n'+(s.headlines||[]).map((h,i)=>(i+1)+'. '+h).join('\n')+'\n\n💡 新闻情绪是短期市场方向的参考，不要因为单日情绪做决策。');
html+=`<div style="background:var(--card);border-radius:12px;padding:12px;margin-bottom:12px;cursor:pointer" onclick="showExplain('sentiment')">
<div style="display:flex;justify-content:space-between;align-items:center">
<div><div style="font-size:13px;font-weight:700">🤖 新闻情绪 <span style="font-size:11px;font-weight:400;color:var(--text2)">${s.source==='llm'?'AI分析':'关键词'}</span></div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">${s.reason||s.level||'中性'}</div></div>
<div style="text-align:right"><div style="font-size:18px;font-weight:900;color:${sentColor}">${s.score>0?'+':''}${s.score}</div>
<div style="font-size:10px;color:var(--text2)">${s.level||'中性'}</div></div></div></div>`}

// 大师策略
if(d.masterStrategies&&d.masterStrategies.length){
html+=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">`;
d.masterStrategies.forEach((ms,i)=>{
const msKey='master_'+i;
setExplain(msKey,ms.icon+' '+ms.master+'的投资策略','💡 核心理念：'+ms.philosophy+'\n\n'+ms.message+'\n\n⚠️ 大师策略基于当前市场数据自动生成分析，仅供参考。');
const msColor=ms.signal==='STRONG_BUY'||ms.signal==='BUY'||ms.signal==='HOLD_BUY'?'var(--green)':ms.signal==='SELL'||ms.signal==='STRONG_SELL'?'var(--red)':'var(--accent)';
const msLabel=ms.signal==='STRONG_BUY'?'强烈买入':ms.signal==='BUY'?'建议买入':ms.signal==='HOLD_BUY'?'可以建仓':ms.signal==='SELL'?'建议减仓':ms.signal==='STRONG_SELL'?'强烈减仓':'持有观望';
html+=`<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="showExplain('${msKey}')">
<div style="font-size:14px;font-weight:700">${ms.icon} ${ms.master}</div>
<div style="font-size:12px;color:${msColor};margin-top:4px;font-weight:600">${msLabel}</div>
<div style="font-size:11px;color:var(--text2);margin-top:4px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${ms.message||ms.philosophy}</div></div>`;
});
html+=`</div>`}

// 智能定投建议
if(d.smartDca){
const sc=d.smartDca;
setExplain('smartdca','智能定投策略','🧠 智能定投 vs 固定定投：\n\n固定定投：每月投相同金额。\n智能定投：根据估值动态调整——低估多买、高估少买。\n\n当前估值百分位：'+sc.valuationPct+'%\n本月倍率：'+sc.multiplier+'x\n'+sc.advice+'\n\n📊 倍率对照表：\n• 估值 <20% → 1.5x（极度低估，多买）\n• 20-30% → 1.3x（低估，适当多买）\n• 30-50% → 1.1x（偏低，略多）\n• 50-70% → 1.0x（正常）\n• 70-85% → 0.7x（偏高，少买）\n• >85% → 0.3x（高估，大幅减少）\n\n历史回测：智能定投比固定定投长期多赚约15-20%。\n\n⚠️ 仅供参考，不构成投资建议。');
html+=`<div style="background:var(--card);border-radius:12px;padding:12px;margin-bottom:12px;cursor:pointer" onclick="showExplain('smartdca')">
<div style="display:flex;justify-content:space-between;align-items:center">
<div><div style="font-size:13px;font-weight:700">🧠 智能定投建议</div><div style="font-size:11px;color:var(--text2);margin-top:2px">${sc.advice}</div></div>
<div style="text-align:right"><div style="font-size:16px;font-weight:900;color:var(--accent)">${sc.multiplier}x</div><div style="font-size:11px;color:var(--text2)">本月倍率</div></div></div></div>`}

// 回测按钮
html+=`<div style="text-align:center"><button onclick="showBacktest()" style="background:transparent;border:1px solid var(--accent);color:var(--accent);padding:8px 20px;border-radius:20px;font-size:12px;cursor:pointer">📊 查看历史回测数据</button></div>`;

el.innerHTML=html;
}catch(e){console.warn('Signal load failed:',e);el.innerHTML=''}
}

