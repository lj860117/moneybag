#!/usr/bin/env python3
"""check_ci.py — 在本机核验 GitHub Actions 的 CI 结果（默认零配置、免 token）

为什么需要它
------------
跑完 `git push` 之后，"CI 绿了吗" 这件事原先在本机**无法回答**：
  - 本机没装 `gh`，也没有 `GITHUB_TOKEN`；
  - 匿名 REST API（api.github.com）走的是共享出口 IP，60 次/小时
    的配额常被别的进程吃光，返回 403 `API rate limit exceeded`
    —— 这个 403 **看起来像"私仓无权限"，实际只是限流**，
    极易被误读成"没有凭据就查不了"。

实际可行的路（本脚本走的就是这条）
----------------------------------
`lj860117/moneybag` 是**公开仓库**，因此 `github.com` 的网页
（不是 api.github.com）**服务端渲染**了完整信息，可以直接解析：
  * `/actions/workflows/<file>` 的运行列表 —— 每行含
    `href=".../commit/<40位sha>"`（可绑定到具体提交）与
    `aria-label="completed successfully:  Run NNN of <workflow>. <commit 标题>"`；
  * `/actions/runs/<id>` 的作业列表 —— 每个 job 的成功/失败由图标
    class（`color-fg-success` / `color-fg-danger`）体现。
这两条路都**不消耗 API 配额、不需要任何凭据**，所以适合当默认路径。

若环境里存在 `GITHUB_TOKEN` / `GH_TOKEN`，本脚本优先走 REST API
（更权威，且能查到更早的提交）；可用 `--force-html` 强制走网页路径。

用法
----
    python3 scripts/check_ci.py                     # 核当前 HEAD
    python3 scripts/check_ci.py --sha 1bc5fb1       # 核指定提交
    python3 scripts/check_ci.py --workflow deploy-pages.yml
    python3 scripts/check_ci.py --pages 3           # 往前多翻几页
    python3 scripts/check_ci.py --json              # 机器可读

退出码
------
    0 全部成功    1 有失败    2 未找到 / 仍在跑    3 访问失败
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

TIMEOUT = 25
_UA = {"User-Agent": "moneybag-check-ci/1.0 (+local dev tooling)"}

_GREEN, _RED, _YEL, _DIM, _RST = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

# aria-label 前缀 → 归一化结论
_CONCLUSION_MAP = {
    "completed successfully": "success",
    "completed with failure": "failure",
    "in progress": "pending",
    "queued": "pending",
    "pending": "pending",
    "cancelled": "cancelled",
    "skipped": "skipped",
}


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="replace")


def _detect_repo() -> str:
    """从 git remote 推断 owner/repo（同时兼容 SSH 与 HTTPS 写法）。"""
    out = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", out)
    if not m:
        raise SystemExit(f"无法从 origin 解析出 owner/repo: {out!r}")
    return f"{m.group(1)}/{m.group(2)}"


def _head_sha(short: bool = False) -> str:
    cmd = ["git", "rev-parse"] + (["--short"] if short else []) + ["HEAD"]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def _normalize(label: str) -> str:
    low = label.strip().lower()
    for prefix, norm in _CONCLUSION_MAP.items():
        if low.startswith(prefix):
            return norm
    return "unknown"


def parse_run_rows(page_html: str, repo: str) -> list[dict]:
    """把运行列表页解析成 [{sha, run_id, number, conclusion, title, url}]。

    每行以 `class="Box-row js-socket-channel"` 起头；行内同时含有
    commit 链接（绑定 sha）与 aria-label（结论 + Run 号 + 提交标题）。
    """
    rows: list[dict] = []
    # 注意：真实属性值后面还挂着别的 class
    # （`class="Box-row js-socket-channel js-updatable-content"`），
    # 所以**不能**带上收尾引号去 split，否则一行都匹配不到。
    for block in page_html.split('class="Box-row js-socket-channel')[1:]:
        block = block[:8000]  # 行内足够，避免跨行误匹配
        sha_m = re.search(rf'/{re.escape(repo)}/commit/([0-9a-f]{{40}})', block)
        run_m = re.search(rf'/{re.escape(repo)}/actions/runs/(\d+)', block)
        lab_m = re.search(r'aria-label="([^"]*)"', block)
        if not (sha_m and run_m and lab_m):
            continue
        label = _html.unescape(lab_m.group(1))
        num_m = re.search(r"Run (\d+) of ([^.]*)\.\s*(.*)$", label)
        rows.append({
            "sha": sha_m.group(1),
            "run_id": run_m.group(1),
            "number": num_m.group(1) if num_m else "?",
            "workflow": (num_m.group(2).strip() if num_m else ""),
            "title": (num_m.group(3).strip() if num_m else ""),
            "conclusion": _normalize(label.split(":")[0]),
            "label": label,
            "url": f"https://github.com/{repo}/actions/runs/{run_m.group(1)}",
        })
    return rows


def parse_jobs(run_html: str, repo: str) -> list[dict]:
    """解析 run 详情页的作业列表。结论靠图标 class：success/danger。"""
    parts = re.split(rf'href="/{re.escape(repo)}/actions/runs/\d+/job/(\d+)"', run_html)
    jobs: list[dict] = []
    # parts = [前言, job_id1, 段1, job_id2, 段2, ...]
    for i in range(1, len(parts) - 1, 2):
        jid, seg = parts[i], parts[i + 1][:4000]
        name_m = re.search(r'data-target="streaming-graph-job\.name">\s*([^<]+?)\s*<', seg)
        if not name_m:
            continue
        if "color-fg-success" in seg:
            concl = "success"
        elif "color-fg-danger" in seg:
            concl = "failure"
        else:
            concl = "unknown"
        dur_m = re.search(r'color-fg-muted flex-shrink-0 pl-1">\s*([\d]+[smh][^<]*)<', seg)
        jobs.append({
            "job_id": jid,
            "name": _html.unescape(name_m.group(1)).strip(),
            "conclusion": concl,
            "duration": dur_m.group(1).strip() if dur_m else "",
        })
    return jobs


def collect_html(repo: str, workflow: str, sha: str, pages: int) -> tuple[list[dict], list[dict]]:
    """返回 (run 行列表, 目标 run 的 job 列表)。"""
    rows: list[dict] = []
    for p in range(1, pages + 1):
        url = f"https://github.com/{repo}/actions/workflows/{workflow}?page={p}"
        try:
            page = _fetch(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise SystemExit(
                    f"❌ 抓不到 {url}（HTTP 404）—— 仓库可能是私有的，"
                    "网页路径不可用；请改用 GITHUB_TOKEN 或 `gh auth login`。"
                )
            raise
        found = parse_run_rows(page, repo)
        if not found:
            break
        rows.extend(found)
        if any(r["sha"].startswith(sha) for r in found):
            break
    target = next((r for r in rows if r["sha"].startswith(sha)), None)
    jobs: list[dict] = []
    if target:
        try:
            jobs = parse_jobs(_fetch(target["url"]), repo)
        except Exception as e:  # 作业列表拿不到不影响主结论
            print(f"{_YEL}  (作业明细获取失败: {e}){_RST}", file=sys.stderr)
    return rows, jobs


def collect_api(repo: str, workflow: str, sha: str, token: str) -> tuple[dict, list[dict]]:
    """REST API 路径（需要 token）。返回 (run dict, job 列表)。"""
    import urllib.request as u

    def api(path: str):
        req = u.Request(f"https://api.github.com/repos/{repo}{path}", headers={
            **_UA, "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        })
        with u.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())

    runs = api(f"/actions/workflows/{workflow}/runs?per_page=50")
    run = next((r for r in runs.get("workflow_runs", [])
                if r["head_sha"].startswith(sha)), None)
    if not run:
        return {}, []
    job_list = api(f"/actions/runs/{run['id']}/jobs").get("jobs", [])
    norm = {
        "sha": run["head_sha"], "run_id": str(run["id"]),
        "number": str(run["run_number"]), "workflow": run["name"],
        "title": (run.get("head_commit") or {}).get("message", "").split("\n")[0],
        "conclusion": run.get("conclusion") or "pending",
        "url": run["html_url"],
        "label": f"{run.get('conclusion')}: Run {run['run_number']} of {run['name']}",
    }
    jobs = [{
        "job_id": str(j["id"]), "name": j["name"],
        "conclusion": j.get("conclusion") or "pending",
        "duration": "",
    } for j in job_list]
    return norm, jobs


def check_one(repo: str, workflow: str, sha: str, token: str,
              pages: int, force_html: bool) -> tuple[dict | None, list[dict], str]:
    """核一个工作流。返回 (run 或 None, jobs, 所用路径说明)。"""
    if token and not force_html:
        run, jobs = collect_api(repo, workflow, sha, token)
        return (run or None), jobs, f"REST API (token …{token[-4:]})"
    rows, jobs = collect_html(repo, workflow, sha, pages)
    return next((r for r in rows if r["sha"].startswith(sha)), None), jobs, "网页解析（免 token、不耗 API 配额）"


def _workflow_list(args) -> list[str]:
    if args.all_workflows:
        wf_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              ".github", "workflows")
        files = sorted(f for f in os.listdir(wf_dir) if f.endswith((".yml", ".yaml")))
        if not files:
            raise SystemExit(f"❌ {wf_dir} 下没找到工作流文件")
        return files
    return [w.strip() for w in args.workflow.split(",") if w.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="核验 GitHub Actions CI 结果（默认免 token）")
    ap.add_argument("--repo", default=None, help="owner/repo（默认从 git origin 推断）")
    ap.add_argument("--sha", default=None, help="要核的提交（默认 HEAD，可用短 sha）")
    ap.add_argument("--workflow", default="ci.yml",
                    help="工作流文件名，可逗号分隔（默认 ci.yml）")
    ap.add_argument("--all-workflows", action="store_true",
                    help="核 .github/workflows/ 下的全部工作流")
    ap.add_argument("--pages", type=int, default=1, help="往前翻页数（默认 1）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--force-html", action="store_true", help="即使有 token 也走网页路径")
    args = ap.parse_args()

    repo = args.repo or _detect_repo()
    sha = args.sha or _head_sha(short=True)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    workflows = _workflow_list(args)

    results = []
    for wf in workflows:
        try:
            run, jobs, route = check_one(repo, wf, sha, token, args.pages, args.force_html)
        except SystemExit:
            raise
        except Exception as e:
            print(f"{_YEL}⚠️  {wf}: 查询失败 {e}{_RST}", file=sys.stderr)
            results.append({"workflow": wf, "run": None, "jobs": [], "error": str(e)})
            continue
        results.append({"workflow": wf, "run": run, "jobs": jobs, "route": route})

    # ---- 汇总退出码：任一失败 → 1；任一无结果/在跑 → 2；全绿 → 0 ----
    worst = 0
    for r in results:
        run = r["run"]
        if not run:
            worst = max(worst, 2)
            continue
        bad = [j for j in r["jobs"] if j["conclusion"] not in ("success", "skipped")]
        if run["conclusion"] != "success" or bad:
            worst = max(worst, 2 if run["conclusion"] in ("pending", "unknown") else 1)

    if args.json:
        print(json.dumps({
            "repo": repo, "sha": sha, "ok": worst == 0,
            "results": [{
                "workflow": r["workflow"], "route": r.get("route", ""),
                "found": bool(r["run"]),
                "run": ({k: r["run"][k] for k in
                         ("run_id", "number", "conclusion", "url", "title")}
                        if r["run"] else None),
                "jobs": r["jobs"],
            } for r in results],
        }, ensure_ascii=False, indent=2))
        return worst

    print(f"{_DIM}仓库 {repo} · 提交 {sha}{_RST}")
    for r in results:
        run, jobs = r["run"], r["jobs"]
        if not run:
            print(f"\n{_YEL}⚠️  {r['workflow']}：最近 {args.pages} 页里没有这个提交的运行{_RST}")
            print(f"{_DIM}   可能还在排队 / 已翻页之前 / 该提交未触发此工作流{_RST}")
            continue
        icon = {"success": f"{_GREEN}✅ success{_RST}",
                "failure": f"{_RED}❌ failure{_RST}"}.get(run["conclusion"],
                                                        f"{_YEL}⏳ {run['conclusion']}{_RST}")
        print(f"\n{run['workflow'] or r['workflow']}  {icon}  Run #{run['number']}")
        print(f"{_DIM}  {run['title'][:90]}{_RST}")
        print(f"{_DIM}  {run['url']}{_RST}")
        if jobs:
            print(f"  作业 {len(jobs)} 个（绿 {sum(1 for j in jobs if j['conclusion'] == 'success')}）:")
            for j in jobs:
                mark = {"success": f"{_GREEN}✅{_RST}",
                        "failure": f"{_RED}❌{_RST}"}.get(j["conclusion"], f"{_YEL}⏳{_RST}")
                dur = f"  {_DIM}{j['duration']}{_RST}" if j["duration"] else ""
                print(f"    {mark} {j['name']}{dur}")

    if worst == 0:
        print(f"\n{_GREEN}✅ 全部工作流通过{_RST}")
    elif worst == 2:
        print(f"\n{_YEL}⚠️  有工作流尚未出结果（排队中或未找到），未确认通过{_RST}")
    else:
        print(f"\n{_RED}❌ 有工作流失败{_RST}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
