/**
 * MoneyBag v9.3.0 · 共享组件库
 * ─────────────────────────────────────────────────────────
 * 9 个可复用 render 函数，返回 HTML 字符串。
 * 挂载在 window.MB.components，各页面直接调用。
 * 具体 HTML 在 PR-4 ~ PR-7 各页面 PR 时逐步细化。
 *
 * 用法示例：
 *   const html = MB.components.renderHeroNetWorth({ netWorth: 123456.78, ... });
 *   container.innerHTML = html;
 */

// 全局命名空间
window.MB = window.MB || {};
window.MB.components = {};

/* ──────────────────────────────────────────────────────────
 * 1. renderTopBar(user)
 *    顶部条：头像 + 问候语 + 主题切换 + 专业模式入口
 * ────────────────────────────────────────────────────────── */
MB.components.renderTopBar = function(user) {
  user = user || {};
  const name = user.name || '用户';
  const initial = user.initial || name.charAt(0).toUpperCase();
  const hour = new Date().getHours();
  const greeting = hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好';
  const now = new Date();
  const weekdays = ['SUNDAY','MONDAY','TUESDAY','WEDNESDAY','THURSDAY','FRIDAY','SATURDAY'];
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const dateStr = weekdays[now.getDay()] + ' · ' + months[now.getMonth()] + ' ' + now.getDate();

  return `
    <header class="mb-flex mb-flex--between" style="padding:6px 4px 14px">
      <div class="mb-flex mb-gap-4">
        <div class="mb-avatar mb-avatar--md mb-avatar--leijiang">${initial}</div>
        <div>
          <b style="font-size:13px">${greeting}，${name}</b>
          <div class="mb-eyebrow" style="margin-top:2px">${dateStr}</div>
        </div>
      </div>
      <div class="mb-flex mb-gap-2">
        <button class="mb-btn mb-btn--secondary mb-btn--sm" data-action="toggle-theme">${getThemeIcon()}</button>
        <button class="mb-pill ${isProMode() ? 'mb-pill--on' : ''}">${isProMode() ? '专业' : '简洁'}</button>
      </div>
    </header>`;
};

/* ──────────────────────────────────────────────────────────
 * 2. renderTabBar(active)
 *    5 Tab 底栏：首页 / 持仓 / 资讯 / AI / 资产
 * ────────────────────────────────────────────────────────── */
MB.components.renderTabBar = function(active) {
  active = active || 'home';
  const tabs = [
    { id: 'home',      icon: '🏠', label: '首页' },
    { id: 'portfolio', icon: '📊', label: '持仓' },
    { id: 'insight',   icon: '📰', label: '资讯' },
    { id: 'chat',      icon: '🤖', label: 'AI' },
    { id: 'assets',    icon: '💰', label: '资产' }
  ];
  const items = tabs.map(t => {
    const cls = t.id === active ? 'mb-tabbar__item mb-tabbar__item--active' : 'mb-tabbar__item';
    return `<a class="${cls}" data-tab="${t.id}">
      <span class="mb-tabbar__item__icon">${t.icon}</span>
      <span>${t.label}</span>
    </a>`;
  }).join('');
  return `<nav class="mb-tabbar">${items}</nav>`;
};

/* ──────────────────────────────────────────────────────────
 * 3. renderHeroNetWorth(data)
 *    净资产 Hero 卡
 *    data: { netWorth, decimal, delta, deltaLabel, splits: [{label, value, type}] }
 * ────────────────────────────────────────────────────────── */
MB.components.renderHeroNetWorth = function(data) {
  data = data || {};
  const netWorth = data.netWorth != null ? data.netWorth : '0';
  const decimal = data.decimal || '00';
  const delta = data.delta || '+¥0';
  const deltaLabel = data.deltaLabel || '今日 · 较昨日收盘';
  const deltaCls = String(delta).includes('-') ? 'mb-pill--bear' : 'mb-pill--bull';
  const splits = data.splits || [
    { label: '📈 投资', value: '¥0', type: '' },
    { label: '💵 现金', value: '¥0', type: '' },
    { label: '📋 负债', value: '-¥0', type: 'dn' }
  ];
  const splitsHtml = splits.map(s => {
    const valCls = s.type === 'dn' ? 'mb-hero__split-value mb-hero__split-value--dn'
                 : s.type === 'up' ? 'mb-hero__split-value mb-hero__split-value--up'
                 : 'mb-hero__split-value';
    return `<div class="mb-hero__split">
      <div class="mb-hero__split-label">${s.label}</div>
      <div class="${valCls}">${s.value}</div>
    </div>`;
  }).join('');

  return `
    <section class="mb-hero">
      <div class="mb-flex mb-flex--between">
        <span class="mb-hero__label">💰 家庭净资产</span>
        <span class="mb-pill mb-pill--secondary" data-action="toggle-money-mask">👁 隐藏</span>
      </div>
      <h1 class="mb-hero__num mb-numeric">¥${netWorth}<small>.${decimal}</small></h1>
      <div class="mb-hero__delta">
        <span class="mb-pill ${deltaCls}">▲ ${delta}</span>
        <span class="mb-text-tertiary">${deltaLabel}</span>
      </div>
      <div class="mb-hero__splits">${splitsHtml}</div>
    </section>`;
};

/* ──────────────────────────────────────────────────────────
 * 4. renderDualAccount(leijiang, buluogeli)
 *    双账户卡（家庭成员资产对比）
 *    leijiang/buluogeli: { name, initial, amount, percent }
 * ────────────────────────────────────────────────────────── */
MB.components.renderDualAccount = function(leijiang, buluogeli) {
  leijiang = leijiang || { name: 'LeiJiang', initial: 'L', amount: '¥0', percent: '0%' };
  buluogeli = buluogeli || { name: 'BuLuoGeLi', initial: 'B', amount: '¥0', percent: '0%' };

  function renderOne(person, avatarCls) {
    return `
      <div class="mb-card--ghost" style="padding:10px">
        <div class="mb-flex mb-gap-2 mb-mb-1">
          <div class="mb-avatar mb-avatar--xs ${avatarCls}">${person.initial}</div>
          <b style="font-size:11px">${person.name}</b>
        </div>
        <div class="mb-money mb-money--sm">${person.amount}</div>
        <div class="mb-caption">占比 ${person.percent}</div>
      </div>`;
  }

  return `
    <section class="mb-card--ghost">
      <div class="mb-flex mb-flex--between mb-mb-3">
        <b style="font-size:12px">👨‍👩 家庭账户</b>
        <span class="mb-text-tertiary" style="font-size:10px">管理 →</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        ${renderOne(leijiang, 'mb-avatar--leijiang')}
        ${renderOne(buluogeli, 'mb-avatar--buluogeli')}
      </div>
    </section>`;
};

/* ──────────────────────────────────────────────────────────
 * 5. renderAITip(text, actions)
 *    紫色 AI 提醒卡
 *    text: 提醒内容
 *    actions: [{ label, type: 'primary'|'secondary', action }]
 * ────────────────────────────────────────────────────────── */
MB.components.renderAITip = function(text, actions) {
  text = text || 'AI 管家暂无提醒';
  actions = actions || [
    { label: '稍后处理', type: 'secondary' },
    { label: '查看详情', type: 'primary' }
  ];
  const btnsHtml = actions.map(a => {
    const cls = a.type === 'primary' ? 'mb-btn mb-btn--primary mb-btn--sm' : 'mb-btn mb-btn--secondary mb-btn--sm';
    return `<button class="${cls}" data-action="${a.action || ''}">${a.label}</button>`;
  }).join('');

  return `
    <section class="mb-card--ai-tip">
      <div class="mb-flex mb-gap-3 mb-mb-3">
        <div class="mb-avatar mb-avatar--xs mb-avatar--ai">✨</div>
        <b style="font-size:12px;color:var(--color-ai-300)">AI 管家提醒</b>
      </div>
      <p style="font-size:var(--fs-sm);color:var(--text-default);line-height:var(--lh-normal);margin-bottom:var(--space-5)">${text}</p>
      <div class="mb-flex mb-gap-3">${btnsHtml}</div>
    </section>`;
};

/* ──────────────────────────────────────────────────────────
 * 6. renderQuickGrid(items)
 *    4 格快捷入口
 *    items: [{ icon, label, action }]
 * ────────────────────────────────────────────────────────── */
MB.components.renderQuickGrid = function(items) {
  items = items || [
    { icon: '📊', label: '资产配置' },
    { icon: '📈', label: '持仓' },
    { icon: '🌐', label: '市场全景' },
    { icon: '⚙️', label: '管理资产' }
  ];
  const cells = items.map(it => `
    <a class="mb-card--ghost" style="padding:12px;text-align:center;cursor:pointer" data-action="${it.action || ''}">
      <div style="font-size:20px;margin-bottom:4px">${it.icon}</div>
      <div style="font-size:var(--fs-xs);color:var(--text-secondary)">${it.label}</div>
    </a>`).join('');

  return `
    <section style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:var(--space-6) 0">
      ${cells}
    </section>`;
};

