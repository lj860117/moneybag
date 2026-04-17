/* =========================================================================
 * V6 欠账 3/6：持仓页 Pro 模式 AI 深度分析按钮
 * 方式：renderStocksContent / renderFundsContent 完成后，注入"AI 深度分析"按钮
 *       点击后调用 /api/stock-holdings/analyze 或 /api/fund-holdings/analyze
 * ========================================================================= */
;(function(){
  'use strict';

  // --- 通用：渲染 AI 分析结果弹窗 ---
  function _showAIAnalysis(title, data){
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };

    let body = '';
    if (data.error) {
      body = `<div style="padding:20px;text-align:center;color:var(--red)">${data.error}</div>`;
    } else if (data.analysis || data.summary) {
      // 结构化输出
      const summary = data.summary || data.analysis || '';
      const sections = data.sections || data.details || [];
      body = `<div style="font-size:14px;line-height:1.8;color:var(--text1);white-space:pre-wrap;margin-bottom:12px">${summary}</div>`;
      if (Array.isArray(sections) && sections.length) {
        sections.forEach(s => {
          body += `<div class="dashboard-card" style="margin-bottom:8px">
            <div style="font-size:13px;font-weight:700;margin-bottom:4px">${s.title || s.name || ''}</div>
            <div style="font-size:12px;color:var(--text2);line-height:1.6">${s.content || s.detail || ''}</div>
          </div>`;
        });
      }
      if (data.risk_warnings && data.risk_warnings.length) {
        body += `<div style="margin-top:8px;padding:10px;background:rgba(239,68,68,.06);border-radius:8px;border-left:3px solid var(--red)">
          <div style="font-size:12px;font-weight:700;color:var(--red);margin-bottom:4px">⚠️ 风险提示</div>
          ${data.risk_warnings.map(w => `<div style="font-size:12px;color:var(--text2);line-height:1.5">• ${w}</div>`).join('')}
        </div>`;
      }
      if (data.suggestions && data.suggestions.length) {
        body += `<div style="margin-top:8px;padding:10px;background:rgba(16,185,129,.06);border-radius:8px;border-left:3px solid var(--green)">
          <div style="font-size:12px;font-weight:700;color:var(--green);margin-bottom:4px">💡 建议</div>
          ${data.suggestions.map(s => `<div style="font-size:12px;color:var(--text2);line-height:1.5">• ${typeof s === 'string' ? s : (s.text || s.content || '')}</div>`).join('')}
        </div>`;
      }
    } else {
      body = `<div style="font-size:13px;color:var(--text2);line-height:1.6;white-space:pre-wrap">${JSON.stringify(data, null, 2)}</div>`;
    }

    overlay.innerHTML = `<div class="modal-sheet" style="max-height:85vh;overflow-y:auto">
      <div class="modal-handle"></div>
      <div class="modal-title">${title}</div>
      <div class="modal-subtitle" style="margin-bottom:12px">🤖 AI 深度分析 · Phase 0</div>
      ${body}
      <button class="form-submit" style="margin-top:16px" onclick="this.closest('.modal-overlay').remove()">关闭</button>
    </div>`;
    document.body.appendChild(overlay);
  }

  // --- 注入按钮到持仓 content 底部 ---
  function _injectStockAIBtn(){
    if (!isProMode()) return;
    const el = document.getElementById('holdingsContent');
    if (!el) return;
    if (el.querySelector('#v6StockAIBtn')) return;

    // 找最后一个 action-btn
    const btns = el.querySelectorAll('.action-btn');
    if (!btns.length) return;
    const lastBtn = btns[btns.length - 1].parentNode;

    const wrap = document.createElement('div');
    wrap.style.cssText = 'margin-top:8px';
    wrap.innerHTML = `<button id="v6StockAIBtn" class="action-btn secondary" style="width:100%;background:linear-gradient(135deg,rgba(59,130,246,.08),rgba(168,85,247,.08));border:1px solid rgba(59,130,246,.2)" onclick="window._v6AnalyzeStocks()">
      🧠 AI 深度分析（Pro）
    </button>`;
    lastBtn.parentNode.insertBefore(wrap, lastBtn.nextSibling);
  }

  function _injectFundAIBtn(){
    if (!isProMode()) return;
    const el = document.getElementById('holdingsContent');
    if (!el) return;
    if (el.querySelector('#v6FundAIBtn')) return;

    const btns = el.querySelectorAll('.action-btn');
    if (!btns.length) return;
    const lastBtn = btns[btns.length - 1].parentNode;

    const wrap = document.createElement('div');
    wrap.style.cssText = 'margin-top:8px';
    wrap.innerHTML = `<button id="v6FundAIBtn" class="action-btn secondary" style="width:100%;background:linear-gradient(135deg,rgba(16,185,129,.08),rgba(168,85,247,.08));border:1px solid rgba(16,185,129,.2)" onclick="window._v6AnalyzeFunds()">
      🧠 AI 深度分析（Pro）
    </button>`;
    lastBtn.parentNode.insertBefore(wrap, lastBtn.nextSibling);
  }

  // --- 全局分析函数（按钮 onclick 调用）---
  window._v6AnalyzeStocks = async function(){
    const btn = document.getElementById('v6StockAIBtn');
    if (btn) { btn.disabled = true; btn.innerHTML = '🧠 正在分析...请稍候（30s）'; }
    const d = await _v6Fetch('/stock-holdings/analyze?' + getProfileParam(), { timeout: 60000 });
    if (btn) { btn.disabled = false; btn.innerHTML = '🧠 AI 深度分析（Pro）'; }
    if (d) _showAIAnalysis('📊 股票持仓 AI 深度分析', d);
    else alert('分析请求失败，请稍后重试');
  };

  window._v6AnalyzeFunds = async function(){
    const btn = document.getElementById('v6FundAIBtn');
    if (btn) { btn.disabled = true; btn.innerHTML = '🧠 正在分析...请稍候（30s）'; }
    const d = await _v6Fetch('/fund-holdings/analyze?' + getProfileParam(), { timeout: 60000 });
    if (btn) { btn.disabled = false; btn.innerHTML = '🧠 AI 深度分析（Pro）'; }
    if (d) _showAIAnalysis('💰 基金持仓 AI 深度分析', d);
    else alert('分析请求失败，请稍后重试');
  };

  // --- 劫持 renderStocksContent / renderFundsContent ---
  function _install(){
    let ok = true;
    if (typeof renderStocksContent === 'function') {
      _v6Hijack('renderStocksContent', () => setTimeout(_injectStockAIBtn, 100));
    } else { ok = false; }
    if (typeof renderFundsContent === 'function') {
      _v6Hijack('renderFundsContent', () => setTimeout(_injectFundAIBtn, 100));
    } else { ok = false; }
    return ok;
  }

  if (!_install()) {
    const t = setInterval(() => { if (_install()) clearInterval(t); }, 200);
    setTimeout(() => clearInterval(t), 5000);
  }

  console.log('[V6-3] holdings AI analysis patch installed');
})();
