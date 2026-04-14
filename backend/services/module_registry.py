"""
钱袋子 — ModuleRegistry（模块注册表）
v3.0 底座 #2

职责：
  启动时自动扫描 services/ 目录，找到有 MODULE_META 的模块注册。
  AI（steward/DeepSeek）通过 discover() 查询可用模块，自己选要调哪些。

核心 API：
  _auto_discover()           → 启动时自动扫描注册
  discover(scope, cost, tag) → AI 筛选模块
  to_llm_catalog()           → 生成给 DeepSeek 看的模块目录
  get_enrich(name)           → 获取模块的 enrich 函数
  list_all()                 → 列出全部已注册模块

MODULE_META 规范（每个 service 文件头部声明）：
  MODULE_META = {
      "name": "snake_case_name",
      "scope": "public|private",       # public=市场数据 / private=需user_id
      "input": ["从ctx读的字段"],
      "output": "写入ctx的字段",
      "cost": "cpu|llm_light|llm_heavy",
      "tags": ["analysis", "data", ...],
      "description": "一句话描述",
      "layer": "data|analysis|risk|output",
      "priority": 50,                  # 0-100, 越高越先执行
  }

设计文档: §二
"""
import importlib
import pkgutil
import time
from pathlib import Path
from typing import Optional, Callable


# services/ 目录路径
_SERVICES_DIR = Path(__file__).parent


class ModuleRegistry:
    """模块注册表 — AI 的模块目录"""

    _instance = None  # 全局单例

    @classmethod
    def instance(cls) -> "ModuleRegistry":
        """获取全局单例（懒初始化）"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._modules: dict[str, dict] = {}  # name → {meta, enrich_fn, module_ref}
        self._discovered = False
        self._discover_time_ms = 0

    def _auto_discover(self) -> int:
        """启动时扫描 services/，找到有 MODULE_META 的模块自动注册
        
        Returns: 注册成功的模块数量
        """
        t0 = time.time()
        count = 0

        for module_info in pkgutil.iter_modules([str(_SERVICES_DIR)]):
            if module_info.name.startswith("_"):
                continue
            # 跳过自身和其他底座文件
            if module_info.name in ("decision_context", "module_registry", "pipeline_runner"):
                continue

            try:
                mod = importlib.import_module(f"services.{module_info.name}")
                meta = getattr(mod, "MODULE_META", None)
                if meta and isinstance(meta, dict) and "name" in meta:
                    enrich_fn = getattr(mod, "enrich", None)
                    self._modules[meta["name"]] = {
                        "meta": meta,
                        "enrich": enrich_fn,
                        "module": mod,
                        "file": module_info.name,
                    }
                    count += 1
            except Exception as e:
                # 模块导入失败不阻塞其他模块
                print(f"[REGISTRY] ⚠️ 跳过 {module_info.name}: {e}")

        self._discovered = True
        self._discover_time_ms = int((time.time() - t0) * 1000)
        print(f"[REGISTRY] 自动发现 {count} 个模块 ({self._discover_time_ms}ms)")
        return count

    def ensure_discovered(self):
        """确保已扫描（懒加载）"""
        if not self._discovered:
            self._auto_discover()

    def discover(self, scope: Optional[str] = None,
                 cost: Optional[str] = None,
                 tag: Optional[str] = None,
                 layer: Optional[str] = None) -> list[dict]:
        """AI 查询可用模块 — 支持多条件筛选
        
        Args:
            scope: "public" / "private" / None(全部)
            cost:  "cpu" / "llm_light" / "llm_heavy" / None(全部)
            tag:   标签名 / None(全部)
            layer: "data" / "analysis" / "risk" / "output" / None(全部)
        
        Returns: [{name, scope, cost, description, has_enrich, ...}, ...]
        """
        self.ensure_discovered()
        results = []

        for name, entry in self._modules.items():
            meta = entry["meta"]

            # 筛选
            if scope and meta.get("scope") != scope:
                continue
            if cost and meta.get("cost") != cost:
                continue
            if tag and tag not in meta.get("tags", []):
                continue
            if layer and meta.get("layer") != layer:
                continue

            results.append({
                **meta,
                "has_enrich": entry["enrich"] is not None,
                "file": entry["file"],
            })

        # 按 priority 降序
        results.sort(key=lambda x: x.get("priority", 50), reverse=True)
        return results

    def get_enrich(self, name: str) -> Optional[Callable]:
        """获取模块的 enrich 函数（Pipeline Layer2 调用）"""
        self.ensure_discovered()
        entry = self._modules.get(name)
        if entry:
            return entry["enrich"]
        return None

    def get_meta(self, name: str) -> Optional[dict]:
        """获取模块的 META 信息"""
        self.ensure_discovered()
        entry = self._modules.get(name)
        if entry:
            return entry["meta"]
        return None

    def list_all(self) -> list[str]:
        """列出全部已注册模块名"""
        self.ensure_discovered()
        return list(self._modules.keys())

    def count(self) -> int:
        """已注册模块数量"""
        self.ensure_discovered()
        return len(self._modules)

    def to_llm_catalog(self) -> str:
        """生成给 DeepSeek 看的模块目录（AI 据此选择调用哪些模块）
        
        Returns: 格式化的文本目录
        """
        self.ensure_discovered()
        lines = [f"## 可用分析模块 ({len(self._modules)} 个)\n"]

        # 按 layer 分组
        by_layer: dict[str, list] = {}
        for name, entry in self._modules.items():
            meta = entry["meta"]
            layer = meta.get("layer", "other")
            by_layer.setdefault(layer, []).append(meta)

        layer_labels = {
            "data": "📊 数据层",
            "analysis": "🧠 分析层",
            "risk": "🛡️ 风控层",
            "output": "📤 输出层",
            "other": "🔧 其他",
        }

        for layer in ["data", "analysis", "risk", "output", "other"]:
            modules = by_layer.get(layer, [])
            if not modules:
                continue
            lines.append(f"\n### {layer_labels.get(layer, layer)} ({len(modules)}个)")
            modules.sort(key=lambda x: x.get("priority", 50), reverse=True)
            for m in modules:
                scope_tag = "🔒" if m.get("scope") == "private" else "🌐"
                cost_tag = {"cpu": "⚡", "llm_light": "💬", "llm_heavy": "🧠"}.get(m.get("cost", "cpu"), "")
                lines.append(f"  {scope_tag}{cost_tag} **{m['name']}**: {m.get('description', '')}")
                lines.append(f"     输入: {m.get('input', [])} → 输出: {m.get('output', '')}")

        return "\n".join(lines)

    def stats(self) -> dict:
        """注册统计"""
        self.ensure_discovered()
        scopes = {}
        costs = {}
        layers = {}
        enrich_count = 0

        for entry in self._modules.values():
            meta = entry["meta"]
            s = meta.get("scope", "?")
            c = meta.get("cost", "?")
            l = meta.get("layer", "?")
            scopes[s] = scopes.get(s, 0) + 1
            costs[c] = costs.get(c, 0) + 1
            layers[l] = layers.get(l, 0) + 1
            if entry["enrich"]:
                enrich_count += 1

        return {
            "total": len(self._modules),
            "with_enrich": enrich_count,
            "by_scope": scopes,
            "by_cost": costs,
            "by_layer": layers,
            "discover_time_ms": self._discover_time_ms,
        }


# ━━ 全局单例 ━━
registry = ModuleRegistry()
