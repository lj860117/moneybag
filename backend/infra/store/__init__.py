"""
Store infrastructure -- persistence implementations.
Invariant #5: All file IO through infra/store.
"""
from infra.store.file_store import FileStore

__all__ = ["FileStore"]
