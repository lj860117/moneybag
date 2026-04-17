/* =========================================================================
 * V6 欠账 1/6：空仓首页市场概览
 * 目标：持仓为空时，不再是一片空白；展示"市场温度+入场时机+今日焦点"
 * 锚点：#dailyFocusSection（renderLanding 渲染出的每日焦点区域）
 * 依赖 API：/api/timing, /api/daily-signal, /api/news/impact
 * ========================================================================= */
;(function(){
  'use strict';

  async function _v6RenderEmptyLanding(){
    // 只在 landing 页且空仓时生效
    if (typeof currentPage !== 'undefined' && currentPage !== 'landing') return;
    if (!window._v6IsEmptyHoldings || !_v6IsEmptyHoldings()) return;

    // 找插入锚：优先 #dailyFocusSection，否则 #signalsSection
    const anchor = document.getElementById('dailyFocusSection')
                || document.getElementById('signalsSection');
    if (!anchor) return;

    // 已注入过？避免重复
    if (document.getElementById('v6EmptyHome')) return;

    const host = document.createElement('div');
    host.id = 'v6EmptyHome';
    host.style.cssText = 'margin-top:12px';
    host.innerHTML = _v6Skeleton('正在为你加载市场概览...');
    anchor.parentNode.insertBefore(host, anchor);

    // 并行拉三份数据
    const [timing, signal, impact] = await Promise.all([
      _v6Fetch('/timing'),
      _v6Fetch('/daily-signal'),
      _v6Fetch('/news/impact')
    ]);

    let html = '';

    // === 欢迎卡 ===
    html += `<div class="pnl-hero" style="background:linear-gradient(135deg,rgba(59,130,246,.08),rgba(16,185,129,.08));border:1px solid rgba(59,130,246,.15)">
      <div style="font-size:18px;font-weight:800;margin-bottom:6px">👋 欢迎来到钱袋子</div>
      <div style="font-size:13px;color:var(--text2);line-height:1.6">
        还没添加持仓？没关系 —— 先看看<span style="color:var(--accent);font-weight:600">今日市场</span>的温度，
        等到合适的时机再出手。
      </div>
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
        <button class="action-btn primary" style="flex:1;min-width:120px" onclick="if(typeof showAddStockModal==='function')showAddStockModal()">➕ 添加股票</button>
        <button class="action-btn secondary" style="flex:1;min-width:120px" onclick="if(typeof _nav==='function')_nav('stocks')">💰 管理持仓</button>
      </div>
    </div>`;

    // === 入场时机卡 ===
    if (timing && timing.signal) {
      const colorMap = {
        'STRONG_BUY':'var(--green)', 'BUY':'var(--green)',
        'HOLD':'#F59E0B', 'WAIT':'#F59E0B',
        'SELL':'var(--red)', 'STRONG_SELL':'var(--red)'
      };
      const labelMap = {
        'STRONG_BUY':'🔥 强烈建议入场', 'BUY':'🟢 适合入场',
        'HOLD':'🟡 可以观望', 'WAIT':'🟡 再等等',
        'SELL':'🟠 暂缓入场', 'STRONG_SELL':'🔴 不宜入场'
      };
      const c = colorMap[timing.signal] || '#F59E0B';
      const label = labelMap[timing.signal] || timing.signal;
      html += _v6Card('⏰ 入场时机', `
        <div style="display:flex;align-items:center;gap:12px">
          <div style="font-size:22px;font-weight:900;color:${c}">${label}</div>
          <div style="font-size:11px;color:var(--text2)">置信度 ${Math.round((timing.confidence||0)*100)}%</div>
        </div>
        <div style="font-size:13px;color:var(--text2);margin-top:6px;line-height:1.6">${timing.reason||timing.summary||''}</div>
        ${timing.suggestion ? `<div style="font-size:12px;margin-top:8px;padding:8px;background:var(--bg3);border-radius:8px">💡 ${timing.suggestion}</div>` : ''}
      `, { border: c });
    }

    // === 市场温度（来自 daily-signal）===
    if (signal && signal.overall) {
      const bgMap = {
        STRONG_BUY:'rgba(16,185,129,.10)', BUY:'rgba(16,185,129,.08)',
        HOLD:'rgba(245,158,11,.08)',
        SELL:'rgba(239,68,68,.08)', STRONG_SELL:'rgba(239,68,68,.10)'
      };
      const labelMap = {
        STRONG_BUY:'市场强势 🔥', BUY:'市场偏多 🟢',
        HOLD:'市场震荡 🟡', SELL:'市场偏空 🟠', STRONG_SELL:'市场疲弱 🔴'
      };
      html += `<div class="dashboard-card" style="background:${bgMap[signal.overall]||''};margin-top:8px">
        <div class="dashboard-card-title">🌡️ 市场温度 <span style="font-size:11px;color:var(--accent);font-weight:400">V4.5 · 12维</span></div>
        <div style="font-size:16px;font-weight:800;margin-top:4px">${labelMap[signal.overall]||signal.overall}</div>
        <div style="font-size:12px;color:var(--text2);margin-top:4px">综合得分 ${signal.score||0} · 置信度 ${Math.round(signal.confidence||0)}%</div>
        <div style="font-size:13px;margin-top:8px;line-height:1.6">${signal.summary||''}</div>
      </div>`;
    }

    // === 今日要闻（news/impact 前 3 条）===
    if (impact && Array.isArray(impact.items) && impact.items.length) {
      const rows = impact.items.slice(0, 3).map(n => {
        const lvl = n.impact_level || n.level || 'neutral';
        const c = lvl === 'bullish' || lvl === 'positive' ? 'var(--green)'
                : lvl === 'bearish' || lvl === 'negative' ? 'var(--red)' : 'var(--text2)';
        const tag = lvl === 'bullish' || lvl === 'positive' ? '利好' :
                    lvl === 'bearish' || lvl === 'negative' ? '利空' : '中性';
        return `<div style="padding:8px 0;border-bottom:1px solid var(--bg3);font-size:13px">
          <span style="display:inline-block;font-size:10px;padding:2px 6px;border-radius:4px;background:${c};color:#fff;margin-right:6px">${tag}</span>
          ${n.title || ''}
          ${n.affected_sectors ? `<div style="font-size:11px;color:var(--text2);margin-top:2px">影响：${(Array.isArray(n.affected_sectors)?n.affected_sectors:[n.affected_sectors]).join(' · ')}</div>` : ''}
        </div>`;
      }).join('');
      html += _v6Card('📰 今日要闻（AI 影响分析）', rows, { badge: 'Phase 0' });
    }

    if (!html) {
      host.innerHTML = `<div class="dashboard-card"><div style="text-align:center;padding:20px;color:var(--text2);font-size:13px">市场数据暂不可用，请稍后刷新</div></div>`;
    } else {
      host.innerHTML = html;
    }
  }

  // 劫持 renderLanding：原函数执行完后触发
  function _install(){
    if (typeof renderLanding !== 'function') return false;
    _v6Hijack('renderLanding', async function(){
      // 给原函数留点时间把 DOM 渲染稳
      setTimeout(_v6RenderEmptyLanding, 150);
    });
    return true;
  }

  if (!_install()) {
    // 如果 app.js 还没解析完，等一下
    const t = setInterval(() => { if (_install()) clearInterval(t); }, 200);
    setTimeout(() => clearInterval(t), 5000);
  }

  console.log('[V6-1] empty-landing patch installed');
})();
