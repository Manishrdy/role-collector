"""Deterministic parsers for ATS detail pages and structured data.

Each parser exposes a ``parse(html, url) -> ExtractedJob | None`` callable.
Returning ``None`` means "this parser doesn't match" and the orchestrator
should fall through to the next strategy.
"""
