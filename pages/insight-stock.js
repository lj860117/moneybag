// ============================================================
// insight-stock.js — 股票选股模块（从 insight.js 拆分，v9.5.48 E1）
// 函数依赖：必须在 app.js 之后、insight.js 之前加载
// ============================================================

// v9.5.114: 涨跌幅时点标签 — 周末/节假日/盘前盘后避免误标"今日"
function _lastTradeDayLabel(){
  const now = new Date();
  const w = now.getDay(); // 0=Sunday, 6=Saturday
  const h = now.getHours();
  const m = now.getMinutes();
  // 周六周日 → 上周五
  if(w===0) return '上周五';
  if(w===6) return '昨日(周五)';
  // 周一 9:30 前 → 上周五
  if(w===1 && (h<9 || (h===9 && m<30))) return '上周五';
  // 周二~周五 9:30 前 → 昨日
  if(h<9 || (h===9 && m<30)) return '昨日';
  // 9:30~15:00 → 实时(今日)
  if((h===9 && m>=30) || (h>=10 && h<15)) return '实时';
  // 15:00 后 → 今日收盘
  return '今日';
}

// v9.5.42 选股六件套（F3/F4/F5/F7/F8/F9）
// ==========================================================================
// F8 股票心愿单（localStorage 按用户隔离）
function _stockWishlist(){try{return JSON.parse(localStorage.getItem(_uk('moneybag_stock_wishlist')))||[]}catch{return[]}}
function _stockWishSave(arr){try{localStorage.setItem(_uk('moneybag_stock_wishlist'),JSON.stringify(arr.slice(0,50)))}catch{}}
window._toggleStockWish = function(code, name){
  const arr = _stockWishlist();
  const idx = arr.findIndex(x=>x.code===code);
  if(idx>=0){ arr.splice(idx,1); _stockWishSave(arr); }
  else { arr.unshift({code,name,t:Date.now()}); _stockWishSave(arr); }
  if(window._lastStockData) _fillStockList(window._lastStockData);
};

