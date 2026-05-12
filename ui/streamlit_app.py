"""Streamlit review dashboard — Phase-7 overview / home page.

Multi-page app. See `ui/pages/` for the Jobs browser, Dedup Review, and
Runs & Events pages. This file is the landing tab: counts, dedup mix,
and a strip of the latest discoveries.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from job_agent.config import load_config

st.set_page_config(page_title="Job Sourcing Agent", layout="wide")
st.title("Job Sourcing Agent — Review")

cfg = load_config()
db_path = Path(cfg.storage.sqlite_path)

if not db_path.exists():
    st.warning(f"Database not found at {db_path}. Run `make migrate` first.")
    st.stop()

with sqlite3.connect(db_path) as conn:
    totals = pd.read_sql(
        """
        SELECT
          COUNT(*) AS jobs,
          SUM(CASE WHEN duplicate_status='new' THEN 1 ELSE 0 END) AS new,
          SUM(CASE WHEN duplicate_status='possible_duplicate' THEN 1 ELSE 0 END) AS possible,
          SUM(CASE WHEN duplicate_status='duplicate' THEN 1 ELSE 0 END) AS dup,
          SUM(needs_review) AS needs_review
        FROM jobs
        """,
        conn,
    ).iloc[0]
    runs_n = pd.read_sql("SELECT COUNT(*) AS n FROM search_runs", conn).iloc[0]["n"]
    latest = pd.read_sql(
        """
        SELECT id, company_name, title, location, ats_type, duplicate_status,
               first_seen_at
        FROM jobs
        ORDER BY id DESC
        LIMIT 10
        """,
        conn,
    )

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total jobs", int(totals["jobs"] or 0))
c2.metric("New", int(totals["new"] or 0))
c3.metric("Possible duplicates", int(totals["possible"] or 0))
c4.metric("Confirmed duplicates", int(totals["dup"] or 0))
c5.metric("Needs review", int(totals["needs_review"] or 0))

st.caption(f"Across {int(runs_n)} search runs · DB: `{db_path}`")

st.subheader("Latest discoveries")
if latest.empty:
    st.info("No jobs yet — run `make discover` to populate.")
else:
    st.dataframe(latest, use_container_width=True, hide_index=True)

st.markdown(
    "Use the sidebar to open **Jobs** (browse + filter), "
    "**Dedup Review** (confirm/reject possible duplicates), "
    "or **Runs & Events** (agent activity log)."
)