/* ──────────────────────────────────────────────────────────
 * 7. renderEmpty(icon, title, desc, ctas)
 *    空状态组件
 *    ctas: [{ label, type, action }]
 * ────────────────────────────────────────────────────────── */
MB.components.renderEmpty = function(icon, title, desc, ctas) {
  icon = icon || '📭';
  title = title || '暂无数据';
  desc = desc || '数据加载中或暂无记录';
  ctas = ctas || [{ label: '刷新', type: 'primary' }];
  const btnsHtml = ctas.map(c => {
    const cls = c.type === 'primary' ? 'mb-btn mb-btn--primary mb-btn--sm'
             : c.type === 'ai' ? 'mb-btn mb-btn--ai mb-btn--sm'
             : 'mb-btn mb-btn--secondary mb-btn--sm';
    return `<button class="${cls}" data-action="${c.action || ''}">${c.label}</button>`;
  }).join(' ');

  return `
    <div class="mb-empty">
      <div class="mb-empty__icon">${icon}</div>
      <div class="mb-empty__title">${title}</div>
      <div class="mb-empty__desc">${desc}</div>
      <div class="mb-flex mb-flex--center mb-gap-3">${btnsHtml}</div>
    </div>`;
};

/* ──────────────────────────────────────────────────────────
 * 8. renderFearGreedGauge(value)
 *    半圆恐慌贪婪仪表盘 SVG
 *    value: 0~100（0=极度恐慌，100=极度贪婪）
 * ────────────────────────────────────────────────────────── */
MB.components.renderFearGreedGauge = function(value) {
  value = Math.max(0, Math.min(100, value || 50));
  // 弧长计算：半圆弧总长约 251（r=80, 半圆 = PI*80 ≈ 251.3）
  const dashoffset = 251 * (1 - value / 100);
  // 指针圆点位置：角度从 -180°(左) 到 0°(右)
  const angle = -180 + (value / 100) * 180;
  const rad = angle * Math.PI / 180;
  const cx = (100 + 80 * Math.cos(rad)).toFixed(1);
  const cy = (90 + 80 * Math.sin(rad)).toFixed(1);
  // 标签
  const label = value <= 20 ? '极度恐慌' : value <= 40 ? '恐慌' : value <= 60 ? '中性' : value <= 80 ? '贪婪' : '极度贪婪';

  return `
    <div style="text-align:center">
      <svg viewBox="0 0 200 100" class="mb-fear-gauge" style="width:100%;max-width:200px">
        <defs>
          <linearGradient id="fgGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="#FF6B6B"/>
            <stop offset="50%" stop-color="#FFB755"/>
            <stop offset="100%" stop-color="#00E5A0"/>
          </linearGradient>
        </defs>
        <path d="M20,90 A80,80 0 0,1 180,90" fill="none" stroke="rgba(255,255,255,.06)" stroke-width="10"/>
        <path d="M20,90 A80,80 0 0,1 180,90" fill="none" stroke="url(#fgGrad)"
              stroke-width="10" stroke-dasharray="251" stroke-dashoffset="${dashoffset}"/>
        <circle r="5" cx="${cx}" cy="${cy}" fill="#FFB755" stroke="#fff" stroke-width="2"/>
      </svg>
      <div style="margin-top:var(--space-3)">
        <span class="mb-money mb-money--md">${value}</span>
        <div class="mb-caption" style="margin-top:2px">${label}</div>
      </div>
    </div>`;
};

/* ──────────────────────────────────────────────────────────
 * 9. renderMasterPicker(activeMaster)
 *    4 大师切换组件
 *    activeMaster: 'buffett'|'graham'|'lynch'|'taleb'
 * ────────────────────────────────────────────────────────── */
MB.components.renderMasterPicker = function(activeMaster) {
  activeMaster = activeMaster || 'buffett';
  const masters = [
    { id: 'buffett', emoji: '🎩', name: '巴菲特', desc: '价值',   bg: 'linear-gradient(135deg,#FF8A65,#E64A19)' },
    { id: 'graham',  emoji: '📚', name: '格雷厄姆', desc: '安全边际', bg: 'linear-gradient(135deg,#5C6BC0,#283593)' },
    { id: 'lynch',   emoji: '🔍', name: '林奇',   desc: '实地研究', bg: 'linear-gradient(135deg,#26A69A,#00695C)' },
    { id: 'taleb',   emoji: '🌪', name: '塔勒布',  desc: '反脆弱',  bg: 'linear-gradient(135deg,#7E57C2,#4527A0)' }
  ];
  const items = masters.map(m => {
    const active = m.id === activeMaster ? ' mb-master--active' : '';
    return `
      <button class="mb-master${active}" data-master="${m.id}" style="display:flex;flex-direction:column;align-items:center;gap:4px;padding:8px 4px;border:1px solid ${m.id === activeMaster ? 'var(--color-brand-500)' : 'var(--border-subtle)'};border-radius:var(--radius-lg);background:var(--bg-elevated);cursor:pointer">
        <div class="mb-avatar mb-avatar--md" style="background:${m.bg}">${m.emoji}</div>
        <b style="font-size:10px;color:var(--text-primary)">${m.name}</b>
        <small style="font-size:9px;color:var(--text-tertiary)">${m.desc}</small>
      </button>`;
  }).join('');

  return `
    <div class="mb-master-grid" style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px">
      ${items}
    </div>`;
};

/* ──────────────────────────────────────────────────────────
 * 主题切换辅助：确保 v9.3.0 新组件响应主题
 * 现有 app.js 的 applyTheme() 已经用
 * document.documentElement.setAttribute('data-theme', ...) 实现，
 * design-tokens.css 的 [data-theme="light"] 会自动响应。
 * 此处仅导出一个快捷方法供新组件使用。
 * ────────────────────────────────────────────────────────── */
MB.components.setTheme = function(theme) {
  // 委托给现有 applyTheme（app.js 已定义）
  if (typeof applyTheme === 'function') {
    applyTheme(theme);
  }
};

MB.components.getTheme = function() {
  return typeof _currentTheme !== 'undefined' ? _currentTheme : 'system';
};

/* ──────────────────────────────────────────────────────────
 * 通用工具：fetch 失败友好提示
 * ────────────────────────────────────────────────────────── */
MB.components.renderFetchError = function(title, retryFn) {
  title = title || '数据暂未开放';
  const retryBtn = retryFn ? `<button class="mb-btn mb-btn--secondary mb-btn--sm" onclick="${retryFn}">🔄 重试</button>` : '';
  return `<div class="mb-empty">
    <div class="mb-empty__icon">📡</div>
    <div class="mb-empty__title">${title}</div>
    <div class="mb-empty__desc">数据源连接失败或尚未开放，请稍后再试</div>
    ${retryBtn ? '<div style="margin-top:12px">' + retryBtn + '</div>' : ''}
  </div>`;
};
// 全局快捷方式
window.renderFetchError = MB.components.renderFetchError;

/* ──────────────────────────────────────────────────────────
 * 通用工具：轻量 Markdown → HTML（不引入外部库）
 * 支持：**bold** / *italic* / \n→<br> / - 列表 / ### 标题
 * ────────────────────────────────────────────────────────── */
MB.components.mdLite = function(text) {
  if (!text) return '';
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/^### (.+)$/gm, '<div style="font-size:14px;font-weight:700;margin:10px 0 4px">$1</div>')
    .replace(/^## (.+)$/gm, '<div style="font-size:15px;font-weight:700;margin:12px 0 6px">$1</div>')
    .replace(/^# (.+)$/gm, '<div style="font-size:16px;font-weight:800;margin:14px 0 6px">$1</div>')
    .replace(/^- (.+)$/gm, '<div style="padding-left:12px">• $1</div>')
    .replace(/\n/g, '<br>');
};
window.mdLite = MB.components.mdLite;

/* ──────────────────────────────────────────────────────────
 * 基金详情弹窗（Phase 1 + Phase 2）
 * 入口：showFundDetailModal(code, name)
 * ────────────────────────────────────────────────────────── */
// v9.5.123 Sprint 2: 保存止盈止损纪律线
window._saveDiscipline = async function(code) {
  const tp = parseFloat(document.getElementById('disciplineTP')?.value) || 0;
  const sl = parseFloat(document.getElementById('disciplineSL')?.value) || 0;
  if(!tp && !sl){alert('请至少设定一条纪律线');return}
  try{
    const r = await fetch(API_BASE+'/fund-holdings/discipline',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getProfileId(),code,take_profit:tp>0?tp:null,stop_loss:sl<0?sl:null})});
    const d = await r.json();
    if(d.ok){alert(`✅ 纪律线已设定\n止盈: +${tp}% | 止损: ${sl}%\n\n到达时会通过企微推送提醒`)}
    else{alert('设定失败: '+(d.error||'未知错误'))}
  }catch(e){alert('网络错误: '+e.message)}
};

window.__fundDetailPrefetchCache = window.__fundDetailPrefetchCache || new Map();
window.__fundDetailInflightCache = window.__fundDetailInflightCache || new Map();

function _fundDetailCacheKey(code, userId){
  return `${userId || ''}:${code || ''}`;
}

