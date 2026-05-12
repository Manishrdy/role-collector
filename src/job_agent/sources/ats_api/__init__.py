"""Per-provider ATS public-API clients.

Three providers publish documented endpoints that return every public
posting for a company as JSON:

- Lever: ``https://api.lever.co/v0/postings/<slug>?mode=json``
- Greenhouse: ``https://boards-api.greenhouse.io/v1/boards/<slug>/jobs``
- Ashby: ``https://api.ashbyhq.com/posting-api/job-board/<slug>``

Each ``enumerate_<provider>(slug)`` returns the list of canonical
posting URLs (Phase-3 fetch/extract handles per-job pages). API failure
returns ``None`` so callers can decide whether to fall back to HTML.

Used by:
- ``sources.funding.watchlist`` (per-company re-poll of resolved ATS URLs)
- ``sources.ats_api.discovery`` (seed-slug enumeration BEFORE Google search)

Workday / SmartRecruiters intentionally omitted: Workday has no public
unified API (each tenant is custom), and SmartRecruiters' enumeration
endpoint requires auth in most cases.
"""

from job_agent.sources.ats_api.clients import (
    enumerate_ashby,
    enumerate_greenhouse,
    enumerate_lever,
)

__all__ = ["enumerate_ashby", "enumerate_greenhouse", "enumerate_lever"]
