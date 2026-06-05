// ============================================================
// v9.5.43 D3 每周复盘助手（独立模块）
// ============================================================
// 入口：insight tab=weekly （已挂在 insight 顶 tab 列表）
// 数据：本地 txns + family/portfolio-summary + AI 复盘按钮

window.renderWeeklyReport = async function(el){
  if(!el) return;
  el.innerHTML = `<div class="dashboard-card" style="overflow:hidden">
    <div class="dashboard-card-title">📋 每周财务复盘</div>
    <div style="font-size:11px;color:var(--text2);margin-bottom:10px">本周收益、操作记录与 AI 复盘建议（数据来自本地交易 + 家庭聚合）</div>
    <div id="weeklyBody"><div style="text-align:center;padding:30px"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div><div style="font-size:12px;color:var(--text2)">汇总本周数据...</div></div></div>
  </div>`;

  const body = document.getElementById('weeklyBody');
  if(!body) return;

  // 1. 计算本周时间区间（周一 00:00 ~ 现在）
  const now = new Date();
  const dow = (now.getDay() + 6) % 7;  // 0=周一
  const monday = new Date(now); monday.setDate(now.getDate() - dow); monday.setHours(0,0,0,0);
  const weekStart = monday.getTime();
  const weekStartStr = monday.toISOString().slice(0,10);
  const todayStr = now.toISOString().slice(0,10);

  // 2. 本地 txns 过滤本周操作
  let weekTxns = [];
  try{
    const all = (typeof loadTxns==='function')?loadTxns():[];
    weekTxns = all.filter(t=>{
      const tt = t.t || t.timestamp || (t.date ? new Date(t.date).getTime() : 0);
      return tt >= weekStart;
    });
  }catch{}

  // 3. 拉家庭聚合（取本周收益）
  let familyData = null;
  try{
    const r = await fetch(API_BASE+'/family/portfolio-summary?userId='+encodeURIComponent(getProfileId()),{signal:AbortSignal.timeout(10000)});
    if(r.ok) familyData = await r.json();
  }catch{}

  // 4. 拉本周大盘走势（用现有 timing/news 摘要）
  let marketData = null;
  try{
    const r = await fetch(API_BASE+'/news?days=7',{signal:AbortSignal.timeout(8000)});
    if(r.ok) marketData = await r.json();
  }catch{}

  // 5. 渲染卡片
  const fmt = v => Math.abs(v).toLocaleString('zh-CN',{maximumFractionDigits:0});
  const sign = v => v>=0?'+':'';
  const colorPnl = v => v>0 ? 'var(--color-bull,#FF6B6B)' : v<0 ? 'var(--color-bear,#00E5A0)' : 'var(--text2)';

  // 头部：本周日期 + 总盈亏
  const totalPnl = familyData?.familyTotal?.pnl ?? familyData?.familyNetWorth?.pnl ?? null;
  const totalPnlPct = familyData?.familyTotal?.pnlPct ?? null;

  const headerHTML = `<div style="padding:12px;background:linear-gradient(135deg,rgba(99,102,241,.1),rgba(168,85,247,.05));border:1px solid rgba(99,102,241,.18);border-radius:12px;margin-bottom:14px">
    <div style="display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:6px">
      <span style="font-size:12px;color:var(--text2)">${weekStartStr} ~ ${todayStr}（本周）</span>
      <span style="font-size:10px;color:var(--text-tertiary,#7A8499)">第 ${_weekOfYear(now)} 周</span>
    </div>
    ${totalPnl!=null?`<div style="font-size:13px;color:var(--text2)">家庭浮动盈亏</div>
    <div style="font-size:24px;font-weight:700;color:${colorPnl(totalPnl)}">${sign(totalPnl)}${fmt(totalPnl)}${totalPnlPct!=null?`<span style="font-size:14px;margin-left:6px">(${sign(totalPnlPct)}${totalPnlPct.toFixed(2)}%)</span>`:''}</div>`:'<div style="color:var(--text2);font-size:13px">⚠️ 家庭账户数据未加载</div>'}
  </div>`;

  // 操作记录
  const buyCount = weekTxns.filter(t=>t.type==='buy'||!t.type).length;
  const sellCount = weekTxns.filter(t=>t.type==='sell').length;
  const totalBuyAmt = weekTxns.filter(t=>t.type==='buy'||!t.type).reduce((s,t)=>s+(t.amount||0),0);
  const totalSellAmt = weekTxns.filter(t=>t.type==='sell').reduce((s,t)=>s+(t.amount||0),0);

  const opsHTML = `<div style="padding:12px;background:rgba(15,23,42,.4);border:1px solid rgba(148,163,184,.1);border-radius:12px;margin-bottom:14px">
    <div style="font-size:13px;font-weight:700;color:var(--text-default,#D8DCE5);margin-bottom:10px">📝 本周操作 (${weekTxns.length} 笔)</div>
    ${weekTxns.length === 0 ? '<div style="font-size:12px;color:var(--text2);text-align:center;padding:14px">本周无交易 — 持续定投或休息中</div>' : `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
        <div style="padding:8px;background:rgba(0,229,160,.06);border-radius:8px">
          <div style="font-size:11px;color:var(--text2)">买入 ${buyCount} 笔</div>
          <div style="font-size:16px;font-weight:700;color:var(--color-bull,#00E5A0)">¥${fmt(totalBuyAmt)}</div>
        </div>
        <div style="padding:8px;background:rgba(248,113,113,.06);border-radius:8px">
          <div style="font-size:11px;color:var(--text2)">卖出 ${sellCount} 笔</div>
          <div style="font-size:16px;font-weight:700;color:var(--color-bear,#F87171)">¥${fmt(totalSellAmt)}</div>
        </div>
      </div>
      <div style="font-size:11px;color:var(--text2);max-height:200px;overflow-y:auto">
        ${weekTxns.slice(0,12).map(t=>{
          const d = t.date || (t.t?new Date(t.t).toISOString().slice(0,10):'—');
          const tt = t.type || 'buy';
          return `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(148,163,184,.04)">
            <span>${d} · ${tt==='sell'?'📤 卖':'📥 买'} ${t.name||t.code}</span>
            <span style="color:${tt==='sell'?'#F87171':'#10B981'}">${tt==='sell'?'-':'+'}¥${fmt(t.amount||0)}</span>
          </div>`;
        }).join('')}
        ${weekTxns.length>12?`<div style="text-align:center;padding:6px;color:var(--text-tertiary)">... 还有 ${weekTxns.length-12} 笔</div>`:''}
      </div>
    `}
  </div>`;

  // 持仓 TOP 涨跌（家庭聚合）
  let topMovers = [];
  if(familyData?.members){
    familyData.members.forEach(m=>{
      (m.holdings||[]).forEach(h=>{
        if(h.pnlPct != null) topMovers.push({...h, owner: m.userId});
      });
    });
    topMovers.sort((a,b)=>Math.abs(b.pnlPct||0) - Math.abs(a.pnlPct||0));
  }
  const topGainers = topMovers.filter(h=>h.pnlPct>0).slice(0,3);
  const topLosers = topMovers.filter(h=>h.pnlPct<0).slice(0,3);

  const moversHTML = (topGainers.length || topLosers.length) ? `<div style="padding:12px;background:rgba(15,23,42,.4);border:1px solid rgba(148,163,184,.1);border-radius:12px;margin-bottom:14px">
    <div style="font-size:13px;font-weight:700;color:var(--text-default,#D8DCE5);margin-bottom:10px">📊 持仓涨跌榜</div>
    ${topGainers.length ? `<div style="margin-bottom:10px">
      <div style="font-size:11px;color:var(--color-bull,#00E5A0);margin-bottom:4px">📈 涨幅 TOP3</div>
      ${topGainers.map(h=>`<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:12px">
        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%">${h.name||h.code}</span>
        <span style="color:var(--color-bull,#00E5A0);font-weight:600">+${h.pnlPct.toFixed(2)}%</span>
      </div>`).join('')}
    </div>`:''}
    ${topLosers.length ? `<div>
      <div style="font-size:11px;color:var(--color-bear,#F87171);margin-bottom:4px">📉 跌幅 TOP3</div>
      ${topLosers.map(h=>`<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:12px">
        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%">${h.name||h.code}</span>
        <span style="color:var(--color-bear,#F87171);font-weight:600">${h.pnlPct.toFixed(2)}%</span>
      </div>`).join('')}
    </div>`:''}
  </div>` : '';

  // AI 复盘按钮（跳到 chat 自动注入 prompt）
  const aiPanel = `<div style="padding:14px;background:linear-gradient(135deg,rgba(168,85,247,.08),rgba(99,102,241,.04));border:1px solid rgba(168,85,247,.18);border-radius:12px;margin-bottom:14px">
    <div style="font-size:13px;font-weight:700;color:#C4B5FD;margin-bottom:6px">🤖 AI 复盘建议</div>
    <div style="font-size:11px;color:var(--text2);margin-bottom:10px;line-height:1.6">点击让 AI 基于本周操作 + 大盘走势给出复盘要点（不提供仓位/价格预测）</div>
    <button onclick="_askAIWeeklyReview()" class="mb-btn mb-btn--ai" style="width:100%;padding:10px">💬 让 AI 帮我复盘本周</button>
  </div>`;

  // 复盘 checklist
  const checklistHTML = `<div style="padding:12px;background:rgba(245,158,11,.05);border:1px solid rgba(245,158,11,.15);border-radius:12px">
    <div style="font-size:13px;font-weight:700;color:#F59E0B;margin-bottom:8px">📋 复盘 checklist（每周必看）</div>
    <div style="font-size:12px;color:var(--text-default,#D8DCE5);line-height:1.9">
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" style="cursor:pointer"> 本周操作是否符合纪律（无追高/止损）</label>
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" style="cursor:pointer"> 是否有偏离目标 ±5% 的持仓需调整 <button onclick="if(typeof showRebalanceModal==='function')showRebalanceModal()" style="font-size:10px;padding:2px 6px;border:1px solid var(--accent);border-radius:3px;background:transparent;color:var(--accent);cursor:pointer;margin-left:auto">查看</button></label>
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" style="cursor:pointer"> 大盘估值百分位是否变化（影响下周定投倍率）</label>
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" style="cursor:pointer"> 持仓中是否有需要止盈/止损的（>20% 或 <-15%）</label>
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" style="cursor:pointer"> 心愿单中是否有可加仓的标的</label>
    </div>
  </div>`;

  body.innerHTML = headerHTML + opsHTML + moversHTML + aiPanel + checklistHTML;
};

function _weekOfYear(d){
  const start = new Date(d.getFullYear(),0,1);
  const days = Math.floor((d - start) / 86400000);
  return Math.ceil((days + start.getDay() + 1) / 7);
}

window._askAIWeeklyReview = function(){
  // 跳到 chat 并注入 prompt
  if(typeof navigateTo==='function'){
    navigateTo('chat');
    setTimeout(()=>{
      const inp = document.getElementById('chatIn');
      if(inp){
        inp.value = '帮我复盘本周（基于我的持仓和操作）：1) 本周收益是否符合预期 2) 操作是否有改进空间 3) 下周需要关注的风险信号 4) 复盘 checklist 哪些值得重点检查。请基于我的持仓上下文输出，不要给具体仓位百分比和价格预测。';
        inp.focus();
      }
    }, 400);
  }
};
