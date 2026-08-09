# 05 · 验收测试清单

> 每个 PR 完成后，Claude Code 必须自查这些项目，全部 ✅ 才能 commit。
> 用户人工 review 时也按这份 checklist。

---

## PR-1 · 基建

- [ ] `index.html` 加载了 design-tokens.css 和 components.css
- [ ] DevTools → Computed → 能看到 `--color-brand-500: #FFB755`
- [ ] 浅色主题切换：`document.documentElement.setAttribute('data-theme','light')` 后页面变浅
- [ ] **现有页面无视觉变化**（这一步只是注入，不改任何东西）
- [ ] Lighthouse 性能分数无下降

## PR-2 · 共享组件

- [ ] `pages/_components.js` 暴露的 9 个 render 函数能在 console 调用并返回 HTML 字符串
- [ ] 调用 `MB.components.renderHeroNetWorth({netWorth: 12345.67, delta: '+0.65%', splits: [...]})` 渲染正确
- [ ] 主题切换刷新后保持（localStorage）

## PR-3 · Tab 7→5

- [ ] 底部只显示 5 个 Tab：首页 / 持仓 / 资讯 / AI / 资产
- [ ] 5 个 Tab 顺序与设计稿一致
- [ ] 每个 Tab 选中态颜色：首页橙 / 持仓橙 / 资讯紫 / AI 绿 / 资产粉（按 03-page-specs 全局规范）
- [ ] 点击 `#history` 仍能访问，顶部有返回箭头
- [ ] 点击 `#quiz` 仍能访问，顶部有返回箭头

## PR-4 · 首页

- [ ] 顶部头像 = `L` 金橙渐变
- [ ] 问候语根据时间显示「早上好 / 下午好 / 晚上好」
- [ ] Hero 净资产数字使用衬线字体（DM Serif Display）
- [ ] Hero 暖光晕在右上角（不是均匀光照）
- [ ] 双账户卡：L 橙 / B 粉 两个 Avatar 字母
- [ ] 紫色 AI 提醒卡有"稍后处理 / 主行动"两个按钮
- [ ] 4 个快捷格无溢出（375 宽度下）
- [ ] 浅色主题切换后所有文字仍可读
- [ ] **视觉对比**：与 redesign.html 第 01 章 NEW 列差异 < 5%

## PR-5 · 持仓 + 资讯

### 持仓
- [ ] Hero 顶部"总持仓资产"+ 0/100 健康分进度条（渐变色）
- [ ] 双账户卡使用 L/B 字母 Avatar
- [ ] 股票⇄基金切换是 pill 而非大色块按钮
- [ ] 空状态有 3 个 CTA：添加股票 / 刷新行情 / AI 深度分析

### 资讯
- [ ] 顶部 5 个胶囊横向可滚动
- [ ] **恐慌贪婪指数是半圆仪表盘**（不是数字+文字）
- [ ] 仪表盘指针位置匹配数值（用浏览器 console 改 value 验证）
- [ ] 估值水平有渐变色条 + 白色标记线
- [ ] 资讯列表每条都有利好/利空/中性 tag

## PR-6 · AI 分析

- [ ] 4 大师切换卡显示在顶部（4 列网格）
- [ ] 默认选中第 1 个（巴菲特），有橙色高亮边框
- [ ] 点击切换大师，被选中卡变橙色边框
- [ ] AI 气泡（左对齐绿色描边）/ 用户气泡（右对齐金橙底）
- [ ] 输入框固定底部 80px 处
- [ ] 移动端键盘弹起时输入框跟随上移（用 `interactive-keyboard-inset` 或 `visualViewport`）
- [ ] 常见问题精选 5 个 + emoji
- [ ] 切换大师后下次提问，AI 回复可观察到风格变化（手动测一次）

## PR-7 · 资产

- [ ] 顶部 Hero 与首页 Hero 视觉一致但不重复（颜色/光晕略不同）
- [ ] 6 类资产网格：房产 / 现金 / 保险 / 车辆 / 收藏品 / 负债
- [ ] 每类有不同色调图标背景
- [ ] 空状态显示「未添加」+ 点击进入对应添加流程
- [ ] 底部"我的记录"显示最近 3 条
- [ ] "全部 →"链接到 `#history`

## PR-8 · 收尾

- [ ] 所有页面底部留白足够（不被 tabbar 遮挡）
- [ ] 浅色主题下所有文字可读（手动浏览 7 页）
- [ ] 版本号已升级到 v9.3.0：
  - [ ] `index.html` 各 `?v=...` 参数
  - [ ] `sw.js` 缓存版本号
  - [ ] `pyproject.toml` version 字段
- [ ] `pytest backend/tests/` 全绿
- [ ] Lighthouse Mobile 性能 ≥ 90 / 可访问性 ≥ 95

---

## Manual Smoke Test（每个 PR 后必跑）

```
1. 启动后端：cd backend && uvicorn main:app --reload
2. 启动前端：cd .. && python -m http.server 8000
3. 浏览器开 http://localhost:8000，DevTools 模式 iPhone 14 (390x844)
4. 登录 Guest / GUEST2026
5. 依次访问 5 个 Tab，检查无报错
6. 切换深色↔浅色主题，无视觉错乱
7. 点击若干交互元素（添加资产、问 AI、切换大师等），无 console error
```

---

## 自动化测试（可选但推荐）

如果想加 Playwright 视觉回归测试：

```bash
# tests/visual/redesign.spec.ts
test('首页视觉与基线一致', async ({ page }) => {
  await page.goto('/');
  await page.fill('[name="username"]', 'Guest');
  await page.fill('[name="invite"]', 'GUEST2026');
  await page.click('button:has-text("确认")');
  await page.waitForSelector('.mb-hero');
  await expect(page).toHaveScreenshot('home.png', { maxDiffPixels: 1000 });
});
```

每个 PR 后跑一次，截图差异超阈值就 fail。