async function _fetchFundDetailPayload(code, userId, opts={}) {
  const key = _fundDetailCacheKey(code, userId);
  const force = !!opts.force;
  if(!force && window.__fundDetailPrefetchCache.has(key)) return window.__fundDetailPrefetchCache.get(key);
  if(!force && window.__fundDetailInflightCache.has(key)) return window.__fundDetailInflightCache.get(key);
  const detailUrl = API_BASE + '/fund/detail/' + code + '?userId=' + encodeURIComponent(userId || '');
  const request = fetch(detailUrl, { signal: AbortSignal.timeout(opts.timeoutMs || 30000) })
    .then(async (r) => {
      if (!r.ok) throw new Error('API ' + r.status);
      const d = await r.json();
      window.__fundDetailPrefetchCache.set(key, d);
      return d;
    })
    .finally(() => {
      window.__fundDetailInflightCache.delete(key);
    });
  window.__fundDetailInflightCache.set(key, request);
  return request;
}

window._prefetchFundDetail = async function(code, name, opts={}) {
  try {
    return await _fetchFundDetailPayload(code, getProfileId(), { timeoutMs: opts.timeoutMs || 20000, force: opts.force });
  } catch (e) {
    console.warn('[FundDetail] prefetch failed:', code, name, e);
    return null;
  }
};

window._clearFundDetailPrefetchCache = function() {
  window.__fundDetailPrefetchCache.clear();
  window.__fundDetailInflightCache.clear();
};

