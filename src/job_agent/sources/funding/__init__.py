"""Funding company discovery (design_plan.md Phase 5).

Finds companies that recently raised so the agent can pull their fresh,
often-unposted roles. Three aggregators in v1: TechCrunch venture listings,
Hacker News Algolia search, and Google funding queries via the existing
nodriver driver. All three feed a single regex-first / LLM-fallback
extractor whose output is `FundingEventCandidate`. The orchestrator
deduplicates by (normalized_company_name, source_url) before writing.
"""
