/* =========================================================================
 * V6 补丁 7/7：Token 预算状态栏（Pro 模式）
 * 方式：劫持 renderNavigation()，在 #profileHeader 右侧追加 Token 用量指示器
 * 数据源：/api/health → llm_usage 对象
 * 显示：🟢ok / 🟡warning(70%) / 🔴critical(90%) + 今日花费 + 调用次数
 * 仅 Pro 模式可见；Simple 模式隐藏
 * ========================================================================= */
;(function(){
  'use strict';

  // 状态颜色映射
  const STATUS_MAP = {
    ok:       { dot: '🟢', color: '#10B981', bg: 'rgba(16,185,129,.1)',  border: 'rgba(16,185,129,.25)' },
    warning:  { dot: '🟡', color: '#F59E0B', bg: 'rgba(245,158,11,.1)',  border: 'rgba(245,158,11,.25)' },
    critical: { dot: '🔴', color: '#EF4444', bg: 'rgba(239,68,68,.1)',   border: 'rgba(239,68,68,.25)' },
    unknown:  { dot: '⚪', color: '#94A3B8', bg: 'rgba(148,163,184,.08)', border: 'rgba(148,163,184,.15)' }
  };

  let _lastBudget = null;   // 缓存上次数据，避免闪烁
  let _pollTimer = null;

  // --- 拉取预算数据（含 keys_status + per-user 花费）---
  async function _fetchBudget(){
    try {
      const d = await _v6Fetch('/health');
      if (d && d.llm_usage) {
        _lastBudget = d.llm_usage;
        // 附带 keys_status
        if (d.keys_status) _lastBudget._keys = d.keys_status;
      }
      // per-user 花费（只在详情弹窗需要，badge 不等它）
      const uid = (typeof currentUserId !== 'undefined' && currentUserId) ? currentUserId : 'LeiJiang';
      try {
        const u = await _v6Fetch('/llm-usage?userId=' + uid);
        if (u && _lastBudget) {
          _lastBudget._userModules = u.modules || {};
          _lastBudget._userCalls = u.daily_count || 0;
          _lastBudget._userLimit = u.daily_limit || 100;
          _lastBudget._userId = uid;
        }
      } catch(e2){ /* per-user 失败不阻塞 */ }
    } catch(e){ /* ignore */ }
    return _lastBudget;
  }

  // --- 渲染预算指示器 ---
  function _renderBudgetBadge(data){
    if (!data) return '';
    const s = STATUS_MAP[data.status] || STATUS_MAP.unknown;
    const cost = (data.today_cost_rmb || 0).toFixed(2);
    const budget = (data.daily_budget_rmb || 3).toFixed(0);
    const calls = data.today_calls || 0;
    const pct = (data.usage_pct || 0).toFixed(0);

    // 进度条宽度
    const barW = Math.min(100, Math.max(2, data.usage_pct || 0));

    return `<div id="v6TokenBudget" onclick="_v6ShowBudgetDetail()" style="
      display:inline-flex;align-items:center;gap:4px;
      font-size:10px;padding:2px 8px;border-radius:6px;
      background:${s.bg};border:1px solid ${s.border};
      color:${s.color};cursor:pointer;white-space:nowrap;
      transition:all .3s ease;position:relative;overflow:hidden;
    " title="Token 预算：¥${cost}/¥${budget} · ${calls}次调用 · ${pct}%">
      <div style="position:absolute;bottom:0;left:0;height:2px;width:${barW}%;background:${s.color};opacity:.4;border-radius:0 0 6px 6px;transition:width .5s ease"></div>
      <span>${s.dot}</span>
      <span style="font-weight:600">¥${cost}</span>
      <span style="opacity:.6">/${budget}</span>
    </div>`;
  }

  // --- 注入/更新到顶栏 ---
  function _injectBadge(data){
    if (!isProMode()) {
      // Simple 模式隐藏
      const existing = document.getElementById('v6TokenBudget');
      if (existing) existing.remove();
      return;
    }

    const hdr = document.getElementById('profileHeader');
    if (!hdr) return;

    const html = _renderBudgetBadge(data);
    const existing = document.getElementById('v6TokenBudget');

    if (existing) {
      // 更新已有元素
      const tmp = document.createElement('span');
      tmp.innerHTML = html;
      existing.replaceWith(tmp.firstElementChild);
    } else {
      // 首次注入：插到右侧按钮组中，Pro/Simple 按钮后面
      const rightSpan = hdr.querySelector('span:last-child');
      if (rightSpan) {
        const wrapper = document.createElement('span');
        wrapper.innerHTML = html;
        rightSpan.appendChild(wrapper.firstElementChild);
      }
    }
  }

  // --- 预算详情弹窗（点击 badge 触发）---
  window._v6ShowBudgetDetail = async function(){
    const data = _lastBudget || await _fetchBudget();
    if (!data) return;

    const s = STATUS_MAP[data.status] || STATUS_MAP.unknown;
    const cost = (data.today_cost_rmb || 0).toFixed(4);
    const budget = (data.daily_budget_rmb || 3).toFixed(2);
    const calls = data.today_calls || 0;
    const pct = (data.usage_pct || 0).toFixed(1);
    const remaining = Math.max(0, (data.daily_budget_rmb || 3) - (data.today_cost_rmb || 0)).toFixed(2);

    // 进度条
    const barPct = Math.min(100, data.usage_pct || 0);

    const o = document.createElement('div');
    o.className = 'modal-overlay';
    o.onclick = e => { if (e.target === o) o.remove(); };
    o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-width:360px">
      <div class="modal-handle"></div>
      <div class="modal-title">${s.dot} AI Token 预算</div>
      <div class="modal-subtitle">今日用量详情 · LLMGateway</div>

      <div style="margin:20px 0">
        <!-- 大数字 -->
        <div style="text-align:center;margin-bottom:16px">
          <div style="font-size:32px;font-weight:900;color:${s.color}">¥${cost}</div>
          <div style="font-size:13px;color:var(--text2);margin-top:4px">今日花费 / 日预算 ¥${budget}</div>
        </div>

        <!-- 进度条 -->
        <div style="background:var(--bg3);border-radius:8px;height:8px;overflow:hidden;margin:12px 0">
          <div style="height:100%;width:${barPct}%;background:${s.color};border-radius:8px;transition:width .5s ease"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text2)">
          <span>已用 ${pct}%</span>
          <span>剩余 ¥${remaining}</span>
        </div>

        <!-- 指标网格 -->
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:16px">
          <div style="background:var(--bg3);border-radius:10px;padding:12px;text-align:center">
            <div style="font-size:11px;color:var(--text2)">调用次数</div>
            <div style="font-size:20px;font-weight:800;color:var(--text1);margin-top:2px">${calls}</div>
            <div style="font-size:10px;color:var(--text2)">/ 100 日限</div>
          </div>
          <div style="background:var(--bg3);border-radius:10px;padding:12px;text-align:center">
            <div style="font-size:11px;color:var(--text2)">预算状态</div>
            <div style="font-size:20px;margin-top:2px">${s.dot}</div>
            <div style="font-size:10px;color:${s.color};font-weight:600">${
              data.status === 'ok' ? '正常' : data.status === 'warning' ? '预警 70%' : data.status === 'critical' ? '危险 90%' : '未知'
            }</div>
          </div>
        </div>

        <!-- 个人花费（我:¥xx）-->
        ${data._userId ? '<div style="margin-top:12px;display:grid;grid-template-columns:1fr 1fr;gap:8px">' +
          '<div style="background:rgba(99,102,241,.06);border-radius:10px;padding:10px 12px;text-align:center;border:1px solid rgba(99,102,241,.12)">' +
            '<div style="font-size:10px;color:var(--text2)">' + data._userId + ' 调用</div>' +
            '<div style="font-size:18px;font-weight:800;color:#6366F1;margin-top:2px">' + (data._userCalls || 0) + '</div>' +
            '<div style="font-size:10px;color:var(--text2)">/ ' + (data._userLimit || 100) + ' 日限</div>' +
          '</div>' +
          '<div style="background:rgba(99,102,241,.06);border-radius:10px;padding:10px 12px;text-align:center;border:1px solid rgba(99,102,241,.12)">' +
            '<div style="font-size:10px;color:var(--text2)">模块分布</div>' +
            '<div style="font-size:11px;color:var(--text1);margin-top:4px;line-height:1.5">' +
              (Object.keys(data._userModules || {}).length > 0 ?
                Object.entries(data._userModules).slice(0, 4).map(function(e){ return e[0] + ':' + e[1]; }).join('<br>') :
                '<span style="color:var(--text2)">今日暂无调用</span>') +
            '</div>' +
          '</div>' +
        '</div>' : ''}

        <!-- Key 健康状态 -->
        ${data._keys ? '<div style="margin-top:12px;padding:10px 12px;background:var(--bg3);border-radius:10px">' +
          '<div style="font-size:11px;color:var(--text2);margin-bottom:6px">🔑 API Key 健康</div>' +
          '<div style="display:flex;gap:12px">' +
            Object.entries(data._keys).map(function(e){
              var ok = e[1] === 'ok';
              return '<div style="display:flex;align-items:center;gap:4px">' +
                '<span style="font-size:12px">' + (ok ? '🟢' : '🔴') + '</span>' +
                '<span style="font-size:12px;font-weight:600;color:' + (ok ? '#10B981' : '#EF4444') + '">' + e[0] + '</span>' +
              '</div>';
            }).join('') +
          '</div>' +
        '</div>' : ''}

        <!-- 说明 -->
        <div style="margin-top:12px;padding:10px 12px;background:rgba(99,102,241,.06);border-radius:10px;border:1px solid rgba(99,102,241,.12)">
          <div style="font-size:11px;color:var(--text2);line-height:1.6">
            💡 <b>预算规则</b><br>
            · 日预算 ¥${budget}（月 ¥${((data.daily_budget_rmb || 3) * 30).toFixed(0)}）<br>
            · 70% 预警 → 降低非必要调用<br>
            · 90% 危险 → 仅允许紧急分析<br>
            · 突发限制：5 分钟内最多 10 次
          </div>
        </div>
      </div>

      <button onclick="this.closest('.modal-overlay').remove()" class="action-btn secondary" style="width:100%">关闭</button>
    </div>`;
    document.body.appendChild(o);
  };

  // --- 自动轮询（每 60 秒更新一次）---
  async function _pollUpdate(){
    const data = await _fetchBudget();
    _injectBadge(data);
  }

  // --- 安装：劫持 renderNav（实际渲染顶栏+底栏的函数）---
  function _install(){
    if (typeof renderNav !== 'function') return false;
    _v6Hijack('renderNav', async function(){
      // 延迟等 header DOM 就绪（renderNav 会重写 hdr.innerHTML）
      await new Promise(r => setTimeout(r, 100));
      const data = await _fetchBudget();
      _injectBadge(data);

      // 启动轮询（仅一次）
      if (!_pollTimer) {
        _pollTimer = setInterval(_pollUpdate, 60000);
      }
    });
    return true;
  }

  if (!_install()) {
    const t = setInterval(() => { if (_install()) clearInterval(t); }, 200);
    setTimeout(() => clearInterval(t), 5000);
  }

  // 模式切换时也要更新显示
  if (typeof toggleUIMode === 'function') {
    _v6Hijack('toggleUIMode', async function(){
      await new Promise(r => setTimeout(r, 200));
      _injectBadge(_lastBudget);
    });
  }

  console.log('[V6-7] token-budget badge patch installed');
})();
