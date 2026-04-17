# MoneyBag 开发环境 Skill 安装清单

> 换电脑 / 新环境开发前，按此清单安装所有必要 Skill。
> 最后更新：2026-04-17

## 前置条件

- Node.js 已安装（`npx` 可用）
- WorkBuddy 已安装

## 一键检查（跑这个看缺什么）

```bash
echo "=== 检查 MoneyBag 必要 Skill ===" && \
for s in hermanlei-conventions moneybag-dev fastapi-async-patterns pwa-development \
         prompt-engineering-expert macro-monitor deep-research llm-wiki \
         agent-team-orchestration frontend-design; do \
  if [ -d "$HOME/.workbuddy/skills/$s" ]; then \
    echo "  ✅ $s"; \
  else \
    echo "  ❌ $s — 需要安装"; \
  fi; \
done
```

## 安装方法

### 自建 Skill（从 git 仓库复制）

这两个在 git 仓库的 `docs/skills/` 里有备份：

```bash
# 1. moneybag-dev（MoneyBag 专属铁律+路由表）
mkdir -p ~/.workbuddy/skills/moneybag-dev
cp docs/skills/moneybag-dev-SKILL.md ~/.workbuddy/skills/moneybag-dev/SKILL.md

# 2. hermanlei-conventions（通用铁律）
# 这个通常 WorkBuddy 内置有，检查：
ls ~/.workbuddy/skills/hermanlei-conventions/SKILL.md
# 如果没有，从 git 仓库复制铁律速查：
mkdir -p ~/.workbuddy/skills/hermanlei-conventions/references/shared-memory
cp docs/skills/rules-quickref.md ~/.workbuddy/skills/hermanlei-conventions/references/shared-memory/
```

### 本地市场 Skill（从 WorkBuddy 市场复制）

```bash
# 这些在 WorkBuddy 本地市场有，直接复制：
for s in prompt-engineering-expert macro-monitor deep-research llm-wiki agent-team-orchestration; do
  if [ -d "$HOME/.workbuddy/skills-marketplace/skills/$s" ]; then
    cp -r "$HOME/.workbuddy/skills-marketplace/skills/$s" "$HOME/.workbuddy/skills/$s"
    echo "✅ $s 已安装"
  else
    echo "⚠️ $s 不在本地市场，需要远程安装"
  fi
done
```

### 远程 Skill（从 skills.sh 安装）

```bash
# fastapi-async-patterns (679 安装量)
npx --yes skills add thebushidocollective/han@fastapi-async-patterns -g -y
# 安装后链接到 workbuddy：
ln -sf ~/.agents/skills/fastapi-async-patterns ~/.workbuddy/skills/fastapi-async-patterns

# pwa-development (1.1K 安装量)
npx --yes skills add alinaqi/claude-bootstrap@pwa-development -g -y
ln -sf ~/.agents/skills/pwa-development ~/.workbuddy/skills/pwa-development
```

### 已内置 Skill（通常不用装）

以下通常 WorkBuddy 自带，只需确认存在：
- `frontend-design`
- `code-simplifier`
- `github`

## 安装后验证

重新跑一次检查脚本，全部 ✅ 即可开始开发。

## Skill 用途速查

| Skill | 用途 | 什么时候用 |
|-------|------|-----------|
| hermanlei-conventions | 通用铁律 22 条 | 每次编码 |
| moneybag-dev | MoneyBag 铁律 M1-M10 + 路由表 | 每次编码 |
| fastapi-async-patterns | 后端 async/shield/错误处理 | V6/V7 后端改动 |
| pwa-development | 前端 SW/缓存/离线/推送 | V6 前端接入 |
| prompt-engineering-expert | Prompt 优化/结构化输出 | V6/V7/V8 prompt 设计 |
| macro-monitor | 宏观数据自动采集 | V6 凌晨预热 |
| deep-research | 结构化深度调研 | V6/V6.5 地缘/行业调研 |
| llm-wiki | LLM 增量知识库 | V8 复盘经验沉淀 |
| agent-team-orchestration | 多 Agent 编排 | V9/V10+ |
| frontend-design | UI/UX 设计 | 前端改动时 |
