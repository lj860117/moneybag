/**
 * 钱袋子 v9.5.123 — Sprint 卡片（独立文件）
 * ==========================================
 * 首页展示1张精简摘要卡片，点击展开详情弹窗
 * 
 * 包含: 家庭全景 + 目标进度 + DNA画像 + 决策复盘
 * 入口: landing.js 调用 loadSprintCard()
 */

async function loadSprintCard() {
  if (!API_AVAILABLE) return;
  const el = document.getElementById('sprintCard');
  if (!el) return;

  // 并行拉取3个API
  const [dnaRes, familyRes, goalsRes] = await Promise.allSettled([
    fetch(`${API_BASE}/investor/dna?userId=${getProfileId()}`, {signal: AbortSignal.timeout(8000)}).then(r => r.ok ? r.json() : null),
    fetch(`${API_BASE}/family/overview`, {signal: AbortSignal.timeout(8000)}).then(r => r.ok ? r.json() : null),
    fetch(`${API_BASE}/goals?userId=${getProfileId()}`, {signal: AbortSignal.timeout(5000)}).then(r => r.ok ? r.json() : null),
  ]);

  const dna = dnaRes.status === 'fulfilled' ? dnaRes.value : null;
  const family = familyRes.status === 'fulfilled' ? familyRes.value : null;
  const goals = goalsRes.status === 'fulfilled' ? goalsRes.value : null;

  // 至少有一个数据才显示卡片
  if (!dna?.available && !family?.total_funds && !goals?.goals?.length) return;

  let html = `<div style="font-size:12px;font-weight:700;margin-bottom:10px;display:flex;align-items:center;justify-content:space-between">
    <span>🧠 AI洞察</span>
    <span style="font-size:10px;font-weight:400;color:var(--text-tertiary,#7A8499);cursor:pointer" onclick="_showSprintDetail()">查看详情 ›</span>
  </div>`;

  // 行1: DNA摘要（一句话）
  if (dna && dna.available) {
    const risk = dna.risk_profile || {};
    const weak = (dna.weaknesses || [])[0] || {};
    const riskColor = risk.level === '激进' ? '#F87171' : risk.level === '进取' ? '#F59E0B' : '#86EFAC';
    html += `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:11px">
      <span style="padding:1px 6px;border-radius:8px;background:rgba(${riskColor === '#F87171' ? '248,113,113' : riskColor === '#F59E0B' ? '245,158,11' : '134,239,172'},.1);color:${riskColor};font-size:10px">${risk.level || '?'}</span>
      <span style="color:var(--text-secondary)">${dna.holding_style?.type || ''} · ${(dna.strong_sectors || []).slice(0, 2).join('/')}</span>
      ${weak.type !== 'none' ? `<span style="color:#F59E0B">⚠️${weak.desc?.slice(0, 10) || ''}</span>` : ''}
    </div>`;
  }

  // 行2: 家庭全景摘要
  if (family && family.total_funds) {
    const warn = (family.warnings || [])[0] || '';
    html += `<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">
      👨‍👩‍👦 家庭${family.total_funds}只基金 · 互补度${family.complementary_score || 0}%
      ${warn && !warn.startsWith('✅') ? ` · <span style="color:#F59E0B">${warn.slice(0, 20)}</span>` : ''}
    </div>`;
  }

  // 行3: 目标进度（如果有）
  if (goals && goals.goals && goals.goals.length) {
    const g = goals.goals[0]; // 只展示第一个目标
    const pct = g.progress_pct || 0;
    const barColor = pct >= 80 ? '#86EFAC' : pct >= 50 ? '#F59E0B' : '#94A3B8';
    html += `<div style="margin-top:4px">
      <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:3px">
        <span>🎯 ${g.name || '目标'}</span>
        <span style="color:var(--text-tertiary)">${pct}%</span>
      </div>
      <div style="height:5px;background:var(--bg3,rgba(0,0,0,.1));border-radius:3px;overflow:hidden">
        <div style="height:100%;width:${Math.min(pct, 100)}%;background:${barColor};border-radius:3px"></div>
      </div>
    </div>`;
  }

  el.innerHTML = html;
  el.style.display = '';

  // 缓存数据供详情弹窗使用
  window._sprintData = {dna, family, goals};
}

