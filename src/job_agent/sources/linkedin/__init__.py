"""Phase 6: public LinkedIn post search.

Never logged in to LinkedIn — discovery is via Google `site:linkedin.com/posts/*`
queries. Individual post pages are fetched with a strict per-domain rate
limit (Semaphore(1), 30-60s jitter) to respect LinkedIn's policies on
automated access.

Pipeline: Google search -> per-post throttled fetch -> hiring-post
classifier -> company + role extraction -> save -> upsert company so the
Phase-5 resolver chain can find a careers page.
"""
