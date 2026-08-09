// ---- Phase 5: 因子 IC 检验 ----
async function renderFactorIC(el, force=false){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🔬 因子 IC 检验</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">验证30因子中哪些真正具有收益预测能力（Spearman IC）</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">📊 |IC| > 0.05 = 优秀因子 · |IC| > 0.03 = 有效因子 · 参考 Barra 多因子模型标准</div>
<div id="factorICContent"><div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>${force?'强制重新计算中，约30-60秒...':'正在计算因子IC，需获取200只股票数据...'}<br><span style="font-size:11px;opacity:0.6">首次约30-60秒</span></div></div></div>`;
try{
const url=API_BASE+'/factor-ic?forward_days=20&pool_size=200'+(force?'&force=true':'');
const r=await fetch(url,{signal:AbortSignal.timeout(120000)});
if(!r.ok)throw new Error('fetch failed');
const d=await r.json();
if(d.error){document.getElementById('factorICContent').innerHTML=`<div style="text-align:center;padding:20px;color:var(--red)">${d.error}</div>`;return}
const ranking=d.ranking||[];
const summary=d.summary||{};
const recs=d.recommendations||[];
const ineffectiveIc=d.ineffective_factors||[];
const insufficientData=d.insufficient_data_factors||[];
// 统计数：真正无效(IC低) vs 数据不足
const realInvalid=insufficientData.length+ineffectiveIc.length;
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:rgba(16,185,129,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:24px;font-weight:900;color:var(--green)">${summary.effective_factors||0}</div>
<div style="font-size:11px;color:var(--text2)">有效因子</div></div>
<div style="background:rgba(239,68,68,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:24px;font-weight:900;color:var(--red)">${ineffectiveIc.length}</div>
<div style="font-size:11px;color:var(--text2)">IC偏低</div></div>
<div style="background:rgba(99,102,241,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:24px;font-weight:900;color:#818CF8">${summary.effectiveness_rate||0}%</div>
<div style="font-size:11px;color:var(--text2)">有效率</div></div></div>`;
// 数据不足提示（非交易日常见）
if(insufficientData.length>0){
const FACTOR_NAMES={'F07_REV_GROWTH':'营收增速','F08_NP_GROWTH':'净利增速','F13_GROSS_MARGIN':'毛利率','F14_NET_MARGIN':'净利率','F15_DEBT_RATIO':'资产负债率','F16_CASHFLOW':'每股现金流','F04_ROE_PB':'ROE/PB复合','F09_ROE':'ROE','F05_EPS':'EPS'};
const names=insufficientData.slice(0,5).map(f=>FACTOR_NAMES[f]||f).join('、');
html+=`<div style="background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:10px;padding:10px 12px;margin-bottom:12px;font-size:11px;color:var(--accent)">
⚠️ <strong>数据不足（${insufficientData.length}个因子）：</strong>${names}<br>
<span style="opacity:0.8">非交易日财务数据接口限流所致，非因子本身失效，下个交易日自动恢复</span></div>`}
// 建议
if(recs.length){html+=`<div style="background:rgba(59,130,246,.06);border-radius:10px;padding:10px 12px;margin-bottom:16px;font-size:12px;line-height:1.8;color:var(--text)">
<div style="font-weight:700;margin-bottom:4px">💡 分析建议</div>
${recs.map(r=>'• '+r).join('<br>')}</div>`}
// 因子排名表
html+=`<div style="font-size:13px;font-weight:700;margin-bottom:8px">📊 因子排名（按 |IC| 降序）</div>
<div style="display:grid;grid-template-columns:30px 1fr 60px 60px 60px;gap:4px;font-size:11px;color:var(--text2);font-weight:600;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.1)">
<div>#</div><div>因子</div><div style="text-align:right">IC</div><div style="text-align:right">样本</div><div style="text-align:right">评级</div></div>`;
ranking.forEach((f,i)=>{
const isInsufficient=f.invalid_reason==='data_insufficient';
const levelColor=f.level==='优秀'?'var(--green)':f.level==='有效'?'#3B82F6':f.level==='微弱'?'var(--accent)':isInsufficient?'#94a3b8':'var(--red)';
const rowOpacity=isInsufficient?'opacity:0.5;':'';
const icColor=f.ic>0?'var(--green)':'var(--red)';
const levelLabel=isInsufficient?'数据缺':f.level;
html+=`<div style="display:grid;grid-template-columns:30px 1fr 60px 60px 60px;gap:4px;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.04);align-items:center;${rowOpacity}">
<div style="font-size:11px;color:var(--text2);font-weight:700">${isInsufficient?'—':i+1}</div>
<div><div style="font-size:12px;font-weight:600">${f.name_cn||f.factor}</div>
<div style="font-size:10px;color:var(--text2)">${isInsufficient?'⚠️ 数据不足，非交易日可能缺失':(f.direction||'')+' · '+f.factor}</div></div>
<div style="text-align:right;font-size:13px;font-weight:700;color:${isInsufficient?'#94a3b8':icColor}">${isInsufficient?'N/A':(f.ic>0?'+':'')+f.ic}</div>
<div style="text-align:right;font-size:11px;color:var(--text2)">${f.samples}</div>
<div style="text-align:right"><span style="font-size:10px;padding:2px 6px;border-radius:4px;background:${levelColor}20;color:${levelColor}">${levelLabel}</span></div></div>`});
html+=`<div style="text-align:center;margin-top:16px;display:flex;gap:8px;justify-content:center">
<button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 20px;font-size:12px" onclick="renderFactorIC(document.getElementById('insightContent'),true)">🔄 强制重新检验</button>
<button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 20px;font-size:12px" onclick="loadICDecay()">📉 查看衰减曲线</button>
</div>
<div id="icDecaySection"></div>
<div style="font-size:11px;color:#475569;margin-top:8px;text-align:center">样本池 ${summary.pool_size||0} 只 · 预测周期 ${summary.forward_days||20} 日 · 耗时 ${summary.elapsed_seconds||0}s${insufficientData.length>0?' · ⚠️ '+insufficientData.length+'个因子数据缺失':''}</div>`;
document.getElementById('factorICContent').innerHTML=html;
}catch(e){console.warn('Factor IC failed:',e);document.getElementById('factorICContent').innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)">加载失败<br><button onclick="renderFactorIC(document.getElementById('insightContent'))" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>`}}


