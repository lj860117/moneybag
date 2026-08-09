// ============================================================
// v9.5.43 A2 再平衡触发模块
// ============================================================
// 目标配置默认 = LeiJiang 5 只 ETF 组合：
//   SP500(30%) + CSI A500(25%) + Nasdaq100(20%) + Dividend Low-Vol(15%) + CSI300(10%)
// 触发阈值：偏离 ±5% 给提示，偏离 ±10% 强烈提示
// 入口：landing 卡片 + 独立 modal

// 默认目标配置（用户可编辑 localStorage 保存）
const REBALANCE_DEFAULT_TARGETS = [
  { tag: 'SP500',     label: '标普500',       pct: 30, match: ['标普500','sp500','标普 500','S&P','SPY','513500','159612','159655'] },
  { tag: 'CSI_A500',  label: '中证A500',      pct: 25, match: ['A500','中证a500','563220','159352','563360','512050'] },
  { tag: 'NDX',       label: '纳斯达克100',   pct: 20, match: ['纳斯达克','纳指','nasdaq','ndx','159632','513100','159509'] },
  { tag: 'DIVIDEND',  label: '红利低波',      pct: 15, match: ['红利','低波','dividend','510880','515100','515450','563020'] },
  { tag: 'CSI300',    label: '沪深300',       pct: 10, match: ['沪深300','hs300','510300','159919','510310'] },
];

function _loadRebalanceTargets(){
  try{
    const raw = localStorage.getItem(_uk('moneybag_rebalance_targets'));
    if(raw){ const arr = JSON.parse(raw); if(Array.isArray(arr) && arr.length) return arr; }
  }catch{}
  return REBALANCE_DEFAULT_TARGETS;
}
function _saveRebalanceTargets(arr){
  try{ localStorage.setItem(_uk('moneybag_rebalance_targets'), JSON.stringify(arr)); }catch{}
}
function _loadRebalanceThreshold(){
  try{
    const v = parseFloat(localStorage.getItem(_uk('moneybag_rebalance_threshold')));
    if(!isNaN(v) && v>0 && v<50) return v;
  }catch{}
  return 5;  // 默认 5%
}
function _saveRebalanceThreshold(v){
  try{ localStorage.setItem(_uk('moneybag_rebalance_threshold'), String(v)); }catch{}
}

// 把持仓匹配到目标 bucket
function _matchHoldingToTarget(h, targets){
  const text = (h.name||'') + ' ' + (h.code||'');
  for(const t of targets){
    if(t.match.some(kw => text.toLowerCase().includes(kw.toLowerCase()))) return t.tag;
  }
  return null;  // 未分配
}

// 核心：拉取持仓 -> 算实际权重 -> 对比目标 -> 输出偏差
async function computeRebalance(){
  const targets = _loadRebalanceTargets();
  const threshold = _loadRebalanceThreshold();
  // 取家庭聚合（覆盖夫妻俩）
  let holdings = [];
  let totalInvest = 0;
  try{
    const r = await fetch(API_BASE+'/family/portfolio-summary?userId='+encodeURIComponent(getProfileId()),{signal:AbortSignal.timeout(10000)});
    if(r.ok){
      const d = await r.json();
      (d.members||[]).forEach(m=>{
        (m.holdings||[]).forEach(h=>{
          if(h.marketValue) holdings.push(h);
        });
      });
      totalInvest = holdings.reduce((s,h)=>s+(h.marketValue||0),0);
    }
  }catch(e){ console.warn('rebalance fetch:',e); }
  // 兜底：本地 txns
  if(!holdings.length){
    try{
      const txns = (typeof loadTxns==='function')?loadTxns():[];
      // 简化：把每笔买入累计为持仓
      const map = new Map();
      txns.forEach(t=>{
        if(t.type==='buy' || !t.type){
          const k = t.code;
          if(!map.has(k)) map.set(k, {code:t.code, name:t.name, marketValue:0});
          map.get(k).marketValue += (t.amount||0);
        }
      });
      holdings = Array.from(map.values());
      totalInvest = holdings.reduce((s,h)=>s+h.marketValue,0);
    }catch{}
  }

  // 按 bucket 汇总
  const bucketTotals = {};
  const unmatched = [];
  for(const h of holdings){
    const tag = _matchHoldingToTarget(h, targets);
    if(tag){
      bucketTotals[tag] = (bucketTotals[tag]||0) + (h.marketValue||0);
    } else {
      unmatched.push(h);
    }
  }
  const unmatchedTotal = unmatched.reduce((s,h)=>s+(h.marketValue||0),0);

  // 算各 bucket 的实际权重（基于 totalInvest 总盘）
  const rows = targets.map(t=>{
    const actual = bucketTotals[t.tag] || 0;
    const actualPct = totalInvest>0 ? (actual/totalInvest)*100 : 0;
    const deviation = actualPct - t.pct;
    let signal = 'ok';
    if(Math.abs(deviation) > threshold*2) signal = 'critical';
    else if(Math.abs(deviation) > threshold) signal = 'warn';
    // 建议操作金额
    const targetValue = totalInvest * (t.pct/100);
    const diffValue = targetValue - actual;  // 正=买入，负=卖出
    return {
      tag: t.tag, label: t.label, targetPct: t.pct,
      actual, actualPct, deviation, signal, diffValue,
    };
  });

  return {
    rows,
    totalInvest,
    threshold,
    unmatched, unmatchedTotal,
    unmatchedPct: totalInvest>0 ? (unmatchedTotal/totalInvest)*100 : 0,
    needsAction: rows.some(r=>r.signal!=='ok'),
  };
}

