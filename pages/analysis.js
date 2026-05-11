// ---- Phase 5: 因子 IC 检验 ----
async function renderFactorIC(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🔬 因子 IC 检验</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">验证30因子中哪些真正具有收益预测能力（Spearman IC）</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">📊 |IC| > 0.05 = 优秀因子 · |IC| > 0.03 = 有效因子 · 参考 Barra 多因子模型标准</div>
<div id="factorICContent"><div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在计算因子IC，需获取200只股票数据...<br><span style="font-size:11px;opacity:0.6">首次约30-60秒</span></div></div></div>`;
try{
const r=await fetch(API_BASE+'/factor-ic?forward_days=20&pool_size=200',{signal:AbortSignal.timeout(120000)});
if(!r.ok)throw new Error('fetch failed');
const d=await r.json();
if(d.error){document.getElementById('factorICContent').innerHTML=`<div style="text-align:center;padding:20px;color:var(--red)">${d.error}</div>`;return}
const ranking=d.ranking||[];
const summary=d.summary||{};
const recs=d.recommendations||[];
const ineffective=d.ineffective_factors||[];
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:rgba(16,185,129,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:24px;font-weight:900;color:var(--green)">${summary.effective_factors||0}</div>
<div style="font-size:11px;color:var(--text2)">有效因子</div></div>
<div style="background:rgba(239,68,68,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:24px;font-weight:900;color:var(--red)">${(summary.total_factors||0)-(summary.effective_factors||0)}</div>
<div style="font-size:11px;color:var(--text2)">无效因子</div></div>
<div style="background:rgba(99,102,241,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:24px;font-weight:900;color:#818CF8">${summary.effectiveness_rate||0}%</div>
<div style="font-size:11px;color:var(--text2)">有效率</div></div></div>`;
// 建议
if(recs.length){html+=`<div style="background:rgba(59,130,246,.06);border-radius:10px;padding:10px 12px;margin-bottom:16px;font-size:12px;line-height:1.8;color:var(--text)">
<div style="font-weight:700;margin-bottom:4px">💡 分析建议</div>
${recs.map(r=>'• '+r).join('<br>')}</div>`}
// 因子排名表
html+=`<div style="font-size:13px;font-weight:700;margin-bottom:8px">📊 因子排名（按 |IC| 降序）</div>
<div style="display:grid;grid-template-columns:30px 1fr 60px 60px 60px;gap:4px;font-size:11px;color:var(--text2);font-weight:600;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.1)">
<div>#</div><div>因子</div><div style="text-align:right">IC</div><div style="text-align:right">样本</div><div style="text-align:right">评级</div></div>`;
ranking.forEach((f,i)=>{
const levelColor=f.level==='优秀'?'var(--green)':f.level==='有效'?'#3B82F6':f.level==='微弱'?'var(--accent)':'var(--red)';
const icColor=f.ic>0?'var(--green)':'var(--red)';
html+=`<div style="display:grid;grid-template-columns:30px 1fr 60px 60px 60px;gap:4px;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.04);align-items:center">
<div style="font-size:11px;color:var(--text2);font-weight:700">${i+1}</div>
<div><div style="font-size:12px;font-weight:600">${f.name_cn||f.factor}</div>
<div style="font-size:10px;color:var(--text2)">${f.direction||''} · ${f.factor}</div></div>
<div style="text-align:right;font-size:13px;font-weight:700;color:${icColor}">${f.ic>0?'+':''}${f.ic}</div>
<div style="text-align:right;font-size:11px;color:var(--text2)">${f.samples}</div>
<div style="text-align:right"><span style="font-size:10px;padding:2px 6px;border-radius:4px;background:${levelColor}20;color:${levelColor}">${f.level}</span></div></div>`});
html+=`<div style="text-align:center;margin-top:16px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="insightTab='factorictest';renderInsight()">🔄 重新检验</button></div>
<div style="font-size:11px;color:#475569;margin-top:8px;text-align:center">样本池 ${summary.pool_size||0} 只 · 预测周期 ${summary.forward_days||20} 日 · 耗时 ${summary.elapsed_seconds||0}s</div>`;
document.getElementById('factorICContent').innerHTML=html;
}catch(e){console.warn('Factor IC failed:',e);document.getElementById('factorICContent').innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)">加载失败<br><button onclick="insightTab='factorictest';renderInsight()" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>`}}