// 详情弹窗
function _showSprintDetail() {
  const data = window._sprintData || {};
  const {dna, family, goals} = data;

  let html = '<div style="max-height:70vh;overflow-y:auto;padding:4px">';

  // DNA画像完整
  if (dna && dna.available) {
    const risk = dna.risk_profile || {};
    const hold = dna.holding_style || {};
    const weaknesses = dna.weaknesses || [];
    const sectors = (dna.strong_sectors || []).join(' / ');
    const dd = dna.drawdown_tolerance || {};

    html += `<div style="margin-bottom:16px">
      <h3 style="font-size:13px;font-weight:700;margin:0 0 8px">🧬 投资DNA画像</h3>
      <div style="font-size:12px;line-height:2">
        <div>风险偏好: <b>${risk.level || '?'}</b> — ${risk.desc || ''}</div>
        <div>持有风格: <b>${hold.type || '?'}</b> — ${hold.desc || ''}</div>
        <div>擅长赛道: ${sectors || '未知'}</div>
        <div>回撤容忍: ${dd.level || '?'} (${dd.desc || ''})</div>
      </div>
      ${weaknesses.length ? `<div style="margin-top:6px;padding:6px 10px;background:rgba(245,158,11,.06);border-radius:6px;font-size:11px">
        <div style="font-weight:600;margin-bottom:2px">⚠️ 行为弱点:</div>
        ${weaknesses.map(w => `<div>• ${w.desc}</div>`).join('')}
      </div>` : ''}
    </div>`;
  }

  // 家庭全景完整
  if (family && family.total_funds) {
    const industries = (family.industry_distribution || []).slice(0, 6);
    const overlap = family.overlap_funds || [];
    const warnings = family.warnings || [];

    html += `<div style="margin-bottom:16px">
      <h3 style="font-size:13px;font-weight:700;margin:0 0 8px">👨‍👩‍👦 家庭持仓全景</h3>
      <div style="font-size:12px;line-height:1.8">
        <div>总持仓: ${family.total_funds}只基金 · 互补度: ${family.complementary_score || 0}%</div>
        <div>行业分布: ${industries.map(([n, c]) => `${n}(${c})`).join(' · ')}</div>
        ${overlap.length ? `<div style="color:#F59E0B">重叠持仓: ${overlap.map(f => f.name).join(', ')}</div>` : ''}
      </div>
      <div style="margin-top:6px;font-size:11px;line-height:1.6">${warnings.map(w => `<div>${w}</div>`).join('')}</div>
    </div>`;
  }

  // 目标完整
  if (goals && goals.goals && goals.goals.length) {
    html += `<div style="margin-bottom:16px">
      <h3 style="font-size:13px;font-weight:700;margin:0 0 8px">🎯 财务目标</h3>`;
    for (const g of goals.goals) {
      const pct = g.progress_pct || 0;
      const barColor = pct >= 80 ? '#86EFAC' : pct >= 50 ? '#F59E0B' : '#94A3B8';
      html += `<div style="margin-bottom:10px;font-size:12px">
        <div style="display:flex;justify-content:space-between;margin-bottom:3px"><span>${g.name}</span><span>${pct}% · ¥${(g.saved_estimate || 0).toLocaleString()} / ¥${(g.target_amount || 0).toLocaleString()}</span></div>
        <div style="height:6px;background:var(--bg3,rgba(0,0,0,.1));border-radius:3px;overflow:hidden"><div style="height:100%;width:${Math.min(pct, 100)}%;background:${barColor};border-radius:3px"></div></div>
        ${g.estimated_finish ? `<div style="font-size:10px;color:var(--text-tertiary);margin-top:2px">预计 ${g.estimated_finish} 达成</div>` : ''}
      </div>`;
    }
    html += `<div style="font-size:10px;color:var(--text-tertiary);cursor:pointer" onclick="alert('在AI对话中说「帮我设定一个财务目标，名称XX，金额XX，期限XX」')">+ 设定新目标</div></div>`;
  }

  html += '</div>';

  // 弹窗
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove() };
  overlay.innerHTML = `<div style="background:var(--bg2,#1e293b);border-radius:16px;padding:20px;max-width:400px;width:100%;max-height:80vh;overflow:hidden">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <b style="font-size:14px;color:var(--text,#f1f5f9)">🧠 AI洞察详情</b>
      <button onclick="this.closest('[style*=fixed]').remove()" style="border:none;background:none;color:var(--text-tertiary);font-size:18px;cursor:pointer">×</button>
    </div>
    ${html}
  </div>`;
  document.body.appendChild(overlay);
}

window._showSprintDetail = _showSprintDetail;
