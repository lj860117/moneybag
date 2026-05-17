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
