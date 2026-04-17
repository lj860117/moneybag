/* =========================================================================
 * V6 Phase 0 前端欠账补丁 - 公共工具
 * 追加式模块化：不改原函数体，通过劫持/后插注入新 UI
 * 依赖全局：API_BASE, API_AVAILABLE, getProfileParam, getProfileId, isProMode
 * ========================================================================= */
;(function(){
  'use strict';
  if (window._V6Patches) return; // 防重复加载
  window._V6Patches = { version: '1.0.0', loadedAt: Date.now() };

  // --- 通用 fetch 封装：统一超时 + 错误回退 ---
  window._v6Fetch = async function(path, opts){
    opts = opts || {};
    const timeout = opts.timeout || 15000;
    const url = (path.startsWith('http') ? path : API_BASE + path);
    try {
      const r = await fetch(url, Object.assign({
        signal: AbortSignal.timeout(timeout)
      }, opts));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return await r.json();
    } catch (e) {
      console.warn('[V6] fetch fail:', path, e.message);
      return null;
    }
  };

  // --- 等锚点出现再注入（带超时） ---
  window._v6WaitEl = function(selector, maxMs){
    maxMs = maxMs || 3000;
    return new Promise(resolve => {
      const start = Date.now();
      const tick = () => {
        const el = document.querySelector(selector);
        if (el) return resolve(el);
        if (Date.now() - start > maxMs) return resolve(null);
        setTimeout(tick, 80);
      };
      tick();
    });
  };

  // --- 卡片模板：统一风格 ---
  window._v6Card = function(title, bodyHtml, opts){
    opts = opts || {};
    const border = opts.border ? `border-left:3px solid ${opts.border};` : '';
    const badge = opts.badge
      ? `<span style="font-size:10px;padding:2px 6px;border-radius:6px;background:rgba(59,130,246,.12);color:#3B82F6;margin-left:6px;font-weight:600">${opts.badge}</span>`
      : '';
    return `<div class="dashboard-card" style="${border}margin-top:8px">
      <div class="dashboard-card-title">${title}${badge}</div>
      ${bodyHtml}
    </div>`;
  };

  // --- 骨架屏 ---
  window._v6Skeleton = function(msg){
    return `<div style="text-align:center;padding:24px;color:var(--text2)">
      <div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>
      <div style="font-size:12px">${msg || '加载中...'}</div>
    </div>`;
  };

  // --- 劫持全局函数：链式包装，每次追加一个 afterFn 钩子 ---
  window._v6Hijack = function(funcName, afterFn){
    const orig = window[funcName];
    if (typeof orig !== 'function') {
      console.warn('[V6] hijack target not found:', funcName);
      return;
    }
    // 允许多次劫持：每次在当前版本外面再包一层
    const wrapped = async function(...args){
      const r = await orig.apply(this, args);
      try { await afterFn.apply(this, args); } catch(e){ console.warn('[V6] after hook error:', funcName, e); }
      return r;
    };
    wrapped.__v6Hijacked = true;
    wrapped.__orig = orig;
    window[funcName] = wrapped;
  };

  // --- 持仓是否为空（判断是否进入"空仓模式"）---
  window._v6IsEmptyHoldings = function(){
    try {
      const stocks = (typeof loadPortfolio === 'function') ? (loadPortfolio() || {}) : {};
      const assets = (typeof loadAssets === 'function') ? (loadAssets() || []) : [];
      // 三个迹象都为空才算空仓
      const sCount = Object.keys(stocks.stocks || stocks || {}).length;
      const aCount = (assets || []).length;
      return sCount === 0 && aCount === 0;
    } catch(e){ return false; }
  };

  console.log('[V6] common utils loaded');
})();
