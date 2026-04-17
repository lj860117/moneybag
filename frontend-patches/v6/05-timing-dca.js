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
