# 06 · 旧 → 新 迁移映射

> Claude Code 改代码时，把旧的硬编码值替换为新的 var()。
> 这份文档**不是设计参考**，是机械替换字典。

---

## 颜色硬编码 → CSS 变量

### 现有 styles.css 里出现的颜色

| 旧值 | 出现位置（grep 关键字） | 新值 | 备注 |
|---|---|---|---|
| `#FFB347` `#FFB755` `#FF7E5F` | 主色 | `var(--color-brand-500)` | 渐变用 `var(--color-brand-gradient)` |
| `#FFB755` 任何渐变背景 | hero / 按钮 | `var(--color-brand-gradient)` | |
| `#0F172A` `#0F1118` | body 背景 | `var(--bg-base)` | |
| `#1A1D26` `#1A1D24` | 卡片背景 | `var(--bg-elevated)` | |
| `#0E1F36` 海军蓝 | （沉稳风的） | 删除 | 不用 |
| `#D4AF37` 金 | （沉稳风的） | 删除 | 不用 |
| `#8B6FE6` `#B89DFF` | AI 紫 | `var(--color-ai-500)` / `var(--color-ai-300)` | |
| `#00E5A0` `#43A574` 绿 | 涨/正面 | `var(--color-bull)` | |
| `#FF6B6B` `#C25450` `#FF1F1F` 红 | 跌/负面 | `var(--color-bear)` | |
| `#FFB800` 黄 | 警示 | `var(--color-warn)` | |
| `#fff` `white` `#FFFFFF` | 主文字 | `var(--text-primary)` | |
| `#bbb` `#ccc` `#aab` | 副文字 | `var(--text-default)` | |
| `#9aa1ac` `#A8B0C0` | 二级灰 | `var(--text-secondary)` | |
| `#7A8499` `#7C8499` `#737a85` | 三级灰 | `var(--text-tertiary)` | |

### 检查命令
```bash
cd ~/moneybag-for-claudecode
# 找出所有硬编码颜色（含 # 的 hex 值）
grep -rn '#[0-9a-fA-F]\{3,8\}' styles.css pages/*.js | grep -v 'design-tokens\|components.css'
# 应该全部能在上表找到对应替换
```

---

## 字号硬编码 → CSS 变量

| 旧值 | 新值 |
|---|---|
| `10px` `9px` | `var(--fs-xs)` |
| `11px` | `var(--fs-sm)` |
| `12px` `13px` | `var(--fs-base)` |
| `14px` `15px` | `var(--fs-md)` |
| `16px` `18px` | `var(--fs-lg)` |
| `20px` `22px` | `var(--fs-xl)` |
| `24px` `28px` | `var(--fs-2xl)` |
| `32px` `38px` | `var(--fs-3xl)` |
| `42px` `46px` `48px` | `var(--fs-4xl)` |

---

## 间距硬编码 → CSS 变量

把所有 `padding/margin/gap` 的 px 值替换：

| 旧值 | 新值 |
|---|---|
| `4px`        | `var(--space-1)` |
| `6px`        | `var(--space-2)` |
| `8px`        | `var(--space-3)` |
| `10px`       | `var(--space-4)` |
| `12px`       | `var(--space-5)` |
| `14px`       | `var(--space-6)` |
| `16px`       | `var(--space-7)` |
| `20px`       | `var(--space-8)` |
| `24px`       | `var(--space-9)` |
| `32px`       | `var(--space-10)` |

---

## 旧 CSS 类 → 新 CSS 类（如果现有 styles.css 有以下类）

| 旧类名 | 新类名 | 说明 |
|---|---|---|
| `.card` `.box` | `.mb-card` | 默认卡片 |
| `.btn-primary` | `.mb-btn .mb-btn--primary` | |
| `.btn-secondary` | `.mb-btn .mb-btn--secondary` | |
| `.btn-ghost` | `.mb-btn .mb-btn--ghost` | |
| `.tag` `.badge` | `.mb-tag` 加 `--bull/bear/warn` | |
| `.up` `.green` | `.mb-text-up` | 涨 |
| `.down` `.red` | `.mb-text-dn` | 跌 |
| `.amount` `.money` | `.mb-money mb-money--{xs|sm|md|lg|xl}` | 衬线大数字 |
| `.tabbar` `.bottom-nav` | `.mb-tabbar` | |
| `.tab-item` | `.mb-tabbar__item` | |
| `.tab-item.active` | `.mb-tabbar__item--active` | |
| `.empty` `.no-data` | `.mb-empty` | |

---

## 字体声明替换

```css
/* 旧 */
font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;

/* 新 */
font-family: var(--font-sans);
```

```css
/* 重要金额数字（从前没有专门处理） */
/* 新增 */
font-family: var(--font-display);
font-weight: 400;
letter-spacing: var(--ls-tight);
```

---

## 圆角替换

| 旧值 | 新值 |
|---|---|
| `4px` | `var(--radius-xs)` |
| `6px` `8px` | `var(--radius-sm)` |
| `10px` `12px` | `var(--radius-md)` |
| `14px` `16px` | `var(--radius-lg)` |
| `18px` `20px` | `var(--radius-xl)` |
| `22px` `24px` | `var(--radius-2xl)` |
| `999px` | `var(--radius-pill)` |

---

## 阴影替换

| 旧值（任何 box-shadow） | 新值 |
|---|---|
| 小阴影 | `var(--shadow-sm)` |
| 中等阴影 | `var(--shadow-md)` |
| 大阴影 | `var(--shadow-lg)` |
| 卡片悬浮阴影 | `var(--shadow-xl)` |
| 金色光晕 | `var(--shadow-glow-brand)` |
| 紫色光晕（AI） | `var(--shadow-glow-ai)` |

---

## 一键替换脚本（谨慎使用）

```bash
#!/bin/bash
# replace-colors.sh — 在 ~/moneybag-for-claudecode 跑
# 警告：先 git commit 当前状态，再跑这个脚本

cd ~/WorkBuddy/moneybag-for-claudecode
sed -i.bak \
  -e 's/#FFB347/var(--color-brand-500)/g' \
  -e 's/#FFB755/var(--color-brand-500)/g' \
  -e 's/#0F172A/var(--bg-base)/g' \
  -e 's/#1A1D26/var(--bg-elevated)/g' \
  -e 's/#00E5A0/var(--color-bull)/g' \
  -e 's/#FF6B6B/var(--color-bear)/g' \
  styles.css pages/*.js

# 检查替换结果
git diff --stat
echo "如果差异符合预期，删除 .bak 文件：rm styles.css.bak pages/*.js.bak"
```

⚠️ **警告**：sed 替换是机械的，可能误改字符串里的颜色（比如 alert 文案里写了 "#FFB755 就是金色"）。跑完必须人工 review `git diff`。

---

## 替换优先级

1. **先替换设计 tokens 引用**（颜色/字号/间距）— 影响大、易跑通
2. **再替换组件类**（.card → .mb-card 等）— 需要配合 HTML 改动
3. **最后处理特殊场景**（动画、SVG 内联样式等）— 通常不多

完成所有替换后，理论上 `styles.css` 的体积会减少（因为大量重复的硬编码值被变量代替），且未来改色只需要改 design-tokens.css 一处。