// F4 股票对比（最多 3 只）
if(!window._stockCompareSet) window._stockCompareSet = new Set();
if(!window._stockCompareData) window._stockCompareData = {};
window._toggleStockCompare = function(code, name){
  if(window._stockCompareSet.has(code)){
    window._stockCompareSet.delete(code);
    delete window._stockCompareData[code];
  } else {
    if(window._stockCompareSet.size >= 3){ alert('最多对比 3 只，请先取消一只'); return; }
    window._stockCompareSet.add(code);
    const stockData = window._lastStockData || {};
    const stock = (stockData?.stocks||[]).find(s=>s.code===code);
    window._stockCompareData[code] = stock || {code, name, scores:{}};
  }
  _renderStockCompareBar();
  if(window._lastStockData) _fillStockList(window._lastStockData);
};
window._clearStockCompare = function(){
  window._stockCompareSet.clear();
  window._stockCompareData = {};
  _renderStockCompareBar();
  if(window._lastStockData) _fillStockList(window._lastStockData);
};
function _renderStockCompareBar(){
  let bar = document.getElementById('stockCompareBar');
  if(!bar){
    bar = document.createElement('div');
    bar.id = 'stockCompareBar';
    bar.style.cssText = 'position:fixed;left:12px;right:12px;bottom:72px;z-index:200;background:rgba(99,102,241,.22);backdrop-filter:blur(10px);border:1px solid rgba(99,102,241,.4);border-radius:10px;padding:10px 14px;display:flex;align-items:center;gap:10px;box-shadow:0 4px 16px rgba(0,0,0,.3)';
    document.body.appendChild(bar);
  }
  const n = window._stockCompareSet.size;
  if(n === 0){ bar.remove(); return; }
  bar.innerHTML = `<span style="font-size:11px;color:#C7D2FE;flex:1">✓ 已选 ${n} 只股票${n>=2?'':'（至少选 2 只）'}</span>
    <button onclick="_showStockCompareModal()" style="padding:5px 12px;font-size:11px;border:1px solid #818CF8;border-radius:6px;background:${n>=2?'#818CF8':'rgba(99,102,241,.25)'};color:${n>=2?'#fff':'#9CA3AF'};cursor:${n>=2?'pointer':'not-allowed'};font-weight:600" ${n<2?'disabled':''}>📊 对比</button>
    <button onclick="_clearStockCompare()" style="padding:5px 10px;font-size:11px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:transparent;color:#9aa1ac;cursor:pointer">清空</button>`;
}
window._showStockCompareModal = function(){
  const codes = Array.from(window._stockCompareSet);
  if(codes.length < 2){ alert('请至少选 2 只股票'); return; }
  const stocks = codes.map(c=>window._stockCompareData[c]).filter(Boolean);
  // 找各列最佳
  const peVals = stocks.map(s=>s.pe).filter(v=>v!=null && v>0);
  const pbVals = stocks.map(s=>s.pb).filter(v=>v!=null && v>0);
  const roeVals = stocks.map(s=>s.roe).filter(v=>v!=null);
  const gmVals = stocks.map(s=>s.gross_margin).filter(v=>v!=null);
  const nmVals = stocks.map(s=>s.net_margin).filter(v=>v!=null);
  const drVals = stocks.map(s=>s.debt_ratio).filter(v=>v!=null);
  const rgVals = stocks.map(s=>s.revenue_growth).filter(v=>v!=null);
  const chgVals = stocks.map(s=>s.change_pct).filter(v=>v!=null);
  const scoreVals = stocks.map(s=>s.score).filter(v=>v!=null);
  // PE/PB/负债率：越低越好
  const bestPE = peVals.length ? Math.min(...peVals) : null;
  const bestPB = pbVals.length ? Math.min(...pbVals) : null;
  const bestDR = drVals.length ? Math.min(...drVals) : null;
  // ROE/毛利/净利/营收增速/涨幅/评分：越高越好
  const bestROE = roeVals.length ? Math.max(...roeVals) : null;
  const bestGM = gmVals.length ? Math.max(...gmVals) : null;
  const bestNM = nmVals.length ? Math.max(...nmVals) : null;
  const bestRG = rgVals.length ? Math.max(...rgVals) : null;
  const bestCHG = chgVals.length ? Math.max(...chgVals) : null;
  const bestScore = scoreVals.length ? Math.max(...scoreVals) : null;

  const rowsHtml = (label, getter, fmt, bestVal) => {
    return `<tr style="border-top:1px solid rgba(148,163,184,.08)">
      <td style="padding:8px 6px;color:var(--text-tertiary,#7A8499);font-size:11px;white-space:nowrap">${label}</td>
      ${stocks.map(s=>{
        const v = getter(s);
        const isBest = v != null && bestVal != null && v === bestVal;
        return `<td style="text-align:right;padding:8px 6px;font-size:12px;font-weight:${isBest?'700':'400'};color:${isBest?'#FFB755':'var(--text-default,#D8DCE5)'}">${fmt(v)}${isBest?' 🏆':''}</td>`;
      }).join('')}
    </tr>`;
  };
  const headHtml = `<tr>
    <td style="padding:8px 6px;width:60px"></td>
    ${stocks.map(s=>{
      const fullName = s.name || s.code;
      return `<td style="text-align:right;padding:8px 6px;font-size:11px;font-weight:600;color:var(--text-default,#D8DCE5);line-height:1.3;vertical-align:bottom" title="${fullName}">
        <div style="word-break:break-all;white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;max-height:2.6em">${fullName}</div>
      </td>`;
    }).join('')}
  </tr>`;

  const o = document.createElement('div');
  o.className = 'modal-overlay';
  o.onclick = e => { if(e.target===o) o.remove(); };
  o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:85vh;display:flex;flex-direction:column">
    <div class="modal-handle"></div>
    <div class="modal-title">📊 股票对比 (${stocks.length} 只)</div>
    <div style="flex:1;overflow-y:auto;padding:0 4px">
      <table style="width:100%;border-collapse:collapse;table-layout:fixed">
        <thead>${headHtml}</thead>
        <tbody>
          ${rowsHtml('代码', s=>s.code?s.code.replace(/^(sh|sz)/i,''):'—', v=>v||'—', null)}
          ${rowsHtml('行业', s=>s.industry, v=>v||'—', null)}
          ${rowsHtml('风险', s=>s.risk_level, v=>({low:'🟢 低',mid:'🟡 中',high:'🔴 高'})[v]||'—', null)}
          ${rowsHtml('涨跌', s=>s.change_pct, v=>v!=null?(v>0?'+':'')+Number(v).toFixed(2)+'%':'—', bestCHG)}
          ${rowsHtml('PE', s=>s.pe, v=>v!=null?Number(v).toFixed(1):'—', bestPE)}
          ${rowsHtml('PB', s=>s.pb, v=>v!=null?Number(v).toFixed(2):'—', bestPB)}
          ${rowsHtml('ROE', s=>s.roe, v=>v!=null?Number(v).toFixed(1)+'%':'—', bestROE)}
          ${rowsHtml('毛利率', s=>s.gross_margin, v=>v!=null?Number(v).toFixed(1)+'%':'—', bestGM)}
          ${rowsHtml('净利率', s=>s.net_margin, v=>v!=null?Number(v).toFixed(1)+'%':'—', bestNM)}
          ${rowsHtml('营收增速', s=>s.revenue_growth, v=>v!=null?(v>0?'+':'')+Number(v).toFixed(1)+'%':'—', bestRG)}
          ${rowsHtml('负债率', s=>s.debt_ratio, v=>v!=null?Number(v).toFixed(1)+'%':'—', bestDR)}
          ${rowsHtml('市值', s=>s.market_cap, v=>v!=null?Number(v).toFixed(0)+'亿':'—', null)}
          ${rowsHtml('评分', s=>s.score, v=>v!=null?Number(v).toFixed(1):'—', bestScore)}
        </tbody>
      </table>
      <div style="margin-top:12px;padding:10px 12px;background:rgba(99,102,241,.08);border-radius:8px;font-size:11px;color:#A5B4FC;line-height:1.6">
        💡 提示：🏆 标记代表该维度最优。<br>
        • PE/PB/负债率：越<b>低</b>越好（保留有效正值）<br>
        • ROE/毛利/净利/营收增速：越<b>高</b>越好<br>
        • 高 PE 不一定差 — 成长股估值偏高常态；低 ROE 警惕价值陷阱
      </div>
    </div>
    <div style="display:flex;gap:8px;margin-top:8px">
      <button class="mb-btn mb-btn--secondary" style="flex:1" onclick="document.querySelector('.modal-overlay')?.remove()">关闭</button>
      <button class="mb-btn mb-btn--primary" style="flex:1" onclick="_clearStockCompare();document.querySelector('.modal-overlay')?.remove()">清空选择</button>
    </div>
  </div>`;
  document.body.appendChild(o);
};

// F5 风险色块（基于 scores.risk，risk 高 = 风险低；现成数据反推）
function _stockRiskBadge(s){
  const r = s.scores?.risk;
  if(r==null) return '';
  let level, label, color;
  if(r>=80){ level='low'; label='🟢 低'; color='#10B981'; }
  else if(r>=55){ level='mid'; label='🟡 中'; color='#F59E0B'; }
  else { level='high'; label='🔴 高'; color='#F87171'; }
  s.risk_level = level;  // 便于对比表读取
  return `<span style="font-size:9px;padding:1px 5px;border-radius:3px;border:1px solid ${color};color:${color};white-space:nowrap;margin-left:4px">${label}</span>`;
}

// F9 拥挤度评分（前端简易版：基于 turnover 换手率 + 近期涨幅）
function _stockCrowdingBadge(s){
  const to = s.turnover;
  const chg = s.change_pct;
  // 换手率 > 8% 且单日大涨 > 5% → 高度拥挤；> 5% → 中度
  if(to==null) return '';
  if(to>=8 && chg!=null && chg>=5) return `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(248,113,113,.15);color:#F87171;margin-left:4px" title="换手率${to}% 涨幅${chg}% — 高度拥挤，警惕追高">🔥拥挤</span>`;
  if(to>=5) return `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(245,158,11,.12);color:#F59E0B;margin-left:4px" title="换手率${to}% — 资金活跃">📊活跃</span>`;
  return '';
}

