# 03 · 页面改造规范

> 7 个页面，每页给出：要做的核心改动 + 关键 HTML 结构 + 必须用的 CSS 类。
> Claude Code 改代码时，**必须打开** `moneybag-mobile-redesign.html` 对照视觉。

---

## 全局规范（每页都要遵守）

1. **底部导航**：5 Tab — 首页 / 持仓 / 资讯 / AI / 资产（小课、历史移走，见 PR-3）
2. **主题切换**：保留顶部显示器图标，切换 `data-theme="light"` 即可，由 design-tokens.css 自动响应
3. **数字显示**：所有重要金额必须用 `.mb-money` 类（衬线字体），其它数字加 `.mb-numeric`
4. **配色铁律**：金橙=主行动 / 紫=AI / 绿=涨/积极 / 红=跌/警告。**禁止其它色随意用**
5. **emoji 节制**：每张卡最多 1 个 emoji 装饰，不要 emoji 堆砌
6. **空状态**：必须用 `.mb-empty` 类 + 提供至少一个 CTA，不要"暂无数据"四个字了事

---

## 页面 1 · landing.js (首页)

### 信息架构（从上到下）
```
顶部条      头像+问候 + 主题切换 + 专业模式入口
非交易日条  仅非交易日显示
家庭净资产Hero  ¥总额 + 涨跌 pill + 投资/现金/负债 三栏
👨‍👩 家庭账户卡   双 Avatar (L 橙 / B 粉) + 各自占比
💡 今日提醒     紫色 AI 提醒 + 主次行动按钮
快捷网格       4 个 → 资产配置 / 持仓 / 市场全景 / 管理资产
🌤 情绪温度计   表情 + 一句话摘要
🤖 AI 管家入口  绿色光晕卡 + 输入框 (跳转 AI Tab)
```

### 关键 DOM 模板
```html
<!-- 顶部条 -->
<header class="mb-flex mb-flex--between" style="padding:6px 4px 14px">
  <div class="mb-flex mb-gap-4">
    <div class="mb-avatar mb-avatar--md mb-avatar--leijiang">L</div>
    <div>
      <b style="font-size:13px">下午好，LeiJiang</b>
      <div class="mb-eyebrow" style="margin-top:2px">SUNDAY · MAY 17 · 非交易日</div>
    </div>
  </div>
  <div class="mb-flex mb-gap-2">
    <button class="mb-btn mb-btn--secondary mb-btn--sm" data-action="toggle-theme">🌙</button>
    <button class="mb-pill mb-pill--on">专业</button>
  </div>
</header>

<!-- Hero 净资产 -->
<section class="mb-hero">
  <div class="mb-flex mb-flex--between">
    <span class="mb-hero__label">💰 家庭净资产</span>
    <span class="mb-pill mb-pill--secondary" data-action="toggle-money-mask">👁 隐藏</span>
  </div>
  <h1 class="mb-hero__num mb-numeric">¥<span data-bind="netWorth">0</span><small>.00</small></h1>
  <div class="mb-hero__delta">
    <span class="mb-pill mb-pill--bull">▲ +¥0</span>
    <span class="mb-text-tertiary">今日 · 较昨日收盘</span>
  </div>
  <div class="mb-hero__splits">
    <div class="mb-hero__split"><div class="mb-hero__split-label">📈 投资</div><div class="mb-hero__split-value">¥0</div></div>
    <div class="mb-hero__split"><div class="mb-hero__split-label">💵 现金</div><div class="mb-hero__split-value">¥0</div></div>
    <div class="mb-hero__split"><div class="mb-hero__split-label">📋 负债</div><div class="mb-hero__split-value mb-hero__split-value--dn">-¥0</div></div>
  </div>
</section>

<!-- 双账户卡 -->
<section class="mb-card--ghost">
  <div class="mb-flex mb-flex--between mb-mb-3">
    <b style="font-size:12px">👨‍👩 家庭账户</b>
    <span class="mb-text-tertiary" style="font-size:10px">管理 →</span>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
    <div class="mb-card--ghost" style="padding:10px">
      <div class="mb-flex mb-gap-2 mb-mb-1">
        <div class="mb-avatar mb-avatar--xs mb-avatar--leijiang">L</div>
        <b style="font-size:11px">LeiJiang</b>
      </div>
      <div class="mb-money mb-money--sm" data-bind="leijiangAssets">¥0</div>
      <div class="mb-caption">占比 0%</div>
    </div>
    <div class="mb-card--ghost" style="padding:10px">
      <div class="mb-flex mb-gap-2 mb-mb-1">
        <div class="mb-avatar mb-avatar--xs mb-avatar--buluogeli">B</div>
        <b style="font-size:11px">BuLuoGeLi</b>
      </div>
      <div class="mb-money mb-money--sm" data-bind="buluogeliAssets">¥0</div>
      <div class="mb-caption">占比 0%</div>
    </div>
  </div>
</section>
```

