# 周度自检（weekly_self_audit.py）调度补齐

> FIX 2026-09-22 · 状态：**等待主理人授权后手工粘贴到服务器 crontab**（本次未改服务器）
> 关联：Bug 3「周度自检从未被调度（孤儿脚本）」

---

## 1. 问题

| 现象 | 证据 |
| --- | --- |
| 运维日报「周度自检」持续告警 | `ops_summary.py:88-94` 巡检项读 `data/audit/latest.json`，`max_stale_days=7`，已 9 天未更新 |
| 脚本在，但没人调它 | 服务器 crontab 无 `weekly_self_audit` 条目；backend 全库 grep `weekly_self_audit` 只有脚本自身，零调用方 |
| 脚本注释里写的 systemd timer 并不存在 | `scripts/weekly_self_audit.py` 头部注释称「systemd timer 每周日凌晨 2 点触发」，服务器上无对应 timer（否则 `latest.json` 不会停更 9 天） |

**根因**：只写了脚本、没接调度，且没人看守「这个脚本到底跑没跑」。

---

## 2. 先确认：脚本能不能写出 `latest.json`？（结论：能，脚本本身无 bug）

调用链（本地代码实证）：

```
scripts/weekly_self_audit.py:main()
  └─ from use_cases.self_audit import run_weekly_audit        # weekly_self_audit.py:34
       └─ run_weekly_audit()                                  # use_cases/self_audit.py:773
            └─ save_audit_report(report)                      # use_cases/self_audit.py:827
                 └─ AUDIT_LATEST.write_text(...)              # self_audit.py:666-668
                    其中 AUDIT_LATEST = DATA_DIR / "audit" / "latest.json"   # self_audit.py:45-47
```

- `DATA_DIR` 来自 `config.py:11`：`Path(os.environ.get("DATA_DIR", str(DEFAULT_DATA_DIR)))`，
  `DEFAULT_DATA_DIR = BACKEND_DIR.parent / "data"` → 生产默认就是 `/opt/moneybag/data`。
- 即：脚本**会**写 `/opt/moneybag/data/audit/latest.json`，与 `ops_summary` 的读取路径一致。
- 唯一的问题是**没有调度**。

### 本次顺带加的兜底（已改代码）

`scripts/weekly_self_audit.py` 新增 `_verify_artifact()`：跑完后校验
`DATA_DIR/audit/latest.json` 存在且 mtime 在 1 小时内，否则打 ERROR 并 **exit 1**。
这样以后再出现「调度掉了 / 审计中途挂了」，cron 退出码 + 日志会直接暴露，
不用靠日报里那个 stale 天数间接猜。

---

## 3. 可直接粘贴的 crontab 条目

```cron
0 2 * * 0 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && export DATA_DIR=/opt/moneybag/data && /opt/moneybag/venv/bin/python scripts/weekly_self_audit.py >> /var/log/moneybag/weekly_self_audit.log 2>&1
```

粘贴方式（服务器上，**ubuntu 用户身份**）：

```bash
crontab -e               # ⚠️ 不加 sudo —— 见下方说明
# 把上面那一行追加到文件末尾，保存退出
crontab -l | grep weekly_self_audit        # 确认已写入
```

> ⚠️ **必须用 ubuntu 用户的 crontab，切勿 `sudo crontab -e`**（2026-09-22 服务器上核实）：
> - MoneyBag 的**全部** cron 任务都在 **ubuntu 用户 crontab** 下；
> - **root crontab 只有 stargate**，与本项目无关；`/etc/cron.d` 下也没有 moneybag 条目。
>
> 用 `sudo crontab -e` 会贴进 root 的 crontab，后果不只是「位置不对」：
> 1. 脚本以 **root 身份**运行，写出的 `data/audit/latest.json` 属主变成 root，而该目录现有文件都是 ubuntu（`/opt/moneybag/data` 是 `drwxrwsr-x ubuntu ubuntu`），后续 ubuntu 身份的进程可能**写不进去**；
> 2. root 下没有与 ubuntu 相同的环境变量上下文，反而更容易踩历史上「cron 侧缺 `DATA_DIR` → data 目录分裂」那类坑。
>
> 若 `/var/log/moneybag` 不存在，先 `sudo mkdir -p /var/log/moneybag`（建目录这一步才需要 sudo）。

---

## 4. 条目各段为什么这么写

