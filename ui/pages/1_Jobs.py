"""Jobs browser — filter, sort, drill into a job."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from job_agent.config import load_config

st.set_page_config(page_title="Jobs", layout="wide")
st.title("Jobs")

cfg = load_config()
db_path = Path(cfg.storage.sqlite_path)
if not db_path.exists():
    st.warning(f"Database not found at {db_path}. Run `make migrate` first.")
    st.stop()


@st.cache_data(ttl=30)
def load_jobs(db: str) -> pd.DataFrame:
    with sqlite3.connect(db) as conn:
        return pd.read_sql(
            """
            SELECT id, company_name, title, location, remote_type, ats_type,
                   apply_url, canonical_url, posted_date, first_seen_at,
                   last_seen_at, duplicate_status, duplicate_of_job_id,
                   duplicate_score, status, needs_review, seniority,
                   employment_type, salary_text, skills_json, description,
                   extraction_confidence, source_type, source_query
            FROM jobs
            ORDER BY first_seen_at DESC, id DESC
            """,
            conn,
        )


jobs = load_jobs(str(db_path))
if jobs.empty:
    st.info("No jobs yet — run `make discover` to populate.")
    st.stop()


st.sidebar.header("Filters")
q = st.sidebar.text_input("Search company / title", "")
dup_choices = st.sidebar.multiselect(
    "Dedup status",
    options=sorted(jobs["duplicate_status"].dropna().unique().tolist()),
    default=["new", "possible_duplicate"],
)
ats_choices = st.sidebar.multiselect(
    "ATS", options=sorted(jobs["ats_type"].dropna().unique().tolist())
)
remote_choices = st.sidebar.multiselect(
    "Remote type", options=sorted(jobs["remote_type"].dropna().unique().tolist())
)
needs_review_only = st.sidebar.checkbox("Needs review only", value=False)

filtered = jobs
if q:
    needle = q.lower()
    filtered = filtered[
        filtered["company_name"].str.lower().str.contains(needle, na=False)
        | filtered["title"].str.lower().str.contains(needle, na=False)
    ]
if dup_choices:
    filtered = filtered[filtered["duplicate_status"].isin(dup_choices)]
if ats_choices:
    filtered = filtered[filtered["ats_type"].isin(ats_choices)]
if remote_choices:
    filtered = filtered[filtered["remote_type"].isin(remote_choices)]
if needs_review_only:
    filtered = filtered[filtered["needs_review"] == 1]

st.caption(f"Showing **{len(filtered)}** of {len(jobs)} jobs")

display_cols = [
    "id",
    "company_name",
    "title",
    "location",
    "remote_type",
    "ats_type",
    "duplicate_status",
    "needs_review",
    "first_seen_at",
]
event = st.dataframe(
    filtered[display_cols],
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
)

selected_rows = event.selection.rows if hasattr(event, "selection") else []
if not selected_rows:
    st.info("Select a row above to see the full job detail.")
    st.stop()

row_idx = selected_rows[0]
job = filtered.iloc[row_idx]

st.divider()
st.subheader(f"{job['title']} — {job['company_name']}")

meta_cols = st.columns(4)
meta_cols[0].markdown(f"**Location**\n\n{job['location'] or '—'}")
meta_cols[1].markdown(f"**Remote**\n\n{job['remote_type'] or '—'}")
meta_cols[2].markdown(f"**ATS**\n\n{job['ats_type'] or '—'}")
meta_cols[3].markdown(f"**Seniority**\n\n{job['seniority'] or '—'}")

meta_cols2 = st.columns(4)
meta_cols2[0].markdown(f"**Employment**\n\n{job['employment_type'] or '—'}")
meta_cols2[1].markdown(f"**Salary**\n\n{job['salary_text'] or '—'}")
meta_cols2[2].markdown(f"**Posted**\n\n{job['posted_date'] or '—'}")
meta_cols2[3].markdown(
    f"**Confidence**\n\n{job['extraction_confidence']:.2f}"
    if pd.notna(job["extraction_confidence"])
    else "**Confidence**\n\n—"
)

if job["apply_url"]:
    st.markdown(f"[Apply →]({job['apply_url']}) · canonical: `{job['canonical_url'] or '—'}`")

skills_raw = job["skills_json"]
if skills_raw:
    try:
        skills = json.loads(skills_raw)
        if skills:
            st.markdown("**Skills:** " + ", ".join(skills))
    except (TypeError, ValueError):
        pass

if job["duplicate_status"] != "new" and pd.notna(job["duplicate_of_job_id"]):
    score = job["duplicate_score"]
    score_str = f"{score:.3f}" if pd.notna(score) else "—"
    st.warning(
        f"Marked **{job['duplicate_status']}** of job "
        f"#{int(job['duplicate_of_job_id'])} (score {score_str})"
    )

st.markdown("### Description")
st.text_area(
    label="description",
    value=job["description"] or "",
    height=300,
    label_visibility="collapsed",
)

with sqlite3.connect(db_path) as conn:
    sources = pd.read_sql(
        "SELECT source_type, source_url, canonical_source_url, source_query, found_at "
        "FROM job_sources WHERE job_id = ? ORDER BY id DESC",
        conn,
        params=(int(job["id"]),),
    )
st.markdown("### Sources")
if sources.empty:
    st.caption("No source records.")
else:
    st.dataframe(sources, use_container_width=True, hide_index=True)
