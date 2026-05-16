// 行为历史页面 (Phase 3 Batch 3)
// 展示用户交易行为事件、检测的行为模式、市场背景等

async function renderBehaviorHistory() {
  const main = document.getElementById('app');
  if (!main) return;

  main.innerHTML = `
    <div class="page-header">
      <h1>📊 行为历史</h1>
      <p style="color: var(--text2); margin-top: 4px">你的交易行为记录和模式分析</p>
    </div>

    <div id="behaviorContainer" style="padding: 0 16px 20px">
      <div style="text-align: center; padding: 32px 0; color: var(--text2)">
        加载中...
      </div>
    </div>
  `;

  try {
    const response = await fetch(`/api/behavior/events?userId=${getProfileId()}&limit=50`);
    const data = await response.json();
    
    const statsResponse = await fetch(`/api/behavior/stats?userId=${getProfileId()}`);
    const stats = await statsResponse.json();
    
    renderBehaviorContent(data, stats);
  } catch (e) {
    console.error('[behavior-history] Error:', e);
    document.getElementById('behaviorContainer').innerHTML = `
      <div style="text-align: center; padding: 32px 0; color: var(--red)">
        加载失败: ${e.message}
      </div>
    `;
  }
}

function renderBehaviorContent(data, stats) {
  const container = document.getElementById('behaviorContainer');
  const { events, today_count, total } = data;

  if (!events || events.length === 0) {
    container.innerHTML = `
      <div style="text-align: center; padding: 40px 16px">
        <div style="font-size: 48px; margin-bottom: 16px">📭</div>
        <div style="font-size: 16px; color: var(--text1); margin-bottom: 8px">
          还没有行为记录
        </div>
        <div style="font-size: 13px; color: var(--text2)">
          进行交易后，系统会自动记录你的行为模式
        </div>
      </div>
    `;
    return;
  }

  let html = '';

  // 统计卡片
  html += renderBehaviorStats(stats, today_count, total);

  // 模式分布
  if (stats && stats.pattern_distribution && Object.keys(stats.pattern_distribution).length > 0) {
    html += renderPatternDistribution(stats.pattern_distribution);
  }

  // 过滤器（将来可添加）
  html += '<div style="margin: 16px 0; display: flex; gap: 8px; flex-wrap: wrap">';
  html += '<button onclick="filterBehaviorByPattern(null)" style="' + getFilterButtonStyle(true) + '">全部</button>';
  
  const patterns = stats && stats.pattern_distribution ? Object.keys(stats.pattern_distribution) : [];
  patterns.forEach(pattern => {
    html += `<button onclick="filterBehaviorByPattern('${pattern}')" style="` + getFilterButtonStyle(false) + `">${getPatternLabel(pattern)}</button>`;
  });
  
  html += '</div>';

  // 事件时间线
  html += '<div style="margin-top: 20px">';
  html += '<div style="font-size: 14px; font-weight: bold; margin-bottom: 12px; color: var(--text1)">📜 交易记录</div>';
  
  events.forEach((event, index) => {
    html += renderBehaviorEvent(event, index);
  });
  
  html += '</div>';

  container.innerHTML = html;
}