| 片段 | 原因 |
| --- | --- |
| `0 2 * * 0` | 每周日 02:00。与脚本原始设计意图（周日凌晨 2 点）一致，且**不与现有周日任务冲突**：周日已有 `0 20 * * 0`（broker_rating_cron + dca weekly）、`0 21 * * 0`（weekly_plan_cron）、`0 22 * * 0`（fund_rank_build）；night_worker 是 `0 1 * * 1-5`（工作日），housekeeping `30 4 * * *`，memory_archive `0 4 1 * *` —— 周日 02:00 是空的。自检耗时 2-5 分钟（含 LLM 审计），02:00 跑完不影响任何下游。 |
| `cd /opt/moneybag/backend` | 脚本内 `sys.path.insert(0, backend)` 依赖自身定位，但 cwd 保持一致可让 `.env`、相对日志路径行为与其余条目一致 |
| `set -a && . /opt/moneybag/backend/.env && set +a` | 与现有全部条目同一风格（`docs/ops/crontab.production.txt`）。自检要调 LLM（`module="self_audit"`）和企微推送，必须有 Key |
| `export DATA_DIR=/opt/moneybag/data` | ⚠️ **项目历史事故根因**：cron 侧缺 `DATA_DIR` 时 `config.py` 会回落到默认值，一旦 cwd/路径变了就会读相对路径、造成 data 目录分裂（`/opt/moneybag/data` 与 `/opt/moneybag/backend/data` 两套）。这里**在 `.env` 之后显式 export**，保证与 systemd 注入给 API 进程的权威值一致 |
| `/opt/moneybag/venv/bin/python` | 用 venv 解释器，与其余条目一致（不能写 `python3`，会拿到系统解释器、缺依赖） |
| `>> /var/log/moneybag/weekly_self_audit.log 2>&1` | 独立日志文件，方便事后 `grep ERROR`；不混进 `cron.log`（那个文件按天追加，会干扰 `ops_summary` 的 24h 错误计数口径） |

---

## 5. 上线后验证（次日 08:05 日报前可自查）

```bash
# 1) 调度已生效（ubuntu 用户 crontab，不加 sudo）
crontab -l | grep weekly_self_audit
# 顺带确认没有误贴进 root crontab（预期：无输出）
sudo crontab -l -u root | grep weekly_self_audit

# 2) 手动跑一次（不依赖 cron 触发，立刻验证链路；同样用 ubuntu 身份）
cd /opt/moneybag/backend && set -a && . .env && set +a && export DATA_DIR=/opt/moneybag/data \
  && /opt/moneybag/venv/bin/python scripts/weekly_self_audit.py; echo "exit=$?"

# 3) 产物已落盘（mtime 应为刚才）
ls -l --time-style=long-iso /opt/moneybag/data/audit/latest.json
/opt/moneybag/venv/bin/python - <<'PY'
import json
d = json.load(open('/opt/moneybag/data/audit/latest.json'))
print(d['date'], d['overall_status'], d['stats'].get('health_score'))
PY

# 4) 日报口径同步归零（第二天 08:03 快照之后）
/opt/moneybag/venv/bin/python - <<'PY'
import json, glob
f = sorted(glob.glob('/opt/moneybag/data/ops/snapshot_*.json'))[-1]
snap = json.load(open(f))
print([(r['name'], r['stale_days'], r['ok']) for r in snap['freshness']])
PY
```

预期：`周度自检` 那一行 `stale_days=0~1`、`ok=true`；日报里「周度自检失效」告警消失。

---

## 6. 影响面 / 风险

- **只读 + 写一个文件**：只写 `/opt/moneybag/data/audit/latest.json` 与 `audit/history/{date}.json`，不动业务数据。
- **成本**：每周一次 LLM 审计（`model_tier="llm_heavy"`）+ 一次企微推送（当日去重，不会重复推）。
- **时段**：周日 02:00 无任何并发任务；即使自检失败也不会阻塞别的 cron（自身 2-5 分钟内结束）。
- **回滚**：`crontab -e` 删掉这一行即可（ubuntu 用户身份，不加 sudo），无任何持久化副作用。
- **属主**：以 ubuntu 身份运行，产出的 `data/audit/latest.json` 属主与目录现有文件一致（ubuntu），不会出现 root 属主把后续 ubuntu 进程挡在门外的问题。
