// ============================================================
// v9.7.0 每周周报（完整版）
// ============================================================
// 入口：insight tab=weekly （已挂在 insight 顶 tab 列表）
// 数据：后端 API 生成完整9大模块周报

window.renderWeeklyReport = async function(el){
  if(!el) return;

  // 1. 显示加载状态
  el.innerHTML = `<div class="dashboard-card" style="overflow:hidden">
    <div class="dashboard-card-title">📋 钱袋子周报</div>
    <div id="weeklyBody">
      <div style="text-align:center;padding:40px">
        <div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 12px;border-width:3px"></div>
        <div style="font-size:13px;color:var(--text2)">正在生成周报...</div>
        <div style="font-size:11px;color:var(--text-tertiary);margin-top:6px">汇总持仓、计算收益、分析风险</div>
      </div>
    </div>
  </div>`;

  const body = document.getElementById('weeklyBody');
  if(!body) return;

  // 2. 调用后端 API
  const userId = getProfileId();
  if(!userId){
    body.innerHTML = `<div style="text-align:center;padding:30px;color:var(--text2)">⚠️ 请先登录</div>`;
    return;
  }

  let reportData = null;
  try {
    const url = API_BASE + '/weekly-report?userId=' + encodeURIComponent(userId) + '&weeks_ago=0';
    const resp = await fetch(url, { signal: AbortSignal.timeout(30000) });
    if(resp.ok){
      reportData = await resp.json();
    }
  } catch(e) {
    console.error('[WEEKLY] API 调用失败:', e);
  }

  // 3. 展示报告
  if(!reportData || reportData.error){
    body.innerHTML = `<div style="text-align:center;padding:30px;color:var(--text2)">
      <div style="font-size:32px;margin-bottom:12px">📊</div>
      <div>周报生成失败</div>
      <div style="font-size:11px;color:var(--text-tertiary);margin-top:6px">${reportData?.error || '网络错误'}</div>
      <button onclick="renderWeeklyReport(document.getElementById('weeklyEl'))" class="mb-btn mb-btn--primary" style="margin-top:16px;padding:8px 20px;font-size:12px">重试</button>
    </div>`;
    return;
  }

  // 4. 渲染 narrative 文本（AI 生成的高质量报告）
  const narrative = reportData.narrative || '';
  const period = reportData.period || '';

  // 将 narrative 文本转换为 HTML
  const formattedReport = _formatNarrativeToHTML(narrative, period);

  body.innerHTML = `<div style="padding:16px">
    ${formattedReport}
  </div>

  <div style="padding:12px 16px;background:rgba(99,102,241,.05);border-top:1px solid rgba(99,102,241,.1)">
    <button onclick="renderWeeklyReport(document.getElementById('weeklyEl'))" class="mb-btn" style="padding:6px 14px;font-size:11px;background:transparent;border:1px solid var(--border);color:var(--text2);cursor:pointer;margin-right:8px">🔄 刷新</button>
    <button onclick="_downloadWeeklyReport()" class="mb-btn" style="padding:6px 14px;font-size:11px;background:transparent;border:1px solid var(--border);color:var(--text2);cursor:pointer">📥 下载</button>
  </div>`;

  // 5. 保存报告数据供下载使用
  window._currentWeeklyReport = reportData;
};

// ============================================================
// 辅助函数：将 narrative 文本转换为格式化的 HTML
// ============================================================
function _formatNarrativeToHTML(narrative, period) {
  if (!narrative) return '<div style="color:var(--text2)">暂无周报数据</div>';

  // 按行分割
  const lines = narrative.split('\n');
  let html = '';
  let inList = false;
  let inCodeBlock = false;

  // 添加标题
  html += `<div style="margin-bottom:20px">
    <div style="font-size:20px;font-weight:800;color:var(--text-default);margin-bottom:4px">📋 钱袋子周报</div>
    <div style="font-size:12px;color:var(--text2)">${period} · 自动生成</div>
  </div>`;

  html += '<div style="font-size:13px;line-height:1.8;color:var(--text-default)">';

  for (let line of lines) {
    const trimmed = line.trim();

    // 跳过空行
    if (!trimmed) {
      if (inList) {
        html += '</div>';
        inList = false;
      }
      html += '<div style="height:8px"></div>';
      continue;
    }

    // 标题行（以 emoji 开头）
    if (/^[\u{1F300}-\u{1F9FF}]/u.test(trimmed)) {
      if (inList) {
        html += '</div>';
        inList = false;
      }

      // 提取 emoji 和标题
      const match = trimmed.match(/^([\u{1F300}-\u{1F9FF}])\s*(.+)$/u);
      if (match) {
        const emoji = match[1];
        const title = match[2];
        html += `<div style="font-size:15px;font-weight:700;color:var(--text-default);margin-top:16px;margin-bottom:8px;padding-bottom:6px;border-bottom:2px solid rgba(99,102,241,.2)">${emoji} ${title}</div>`;
      } else {
        html += `<div style="font-size:15px;font-weight:700;color:var(--text-default);margin-top:16px;margin-bottom:8px">${trimmed}</div>`;
      }
      continue;
    }

    // 列表项（以 • 或 - 或数字开头）
    if (/^[•\-–—]/.test(trimmed) || /^\d+\./.test(trimmed)) {
      if (!inList) {
        html += '<div style="padding-left:12px">';
        inList = true;
      }
      const content = trimmed.replace(/^[•\-–—]\s*/, '').replace(/^\d+\.\s*/, '');
      html += `<div style="padding:3px 0;font-size:12px;color:var(--text2)">• ${content}</div>`;
      continue;
    }

    // 普通文本行（可能是缩进的内容）
    if (inList) {
      html += '</div>';
      inList = false;
    }

    // 处理缩进的文本（通常是列表项的详细描述）
    if (line.startsWith('   ') || line.startsWith('\t')) {
      const content = trimmed;
      html += `<div style="padding:4px 0 4px 16px;font-size:12px;color:var(--text2);line-height:1.6">${content}</div>`;
    } else {
      // 普通段落
      html += `<div style="padding:4px 0;font-size:13px;color:var(--text-default);line-height:1.6">${trimmed}</div>`;
    }
  }

  if (inList) {
    html += '</div>';
  }

  html += '</div>';

  return html;
}

// ============================================================
// 下载周报
// ============================================================
window._downloadWeeklyReport = function() {
  const data = window._currentWeeklyReport;
  if (!data) return;

  const content = data.narrative || JSON.stringify(data, null, 2);
  const period = data.period || 'weekly';
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `钱袋子周报_${period.replace('/', '-')}.txt`;
  a.click();
  URL.revokeObjectURL(url);
};

// ============================================================
// 兼容旧版本的函数（如果被其他模块调用）
// ============================================================
window._askAIWeeklyReview = function(){
  if(typeof navigateTo==='function'){
    navigateTo('chat');
    setTimeout(()=>{
      const inp = document.getElementById('chatIn');
      if(inp){
        inp.value = '帮我复盘本周（基于我的持仓和操作）：1) 本周收益是否符合预期 2) 操作是否有改进空间 3) 下周需要关注的风险信号。请基于我的持仓上下文输出，不要给具体仓位百分比和价格预测。';
        inp.focus();
      }
    }, 400);
  }
};