window.showFundDetailModal = async function(code, name) {
  const o = document.createElement('div');
  o.className = 'modal-overlay';
  o.onclick = e => { if (e.target === o) o.remove(); };
  o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:90vh;overflow-y:auto">
    <div class="modal-handle"></div>
    <div class="modal-title">📊 ${name || code}</div>
    <div class="modal-subtitle">${code} · 加载详情中...</div>
    <div id="fundDetailBody" style="padding:12px 0"><div style="text-align:center;padding:30px"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div><div style="font-size:12px;color:var(--text2)">正在获取基金经理和规模数据...</div></div></div>
    <div style="display:flex;gap:8px;margin-top:12px">
      <button class="mb-btn mb-btn--secondary mb-btn--block" onclick="showFundChart('${code}')">📈 K线</button>
      <button class="mb-btn mb-btn--ghost mb-btn--block" style="flex-shrink:0;width:auto;padding:0 14px" onclick="_showBuyMemoModal('${code}','${(name||'').replace(/'/g,"\\'")}')" title="记录本次买入理由，方便日后复盘">📝</button>
      <button class="mb-btn mb-btn--ai mb-btn--block" onclick="document.querySelector('.modal-overlay')?.remove();navigateTo('chat');setTimeout(()=>{const inp=document.getElementById('chatIn');if(inp){inp.value='帮我分析基金${code}的投资价值';inp.focus()}},300)">💬 问 AI</button>
    </div>
  </div>`;
  document.body.appendChild(o);

  // 异步加载详情（v9.9.4: 优先复用前 3 个预取缓存，其次复用同 code 的 inflight 请求）
  try {
    const d = await _fetchFundDetailPayload(code, getProfileId(), { timeoutMs: 30000 });
    const body = document.getElementById('fundDetailBody');
    if (!body) return;
    const isMyHolding = !!d.holding_relation;

    // v9.5.122/v9.8.7: 如果后端返回了持仓决策增强数据（advices/holding_relation），展示决策面板
    if (d.holding_relation && d.advices) {
      let advHtml = '<div style="margin-bottom:14px;padding:10px 12px;background:rgba(99,102,241,.04);border:1px solid rgba(99,102,241,.12);border-radius:8px">';
      advHtml += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><span style="font-size:11px;font-weight:600;color:var(--text-primary)">🎯 持仓决策辅助</span><span style="font-size:12px;font-weight:700;color:${d.action_direction==='减仓观望'?'#F59E0B':d.action_direction==='适量加仓'?'#10B981':'#9AA1AC'}">${d.action_direction||'持有观察'}</span></div>`;
      // 个人持仓摘要
      if(d.my_holding) {
        const my = d.my_holding;
        const pnlColor = (d.pnl_pct||0)>=0 ? 'var(--color-bull,#FF6B6B)' : 'var(--color-bear,#00E5A0)';
        advHtml += `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:8px;font-size:11px">
          <div style="text-align:center;padding:6px;background:rgba(255,255,255,.03);border-radius:4px"><div style="color:var(--text-tertiary);font-size:9px">持有份额</div><div style="font-weight:600">${my.shares.toFixed(2)}</div></div>
          <div style="text-align:center;padding:6px;background:rgba(255,255,255,.03);border-radius:4px"><div style="color:var(--text-tertiary);font-size:9px">成本均价</div><div style="font-weight:600">¥${my.avg_cost.toFixed(4)}</div></div>
          <div style="text-align:center;padding:6px;background:rgba(255,255,255,.03);border-radius:4px"><div style="color:var(--text-tertiary);font-size:9px">当前盈亏</div><div style="font-weight:600;color:${pnlColor}">${d.pnl_pct!=null?(d.pnl_pct>=0?'+':'')+d.pnl_pct.toFixed(1)+'%':'—'}</div></div>
        </div>`;
      }
      // 诊断指标卡
      const tags = [];
      if(d.nav_pct_label) tags.push(`<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:${(d.nav_percentile||0)>=70?'rgba(245,158,11,.12)':'rgba(134,239,172,.12)'};color:${(d.nav_percentile||0)>=70?'#F59E0B':'#86EFAC'}">${d.nav_pct_label}</span>`);
      if(d.industry_tag && d.industry_tag!=='其他') tags.push(`<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(99,102,241,.1);color:#A5B4FC">${d.industry_tag}</span>`);
      if(d.timing_label) tags.push(`<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(148,163,184,.1);color:#9AA1AC">${d.timing_label}</span>`);
      if(d.scale_billion) tags.push(`<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(148,163,184,.1);color:#9AA1AC">${d.scale_billion}亿</span>`);
      if(tags.length) advHtml += `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px">${tags.join('')}</div>`;
      // 建议列表
      if(d.advices.length) {
        advHtml += '<div style="margin-top:6px">';
        for(const a of d.advices) {
          const bgColor = a.type==='risk'?'rgba(248,113,113,.06)':a.type==='sell'?'rgba(245,158,11,.06)':a.type==='buy'?'rgba(134,239,172,.06)':'rgba(148,163,184,.04)';
          advHtml += `<div style="padding:5px 8px;margin-bottom:4px;border-radius:4px;background:${bgColor};font-size:11px;color:var(--text-primary)">${a.icon} ${a.text}</div>`;
        }
        advHtml += '</div>';
      }
      advHtml += '</div>';
      
      // v9.5.123: 走势预估 Layer 2 摘要面板（8维度+置信度）
      if(d.trend_direction) {
        const tDir = d.trend_direction;
        const tScore = d.trend_score || 0;
        const tConf = d.trend_confidence || 0;
        const tReason = d.trend_reason || '';
        const tConflict = d.trend_conflict || '';
        const tDims = d.trend_dimensions || {};
        const tColor = tDir==='up'?'#86EFAC':tDir==='down'?'#FCA5A5':'#9AA1AC';
        const tBg = tDir==='up'?'rgba(134,239,172,.06)':tDir==='down'?'rgba(252,165,165,.06)':'rgba(154,161,172,.04)';
        const tLabel = d.trend_label || '→ 震荡';
        
        advHtml += `<div style="margin-bottom:14px;padding:10px 12px;background:${tBg};border:1px solid ${tDir==='up'?'rgba(134,239,172,.2)':tDir==='down'?'rgba(252,165,165,.2)':'rgba(154,161,172,.15)'};border-radius:8px">`;
        advHtml += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <span style="font-size:13px;font-weight:700;color:${tColor}">${tLabel} ${tScore>0?'+':''}${tScore}分</span>
          <span style="font-size:10px;color:var(--text-tertiary)">置信度 ${tConf}%</span>
        </div>`;
        advHtml += `<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">核心驱动: ${tReason}</div>`;
        
        // 8维度分项
        if(Object.keys(tDims).length > 0) {
          advHtml += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:3px;font-size:10px">';
          for(const [dimName, dimData] of Object.entries(tDims)) {
            const dScore = dimData.score || 0;
            const dMax = dimData.max || 1;
            const dColor = dScore > 0 ? '#86EFAC' : dScore < 0 ? '#FCA5A5' : '#9AA1AC';
            advHtml += `<div style="display:flex;justify-content:space-between;padding:2px 4px;border-radius:3px;background:rgba(255,255,255,.02)"><span style="color:var(--text-tertiary)">${dimName}</span><span style="color:${dColor};font-weight:500">${dScore>0?'+':''}${dScore} / ±${dMax}</span></div>`;
          }
          advHtml += '</div>';
        }
        
        // 信号冲突提示
        if(tConflict) {
          advHtml += `<div style="margin-top:6px;padding:4px 8px;border-radius:4px;background:rgba(245,158,11,.08);font-size:10px;color:#F59E0B">⚠️ 信号冲突: ${tConflict}</div>`;
        }
        advHtml += '</div>';
      }
      
      // v9.5.123: 双因子智能定投建议面板
      if(d.dca && d.dca.multiplier != null) {
        const dca = d.dca;
        const dcaMult = dca.multiplier;
        const dcaColor = dcaMult>=1.5?'#86EFAC':dcaMult>=1.0?'#A5B4FC':dcaMult>=0.5?'#F59E0B':'#FCA5A5';
        const dcaBg = dcaMult>=1.3?'rgba(134,239,172,.05)':dcaMult>=0.8?'rgba(99,102,241,.04)':'rgba(252,165,165,.05)';
        advHtml += `<div style="margin-bottom:14px;padding:10px 12px;background:${dcaBg};border:1px solid ${dcaMult>=1.3?'rgba(134,239,172,.15)':'rgba(148,163,184,.12)'};border-radius:8px">`;
        advHtml += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <span style="font-size:11px;font-weight:600;color:var(--text-primary)">💡 智能定投建议</span>
          <span style="font-size:14px;font-weight:700;color:${dcaColor}">${dca.label}</span>
        </div>`;
        advHtml += `<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">${dca.advice}</div>`;
        // 因子详情
        if(dca.factors) {
          const f = dca.factors;
          advHtml += `<div style="display:flex;flex-wrap:wrap;gap:4px;font-size:10px">
            <span style="padding:2px 6px;border-radius:4px;background:rgba(148,163,184,.08);color:var(--text-tertiary)">走势:${f.trend_direction==='up'?'偏多':f.trend_direction==='down'?'偏空':'震荡'}</span>
            <span style="padding:2px 6px;border-radius:4px;background:rgba(148,163,184,.08);color:var(--text-tertiary)">估值:${f.valuation_tier}</span>
            <span style="padding:2px 6px;border-radius:4px;background:rgba(148,163,184,.08);color:var(--text-tertiary)">置信:${f.trend_confidence}%</span>
            <span style="padding:2px 6px;border-radius:4px;background:rgba(148,163,184,.08);color:var(--text-tertiary)">基准:${f.base_multiplier}x</span>
          </div>`;
        }
        advHtml += '</div>';
      }
      
      // v9.5.123 Sprint 2: 止盈止损纪律线设定
      if(isMyHolding) {
        advHtml += `<div style="margin-bottom:14px;padding:8px 12px;background:rgba(148,163,184,.04);border:1px solid rgba(148,163,184,.1);border-radius:8px">
          <div style="font-size:11px;font-weight:600;color:var(--text-primary);margin-bottom:6px">🎯 纪律线设定</div>
          <div style="display:flex;gap:8px;align-items:center;font-size:11px">
            <label style="color:var(--text-secondary)">止盈%</label>
            <input id="disciplineTP" type="number" value="${d._discipline_tp||30}" min="5" max="200" step="5" style="width:50px;padding:3px 6px;border-radius:4px;border:1px solid var(--bg3,#334155);background:var(--bg,#0f172a);color:var(--text,#f1f5f9);font-size:11px;text-align:center">
            <label style="color:var(--text-secondary)">止损%</label>
            <input id="disciplineSL" type="number" value="${d._discipline_sl||-20}" min="-80" max="-5" step="5" style="width:50px;padding:3px 6px;border-radius:4px;border:1px solid var(--bg3,#334155);background:var(--bg,#0f172a);color:var(--text,#f1f5f9);font-size:11px;text-align:center">
            <button onclick="_saveDiscipline('${code}')" style="padding:3px 10px;border-radius:4px;border:1px solid rgba(99,102,241,.3);background:rgba(99,102,241,.08);color:#A5B4FC;font-size:10px;cursor:pointer">保存</button>
          </div>
          <div style="font-size:10px;color:var(--text-tertiary);margin-top:4px">到达纪律线时会通过企微推送提醒你执行</div>
        </div>`;
      }
      
      body.innerHTML = advHtml;
      // 然后继续渲染通用详情（追加到下面）
    }

    let html = '';

    try {  // v9.5.28: 包一层 try，定位渲染期错误
    // v9.5.122: 兼容增强详情的 return_1y/return_3m 字段 + 通用详情的 returns 对象
    const returns = d.returns || {};
    const r1y = returns['1y'] != null ? returns['1y'] : d.return_1y;
    const r3y = returns['3y'] != null ? returns['3y'] : d.return_3y;
    const r3m = returns['3m'] != null ? returns['3m'] : d.return_3m;
    // 基本信息网格(v9.5.123: 扩展为2行6格)
    const _dd = d.max_drawdown;
    const _rank = d.category_rank;
    const _annual = d.annual_since_inception;
    html += `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px">
      <div class="mb-card--ghost" style="padding:10px;text-align:center" onclick="alert('近1年收益率\\n\\n这只基金过去1年涨了(或跌了)多少。\\n\\n怎么看：\\n• +30%以上 = 表现不错\\n• +10%~30% = 中规中矩\\n• 负数 = 这段时间在亏钱')"><div style="font-size:10px;color:var(--text-tertiary)">近1年</div><div style="font-size:16px;font-weight:700;color:${(r1y||0)>=0?'var(--color-bull,#00E5A0)':'var(--color-bear,#FF6B6B)'}">${r1y!=null?(r1y>0?'+':'')+r1y.toFixed(1)+'%':'—'}</div></div>
      <div class="mb-card--ghost" style="padding:10px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">${r3y!=null?'近3年':'近3月'}</div><div style="font-size:16px;font-weight:700;color:${(r3y||r3m||0)>=0?'var(--color-bull,#00E5A0)':'var(--color-bear,#FF6B6B)'}">${r3y!=null?(r3y>0?'+':'')+r3y.toFixed(1)+'%':(r3m!=null?(r3m>0?'+':'')+r3m.toFixed(1)+'%':'—')}</div></div>
      <div class="mb-card--ghost" style="padding:10px;text-align:center" onclick="alert('基金规模\\n\\n这只基金管理的总资金量。\\n\\n怎么看：\\n• 10-200亿 = 最佳区间(太小有清盘风险,太大船大难掉头)\\n• <5亿 = 小心!可能被清盘\\n• >500亿 = 规模太大,收益可能受限')"><div style="font-size:10px;color:var(--text-tertiary)">规模</div><div style="font-size:16px;font-weight:700">${d.scale_billion?d.scale_billion+'亿':'—'}</div></div>
      <div class="mb-card--ghost" style="padding:10px;text-align:center" onclick="alert('最大回撤\\n\\n过去1年里,从最高点到最低点最多亏过多少。\\n\\n通俗说：你买在最高点卖在最低点,最惨会亏多少。\\n\\n怎么看：\\n• <10% = 很稳(适合保守型)\\n• 10%-20% = 正常波动\\n• >20% = 波动大(心脏不好慎入)\\n• >30% = 过山车级别')"><div style="font-size:10px;color:var(--text-tertiary)">最大回撤</div><div style="font-size:16px;font-weight:700;color:${_dd&&_dd>20?'#F87171':_dd&&_dd>10?'#F59E0B':'#86EFAC'}">${_dd!=null?'-'+_dd+'%':'—'}</div></div>
      <div class="mb-card--ghost" style="padding:10px;text-align:center" onclick="alert('同类排名\\n\\n在所有同类型基金里,这只排第几。\\n\\n通俗说：全班考试排名。\\n\\n怎么看：\\n• 前10% = 学霸(同类最优秀)\\n• 前30% = 优等生\\n• 前50% = 中等\\n• 后50% = 不及格(不建议买)')"><div style="font-size:10px;color:var(--text-tertiary)">同类排名</div><div style="font-size:16px;font-weight:700;color:${_rank&&_rank.percentile>=80?'#86EFAC':_rank&&_rank.percentile>=50?'#F59E0B':'#F87171'}">${_rank?'前'+Math.round(100-_rank.percentile)+'%':'—'}</div></div>
      <div class="mb-card--ghost" style="padding:10px;text-align:center" onclick="alert('${d.sharpe_ratio!=null?"夏普比率(Sharpe)\\n\\n每承受1份风险能赚多少钱。\\n\\n通俗说：性价比。同样冒险,谁赚得更多。\\n\\n怎么看：\\n• >1.5 = 优秀(高性价比)\\n• 1.0~1.5 = 良好\\n• 0.5~1.0 = 一般\\n• <0.5 = 不值得冒这个险":"成立以来年化\\n\\n基金从成立到现在,平均每年赚多少。\\n\\n怎么看：\\n• >15% = 很厉害\\n• 8%-15% = 不错\\n• <5% = 还不如买货币基金"}')"><div style="font-size:10px;color:var(--text-tertiary)">${d.sharpe_ratio!=null?'夏普比率':'成立年化'}</div><div style="font-size:16px;font-weight:700;color:${d.sharpe_ratio!=null?(d.sharpe_ratio>=1.5?'#86EFAC':d.sharpe_ratio>=0.8?'#00E5A0':'#F59E0B'):((_annual||0)>=0?'#00E5A0':'#FF6B6B')}">${d.sharpe_ratio!=null?d.sharpe_ratio:(_annual!=null?(_annual>0?'+':'')+_annual+'%':'—')}</div></div>
    </div>`;

    // v9.5.123: Sortino + Alpha 风险指标行(带点击说明)
    if(d.sortino_ratio!=null || d.alpha_pct!=null){
      let _riskHtml='<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;font-size:11px">';
      if(d.sortino_ratio!=null){
        const stColor=d.sortino_ratio>=2?'#86EFAC':d.sortino_ratio>=1?'#00E5A0':'#F59E0B';
        _riskHtml+=`<span style="padding:3px 8px;border-radius:6px;background:rgba(148,163,184,.05);cursor:pointer" onclick="alert('Sortino比率(索提诺)\\n\\n和夏普类似,但更聪明——只看亏钱的波动,不管赚钱的波动。\\n\\n怎么看:\\n• >2.0 = 非常优秀\\n• 1.0~2.0 = 良好\\n• <1.0 = 下行风险偏大\\n\\n对比夏普：Sortino只算亏损的风险,更适合评估基金。')">Sortino <b style="color:${stColor}">${d.sortino_ratio}</b></span>`;
      }
      if(d.alpha_pct!=null){
        const alColor=d.alpha_pct>0?'#86EFAC':'#FCA5A5';
        _riskHtml+=`<span style="padding:3px 8px;border-radius:6px;background:rgba(148,163,184,.05);cursor:pointer" onclick="alert('Alpha(阿尔法/超额收益)\\n\\n基金经理到底有没有真本事?Alpha就是答案。\\n\\n通俗说：大盘涨了30%,你的基金涨了40%,多出来的10%就是Alpha。说明经理确实有两把刷子,不是靠运气。\\n\\n怎么看：\\n• >10% = 经理很厉害(选股能力强)\\n• 0~10% = 有点本事\\n• <0% = 不如直接买指数基金(经理拖后腿了)\\n\\n注意：Alpha用的基准是沪深300,不同类型基金的Alpha不能直接对比。')">Alpha <b style="color:${alColor}">${d.alpha_pct>0?'+':''}${d.alpha_pct}%</b></span>`;
      }
      if(d.sharpe_ratio!=null){
        _riskHtml+=`<span style="padding:3px 8px;border-radius:6px;background:rgba(148,163,184,.05);cursor:pointer" onclick="alert('夏普比率(Sharpe)\\n\\n每承受1份风险能赚多少钱。\\n\\n通俗说：你冒了这么大风险(波动),值不值?夏普越高说明同样冒险赚得越多。\\n\\n怎么看：\\n• >1.5 = 优秀\\n• 1.0~1.5 = 良好\\n• 0.5~1.0 = 一般\\n• <0.5 = 性价比差')">Sharpe <b>${d.sharpe_ratio}</b></span>`;
      }
      _riskHtml+='</div>';
      html+=_riskHtml;
    }

    // v9.5.123: 季度持仓变动
    if(d.portfolio_changes && d.portfolio_changes.length){
      html += `<div style="font-size:11px;margin-bottom:10px;padding:6px 10px;background:rgba(99,102,241,.04);border-radius:6px">
        <span style="font-weight:600">📋 本季变动: </span>
        ${d.portfolio_changes.map(c=>`<span style="color:${c.action==='新增'?'#86EFAC':'#FCA5A5'}">${c.emoji}${c.name||c.symbol}(${c.action})</span>`).join(' ')}
      </div>`;
    }

    // v9.5.123: 分红历史 + 基金类型/成立/公司 (一行简要信息)
    const _divInfo = d.dividend;
    const _metaItems = [];
    if(d.fund_type) _metaItems.push(d.fund_type);
    if(d.company) _metaItems.push(d.company);
    if(d.founded) _metaItems.push('成立 '+d.founded.slice(0,10));
    if(_divInfo && _divInfo.has_history) _metaItems.push(`分红${_divInfo.history_count}次(最近${_divInfo.history_label||''})`);
    else if(_divInfo) _metaItems.push('暂无分红记录');
    if(_metaItems.length) {
      html += `<div style="font-size:11px;color:var(--text-tertiary);margin-bottom:12px;line-height:1.7;padding:8px 10px;background:rgba(148,163,184,.04);border-radius:8px">${_metaItems.join(' · ')}</div>`;
    }

    // v9.5.128: 场内/场外标识 + 购买渠道引导
    {
      const name = d.name || '';
      const code = d.code || '';
      const fundType = (d.fund_type || '').toLowerCase();
      // 判断场内基金：LOF/ETF/ETF联接(LOF后缀) / 代码5开头(部分沪市LOF) / 基金类型含ETF
      const isLOF = name.includes('(LOF)') || name.includes('（LOF）') || code.startsWith('5');
      const isETF = name.includes('ETF') && !name.includes('联接');
      const isExchange = isLOF || isETF;

      if(isExchange) {
        // 场内基金
        html += `<div style="padding:10px 12px;margin-bottom:10px;background:rgba(245,158,11,.04);border:1px solid rgba(245,158,11,.15);border-radius:10px;font-size:11px">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
            <span style="font-size:12px;font-weight:700;color:#F59E0B">🏦 场内基金</span>
            <span style="font-size:10px;padding:1px 6px;border-radius:4px;background:rgba(245,158,11,.12);color:#F59E0B">${isETF?'ETF':'LOF'}</span>
          </div>
          <div style="color:var(--text-secondary);line-height:1.8;font-size:11px">
            <div>📱 <b>支付宝/天天基金</b>：搜代码 ${code}，按净值场外申购（T+1确认）</div>
            <div>📈 <b>证券账户</b>（如华泰/招商等）：像买股票一样场内实时交易，可能有溢/折价</div>
            ${isETF?'<div style="margin-top:4px;font-size:10px;color:var(--text-tertiary)">💡 ETF 只能通过证券账户场内买卖，不支持支付宝场外申购</div>':'<div style="margin-top:4px;font-size:10px;color:var(--text-tertiary)">💡 LOF 两种方式都能买，支付宝方便，证券账户费率可能更低</div>'}
          </div>
        </div>`;
      } else {
        // 场外普通基金
        html += `<div style="padding:8px 12px;margin-bottom:10px;background:rgba(99,102,241,.03);border:1px solid rgba(99,102,241,.1);border-radius:10px;font-size:11px">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
            <span style="font-size:12px;font-weight:700;color:#A5B4FC">📱 场外基金</span>
          </div>
          <div style="color:var(--text-secondary);line-height:1.8">
            <div>支付宝 / 天天基金 / 蚂蚁基金均可购买，搜代码 <b>${code}</b></div>
            <div style="font-size:10px;color:var(--text-tertiary)">按每日净值申购赎回，T+1或T+3到账</div>
          </div>
        </div>`;
      }
    }

    // 购买限制信息
    if (d.purchase && d.purchase.available) {
      const p = d.purchase;
      // v9.5.120: 直接展示原始状态，不做"可买/不可买"二元判断（各平台状态不同步）
      const rawStatus = p.purchase_status || '未知';
      const _isSuspended = (s) => s && (s.includes('暂停') || s.includes('封闭'));
      const canBuy = !_isSuspended(rawStatus);
      const canRedeem = p.redeem_status && !_isSuspended(p.redeem_status);
      const isQdiiLimit = p.daily_limit && p.daily_limit <= 1000;
      const limitWarn = isQdiiLimit
        ? `<span style="color:#F87171;font-weight:600">⚠️ 每日限购${p.daily_limit >= 1000 ? (p.daily_limit/1000)+'千' : p.daily_limit}元（QDII限额）</span>`
        : p.daily_limit ? `每日限额${p.daily_limit >= 10000 ? (p.daily_limit/10000)+'万' : p.daily_limit}元` : '';
      html += `<div style="padding:10px 12px;margin-bottom:12px;background:${canBuy?'rgba(16,185,129,.05)':'rgba(239,68,68,.05)'};border:1px solid ${canBuy?'rgba(16,185,129,.15)':'rgba(239,68,68,.2)'};border-radius:10px;font-size:11px">
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px">
          <div style="display:flex;gap:8px;align-items:center">
            <span style="font-weight:700;color:${canBuy?'#10B981':'#F59E0B'}">${canBuy?'🟢 '+rawStatus:'⚠️ '+rawStatus+'(以实际平台为准)'}</span>
            <span style="color:${canRedeem?'#10B981':'#F87171'}">${canRedeem?'可赎回':'暂停赎回'}</span>
          </div>
          <div style="color:var(--text-tertiary);display:flex;gap:8px;flex-wrap:wrap">
            ${p.min_buy!=null?`<span>起购 <b>${p.min_buy}元</b></span>`:''}
            ${p.fee_rate!=null?`<span>申购费 <b>${p.fee_rate}%</b></span>`:''}
          </div>
        </div>
        ${limitWarn?`<div style="margin-top:6px">${limitWarn}</div>`:''}
        <div style="margin-top:4px;font-size:10px;color:var(--text-tertiary)">💡 数据来源：天天基金 · 实际申购状态以支付宝/购买平台为准</div>
      </div>`;
    }

    // 基金经理卡
    if (d.manager) {
      const m = d.manager;
      const focusHtml = m.focus_industries && m.focus_industries.length
        ? '<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:6px">' + m.focus_industries.map(ind => '<span style="font-size:10px;padding:2px 8px;border-radius:10px;background:rgba(99,102,241,.1);color:#818CF8">' + ind + '</span>').join('') + '</div>'
        : '';
      // v9.5.126: tenure_note=estimate 时显示"≥X年（估算）"不显示不详
      const tenureText = !m.tenure_years ? '任期不详'
        : m.tenure_years < 1 ? '任期不足1年'
        : m.tenure_note === 'estimate' ? `任期 ≥${m.tenure_years} 年（估算）`
        : `任期 ${m.tenure_years} 年`;
      const scaleText = d.scale_billion ? ` · 基金规模 ${d.scale_billion}亿` : '';
      // 换人风险提示
      const managerRisk = !m.tenure_years
        ? `<div style="margin-top:8px;padding:8px 10px;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);border-radius:8px;font-size:11px;color:#F59E0B">⚠️ 基金经理任期不详，历史业绩可能不具参考价值，请谨慎</div>`
        : m.tenure_years < 1
        ? `<div style="margin-top:8px;padding:8px 10px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:8px;font-size:11px;color:var(--red)">🔴 基金经理任期不足1年，3年/5年收益历史为前任经理创造，现任能否复制尚未验证</div>`
        : m.tenure_years < 2
        ? `<div style="margin-top:8px;padding:8px 10px;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);border-radius:8px;font-size:11px;color:#F59E0B">⚠️ 基金经理任期不足2年，历史业绩仅部分由现任创造，需关注风格一致性</div>`
        : '';
      html += `<div class="mb-card--ghost" style="padding:12px;margin-bottom:14px">
        <div class="mb-flex mb-gap-3 mb-mb-3">
          <div class="mb-avatar mb-avatar--md" style="background:linear-gradient(135deg,#5C6BC0,#283593)">👤</div>
          <div>
            <b style="font-size:14px">${m.name}</b>
            <div style="font-size:11px;color:var(--text-tertiary);margin-top:2px">${tenureText}${scaleText}</div>
          </div>
        </div>
        ${focusHtml?'<div style="font-size:10px;color:var(--text-tertiary);margin-bottom:4px">🎯 侧重行业</div>'+focusHtml:''}
        ${m.resume?'<div style="font-size:11px;color:var(--text-secondary);line-height:1.5;margin-top:6px">'+m.resume+'</div>':'<div style="font-size:11px;color:var(--text-tertiary);margin-top:6px;opacity:0.6">（暂无简历信息）</div>'}
        ${managerRisk}
        <button class="mb-btn mb-btn--ghost mb-btn--sm" style="margin-top:8px;width:100%" onclick="loadManagerTrack('${code}','${m.name}')">📊 查看规模-业绩对照</button>
        <div id="managerTrackArea"></div>
      </div>`;
    }

    // 重仓持仓（静态）
    if (d.top_holdings && d.top_holdings.length) {
      html += `<div style="font-size:12px;font-weight:700;margin-bottom:6px">🏦 重仓持仓 TOP5</div>`;
      html += d.top_holdings.map(h => `<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;font-size:12px;border-bottom:1px solid var(--border-subtle,rgba(255,255,255,.04))"><div><span style="font-weight:600">${h.name||''}</span> <span style="color:var(--text-tertiary);font-size:10px">${h.symbol}</span>${h.industry?'<span style="font-size:9px;margin-left:4px;padding:1px 5px;border-radius:8px;background:rgba(99,102,241,.08);color:#818CF8">'+h.industry+'</span>':''}</div><span style="color:var(--text-tertiary);font-weight:600">${h.ratio?h.ratio+'%':''}</span></div>`).join('');
      // v9.5.123 P3-3: 行业集中度迷你饼图
      const _indMap={};
      d.top_holdings.forEach(h=>{if(h.industry){_indMap[h.industry]=(_indMap[h.industry]||0)+1}});
      const _indEntries=Object.entries(_indMap).sort((a,b)=>b[1]-a[1]);
      if(_indEntries.length>1){
        const _pieColors=['#6366F1','#F59E0B','#10B981','#F87171','#8B5CF6'];
        let _pieHtml='<div style="display:flex;align-items:center;gap:10px;margin-top:8px;padding:6px 8px;background:rgba(99,102,241,.03);border-radius:6px"><svg width="36" height="36" viewBox="0 0 36 36">';
        let _startAngle=0;const _total=d.top_holdings.length;
        _indEntries.forEach(([ind,cnt],i)=>{
          const pct=cnt/_total;const angle=pct*360;
          const endAngle=_startAngle+angle;
          const x1=18+16*Math.cos((_startAngle-90)*Math.PI/180);
          const y1=18+16*Math.sin((_startAngle-90)*Math.PI/180);
          const x2=18+16*Math.cos((endAngle-90)*Math.PI/180);
          const y2=18+16*Math.sin((endAngle-90)*Math.PI/180);
          const largeArc=angle>180?1:0;
          _pieHtml+=`<path d="M18 18 L${x1} ${y1} A16 16 0 ${largeArc} 1 ${x2} ${y2} Z" fill="${_pieColors[i%5]}"/>`;
          _startAngle=endAngle;
        });
        _pieHtml+='</svg><div style="font-size:10px;color:var(--text-secondary);line-height:1.6">';
        _indEntries.slice(0,3).forEach(([ind,cnt],i)=>{_pieHtml+=`<span style="color:${_pieColors[i%5]}">${ind}(${cnt}只)</span> `});
        _pieHtml+='</div></div>';
        html+=_pieHtml;
      }
      html += '<div style="margin-bottom:14px"></div>';
    }

    // 季度持仓（天天基金，动态加载）
    html += `<div id="fundPortfolioArea_${code}" style="margin-bottom:12px">
      <button class="mb-btn mb-btn--ghost mb-btn--sm" style="width:100%" onclick="loadFundPortfolio('${code}')">📋 加载季度前十持仓</button>
    </div>`;

    // 其他信息
    html += `<div style="font-size:11px;color:var(--text-tertiary);text-align:center">费率 ${d.fee||'—'} · 数据来源 ${d.source||'tushare'} · ${d.updatedAt||''}</div>`;

    // v9.5.124: 多模型AI评分区域（异步加载，不阻塞主详情）
    html += `<div id="aiScoreArea_${code}" style="margin-top:14px;padding:10px 12px;background:rgba(139,92,246,.03);border:1px dashed rgba(139,92,246,.2);border-radius:8px">
      <div style="display:flex;align-items:center;gap:6px;cursor:pointer" onclick="_loadAiScore('${code}',this.parentElement)">
        <span style="font-size:11px;font-weight:600;color:#A78BFA">🤖 AI 多模型评审</span>
        <span style="font-size:10px;color:var(--text-tertiary)">DeepSeek + 豆包 + 千问 各自打分</span>
        <span style="margin-left:auto;font-size:10px;color:#A78BFA">点击加载 →</span>
      </div>
    </div>`;

    // v9.5.122: 如果已有决策面板（持仓基金），追加而非覆盖
    if(isMyHolding && body.innerHTML.includes('持仓决策辅助')){
      body.innerHTML += html;
    } else {
      body.innerHTML = html;
    }
    } catch (renderErr) {
      console.error('[FundDetail] render error:', renderErr, 'data:', d);
      body.innerHTML = `<div style="text-align:center;padding:20px;color:var(--text2);font-size:12px">
        <div style="font-size:32px;margin-bottom:8px">⚠️</div>
        <div style="margin-bottom:6px">基金详情渲染失败</div>
        <div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-bottom:8px">数据已收到但前端解析出错</div>
        <details style="text-align:left;font-size:10px;background:rgba(239,68,68,.05);padding:8px;border-radius:6px"><summary style="cursor:pointer;color:var(--red)">展开错误详情</summary><pre style="white-space:pre-wrap;word-break:break-all;margin-top:6px">${(renderErr.message||renderErr).toString().slice(0,200)}</pre></details>
        <div style="margin-top:8px"><b style="color:var(--text-default,#D8DCE5)">${d.name||code}</b></div>
        <div style="font-size:11px;margin-top:4px">最新净值: ¥${d.nav||'—'} · 规模: ${d.scale_billion?d.scale_billion+'亿':'—'}</div>
        <div style="font-size:11px;margin-top:2px">近1年: ${d.returns?.['1y']!=null?(d.returns['1y']>0?'+':'')+d.returns['1y']+'%':'—'} · 近3年: ${d.returns?.['3y']!=null?(d.returns['3y']>0?'+':'')+d.returns['3y']+'%':'—'}</div>
      </div>`;
    }
  } catch (e) {
    console.error('[FundDetail] fetch error:', e);
    const body = document.getElementById('fundDetailBody');
    if (body) body.innerHTML = `<div style="text-align:center;padding:20px;color:var(--text2);font-size:12px">
      <div style="font-size:32px;margin-bottom:8px">📡</div>
      <div>基金详情加载失败</div>
      <div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:6px">${e.name==='TimeoutError'?'后端响应超时（>15秒）':'网络或服务器异常'}</div>
      <div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:2px">代码: ${code}</div>
      <button onclick="document.querySelector('.modal-overlay')?.remove();showFundDetailModal('${code}','${(name||'').replace(/'/g,'')}')" style="margin-top:10px;padding:6px 14px;border-radius:6px;border:1px solid rgba(99,102,241,.3);background:rgba(99,102,241,.08);color:#818CF8;font-size:11px;cursor:pointer">🔄 重试</button>
    </div>`;
  }
};

