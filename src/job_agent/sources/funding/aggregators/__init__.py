"""Aggregator implementations for funding-event discovery.

Each aggregator yields `FundingEventCandidate` records produced by the
extractor in `sources.funding.extractor`. Aggregators are isolated so a
single broken source (e.g., TechCrunch DOM churn) does not abort the run.
"""
