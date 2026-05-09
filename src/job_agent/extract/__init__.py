"""Phase 3 job extraction pipeline.

Importable surface lives in submodules to keep optional deps (Playwright,
httpx) out of the package's import-time path. Use:

* ``from job_agent.extract.schema import ExtractedJob``
* ``from job_agent.extract.pipeline import extract_job``
"""