/* 基金季度持仓加载 */
window.loadFundPortfolio = async function(code) {
  const area = document.getElementById('fundPortfolioArea_' + code);
  if (!area) return;
  area.innerHTML = '<div style="text-align:center;padding:12px;font-size:12px;color:var(--text2)">📡 加载季度持仓数据...</div>';
  try {
    const userId = typeof getUserId === 'function' ? getUserId() : '';
    const r = await fetch(API_BASE + '/fund/portfolio/' + code + (userId ? '?userId=' + userId : ''), {signal: AbortSignal.timeout(20000)});
    const d = await r.json();
    if (!d.available) {
      area.innerHTML = '<div style="font-size:11px;color:var(--text2);text-align:center;padding:8px">' + (d.reason||'暂无持仓数据') + '</div>';
      return;
    }
    const overlapCodes = new Set(d.overlap_codes || []);
    const overlapHtml = d.overlap_count > 0
      ? `<div style="margin-bottom:6px;font-size:11px;color:#F59E0B">⚠️ 与你持股重叠 ${d.overlap_count} 只，持该基金可能加大集中度</div>`
      : '';
    area.innerHTML = `
      <div style="font-size:12px;font-weight:700;margin-bottom:8px">📋 季度前十持仓 <span style="font-size:10px;color:var(--text2);font-weight:400">· ${d.data_source}</span></div>
      ${overlapHtml}
      ${d.holdings.map((h,i) => `<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;font-size:12px;border-bottom:1px solid rgba(148,163,184,.06)">
        <div style="display:flex;align-items:center;gap:6px">
          <span style="color:var(--text2);font-size:10px;min-width:14px">${h.rank||i+1}</span>
          <span style="font-weight:600${overlapCodes.has(h.code)?';color:#F59E0B':''}">${h.name||h.code}</span>
          ${overlapCodes.has(h.code)?'<span style="font-size:9px;color:#F59E0B">⚠️你也持有</span>':''}
        </div>
        <span style="color:var(--text2);font-size:11px">${h.pct!=null?h.pct+'%':'--'}</span>
      </div>`).join('')}
      <div style="font-size:10px;color:var(--text2);margin-top:6px;opacity:0.6">数据来源季度报告，存在1-3个月滞后</div>`;
  } catch(e) {
    area.innerHTML = '<div style="font-size:11px;color:var(--text2);text-align:center;padding:8px">加载失败，请稍后重试</div>';
  }
};

