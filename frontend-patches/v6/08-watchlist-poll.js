// ==== V6 Patch 08: 盯盘预警前端轮询 ====
// 功能：交易时段每 15 秒轮询 /api/watchlist/alerts，有预警时 toast 弹出
// 条件：Pro 模式 + 有持仓 + 交易时段（9:30-15:00 工作日）
// 空仓时返回 idle 状态，不弹预警

(function _v6_watchlist_poll() {
  let _watchTimer = null;
  let _shownAlerts = new Set(); // 避免重复弹同一条

  function isTradeHours() {
    const now = new Date();
    const day = now.getDay();
    if (day === 0 || day === 6) return false; // 周末
    const h = now.getHours(), m = now.getMinutes();
    const t = h * 60 + m;
    return t >= 9 * 60 + 25 && t <= 15 * 60 + 5; // 9:25~15:05（提前5分钟开始，延后5分钟收尾）
  }

  function showAlertToast(alert) {
    const key = `${alert.type}_${alert.code}`;
    if (_shownAlerts.has(key)) return;
    _shownAlerts.add(key);
    // 5 分钟后允许再次弹出同一预警
    setTimeout(() => _shownAlerts.delete(key), 5 * 60 * 1000);

    const colors = {
      danger: { bg: 'rgba(239,68,68,.15)', border: 'rgba(239,68,68,.4)', text: '#EF4444', icon: '🚨' },
      warning: { bg: 'rgba(245,158,11,.15)', border: 'rgba(245,158,11,.4)', text: '#F59E0B', icon: '⚠️' },
      info: { bg: 'rgba(59,130,246,.15)', border: 'rgba(59,130,246,.4)', text: '#3B82F6', icon: 'ℹ️' },
    };
    const c = colors[alert.level] || colors.info;

    const toast = document.createElement('div');
    toast.className = 'watchlist-alert-toast';
    toast.innerHTML = `
      <div style="display:flex;align-items:flex-start;gap:8px">
        <span style="font-size:18px;flex-shrink:0">${c.icon}</span>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:13px;color:${c.text};margin-bottom:2px">${alert.name || alert.code}</div>
          <div style="font-size:12px;color:var(--text-secondary,#94A3B8);line-height:1.4">${alert.message}</div>
        </div>
        <button onclick="this.parentElement.parentElement.remove()" style="background:none;border:none;color:var(--text-muted,#64748B);cursor:pointer;font-size:16px;padding:0;line-height:1">×</button>
      </div>
    `;
    toast.style.cssText = `
      position:fixed;top:${60 + document.querySelectorAll('.watchlist-alert-toast').length * 75}px;right:16px;z-index:10001;
      background:${c.bg};border:1px solid ${c.border};border-radius:12px;padding:12px 14px;
      max-width:320px;min-width:240px;backdrop-filter:blur(12px);
      box-shadow:0 4px 20px rgba(0,0,0,.15);
      animation:watchToastIn .3s ease-out;
      transition:opacity .3s,transform .3s;
    `;
    document.body.appendChild(toast);

    // 8 秒后自动消失
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(100%)';
      setTimeout(() => toast.remove(), 300);
    }, 8000);
  }

  async function pollAlerts() {
    if (!window._proMode) return; // Simple 模式不轮询
    if (!isTradeHours()) return; // 非交易时段不轮询

    const userId = localStorage.getItem('moneybag_current_profile') || 'default';
    try {
      const r = await fetch(`/api/watchlist/alerts?userId=${userId}`);
      if (!r.ok) return;
      const data = await r.json();

      // 空仓：total_holdings=0，不弹预警
      if (!data.total_holdings || data.total_holdings === 0) return;

      const alerts = data.alerts || [];
      if (alerts.length === 0) return;

      alerts.forEach(a => showAlertToast(a));
    } catch (e) {
      console.warn('[Watchlist] poll error:', e);
    }
  }

  function startPolling() {
    if (_watchTimer) return;
    pollAlerts(); // 立即查一次
    _watchTimer = setInterval(pollAlerts, 15000); // 每 15 秒
    console.log('[Watchlist] polling started (15s interval, trade hours only)');
  }

  function stopPolling() {
    if (_watchTimer) {
      clearInterval(_watchTimer);
      _watchTimer = null;
      console.log('[Watchlist] polling stopped');
    }
  }

  // 注入 CSS 动画
  const style = document.createElement('style');
  style.textContent = `
    @keyframes watchToastIn {
      from { opacity:0; transform:translateX(100%) }
      to { opacity:1; transform:translateX(0) }
    }
  `;
  document.head.appendChild(style);

  // 劫持 toggleUIMode：Pro 模式开轮询，Simple 模式停
  if (typeof window.toggleUIMode === 'function') {
    const _origToggle08 = window.toggleUIMode;
    window.toggleUIMode = function() {
      _origToggle08.apply(this, arguments);
      if (window._proMode) startPolling();
      else stopPolling();
    };
  }

  // 启动：如果当前是 Pro 模式就开始轮询
  if (window._proMode) {
    startPolling();
  }

  // 页面不可见时暂停，可见时恢复
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopPolling();
    } else if (window._proMode) {
      startPolling();
    }
  });
})();
