"""Phase 4 deduplication: exact + semantic.

Public surface (import from submodules):

* :mod:`hashing`      — ``description_hash(text)`` for cross-URL exact dedup
* :mod:`embeddings`   — sentence-transformers-backed embedder, lazy-loaded
* :mod:`scoring`      — weighted similarity per design §18.3
"""