/* 经理规模-业绩对照（Phase 2 前端） */
window.loadManagerTrack = async function(code, managerName) {
  const area = document.getElementById('managerTrackArea');
  if (!area) return;
  area.innerHTML = '<div style="text-align:center;padding:12px;font-size:11px;color:var(--text-tertiary)"><div class="loading-spinner" style="width:16px;height:16px;margin:0 auto 6px;border-width:2px"></div>加载规模-战绩数据...</div>';

  try {
    const r = await fetch(API_BASE + '/fund/manager-track/' + code, { signal: AbortSignal.timeout(20000) });
    if (!r.ok) throw new Error('API ' + r.status);
    const d = await r.json();
    if (!d.available) {
      area.innerHTML = '<div style="font-size:11px;color:var(--text-tertiary);padding:8px">' + (d.reason || '数据不足') + '</div>';
      return;
    }

    let html = '<div style="margin-top:10px;border-top:1px solid var(--border-subtle,rgba(255,255,255,.04));padding-top:10px">';
    html += '<div style="font-size:12px;font-weight:700;margin-bottom:8px">📊 ' + (managerName||'') + ' 规模-业绩对照</div>';

    // 表格
    if (d.track && d.track.length) {
      html += '<div style="max-height:180px;overflow-y:auto;font-size:11px">';
      html += '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:2px;padding:4px 0;border-bottom:1px solid var(--border-subtle);font-weight:600;color:var(--text-tertiary)"><span>季度</span><span>规模(亿)</span><span>收益</span></div>';
      d.track.forEach(t => {
        const retColor = (t.quarter_return_pct||0) >= 0 ? 'var(--color-bull,#00E5A0)' : 'var(--color-bear,#FF6B6B)';
        html += '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:2px;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.02)"><span>' + t.quarter + '</span><span>' + (t.scale_billion||'—') + '</span><span style="color:' + retColor + '">' + (t.quarter_return_pct!=null?(t.quarter_return_pct>0?'+':'')+t.quarter_return_pct+'%':'—') + '</span></div>';
      });
      html += '</div>';
    }

    // 结论
    if (d.verdict) {
      html += '<div style="margin-top:8px;padding:8px;background:rgba(255,183,85,.06);border-radius:8px;font-size:12px;line-height:1.5">' + d.verdict + '</div>';
    }

    html += '</div>';
    area.innerHTML = html;
  } catch (e) {
    area.innerHTML = '<div style="font-size:11px;color:var(--text-tertiary);padding:8px">规模数据加载失败</div>';
  }
};