### 数据绑定（保留现有 app.js 的 fetch 逻辑，只改渲染选择器）
旧选择器 → 新选择器映射在 `06-migration-guide.md`

---

## 页面 2 · portfolio.js (持仓)

### 改动要点
- **Hero 卡**：总持仓 + 0/0 健康分进度条做成一体
- **家庭总资产卡**：移除！（信息已在首页）保留双账户对比卡
- **股票⇄基金切换**：用 `.mb-pill--on` / `.mb-pill` 双胶囊（不再是大色块按钮）
- **空状态**：`.mb-empty` + 3 个 CTA（添加股票 / 刷新行情 / AI 深度分析）

---

## 页面 3 · insight.js (资讯)

### 改动要点
- **顶部 Tab**：横向滚动胶囊（情绪/推荐/决策/行业/数据）
- **🌡 恐慌贪婪指数**：必须做成**半圆仪表盘 SVG**（参考 redesign HTML 那段 SVG），不再只是数字
- **💎 估值水平**：渐变色条（绿→橙→红），白色标记线指向当前分位
- **技术 + 宏观**：合并为 2x2 网格，每格用 `.mb-money--sm` 显示数据
- **资讯流**：每条带利好/利空/中性 tag（用 `.mb-tag--bull/bear/warn`）

### 恐慌贪婪仪表盘 SVG 模板
```html
<svg viewBox="0 0 200 100" class="mb-fear-gauge">
  <defs>
    <linearGradient id="fgGrad" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%"   stop-color="#FF6B6B"/>
      <stop offset="50%"  stop-color="#FFB755"/>
      <stop offset="100%" stop-color="#00E5A0"/>
    </linearGradient>
  </defs>
  <!-- 底色弧 -->
  <path d="M20,90 A80,80 0 0,1 180,90" fill="none" stroke="rgba(255,255,255,.06)" stroke-width="10"/>
  <!-- 渐变弧（stroke-dashoffset 由 JS 根据 value 计算） -->
  <path d="M20,90 A80,80 0 0,1 180,90" fill="none" stroke="url(#fgGrad)"
        stroke-width="10" stroke-dasharray="251" data-fear-arc/>
  <!-- 指针圆点（cx/cy 由 JS 计算） -->
  <circle r="5" fill="#FFB755" stroke="#fff" stroke-width="2" data-fear-pin/>
</svg>
<!-- JS 计算公式（value: 0~100）：
     dashoffset = 251 * (1 - value/100);
     angle = -180 + (value/100)*180;  // -180~0
     cx = 100 + 80*cos(angle*PI/180);
     cy = 90  + 80*sin(angle*PI/180);
-->
```

---

## 页面 4 · chat.js (AI 分析)

### 改动要点
- **顶部右侧**：模型选择从 `<select>` 改成自定义 `.mb-pill--ai` 下拉
- **4 大师切换**：必须新增！4 个圆头像卡（巴菲特橙红/格雷厄姆深蓝/林奇绿/塔勒布紫），点击切换 system prompt 视角
- **对话气泡**：用 `.mb-bubble--ai` / `.mb-bubble--user`
- **常见问题**：从 10 个减到 5 个 + emoji，用 `.mb-card--ghost` 风格
- **输入框**：固定底部 80px 处，悬浮气泡效果

