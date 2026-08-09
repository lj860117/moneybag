"""
钱袋子 — 统一内存缓存（MemoryCache）

设计目标：
  1. 统一替代 50+ 个模块级 _cache = {} 字典
  2. TTL 自动过期 + max_entries LRU 淘汰
  3. 线程安全（简单锁保护写操作）
  4. 统计信息（命中率、条目数）便于监控

用法：
  from services.memory_cache import MemoryCache

  cache = MemoryCache("my_module", ttl=600, max_entries=200)

  # 写
  cache.set("key", value)

  # 读
  val = cache.get("key")
  val = cache.get("key", default=None)

  # 删除
  cache.delete("key")
  cache.clear()

  # 统计
  stats = cache.stats()  # {"hits": 0, "misses": 0, "entries": 0, ...}
"""

import time
import threading
from collections import OrderedDict


class MemoryCache:
    """统一内存缓存，支持 TTL + LRU 淘汰"""

    # 全局注册表，方便监控所有缓存实例
    _instances = []
    _instances_lock = threading.Lock()

    def __init__(self, name: str = "", ttl: float = 3600, max_entries: int = 500):
        """
        Args:
            name: 缓存实例名称（用于日志和监控）
            ttl: 默认 TTL（秒），0 表示永不过期
            max_entries: 最大条目数，超过后 LRU 淘汰最久未访问的条目；0 表示无限制
        """
        self.name = name or "unnamed"
        self.default_ttl = ttl
        self.max_entries = max(0, max_entries)  # 0 = 无限制

        self._store = OrderedDict()  # key -> {"value": v, "ts": float, "ttl": float}
        self._lock = threading.Lock()

        # 统计
        self._hits = 0
        self._misses = 0

        # 注册到全局列表
        with MemoryCache._instances_lock:
            MemoryCache._instances.append(self)

    def get(self, key, default=None):
        """读取缓存，过期返回 default"""
        with self._lock:
            if key in self._store:
                entry = self._store[key]
                ttl = entry.get("ttl", self.default_ttl)
                if ttl > 0 and time.time() - entry["ts"] > ttl:
                    # 已过期
                    del self._store[key]
                    self._misses += 1
                    return default
                # LRU: 移到末尾（最近访问）
                self._store.move_to_end(key)
                self._hits += 1
                return entry["value"]
            self._misses += 1
            return default

    def set(self, key, value, ttl: float = None):
        """写入缓存

        Args:
            key: 缓存键
            value: 缓存值
            ttl: 本次写入的 TTL（秒），None 则用实例默认 TTL
        """
        with self._lock:
            # 如果已存在，先删除再插入（保证顺序）
            if key in self._store:
                del self._store[key]

            self._store[key] = {
                "value": value,
                "ts": time.time(),
                "ttl": ttl if ttl is not None else self.default_ttl,
            }

            # LRU 淘汰
            if self.max_entries > 0:
                while len(self._store) > self.max_entries:
                    self._store.popitem(last=False)  # 弹出最久未访问的

    def delete(self, key):
        """删除指定键"""
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        """清空所有缓存"""
        with self._lock:
            self._store.clear()

    def invalidate_prefix(self, prefix: str):
        """批量删除指定前缀的键"""
        with self._lock:
            keys_to_delete = [k for k in self._store if str(k).startswith(prefix)]
            for k in keys_to_delete:
                del self._store[k]

    def stats(self) -> dict:
        """返回缓存统计信息"""
        with self._lock:
            total = self._hits + self._misses
            return {
                "name": self.name,
                "entries": len(self._store),
                "max_entries": self.max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 4) if total > 0 else 0,
                "default_ttl": self.default_ttl,
            }

    @classmethod
    def all_stats(cls) -> list:
        """返回所有缓存实例的统计"""
        with cls._instances_lock:
            return [c.stats() for c in cls._instances]

    @classmethod
    def get_instance(cls, name: str):
        """按名称查找缓存实例"""
        with cls._instances_lock:
            for c in cls._instances:
                if c.name == name:
                    return c
        return None