/* 选股详情弹窗（Phase 3 前端） */
function _buildStockDetailModalView(stockData, code, policyHtml='') {
  const s = stockData || {};
  const scoreValue = s.score ?? s.longterm_score;
  const chgBase = s.change_pct ?? 0;
  const chgColor = chgBase >= 0 ? 'var(--color-bull,#00E5A0)' : 'var(--color-bear,#FF6B6B)';
  const scoreColor = (scoreValue || 0) > 65 ? 'var(--color-bull,#00E5A0)' : (scoreValue || 0) > 50 ? 'var(--accent,#F59E0B)' : 'var(--color-bear,#FF6B6B)';
  const subtitleParts = [code];
  if (s.change_pct != null) subtitleParts.push((s.change_pct > 0 ? '+' : '') + s.change_pct + '%');
  if (s.market_cap) subtitleParts.push('市值 ' + s.market_cap + '亿');
  else if (s.industry) subtitleParts.push(s.industry);
  else if (s.market) subtitleParts.push(s.market);
  const roeValue = s.roe ?? s.avg_roe;
  const grossMarginValue = s.gross_margin ?? s.avg_gpm;
  const debtValue = s.debt_ratio ?? s.avg_debt;
  const revenueGrowthValue = s.revenue_growth ?? s.avg_np_growth;
  const turnoverLabel = s.turnover ? s.turnover + '%' : '—';
  const aiComment = s.aiComment || s.note || '';
  const longtermSummary = (s.longterm_score != null || s.holding_years || s.note)
    ? `<div style="padding:10px 12px;background:rgba(99,102,241,.08);border-radius:10px;font-size:12px;line-height:1.6;margin-bottom:14px;color:#E0E7FF">
        <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:4px">
          <span>🛡️ 长持评分 <b style="color:${scoreColor}">${scoreValue != null ? scoreValue : '—'}</b></span>
          <span>${s.holding_years || '建议长期持有'}</span>
        </div>
        <div>${s.note || '基于护城河因子筛选出的长期持有候选'}</div>
      </div>`
    : '';
  const scoreCards = s.scores ? `<div style="font-size:12px;font-weight:700;margin-bottom:8px">🎯 7维评分</div>
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px">
      ${Object.entries(s.scores).map(([k,v])=>{const labels={value:'价值',growth:'成长',quality:'质量',momentum:'动量',risk:'风险',liquidity:'流动性',sentiment:'舆情'};const color=v>=70?'var(--color-bull,#00E5A0)':v>=50?'var(--accent,#F59E0B)':'var(--color-bear,#FF6B6B)';return '<div style="flex:1;min-width:70px;text-align:center;padding:6px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><div style="font-size:10px;color:var(--text-tertiary)">'+(labels[k]||k)+'</div><div style="font-size:14px;font-weight:700;color:'+color+'">'+v+'</div></div>'}).join('')}
    </div>` : '';
  const titleHtml = `<span>📈 ${s.name || code}</span>${s.price != null ? `<span style="font-size:18px;font-weight:800;color:${chgColor}">¥${s.price}</span>` : ''}`;
  const bodyHtml = `${policyHtml}${longtermSummary}
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0">
      <div class="mb-card--ghost" style="padding:10px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">PE</div><div style="font-size:16px;font-weight:700">${s.pe!=null?s.pe:'—'}</div></div>
      <div class="mb-card--ghost" style="padding:10px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">PB</div><div style="font-size:16px;font-weight:700">${s.pb!=null?s.pb:'—'}</div></div>
      <div class="mb-card--ghost" style="padding:10px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">综合评分</div><div style="font-size:16px;font-weight:800;color:${scoreColor}">${scoreValue!=null?scoreValue:'—'}</div></div>
    </div>
    <div style="font-size:12px;font-weight:700;margin-bottom:8px">📋 财务指标</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:14px">
      <div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 8px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><span style="color:var(--text-tertiary)">ROE</span><span style="font-weight:600">${roeValue!=null?roeValue+'%':'—'}</span></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 8px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><span style="color:var(--text-tertiary)">毛利率</span><span style="font-weight:600">${grossMarginValue!=null?grossMarginValue+'%':'—'}</span></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 8px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><span style="color:var(--text-tertiary)">净利率</span><span style="font-weight:600">${s.net_margin!=null?s.net_margin+'%':'—'}</span></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 8px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><span style="color:var(--text-tertiary)">负债率</span><span style="font-weight:600">${debtValue!=null?debtValue+'%':'—'}</span></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 8px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><span style="color:var(--text-tertiary)">营收增速</span><span style="font-weight:600">${revenueGrowthValue!=null?revenueGrowthValue+'%':'—'}</span></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 8px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><span style="color:var(--text-tertiary)">EPS</span><span style="font-weight:600">${s.eps!=null?s.eps:'—'}</span></div>
    </div>
    ${scoreCards}
    ${aiComment?'<div style="padding:10px;background:rgba(99,102,241,.08);border-radius:10px;font-size:12px;color:#E0E7FF;line-height:1.6;margin-bottom:14px">🤖 '+aiComment+'</div>':''}
    <div style="font-size:11px;color:var(--text-tertiary);text-align:center;margin-bottom:12px">换手率 ${turnoverLabel}${s.financial_source?' · 财务源 '+s.financial_source:''}</div>
    <div style="display:flex;gap:8px">
      <button class="mb-btn mb-btn--secondary mb-btn--block" onclick="showFundChart('${code}')">📈 K线</button>
      <button class="mb-btn mb-btn--ai mb-btn--block" onclick="document.querySelector('.modal-overlay')?.remove();navigateTo('chat');setTimeout(()=>{const inp=document.getElementById('chatIn');if(inp){inp.value='帮我分析${(s.name||code).replace(/'/g,'')}(${code})的投资价值';inp.focus()}},300)">💬 问 AI</button>
    </div>`;
  return {titleHtml, subtitle: subtitleParts.join(' · '), bodyHtml};
}

