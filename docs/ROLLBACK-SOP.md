# MoneyBag 数据回滚 SOP

> 目标：5 分钟内恢复到 M1 重构前的状态
> 最后更新：2026-04-26

---

## 回滚触发条件

- M1 拆路由导致核心功能不可用
- DATA_DIR 数据被误删或损坏
- 需要放弃当前 M1 改动，回到重构前基线

---

## 回滚步骤（3 步，≤5 分钟）

### Step 1：停服务（30 秒）

```bash
# 找到并停止当前运行的服务
ps aux | grep -E "python.*main|uvicorn|gunicorn" | grep -v grep
kill <PID>           # 或 pkill -f "python backend/main.py"
```

### Step 2：回滚代码（1 分钟）

```bash
cd ~/WorkBuddy/moneybag-for-claudecode

# 回滚到 M1 重构前最后一个稳定版本
git checkout pre-refactor

# 或如果要保留当前分支，新建回滚分支
git checkout -b rollback-$(date +%Y%m%d) pre-refactor
```

### Step 3：恢复数据（2 分钟）

```bash
cd ~/WorkBuddy/moneybag-for-claudecode/backend

# 备份当前数据（以防万一）
mv data data-broken-$(date +%Y%m%d-%H%M%S)

# 恢复 M1 前数据
cp -r data-backup-pre-m1 data

# 启动服务
cd ~/WorkBuddy/moneybag-for-claudecode/backend
python main.py        # 或你的启动命令
```

---

## 验证回滚成功

- [ ] 服务启动无报错
- [ ] 首页能打开
- [ ] 登录正常
- [ ] 持仓数据可见
- [ ] AI 分析能跑通

---

## 备选方案

如果 `pre-refactor` 标签不可用：

```bash
# 基于日期回滚（M1 W1 开始于 2026-04-25 凌晨）
git log --before="2026-04-25" --oneline -1
# 找到最后一个 commit，手动 checkout
```

---

## 关键信息

| 项目 | 位置 |
|------|------|
| 回滚代码基线 | `git tag: pre-refactor` → commit `00082ab` |
| 回滚数据备份 | `backend/data-backup-pre-m1/` |
| 当前 M1 W1 完成状态 | `git tag: m1-w1-skeleton` → commit `29c4d18` |
