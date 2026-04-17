/* =========================================================================
 * V6 欠账 4/6：信号页 AI 12 维解读卡片
 * 方式：劫持 loadSignals()，在原始信号卡渲染完后追加 AI 解读区
 * 依赖 API：/api/daily-signal/interpret
 * ========================================================================= */
;(function(){
  'use strict';

  async function _v6InjectSignalInterpret(){
    // 仅 Pro 模式展示完整解读，Simple 模式展示精简版
    const section = document.getElementById('signalsSection');
    if (!section) return;
    if (section.querySelector('#v6SignalInterpret')) return;

    const host = document.createElement('div');
    host.id = 'v6SignalInterpret';
    host.style.cssText = 'margin-top:12px';
    host.innerHTML = isProMode()
      ? _v6Skeleton('🤖 AI 正在解读信号（约10s）...')
      : _v6Skeleton('正在生成信号摘要...');
    section.appendChild(host);

    const d = await _v6Fetch('/daily-signal/interpret', { timeout: 45000 });
    if (!d) {
      host.innerHTML = `<div class="dashboard-card" style="border-left:3px solid var(--bg3)">
        <div class="dashboard-card-title">🤖 AI 信号解读</div>
        <div style="font-size:12px;color:var(--text2)">解读数据暂不可用</div>
      </div>`;
      return;
    }

    let html = '';

    // === Simple 模式：只显示一句话结论 ===
    if (!isProMode()) {
      const conclusion = d.conclusion || d.summary || d.tldr || '';
      if (conclusion) {
        html = _v6Card('🤖 AI 一句话解读', `
          <div style="font-size:14px;line-height:1.8;color:var(--text1)">${conclusion}</div>
          <div style="font-size:11px;color:var(--accent);margin-top:6px">切换到 Pro 模式查看 12 维完整解读 ›</div>
        `);
      }
      host.innerHTML = html;
      return;
    }

    // === Pro 模式：12 维完整解读 ===
    html += `<div class="section-title">🤖 AI 12 维信号深度解读 <span style="font-size:11px;color:var(--accent);font-weight:400">Phase 0 · DeepSeek</span></div>`;

    // 总结论
    if (d.conclusion || d.summary) {
      html += `<div class="dashboard-card" style="background:linear-gradient(135deg,rgba(59,130,246,.06),rgba(16,185,129,.06));border:1px solid rgba(59,130,246,.12)">
        <div style="font-size:14px;font-weight:700;margin-bottom:6px">📋 综合结论</div>
        <div style="font-size:14px;line-height:1.8;color:var(--text1)">${d.conclusion || d.summary}</div>
      </div>`;
    }

    // 12 维度逐条解读
    const dims = d.dimensions || d.factors || d.details || [];
    if (dims.length) {
      const catIcons = {
        '技术面':'📊','基本面':'📈','资金面':'💰','情绪面':'😊','宏观面':'🏛️',
        'technical':'📊','fundamental':'📈','flow':'💰','sentiment':'😊','macro':'🏛️'
      };
      html += `<div style="display:grid;grid-template-columns:1fr;gap:6px;margin-top:8px">`;
      dims.forEach(dim => {
        const score = dim.score != null ? dim.score : '';
        const scoreColor = score > 10 ? 'var(--green)' : score < -10 ? 'var(--red)' : '#F59E0B';
        const icon = catIcons[dim.category || dim.cat || ''] || '📌';
        html += `<div class="dashboard-card" style="margin:0;padding:10px 12px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div style="font-size:13px;font-weight:700">${icon} ${dim.name || dim.title || ''}</div>
            ${score !== '' ? `<div style="font-size:16px;font-weight:900;color:${scoreColor}">${score > 0 ? '+' : ''}${score}</div>` : ''}
          </div>
          <div style="font-size:12px;color:var(--text2);margin-top:4px;line-height:1.6">${dim.interpretation || dim.detail || dim.analysis || ''}</div>
        </div>`;
      });
      html += '</div>';
    }

    // 操作建议
    if (d.action_plan || d.suggestions) {
      const items = d.action_plan || d.suggestions || [];
      if (Array.isArray(items) && items.length) {
        html += `<div class="dashboard-card" style="margin-top:8px;border-left:3px solid var(--accent)">
          <div class="dashboard-card-title">🎯 操作建议</div>
          ${items.map(a => `<div style="padding:6px 0;font-size:13px;border-bottom:1px solid var(--bg3)">${typeof a === 'string' ? a : (a.text || a.action || '')}</div>`).join('')}
        </div>`;
      }
    }

    host.innerHTML = html;
  }

  function _install(){
    if (typeof loadSignals !== 'function') return false;
    _v6Hijack('loadSignals', async function(){
      // 等原函数的 DOM 渲染完
      setTimeout(_v6InjectSignalInterpret, 300);
    });
    return true;
  }

  if (!_install()) {
    const t = setInterval(() => { if (_install()) clearInterval(t); }, 200);
    setTimeout(() => clearInterval(t), 5000);
  }

  console.log('[V6-4] signal-interpret patch installed');
})();
