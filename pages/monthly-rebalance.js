// 月度再平衡页面 (Phase 3 Batch 3)
// 展示月度资产快照、趋势数据、再平衡建议

async function renderMonthlyRebalance() {
  const main = document.getElementById('app');
  if (!main) return;

  main.innerHTML = `
    <div class="page-header">
      <h1>🔄 月度再平衡</h1>
      <p style="color: var(--text2); margin-top: 4px">资产快照和再平衡建议</p>
    </div>

    <div id="rebalanceContainer" style="padding: 0 16px 20px">
      <div style="text-align: center; padding: 32px 0; color: var(--text2)">
        加载中...
      </div>
    </div>
  `;

  try {
    // 并行获取多个数据源
    const [snapshotsResp, trendResp, suggestionsResp, latestResp] = await Promise.all([
      fetch(`/api/monthly/snapshots?userId=${getProfileId()}&months=12`),
      fetch(`/api/monthly/trend?userId=${getProfileId()}&months=12`),
      fetch(`/api/monthly/suggestions?userId=${getProfileId()}`),
      fetch(`/api/monthly/latest?userId=${getProfileId()}`)
    ]);

    const snapshots = await snapshotsResp.json();
    const trend = await trendResp.json();
    const suggestions = await suggestionsResp.json();
    const latest = await latestResp.json();

    renderRebalanceContent(snapshots, trend, suggestions, latest);
  } catch (e) {
    console.error('[monthly-rebalance] Error:', e);
    document.getElementById('rebalanceContainer').innerHTML = `
      <div style="text-align: center; padding: 32px 0; color: var(--red)">
        加载失败: ${e.message}
      </div>
    `;
  }
}

function renderRebalanceContent(snapshots, trend, suggestions, latest) {
  const container = document.getElementById('rebalanceContainer');
  let html = '';

  // 1. 最新净资产卡片
  if (latest && latest.snapshot) {
    html += renderLatestSnapshot(latest.snapshot);
  }

  // 2. 月度趋势
  if (trend && trend.trend_data && trend.trend_data.length > 0) {
    html += renderMonthlyTrend(trend.trend_data);
  }

  // 3. 再平衡建议
  if (suggestions && suggestions.suggestions && suggestions.suggestions.length > 0) {
    html += renderRebalanceSuggestions(suggestions.suggestions);
  }

  // 4. 历史快照表格
  if (snapshots && snapshots.snapshots && snapshots.snapshots.length > 0) {
    html += renderSnapshotsTable(snapshots.snapshots);
  }

  if (!html) {
    html = `
      <div style="text-align: center; padding: 40px 16px">
        <div style="font-size: 48px; margin-bottom: 16px">📅</div>
        <div style="font-size: 16px; color: var(--text1); margin-bottom: 8px">
          还没有月度快照
        </div>
        <div style="font-size: 13px; color: var(--text2)">
          每月初会自动保存资产快照，稍候再来查看
        </div>
      </div>
    `;
  }

  container.innerHTML = html;
}

function renderLatestSnapshot(snapshot) {
  if (!snapshot) return '';

  const netWorth = snapshot.net_worth || 0;
  const month = snapshot.month || new Date().toISOString().substring(0, 7);
  const allocation = snapshot.allocation || {};

  return `
    <div style="margin-bottom: 20px">
      <div style="font-size: 12px; font-weight: bold; color: var(--text2); margin-bottom: 8px">
        最新快照 · ${month}
      </div>
      <div style="background: linear-gradient(135deg, var(--blue) 0%, var(--purple) 100%); border-radius: 12px; padding: 20px; color: white">
        <div style="font-size: 13px; opacity: 0.9; margin-bottom: 8px">家庭净资产</div>
        <div style="font-size: 32px; font-weight: bold; margin-bottom: 16px">
          ¥${formatNumber(netWorth)}
        </div>
        
        <div style="display: flex; gap: 16px; margin-top: 16px">
          ${allocation.stock !== undefined ? `
            <div>
              <div style="font-size: 12px; opacity: 0.9">股票</div>
              <div style="font-size: 18px; font-weight: bold">${(allocation.stock * 100).toFixed(0)}%</div>
            </div>
          ` : ''}
          ${allocation.bond !== undefined ? `
            <div>
              <div style="font-size: 12px; opacity: 0.9">债券</div>
              <div style="font-size: 18px; font-weight: bold">${(allocation.bond * 100).toFixed(0)}%</div>
            </div>
          ` : ''}
          ${allocation.cash !== undefined ? `
            <div>
              <div style="font-size: 12px; opacity: 0.9">现金</div>
              <div style="font-size: 18px; font-weight: bold">${(allocation.cash * 100).toFixed(0)}%</div>
            </div>
          ` : ''}
        </div>
      </div>
    </div>
  `;
}

