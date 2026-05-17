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
      <button class="mb-btn mb-btn--ai mb-btn--block" onclick="document.querySelector('.modal-overlay')?.remove();navigateTo('chat');setTimeout(()=>{const inp=document.getElementById('chatIn');if(inp){inp.value='帮我分析基金${code}的投资价值';inp.focus()}},300)">💬 问 AI</button>
    </div>
  </div>`;
  document.body.appendChild(o);

  // 异步加载详情
  try {
    const r = await fetch(API_BASE + '/fund/detail/' + code, { signal: AbortSignal.timeout(15000) });
    if (!r.ok) throw new Error('API ' + r.status);
    const d = await r.json();
    const body = document.getElementById('fundDetailBody');
    if (!body) return;

    let html = '';

    // 基本信息网格
    const returns = d.returns || {};
    html += `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px">
      <div class="mb-card--ghost" style="padding:10px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">近1年</div><div style="font-size:16px;font-weight:700;color:${(returns['1y']||0)>=0?'var(--color-bull,#00E5A0)':'var(--color-bear,#FF6B6B)'}">${returns['1y']!=null?(returns['1y']>0?'+':'')+returns['1y']+'%':'—'}</div></div>
      <div class="mb-card--ghost" style="padding:10px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">近3年</div><div style="font-size:16px;font-weight:700;color:${(returns['3y']||0)>=0?'var(--color-bull,#00E5A0)':'var(--color-bear,#FF6B6B)'}">${returns['3y']!=null?(returns['3y']>0?'+':'')+returns['3y']+'%':'—'}</div></div>
      <div class="mb-card--ghost" style="padding:10px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">规模</div><div style="font-size:16px;font-weight:700">${d.scale_billion?d.scale_billion+'亿':'—'}</div></div>
    </div>`;

    // 基金经理卡
    if (d.manager) {
      const m = d.manager;
      html += `<div class="mb-card--ghost" style="padding:12px;margin-bottom:14px">
        <div class="mb-flex mb-gap-3 mb-mb-3">
          <div class="mb-avatar mb-avatar--md" style="background:linear-gradient(135deg,#5C6BC0,#283593)">👤</div>
          <div>
            <b style="font-size:14px">${m.name}</b>
            <div style="font-size:11px;color:var(--text-tertiary);margin-top:2px">任期 ${m.tenure_years} 年${d.scale_billion?' · 管理 '+d.scale_billion+'亿':''}</div>
          </div>
        </div>
        ${m.resume?'<div style="font-size:11px;color:var(--text-secondary);line-height:1.5;margin-top:6px">'+m.resume+'</div>':''}
        <button class="mb-btn mb-btn--ghost mb-btn--sm" style="margin-top:8px;width:100%" onclick="loadManagerTrack('${code}','${m.name}')">📊 查看规模-业绩对照</button>
        <div id="managerTrackArea"></div>
      </div>`;
    }

    // 重仓持仓
    if (d.top_holdings && d.top_holdings.length) {
      html += `<div style="font-size:12px;font-weight:700;margin-bottom:6px">🏦 重仓持仓 TOP5</div>`;
      html += d.top_holdings.map(h => `<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:12px;border-bottom:1px solid var(--border-subtle,rgba(255,255,255,.04))"><span>${h.symbol}</span><span style="color:var(--text-tertiary)">${h.ratio?h.ratio+'%':''}</span></div>`).join('');
      html += '<div style="margin-bottom:14px"></div>';
    }

    // 其他信息
    html += `<div style="font-size:11px;color:var(--text-tertiary);text-align:center">费率 ${d.fee||'—'} · 数据来源 ${d.source||'tushare'} · ${d.updatedAt||''}</div>`;

    body.innerHTML = html;
  } catch (e) {
    const body = document.getElementById('fundDetailBody');
    if (body) body.innerHTML = typeof renderFetchError === 'function' ? renderFetchError('基金详情加载失败') : '<div style="text-align:center;padding:20px;color:var(--text2)">加载失败</div>';
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
window.showStockDetailModal = function(stockData) {
  if (!stockData) return;
  const s = stockData;
  const code = (s.code || '').replace(/^(sh|sz)/i, '');
  const chgColor = (s.change_pct||0) >= 0 ? 'var(--color-bull,#00E5A0)' : 'var(--color-bear,#FF6B6B)';
  const scoreColor = (s.score||0) > 65 ? 'var(--color-bull,#00E5A0)' : (s.score||0) > 50 ? 'var(--accent,#F59E0B)' : 'var(--color-bear,#FF6B6B)';

  // 政策标签
  let policyHtml = '';
  if (typeof _policyTagsCache !== 'undefined' && _policyTagsCache && _policyTagsCache[code]) {
    policyHtml = '<div style="display:flex;gap:4px;flex-wrap:wrap;margin:8px 0">' +
      _policyTagsCache[code].map(t => '<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(245,158,11,.12);color:#FBBF24">\u{1F3F7}️' + t + '</span>').join('') + '</div>';
  }

  const o = document.createElement('div');
  o.className = 'modal-overlay';
  o.onclick = e => { if (e.target === o) o.remove(); };
  o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:90vh;overflow-y:auto">
    <div class="modal-handle"></div>
    <div class="modal-title" style="display:flex;align-items:baseline;gap:8px">
      <span>\u{1F4C8} ${s.name||code}</span>
      <span style="font-size:18px;font-weight:800;color:${chgColor}">${s.price?'¥'+s.price:''}</span>
    </div>
    <div class="modal-subtitle">${code} · ${s.change_pct!=null?(s.change_pct>0?'+':'')+s.change_pct+'%':'—'} · 市值 ${s.market_cap?s.market_cap+'亿':'—'}</div>
    ${policyHtml}

    <!-- 核心指标网格 -->
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0">
      <div class="mb-card--ghost" style="padding:10px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">PE</div><div style="font-size:16px;font-weight:700">${s.pe!=null?s.pe:'—'}</div></div>
      <div class="mb-card--ghost" style="padding:10px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">PB</div><div style="font-size:16px;font-weight:700">${s.pb!=null?s.pb:'—'}</div></div>
      <div class="mb-card--ghost" style="padding:10px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">综合评分</div><div style="font-size:16px;font-weight:800;color:${scoreColor}">${s.score||'—'}</div></div>
    </div>

    <!-- 财务指标 -->
    <div style="font-size:12px;font-weight:700;margin-bottom:8px">\u{1F4CB} 财务指标</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:14px">
      <div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 8px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><span style="color:var(--text-tertiary)">ROE</span><span style="font-weight:600">${s.roe?s.roe+'%':'—'}</span></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 8px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><span style="color:var(--text-tertiary)">毛利率</span><span style="font-weight:600">${s.gross_margin?s.gross_margin+'%':'—'}</span></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 8px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><span style="color:var(--text-tertiary)">净利率</span><span style="font-weight:600">${s.net_margin?s.net_margin+'%':'—'}</span></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 8px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><span style="color:var(--text-tertiary)">负债率</span><span style="font-weight:600">${s.debt_ratio?s.debt_ratio+'%':'—'}</span></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 8px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><span style="color:var(--text-tertiary)">营收增速</span><span style="font-weight:600">${s.revenue_growth?s.revenue_growth+'%':'—'}</span></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 8px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><span style="color:var(--text-tertiary)">EPS</span><span style="font-weight:600">${s.eps||'—'}</span></div>
    </div>

    <!-- 7维评分 -->
    ${s.scores?`<div style="font-size:12px;font-weight:700;margin-bottom:8px">\u{1F3AF} 7维评分</div>
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px">
      ${Object.entries(s.scores).map(([k,v])=>{const labels={value:'价值',growth:'成长',quality:'质量',momentum:'动量',risk:'风险',liquidity:'流动性',sentiment:'舆情'};const color=v>=70?'var(--color-bull,#00E5A0)':v>=50?'var(--accent,#F59E0B)':'var(--color-bear,#FF6B6B)';return '<div style="flex:1;min-width:70px;text-align:center;padding:6px;background:var(--bg-elevated,rgba(255,255,255,.03));border-radius:6px"><div style="font-size:10px;color:var(--text-tertiary)">'+(labels[k]||k)+'</div><div style="font-size:14px;font-weight:700;color:'+color+'">'+v+'</div></div>'}).join('')}
    </div>`:''}

    <!-- AI 点评 -->
    ${s.aiComment?'<div style="padding:10px;background:rgba(99,102,241,.08);border-radius:10px;font-size:12px;color:#E0E7FF;line-height:1.6;margin-bottom:14px">\u{1F916} '+s.aiComment+'</div>':''}

    <!-- 换手率 -->
    <div style="font-size:11px;color:var(--text-tertiary);text-align:center;margin-bottom:12px">换手率 ${s.turnover?s.turnover+'%':'—'}</div>

    <div style="display:flex;gap:8px">
      <button class="mb-btn mb-btn--secondary mb-btn--block" onclick="showFundChart('${code}')">\u{1F4C8} K线</button>
      <button class="mb-btn mb-btn--ai mb-btn--block" onclick="document.querySelector('.modal-overlay')?.remove();navigateTo('chat');setTimeout(()=>{const inp=document.getElementById('chatIn');if(inp){inp.value='帮我分析${(s.name||code).replace(/'/g,'')}(${code})的投资价值';inp.focus()}},300)">\u{1F4AC} 问 AI</button>
    </div>
  </div>`;
  document.body.appendChild(o);
};