// 渲染 modal
window.showRebalanceModal = async function(){
  const o = document.createElement('div');
  o.className = 'modal-overlay';
  o.onclick = e => { if(e.target===o) o.remove(); };
  o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:90vh;display:flex;flex-direction:column">
    <div class="modal-handle"></div>
    <div class="modal-title">🎯 组合再平衡检查</div>
    <div id="rebalanceBody" style="flex:1;overflow-y:auto;padding:20px 4px;text-align:center;color:var(--text2);font-size:12px">
      <div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 10px;border-width:2px"></div>正在计算偏差...
    </div>
    <div style="display:flex;gap:8px;margin-top:8px">
      <button class="mb-btn mb-btn--secondary" style="flex:1" onclick="_editRebalanceTargets()">⚙️ 编辑目标</button>
      <button class="mb-btn mb-btn--primary" style="flex:1" onclick="document.querySelector('.modal-overlay')?.remove()">关闭</button>
    </div>
  </div>`;
  document.body.appendChild(o);

  try{
    const r = await computeRebalance();
    const body = document.getElementById('rebalanceBody');
    if(!body) return;
    body.style.cssText = 'flex:1;overflow-y:auto;padding:0 4px;color:inherit;text-align:left';
    const fmt = v => '¥' + Math.abs(v).toLocaleString('zh-CN',{maximumFractionDigits:0});
    const totalLine = `<div style="padding:10px 12px;margin-bottom:10px;background:rgba(99,102,241,.08);border:1px solid rgba(99,102,241,.2);border-radius:10px">
      <div style="font-size:11px;color:var(--text2)">家庭总投资市值（含夫妻俩）</div>
      <div style="font-size:18px;font-weight:700;color:#A5B4FC">¥${r.totalInvest.toLocaleString('zh-CN',{maximumFractionDigits:0})}</div>
      <div style="font-size:10px;color:var(--text2);margin-top:4px">偏差阈值 ±${r.threshold}%（可在编辑目标里调整）</div>
    </div>`;

    const tableRows = r.rows.map(row=>{
      const sigBadge = row.signal==='critical' ? '<span style="color:#F87171;font-weight:700">🔴 严重</span>'
                    : row.signal==='warn' ? '<span style="color:#F59E0B;font-weight:700">🟡 偏离</span>'
                    : '<span style="color:#10B981">🟢 OK</span>';
      const devColor = Math.abs(row.deviation) > r.threshold ? '#F87171' : '#10B981';
      const actionText = Math.abs(row.diffValue) < 100 ? '—' : (row.diffValue > 0
        ? `<span style="color:#10B981">+${fmt(row.diffValue)} 补仓</span>`
        : `<span style="color:#F87171">-${fmt(Math.abs(row.diffValue))} 减仓</span>`);
      return `<tr style="border-top:1px solid rgba(148,163,184,.08)">
        <td style="padding:10px 6px;font-size:12px;font-weight:600;color:var(--text-default,#D8DCE5)">${row.label}</td>
        <td style="padding:10px 6px;text-align:right;font-size:11px;color:var(--text2)">${row.targetPct}%</td>
        <td style="padding:10px 6px;text-align:right;font-size:12px;font-weight:600">${row.actualPct.toFixed(1)}%</td>
        <td style="padding:10px 6px;text-align:right;font-size:12px;font-weight:700;color:${devColor}">${row.deviation>=0?'+':''}${row.deviation.toFixed(1)}%</td>
        <td style="padding:10px 6px;text-align:right;font-size:11px">${sigBadge}</td>
      </tr>`;
    }).join('');

    const actionList = r.rows.filter(row=>row.signal!=='ok' && Math.abs(row.diffValue)>=100).map(row=>{
      const action = row.diffValue>0
        ? `<span style="color:#10B981">📈 ${row.label}</span>：补仓 <b>${fmt(row.diffValue)}</b>`
        : `<span style="color:#F87171">📉 ${row.label}</span>：减仓 <b>${fmt(Math.abs(row.diffValue))}</b>`;
      return `<div style="padding:6px 0;font-size:12px;border-bottom:1px solid rgba(148,163,184,.06)">${action}</div>`;
    }).join('');
    const actionPanel = actionList ? `<div style="margin-top:14px;padding:12px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.18);border-radius:10px">
      <div style="font-size:13px;font-weight:700;color:#F59E0B;margin-bottom:6px">⚠️ 建议操作</div>
      ${actionList}
      <div style="margin-top:8px;font-size:10px;color:var(--text2);line-height:1.5">⚠️ 操作前请结合估值/情绪/政策综合判断。市场波动期可分批执行，避免一次性买卖造成冲击。</div>
    </div>` : `<div style="margin-top:14px;padding:14px;background:rgba(16,185,129,.06);border:1px solid rgba(16,185,129,.18);border-radius:10px;text-align:center">
      <div style="font-size:24px;margin-bottom:4px">✅</div>
      <div style="font-size:13px;font-weight:700;color:#10B981">配置均衡，无需调整</div>
      <div style="font-size:11px;color:var(--text2);margin-top:4px">所有目标偏差均在 ±${r.threshold}% 内</div>
    </div>`;

    const unmatchedPanel = r.unmatched.length ? `<div style="margin-top:14px;padding:10px 12px;background:rgba(148,163,184,.06);border-radius:8px">
      <div style="font-size:11px;color:var(--text2);margin-bottom:4px">📦 未归类持仓 (${r.unmatched.length} 只 · ${r.unmatchedPct.toFixed(1)}%)</div>
      <div style="font-size:11px;color:var(--text2);line-height:1.7">${r.unmatched.slice(0,5).map(h=>`${h.name||h.code}（${fmt(h.marketValue||0)}）`).join('、')}${r.unmatched.length>5?`，等 ${r.unmatched.length} 只`:''}</div>
      <div style="font-size:10px;color:var(--text2);margin-top:4px;opacity:0.7">这些持仓不在 5 大目标 bucket 内，不计入再平衡</div>
    </div>` : '';

    // v9.5.88: 本次投入计算器 — 输入预算，按缺口比例智能分配
    const _rebalRows = r.rows; // 闭包捕获
    const _rebalTotal = r.totalInvest;
    const budgetCalcPanel = `<div id="budgetCalcPanel" style="margin-top:14px;padding:12px;background:rgba(99,102,241,.07);border:1px solid rgba(99,102,241,.2);border-radius:10px">
      <div style="font-size:13px;font-weight:500;color:#A5B4FC;margin-bottom:8px">💰 本次投入分配计算</div>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <span style="font-size:12px;color:var(--text2);flex-shrink:0">投入预算</span>
        <input id="budgetInput" type="number" min="100" step="100" placeholder="例：5000" style="flex:1;padding:6px 10px;border:1px solid rgba(148,163,184,.3);border-radius:8px;background:rgba(15,23,42,.5);color:#fff;font-size:13px">
        <span style="font-size:12px;color:var(--text2);flex-shrink:0">元</span>
        <button onclick="(function(){
          const budget=parseFloat(document.getElementById('budgetInput').value);
          if(isNaN(budget)||budget<100){document.getElementById('budgetResult').innerHTML='<span style=color:#F87171>请输入有效金额</span>';return;}
          // 按缺口从大到小分配：先填最大缺口，剩余按比例
          const gaps=JSON.parse(document.getElementById('budgetInput').dataset.rows||'[]');
          if(!gaps.length){document.getElementById('budgetResult').innerHTML='<span style=color:#F87171>数据未就绪</span>';return;}
          // 缺口 = diffValue > 0 的桶（需要补仓），按绝对缺口量排序
          const needing=gaps.filter(g=>g.diffValue>0).sort((a,b)=>b.diffValue-a.diffValue);
          if(!needing.length){document.getElementById('budgetResult').innerHTML='<div style=color:#10B981;font-size:12px>当前配置已均衡，可按目标比例均摊</div>';return;}
          // 按各桶缺口占总缺口比例分配预算
          const totalGap=needing.reduce((s,g)=>s+g.diffValue,0);
          let html='<div style=font-size:12px;margin-top:4px>';
          needing.forEach(g=>{
            const share=Math.round(budget*(g.diffValue/totalGap)/100)*100;
            if(share<100)return;
            html+=\`<div style='padding:5px 0;border-bottom:1px solid rgba(148,163,184,.08)'><span style=color:#A5B4FC;font-weight:500>\${g.label}</span> → <b style=color:#10B981>¥\${share.toLocaleString()}</b> <span style=font-size:10px;color:var(--text2)>（缺口\${g.deviation.toFixed(1)}%）</span></div>\`;
          });
          html+='<div style=margin-top:6px;font-size:10px;color:var(--text2)>按缺口比例分配，优先填补最大偏离</div></div>';
          document.getElementById('budgetResult').innerHTML=html;
        })()" style="padding:5px 12px;border-radius:8px;border:none;background:rgba(99,102,241,.4);color:#fff;font-size:12px;cursor:pointer">计算</button>
      </div>
      <div id="budgetResult" style="font-size:12px;color:var(--text2)">输入预算金额后点计算</div>
    </div>`;

    // 把再平衡数据存到 input 的 data 属性，供计算器读取
    const rowsJson = JSON.stringify(r.rows.map(row=>({label:row.label,diffValue:row.diffValue,deviation:row.deviation})));

    body.innerHTML = `${totalLine}
      <table style="width:100%;border-collapse:collapse;margin-bottom:6px">
        <thead>
          <tr style="color:var(--text-tertiary,#7A8499);font-size:11px">
            <td style="padding:8px 6px;text-align:left">目标</td>
            <td style="padding:8px 6px;text-align:right;width:48px">目标%</td>
            <td style="padding:8px 6px;text-align:right;width:54px">实际%</td>
            <td style="padding:8px 6px;text-align:right;width:54px">偏差</td>
            <td style="padding:8px 6px;text-align:right;width:60px">信号</td>
          </tr>
        </thead>
        <tbody>${tableRows}</tbody>
      </table>
      ${actionPanel}
      ${budgetCalcPanel}
      ${unmatchedPanel}
      <div style="margin-top:12px;padding:8px 10px;background:rgba(99,102,241,.05);border-radius:6px;font-size:10px;color:#A5B4FC;line-height:1.6">
        💡 再平衡逻辑：实际权重偏离目标 > ±${r.threshold}% → 🟡 偏离；> ±${(r.threshold*2)}% → 🔴 严重。<br>
        建议金额 = (目标比例 - 实际比例) × 总市值。
      </div>`;
    // 把行数据注入到 budgetInput 的 dataset
    const bi = body.querySelector('#budgetInput');
    if(bi) bi.dataset.rows = rowsJson;
  }catch(e){
    const body = document.getElementById('rebalanceBody');
    if(body) body.innerHTML = `<div style="text-align:center;padding:30px;color:var(--red)">计算失败：${e.message}</div>`;
  }
};

// 编辑目标配置 modal
window._editRebalanceTargets = function(){
  const targets = _loadRebalanceTargets();
  const threshold = _loadRebalanceThreshold();
  const o = document.createElement('div');
  o.className = 'modal-overlay';
  o.onclick = e => { if(e.target===o) o.remove(); };
  o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:85vh;overflow-y:auto">
    <div class="modal-handle"></div>
    <div class="modal-title">⚙️ 编辑再平衡目标</div>
    <div style="font-size:11px;color:var(--text2);margin-bottom:12px">修改各目标占比（总和应=100%），保存后立即生效</div>
    <div id="rbEditRows" style="display:grid;grid-template-columns:1fr 60px;gap:8px 10px;font-size:12px;align-items:center">
    ${targets.map((t,i)=>`
      <div style="color:var(--text-default,#D8DCE5)">${t.label} <span style="font-size:10px;color:var(--text2)">[${t.tag}]</span></div>
      <input type="number" id="rb_${i}" step="1" min="0" max="100" value="${t.pct}" style="padding:6px 8px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:rgba(15,23,42,.5);color:#fff;width:100%;font-size:12px">
    `).join('')}
    </div>
    <div id="rbSum" style="font-size:11px;color:var(--text2);margin-top:8px;text-align:right"></div>
    <div style="margin-top:14px;display:grid;grid-template-columns:1fr 80px;gap:8px;align-items:center">
      <div style="color:var(--text-default,#D8DCE5);font-size:12px">偏差阈值 (%)</div>
      <input type="number" id="rb_threshold" step="0.5" min="1" max="20" value="${threshold}" style="padding:6px 8px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:rgba(15,23,42,.5);color:#fff;width:100%;font-size:12px">
    </div>
    <div style="display:flex;gap:8px;margin-top:16px">
      <button class="mb-btn mb-btn--secondary" style="flex:1" onclick="localStorage.removeItem(_uk('moneybag_rebalance_targets'));localStorage.removeItem(_uk('moneybag_rebalance_threshold'));document.querySelector('.modal-overlay')?.remove();showRebalanceModal();">恢复默认</button>
      <button class="mb-btn mb-btn--primary" style="flex:1" onclick="_saveRebalanceEdit()">保存</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  // 实时显示总和
  const recompute = ()=>{
    let sum = 0;
    targets.forEach((t,i)=>{ const v=parseFloat(document.getElementById('rb_'+i)?.value); if(!isNaN(v)) sum+=v; });
    const sumEl = document.getElementById('rbSum');
    if(sumEl) sumEl.innerHTML = `合计：<b style="color:${Math.abs(sum-100)<0.01?'#10B981':'#F59E0B'}">${sum.toFixed(0)}%</b>${Math.abs(sum-100)>0.01?'（应=100%）':''}`;
  };
  targets.forEach((t,i)=>{ document.getElementById('rb_'+i)?.addEventListener('input', recompute); });
  recompute();
};
window._saveRebalanceEdit = function(){
  const cur = _loadRebalanceTargets();
  const newArr = cur.map((t,i)=>{
    const v = parseFloat(document.getElementById('rb_'+i)?.value);
    return { ...t, pct: isNaN(v)?t.pct:v };
  });
  const sum = newArr.reduce((s,t)=>s+t.pct, 0);
  if(Math.abs(sum-100) > 0.5){ alert('合计应为 100%，当前 ' + sum.toFixed(0) + '%'); return; }
  _saveRebalanceTargets(newArr);
  const th = parseFloat(document.getElementById('rb_threshold')?.value);
  if(!isNaN(th) && th>0 && th<20) _saveRebalanceThreshold(th);
  document.querySelector('.modal-overlay')?.remove();
  showRebalanceModal();
};

// landing 卡片入口：自动检测偏差并渲染状态卡
window.renderRebalanceCard = async function(){
  const card = document.getElementById('rebalanceCard');
  if(!card) return;
  try{
    const r = await computeRebalance();
    if(r.totalInvest < 100){
      // v9.5.90: 阈值从 1000 降到 100，方便小额测试用户也能看到入口
      card.style.display='none';
      return;
    }
    const sigCount = r.rows.filter(row=>row.signal!=='ok').length;
    const criticalCount = r.rows.filter(row=>row.signal==='critical').length;
    let badgeText, badgeColor, line2;
    if(criticalCount > 0){
      badgeText = `🔴 ${criticalCount} 项严重偏离`;
      badgeColor = '#F87171';
      line2 = `${sigCount} 个目标偏离 > ±${r.threshold}%，建议尽快再平衡`;
    } else if(sigCount > 0){
      badgeText = `🟡 ${sigCount} 项偏离`;
      badgeColor = '#F59E0B';
      line2 = `偏差仍在可控范围，可择机调整`;
    } else {
      badgeText = `✅ 配置均衡`;
      badgeColor = '#10B981';
      line2 = `所有目标偏差 ±${r.threshold}% 内 — 持续定投即可`;
    }
    card.innerHTML = `<div onclick="showRebalanceModal()" style="cursor:pointer;padding:14px;background:linear-gradient(135deg,rgba(99,102,241,.08),rgba(168,85,247,.05));border:1px solid rgba(99,102,241,.18);border-radius:14px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
        <div style="display:flex;align-items:center;gap:8px">
          <span style="font-size:20px">🎯</span>
          <div>
            <div style="font-size:14px;font-weight:700;color:var(--text-default,#D8DCE5)">再平衡检查</div>
            <div style="font-size:11px;color:var(--text2);margin-top:2px">${line2}</div>
          </div>
        </div>
        <span style="font-size:11px;padding:4px 10px;border-radius:14px;background:${badgeColor}22;color:${badgeColor};font-weight:600;white-space:nowrap">${badgeText}</span>
      </div>
    </div>`;
    card.style.display = 'block';
  }catch(e){
    card.style.display='none';
  }
};