function renderMonthlyTrend(trendData) {
  if (!trendData || trendData.length === 0) return '';

  // 取最近 6 个月
  const recentTrend = trendData.slice(-6);
  
  const maxNW = Math.max(...recentTrend.map(t => t.net_worth || 0));
  const minNW = Math.min(...recentTrend.map(t => t.net_worth || 0));
  const range = maxNW - minNW || maxNW;

  return `
    <div style="margin-bottom: 20px; background: var(--bg2); border-radius: 12px; padding: 16px">
      <div style="font-size: 14px; font-weight: bold; color: var(--text1); margin-bottom: 16px">
        📈 净资产趋势（最近 6 月）
      </div>

      <div style="display: flex; align-items: flex-end; gap: 6px; height: 120px">
        ${recentTrend.map(item => {
          const nw = item.net_worth || 0;
          const height = range > 0 ? ((nw - minNW) / range) * 100 : 50;
          const returnRate = item.returns || 0;
          const returnColor = returnRate > 0 ? '#4CAF50' : returnRate < 0 ? '#F44336' : '#9E9E9E';
          
          return `
            <div style="flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px">
              <div style="
                width: 100%;
                height: ${height}%;
                background: linear-gradient(180deg, var(--blue) 0%, rgba(33, 150, 243, 0.3) 100%);
                border-radius: 4px;
                min-height: 8px;
              " title="${item.month}: ¥${formatNumber(nw)}"></div>
              <div style="font-size: 10px; color: var(--text2); text-align: center; width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis">
                ${item.month}
              </div>
              ${item.returns !== undefined && item.returns !== 0 ? `
                <div style="font-size: 9px; color: ${returnColor}; font-weight: bold">
                  ${returnRate > 0 ? '+' : ''}${(returnRate * 100).toFixed(1)}%
                </div>
              ` : ''}
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}

function renderRebalanceSuggestions(suggestions) {
  if (!suggestions || suggestions.length === 0) return '';

  return `
    <div style="margin-bottom: 20px">
      <div style="font-size: 14px; font-weight: bold; color: var(--text1); margin-bottom: 12px">
        💡 再平衡建议
      </div>

      ${suggestions.map(suggestion => {
        const level = suggestion.level || 'info';
        const levelColors = {
          'danger': { bg: 'rgba(244, 67, 54, 0.1)', text: '#F44336', icon: '🔴' },
          'warning': { bg: 'rgba(255, 152, 0, 0.1)', text: '#FF9800', icon: '🟡' },
          'info': { bg: 'rgba(33, 150, 243, 0.1)', text: '#2196F3', icon: '🔵' },
        };
        const colors = levelColors[level] || levelColors['info'];

        return `
          <div style="
            background: ${colors.bg};
            border-left: 3px solid ${colors.text};
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 12px;
          ">
            <div style="display: flex; gap: 8px; align-items: flex-start">
              <div style="font-size: 18px; line-height: 1.4">${colors.icon}</div>
              <div style="flex: 1">
                <div style="font-size: 13px; font-weight: 500; color: ${colors.text}; margin-bottom: 4px">
                  ${suggestion.action || ''}
                </div>
                ${suggestion.reason ? `
                  <div style="font-size: 12px; color: var(--text2)">
                    ${suggestion.reason}
                  </div>
                ` : ''}
              </div>
            </div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

function renderSnapshotsTable(snapshots) {
  if (!snapshots || snapshots.length === 0) return '';

  return `
    <div style="margin-bottom: 20px">
      <div style="font-size: 14px; font-weight: bold; color: var(--text1); margin-bottom: 12px">
        📋 历史快照
      </div>

      <div style="overflow-x: auto">
        <table style="width: 100%; font-size: 12px; border-collapse: collapse">
          <thead>
            <tr style="border-bottom: 2px solid var(--bg3)">
              <th style="text-align: left; padding: 8px; color: var(--text2); font-weight: bold">月份</th>
              <th style="text-align: right; padding: 8px; color: var(--text2); font-weight: bold">净资产</th>
              <th style="text-align: center; padding: 8px; color: var(--text2); font-weight: bold">股票%</th>
              <th style="text-align: center; padding: 8px; color: var(--text2); font-weight: bold">债券%</th>
              <th style="text-align: center; padding: 8px; color: var(--text2); font-weight: bold">现金%</th>
            </tr>
          </thead>
          <tbody>
            ${snapshots.map(snapshot => {
              const allocation = snapshot.allocation || {};
              return `
                <tr style="border-bottom: 1px solid var(--bg3); background: var(--bg2)">
                  <td style="padding: 8px; color: var(--text1)">${snapshot.month}</td>
                  <td style="text-align: right; padding: 8px; color: var(--text1); font-weight: bold">
                    ¥${formatNumber(snapshot.net_worth || 0)}
                  </td>
                  <td style="text-align: center; padding: 8px; color: var(--text2)">
                    ${allocation.stock ? (allocation.stock * 100).toFixed(0) : '-'}%
                  </td>
                  <td style="text-align: center; padding: 8px; color: var(--text2)">
                    ${allocation.bond ? (allocation.bond * 100).toFixed(0) : '-'}%
                  </td>
                  <td style="text-align: center; padding: 8px; color: var(--text2)">
                    ${allocation.cash ? (allocation.cash * 100).toFixed(0) : '-'}%
                  </td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function formatNumber(num) {
  if (num >= 1000000) {
    return (num / 1000000).toFixed(1) + 'M';
  } else if (num >= 10000) {
    return (num / 10000).toFixed(1) + 'W';
  }
  return num.toFixed(0);
}