// ---- IC 衰减曲线（按需加载）----
async function loadICDecay(){
const sec=document.getElementById('icDecaySection');
if(!sec)return;
sec.innerHTML=`<div style="margin-top:12px;padding:12px;background:var(--bg2);border-radius:10px;text-align:center;color:var(--text2);font-size:12px"><div class="loading-spinner" style="width:18px;height:18px;margin:0 auto 6px;border-width:2px"></div>加载衰减曲线（约30秒）...</div>`;
try{
const r=await fetch(API_BASE+'/factor-ic/decay?pool_size=150',{signal:AbortSignal.timeout(180000)});
if(!r.ok)throw new Error('fetch failed');
const d=await r.json();
const decay=d.decay||{};
const periods=d.periods||[5,10,20,60];
const factors=Object.entries(decay).sort((a,b)=>{
const a0=Math.abs(Object.values(a[1].periods||{})[0]?.ic||0);
const b0=Math.abs(Object.values(b[1].periods||{})[0]?.ic||0);
return b0-a0;}).slice(0,8);
let html=`<div style="margin-top:12px;padding:12px;background:var(--bg2);border-radius:10px">
<div style="font-size:12px;font-weight:700;margin-bottom:8px">📉 IC 衰减曲线（TOP8因子，5/10/20/60日）</div>
<div style="font-size:10px;color:var(--text2);margin-bottom:10px">观察因子是短期有效还是长期有效</div>`;
factors.forEach(([fname,info])=>{
const ps=info.periods||{};
const ics=periods.map(p=>ps[String(p)]?.ic||0);
const maxAbs=Math.max(...ics.map(Math.abs),0.001);
const patternColor=info.pattern==='短期因子'?'var(--accent)':info.pattern==='长期因子'?'#818CF8':'var(--green)';
html+=`<div style="margin-bottom:8px;padding:8px;background:var(--card);border-radius:8px">
<div style="display:flex;justify-content:space-between;margin-bottom:4px">
<span style="font-size:11px;font-weight:600">${info.name_cn||fname}</span>
<span style="font-size:10px;color:${patternColor}">${info.pattern||''}</span></div>
<div style="display:flex;gap:4px;align-items:flex-end;height:32px">
${periods.map((p,i)=>{
const ic=ics[i];const h=Math.abs(ic)/maxAbs*28;const c=ic>0?'var(--green)':'var(--red)';
return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:2px">
<div style="width:100%;height:${h}px;background:${c};border-radius:2px;min-height:2px"></div>
<div style="font-size:9px;color:var(--text2)">${p}d</div></div>`;}).join('')}
</div></div>`;});
html+=`</div>`;
sec.innerHTML=html;
}catch(e){sec.innerHTML=`<div style="margin-top:8px;text-align:center;font-size:11px;color:var(--text2)">衰减曲线加载失败（需较长时间，可稍后重试）</div>`}}

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
<div id="scorecardContent"><div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载成绩单（自动补验到期记录）...</div></div>
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

// 校准按钮状态（数据不足时灰显）
const canCalibrate=card.can_calibrate;
const calibBtn=document.getElementById('calibBtn');
if(calibBtn){
  if(!canCalibrate){
    calibBtn.disabled=true;
    calibBtn.style.opacity='0.4';
    calibBtn.style.cursor='not-allowed';
    calibBtn.title=`需要至少${card.calibrate_needed||10}条已验证记录（当前${card.verified||0}条）`;
  }
}

// 核心指标卡
const accColor=card.accuracy>=70?'var(--green)':card.accuracy>=50?'var(--accent)':'var(--red)';
const verifyDays=card.verify_days||15;
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px">
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900;color:${accColor}">${card.accuracy}%</div>
<div style="font-size:11px;color:var(--text2)">准确率</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900">${card.total}</div>
<div style="font-size:11px;color:var(--text2)">总判断</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900;color:var(--green)">${card.correct}</div>
<div style="font-size:11px;color:var(--text2)">✅正确 / ❌${card.wrong} / 🟡${card.partial}</div></div></div>`;

// 待验证说明（有大量待验证时给解释）
if(card.pending>0){
  html+=`<div style="background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.15);border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:11px;color:var(--accent)">
  ⏳ <b>${card.pending}条</b> 判断待验证（判断后第${verifyDays}天自动核对，刷新时自动补验到期记录）</div>`}

// 无已验证记录时的说明
if(card.verified===0&&card.total>0){
  html+=`<div style="background:rgba(99,102,241,.06);border-radius:10px;padding:10px 12px;margin-bottom:12px;font-size:12px;color:var(--text2)">
  💡 所有判断还在观察期中，${verifyDays}天后系统会对照实际行情自动评分。目前准确率 0% 属于正常——还没有到期记录可以验证。</div>`}

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
const dir=r.direction||'neutral';
const dirIcon=dir==='bullish'?'📈':dir==='bearish'?'📉':'➖';
const dirColor=dir==='bullish'?'var(--green)':dir==='bearish'?'var(--red)':'var(--text2)';
const verdictIcon=r.verdict==='correct'?'✅':r.verdict==='wrong'?'❌':r.verdict==='partial'?'🟡':'⏳';
const verdictLabel=r.verdict==='correct'?'正确':r.verdict==='wrong'?'错误':r.verdict==='partial'?'部分':'待验证';
const dt=r.recorded_at?.slice(0,16).replace('T',' ')||'';
const regimeLabel=r.regime||'';
const conf=r.confidence||0;
// 计算还差几天可以验证
let daysLeft='';
if(!r.verified&&r.verify_at){
  const diff=Math.ceil((new Date(r.verify_at)-new Date())/86400000);
  daysLeft=diff>0?`还${diff}天`:'已到期';
}
html+=`<div style="display:flex;align-items:center;gap:6px;padding:7px 0;border-bottom:1px solid rgba(148,163,184,.04);font-size:12px">
<span style="font-size:14px">${dirIcon}</span>
<div style="flex:1;min-width:0">
  <div style="color:${dirColor};font-weight:600;font-size:11px">${regimeLabel} · ${dir==='bullish'?'看多':dir==='bearish'?'看空':'中性'} · 置信${conf}%</div>
  <div style="font-size:10px;color:var(--text2)">${dt.slice(5)}</div>
</div>
<div style="text-align:right;min-width:80px">
  <div style="font-size:12px">${verdictIcon} <span style="font-size:11px;color:var(--text2)">${verdictLabel}</span></div>
  ${r.actual_return!=null?`<div style="font-size:11px;color:${r.actual_return>=0?'var(--green)':'var(--red)'}">实际${r.actual_return>0?'+':''}${r.actual_return}%</div>`:`<div style="font-size:10px;color:var(--text3,#475569)">${daysLeft}</div>`}
</div></div>`})}

el2.innerHTML=html;

// 权重面板
const wel=document.getElementById('weightsContent');if(wel){
const w=wdata.weights||{};
if(Object.keys(w).length){
let wh=`<div style="font-size:13px;font-weight:700;margin-bottom:8px">⚖️ 当前模块权重${wdata.calibrated_at?'（已EMA校准 '+wdata.calibrated_at.slice(0,10)+'）':'（默认权重，未校准）'}</div>`;
Object.entries(w).sort((a,b)=>b[1]-a[1]).forEach(([mod,val])=>{
const pct=Math.round(val*100);
wh+=`<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px">
<span style="min-width:120px">${mod}</span>
<div style="flex:1;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden"><div style="height:100%;width:${pct}%;background:var(--accent);border-radius:3px"></div></div>
<span style="min-width:35px;text-align:right;font-weight:600">${pct}%</span></div>`});
if(!canCalibrate){wh+=`<div style="font-size:11px;color:var(--text2);margin-top:8px">⚙️ 需 ${card.calibrate_needed||10} 条已验证记录才能EMA校准（当前 ${card.verified||0} 条）</div>`}
wel.innerHTML=wh}}
}catch(e){console.warn('Scorecard failed:',e);
const el2=document.getElementById('scorecardContent');
if(el2)el2.innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)">暂无成绩数据<br><span style="font-size:11px;opacity:0.6">需要先有 Pipeline 决策记录</span></div>`}}