function renderBehaviorStats(stats, today_count, total) {
  if (!stats) {
    return `
      <div style="margin-bottom: 16px; padding: 12px; background: var(--bg2); border-radius: 8px">
        <div style="display: flex; justify-content: space-between; align-items: center">
          <div>
            <div style="font-size: 12px; color: var(--text2)">今日记录</div>
            <div style="font-size: 24px; font-weight: bold; color: var(--blue)">${today_count}</div>
          </div>
          <div>
            <div style="font-size: 12px; color: var(--text2)">总记录数</div>
            <div style="font-size: 24px; font-weight: bold; color: var(--text1)">${total}</div>
          </div>
        </div>
      </div>
    `;
  }

  return `
    <div style="margin-bottom: 16px; padding: 12px; background: var(--bg2); border-radius: 8px">
      <div style="display: flex; justify-content: space-between; align-items: center">
        <div>
          <div style="font-size: 12px; color: var(--text2)">今日</div>
          <div style="font-size: 24px; font-weight: bold; color: var(--blue)">${stats.today_event_count || 0}</div>
        </div>
        <div>
          <div style="font-size: 12px; color: var(--text2)">总数</div>
          <div style="font-size: 24px; font-weight: bold; color: var(--text1)">${stats.total_events_tracked || 0}</div>
        </div>
        <div>
          <div style="font-size: 12px; color: var(--text2)">模式类型</div>
          <div style="font-size: 24px; font-weight: bold; color: var(--purple)">${Object.keys(stats.pattern_distribution || {}).length}</div>
        </div>
      </div>
    </div>
  `;
}

function renderPatternDistribution(patterns) {
  let html = '<div style="margin-bottom: 20px">';
  html += '<div style="font-size: 14px; font-weight: bold; margin-bottom: 12px; color: var(--text1)">🎯 行为模式分布</div>';
  
  const total = Object.values(patterns).reduce((a, b) => a + b, 0);
  const sorted = Object.entries(patterns).sort((a, b) => b[1] - a[1]);
  
  sorted.forEach(([pattern, count]) => {
    const percentage = ((count / total) * 100).toFixed(1);
    const color = getPatternColor(pattern);
    
    html += `
      <div style="margin-bottom: 12px">
        <div style="display: flex; justify-content: space-between; margin-bottom: 4px">
          <span style="font-size: 13px; color: var(--text1)">${getPatternLabel(pattern)}</span>
          <span style="font-size: 13px; color: var(--text2)">${count} (${percentage}%)</span>
        </div>
        <div style="background: var(--bg1); border-radius: 4px; overflow: hidden; height: 6px">
          <div style="background: ${color}; height: 100%; width: ${percentage}%"></div>
        </div>
      </div>
    `;
  });
  
  html += '</div>';
  return html;
}

function renderBehaviorEvent(event, index) {
  const patterns = event.patterns_detected || [];
  const marketContext = event.market_context || {};
  const tradeDetails = event.trade_details || {};
  
  const timestamp = new Date(event.recorded_at);
  const timeStr = formatEventTime(timestamp);
  const dateStr = formatEventDate(timestamp);
  
  const direction = tradeDetails.direction === 'buy' ? '买入' : '卖出';
  const directionColor = tradeDetails.direction === 'buy' ? '#4CAF50' : '#FF6B6B';
  const code = tradeDetails.code || '未知代码';
  const amount = tradeDetails.amount ? `${tradeDetails.amount.toFixed(2)}` : '-';
  const price = tradeDetails.price ? `¥${tradeDetails.price.toFixed(2)}` : '-';

  return `
    <div style="
      background: var(--bg2);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 12px;
      border-left: 3px solid ${directionColor};
    ">
      <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px">
        <div style="flex: 1">
          <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px">
            <span style="
              font-weight: bold;
              color: white;
              background: ${directionColor};
              padding: 2px 8px;
              border-radius: 4px;
              font-size: 12px;
            ">${direction}</span>
            <span style="font-size: 14px; font-weight: bold; color: var(--text1)">${code}</span>
          </div>
          <div style="font-size: 12px; color: var(--text2); margin-bottom: 4px">
            数量: ${amount} | 价格: ${price}
          </div>
          <div style="font-size: 11px; color: var(--text2)">
            ${timeStr} · ${dateStr}
          </div>
        </div>
      </div>

      <!-- 行为模式标签 -->
      ${patterns.length > 0 ? `
        <div style="display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px">
          ${patterns.map(pattern => `
            <span style="
              font-size: 11px;
              background: rgba(255, 193, 7, 0.1);
              color: #FFC107;
              padding: 3px 8px;
              border-radius: 4px;
              border: 1px solid #FFC107;
            ">${getPatternLabel(pattern)}</span>
          `).join('')}
        </div>
      ` : ''}

      <!-- 市场背景 -->
      ${Object.keys(marketContext).length > 0 ? `
        <div style="font-size: 11px; color: var(--text2); background: var(--bg1); padding: 6px 8px; border-radius: 4px">
          ${marketContext.fgi_score ? `😨 恐贪指数: ${marketContext.fgi_score}` : ''}
          ${marketContext.market_change ? `📈 市场涨跌: ${(marketContext.market_change > 0 ? '+' : '')}${marketContext.market_change.toFixed(2)}%` : ''}
          ${marketContext.valuation_percentile ? `💰 估值位置: ${marketContext.valuation_percentile}分位` : ''}
        </div>
      ` : ''}
    </div>
  `;
}