window.showStockDetailModal = async function(stockData) {
  if (!stockData) return;
  const baseData = {...stockData};
  const code = (baseData.code || '').replace(/^(sh|sz)/i, '');

  let policyHtml = '';
  if (typeof _policyTagsCache !== 'undefined' && _policyTagsCache && _policyTagsCache[code]) {
    policyHtml = '<div style="display:flex;gap:4px;flex-wrap:wrap;margin:8px 0">' +
      _policyTagsCache[code].map(t => '<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(245,158,11,.12);color:#FBBF24">🏷️' + t + '</span>').join('') + '</div>';
  }

  const initialView = _buildStockDetailModalView({...baseData, score: baseData.score ?? baseData.longterm_score, aiComment: baseData.aiComment || baseData.note || ''}, code, policyHtml);
  const o = document.createElement('div');
  o.className = 'modal-overlay';
  o.onclick = e => { if (e.target === o) o.remove(); };
  o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:90vh;overflow-y:auto">
    <div class="modal-handle"></div>
    <div class="modal-title" id="stockDetailTitle" style="display:flex;align-items:baseline;gap:8px">${initialView.titleHtml}</div>
    <div class="modal-subtitle" id="stockDetailSubtitle">${initialView.subtitle}</div>
    <div id="stockDetailBody" style="padding:12px 0"><div style="text-align:center;padding:28px 12px"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div><div style="font-size:12px;color:var(--text2)">正在补齐股票基础与财务数据...</div></div></div>
  </div>`;
  document.body.appendChild(o);

  const titleEl = document.getElementById('stockDetailTitle');
  const subtitleEl = document.getElementById('stockDetailSubtitle');
  const bodyEl = document.getElementById('stockDetailBody');
  const renderView = (payload) => {
    const view = _buildStockDetailModalView(payload, code, policyHtml);
    if (titleEl) titleEl.innerHTML = view.titleHtml;
    if (subtitleEl) subtitleEl.textContent = view.subtitle;
    if (bodyEl) bodyEl.innerHTML = view.bodyHtml;
  };

  const fetchJson = async (url) => {
    const r = await fetch(url, { signal: AbortSignal.timeout(15000) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  };

  try {
    const [basicRes, finRes] = await Promise.allSettled([
      fetchJson(API_BASE + '/stock-basic/' + code),
      fetchJson(API_BASE + '/stock/financials/' + code),
    ]);
    const basic = basicRes.status === 'fulfilled' ? basicRes.value : {};
    const fin = finRes.status === 'fulfilled' ? finRes.value : {};
    const merged = {
      ...baseData,
      name: basic.name || baseData.name || code,
      industry: basic.industry || baseData.industry,
      price: basic.price ?? baseData.price,
      roe: fin.roe ?? baseData.roe ?? baseData.avg_roe,
      gross_margin: fin.gross_margin ?? baseData.gross_margin ?? baseData.avg_gpm,
      net_margin: fin.net_margin ?? baseData.net_margin,
      debt_ratio: fin.debt_ratio ?? baseData.debt_ratio ?? baseData.avg_debt,
      revenue_growth: fin.revenue_growth ?? baseData.revenue_growth ?? baseData.avg_np_growth,
      eps: fin.eps ?? baseData.eps,
      score: baseData.score ?? baseData.longterm_score,
      aiComment: baseData.aiComment || baseData.note || '',
      financial_source: fin.source || '',
    };
    renderView(merged);
  } catch (e) {
    renderView({
      ...baseData,
      score: baseData.score ?? baseData.longterm_score,
      aiComment: `详情补全失败：${e.message || '网络异常'}。先展示长持榜已有数据。`,
    });
  }
};

// v9.5.124: 多模型AI评分异步加载
window._loadAiScore = async function(code, container) {
  if(!container) return;
  container.innerHTML = `<div style="text-align:center;padding:12px"><div class="loading-spinner" style="width:18px;height:18px;margin:0 auto 6px;border-width:2px"></div><div style="font-size:11px;color:var(--text-tertiary)">三大AI模型评分中... (约5-10秒)</div></div>`;
  try {
    const r = await fetch(API_BASE + '/fund/ai-score/' + code, {signal: AbortSignal.timeout(45000)});
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    if(d.error) { container.innerHTML = `<div style="font-size:11px;color:#F87171">${d.error}</div>`; return; }

    let h = `<div style="font-size:11px;font-weight:600;color:#A78BFA;margin-bottom:8px">🤖 AI 多模型评审</div>`;

    // 综合分大字
    const avgColor = (d.avg_score||0)>=7?'#86EFAC':(d.avg_score||0)>=5?'#F59E0B':'#FCA5A5';
    const consColor = d.consensus?.includes('推荐')?'#86EFAC':d.consensus?.includes('谨慎')?'#FCA5A5':'#F59E0B';
    h += `<div style="display:flex;align-items:baseline;gap:10px;margin-bottom:10px">
      <span style="font-size:22px;font-weight:800;color:${avgColor}">${d.avg_score||'—'}</span><span style="font-size:11px;color:var(--text-tertiary)">/10</span>
      <span style="font-size:11px;padding:2px 8px;border-radius:8px;background:rgba(139,92,246,.1);color:${consColor};font-weight:600">${d.consensus||'未知'}</span>
      <span style="font-size:10px;color:var(--text-tertiary);margin-left:auto">${d.model_count||0}个模型</span>
    </div>`;

    // 各模型分项
    h += '<div style="display:flex;flex-direction:column;gap:6px">';
    for(const s of (d.scores||[])) {
      const ok = s.score != null;
      const sColor = ok ? (s.score>=7?'#86EFAC':s.score>=5?'#F59E0B':'#FCA5A5') : '#9AA1AC';
      const icon = s.id==='deepseek'?'🔵':s.id==='doubao'?'🟠':'🟣';
      h += `<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;background:rgba(255,255,255,.02);border-radius:6px">
        <span style="font-size:12px">${icon}</span>
        <span style="font-size:11px;color:var(--text-secondary);width:80px;flex-shrink:0">${s.name}</span>
        <span style="font-size:14px;font-weight:700;color:${sColor};min-width:28px">${ok?s.score:'—'}</span>
        <span style="font-size:10px;color:var(--text-tertiary);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.reason||''}</span>
        ${s.risk?`<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(248,113,113,.08);color:#FCA5A5;flex-shrink:0">${s.risk}</span>`:''}
      </div>`;
    }
    h += '</div>';

    // 缓存标注
    if(d.from_cache) {
      h += `<div style="font-size:9px;color:var(--text-tertiary);margin-top:6px;text-align:right">缓存 · 12h刷新一次</div>`;
    }

    container.innerHTML = h;
  } catch(e) {
    container.innerHTML = `<div style="font-size:11px;color:#F87171">AI评分加载失败: ${e.message||'超时'}<br><button onclick="_loadAiScore('${code}',this.parentElement.parentElement)" style="margin-top:4px;padding:3px 10px;border-radius:4px;border:1px solid rgba(248,113,113,.3);background:transparent;color:#F87171;font-size:10px;cursor:pointer">重试</button></div>`;
  }
};
