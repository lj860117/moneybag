"""
FileStore -- JSON file persistence with atomic writes
======================================================
Thin wrapper around the patterns in services/persistence.py:
  - SHA256[:16] key hashing for safe filenames (same algorithm as persistence.py:28)
  - Atomic writes (tempfile + fsync + os.replace)
  - .bak backup files for crash recovery
  - Corruption recovery (fallback to .bak on read)

COEXISTENCE: This does NOT replace persistence.py during the strangler-fig
migration. Both exist side by side. New code uses FileStore; old code keeps
calling persistence.py. They share the same DATA_DIR and file format.

Design doc: docs/design/12-framework-refactor.md
Satisfies: domain.protocols.StoreProtocol (structural subtyping)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional


class FileStore:
    """File-based persistence satisfying domain.protocols.StoreProtocol.

    Directory layout::

        {base_dir}/{collection}/{hashed_key}.json
        {base_dir}/{collection}/{hashed_key}.json.bak
    """

    __slots__ = ("_base_dir",)

    def __init__(self, base_dir: Optional[str] = None) -> None:
        if base_dir is None:
            base_dir = os.environ.get("DATA_DIR", "./data")
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    # ---- StoreProtocol methods ----

    def read(self, collection: str, key: str) -> Optional[Dict]:
        """Read JSON document with corruption recovery."""
        path = self._path(collection, key)
        backup = path.with_suffix(".json.bak")

        # 1. Try primary file
        data = self._safe_read(path)
        if data is not None:
            return data

        # 2. Primary missing/corrupt -> try backup
        data = self._safe_read(backup)
        if data is not None:
            # Restore primary from backup
            self._atomic_write(path, data)
            return data

        return None

    def write(self, collection: str, key: str, data: Dict) -> None:
        """Atomic write with backup rotation."""
        path = self._path(collection, key)

        # Rotate: current -> .bak before writing new version
        if path.exists():
            backup = path.with_suffix(".json.bak")
            try:
                shutil.copy2(str(path), str(backup))
            except OSError:
                pass  # Best-effort backup

        self._atomic_write(path, data)

    def delete(self, collection: str, key: str) -> bool:
        """Delete document and its backup."""
        path = self._path(collection, key)
        existed = path.exists()
        for p in (path, path.with_suffix(".json.bak")):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        return existed

    def exists(self, collection: str, key: str) -> bool:
        """Check if document exists."""
        return self._path(collection, key).exists()

    def list_keys(self, collection: str) -> List[str]:
        """List all keys in a collection (hashed filenames sans extension)."""
        coll_dir = self._base_dir / collection
        if not coll_dir.is_dir():
            return []
        return sorted(
            p.stem for p in coll_dir.glob("*.json")
            if not p.name.endswith(".bak")
        )

    # ---- Internal helpers ----

    def _path(self, collection: str, key: str) -> Path:
        """Build file path: base/collection/sha256(key)[:16].json

        SHA256[:16] matches services/persistence.py line 28 exactly,
        so FileStore and persistence.py resolve the same user_id to the
        same file. They coexist on the same data directory.
        """
        safe_key = hashlib.sha256(key.encode()).hexdigest()[:16]
        coll_dir = self._base_dir / collection
        coll_dir.mkdir(parents=True, exist_ok=True)
        return coll_dir / "{}.json".format(safe_key)

    @staticmethod
    def _safe_read(path: Path) -> Optional[Dict]:
        """Read JSON, return None on any error."""
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return None

    @staticmethod
    def _atomic_write(filepath: Path, data: Dict) -> None:
        """Atomic JSON write: tempfile + fsync + os.replace.

        Same algorithm as services/persistence.py:atomic_write_json() (line 68-91).
        """
        filepath.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(filepath.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(filepath))
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