async function manualCalibrate(){
const btn=document.getElementById('calibBtn');
if(btn&&btn.disabled){
  alert('⚠️ 校准需要更多数据\n\n当前已验证记录不足，请等待更多判断记录到期验证后再校准。\n\n刷新成绩单时会自动补验到期记录。');
  return;
}
if(btn){btn.textContent='校准中...';btn.disabled=true}
try{const uid=getProfileId()||getUserId();
const r=await fetch(API_BASE+'/judgment/calibrate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:uid}),signal:AbortSignal.timeout(15000)});
const d=await r.json();
if(d.status==='calibrated'){
  const changes=d.changes||{};
  const changeStr=Object.entries(changes).map(([m,c])=>`${m}: ${c.old*100|0}%→${c.new*100|0}% ${c.direction}`).join('\n');
  alert(`✅ 权重校准完成！\n准确率:${d.overall_accuracy}%\n\n${changeStr||'权重无变化'}`);
  renderScorecard(document.getElementById('insightContent'))
}else{alert('⚠️ '+d.message)}
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
const dirLabel={'bullish':'看多','bearish':'看空','neutral':'中性','blocked':'已拦截'}[d.direction]||'中性';
const pipeLabel={'default':'日常','fast':'快速','cautious':'谨慎'}[d.pipeline]||d.pipeline||'日常';
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">方向</div><div style="font-size:22px;font-weight:900;color:${dirColor}">${dirIcon}<br>${dirLabel}</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">置信度</div><div style="font-size:22px;font-weight:900">${d.confidence||0}%</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">管线</div><div style="font-size:14px;font-weight:700">${pipeLabel}</div></div></div>`;
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


// ============================================================
// ♾️ 长期持有筛选器 + 复利计算器
// ============================================================

async function renderLongtermScreen(el){
el.innerHTML=`
<div class="dashboard-card" style="overflow:hidden;margin-bottom:12px">
  <div class="dashboard-card-title">♾️ 长期持有筛选器</div>
  <div style="font-size:12px;color:var(--text2);margin-bottom:12px">
    面向 10-20 年复利定投。与短期排行榜不同，筛选维度：夏普比率·最大回撤·成立年限·规模适中
  </div>
  <div style="display:flex;gap:8px;margin-bottom:12px">
    <button class="section-tab active" id="ltTabFund" onclick="switchLtTab('fund')">💼 长持基金</button>
    <button class="section-tab" id="ltTabStock" onclick="switchLtTab('stock')">📈 长持股票</button>
    <button class="section-tab" id="ltTabCalc" onclick="switchLtTab('calc')">🧮 复利计算</button>
  </div>
  <div id="ltContent"><div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载长持基金榜单...</div></div>
</div>`;
loadLtFunds();}

let _ltFundData=null,_ltStockData=null;

function switchLtTab(tab){
['fund','stock','calc'].forEach(t=>{const btn=document.getElementById('ltTab'+t.charAt(0).toUpperCase()+t.slice(1));if(btn)btn.classList.toggle('active',t===tab)});
if(tab==='fund')loadLtFunds();
else if(tab==='stock')loadLtStocks();
else renderLtCalc();}

function _ltBuildUrl(path, force){
return API_BASE+path+'?userId='+encodeURIComponent(getProfileId())+(force?'&force=true':'');
}

function _ltDescribeLoadError(err, label){
const raw=(err&&err.message?String(err.message):'').trim();
if(!raw) return `${label}服务暂时不可用，请稍后再试`;
if(/abort|timeout/i.test(raw)) return `${label}接口响应超时，请稍后再试`;
if(/^HTTP\s*\d+/i.test(raw)) return `${label}服务暂时异常（${raw}）`;
if(/fetch failed/i.test(raw)) return `${label}服务暂时异常，请稍后重试`;
return `${label}返回异常：${raw}`;
}

function _ltEscapeHtml(text){
return String(text||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function _ltBuildDiagnostic(kind, detail, requestPath, force){
const now = new Date();
const pageUrl = typeof location!=='undefined' ? location.href : '';
const userAgent = typeof navigator!=='undefined' ? navigator.userAgent : '';
const online = typeof navigator!=='undefined' ? navigator.onLine : true;
const userId = typeof getProfileId==='function' ? getProfileId() : '';
return [
  '[MoneyBag 长持诊断]',
  `模块: 长持${kind}`,
  `时间: ${now.toLocaleString('zh-CN', { hour12:false })}`,
  `用户: ${userId || 'unknown'}`,
  `请求: ${requestPath}?userId=${encodeURIComponent(userId || '')}`,
  `强制刷新: ${force ? '是' : '否'}`,
  `错误: ${detail}`,
  `页面: ${pageUrl}`,
  `在线状态: ${String(online)}`,
  `UA: ${userAgent}`,
].join('\n');
}

async function _ltCopyText(text){
if(!text) return false;
try{
  if(navigator.clipboard&&navigator.clipboard.writeText){
    await navigator.clipboard.writeText(text);
    return true;
  }
}catch(_err){}
try{
  const ta=document.createElement('textarea');
  ta.value=text;
  ta.setAttribute('readonly','readonly');
  ta.style.position='fixed';
  ta.style.opacity='0';
  document.body.appendChild(ta);
  ta.select();
  const ok=document.execCommand('copy');
  ta.remove();
  return !!ok;
}catch(_err){return false;}
}

window._ltCopyErrorInfo = async function(kind, encodedDetail, requestPath, force){
const detail = decodeURIComponent(encodedDetail||'');
const text = _ltBuildDiagnostic(kind, detail, requestPath, !!force);
const ok = await _ltCopyText(text);
alert(ok ? '已复制错误信息，可直接截图或粘贴给我排查。' : '复制失败，请长按选择错误文本或直接上报诊断。');
};

window._ltReportDiagnostic = async function(kind, encodedDetail, requestPath, force){
const detail = decodeURIComponent(encodedDetail||'');
const text = _ltBuildDiagnostic(kind, detail, requestPath, !!force);
await _ltCopyText(text);
if(typeof navigateTo==='function') navigateTo('chat');
setTimeout(()=>{
  const inp=document.getElementById('chatIn');
  if(inp){
    inp.value=`请帮我排查长持${kind}加载失败，诊断信息如下：\n\n${text}`;
    inp.focus();
  }
},300);
};

window._prefetchLongtermFundDetails = function(funds, force=false){
if(typeof window._prefetchFundDetail!=='function') return Promise.resolve([]);
const topFunds=(funds||[]).filter(f=>f&&f.code).slice(0,3);
return Promise.allSettled(topFunds.map(f=>window._prefetchFundDetail(f.code, f.name||'', { force: !!force })));
};

function _ltRenderErrorCard(kind, detail, retryFn, forceFn, requestPath, force){
const title = kind==='基金' ? '长持基金加载失败' : '长持股票加载失败';
const encodedDetail = encodeURIComponent(detail||'');
return `<div style="padding:16px 14px;border-radius:12px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.18);color:var(--text-primary,#F0F2F7)">
  <div style="font-size:14px;font-weight:700;color:#FCA5A5">⚠️ ${title}</div>
  <div style="margin-top:8px;font-size:12px;color:var(--text2);line-height:1.6">${_ltEscapeHtml(detail)}</div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
    <button onclick="${retryFn}" style="padding:6px 14px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 再试一次</button>
    <button onclick="${forceFn}" style="padding:6px 14px;border-radius:8px;border:1px solid rgba(99,102,241,.25);background:rgba(99,102,241,.12);color:#818CF8;cursor:pointer;font-size:12px">⚡ 强制刷新</button>
    <button onclick="_ltCopyErrorInfo('${kind}','${encodedDetail}','${requestPath}',${force?'true':'false'})" style="padding:6px 14px;border-radius:8px;border:1px solid rgba(148,163,184,.25);background:rgba(148,163,184,.1);color:var(--text2);cursor:pointer;font-size:12px">📋 复制错误信息</button>
    <button onclick="_ltReportDiagnostic('${kind}','${encodedDetail}','${requestPath}',${force?'true':'false'})" style="padding:6px 14px;border-radius:8px;border:1px solid rgba(16,185,129,.25);background:rgba(16,185,129,.12);color:#86EFAC;cursor:pointer;font-size:12px">🩺 上报诊断</button>
  </div>
  <div style="margin-top:8px;font-size:11px;color:var(--text2);line-height:1.5">如果后端刚恢复，点“强制刷新”会跳过缓存强制拉取最新结果。</div>
</div>`;
}

async function loadLtFunds(force=false){
const el=document.getElementById('ltContent');if(!el)return;
if(!force&&_ltFundData){renderLtFundList(_ltFundData);return;}
if(force){
  _ltFundData=null;
  if(typeof window._clearFundDetailPrefetchCache==='function') window._clearFundDetailPrefetchCache();
}
// v9.9.2: 明确区分普通刷新与强制刷新
el.innerHTML=`<div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>${force?'强制刷新长持评分，正在跳过缓存拉取最新结果...':'加载长持评分...'}</div>`;
try{
const r=await fetch(_ltBuildUrl('/longterm/funds', force),{signal:AbortSignal.timeout(force?90000:60000)});
let d=null;
try{d=await r.json();}catch(_err){}
if(!r.ok)throw new Error((d&&(d.detail||d.error||d.message))||`HTTP ${r.status}`);
if(d&&d.error)throw new Error(d.error);
_ltFundData=d;
renderLtFundList(d);
}catch(e){el.innerHTML=_ltRenderErrorCard('基金',_ltDescribeLoadError(e,'长持基金'),'loadLtFunds()','loadLtFunds(true)','/api/longterm/funds',force)}}

// v9.5.54 方案A: 长持评级转换（A+/A/B+/B/C）
function _ltGrade(score){
  if(score>=80) return {label:'A+', color:'#86EFAC', border:'rgba(34,197,94,.6)', bg:'rgba(34,197,94,.18)'};
  if(score>=70) return {label:'A',  color:'#86EFAC', border:'rgba(34,197,94,.5)', bg:'rgba(34,197,94,.12)'};
  if(score>=60) return {label:'B+', color:'#FBBF24', border:'rgba(245,158,11,.5)', bg:'rgba(245,158,11,.15)'};
  if(score>=50) return {label:'B',  color:'#FBBF24', border:'rgba(245,158,11,.4)', bg:'rgba(245,158,11,.1)'};
  return            {label:'C',  color:'#FCA5A5', border:'rgba(239,68,68,.4)',  bg:'rgba(239,68,68,.1)'};
}

// v9.5.54: 行业图标映射
function _ltIndustryIcon(itag){
  const s = itag||'';
  if(/白酒|消费|食品/.test(s)) return '🍷';
  if(/半导体|芯片/.test(s)) return '💎';
  if(/医药|生物|医疗/.test(s)) return '💊';
  if(/新能源|光伏|风电|锂电/.test(s)) return '☀️';
  if(/军工|防务/.test(s)) return '🛡️';
  if(/银行|金融/.test(s)) return '🏦';
  if(/煤炭|石油|能源/.test(s)) return '⛽';
  if(/AI|算力|科技|计算机/.test(s)) return '🤖';
  if(/红利|低波|价值/.test(s)) return '💰';
  if(/通信|5G/.test(s)) return '📡';
  if(/汽车|新能源车/.test(s)) return '🚗';
  if(/纳指|标普|海外/.test(s)) return '🇺🇸';
  if(/港股|恒生/.test(s)) return '🇭🇰';
  return '📊';
}

// v9.5.56: 行业兜底从基金名提取
function _ltGuessIndustryFromName(name){
  const n = name||'';
  if(/黄金|金ETF|金条/.test(n)) return '黄金';
  if(/纳指|纳斯达克|纳100|标普|S&P/.test(n)) return '美股';
  if(/港股|香港|恒生|H股/.test(n)) return '港股';
  if(/日经|日本/.test(n)) return '日股';
  if(/越南|印度|德国|法国|欧洲/.test(n)) return '海外';
  if(/沪深300|中证300/.test(n)) return '沪深300';
  if(/上证50/.test(n)) return '上证50';
  if(/中证500/.test(n)) return '中证500';
  if(/中证1000/.test(n)) return '中证1000';
  if(/创业板|创业50/.test(n)) return '创业板';
  if(/科创|科创50/.test(n)) return '科创板';
  if(/北证|北交所/.test(n)) return '北交所';
  if(/红利|低波|高股息|价值/.test(n)) return '红利价值';
  if(/消费|白酒|食品|饮料/.test(n)) return '消费';
  if(/医药|医疗|生物|创新药/.test(n)) return '医药';
  if(/半导体|芯片|集成电路/.test(n)) return '半导体';
  if(/AI|算力|人工智能|智能/.test(n)) return 'AI算力';
  if(/新能源|光伏|风电|锂电/.test(n)) return '新能源';
  if(/军工|国防|防务|航空航天/.test(n)) return '军工';
  if(/银行/.test(n)) return '银行';
  if(/证券|券商/.test(n)) return '证券';
  if(/煤炭|石油|油气/.test(n)) return '能源';
  if(/通信|5G|光通信/.test(n)) return '通信';
  if(/汽车|新能源车/.test(n)) return '汽车';
  if(/物流|交运|航运/.test(n)) return '物流';
  if(/混合|平衡/.test(n)) return '混合型';
  if(/债|纯债|信用债/.test(n)) return '债券';
  if(/ETF|指数|跟踪/.test(n)) return '宽基指数';
  return '';
}

// v9.5.54: 基金长持标签生成（最多 4 个）
function _buildLtFundTags(f){
  const tags=[];
  const ageYears = f.age_years || (f.age_label ? parseFloat(f.age_label.match(/(\d+\.?\d*)/)?.[1])||0 : 0);

  // 1. 成立年限
  if(ageYears>=10) tags.push({label:`⭐ ${Math.floor(ageYears)}年老牌`, color:'#86EFAC', bg:'rgba(34,197,94,.18)'});
  else if(ageYears>=5) tags.push({label:`⭐ ${Math.floor(ageYears)}年成熟`, color:'#FBBF24', bg:'rgba(245,158,11,.15)'});
  else if(ageYears>=3) tags.push({label:`✅ ${Math.floor(ageYears)}年`, color:'#A5B4FC', bg:'rgba(99,102,241,.12)'});

  // 2. 行业徽章（兜底从名字提取）
  const indTag = f.industry_tag || _ltGuessIndustryFromName(f.name||'');
  if(indTag) tags.push({label:`${_ltIndustryIcon(indTag)} ${indTag}`, color:'#A5B4FC', bg:'rgba(99,102,241,.18)'});

  // 3. 晨星评级
  const ms5 = f.morning_star_5y || f.morning_star_3y;
  if(ms5) tags.push({label:`⭐ 晨星${f.morning_star_5y?'5年':'3年'}${ms5}`, color:'#FBBF24', bg:'rgba(245,158,11,.15)'});

  // 4. 适用场景标签（基于收益+波动）
  const annRet = f.ann_ret_5y || f.ann_ret_3y;
  if(annRet!=null && annRet>=8 && (f.max_drawdown==null || Math.abs(f.max_drawdown)<=30)){
    tags.push({label:'💰 适合定投', color:'#F9A8D4', bg:'rgba(244,114,182,.15)'});
  }
  if(f.sharpe!=null && f.sharpe>=1.2){
    tags.push({label:'🛡️ 风险调整优', color:'#86EFAC', bg:'rgba(34,197,94,.15)'});
  }
  // 兜底：长持评分高 → 长持优选
  if(tags.length<4 && (f.longterm_score||0)>=70){
    tags.push({label:'🏆 长持优选', color:'#86EFAC', bg:'rgba(34,197,94,.15)'});
  }

  return tags.slice(0,4);
}

// v9.5.57: 复利预期（三档：保守/中性/乐观）
// 输入：当前年化（来自近1-5年表现）+ 基金名（判断类别长期均值）
// 输出：{conservative, neutral, optimistic} 各档 15 年终值
function _ltCategoryLongRunAvg(name){
  // 类别长期均值（基于 A 股近 20 年实证 + 国际市场长期数据）
  const n = name||'';
  if(/黄金|金ETF/.test(n)) return 6;          // 黄金长期年化 5-8%
  if(/纯债|信用债|国债/.test(n)) return 4;     // 债券 3-5%
  if(/红利|低波|高股息/.test(n)) return 9;     // 红利策略 8-10%
  if(/沪深300|上证50|中证300/.test(n)) return 8; // A股宽基 7-9%
  if(/中证500|中证1000/.test(n)) return 9;     // A股中小盘略高
  if(/创业板|科创/.test(n)) return 10;         // 成长板块波动大
  if(/纳指|纳斯达克|标普|S&P/.test(n)) return 10; // 美股长期 ~10%
  if(/港股|恒生/.test(n)) return 7;            // 港股 6-8%
  if(/消费|白酒/.test(n)) return 11;           // 消费龙头长期表现优
  if(/医药/.test(n)) return 9;
  if(/半导体|芯片|AI|算力/.test(n)) return 11;  // 科技高波动
  if(/新能源|光伏|风电/.test(n)) return 10;
  if(/军工|周期/.test(n)) return 7;
  if(/混合|平衡/.test(n)) return 8;
  return 8; // 默认 A 股均值
}

function _ltCompoundEstimate(annRetPct, fundName){
  if(annRetPct==null) return null;
  const monthly=2000, years=15, n=years*12, invested=monthly*n;
  // 类别长期均值
  const longAvg = _ltCategoryLongRunAvg(fundName);
  // 三档年化（避免线性外推）
  const conservative = Math.min(longAvg * 0.6, Math.max(annRetPct * 0.4, 3));  // 保守：长均×0.6，或当前×0.4，至少 3%
  const neutral      = Math.min(annRetPct, longAvg);                            // 中性：取 min(当前, 长均) — 关键！防止 +29% 这种短期热度外推
  const optimistic   = Math.min(Math.max(annRetPct * 0.8, longAvg), 15);        // 乐观：当前×0.8（折损 20%），封顶 15%
  function fv(annPct){
    if(annPct<=0) return invested;
    const r=(annPct/100)/12;
    return monthly*((Math.pow(1+r,n)-1)/r);
  }
  return {
    conservative: {rate: +conservative.toFixed(1), fv: Math.round(fv(conservative))},
    neutral:      {rate: +neutral.toFixed(1),      fv: Math.round(fv(neutral))},
    optimistic:   {rate: +optimistic.toFixed(1),   fv: Math.round(fv(optimistic))},
    invested,
    longAvg,
  };
}

function renderLtFundList(d){
const el=document.getElementById('ltContent');if(!el)return;
const funds=d.funds||[];
if(!funds.length){el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">暂无数据（等待月初缓存预热）</div>';return;}
const src=d.data_source==='tushare_fund_indicator'?'Tushare精算':'快速估算';
const isFallback=d.data_source!=='tushare_fund_indicator';
const genDate=(d.generated_at||'').slice(0,10);
let html=`<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:11px;color:var(--text2);margin-bottom:8px"><span>数据来源：${src} · 更新：${genDate} · 30天缓存</span><span style="display:flex;gap:6px;margin-left:auto"><button onclick="loadLtFunds()" style="padding:2px 8px;border-radius:4px;border:none;background:var(--bg3);color:var(--text2);font-size:10px;cursor:pointer">🔄 刷新</button><button onclick="loadLtFunds(true)" style="padding:2px 8px;border-radius:4px;border:1px solid rgba(99,102,241,.25);background:rgba(99,102,241,.12);color:#818CF8;font-size:10px;cursor:pointer">⚡ 强制刷新</button></span></div>`;
if(isFallback){html+=`<div style="background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:8px;padding:8px 10px;margin-bottom:10px;font-size:11px;color:#F59E0B">⚠️ 夏普/回撤需要Tushare高级权限（当前积分不足）<br>已用3年收益稳定性评分，并补充晨星/济安金信评级 ★★★★</div>`;}

funds.slice(0,20).forEach((f,i)=>{
  const grade = _ltGrade(f.longterm_score||0);
  const annRet = f.ann_ret_5y!=null ? f.ann_ret_5y : f.ann_ret_3y;
  const annLabel = f.ann_ret_5y!=null ? '5年年化' : (f.ann_ret_3y!=null ? '3年年化' : '');
  const annColor = annRet>=8 ? '#86EFAC' : annRet>=3 ? '#FBBF24' : '#FCA5A5';

  // 4 类标签
  const tags = _buildLtFundTags(f);
  const tagsHtml = tags.map(t=>`<span style="display:inline-flex;align-items:center;font-size:10px;padding:2px 7px;border-radius:4px;background:${t.bg};color:${t.color};font-weight:500;line-height:1.4;white-space:nowrap">${t.label}</span>`).join('');

  // 4 指标横向（优先 Tushare 精算字段，无则降级到收益+评分）
  const metrics=[];
  if(f.sharpe!=null) metrics.push({l:'夏普', v:f.sharpe.toFixed(2), c: f.sharpe>=1.2?'#86EFAC':f.sharpe>=0.8?'#FBBF24':'#9AA1AC'});
  if(f.max_drawdown!=null) metrics.push({l:'最大回撤', v:`${f.max_drawdown}%`, c:'#FBBF24'});
  if(f.scale_billion!=null) metrics.push({l:'规模', v:`${f.scale_billion>=100?Math.round(f.scale_billion)+'亿':f.scale_billion+'亿'}`, c:''});
  if(f.fee!=null) metrics.push({l:'费率', v:`${f.fee}%`, c:''});
  // v9.5.56: 兜底字段（快速估算模式可用）
  if(metrics.length<4 && f.return_1y!=null){
    const c1y = f.return_1y>=15?'#86EFAC':f.return_1y>=0?'#FBBF24':'#FCA5A5';
    metrics.push({l:'近1年', v:`${f.return_1y>0?'+':''}${f.return_1y}%`, c:c1y});
  }
  if(metrics.length<4 && f.ann_ret_3y!=null && f.ann_ret_5y!=null){
    // 5年化已显示为大字，这里补 3 年化
    metrics.push({l:'3年化', v:`${f.ann_ret_3y>0?'+':''}${f.ann_ret_3y}%`, c:f.ann_ret_3y>=8?'#86EFAC':''});
  }
  if(metrics.length<4 && f.consistency_pct!=null){
    metrics.push({l:'稳定性', v:`${f.consistency_pct}%`, c:f.consistency_pct>=70?'#86EFAC':''});
  }
  if(metrics.length<4 && f.longterm_score!=null){
    metrics.push({l:'长持评分', v:`${f.longterm_score}`, c:f.longterm_score>=70?'#86EFAC':'#FBBF24'});
  }
  if(metrics.length<4 && (f.age_years||(f.age_label ? parseFloat(f.age_label.match(/(\d+\.?\d*)/)?.[1])||0 : 0))){
    const yrs = f.age_years || parseFloat(f.age_label.match(/(\d+\.?\d*)/)?.[1])||0;
    metrics.push({l:'成立', v:`${yrs.toFixed(1)}年`, c:''});
  }
  const metricsHtml = metrics.length>=2 ? `<div style="margin-top:10px;padding:8px 10px;background:rgba(255,255,255,.03);border-radius:6px;display:flex;justify-content:space-between;align-items:center">${
    metrics.slice(0,4).map((m,idx,arr)=>`<div style="text-align:center;flex:1${idx<arr.length-1?';border-right:1px solid rgba(255,255,255,.06)':''}"><div style="font-size:11px;font-weight:700${m.c?';color:'+m.c:''}">${m.v}</div><div style="font-size:9px;color:#7A8499;margin-top:1px">${m.l}</div></div>`).join('')
  }</div>` : '';

  // 复利预期（v9.5.57 三档：保守/中性/乐观，避免黄金等高波动品种线性外推）
  const skipCompound = /纯债|信用债|国债|货币|现金/.test(f.name||''); // 仅彻底跳过债券/货基
  const compound = skipCompound ? null : _ltCompoundEstimate(annRet, f.name);
  const compoundHtml = compound ? `<div style="margin-top:8px;padding:8px 10px;background:rgba(99,102,241,.08);border-radius:6px">
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
      <span style="font-size:13px">📈</span>
      <span style="font-size:11px;color:#A5B4FC;font-weight:600">月投 ¥2000 · 15 年 · 三档预期</span>
      <span style="font-size:9px;color:#7A8499;margin-left:auto" title="参考类别长期均值${compound.longAvg}%">类别基准 ${compound.longAvg}%</span>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px">
      <div style="text-align:center;padding:6px 4px;background:rgba(239,68,68,.06);border-radius:4px">
        <div style="font-size:9px;color:#FCA5A5">保守 ${compound.conservative.rate}%</div>
        <div style="font-size:12px;font-weight:700;color:#FCA5A5;margin-top:2px">¥${(compound.conservative.fv/10000).toFixed(0)}万</div>
      </div>
      <div style="text-align:center;padding:6px 4px;background:rgba(245,158,11,.08);border-radius:4px">
        <div style="font-size:9px;color:#FBBF24">中性 ${compound.neutral.rate}%</div>
        <div style="font-size:13px;font-weight:800;color:#FBBF24;margin-top:2px">¥${(compound.neutral.fv/10000).toFixed(0)}万</div>
      </div>
      <div style="text-align:center;padding:6px 4px;background:rgba(34,197,94,.08);border-radius:4px">
        <div style="font-size:9px;color:#86EFAC">乐观 ${compound.optimistic.rate}%</div>
        <div style="font-size:12px;font-weight:700;color:#86EFAC;margin-top:2px">¥${(compound.optimistic.fv/10000).toFixed(0)}万</div>
      </div>
    </div>
    <div style="font-size:9px;color:#7A8499;margin-top:4px;text-align:center">本金 ¥${(compound.invested/10000).toFixed(0)} 万 · 仅基于历史推算，实际波动较大</div>
  </div>` : '';

  // v9.5.76: 再平衡缺口方向提示 + 持仓关联
  const ltGapHtml = f.gap_match&&f.gap_hint ? `<div style="font-size:11px;padding:4px 8px;background:rgba(34,197,94,.12);border-radius:6px;margin-top:6px;color:#86EFAC">${f.gap_hint}</div>` : '';
  const ltRelHtml = f.holding_relation&&f.holding_relation!=='🟢 新敞口'&&f.holding_hint ? `<div style="font-size:11px;padding:4px 8px;background:${f.holding_relation==='🔵 已持仓'?'rgba(59,130,246,.15)':'rgba(234,179,8,.1)'};border-radius:6px;margin-top:4px;color:${f.holding_relation==='🔵 已持仓'?'#93C5FD':'#FDE68A'}">${f.holding_relation} ${f.holding_hint}</div>` : '';

  // AI 评论（用 industry_desc）
  const aiHtml = f.industry_desc ? `<div style="margin-top:8px;padding:6px 10px;background:rgba(99,102,241,.08);border-radius:6px;font-size:11px;color:#A5B4FC;line-height:1.5">🤖 ${f.industry_desc}</div>` : '';

  // 按钮组
  const wished = typeof _isWished==='function' ? _isWished(f.code) : false;
  const compareSet = window._compareSet || new Set();
  const inCmp = compareSet.has(f.code);

  html += `<div style="padding:14px 0;border-bottom:1px solid rgba(148,163,184,.06);cursor:pointer" onclick="typeof showFundDetailModal==='function'&&showFundDetailModal('${f.code}','${(f.name||'').replace(/'/g,'')}')">
    <!-- 行1: 序号 + 评级圆 + 名字/标签 + 年化大字 -->
    <div style="display:flex;align-items:flex-start;gap:10px">
      <span style="font-size:11px;color:#7A8499;font-weight:700;flex-shrink:0;width:14px;text-align:center;line-height:46px">${i+1}</span>
      <div style="width:46px;height:46px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;flex-shrink:0;border:2px solid ${grade.border};color:${grade.color};background:${grade.bg}">
        <span style="font-size:16px;font-weight:800;line-height:1">${grade.label}</span>
        <span style="font-size:7px;line-height:1;margin-top:2px;opacity:0.8">长持</span>
      </div>
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;font-weight:600;line-height:1.35;color:var(--text-primary,#F0F2F7);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-all">${f.name||f.code} <span style="font-size:11px;color:#7A8499;font-weight:400">${f.code}</span></div>
        ${tagsHtml ? `<div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:4px">${tagsHtml}</div>` : ''}
      </div>
      <div style="text-align:right;flex-shrink:0;line-height:1">
        <div style="font-size:17px;font-weight:800;color:${annColor}">${annRet!=null?(annRet>0?'+':'')+annRet+'%':'—'}</div>
        <div style="font-size:9px;color:#7A8499;margin-top:3px">${annLabel||'年化'}</div>
      </div>
    </div>
    ${metricsHtml}
    ${ltGapHtml}${ltRelHtml}
    ${compoundHtml}
    ${aiHtml}
    <!-- 操作按钮 -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;padding-left:70px;gap:8px">
      <span style="font-size:10px;color:#7A8499;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${f.invest_type||''}${f.age_label?' · '+f.age_label:''}</span>
      <div style="display:flex;gap:4px;flex-shrink:0;align-items:center" onclick="event.stopPropagation()">
        ${typeof _toggleWish==='function'?`<button onclick="_toggleWish('${f.code}','${(f.name||'').replace(/'/g,'')}')" style="padding:2px 5px;font-size:13px;border:none;background:transparent;cursor:pointer" title="${wished?'从心愿单移除':'加入心愿单'}">${wished?'❤️':'🤍'}</button>`:''}
        ${typeof _toggleCompare==='function'?`<button onclick="_toggleCompare('${f.code}','${(f.name||'').replace(/'/g,'')}')" style="padding:2px 7px;font-size:10px;font-weight:600;border:1px solid ${inCmp?'#818CF8':'rgba(148,163,184,.3)'};border-radius:4px;background:${inCmp?'rgba(99,102,241,.18)':'transparent'};color:${inCmp?'#818CF8':'#9aa1ac'};cursor:pointer">${inCmp?'✓':'+'}</button>`:''}
        ${typeof _showFundKlineModal==='function'?`<button onclick="_showFundKlineModal('${f.code}','${(f.name||'').replace(/'/g,'')}')" style="padding:2px 8px;font-size:10px;border:1px solid rgba(148,163,184,.3);border-radius:4px;background:transparent;color:#9aa1ac;cursor:pointer">📈 K线</button>`:''}
      </div>
    </div>
  </div>`;
});

html+=`<div style="margin-top:12px;padding:10px;background:rgba(99,102,241,.06);border-radius:10px;font-size:11px;color:var(--text2)">
💡 长持评级：A+ ≥80分 · A ≥70 · B+ ≥60 · B ≥50 · C <50<br>
评分 = 3年年化收益(50%) + 稳定性(30%) + 一致性(20%) + 成立年限加分<br>
复利预期按当前年化推算（仅参考，实际收益受市场波动影响）
</div>`;
el.innerHTML=html;
if(typeof window._prefetchLongtermFundDetails==='function'){
  setTimeout(()=>window._prefetchLongtermFundDetails(funds.slice(0,3)),120);
}
}

async function loadLtStocks(force=false){
const el=document.getElementById('ltContent');if(!el)return;
if(!force&&_ltStockData){renderLtStockList(_ltStockData);return;}
if(force)_ltStockData=null;
// v9.9.2: 股票分析支持明确失败提示与强制刷新
el.innerHTML=`<div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>${force?'强制刷新长持股票分析，正在跳过缓存拉取最新结果...':'加载长持股票分析...'}</div>`;
try{
const r=await fetch(_ltBuildUrl('/longterm/stocks', force),{signal:AbortSignal.timeout(force?240000:180000)});
let d=null;
try{d=await r.json();}catch(_err){}
if(!r.ok)throw new Error((d&&(d.detail||d.error||d.message))||`HTTP ${r.status}`);
if(d&&d.error)throw new Error(d.error);
_ltStockData=d;
renderLtStockList(d);
}catch(e){el.innerHTML=_ltRenderErrorCard('股票',_ltDescribeLoadError(e,'长持股票'),'loadLtStocks()','loadLtStocks(true)','/api/longterm/stocks',force)}}

// v9.5.54: 股票护城河标签生成
function _buildLtStockTags(s){
  const tags=[];
  // 1. 行业
  if(s.industry) tags.push({label:`${_ltIndustryIcon(s.industry)} ${s.industry}`, color:'#A5B4FC', bg:'rgba(99,102,241,.18)'});
  // 2. 高 ROE
  if(s.avg_roe!=null && s.avg_roe>=20) tags.push({label:`⭐ ROE ${s.avg_roe}%`, color:'#FBBF24', bg:'rgba(245,158,11,.18)'});
  else if(s.avg_roe!=null && s.avg_roe>=15) tags.push({label:`💎 ROE ${s.avg_roe}%`, color:'#86EFAC', bg:'rgba(34,197,94,.15)'});
  // 3. 低负债
  if(s.avg_debt!=null && s.avg_debt<40) tags.push({label:`🛡️ 低负债`, color:'#86EFAC', bg:'rgba(34,197,94,.15)'});
  // 4. 高毛利
  if(s.avg_gpm!=null && s.avg_gpm>=50) tags.push({label:`🏆 高毛利`, color:'#FBBF24', bg:'rgba(245,158,11,.15)'});
  // 5. 高成长
  if(s.avg_np_growth!=null && s.avg_np_growth>=20) tags.push({label:`🚀 成长 +${s.avg_np_growth}%`, color:'#F9A8D4', bg:'rgba(244,114,182,.15)'});
  return tags.slice(0,4);
}

function renderLtStockList(d){
const el=document.getElementById('ltContent');if(!el)return;
const stocks=d.stocks||[];
if(!stocks.length){el.innerHTML=`<div style="text-align:center;padding:30px;color:var(--text2)">
<div style="font-size:16px;margin-bottom:8px">📊</div>
股票数据首次加载需约1-2分钟<br><span style="font-size:11px">分析近3年ROE连续性+净利润增速+负债率</span><br>
<button onclick="loadLtStocks(true)" style="margin-top:12px;padding:8px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">⚙️ 开始分析</button>
</div>`;return;}
const genDate=(d.generated_at||'').slice(0,10);
let html=`<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:11px;color:var(--text2);margin-bottom:8px"><span>筛选条件：近3年平均ROE≥12% · 最差年份ROE≥8% · 负债率≤65% · 更新：${genDate} · 90天缓存</span><span style="display:flex;gap:6px;margin-left:auto"><button onclick="loadLtStocks()" style="padding:2px 8px;border-radius:4px;border:none;background:var(--bg3);color:var(--text2);font-size:10px;cursor:pointer">🔄 刷新</button><button onclick="loadLtStocks(true)" style="padding:2px 8px;border-radius:4px;border:1px solid rgba(99,102,241,.25);background:rgba(99,102,241,.12);color:#818CF8;font-size:10px;cursor:pointer">⚡ 强制刷新</button></span></div>`;

stocks.slice(0,20).forEach((s,i)=>{
  const grade = _ltGrade(s.longterm_score||0);
  // 主要指标 = ROE
  const roeColor = s.avg_roe>=20?'#86EFAC':s.avg_roe>=15?'#FBBF24':'#9AA1AC';

  const tags = _buildLtStockTags(s);
  const tagsHtml = tags.map(t=>`<span style="display:inline-flex;align-items:center;font-size:10px;padding:2px 7px;border-radius:4px;background:${t.bg};color:${t.color};font-weight:500;line-height:1.4;white-space:nowrap">${t.label}</span>`).join('');

  // 4 指标横向（ROE/净利增速/负债/毛利）
  const metrics=[];
  if(s.avg_roe!=null) metrics.push({l:'平均ROE', v:`${s.avg_roe}%`, c: s.avg_roe>=20?'#86EFAC':s.avg_roe>=15?'#FBBF24':''});
  if(s.avg_np_growth!=null) metrics.push({l:'净利增速', v:`${s.avg_np_growth>0?'+':''}${s.avg_np_growth}%`, c: s.avg_np_growth>=15?'#86EFAC':''});
  if(s.avg_debt!=null) metrics.push({l:'负债率', v:`${s.avg_debt}%`, c: s.avg_debt<40?'#86EFAC':s.avg_debt<55?'#FBBF24':'#FCA5A5'});
  if(s.avg_gpm!=null) metrics.push({l:'毛利率', v:`${s.avg_gpm}%`, c: s.avg_gpm>=50?'#FBBF24':''});
  const metricsHtml = metrics.length ? `<div style="margin-top:10px;padding:8px 10px;background:rgba(255,255,255,.03);border-radius:6px;display:flex;justify-content:space-between;align-items:center">${
    metrics.slice(0,4).map((m,idx,arr)=>`<div style="text-align:center;flex:1${idx<arr.length-1?';border-right:1px solid rgba(255,255,255,.06)':''}"><div style="font-size:11px;font-weight:700${m.c?';color:'+m.c:''}">${m.v}</div><div style="font-size:9px;color:#7A8499;margin-top:1px">${m.l}</div></div>`).join('')
  }</div>` : '';

  // 护城河描述
  let moatDesc = '';
  if(s.avg_roe>=20 && s.avg_debt<40) moatDesc = '🏰 高ROE+低负债，定价权强、抗风险能力突出';
  else if(s.avg_roe>=15 && s.avg_np_growth>=15) moatDesc = '🚀 ROE稳定+成长性好，质量与扩张兼具';
  else if(s.avg_gpm>=50) moatDesc = '🏆 高毛利印证产品溢价，行业护城河深';
  else if(s.avg_roe>=12) moatDesc = '✅ ROE 持续达标，业务稳健';
  const moatHtml = moatDesc ? `<div style="margin-top:8px;padding:6px 10px;background:rgba(99,102,241,.08);border-radius:6px;font-size:11px;color:#A5B4FC;line-height:1.5">${moatDesc}${s.note?' · '+s.note:''}</div>` : (s.note ? `<div style="margin-top:8px;padding:6px 10px;background:rgba(99,102,241,.08);border-radius:6px;font-size:11px;color:#A5B4FC;line-height:1.5">🤖 ${s.note}</div>` : '');

  const cleanCode = (s.code||'').replace(/^(sh|sz)/i,'');

  html += `<div style="padding:14px 0;border-bottom:1px solid rgba(148,163,184,.06);cursor:pointer" onclick="typeof showStockDetailModal==='function'&&showStockDetailModal(${JSON.stringify(s).replace(/"/g,'&quot;')})">
    <!-- 行1: 序号 + 评级圆 + 名字/标签 + ROE 大字 -->
    <div style="display:flex;align-items:flex-start;gap:10px">
      <span style="font-size:11px;color:#7A8499;font-weight:700;flex-shrink:0;width:14px;text-align:center;line-height:46px">${i+1}</span>
      <div style="width:46px;height:46px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;flex-shrink:0;border:2px solid ${grade.border};color:${grade.color};background:${grade.bg}">
        <span style="font-size:16px;font-weight:800;line-height:1">${grade.label}</span>
        <span style="font-size:7px;line-height:1;margin-top:2px;opacity:0.8">护城河</span>
      </div>
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;font-weight:600;line-height:1.35;color:var(--text-primary,#F0F2F7);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-all">${s.name||s.code} <span style="font-size:11px;color:#7A8499;font-weight:400">${cleanCode}</span></div>
        ${tagsHtml ? `<div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:4px">${tagsHtml}</div>` : ''}
      </div>
      <div style="text-align:right;flex-shrink:0;line-height:1">
        <div style="font-size:17px;font-weight:800;color:${roeColor}">${s.avg_roe!=null?s.avg_roe+'%':'—'}</div>
        <div style="font-size:9px;color:#7A8499;margin-top:3px">3年均ROE</div>
      </div>
    </div>
    ${metricsHtml}
    ${moatHtml}
    <div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;padding-left:70px;gap:8px">
      <span style="font-size:10px;color:#7A8499;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.market||''}${s.code?' · '+s.code:''}</span>
      <div style="display:flex;gap:4px;flex-shrink:0;align-items:center" onclick="event.stopPropagation()">
        ${typeof _toggleStockWish==='function'?`<button onclick="_toggleStockWish('${s.code}','${(s.name||'').replace(/'/g,'')}')" style="padding:2px 5px;font-size:13px;border:none;background:transparent;cursor:pointer">🤍</button>`:''}
        ${typeof showFundChart==='function'?`<button onclick="showFundChart('${cleanCode}')" style="padding:2px 8px;font-size:10px;border:1px solid rgba(148,163,184,.3);border-radius:4px;background:transparent;color:#9aa1ac;cursor:pointer">📈 K线</button>`:''}
      </div>
    </div>
  </div>`;
});

html+=`<div style="margin-top:12px;padding:10px;background:rgba(99,102,241,.06);border-radius:10px;font-size:11px;color:var(--text2)">
💡 护城河评级：A+ ≥80分 · A ≥70 · B+ ≥60 · B ≥50 · C <50<br>
评分 = ROE稳定性40% + 净利增速30% + 低负债20% + 高毛利10%<br>
ROE连续高且稳定 = 定价权强 · 低负债 = 抗风险能力强</div>`;
el.innerHTML=html;}

function renderLtCalc(){
const el=document.getElementById('ltContent');if(!el)return;
el.innerHTML=`<div style="padding:4px 0">
<div style="font-size:13px;font-weight:700;margin-bottom:12px">🧮 复利计算器</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:12px">月定投+复利，直观看清长期财富效应</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
<div>
  <div style="font-size:11px;color:var(--text2);margin-bottom:4px">月定投金额（元）</div>
  <input id="ltMonthly" type="number" value="2000" step="100" style="width:100%;padding:10px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px;box-sizing:border-box" oninput="calcCompound()">
</div>
<div>
  <div style="font-size:11px;color:var(--text2);margin-bottom:4px">投资年限（年）</div>
  <input id="ltYears" type="number" value="15" min="1" max="50" style="width:100%;padding:10px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px;box-sizing:border-box" oninput="calcCompound()">
</div>
</div>
<div style="margin-bottom:12px">
  <div style="font-size:11px;color:var(--text2);margin-bottom:6px">预期年化收益率</div>
  <div style="display:flex;gap:8px;flex-wrap:wrap">
  ${[5,8,10,12,15].map(r=>`<button class="section-tab ${r===10?'active':''}" id="ltRate${r}" onclick="selectRate(${r})" style="font-size:11px;padding:5px 12px">${r}%</button>`).join('')}
  <input id="ltCustomRate" type="number" placeholder="自定义%" min="1" max="50" style="width:72px;padding:5px 8px;border-radius:8px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:12px" oninput="selectRate(null,this.value)">
  </div>
</div>
<div id="ltCalcResult" style="padding:16px;background:var(--bg2);border-radius:12px"></div>
<div style="margin-top:12px;font-size:11px;color:var(--text2)">
💡 分阶段对比：
<div id="ltCalcTable" style="margin-top:8px"></div>
</div>
</div>`;
_ltRate=10;calcCompound();}

let _ltRate=10;
function selectRate(r,custom){
if(r!==null){_ltRate=r;[5,8,10,12,15].forEach(v=>{const btn=document.getElementById('ltRate'+v);if(btn)btn.classList.toggle('active',v===r)})}
else if(custom){_ltRate=parseFloat(custom)||10}
calcCompound();}

function calcCompound(){
const monthly=parseFloat(document.getElementById('ltMonthly')?.value)||2000;
const years=parseFloat(document.getElementById('ltYears')?.value)||15;
const rate=_ltRate/100;
const n=years*12;
const r=rate/12;
// FV = PMT × ((1+r)^n - 1) / r
const fv=r>0?monthly*((Math.pow(1+r,n)-1)/r):monthly*n;
const totalInvested=monthly*n;
const profit=fv-totalInvested;
const profitPct=((fv/totalInvested-1)*100).toFixed(1);
const fmtW=v=>v>=10000?'¥'+Math.round(v/10000)+'万':'¥'+Math.round(v).toLocaleString();
const res=document.getElementById('ltCalcResult');
if(res){res.innerHTML=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;text-align:center">
<div><div style="font-size:28px;font-weight:900;color:var(--green)">${fmtW(fv)}</div><div style="font-size:11px;color:var(--text2)">最终资产</div></div>
<div><div style="font-size:28px;font-weight:900;color:var(--accent)">${fmtW(profit)}</div><div style="font-size:11px;color:var(--text2)">收益 (+${profitPct}%)</div></div>
<div><div style="font-size:18px;font-weight:700">${fmtW(totalInvested)}</div><div style="font-size:11px;color:var(--text2)">累计投入</div></div>
<div><div style="font-size:18px;font-weight:700;color:var(--accent)">${(_ltRate)}% 年化</div><div style="font-size:11px;color:var(--text2)">预期收益率</div></div>
</div>`;}
// 阶段表
const tableEl=document.getElementById('ltCalcTable');
if(tableEl){
let thtml='<table style="width:100%;border-collapse:collapse;font-size:11px">';
thtml+='<tr style="color:var(--text2)"><th style="text-align:left;padding:3px 0">年限</th><th style="text-align:right">投入</th><th style="text-align:right">资产</th><th style="text-align:right">翻倍</th></tr>';
[3,5,10,15,20,25,30].filter(y=>y<=years+1).forEach(y=>{
const ni=y*12;const fvi=r>0?monthly*((Math.pow(1+r,ni)-1)/r):monthly*ni;
const inv=monthly*ni;const mult=(fvi/inv).toFixed(1);
thtml+=`<tr><td style="padding:3px 0">${y}年</td><td style="text-align:right">${fmtW(inv)}</td><td style="text-align:right;color:var(--green)">${fmtW(fvi)}</td><td style="text-align:right;color:var(--accent)">${mult}x</td></tr>`;});
thtml+='</table>';
tableEl.innerHTML=thtml;}}

