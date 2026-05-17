// ---- 设置页 (v9.3.0 PR-3 新建) ----
// 最小可用版本：主题切换 + 财商小课入口 + 分析历史入口
function renderSettings(){
  currentPage='settings';
  renderNav();
  const themeLabel = _currentTheme === 'light' ? '☀️ 浅色' : _currentTheme === 'dark' ? '🌙 深色' : '🖥️ 跟随系统';
  $('#app').innerHTML=`<div class="result-page fade-up">
<div style="display:flex;align-items:center;gap:10px;margin-bottom:20px">
<a onclick="navigateTo('landing')" style="font-size:18px;cursor:pointer;color:var(--text2);text-decoration:none">←</a>
<h2 style="font-size:18px;font-weight:700;margin:0">⚙️ 设置</h2>
</div>

<div style="background:var(--card,var(--bg-elevated));border-radius:12px;overflow:hidden;border:1px solid var(--bg3,rgba(255,255,255,.06))">

<div style="padding:14px 16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--bg3,rgba(255,255,255,.06));cursor:pointer" onclick="cycleTheme();renderSettings()">
<div style="display:flex;align-items:center;gap:10px">
<span style="font-size:18px">🎨</span>
<div><div style="font-size:14px;font-weight:600">主题</div><div style="font-size:11px;color:var(--text2)">当前：${themeLabel}</div></div>
</div>
<span style="color:var(--text2)">›</span>
</div>

<div style="padding:14px 16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--bg3,rgba(255,255,255,.06));cursor:pointer" onclick="navigateTo('weekly-lesson')">
<div style="display:flex;align-items:center;gap:10px">
<span style="font-size:18px">📚</span>
<div><div style="font-size:14px;font-weight:600">财商小课</div><div style="font-size:11px;color:var(--text2)">每周一课，提升投资认知</div></div>
</div>
<span style="color:var(--text2)">›</span>
</div>

<div style="padding:14px 16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--bg3,rgba(255,255,255,.06));cursor:pointer" onclick="navigateTo('history')">
<div style="display:flex;align-items:center;gap:10px">
<span style="font-size:18px">📋</span>
<div><div style="font-size:14px;font-weight:600">分析历史</div><div style="font-size:11px;color:var(--text2)">查看所有 AI 分析记录</div></div>
</div>
<span style="color:var(--text2)">›</span>
</div>

<div style="padding:14px 16px;display:flex;justify-content:space-between;align-items:center;cursor:pointer" onclick="showProfileSettings()">
<div style="display:flex;align-items:center;gap:10px">
<span style="font-size:18px">👤</span>
<div><div style="font-size:14px;font-weight:600">个人信息</div><div style="font-size:11px;color:var(--text2)">账号绑定、企微推送、清缓存</div></div>
</div>
<span style="color:var(--text2)">›</span>
</div>

</div>

<div style="text-align:center;margin-top:24px;font-size:11px;color:var(--text2)">
钱袋子 v9.3.0 · AI 做管家，人做 CFO
</div>
</div>`;
}