// ---- Phase 6: 蒙特卡洛模拟 ----
async function renderMonteCarlo(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🎲 蒙特卡洛模拟</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">基于历史收益分布，5000次模拟生成概率预测（替代单点预测）</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">🎯 输入股票代码 → 获取盈利概率/最差情景/收益分布 · 参考 AQR 蒙特卡洛方法论</div>
<div style="display:flex;gap:8px;margin-bottom:8px">
<input id="mcCode" placeholder="股票代码 如 600519" class="input-field" style="flex:1;min-width:0;padding:10px 12px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px">
<select id="mcHorizon" style="padding:10px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:12px;flex-shrink:0">
<option value="125">半年</option><option value="250" selected>一年</option><option value="500">两年</option></select>
</div>
<button onclick="runMonteCarlo()" style="width:100%;padding:10px 16px;border-radius:10px;border:none;background:var(--accent);color:#fff;font-weight:700;cursor:pointer;font-size:14px;margin-bottom:16px">🎲 开始模拟</button>
<div id="mcResult"></div>
<div style="margin-top:16px;padding-top:16px;border-top:1px solid rgba(148,163,184,.1)">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">📊 持仓组合模拟</div>
<div style="font-size:11px;color:var(--text2);margin-bottom:8px">自动读取你的持仓，模拟组合概率分布</div>
<button onclick="runPortfolioMC()" style="padding:10px 20px;border-radius:10px;border:1px solid var(--accent);background:transparent;color:var(--accent);font-weight:600;cursor:pointer;font-size:12px">🚀 模拟我的组合</button>
<div id="mcPortfolioResult" style="margin-top:12px"></div>
</div></div>`;
}

async function runMonteCarlo(){
const code=document.getElementById('mcCode')?.value?.trim();
if(!code){alert('请输入股票代码');return}
const horizon=parseInt(document.getElementById('mcHorizon')?.value||'250');
const el=document.getElementById('mcResult');
if(!el)return;
el.innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>5000次模拟进行中...</div>`;
try{
// 同时跑有纪律 vs 无纪律对比
const r=await fetch(API_BASE+'/monte-carlo/compare/'+code+'?simulations=5000&horizon_days='+horizon,{signal:AbortSignal.timeout(120000)});
if(!r.ok)throw new Error('API failed');
const d=await r.json();
if(d.error){el.innerHTML=`<div style="padding:16px;color:var(--red);text-align:center">${d.error}</div>`;return}
const w=d.with_discipline||{};
const wo=d.without_discipline||{};
const imp=d.improvement||{};
const wp=w.percentiles||{};const wop=wo.percentiles||{};
const wprob=w.probabilities||{};const woprob=wo.probabilities||{};
const wrisk=w.risk_metrics||{};
const wdisc=w.discipline_stats||{};
let html=`<div style="font-size:13px;font-weight:700;margin-bottom:12px">📊 ${code} · ${w.horizon_years||1}年模拟（${w.simulations||5000}次）</div>`;
// 核心概率卡片
html+=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:rgba(16,185,129,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900;color:var(--green)">${wprob.profit||0}%</div>
<div style="font-size:11px;color:var(--text2)">盈利概率</div>
<div style="font-size:10px;color:var(--text3)">无纪律 ${woprob.profit||0}%</div></div>
<div style="background:rgba(239,68,68,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900;color:var(--red)">${wprob.loss_over_10pct||0}%</div>
<div style="font-size:11px;color:var(--text2)">大亏概率(>10%)</div>
<div style="font-size:10px;color:var(--text3)">无纪律 ${woprob.loss_over_10pct||0}%</div></div>
<div style="background:rgba(99,102,241,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900;color:#818CF8">${wprob.gain_over_20pct||0}%</div>
<div style="font-size:11px;color:var(--text2)">大赚概率(>20%)</div>
<div style="font-size:10px;color:var(--text3)">无纪律 ${woprob.gain_over_20pct||0}%</div></div></div>`;
// 收益分布对比
html+=`<div style="background:var(--bg2);border-radius:12px;padding:12px;margin-bottom:12px">
<div style="font-size:12px;font-weight:700;margin-bottom:8px">收益分布（有纪律 vs 无纪律）</div>
<div style="display:grid;grid-template-columns:60px 1fr 1fr;gap:4px;font-size:11px">
<div style="color:var(--text2);font-weight:600">分位</div>
<div style="color:var(--green);font-weight:600;text-align:right">✅ 有纪律</div>
<div style="color:var(--text2);font-weight:600;text-align:right">❌ 无纪律</div>
${['P10','P25','P50','P75','P90'].map(p=>{
const wv=wp[p]||0;const wov=wop[p]||0;
const label=p==='P10'?'最差10%':p==='P25'?'较差25%':p==='P50'?'中位数':p==='P75'?'较好75%':'最好90%';
return`<div style="color:var(--text2);padding:4px 0">${label}</div>
<div style="text-align:right;padding:4px 0;font-weight:700;color:${wv>=0?'var(--green)':'var(--red)'}">${wv>=0?'+':''}${wv}%</div>
<div style="text-align:right;padding:4px 0;color:${wov>=0?'var(--green)':'var(--red)'}">${wov>=0?'+':''}${wov}%</div>`}).join('')}</div></div>`;
// 风险指标
html+=`<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px">
<div style="background:var(--bg2);border-radius:10px;padding:10px">
<div style="font-size:11px;color:var(--text2)">VaR(95%)</div>
<div style="font-size:16px;font-weight:800;color:var(--red)">${wrisk.var_95||0}%</div>
<div style="font-size:10px;color:var(--text3)">最差5%情景的收益</div></div>
<div style="background:var(--bg2);border-radius:10px;padding:10px">
<div style="font-size:11px;color:var(--text2)">CVaR(95%)</div>
<div style="font-size:16px;font-weight:800;color:var(--red)">${wrisk.cvar_95||0}%</div>
<div style="font-size:10px;color:var(--text3)">尾部风险平均损失</div></div></div>`;
// 纪律触发统计
html+=`<div style="background:rgba(245,158,11,.06);border-radius:10px;padding:10px;margin-bottom:12px;font-size:12px">
<div style="font-weight:700;margin-bottom:4px">⚡ 纪律触发率</div>
止损(-8%)触发：${wdisc.stop_loss_triggered||0}% 的路径 · 止盈(+20%)触发：${wdisc.take_profit_triggered||0}% 的路径</div>`;
// 结论
if(d.conclusion){html+=`<div style="background:rgba(59,130,246,.06);border-radius:10px;padding:10px 12px;font-size:12px;line-height:1.8">
<div style="font-weight:700;margin-bottom:4px">📝 结论</div>${d.conclusion}</div>`}
// 历史参数
const hp=w.historical_params||{};
html+=`<div style="font-size:11px;color:#475569;margin-top:12px;text-align:center">基于历史：年化 ${hp.annual_return||0}% · 波动率 ${hp.annual_volatility||0}% · 偏度 ${hp.skewness||0} · 历史最大回撤 ${hp.historical_max_dd||0}%</div>`;
el.innerHTML=html;
}catch(e){console.warn('MC failed:',e);el.innerHTML=`<div style="text-align:center;padding:16px;color:var(--text2)">模拟失败<br><span style="font-size:11px;opacity:0.6">${e.message}</span><br><button onclick="runMonteCarlo()" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>`}}

async function runPortfolioMC(){
const el=document.getElementById('mcPortfolioResult');if(!el)return;
el.innerHTML=`<div style="text-align:center;padding:16px;color:var(--text2)"><div class="loading-spinner" style="width:20px;height:20px;margin:0 auto 6px;border-width:2px"></div>读取持仓并模拟中...</div>`;
try{
// 先获取用户持仓
const sr=await fetch(API_BASE+'/stock-holdings/scan?'+getProfileParam(),{signal:AbortSignal.timeout(30000)});
if(!sr.ok)throw new Error('获取持仓失败');
const sd=await sr.json();
const holdings=(sd.holdings||[]).filter(h=>h.code&&h.currentPrice>0);
if(!holdings.length){el.innerHTML='<div style="padding:12px;color:var(--text2);text-align:center">暂无股票持仓，请先添加</div>';return}
// 构建组合
const totalValue=holdings.reduce((s,h)=>s+(h.currentPrice*(h.quantity||0)),0)||holdings.length;
const payload={
holdings:holdings.map(h=>({code:h.code.replace(/^(sh|sz)/i,''),weight:totalValue>0?(h.currentPrice*(h.quantity||0))/totalValue:1/holdings.length})),
simulations:3000,horizon_days:250,initial:100000,discipline:true};
const r=await fetch(API_BASE+'/monte-carlo/portfolio',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(120000)});
if(!r.ok)throw new Error('模拟失败');
const d=await r.json();
if(d.error){el.innerHTML=`<div style="padding:12px;color:var(--red)">${d.error}</div>`;return}
const p=d.percentiles||{};const prob=d.probabilities||{};
let html=`<div style="font-size:13px;font-weight:700;margin-bottom:8px">我的组合（${d.holdings?.length||0}只）· 1年模拟</div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px">
<div style="background:rgba(16,185,129,.08);border-radius:10px;padding:10px;text-align:center">
<div style="font-size:22px;font-weight:900;color:var(--green)">${prob.profit||0}%</div>
<div style="font-size:10px;color:var(--text2)">盈利概率</div></div>
<div style="background:rgba(239,68,68,.08);border-radius:10px;padding:10px;text-align:center">
<div style="font-size:22px;font-weight:900;color:var(--red)">${prob.loss_over_10pct||0}%</div>
<div style="font-size:10px;color:var(--text2)">大亏(>10%)</div></div>
<div style="background:rgba(99,102,241,.08);border-radius:10px;padding:10px;text-align:center">
<div style="font-size:22px;font-weight:900;color:#818CF8">${prob.gain_over_20pct||0}%</div>
<div style="font-size:10px;color:var(--text2)">大赚(>20%)</div></div></div>
<div style="font-size:12px;line-height:1.8;color:var(--text)">
收益分布：最差10%=${p.P10||0}% · 中位数=${p.P50||0}% · 最好90%=${p.P90||0}%<br>
预期收益=${d.expected_return||0}% · VaR(95%)=${d.risk_metrics?.var_95||0}%</div>`;
// 持仓明细
if(d.holdings){html+=`<div style="margin-top:8px;font-size:11px;color:var(--text2)">
${d.holdings.map(h=>`${h.code}(${h.weight}%) 年化${h.annual_return||'--'}%`).join(' · ')}</div>`}
el.innerHTML=html;
}catch(e){console.warn('Portfolio MC failed:',e);el.innerHTML=`<div style="text-align:center;padding:12px;color:var(--text2)">组合模拟失败: ${e.message}<br><button onclick="runPortfolioMC()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}


// ============================================================
// 六大量化引擎 UI（对标幻方量化）
// ============================================================

// ---- P1: AI 预测引擎（已废弃 M5 W4，功能迁至决策复盘系统）----
// 旧函数保留空壳避免 undefined 错误
async function renderAIPredict(el){
el.innerHTML='<div class="dashboard-card"><div class="dashboard-card-title">🤖 AI 预测引擎</div><div style="padding:20px;text-align:center;color:var(--text2)"><div style="font-size:48px;margin-bottom:12px">🚫</div><div style="font-size:14px;font-weight:600;margin-bottom:8px">此功能已废弃</div><div style="font-size:12px;line-height:1.6">AI 预测功能已整合到决策复盘系统。<br>请使用「决策复盘」标签页查看你的决策质量和行为模式分析。</div></div></div>';}
async function runAIPredict(){}
async function runAIPredPortfolio(){}

// ---- P2: 遗传因子挖掘 ----
async function renderGeneticFactor(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">🧬 遗传编程因子挖掘</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">用遗传算法自动发现人类想不到的 Alpha 因子（对标幻方量化因子挖掘）</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">🧬 200个体 × 30代进化 → 保留 IC 最高的因子表达式</div>
<div style="display:flex;gap:8px;margin-bottom:16px">
<input id="gfCode" placeholder="股票代码 如 000001" class="input-field" value="000001" style="flex:1;padding:10px 12px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px">
<button onclick="runGeneticFactor()" style="padding:10px 16px;border-radius:10px;border:none;background:linear-gradient(135deg,#10B981,#059669);color:#fff;font-weight:700;cursor:pointer;white-space:nowrap">🧬 开始进化</button></div>
<div id="gfResult"></div></div>`;
}

async function runGeneticFactor(){const code=document.getElementById('gfCode')?.value?.trim()||'000001';
const el=document.getElementById('gfResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>遗传进化中... 200个体 × 30代<br><span style="font-size:11px;opacity:0.6">约30-90秒</span></div>';
try{const r=await fetch(API_BASE+`/genetic-factor/${code}?generations=30&top_k=10`,{signal:AbortSignal.timeout(120000)});const d=await r.json();
if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">${d.error}</div>`;return}
const s=d.summary||{};
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">🏆 优秀因子</div><div style="font-size:20px;font-weight:800;color:#10B981">${s.excellent||0}</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">✅ 有效因子</div><div style="font-size:20px;font-weight:800;color:#3B82F6">${s.effective||0}</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">⚠️ 弱因子</div><div style="font-size:20px;font-weight:800;color:#94A3B8">${s.weak||0}</div></div></div>`;
(d.top_factors||[]).forEach(f=>{const icColor=f.ic>0.05?'#10B981':(f.ic>0.03?'#3B82F6':'#94A3B8');
html+=`<div style="padding:10px;margin-bottom:6px;background:var(--bg2);border-radius:10px;border-left:3px solid ${icColor}">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><span style="font-weight:700;font-size:12px">#${f.rank} ${f.rating}</span><span style="font-size:12px;color:${icColor};font-weight:700">IC=${f.ic}</span></div>
<div style="font-size:10px;color:var(--text2);font-family:monospace;word-break:break-all;background:var(--bg3);padding:4px 6px;border-radius:4px">${f.expression}</div></div>`});
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">进化失败: ${e.message}<br><button onclick="runGeneticFactor()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}

// ---- P3: 组合优化器 ----
async function renderOptimizer(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">⚡ 组合优化器</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">5种方法计算数学最优持仓比例（从"拍脑袋"到Markowitz/CVaR/HRP）</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">📈 最大夏普 · 🛡️ 最小方差 · ⚡ CVaR(幻方方法) · 🌳 HRP · ⚖️ 等权基准</div>
<button onclick="runOptimizer()" style="padding:10px 20px;border-radius:10px;border:none;background:linear-gradient(135deg,#F59E0B,#EF4444);color:#fff;font-weight:700;cursor:pointer;font-size:13px">⚡ 优化我的持仓</button>
<div id="optResult" style="margin-top:16px"></div></div>`;
}

async function runOptimizer(){const el=document.getElementById('optResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>获取历史数据 + 计算协方差矩阵...</div>';
try{const uid=getProfileId();const r=await fetch(API_BASE+`/portfolio-optimize/${uid}`,{signal:AbortSignal.timeout(90000)});const d=await r.json();
if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">${d.error}</div>`;return}
let html='';
if(d.recommendation)html+=`<div style="padding:10px;background:rgba(59,130,246,.1);border-radius:10px;border:1px solid rgba(59,130,246,.2);margin-bottom:16px;font-size:13px;font-weight:600">💡 ${d.recommendation}</div>`;
const methods=d.methods||{};
Object.entries(methods).forEach(([key,m])=>{const met=m.metrics||{};
html+=`<div style="padding:12px;margin-bottom:10px;background:var(--bg2);border-radius:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">${m.name}</div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;font-size:11px;margin-bottom:8px">
<div>年化收益 <b style="color:#10B981">${met.annual_return}%</b></div>
<div>夏普比率 <b>${met.sharpe_ratio}</b></div>
<div>最大回撤 <b style="color:#EF4444">${met.max_drawdown}%</b></div></div>
<div style="display:flex;flex-wrap:wrap;gap:4px">`
;(m.allocations||[]).forEach(a=>{html+=`<span style="font-size:10px;padding:2px 8px;background:var(--bg3);border-radius:4px">${a.name} ${a.weight}%</span>`});
html+=`</div></div>`});
if(d.adjustments?.length>0){html+=`<div style="padding:12px;background:rgba(245,158,11,.08);border-radius:12px;border:1px solid rgba(245,158,11,.15)"><div style="font-size:13px;font-weight:700;margin-bottom:8px">📋 建议调仓</div>`;
d.adjustments.forEach(a=>{const c=a.action.includes('加仓')?'#10B981':'#EF4444';
html+=`<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:12px;border-bottom:1px solid rgba(148,163,184,.08)"><span>${a.name}</span><span>${a.current}% → ${a.optimal}%</span><span style="color:${c};font-weight:700">${a.action}</span></div>`});
html+=`</div>`}
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">优化失败: ${e.message}<br><button onclick="runOptimizer()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}

// ---- P4: 另类数据 ----
async function renderAltData(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">📡 另类数据仪表盘</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:12px">散户版"卫星替代品" — 北向资金/融资融券/龙虎榜/大宗交易/行业资金流</div>
<div id="altDataResult"><div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载6大另类数据源...</div></div></div>`;
try{const r=await fetch(API_BASE+'/alt-data/dashboard',{signal:AbortSignal.timeout(60000)});const d=await r.json();
const el2=document.getElementById('altDataResult');if(!el2)return;
let html='';
if(d.overall_signal)html+=`<div style="padding:10px;background:rgba(59,130,246,.1);border-radius:10px;border:1px solid rgba(59,130,246,.2);margin-bottom:16px;font-size:14px;font-weight:700;text-align:center">${d.overall_signal}</div>`;
// 北向资金
const nb=d.northbound||{};
html+=`<div style="padding:10px;margin-bottom:8px;background:var(--bg2);border-radius:10px"><div style="font-size:13px;font-weight:700;margin-bottom:6px">🏦 北向资金</div>`;
if(nb.signal)html+=`<div style="font-size:12px;margin-bottom:6px">${nb.signal}</div>`;
if(nb.top_stocks?.length>0){html+=`<div style="font-size:11px;color:var(--text2)">Top 持股: `;nb.top_stocks.slice(0,5).forEach(s=>{html+=`<span style="margin-right:6px">${s.name}</span>`});html+=`</div>`}
html+=`</div>`;
// 融资融券
const mg=d.margin||{};
html+=`<div style="padding:10px;margin-bottom:8px;background:var(--bg2);border-radius:10px"><div style="font-size:13px;font-weight:700;margin-bottom:6px">💰 融资融券</div>`;
if(mg.signal)html+=`<div style="font-size:12px">${mg.signal}</div>`;
html+=`</div>`;
// 行业资金流
const sf=d.sector_flow||{};
if(sf.inflow?.length>0){html+=`<div style="padding:10px;margin-bottom:8px;background:var(--bg2);border-radius:10px"><div style="font-size:13px;font-weight:700;margin-bottom:6px">🏭 行业资金流</div>`;
html+=`<div style="font-size:11px;color:#10B981;margin-bottom:4px">流入: `;sf.inflow.slice(0,5).forEach(s=>{html+=`${s.name}(${s.net_flow.toFixed(1)}亿) `});
html+=`</div><div style="font-size:11px;color:#EF4444">流出: `;(sf.outflow||[]).slice(0,5).forEach(s=>{html+=`${s.name}(${s.net_flow.toFixed(1)}亿) `});
html+=`</div></div>`}
// 龙虎榜
const dt=d.dragon_tiger||{};
if(dt.records?.length>0){html+=`<div style="padding:10px;margin-bottom:8px;background:var(--bg2);border-radius:10px"><div style="font-size:13px;font-weight:700;margin-bottom:6px">🐲 龙虎榜</div>`;
dt.records.slice(0,5).forEach(r2=>{html+=`<div style="font-size:11px;padding:3px 0;border-bottom:1px solid rgba(148,163,184,.06)">${r2.name} | ${r2.reason} | 净额 ${r2.net>0?'+':''}${r2.net.toFixed(0)}万</div>`});
html+=`</div>`}
el2.innerHTML=html;
}catch(e){document.getElementById('altDataResult').innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">加载失败: ${e.message}<br><button onclick="insightTab='altdata';renderInsight()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}

// ---- P5: RL 仓位管理 ----
async function renderRLPosition(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">🎮 强化学习仓位建议</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">Q-Learning Agent 在历史数据上训练，给出动态仓位建议</div>
<div style="display:flex;gap:8px;margin-bottom:16px">
<input id="rlCode" placeholder="股票代码 如 600519" class="input-field" style="flex:1;padding:10px 12px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px">
<button onclick="runRL()" style="padding:10px 16px;border-radius:10px;border:none;background:linear-gradient(135deg,#8B5CF6,#6366F1);color:#fff;font-weight:700;cursor:pointer;white-space:nowrap">🎮 获取建议</button></div>
<div id="rlResult"></div></div>`;
}

async function runRL(){const code=document.getElementById('rlCode')?.value?.trim();if(!code){alert('请输入股票代码');return}
const el=document.getElementById('rlResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>Q-Learning 训练中（5轮迭代）...</div>';
try{const r=await fetch(API_BASE+`/rl-position/${code}`,{signal:AbortSignal.timeout(90000)});const d=await r.json();
if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">${d.error}</div>`;return}
const ms=d.market_state||{};const ts=d.training_summary||{};
let html=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">
<div style="background:var(--bg2);border-radius:10px;padding:10px"><div style="font-size:11px;color:var(--text2)">市场状态</div><div style="font-size:13px;font-weight:700">${ms.trend} | RSI ${ms.rsi}</div><div style="font-size:11px;color:var(--text2)">20日收益 ${ms.return_20d}% · 波动 ${ms.volatility}%</div></div>
<div style="background:var(--bg2);border-radius:10px;padding:10px"><div style="font-size:11px;color:var(--text2)">训练效果</div><div style="font-size:13px;font-weight:700;color:${ts.outperformance>0?'#10B981':'#EF4444'}">超额收益 ${ts.outperformance>0?'+':''}${ts.outperformance}%</div><div style="font-size:11px;color:var(--text2)">RL ${ts.final_rl_return}% vs 买入持有 ${ts.buy_hold_return}%</div></div></div>`;
html+=`<div style="font-size:12px;font-weight:600;margin-bottom:8px">📋 不同仓位下的建议</div>`;
(d.recommendations||[]).forEach(r2=>{
html+=`<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 10px;margin-bottom:4px;background:var(--bg2);border-radius:8px;font-size:12px"><span style="width:70px">当前 ${r2.current_position}</span><span style="flex:1;font-weight:700">${r2.action}</span><span>→ ${r2.target_position}</span></div>`});
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">失败: ${e.message}</div>`}}

// ---- P6: LLM 因子生成 ----
async function renderLLMFactor(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">🧠 LLM 因子生成器 (Alpha-GPT)</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">让 DeepSeek AI 自动构思交易因子 → 生成代码 → IC 验证 → 迭代优化</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">🧠 AI 生成因子假设 → Python代码 → 自动回测IC → 反馈迭代（2轮进化）</div>
<div style="display:flex;gap:8px;margin-bottom:16px">
<input id="llmfCode" placeholder="股票代码 如 000001" class="input-field" value="000001" style="flex:1;padding:10px 12px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px">
<button onclick="runLLMFactor()" style="padding:10px 16px;border-radius:10px;border:none;background:linear-gradient(135deg,#EC4899,#8B5CF6);color:#fff;font-weight:700;cursor:pointer;white-space:nowrap">🧠 AI 生成</button></div>
<div id="llmfResult"></div></div>`;
}

async function runLLMFactor(){const code=document.getElementById('llmfCode')?.value?.trim()||'000001';
const el=document.getElementById('llmfResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>DeepSeek AI 构思因子中（2轮迭代）...<br><span style="font-size:11px;opacity:0.6">约30-90秒</span></div>';
try{const r=await fetch(API_BASE+`/llm-factor/${code}?count=5&iterations=2`,{signal:AbortSignal.timeout(180000)});const d=await r.json();
if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">${d.error}</div>`;return}
const s=d.summary||{};
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">生成因子</div><div style="font-size:20px;font-weight:800">${s.total_generated||0}</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">有效因子</div><div style="font-size:20px;font-weight:800;color:#10B981">${s.effective||0}</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">最高IC</div><div style="font-size:20px;font-weight:800;color:#3B82F6">${s.best_ic||0}</div></div></div>`;
(d.effective_factors||[]).forEach(f=>{const icColor=f.abs_ic>0.05?'#10B981':'#3B82F6';
html+=`<div style="padding:10px;margin-bottom:6px;background:var(--bg2);border-radius:10px;border-left:3px solid ${icColor}">
<div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="font-weight:700;font-size:12px">${f.name} ${f.status}</span><span style="color:${icColor};font-weight:700;font-size:12px">IC=${f.ic}</span></div>
<div style="font-size:11px;color:var(--text2);margin-bottom:4px">${f.logic||''}</div>
${f.code?`<details><summary style="font-size:10px;color:var(--accent);cursor:pointer">查看代码</summary><pre style="font-size:9px;background:var(--bg3);padding:6px;border-radius:4px;overflow-x:auto;margin-top:4px">${f.code}</pre></details>`:''}</div>`});
if(d.failed_factors?.length>0){html+=`<details style="margin-top:12px"><summary style="font-size:12px;color:var(--text2);cursor:pointer">❌ 失败/无效因子 (${d.failed_factors.length}个)</summary>`;
d.failed_factors.forEach(f=>{html+=`<div style="font-size:11px;color:var(--text2);padding:4px 8px">${f.name}: IC=${f.ic} ${f.status}</div>`});
html+=`</details>`}
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">生成失败: ${e.message}<br><button onclick="runLLMFactor()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}


// ---- 📡 信号侦察兵 Tab ----
async function renderSignalScout(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">📡 信号侦察兵</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">多源信号收集（新闻/公告/增减持/解禁/资金）→ 自动匹配你的持仓</div>
<div style="display:flex;gap:8px;margin-bottom:12px">
<button onclick="renderSignalScout(document.getElementById('insightContent'))" style="padding:8px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:12px">🔄 刷新</button>
<button onclick="manualScanSignals()" id="scanBtn" style="padding:8px 16px;border-radius:8px;border:1px solid var(--accent);background:transparent;color:var(--accent);font-weight:600;cursor:pointer;font-size:12px">🔍 全市场扫描</button>
</div>
<div id="signalScoutContent"><div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在匹配信号与你的持仓...</div></div>
</div>`;
try{
const uid=getProfileId();
const r=await fetch(API_BASE+'/signal-scout/latest?userId='+encodeURIComponent(uid),{signal:AbortSignal.timeout(30000)});
if(!r.ok)throw new Error('fetch failed');
const d=await r.json();
const el2=document.getElementById('signalScoutContent');if(!el2)return;
const signals=d.signals||[];
if(!signals.length){el2.innerHTML='<div style="text-align:center;padding:30px;color:var(--text2)">暂无匹配信号<br><span style="font-size:11px;opacity:0.6">你的持仓暂未检测到相关信号</span></div>';return}
const levelColor={danger:'var(--red)',warning:'#F59E0B',info:'var(--text2)'};
const levelIcon={danger:'🔴',warning:'⚠️',info:'📌'};
let html=`<div style="display:flex;gap:8px;margin-bottom:12px;font-size:12px;color:var(--text2)">
<span>匹配 <b style="color:var(--accent)">${d.total}</b> 条</span>
<span>高相关 <b style="color:var(--green)">${d.high_relevance}</b></span>
<span>${d.is_trading_day?'✅ 交易日':'🔒 非交易日'}</span></div>`;
html+=signals.map(s=>{
const icon=levelIcon[s.level]||'📌';
const color=levelColor[s.level]||'var(--text2)';
const relBadge=s.relevance>=50?`<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:rgba(16,185,129,.15);color:var(--green)">持仓相关</span>`:'';
const holdingBadge=s.related_holding?`<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:rgba(99,102,241,.15);color:#818CF8">${s.related_holding}</span>`:'';
const tags=(s.tags||[]).slice(0,3).map(t=>`<span style="font-size:10px;padding:1px 4px;border-radius:3px;background:var(--bg3);color:var(--text2)">${t}</span>`).join('');
return`<div style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,.06)">
<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
<span style="color:${color}">${icon}</span>
<span style="font-size:13px;font-weight:600;flex:1">${s.title}</span>
${relBadge}${holdingBadge}
</div>
<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:4px">${tags}</div>
<div style="font-size:11px;color:var(--text2);margin-top:4px">${s.source||''} · ${s.time||''}</div>
</div>`}).join('');
html+=`<div style="font-size:11px;color:#475569;margin-top:12px;text-align:center">扫描于 ${new Date(d.scanned_at).toLocaleString('zh-CN')}</div>`;
el2.innerHTML=html;
}catch(e){console.warn('Signal scout failed:',e);
const el2=document.getElementById('signalScoutContent');
if(el2)el2.innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)">信号加载失败<br><button onclick="renderSignalScout(document.getElementById('insightContent'))" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>`}}

async function manualScanSignals(){
const btn=document.getElementById('scanBtn');if(btn){btn.textContent='扫描中...';btn.disabled=true}
try{await fetch(API_BASE+'/signal-scout/scan',{method:'POST',signal:AbortSignal.timeout(30000)});
renderSignalScout(document.getElementById('insightContent'))}
catch(e){if(btn){btn.textContent='🔍 全市场扫描';btn.disabled=false}}}


// ---- 📊 判断成绩单 Tab ----
async function renderScorecard(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">📊 判断成绩单</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">追踪每次AI决策的准确率 → EMA自动校准模块权重 → 越用越准</div>
<div style="display:flex;gap:8px;margin-bottom:12px">
<button onclick="renderScorecard(document.getElementById('insightContent'))" style="padding:8px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:12px">🔄 刷新</button>
<button onclick="manualCalibrate()" id="calibBtn" style="padding:8px 16px;border-radius:8px;border:1px solid var(--accent);background:transparent;color:var(--accent);font-weight:600;cursor:pointer;font-size:12px">⚖️ 手动校准权重</button>
</div>
<div id="scorecardContent"><div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载成绩单...</div></div>
<div id="weightsContent" style="margin-top:16px"></div>
</div>`;
try{
const uid=getProfileId()||getUserId();
const[cardRes,weightRes]=await Promise.all([
fetch(API_BASE+'/judgment/scorecard?userId='+encodeURIComponent(uid),{signal:AbortSignal.timeout(15000)}),
fetch(API_BASE+'/judgment/weights?userId='+encodeURIComponent(uid),{signal:AbortSignal.timeout(10000)})
]);
const card=await cardRes.json();const wdata=await weightRes.json();
const el2=document.getElementById('scorecardContent');if(!el2)return;

// 核心指标卡
const accColor=card.accuracy>=70?'var(--green)':card.accuracy>=50?'var(--accent)':'var(--red)';
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900;color:${accColor}">${card.accuracy}%</div>
<div style="font-size:11px;color:var(--text2)">准确率</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900">${card.total}</div>
<div style="font-size:11px;color:var(--text2)">总判断</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900;color:var(--green)">${card.correct}</div>
<div style="font-size:11px;color:var(--text2)">✅正确 / ❌${card.wrong} / 🟡${card.partial}</div></div></div>`;

// 模块准确率
const modAcc=card.module_accuracy||{};
if(Object.keys(modAcc).length){
html+=`<div style="font-size:13px;font-weight:700;margin-bottom:8px">📊 各模块准确率</div>`;
Object.entries(modAcc).sort((a,b)=>b[1].accuracy-a[1].accuracy).forEach(([mod,s])=>{
const mc=s.accuracy>=70?'var(--green)':s.accuracy>=50?'var(--accent)':'var(--red)';
html+=`<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.06)">
<span style="flex:1;font-size:12px">${mod}</span>
<span style="font-size:11px;color:var(--text2)">${s.correct}/${s.total}</span>
<div style="width:80px;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden"><div style="height:100%;width:${s.accuracy}%;background:${mc};border-radius:3px"></div></div>
<span style="font-size:12px;font-weight:700;color:${mc};min-width:40px;text-align:right">${s.accuracy}%</span></div>`})}

// 最近判断
if(card.recent&&card.recent.length){
html+=`<div style="font-size:13px;font-weight:700;margin:16px 0 8px">📋 最近判断</div>`;
card.recent.forEach(r=>{
const dirIcon=r.direction==='bullish'?'📈':r.direction==='bearish'?'📉':'➖';
const verdictIcon=r.verdict==='correct'?'✅':r.verdict==='wrong'?'❌':r.verdict==='partial'?'🟡':'⏳';
const dt=r.recorded_at?.slice(0,16).replace('T',' ')||'';
html+=`<div style="display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.04);font-size:12px">
<span>${dirIcon}</span><span style="flex:1">${r.regime||''} · 置信${r.confidence}%</span>
<span>${verdictIcon}</span>
${r.actual_return!=null?`<span style="color:${r.actual_return>=0?'var(--green)':'var(--red)'}">实际${r.actual_return>0?'+':''}${r.actual_return}%</span>`:'<span style="color:var(--text2)">待验证</span>'}
<span style="color:var(--text2);font-size:10px">${dt.slice(5)}</span></div>`})}

el2.innerHTML=html;

// 权重面板
const wel=document.getElementById('weightsContent');if(wel){
const w=wdata.weights||{};
let wh=`<div style="font-size:13px;font-weight:700;margin-bottom:8px">⚖️ 当前模块权重（EMA校准）</div>`;
Object.entries(w).sort((a,b)=>b[1]-a[1]).forEach(([mod,val])=>{
const pct=Math.round(val*100);
wh+=`<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px">
<span style="min-width:120px">${mod}</span>
<div style="flex:1;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden"><div style="height:100%;width:${pct}%;background:var(--accent);border-radius:3px"></div></div>
<span style="min-width:35px;text-align:right;font-weight:600">${pct}%</span></div>`});
wel.innerHTML=wh}
}catch(e){console.warn('Scorecard failed:',e);
const el2=document.getElementById('scorecardContent');
if(el2)el2.innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)">暂无成绩数据<br><span style="font-size:11px;opacity:0.6">需要先有 Pipeline 决策记录</span></div>`}}

async function manualCalibrate(){
const btn=document.getElementById('calibBtn');if(btn){btn.textContent='校准中...';btn.disabled=true}
try{const uid=getProfileId()||getUserId();
const r=await fetch(API_BASE+'/judgment/calibrate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:uid}),signal:AbortSignal.timeout(15000)});
const d=await r.json();
if(d.status==='calibrated'){alert('✅ 权重校准完成！准确率:'+d.overall_accuracy+'%');renderScorecard(document.getElementById('insightContent'))}
else{alert('⚠️ '+d.message)}
}catch(e){alert('校准失败: '+e.message)}
finally{if(btn){btn.textContent='⚖️ 手动校准权重';btn.disabled=false}}}


// ---- 🏥 持仓体检 Tab ----
async function renderDoctor(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🏥 持仓体检</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">压力测试+集中度诊断+健康评分 — 找出你的持仓隐患</div>
<button onclick="runDoctor()" style="width:100%;padding:12px;border-radius:10px;border:none;background:linear-gradient(135deg,#10B981,#059669);color:#fff;font-weight:700;cursor:pointer;font-size:14px;margin-bottom:16px">🏥 开始体检</button>
<div id="doctorResult"></div></div>`;
}

async function runDoctor(){
const el=document.getElementById('doctorResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在体检（收集持仓+压力测试+集中度分析）...</div>';
try{
const r=await fetch(API_BASE+'/portfolio-doctor/diagnose?userId='+getProfileId(),{signal:AbortSignal.timeout(60000)});
if(!r.ok)throw new Error('体检失败');
const d=await r.json();
if(d.status==='no_data'){el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">暂无持仓，请先添加股票或基金</div>';return}
const h=d.health||{};const c=d.concentration||{};const s=d.stress_test||{};
let html='';
// 健康评分卡
html+=`<div style="text-align:center;padding:20px;margin-bottom:16px;background:var(--bg2);border-radius:16px">
<div style="font-size:48px;font-weight:900;color:${h.score>=70?'var(--green)':h.score>=50?'var(--accent)':'var(--red)'}">${h.score||0}</div>
<div style="font-size:16px;font-weight:700;margin-top:4px">${h.grade||'?'}</div>
<div style="display:flex;justify-content:center;gap:16px;margin-top:12px;font-size:12px">
${Object.entries(h.dimensions||{}).map(([k,v])=>{const max=(h.max_scores||{})[k]||25;const labels={concentration:'集中度',diversification:'多样性',risk:'风险',stability:'稳定性'};return`<div><div style="color:var(--text2)">${labels[k]||k}</div><div style="font-weight:700;color:${v>=max*0.7?'var(--green)':v>=max*0.4?'var(--accent)':'var(--red)'}\">${v}/${max}</div></div>`}).join('')}
</div></div>`;
// 集中度
html+=`<div style="margin-bottom:16px;background:var(--bg2);border-radius:12px;padding:12px">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">📊 集中度 ${c.hhi_level||''}</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">HHI=${c.hhi||0} | 权益占比 ${c.equity_pct||0}%</div>
${(c.holdings_weight||[]).slice(0,8).map(w=>`<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;font-size:12px"><span style="width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${w.name}</span><div style="flex:1;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden"><div style="height:100%;width:${Math.min(w.weight,100)}%;background:${w.weight>30?'var(--red)':w.weight>15?'var(--accent)':'var(--green)'};border-radius:3px"></div></div><span style="min-width:40px;text-align:right">${w.weight}%</span></div>`).join('')}
</div>`;
// 压力测试
if(s.scenarios&&s.scenarios.length){
html+=`<div style="margin-bottom:16px;background:var(--bg2);border-radius:12px;padding:12px">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">🔬 压力测试（总市值 ¥${(s.total_value||0).toLocaleString()}）</div>
${s.scenarios.map(sc=>`<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.06);font-size:12px">
<div><div style="font-weight:600">${sc.name}</div><div style="color:var(--text2);font-size:11px">${sc.description}</div></div>
<div style="text-align:right;min-width:70px"><div style="font-weight:800;color:var(--red)">${sc.loss_pct}%</div><div style="font-size:11px;color:var(--text2)">¥${Math.abs(sc.loss).toLocaleString()}</div></div>
</div>`).join('')}
</div>`}
// 问题清单
const issues=[...(h.issues||[]),...(c.issues||[])];
if(issues.length){
html+=`<div style="background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.15);border-radius:12px;padding:12px;margin-bottom:16px">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">⚠️ 发现 ${issues.length} 个问题</div>
${issues.map(i=>`<div style="font-size:12px;padding:4px 0;border-bottom:1px solid rgba(148,163,184,.06)">${i}</div>`).join('')}
</div>`}
html+=`<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="runDoctor()">🔄 重新体检</button></div>`;
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)">体检失败: ${e.message}<br><button onclick="runDoctor()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}


// ---- 🤖 AI管家 Tab ----
async function renderSteward(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🤖 AI 投资管家</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">管家按 Pipeline 全流程分析：Regime→模块并行→门控→EV→风控→结论</div>
<div style="display:flex;gap:8px;margin-bottom:8px">
<input id="stewardQ" placeholder="输入问题 如：茅台能买吗？" class="input-field" style="flex:1;min-width:0;padding:10px 12px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px">
<select id="stewardPipe" style="padding:10px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:12px;flex-shrink:0">
<option value="">自动选管线</option><option value="default">日常(default)</option><option value="fast">快速(fast)</option><option value="cautious">谨慎(cautious)</option></select>
</div>
<div style="display:flex;gap:8px;margin-bottom:16px">
<button onclick="runStewardAsk()" style="flex:2;padding:12px;border-radius:10px;border:none;background:var(--accent);color:#fff;font-weight:700;cursor:pointer;font-size:14px">🤖 管家分析</button>
<button onclick="runStewardBriefing()" style="flex:1;padding:12px;border-radius:10px;border:none;background:var(--card);border:1px solid var(--border);color:var(--text);font-weight:600;cursor:pointer;font-size:12px">📋 简报</button>
<button onclick="runBriefingHistory()" style="flex:1;padding:12px;border-radius:10px;border:none;background:var(--card);border:1px solid var(--border);color:var(--text);font-weight:600;cursor:pointer;font-size:12px">📚 往期</button>
</div>
<div id="stewardResult"></div>
<div style="margin-top:16px;padding-top:16px;border-top:1px solid rgba(148,163,184,.1)">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">📊 当前市场状态 (Regime)</div>
<div id="regimeResult"><div style="text-align:center;padding:12px;color:var(--text2);font-size:12px">点击上方按钮获取...</div></div>
</div></div>`;
loadRegime()}

async function loadRegime(){
const el=document.getElementById('regimeResult');if(!el||!API_AVAILABLE)return;
try{const r=await fetch(API_BASE+'/regime',{signal:AbortSignal.timeout(15000)});if(!r.ok)return;const d=await r.json();
const iconMap={'trending_bull':'📈','oscillating':'📊','high_vol_bear':'📉','rotation':'🔄'};
const colorMap={'trending_bull':'var(--green)','oscillating':'var(--accent)','high_vol_bear':'var(--red)','rotation':'#8B5CF6'};
el.innerHTML=`<div style="display:flex;align-items:center;gap:12px;padding:12px;background:var(--bg2);border-radius:12px;border-left:3px solid ${colorMap[d.regime]||'var(--accent)'}">
<div style="font-size:32px">${iconMap[d.regime]||'📊'}</div>
<div><div style="font-size:16px;font-weight:800;color:${colorMap[d.regime]||'var(--text)'}">${d.description||d.regime}</div>
<div style="font-size:12px;color:var(--text2);margin-top:4px">置信度 ${d.confidence}% · 管线→${d.regime==='high_vol_bear'?'cautious':d.regime==='rotation'?'fast':'default'}</div></div></div>`
}catch(e){el.innerHTML=`<div style="font-size:12px;color:var(--text2)">Regime 加载失败</div>`}}

async function runStewardAsk(){
const question=document.getElementById('stewardQ')?.value?.trim()||'综合分析';
const pipeline=document.getElementById('stewardPipe')?.value||null;
const el=document.getElementById('stewardResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>管家正在跑 Pipeline 全流程分析...<br><span style="font-size:11px;opacity:0.6">Regime→模块并行→门控→EV→风控→结论</span></div>';
try{const uid=getProfileId()||getUserId();
const body={userId:uid,question};if(pipeline)body.pipeline=pipeline;
const r=await fetch(API_BASE+'/steward/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(60000)});
const d=await r.json();
const dirColor=d.direction==='bullish'?'var(--green)':d.direction==='bearish'?'var(--red)':d.direction==='blocked'?'#EF4444':'var(--accent)';
const dirIcon=d.direction==='bullish'?'📈':d.direction==='bearish'?'📉':d.direction==='blocked'?'🚫':'📊';
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">方向</div><div style="font-size:22px;font-weight:900;color:${dirColor}">${dirIcon} ${d.direction||'neutral'}</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">置信度</div><div style="font-size:22px;font-weight:900">${d.confidence||0}%</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">管线</div><div style="font-size:14px;font-weight:700">${d.pipeline||'?'}</div></div></div>`;
if(d.conclusion)html+=`<div style="padding:12px;background:rgba(99,102,241,.06);border-radius:10px;border-left:3px solid ${dirColor};margin-bottom:12px;font-size:13px;line-height:1.8">${d.conclusion}</div>`;
if(d.regime_description)html+=`<div style="font-size:12px;color:var(--text2);margin-bottom:8px">📊 ${d.regime_description}</div>`;
if(d.gate_decision)html+=`<div style="font-size:12px;color:var(--text2);margin-bottom:8px">🚦 门控: ${d.gate_decision} (${d.gate_reason||''})</div>`;
if(d.ev_params)html+=`<div style="font-size:12px;color:var(--text2);margin-bottom:8px">📐 EV: ${d.ev_params.ev_pct}% (胜率${d.ev_params.winrate}% 盈${d.ev_params.expected_gain}% 亏${d.ev_params.expected_loss}%)</div>`;
if(d.risk_level&&d.risk_level!=='normal')html+=`<div style="font-size:12px;padding:8px;background:rgba(239,68,68,.08);border-radius:8px;margin-bottom:8px;color:var(--red)">${{'warning':'⚠️ 有风险提示','danger':'🔴 风控红灯','blocked':'🚫 操作已拦截'}[d.risk_level]||'⚠️ '+d.risk_level} ${(d.risk_alerts||[]).map(a=>a.msg).join(' · ')}</div>`;
if(d.modules_called?.length)html+=`<div style="font-size:11px;color:var(--text2);margin-bottom:8px">📦 模块: ${d.modules_called.join(', ')} (${d.modules_called.length}个)</div>`;
html+=`<div style="font-size:11px;color:var(--text3);text-align:center;margin-top:8px">Pipeline ${d.pipeline_steps?.length||0}步 · ${d.elapsed||0}s · LLM调用 ${d.llm_calls||0}次 · ${d.timestamp||''}</div>`;
el.innerHTML=html;loadRegime();
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:16px">分析失败: ${e.message}<br><button onclick="runStewardAsk()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}

async function runStewardBriefing(){
const el=document.getElementById('stewardResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>获取每日简报（快速版，0次LLM）...</div>';
try{const uid=getProfileId()||getUserId();
const r=await fetch(API_BASE+'/steward/briefing?userId='+encodeURIComponent(uid),{signal:AbortSignal.timeout(30000)});
const d=await r.json();
const iconMap={'trending_bull':'📈','oscillating':'📊','high_vol_bear':'📉','rotation':'🔄'};
let html=`<div style="padding:16px;background:var(--bg2);border-radius:12px;margin-bottom:12px">
<div style="font-size:18px;font-weight:800;margin-bottom:8px">${d.one_line||'每日简报'}</div>
<div style="display:flex;gap:12px;font-size:12px;color:var(--text2)">
<span>${iconMap[d.regime]||'📊'} ${d.regime_description||d.regime}</span>
<span>🛡️ ${{'normal':'正常','warning':'有风险提示','danger':'风控红灯','blocked':'操作已拦截'}[d.risk_level]||d.risk_level}</span>
<span>📡 ${d.signals_count||0}条信号</span>
</div>
${d.top_signal?`<div style="margin-top:8px;font-size:13px;padding:8px;background:rgba(245,158,11,.06);border-radius:8px">💡 ${d.top_signal}</div>`:''}
<div style="font-size:11px;color:var(--text3);margin-top:8px">${d.elapsed||0}s · 0次LLM</div></div>`;
el.innerHTML=html;loadRegime();
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">简报获取失败: ${e.message}</div>`}}

async function runBriefingHistory(){
const el=document.getElementById('stewardResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载往期晨报...</div>';
try{const uid=getProfileId()||getUserId();
const r=await fetch(API_BASE+'/steward/briefing-history?userId='+encodeURIComponent(uid)+'&days=7',{signal:AbortSignal.timeout(15000)});
const d=await r.json();
const items=d.history||[];
if(!items.length){el.innerHTML='<div style="text-align:center;padding:24px;color:var(--text2)">暂无往期晨报记录<br><span style="font-size:12px;opacity:0.6">管家每天生成的简报会保留7天</span></div>';return}
const iconMap={'trending_bull':'📈','oscillating':'📊','high_vol_bear':'📉','rotation':'🔄'};
let html='<div class="dashboard-card-title" style="margin-bottom:12px">📚 往期晨报 (近7天)</div>';
items.forEach(b=>{
const dateStr=b.date||'';
const dateLabel=dateStr.length===8?`${dateStr.slice(0,4)}-${dateStr.slice(4,6)}-${dateStr.slice(6,8)}`:dateStr;
html+=`<div style="padding:12px;background:var(--bg2);border-radius:12px;margin-bottom:8px">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
<span style="font-size:13px;font-weight:700">${iconMap[b.regime]||'📊'} ${dateLabel}</span>
<span style="font-size:11px;color:var(--text2)">🛡️ ${{'normal':'正常','warning':'有风险提示','danger':'风控红灯','blocked':'操作已拦截'}[b.risk_level]||b.risk_level||'正常'} · ${b.signals_count||0}条信号</span></div>
<div style="font-size:12px;color:var(--text1)">${b.one_line||b.regime_description||''}</div>
${b.top_signal?`<div style="font-size:12px;color:var(--text2);margin-top:4px">💡 ${b.top_signal}</div>`:''}
</div>`;});
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">加载失败: ${e.message}</div>`}}

// ---- 📋 周报 Tab ----
async function renderWeeklyReport(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">📋 投资周报</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:12px">汇总一周的判断记录+持仓变化+市场回顾</div>
<button onclick="loadWeeklyReport(0)" style="padding:10px 20px;border-radius:10px;border:none;background:var(--accent);color:#fff;font-weight:700;cursor:pointer;font-size:13px;margin-bottom:16px">📋 生成本周报告</button>
<div id="weeklyResult"></div>
<div style="margin-top:16px;padding-top:16px;border-top:1px solid rgba(148,163,184,.1)">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">📚 历史周报</div>
<div id="weeklyHistory"><div style="text-align:center;padding:12px;color:var(--text2);font-size:12px">点击上方按钮生成...</div></div>
</div></div>`;
loadWeeklyHistory()}

async function loadWeeklyReport(weeksAgo){
const el=document.getElementById('weeklyResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>生成周报中...</div>';
try{const r=await fetch(API_BASE+`/weekly-report?userId=${getProfileId()}&weeks_ago=${weeksAgo}`,{signal:AbortSignal.timeout(15000)});
const d=await r.json();if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">${d.error}</div>`;return}
const j=d.judgments||{};const p=d.portfolio_changes||{};const m=d.market_review||{};const recs=d.recommendations||[];
let html=`<div style="font-size:14px;font-weight:700;margin-bottom:12px">📊 ${d.period}</div>
<div style="font-size:13px;color:var(--text1);margin-bottom:12px;padding:8px 12px;background:rgba(99,102,241,.06);border-radius:10px">${d.summary}</div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:var(--bg2);border-radius:10px;padding:10px;text-align:center"><div style="font-size:11px;color:var(--text2)">分析次数</div><div style="font-size:20px;font-weight:800">${j.total_judgments||0}</div></div>
<div style="background:var(--bg2);border-radius:10px;padding:10px;text-align:center"><div style="font-size:11px;color:var(--text2)">准确率</div><div style="font-size:20px;font-weight:800;color:${(j.accuracy||0)>=60?'var(--green)':'var(--red)'}">${j.accuracy||0}%</div></div>
<div style="background:var(--bg2);border-radius:10px;padding:10px;text-align:center"><div style="font-size:11px;color:var(--text2)">交易笔数</div><div style="font-size:20px;font-weight:800">${p.total_transactions||0}</div></div></div>`;
if(m.regime)html+=`<div style="font-size:12px;color:var(--text2);margin-bottom:12px">📊 市场状态: <b>${m.regime_description||m.regime}</b> (${m.confidence||0}%)</div>`;
if(recs.length)html+=`<div style="padding:10px;background:rgba(59,130,246,.06);border-radius:10px;margin-bottom:12px">${recs.map(r2=>`<div style="font-size:12px;line-height:1.8">${r2}</div>`).join('')}</div>`;
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);padding:12px">生成失败: ${e.message}</div>`}}

async function loadWeeklyHistory(){
const el=document.getElementById('weeklyHistory');if(!el||!API_AVAILABLE)return;
try{const r=await fetch(API_BASE+`/weekly-report/history?userId=${getProfileId()}&limit=4`,{signal:AbortSignal.timeout(10000)});
const d=await r.json();const reports=d.reports||[];
if(!reports.length){el.innerHTML='<div style="text-align:center;padding:12px;font-size:12px;color:var(--text2)">暂无历史周报</div>';return}
el.innerHTML=reports.map(r2=>`<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--bg3);font-size:12px"><span>${r2.period}</span><span style="color:var(--text2)">${r2.summary||''}</span></div>`).join('');
}catch(e){el.innerHTML='<div style="font-size:12px;color:var(--text2)">加载失败</div>'}}

