// 待办页面 (Phase 3 Batch 3)
// 展示用户待办任务列表、完成状态、规则触发原因等

async function renderTodos() {
  const main = document.getElementById('app');
  if (!main) return;

  main.innerHTML = `
    <div class="page-header">
      <h1>📋 待办任务</h1>
      <p style="color: var(--text2); margin-top: 4px">本周的财务任务清单</p>
    </div>

    <div id="todosContainer" style="padding: 0 16px 20px">
      <div style="text-align: center; padding: 32px 0; color: var(--text2)">
        加载中...
      </div>
    </div>
  `;

  try {
    const response = await fetch(`/api/todos?userId=${getProfileId()}`);
    const data = await response.json();
    
    renderTodosList(data);
  } catch (e) {
    console.error('[todos] Error:', e);
    document.getElementById('todosContainer').innerHTML = `
      <div style="text-align: center; padding: 32px 0; color: var(--red)">
        加载失败: ${e.message}
      </div>
    `;
  }
}

function renderTodosList(data) {
  const container = document.getElementById('todosContainer');
  const { todos, open_count, total } = data;

  if (!todos || todos.length === 0) {
    container.innerHTML = `
      <div style="text-align: center; padding: 40px 16px">
        <div style="font-size: 48px; margin-bottom: 16px">✅</div>
        <div style="font-size: 16px; color: var(--text1); margin-bottom: 8px">
          没有待办任务
        </div>
        <div style="font-size: 13px; color: var(--text2)">
          保持你的投资计划，继续前进！
        </div>
      </div>
    `;
    return;
  }

  // 按状态分组
  const openTodos = todos.filter(t => t.status === 'open');
  const completedTodos = todos.filter(t => t.status === 'completed');

  let html = `
    <div style="margin-bottom: 16px; padding: 12px; background: var(--bg2); border-radius: 8px">
      <div style="display: flex; justify-content: space-between; align-items: center">
        <div>
          <div style="font-size: 12px; color: var(--text2)">待完成</div>
          <div style="font-size: 24px; font-weight: bold; color: var(--blue)">${open_count}</div>
        </div>
        <div>
          <div style="font-size: 12px; color: var(--text2)">已完成</div>
          <div style="font-size: 24px; font-weight: bold; color: var(--green)">${completedTodos.length}</div>
        </div>
        <div>
          <div style="font-size: 12px; color: var(--text2)">总计</div>
          <div style="font-size: 24px; font-weight: bold; color: var(--text1)">${total}</div>
        </div>
      </div>
    </div>
  `;

  // 待完成列表
  if (openTodos.length > 0) {
    html += '<div style="margin-bottom: 24px">';
    html += '<div style="font-size: 14px; font-weight: bold; margin-bottom: 12px; color: var(--text1)">📌 待完成</div>';
    
    openTodos.forEach(todo => {
      html += renderTodoItem(todo);
    });
    
    html += '</div>';
  }

  // 已完成列表
  if (completedTodos.length > 0) {
    html += '<div style="margin-bottom: 24px">';
    html += '<div style="font-size: 14px; font-weight: bold; margin-bottom: 12px; color: var(--text2)">✓ 已完成</div>';
    
    completedTodos.forEach(todo => {
      html += renderTodoItem(todo, true);
    });
    
    html += '</div>';
  }

  container.innerHTML = html;
}

function renderTodoItem(todo, isCompleted = false) {
  const dueSoon = isCompleted ? false : isDueSoon(todo.due_by);
  const dueText = formatDueDate(todo.due_by);
  
  const ruleLabel = getRuleLabel(todo.rule_triggered);

  return `
    <div style="
      background: var(--bg2);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 8px;
      border-left: 3px solid ${isCompleted ? 'var(--green)' : dueSoon ? 'var(--red)' : 'var(--blue)'};
      opacity: ${isCompleted ? 0.6 : 1};
      text-decoration: ${isCompleted ? 'line-through' : 'none'};
    ">
      <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 12px">
        <div style="flex: 1">
          <div style="font-size: 14px; font-weight: 500; color: var(--text1); margin-bottom: 4px">
            ${todo.title}
          </div>
          <div style="display: flex; gap: 8px; flex-wrap: wrap">
            <span style="
              font-size: 11px;
              background: var(--bg1);
              padding: 3px 8px;
              border-radius: 4px;
              color: var(--text2);
            ">
              ${ruleLabel}
            </span>
            ${dueText ? `<span style="
              font-size: 11px;
              background: ${dueSoon ? 'rgba(255, 67, 54, 0.1)' : 'rgba(33, 150, 243, 0.1)'};
              padding: 3px 8px;
              border-radius: 4px;
              color: ${dueSoon ? 'var(--red)' : 'var(--blue)'};
            ">
              ${dueText}
            </span>` : ''}
          </div>
        </div>
        <div style="display: flex; gap: 8px">
          ${!isCompleted ? `
            <button onclick="markTodoDone('${todo.id}')" style="
              background: var(--green);
              color: white;
              border: none;
              border-radius: 4px;
              padding: 6px 12px;
              font-size: 12px;
              cursor: pointer;
            ">
              完成
            </button>
          ` : ''}
          <button onclick="deleteTodo('${todo.id}')" style="
            background: var(--bg1);
            color: var(--text2);
            border: none;
            border-radius: 4px;
            padding: 6px 12px;
            font-size: 12px;
            cursor: pointer;
          ">
            删除
          </button>
        </div>
      </div>
    </div>
  `;
}

function isDueSoon(dueDate) {
  if (!dueDate) return false;
  const due = new Date(dueDate);
  const now = new Date();
  const daysUntilDue = (due - now) / (1000 * 60 * 60 * 24);
  return daysUntilDue <= 1 && daysUntilDue > -1;
}

function formatDueDate(dueDate) {
  if (!dueDate) return '';
  
  const due = new Date(dueDate);
  const now = new Date();
  const diff = (due - now) / (1000 * 60 * 60 * 24);
  
  if (diff < 0) {
    return '已过期';
  } else if (diff < 1) {
    return '今天截止';
  } else if (diff < 2) {
    return '明天截止';
  } else if (diff <= 7) {
    return Math.ceil(diff) + '天后截止';
  } else {
    return '';
  }
}

function getRuleLabel(ruleTriggered) {
  const ruleMap = {
    'allocation_deviation_gt_15': '配置偏离',
    'weekly_review': '周末复盘',
    'accounting_overdue': '记账提醒',
    'no_target_config': '目标配置',
    'behavior_alert_fomo': 'FOMO风险',
    'behavior_alert_chasing': '追涨提醒',
    'rebalance_opportunity': '再平衡机会',
  };
  return ruleMap[ruleTriggered] || ruleTriggered;
}

async function markTodoDone(todoId) {
  try {
    const response = await fetch(`/api/todos/${todoId}/done`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userId: getProfileId() })
    });
    const data = await response.json();
    if (data.ok) {
      renderTodos(); // 刷新列表
    }
  } catch (e) {
    console.error('[todos] Mark done failed:', e);
    alert('操作失败');
  }
}

async function deleteTodo(todoId) {
  if (!confirm('确认删除该待办项？')) return;
  
  try {
    const response = await fetch(`/api/todos/${todoId}?userId=${getProfileId()}`, {
      method: 'DELETE',
    });
    const data = await response.json();
    if (data.ok) {
      renderTodos(); // 刷新列表
    }
  } catch (e) {
    console.error('[todos] Delete failed:', e);
    alert('删除失败');
  }
}