// F7 事件标（持仓股复用现有 risk-events 接口，懒加载）
async function _enrichStockEvents(){
  // 取我的持仓股代码
  let holdingCodes = [];
  try{
    const userId = getProfileId();
    if(!userId) return;
    const r = await fetch(API_BASE+'/stock-holdings?userId='+userId, {signal:AbortSignal.timeout(8000)});
    if(!r.ok) return;
    const d = await r.json();
    holdingCodes = (d.holdings||[]).map(h=>h.symbol||h.code).filter(Boolean);
    window._myStockCodes = holdingCodes;  // 给"我的持仓"视图用
  }catch(e){ return; }
  if(!holdingCodes.length) return;
  // 调风险事件接口（如不存在则静默失败）
  try{
    const r = await fetch(API_BASE+'/risk-events?codes='+holdingCodes.join(','), {signal:AbortSignal.timeout(10000)});
    if(!r.ok) return;
    const d = await r.json();
    const eventMap = d.events||{};
    document.querySelectorAll('[data-stock-events]').forEach(el=>{
      const code = el.dataset.stockEvents;
      const evs = eventMap[code];
      if(!evs || !evs.length) return;
      const badges = evs.slice(0,3).map(e=>{
        const icon = e.type==='dividend'?'💰':e.type==='unlock'?'🔓':e.type==='reduce'?'⬇️':e.type==='announcement'?'📢':'⚠️';
        return `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(245,158,11,.12);color:#F59E0B;margin-left:3px" title="${e.text||e.type}">${icon}${e.label||''}</span>`;
      }).join('');
      el.innerHTML = badges;
    });
  }catch(e){ /* silent */ }
}

// F3 推荐理由（前端基于 scores 各维度生成自然语言句）
function _computeStockReason(s){
  if(s._reasonComputed) return s.reason;
  const sc = s.scores || {};
  const parts = [];
  if(sc.value>=80) parts.push('估值低位');
  else if(sc.value>=65) parts.push('估值合理');
  if(sc.quality>=80) parts.push('盈利质量强');
  else if(sc.quality>=70) parts.push('基本面稳健');
  if(sc.growth>=80) parts.push('成长性突出');
  else if(sc.growth>=70) parts.push('增长可期');
  if(sc.momentum>=80) parts.push('动量强劲');
  else if(sc.momentum>=70) parts.push('趋势向上');
  if(sc.risk>=85) parts.push('风险较低');
  else if(sc.risk<50) parts.push('波动较大');
  if(s.roe!=null && s.roe>20) parts.push(`ROE ${typeof s.roe==='number'?s.roe.toFixed(0):s.roe}%`);
  if(s.gross_margin!=null && s.gross_margin>60) parts.push(`毛利 ${typeof s.gross_margin==='number'?s.gross_margin.toFixed(0):s.gross_margin}%`);
  if(s.revenue_growth!=null && s.revenue_growth>30) parts.push(`营收 +${typeof s.revenue_growth==='number'?s.revenue_growth.toFixed(0):s.revenue_growth}%`);
  if(s.turnover!=null && s.turnover>=8 && s.change_pct!=null && s.change_pct>=5) parts.push('⚠️ 警惕追高');
  s._reasonComputed = true;
  s.reason = parts.length ? parts.slice(0,5).join(' · ') : '综合得分中等 — 待观察';
  return s.reason;
}