### 4 大师切换组件
```html
<div class="mb-master-grid" style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px">
  <button class="mb-master mb-master--active" data-master="buffett">
    <div class="mb-avatar mb-avatar--md" style="background:linear-gradient(135deg,#FF8A65,#E64A19)">🎩</div>
    <b>巴菲特</b><small>价值</small>
  </button>
  <button class="mb-master" data-master="graham">
    <div class="mb-avatar mb-avatar--md" style="background:linear-gradient(135deg,#5C6BC0,#283593)">📚</div>
    <b>格雷厄姆</b><small>安全边际</small>
  </button>
  <button class="mb-master" data-master="lynch">
    <div class="mb-avatar mb-avatar--md" style="background:linear-gradient(135deg,#26A69A,#00695C)">🔍</div>
    <b>林奇</b><small>实地研究</small>
  </button>
  <button class="mb-master" data-master="taleb">
    <div class="mb-avatar mb-avatar--md" style="background:linear-gradient(135deg,#7E57C2,#4527A0)">🌪</div>
    <b>塔勒布</b><small>反脆弱</small>
  </button>
</div>
```

后端 chat API 接受 `master` 参数（`buffett`|`graham`|`lynch`|`taleb`），拼接 system prompt。如果后端暂未支持，前端先把 master 名拼到用户消息开头："（请用巴菲特视角回答）..."

---

## 页面 5 · assets.js (资产)

### 改动要点
- **Hero**：统一净资产 + 健康分 pill + 投资/实物/负债 三栏
- **新增 6 类网格**（核心改动）：房产 / 现金 / 保险 / 车辆 / 收藏品 / 负债
  - 每格无数据时显示「未添加」+ 不同色背景图标
  - 点击进入对应分类的添加流程
- **底部加入"我的记录"**：承接砍掉的「历史」Tab，3 条最新分析

### 6 类网格模板
```html
<div class="mb-cat-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">
  <a class="mb-cat" data-cat="property" href="#assets/add?type=property">
    <div class="mb-cat__icon" style="background:rgba(255,183,85,.12)">🏠</div>
    <div class="mb-cat__title">房产</div>
    <div class="mb-cat__value mb-cat__value--empty">未添加</div>
  </a>
  <!-- ... 其它 5 类同结构 -->
</div>
```

---

## 页面 6 · history.js (历史) → 移除底 Tab

不删除文件，只是从底部 nav 隐藏。
保留 `#history` 路由，从 assets 页"我的记录"的"全部 →"链接进入。
未来可考虑改造成「时间轴 + AI 决策回放」（参考 redesign 稿 05），但本次升级只做迁移，不重写。

---

## 页面 7 · quiz.js (小课) → 移到设置页

不删除文件，从底部 nav 隐藏。
在「个人/设置」页（如果没有就新建）加入"📚 财商小课"入口。
本身页面布局保留，但顶部加返回箭头：`<a href="#settings">←</a>`

---

## 共享组件清单（开发可单独抽出来 `pages/_components.js`）

- `renderTopBar(user)` — 头像问候 + 主题切换
- `renderTabBar(active)` — 5 Tab 底栏
- `renderHeroNetWorth(data)` — 净资产 Hero
- `renderDualAccount(leijiang, buluogeli)` — 双账户卡
- `renderAITip(text, actions)` — 紫色 AI 提醒
- `renderQuickGrid(items)` — 4 格快捷
- `renderEmpty(icon, title, desc, ctas)` — 空状态
- `renderFearGreedGauge(value)` — 半圆仪表盘
- `renderMasterPicker(activeMaster)` — 4 大师切换

这些组件可由 PR-1 的「基建」阶段统一实现，后续每个页面 PR 直接调用。
