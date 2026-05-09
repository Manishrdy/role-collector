"""Streamlit review dashboard — Phase-7 placeholder.

Lists rows from the SQLite db. Filtering, dedupe review, and labeling land
in Phase 7 once jobs actually start landing in the database.
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
    runs_df = pd.read_sql(
        "SELECT id, source_type, status, started_at, finished_at, query, time_window "
        "FROM search_runs ORDER BY id DESC LIMIT 50",
        conn,
    )
    jobs_count = pd.read_sql("SELECT COUNT(*) AS n FROM jobs", conn).iloc[0]["n"]
    events_df = pd.read_sql(
        "SELECT id, search_run_id, event_type, event_message, created_at "
        "FROM agent_events ORDER BY id DESC LIMIT 100",
        conn,
    )

col1, col2, col3 = st.columns(3)
col1.metric("Total jobs", int(jobs_count))
col2.metric("Recent runs", len(runs_df))
col3.metric("Recent events", len(events_df))

st.subheader("Recent runs")
st.dataframe(runs_df, use_container_width=True)

st.subheader("Recent agent events")
st.dataframe(events_df, use_container_width=True)

st.caption("Phase-7 dashboard placeholder. Real job listing, filtering, and dedupe review land later.")