// F2 股票自定义筛选器 modal（5 维数值）
window._showStockFilterModal = function(){
  const f = window._stockFilter || {};
  const o = document.createElement('div');
  o.className = 'modal-overlay';
  o.onclick = e => { if(e.target===o) o.remove(); };
  o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:80vh;overflow-y:auto">
    <div class="modal-handle"></div>
    <div class="modal-title">⚙️ 自定义筛选 — 股票</div>
    <div style="font-size:11px;color:var(--text2);margin-bottom:14px">留空即不限制；筛选在当前 TOP 50 内进行</div>
    <div style="display:grid;grid-template-columns:auto 1fr;gap:10px 12px;font-size:12px;align-items:center">
      <span style="color:#9aa1ac">PE 上限</span><input id="flt_pe" type="number" step="1" placeholder="例：30" value="${f.pe_max??''}" style="padding:6px 8px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:rgba(15,23,42,.5);color:#fff;width:100%">
      <span style="color:#9aa1ac">PB 上限</span><input id="flt_pb" type="number" step="0.1" placeholder="例：5" value="${f.pb_max??''}" style="padding:6px 8px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:rgba(15,23,42,.5);color:#fff;width:100%">
      <span style="color:#9aa1ac">ROE ≥ (%)</span><input id="flt_roe" type="number" step="1" placeholder="例：15" value="${f.roe_min??''}" style="padding:6px 8px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:rgba(15,23,42,.5);color:#fff;width:100%">
      <span style="color:#9aa1ac">毛利率 ≥ (%)</span><input id="flt_gm" type="number" step="1" placeholder="例：40" value="${f.gm_min??''}" style="padding:6px 8px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:rgba(15,23,42,.5);color:#fff;width:100%">
      <span style="color:#9aa1ac">市值 ≥ (亿)</span><input id="flt_mcap" type="number" step="50" placeholder="例：100" value="${f.mcap_min??''}" style="padding:6px 8px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:rgba(15,23,42,.5);color:#fff;width:100%">
    </div>
    <div style="display:flex;gap:8px;margin-top:16px">
      <button class="mb-btn mb-btn--secondary" style="flex:1" onclick="window._stockFilter={pe_max:null,pb_max:null,roe_min:null,gm_min:null,mcap_min:null};document.querySelector('.modal-overlay')?.remove();if(window._lastStockData)_fillStockList(window._lastStockData);">清空</button>
      <button class="mb-btn mb-btn--primary" style="flex:1" onclick="(function(){const g=id=>{const v=document.getElementById(id).value.trim();return v===''?null:parseFloat(v);};window._stockFilter={pe_max:g('flt_pe'),pb_max:g('flt_pb'),roe_min:g('flt_roe'),gm_min:g('flt_gm'),mcap_min:g('flt_mcap')};document.querySelector('.modal-overlay')?.remove();if(window._lastStockData)_fillStockList(window._lastStockData);})()">应用</button>
    </div>
  </div>`;
  document.body.appendChild(o);
};

function _stockTagsHTML(s){
  const sc=s.scores||{};
  const tags=[];
  // v9.5.50 方案A: 升级股票标签云（颜色化分类）
  // 行业/赛道标签（绿色）
  const indTags=s.industry_tags||[];
  const indHtml = indTags.map(t=>`<span style="font-size:10px;padding:2px 7px;border-radius:4px;background:rgba(99,102,241,.18);color:#A5B4FC;font-weight:500;line-height:1.4;white-space:nowrap" title="行业/赛道">${t}</span>`).join('');
  // 因子优势标签（蓝紫色）
  if(sc.value>=70)tags.push({l:'💰 低估值', color:'#86EFAC', bg:'rgba(34,197,94,.15)', t:'PE/PB 处于低位'});
  if(sc.growth>=70)tags.push({l:'🚀 高成长', color:'#F9A8D4', bg:'rgba(244,114,182,.15)', t:'营收/利润成长性强'});
  if(sc.quality>=75)tags.push({l:'⭐ 高质量', color:'#FBBF24', bg:'rgba(245,158,11,.15)', t:'ROE/毛利/现金流优秀'});
  if(sc.momentum>=70)tags.push({l:'📈 强动量', color:'#FCA5A5', bg:'rgba(239,68,68,.15)', t:'近期涨势强'});
  if(sc.risk>=80)tags.push({l:'🛡️ 低风险', color:'#86EFAC', bg:'rgba(34,197,94,.15)', t:'波动率低'});
  if(s.roe&&s.roe>20)tags.push({l:'💎 ROE>20%', color:'#FBBF24', bg:'rgba(245,158,11,.15)', t:`ROE=${s.roe}%`});
  if(s.gross_margin&&s.gross_margin>50)tags.push({l:'🏆 高毛利', color:'#FBBF24', bg:'rgba(245,158,11,.15)', t:`毛利率=${s.gross_margin}%`});
  // 拥挤度警示
  if((s.turnover_rate||s.turnover||0)>=8 && Math.abs(s.change_pct||0)>=5){
    tags.push({l:'🔥 拥挤交易', color:'#FCA5A5', bg:'rgba(239,68,68,.15)', t:'换手率高+涨幅大，注意追高'});
  }
  const factorHtml = tags.slice(0,4).map(t=>`<span style="font-size:10px;padding:2px 7px;border-radius:4px;background:${t.bg};color:${t.color};font-weight:500;line-height:1.4;white-space:nowrap" title="${t.t}">${t.l}</span>`).join('');
  const policyBadges=_policyBadgesHTML(s.code?s.code.replace(/^(sh|sz)/i,''):'',s.name||'');

  let h='';
  if(indHtml || factorHtml || policyBadges){
    h+='<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:4px">'+indHtml+factorHtml+policyBadges+'</div>';
  }
  if(s.industry_insight)h+='<div style="font-size:11px;color:var(--text2);padding:2px 0;margin-bottom:2px">'+s.industry_insight+'</div>';
  // v9.5.81: 潜力评分（最顶部，比买入信号更醒目）
  if(s.potential){const pt=s.potential;const ptBg=pt.level==='high'?'rgba(83,74,183,.12)':'rgba(186,117,23,.08)';const ptColor=pt.level==='high'?'#AFA9EC':'#EF9F27';h+='<div style="font-size:12px;padding:5px 10px;background:'+ptBg+';border-radius:6px;margin-bottom:5px;color:'+ptColor+';font-weight:500">'+pt.label+'<span style="font-size:10px;margin-left:6px;opacity:0.7">'+pt.signal_flags+'</span>'+(pt.reason?'<div style="font-size:11px;margin-top:2px;font-weight:400;opacity:0.85">'+pt.reason+'</div>':'')+'</div>';}
  // v9.5.78: 综合买入信号（最重要，最先展示）
  if(s.price_signal&&s.price_signal.level!=='neutral'){const ps=s.price_signal;const psBg=ps.level==='strong_buy'?'rgba(34,197,94,.15)':ps.level==='buy'?'rgba(34,197,94,.08)':ps.level==='caution'?'rgba(234,179,8,.1)':'rgba(239,68,68,.08)';const psColor=ps.level==='strong_buy'?'#4ADE80':ps.level==='buy'?'#86EFAC':ps.level==='caution'?'#FDE68A':'#FCA5A5';h+='<div style="font-size:12px;padding:5px 10px;background:'+psBg+';border-radius:6px;margin-bottom:5px;color:'+psColor+';font-weight:500">'+ps.label+(ps.reason?' · '+ps.reason:'')+'</div>';}
  // v9.5.78: PE/PB历史百分位（好价格信号）
  if(s.pe_pct_label||s.pb_pct_label){const peP=s.pe_percentile;const pbP=s.pb_percentile;const peBg=peP!=null&&peP<=30?'rgba(34,197,94,.1)':peP!=null&&peP>=70?'rgba(239,68,68,.08)':'rgba(100,100,100,.06)';const peColor=peP!=null&&peP<=30?'#86EFAC':peP!=null&&peP>=70?'#FCA5A5':'#9CA3AF';let pLine='';if(s.pe_pct_label)pLine+='PE '+s.pe_pct_label;if(s.pb_pct_label)pLine+=(pLine?' | ':'')+'PB '+s.pb_pct_label;if(pLine)h+='<div style="font-size:11px;padding:4px 8px;background:'+peBg+';border-radius:6px;margin-bottom:4px;color:'+peColor+'">📊 估值历史位置 '+pLine+'</div>';}
  // v9.5.99: 业绩催化标签（业绩预增/快报/回购）
  if(Array.isArray(s.catalyst_flags)&&s.catalyst_flags.length){
    const bonus=s.catalyst_bonus||0;
    const catBg=bonus>=3?'rgba(34,197,94,.1)':bonus<=-3?'rgba(239,68,68,.08)':'rgba(100,100,100,.06)';
    const catColor=bonus>=3?'#86EFAC':bonus<=-3?'#FCA5A5':'#9CA3AF';
    h+='<div style="font-size:11px;padding:4px 8px;background:'+catBg+';border-radius:6px;margin-bottom:4px;color:'+catColor+'">⚡ '+s.catalyst_flags.join(' · ')+(bonus?' （'+(bonus>0?'+':'')+bonus+'分）':'')+'</div>';
  }
  // v9.5.102: 防御性过滤 — 跳过标题前缀和纯指标串（避免后端漏过滤展示给用户）
  if(s.aiComment){
    let cmt = String(s.aiComment).trim();
    const badPrefix = /^(逐只|下面|以下|以上|点评|分析|针对|对于|股票点评|每只)/;
    const isMetricOnly = /^(PE|PB|ROE|EPS|市值|毛利)[\s=:＝]/i.test(cmt) || /^[A-Za-z0-9.,%\s=:+\-]+$/.test(cmt);
    if(!badPrefix.test(cmt) && !isMetricOnly && cmt.length >= 4){
      h+='<div style="font-size:12px;color:#E0E7FF;padding:6px 10px;background:rgba(99,102,241,.08);border-radius:8px;line-height:1.5">🤖 '+cmt+'</div>';
    }
  }
  return h?'<div style="padding:4px 0 8px 34px;border-bottom:1px solid rgba(148,163,184,.04)">'+h+'</div>':'';
}

// v9.5.51 方案A: 股票标签云生成器（与基金 _buildFundTagPool 对称）
// 返回 [{kind, label, color, bg, title}] 按优先级排序
// kind 优先级：hold > industry > theme > policy > factor > event > risk
function _buildStockTagPool(s){
  const tags = [];
  const name = s.name || '';
  const sc = s.scores || {};

  // 1. 持仓关系
  if(s.stock_relation==='🔵 已持有'){
    tags.push({kind:'hold', label:'🔵 已持有', color:'#93C5FD', bg:'rgba(59,130,246,.18)', title:'你已持有此股票'});
  } else if(s.stock_relation==='🟡 同行业'){
    tags.push({kind:'hold', label:'🟡 同行业', color:'#FBBF24', bg:'rgba(245,158,11,.15)', title:s.stock_relation_hint||'你已有同行业持仓'});
  }

  // 2. 行业徽章（s.industry 或 industry_tags）
  const indPrimary = s.industry || (Array.isArray(s.industry_tags) && s.industry_tags[0]) || '';
  if(indPrimary){
    // 智能图标
    let icon = '📊';
    if(/白酒|食品|消费/.test(indPrimary)) icon = '🍷';
    else if(/半导体|芯片|集成电路/.test(indPrimary)) icon = '💎';
    else if(/银行|金融/.test(indPrimary)) icon = '🏦';
    else if(/医药|生物|医疗/.test(indPrimary)) icon = '💊';
    else if(/煤炭|石油|油气/.test(indPrimary)) icon = '⛽';
    else if(/新能源|光伏|风电|锂电/.test(indPrimary)) icon = '☀️';
    else if(/军工|防务|航空航天/.test(indPrimary)) icon = '🛡️';
    else if(/汽车|新能源车/.test(indPrimary)) icon = '🚗';
    else if(/通信|5G/.test(indPrimary)) icon = '📡';
    else if(/计算机|AI|软件/.test(indPrimary)) icon = '🤖';
    else if(/地产|建筑/.test(indPrimary)) icon = '🏘️';
    else if(/钢铁|有色|金属/.test(indPrimary)) icon = '⚙️';
    else if(/农业|食品|养殖/.test(indPrimary)) icon = '🌾';
    else if(/家电|轻工/.test(indPrimary)) icon = '🏠';
    else if(/电力|公用/.test(indPrimary)) icon = '⚡';
    else if(/传媒|游戏|互联网/.test(indPrimary)) icon = '🎮';
    tags.push({kind:'industry', label:`${icon} ${indPrimary}`, color:'#A5B4FC', bg:'rgba(99,102,241,.18)', title:'行业/板块'});
  }

  // 3. 因子优势（最多 2 个）
  const factorPool = [];
  if(sc.quality>=75) factorPool.push({label:'⭐ 高质量', color:'#FBBF24', bg:'rgba(245,158,11,.18)', title:`质量评分=${sc.quality}`, score:sc.quality});
  if(sc.value>=70) factorPool.push({label:'💰 低估值', color:'#86EFAC', bg:'rgba(34,197,94,.18)', title:`价值评分=${sc.value}`, score:sc.value});
  if(sc.growth>=70) factorPool.push({label:'🚀 高成长', color:'#F9A8D4', bg:'rgba(244,114,182,.18)', title:`成长评分=${sc.growth}`, score:sc.growth});
  if(sc.momentum>=70) factorPool.push({label:'📈 强动量', color:'#FCA5A5', bg:'rgba(239,68,68,.15)', title:`动量评分=${sc.momentum}`, score:sc.momentum});
  if(sc.risk>=80) factorPool.push({label:'🛡️ 低风险', color:'#86EFAC', bg:'rgba(34,197,94,.15)', title:`风险控制评分=${sc.risk}`, score:sc.risk});
  // 按 score 高到低取 Top 2
  factorPool.sort((a,b)=>b.score-a.score);
  factorPool.slice(0,2).forEach(f=>tags.push({kind:'factor', ...f}));

  // 4. 政策受益
  const policyTags = s.policy_tags || s.policies || [];
  if(Array.isArray(policyTags) && policyTags.length){
    tags.push({kind:'policy', label:`🏛️ ${policyTags[0]}`, color:'#D8B4FE', bg:'rgba(168,85,247,.15)', title:'政策利好：'+policyTags.join(',')});
  }

  // 5. 事件/警示标签
  if((s.turnover_rate||s.turnover||0)>=8 && Math.abs(s.change_pct||0)>=5){
    tags.push({kind:'event', label:'🔥 拥挤交易', color:'#FCA5A5', bg:'rgba(220,38,38,.2)', title:`换手率${(s.turnover_rate||s.turnover).toFixed(1)}%, 涨幅${Number(s.change_pct).toFixed(2)}%, 注意追高`});
  }
  if(s.change_pct!=null && s.change_pct>=9){
    tags.push({kind:'event', label:'🚀 涨停', color:'#FCA5A5', bg:'rgba(239,68,68,.18)', title:'今日涨停板'});
  }
  // v9.5.59 兜底事件：PE 极高/极低 → 显著估值标签
  if(s.pe!=null && s.pe<10 && s.pe>0){
    tags.push({kind:'event', label:'💰 PE极低', color:'#86EFAC', bg:'rgba(34,197,94,.15)', title:`PE=${Number(s.pe).toFixed(1)}, 估值便宜`});
  } else if(s.pe!=null && s.pe>100){
    tags.push({kind:'event', label:'⚠️ PE偏高', color:'#FCA5A5', bg:'rgba(239,68,68,.15)', title:`PE=${Number(s.pe).toFixed(1)}, 估值偏贵`});
  }
  // 大市值蓝筹标签
  if(s.market_cap!=null && s.market_cap>=5000 && !tags.some(t=>t.kind==='hold')){
    tags.push({kind:'theme', label:'🏛️ 大盘蓝筹', color:'#D8B4FE', bg:'rgba(168,85,247,.15)', title:`市值 ${s.market_cap>=10000?(s.market_cap/10000).toFixed(1)+'万亿':Math.round(s.market_cap)+'亿'}`});
  }

  // 优先级排序
  const priority = {hold:0, industry:1, theme:2, factor:3, policy:4, event:5, risk:6};
  tags.sort((a,b)=>(priority[a.kind]||9)-(priority[b.kind]||9));
  return tags;
}
async function renderStockPick(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🧠 AI 多因子选股</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">30因子7维打分 V3：AI 动态权重 + LLM 舆情 + 因子生成器加分</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">⚠️ 含真实财务数据（ROE/毛利率/净利率/现金流/负债率），DeepSeek 根据市场环境动态调权重。仅供参考，不构成投资建议。</div>
<div id="stockScreenMeta" style="display:none;font-size:11px;color:var(--accent);margin-bottom:8px;padding:6px 8px;background:rgba(59,130,246,.06);border-radius:6px"></div>
<div id="stockPickList"><div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在从 5000+ A股中筛选（AI 动态调权中）...</div></div>
</div>`;
// v9.5.120: 前端不缓存，后端文件缓存保证秒回
try{
const _uid=getProfileId()||'';
const r=await fetch(API_BASE+'/stock-screen?top_n=50'+(_uid?'&userId='+_uid:''),{signal:AbortSignal.timeout(60000)});
if(!r.ok)throw new Error('fetch failed');
const data=await r.json();
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
const _regimeMap={
  'trending_bull':'趋势牛市','trending_bear':'趋势熊市',
  'volatile':'震荡市','neutral':'中性','recovery':'修复期',
  'overheated':'过热','panic':'恐慌',
  // 后端中文 regime 直接映射（DeepSeek 有时输出中文）
  '牛市':'牛市','熊市':'熊市','震荡':'震荡市','中性':'中性',
  '轮动':'行业轮动','过热':'过热','恐慌':'恐慌底部',
  'rotation':'行业轮动','bull':'牛市','bear':'熊市',
};
const regimeZh=_regimeMap[regime]||regime;
const weights=data.weights||{};
const _wMap={'value':'价值','growth':'成长','quality':'质量','momentum':'动量','risk':'风险','liquidity':'流动性','sentiment':'舆情'};
const wText=Object.entries(weights).map(([k,v])=>`${_wMap[k]||k}:${v}%`).join(' · ');

// ★ 行业流动提示：来自 market_timing.regime_hint 或 style_timing
const regimeHint = mt.regime_hint || '';
// style_timing：高位/低位行业
const st = data.style_timing || {};
const styles = st.styles || [];
const highStyles = styles.filter(s=>(s.avg_3m||0) > 12).map(s=>s.style).slice(0,3);
const lowStyles  = styles.filter(s=>(s.avg_3m||0) < -2).map(s=>s.style).slice(0,3);
let styleHint = '';
if(highStyles.length || lowStyles.length){
  const parts=[];
  if(highStyles.length) parts.push(`高位: ${highStyles.join('/')}（涨多）`);
  if(lowStyles.length)  parts.push(`低估: ${lowStyles.join('/')}（潜力）`);
  styleHint = parts.join('　');
}

metaEl.innerHTML=`<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
  <span>🧠 市场判断: <b>${regimeZh}</b></span>
  <span style="opacity:0.4">|</span>
  <span style="font-size:10px;color:var(--text2)">动态权重: ${wText}</span>
</div>
${regimeHint?`<div style="font-size:11px;color:var(--color-brand-400,#FFB755);margin-top:3px;line-height:1.6">${regimeHint}</div>`:''}
${styleHint?`<div style="font-size:10px;color:var(--text2);margin-top:2px;opacity:0.85">${styleHint}</div>`:''}`;
metaEl.style.display='block';
}
if(!stocks.length){listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">'+(data.error||'暂无数据')+'</div>';return}
window._stockScreenData=stocks;

// v9.5.42 F3 推荐理由（先于渲染计算，注入 s.reason）
stocks.forEach(s=>{ if(!s.reason) s.reason = _computeStockReason(s); });

// v9.5.42 视图过滤（all/wish/mine）+ P5 自定义筛选
if(window._stockView==null) window._stockView='all';
if(window._stockFilter==null) window._stockFilter={pe_max:null,pb_max:null,roe_min:null,gm_min:null,mcap_min:null};
const wishSet=new Set(_stockWishlist().map(x=>x.code));
const filt=window._stockFilter;
let shownStocks=stocks.slice();
if(window._stockView==='wish'){
  shownStocks = shownStocks.filter(s=>wishSet.has(s.code));
}else if(window._stockView==='mine'){
  // 取持仓股 code，标准化（去 sh/sz 前缀）
  const myCodes = new Set((window._myStockCodes||[]).map(c=>String(c).replace(/^(sh|sz)/i,'')));
  shownStocks = shownStocks.filter(s=>myCodes.has(s.code.replace(/^(sh|sz)/i,'')));
}else if(window._stockView==='potential'){
  // v9.5.83: 潜力视图 — 只显示有 potential 标识的股票，按 high > mid 排序
  shownStocks = shownStocks.filter(s=>s.potential);
  shownStocks.sort((a,b)=>{
    const lvA=a.potential?.level==='high'?2:1, lvB=b.potential?.level==='high'?2:1;
    return lvA!==lvB ? lvB-lvA : (b.score||0)-(a.score||0);
  });
}
// P5 数值筛选
shownStocks = shownStocks.filter(s=>{
  if(filt.pe_max!=null && s.pe!=null && s.pe>filt.pe_max) return false;
  if(filt.pb_max!=null && s.pb!=null && s.pb>filt.pb_max) return false;
  if(filt.roe_min!=null && (s.roe==null || s.roe<filt.roe_min)) return false;
  if(filt.gm_min!=null && (s.gross_margin==null || s.gross_margin<filt.gm_min)) return false;
  if(filt.mcap_min!=null && (s.market_cap==null || s.market_cap<filt.mcap_min)) return false;
  return true;
});

const wishCount=_stockWishlist().length;
const potentialCount=stocks.filter(s=>s.potential).length;
// v9.5.83b: 选股 viewItems 去掉心愿，用筛选按钮替代位置
const viewItems=[['all','📈','全部'],['potential','🚀',`潜力${potentialCount>0?' '+potentialCount:''}`],['mine','📋','持仓']];
const filterActive = Object.values(filt).some(v=>v!=null);
window._lastStockData = data;  // 保存供 toolbar 切换视图复用
const toolbarHTML=`<div style="display:flex;align-items:center;gap:5px;margin-bottom:10px;flex-wrap:wrap">
  ${viewItems.map(([k,icon,label])=>{
    const isAct=window._stockView===k;
    return `<button onclick="window._stockView='${k}';_fillStockList(window._lastStockData||{stocks:window._stockScreenData,_skipFetch:true})" style="display:inline-flex;align-items:center;gap:3px;padding:4px 9px;font-size:11px;font-weight:${isAct?'700':'500'};border:1px solid ${isAct?'rgba(255,138,76,.7)':'rgba(148,163,184,.22)'};border-radius:14px;background:${isAct?'rgba(255,138,76,.18)':'transparent'};color:${isAct?'#FFB755':'#9aa1ac'};cursor:pointer">${icon} <span>${label}</span></button>`;
  }).join('')}
  <button onclick="_showStockFilterModal()" style="display:inline-flex;align-items:center;gap:2px;padding:4px 9px;font-size:11px;border:1px solid ${filterActive?'rgba(99,102,241,.7)':'rgba(148,163,184,.22)'};border-radius:14px;background:${filterActive?'rgba(99,102,241,.18)':'transparent'};color:${filterActive?'#A5B4FC':'#9aa1ac'};cursor:pointer">⚙️ <span>筛选${filterActive?' ●':''}</span></button>
</div>`;

const emptyHTML = !shownStocks.length ? `<div style="text-align:center;padding:30px;color:var(--text2);font-size:12px">${window._stockView==='wish'?'❤️ 心愿单为空 — 点列表中 🤍 加入':window._stockView==='mine'?'📋 当前榜单中未匹配到你的持仓股':window._stockView==='potential'?'<div style="font-size:24px;margin-bottom:8px">🔭</div><div style="font-size:13px;font-weight:500">当前50只中没有达到潜力标准的股票</div><div style="font-size:11px;color:var(--text-tertiary);margin-top:8px;line-height:1.7">需同时满足：A 小盘低估成长 + B 动量突破 + C 质量保障中的 2 项<br>市场高位时潜力股较少属于正常</div>':'⚙️ 当前筛选条件无匹配股票'}</div>` : '';

listEl.innerHTML=`${timingBanner}${toolbarHTML}<div style="font-size:11px;color:var(--text2);margin-bottom:8px">全市场 ${data.total||'5000+'} 只 → 当前视图 ${shownStocks.length}/${stocks.length}</div>
${emptyHTML || ''}
${shownStocks.map((s,i)=>{
  // v9.5.51 方案A: 评分圆 + 标签云 + 横向指标行
  const chgColor=s.change_pct>0?'var(--color-bull,#FF6B6B)':s.change_pct<0?'var(--color-bear,#00E5A0)':'var(--text2)';
  // 评分圆配色（≥75绿/60-75橙/<60红）
  const scoreColorMap = s.score>=75 ? {border:'rgba(34,197,94,.5)', text:'#86EFAC'}
                      : s.score>=60 ? {border:'rgba(245,158,11,.5)', text:'#FBBF24'}
                      : {border:'rgba(239,68,68,.5)', text:'#FCA5A5'};
  const cleanCode=s.code.replace(/^(sh|sz)/i,'');
  const wished=wishSet.has(s.code);
  const inCmp=window._stockCompareSet.has(s.code);
  const dataIdx = window._stockScreenData.indexOf(s);

  // 构建标签云（最多 4 个）
  const stockTags = _buildStockTagPool(s);
  const tagsHtml = stockTags.slice(0,4).map(t =>
    `<span style="display:inline-flex;align-items:center;font-size:10px;padding:2px 7px;border-radius:4px;background:${t.bg};color:${t.color};font-weight:500;line-height:1.4;white-space:nowrap" title="${t.title}">${t.label}</span>`
  ).join('');

  // 横向指标行（PE/ROE/毛利/市值） — v9.5.59 .toFixed(2) 控制精度
  const metrics = [];
  if(s.pe!=null) metrics.push({l:'PE', v: Number(s.pe).toFixed(2)});
  if(s.roe!=null) metrics.push({l:'ROE', v: Number(s.roe).toFixed(2)+'%'});
  if(s.gross_margin!=null) metrics.push({l:'毛利', v: Number(s.gross_margin).toFixed(2)+'%'});
  if(s.market_cap!=null) metrics.push({l:'市值', v: s.market_cap>=10000?(s.market_cap/10000).toFixed(2)+'万亿':Math.round(s.market_cap)+'亿'});
  const metricsHtml = metrics.slice(0,4).map(m=>`<span style="display:inline-flex;align-items:center;font-size:10px;color:#7A8499;margin-right:10px">${m.l}<b style="color:#D8DCE5;font-weight:600;margin-left:3px">${m.v}</b></span>`).join('');

  // AI 评论或 reason
  const aiText = s.aiComment || s.reason || '';
  const aiHtml = aiText
    ? `<div style="font-size:11px;color:#A5B4FC;line-height:1.5;margin-top:6px;padding:6px 10px;background:rgba(99,102,241,.08);border-radius:6px">🤖 ${aiText}</div>`
    : '';

  return`<div style="padding:12px 0;border-bottom:1px solid rgba(148,163,184,.06);cursor:pointer" onclick="showStockDetailModal(window._stockScreenData[${dataIdx}])">
    <!-- 行1: 序号 + 评分圆 + 名字/标签云 + 涨跌 -->
    <div style="display:flex;align-items:center;gap:10px">
      <span style="font-size:12px;color:var(--text2);font-weight:700;flex-shrink:0;width:14px;text-align:center">${i+1}</span>
      <div style="width:42px;height:42px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;flex-shrink:0;border:2px solid ${scoreColorMap.border};color:${scoreColorMap.text}">
        <span style="font-size:14px;font-weight:800;line-height:1">${s.score}</span>
        <span style="font-size:8px;line-height:1;margin-top:2px;opacity:0.7">评分</span>
      </div>
      <div style="flex:1;min-width:0">
        <div style="font-size:14px;font-weight:600;color:var(--text-primary,#F0F2F7);line-height:1.35;display:flex;align-items:baseline;gap:6px;flex-wrap:wrap">
          <span>${s.name}</span>
          <span style="font-size:11px;color:var(--text-tertiary,#7A8499);font-weight:400">${cleanCode}</span>
        </div>
        <div style="margin-top:5px;display:flex;flex-wrap:wrap;gap:4px;align-items:center"><span data-stock-events="${cleanCode}"></span>${tagsHtml}</div>
      </div>
      <div style="text-align:right;flex-shrink:0">
        <div style="font-size:15px;font-weight:800;color:${chgColor};line-height:1">${s.change_pct!=null?(s.change_pct>0?'+':'')+Number(s.change_pct).toFixed(2)+'%':'—'}</div>
        <div style="font-size:9px;color:var(--text-tertiary,#7A8499);margin-top:3px" title="A股交易日 9:30-15:00 显示当日实时；非交易时段显示最近一个交易日">${_lastTradeDayLabel()}</div>
      </div>
    </div>
    <!-- 行2: 横向指标 -->
    ${metricsHtml?`<div style="margin-top:8px;padding-left:66px">${metricsHtml}</div>`:''}
    <!-- 行3: AI 评论 -->
    ${aiHtml}
    <!-- 行4: 操作按钮组 -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;padding-left:66px;gap:8px">
      <span style="font-size:10px;color:var(--text-tertiary,#7A8499);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.timing_label||''}${s.industry_insight?' · '+s.industry_insight.slice(0,30):''}</span>
      <div style="display:flex;gap:4px;flex-shrink:0;align-items:center" onclick="event.stopPropagation()">
        <button onclick="_toggleStockWish('${s.code}','${(s.name||'').replace(/'/g,'')}')" style="padding:2px 5px;font-size:13px;border:none;background:transparent;cursor:pointer" title="${wished?'从心愿单移除':'加入心愿单'}">${wished?'❤️':'🤍'}</button>
        <button onclick="_toggleStockCompare('${s.code}','${(s.name||'').replace(/'/g,'')}')" style="padding:2px 7px;font-size:10px;font-weight:600;border:1px solid ${inCmp?'#818CF8':'rgba(148,163,184,.3)'};border-radius:4px;background:${inCmp?'rgba(99,102,241,.18)':'transparent'};color:${inCmp?'#818CF8':'#9aa1ac'};cursor:pointer" title="${inCmp?'已加入对比':'加入对比'}">${inCmp?'✓':'+'}</button>
        <button onclick="showFundChart('${cleanCode}')" style="padding:2px 8px;font-size:10px;border:1px solid rgba(148,163,184,.3);border-radius:4px;background:transparent;color:#9aa1ac;cursor:pointer">📈 K线</button>
      </div>
    </div>
  </div>`;
}).join('')}
<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="insightTab='stockpick';renderInsight()">🔄 刷新</button></div>
<div style="font-size:11px;color:#475569;margin-top:8px;line-height:1.5">${data.method||''}<br>${data.note||''}</div>`;

// F7 事件标（持仓股复用现有 risk-events 接口，懒加载）

stocks.forEach(s=>{
const sc=s.scores||{};
setExplain('stock_'+s.code,s.name+' ('+s.code+')',
'💰 价格：¥'+s.price+' · 涨跌：'+(s.change_pct!=null?s.change_pct+'%':'—')+'\n📊 PE：'+(s.pe||'—')+' · PB：'+(s.pb||'—')+' · 换手率：'+(s.turnover||'—')+'%\n📈 市值：'+(s.market_cap?s.market_cap+'亿':'—')+'\n\n📋 财务指标：\n• ROE：'+(s.roe||'—')+'%\n• 毛利率：'+(s.gross_margin||'—')+'%\n• 净利率：'+(s.net_margin||'—')+'%\n• 负债率：'+(s.debt_ratio||'—')+'%\n• 营收增速：'+(s.revenue_growth||'—')+'%\n• EPS：'+(s.eps||'—')+'\n\n🎯 综合评分：'+s.score+'/100\n\n7维30因子详情：\n• 价值(20%)：'+sc.value+' (PE/PB/股息率/ROE-PB/EPS/低PE高ROE)\n• 成长(15%)：'+sc.growth+' (营收增速/ROE/EPS/60日动量/PEG)\n• 质量(18%)：'+sc.quality+' (ROE/毛利率/净利率/负债率/现金流/市值)\n• 动量(15%)：'+sc.momentum+' (5日/20日/60日/今日)\n• 风险(12%)：'+sc.risk+' (振幅/负债率/现金流/PE极端)\n• 流动性(10%)：'+sc.liquidity+' (换手率/市值/成交额)\n• 舆情(10%)：'+sc.sentiment+' (新闻情绪/LLM评分)\n\n⚠️ 仅供参考，不构成投资建议。',
{type:'stock',code:s.code,name:s.name,score:s.score,pe:s.pe||0,roe:s.roe||0,gross_margin:s.gross_margin||0})
})}

