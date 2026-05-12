"""Runs & Events — search_runs history and agent_events log."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from job_agent.config import load_config

st.set_page_config(page_title="Runs & Events", layout="wide")
st.title("Runs & Events")

cfg = load_config()
db_path = Path(cfg.storage.sqlite_path)
if not db_path.exists():
    st.warning(f"Database not found at {db_path}. Run `make migrate` first.")
    st.stop()

with sqlite3.connect(db_path) as conn:
    runs_df = pd.read_sql(
        """
        SELECT id, source_type, status, started_at, finished_at, query,
               time_window, error_message
        FROM search_runs
        ORDER BY id DESC LIMIT 100
        """,
        conn,
    )
    fetches_df = pd.read_sql(
        """
        SELECT id, search_run_id, status, http_status, domain,
               detected_page_type, blocked_reason, fetched_at, url
        FROM page_fetches
        ORDER BY id DESC LIMIT 200
        """,
        conn,
    )
    events_df = pd.read_sql(
        """
        SELECT id, search_run_id, event_type, event_message, created_at
        FROM agent_events
        ORDER BY id DESC LIMIT 200
        """,
        conn,
    )

st.subheader("Search runs")
st.dataframe(runs_df, use_container_width=True, hide_index=True)

st.subheader("Page fetches")
st.dataframe(fetches_df, use_container_width=True, hide_index=True)

st.subheader("Agent events")
st.dataframe(events_df, use_container_width=True, hide_index=True)
