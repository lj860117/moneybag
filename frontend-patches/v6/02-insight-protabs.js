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
    const d = await _v6Fetch('/news/impact');
    if (!d || !d.items || !d.items.length) {
      el.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text2)">暂无深度影响数据</div>';
      return;
    }
    let html = `<div class="section-title">💥 新闻深度影响分析 <span style="font-size:11px;color:var(--accent);font-weight:400">Phase 0 · AI 驱动</span></div>`;
    d.items.forEach(item => {
      const lvl = item.impact_level || item.level || 'neutral';
      const c = lvl === 'bullish' || lvl === 'positive' ? 'var(--green)'
              : lvl === 'bearish' || lvl === 'negative' ? 'var(--red)' : '#F59E0B';
      const tag = lvl === 'bullish' || lvl === 'positive' ? '📈 利好'
               : lvl === 'bearish' || lvl === 'negative' ? '📉 利空' : '➖ 中性';
      const sectors = item.affected_sectors
        ? (Array.isArray(item.affected_sectors) ? item.affected_sectors : [item.affected_sectors]).join(' · ')
        : '';
      const score = item.impact_score != null ? item.impact_score : '';
      html += `<div class="dashboard-card" style="border-left:3px solid ${c};margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;align-items:flex-start">
          <div style="flex:1">
            <div style="font-size:14px;font-weight:700;line-height:1.5">${item.title || ''}</div>
            <div style="font-size:12px;color:var(--text2);margin-top:4px;line-height:1.6">${item.analysis || item.summary || ''}</div>
          </div>
          <div style="text-align:right;min-width:60px;margin-left:12px">
            <div style="font-size:12px;font-weight:700;color:${c}">${tag}</div>
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

    let html = `<div class="section-title">🛡️ 组合风险评估 <span style="font-size:11px;color:var(--accent);font-weight:400">Phase 0 · Pro</span></div>`;

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
        const val = metrics[m.k];
        if (val == null) return;
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