function getFilterButtonStyle(active) {
  if (active) {
    return `
      background: var(--blue);
      color: white;
      border: none;
      border-radius: 4px;
      padding: 6px 12px;
      font-size: 12px;
      cursor: pointer;
    `;
  }
  return `
    background: var(--bg1);
    color: var(--text2);
    border: 1px solid var(--bg3);
    border-radius: 4px;
    padding: 6px 12px;
    font-size: 12px;
    cursor: pointer;
  `;
}

function formatEventTime(date) {
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${hours}:${minutes}`;
}

function formatEventDate(date) {
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  
  const isToday = date.toDateString() === now.toDateString();
  const isYesterday = date.toDateString() === yesterday.toDateString();
  
  if (isToday) {
    return '今天';
  } else if (isYesterday) {
    return '昨天';
  } else {
    const month = date.getMonth() + 1;
    const day = date.getDate();
    return `${month}月${day}日`;
  }
}

function getPatternLabel(pattern) {
  const patternMap = {
    'chasing_high': '追涨',
    'panic_selling': '恐慌卖',
    'fomo': 'FOMO风险',
    'emotional_trading': '情绪交易',
    'high_frequency': '频繁交易',
    'concentration': '集中持仓',
    'overweight': '超配',
    'underweight': '低配',
  };
  return patternMap[pattern] || pattern;
}

function getPatternColor(pattern) {
  const colorMap = {
    'chasing_high': '#FF9800',
    'panic_selling': '#F44336',
    'fomo': '#E91E63',
    'emotional_trading': '#9C27B0',
    'high_frequency': '#673AB7',
    'concentration': '#3F51B5',
    'overweight': '#2196F3',
    'underweight': '#00BCD4',
  };
  return colorMap[pattern] || '#9E9E9E';
}

async function filterBehaviorByPattern(pattern) {
  // 重新加载，这次带上 pattern 参数
  const main = document.getElementById('app');
  if (!main) return;

  main.innerHTML = `
    <div class="page-header">
      <h1>📊 行为历史</h1>
      <p style="color: var(--text2); margin-top: 4px">你的交易行为记录和模式分析</p>
    </div>

    <div id="behaviorContainer" style="padding: 0 16px 20px">
      <div style="text-align: center; padding: 32px 0; color: var(--text2)">
        加载中...
      </div>
    </div>
  `;

  try {
    let url = `/api/behavior/events?userId=${getProfileId()}&limit=50`;
    if (pattern) {
      url += `&pattern=${pattern}`;
    }
    
    const response = await fetch(url);
    const data = await response.json();
    
    const statsResponse = await fetch(`/api/behavior/stats?userId=${getProfileId()}`);
    const stats = await statsResponse.json();
    
    renderBehaviorContent(data, stats);
  } catch (e) {
    console.error('[behavior-history] Filter error:', e);
    document.getElementById('behaviorContainer').innerHTML = `
      <div style="text-align: center; padding: 32px 0; color: var(--red)">
        过滤失败: ${e.message}
      </div>
    `;
  }
}
