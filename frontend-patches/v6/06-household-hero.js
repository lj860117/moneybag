/* =========================================================================
 * V6 欠账 6/6：家庭成员资产汇总 Hero 明细展示
 * 方式：劫持 loadOverviewHero()，在总览 hero 下方追加家庭汇总卡
 * 依赖 API：/api/household/summary
 * 条件：Pro 模式 + 有多个 profile 时显示
 * ========================================================================= */
;(function(){
  'use strict';

  async function _v6InjectHouseholdHero(){
    if (!isProMode()) return;
    const hero = document.getElementById('overviewHero');
    if (!hero) return;
    if (hero.querySelector('#v6HouseholdHero')) return;

    // 拉家庭汇总
    const d = await _v6Fetch('/household/summary');
    if (!d || !d.members || d.members.length < 2) return; // 只有一个人就不展示

    const host = document.createElement('div');
    host.id = 'v6HouseholdHero';
    host.style.cssText = 'margin-top:12px';

    // 家庭总资产 Hero
    const total = d.total_assets || d.totalAssets || 0;
    const totalPnl = d.total_pnl || d.totalPnl || 0;
    const pnlC = totalPnl >= 0 ? 'var(--green)' : 'var(--red)';

    let html = `<div class="pnl-hero" style="background:linear-gradient(135deg,rgba(168,85,247,.08),rgba(59,130,246,.08));border:1px solid rgba(168,85,247,.12)">
      <div class="pnl-label">👨‍👩‍👧‍👦 家庭总资产</div>
      <div class="pnl-total-value">¥${total.toLocaleString()}</div>
      ${totalPnl ? `<div class="pnl-change ${totalPnl >= 0 ? 'pos' : 'neg'}" style="color:${pnlC}">
        总盈亏 ${totalPnl >= 0 ? '+' : ''}¥${totalPnl.toFixed(0)}
      </div>` : ''}
    </div>`;

    // 成员明细
    html += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:8px">`;
    d.members.forEach(m => {
      const mPnl = m.pnl || m.totalPnl || 0;
      const mC = mPnl >= 0 ? 'var(--green)' : 'var(--red)';
      const assets = m.total_assets || m.totalAssets || m.marketValue || 0;
      const pct = total > 0 ? ((assets / total) * 100).toFixed(0) : 0;

      // 头像颜色
      const avatarColors = ['#3B82F6','#10B981','#F59E0B','#EC4899','#8B5CF6','#EF4444'];
      const ci = (m.name || '').charCodeAt(0) % avatarColors.length;

      html += `<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="if(typeof switchProfile==='function')switchProfile('${m.id || m.userId || ''}')">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
          <div style="width:32px;height:32px;border-radius:50%;background:${avatarColors[ci]};display:flex;align-items:center;justify-content:center;color:#fff;font-size:14px;font-weight:700">${(m.name || '?')[0]}</div>
          <div>
            <div style="font-size:13px;font-weight:700">${m.name || '未命名'}</div>
            <div style="font-size:11px;color:var(--text2)">占比 ${pct}%</div>
          </div>
        </div>
        <div style="font-size:16px;font-weight:800">¥${assets.toLocaleString()}</div>
        ${mPnl ? `<div style="font-size:12px;color:${mC};margin-top:2px">${mPnl >= 0 ? '+' : ''}¥${mPnl.toFixed(0)}</div>` : ''}
        <div style="margin-top:6px;background:var(--bg3);border-radius:4px;height:4px;overflow:hidden">
          <div style="height:100%;width:${pct}%;background:${avatarColors[ci]};border-radius:4px;transition:width .3s"></div>
        </div>
      </div>`;
    });
    html += '</div>';

    // 资产配置对比
    if (d.allocation_comparison) {
      html += `<div class="dashboard-card" style="margin-top:8px">
        <div class="dashboard-card-title">📊 家庭配置对比</div>
        <div style="font-size:12px;color:var(--text2);line-height:1.6">${
          typeof d.allocation_comparison === 'string' ? d.allocation_comparison
          : JSON.stringify(d.allocation_comparison)
        }</div>
      </div>`;
    }

    // 建议
    if (d.suggestions && d.suggestions.length) {
      html += `<div class="dashboard-card" style="margin-top:8px;border-left:3px solid var(--accent)">
        <div class="dashboard-card-title">💡 家庭资产建议</div>
        ${d.suggestions.map(s => `<div style="padding:6px 0;font-size:12px;border-bottom:1px solid var(--bg3);line-height:1.5">${typeof s === 'string' ? s : (s.text || s.content || '')}</div>`).join('')}
      </div>`;
    }

    host.innerHTML = html;
    hero.appendChild(host);
  }

  function _install(){
    if (typeof loadOverviewHero !== 'function') return false;
    _v6Hijack('loadOverviewHero', async function(){
      setTimeout(_v6InjectHouseholdHero, 200);
    });
    return true;
  }

  if (!_install()) {
    const t = setInterval(() => { if (_install()) clearInterval(t); }, 200);
    setTimeout(() => clearInterval(t), 5000);
  }

  console.log('[V6-6] household-hero patch installed');
})();
