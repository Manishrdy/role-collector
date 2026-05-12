"""Dedup Review — pair each `possible_duplicate` with its match and let the user confirm or reject."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from job_agent.config import load_config
from job_agent.db import repo

st.set_page_config(page_title="Dedup Review", layout="wide")
st.title("Dedup Review")

cfg = load_config()
db_path = Path(cfg.storage.sqlite_path)
if not db_path.exists():
    st.warning(f"Database not found at {db_path}. Run `make migrate` first.")
    st.stop()


def fetch_possibles(db: str) -> pd.DataFrame:
    with sqlite3.connect(db) as conn:
        return pd.read_sql(
            """
            SELECT id, company_name, title, location, ats_type, apply_url,
                   canonical_url, description, duplicate_of_job_id,
                   duplicate_score, first_seen_at
            FROM jobs
            WHERE duplicate_status = 'possible_duplicate'
            ORDER BY duplicate_score DESC, id DESC
            """,
            conn,
        )


def fetch_job(db: str, job_id: int) -> pd.Series | None:
    with sqlite3.connect(db) as conn:
        df = pd.read_sql(
            "SELECT id, company_name, title, location, ats_type, apply_url, "
            "canonical_url, description, first_seen_at "
            "FROM jobs WHERE id = ?",
            conn,
            params=(job_id,),
        )
    return df.iloc[0] if not df.empty else None


def fetch_score_breakdown(db: str, job_id: int, candidate_id: int) -> pd.Series | None:
    with sqlite3.connect(db) as conn:
        df = pd.read_sql(
            """
            SELECT company_score, title_score, description_score,
                   location_score, skills_score, duplicate_score, decision,
                   created_at
            FROM duplicate_candidates
            WHERE job_id = ? AND candidate_job_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            conn,
            params=(job_id, candidate_id),
        )
    return df.iloc[0] if not df.empty else None


possibles = fetch_possibles(str(db_path))
st.caption(f"**{len(possibles)}** jobs awaiting dedup review")

if possibles.empty:
    st.success("Nothing to review — no possible duplicates pending.")
    st.stop()

for _, row in possibles.iterrows():
    job_id = int(row["id"])
    match_id_raw = row["duplicate_of_job_id"]
    if pd.isna(match_id_raw):
        st.warning(f"Job #{job_id} marked possible_duplicate but has no match id — skipping.")
        continue
    match_id = int(match_id_raw)
    match = fetch_job(str(db_path), match_id)
    breakdown = fetch_score_breakdown(str(db_path), job_id, match_id)

    score = row["duplicate_score"]
    score_str = f"{score:.3f}" if pd.notna(score) else "—"
    with st.container(border=True):
        st.markdown(f"### Pair: job **#{job_id}** ⇄ job **#{match_id}** · score `{score_str}`")

        left, right = st.columns(2)
        for col, j, label in ((left, row, "new"), (right, match, "existing match")):
            with col:
                if j is None:
                    st.error(f"Match job #{match_id} not found.")
                    continue
                st.markdown(f"**{label.upper()} — #{int(j['id'])}**")
                st.markdown(f"**{j['title']}** — {j['company_name']}")
                st.caption(
                    f"{j['location'] or '—'} · {j['ats_type'] or '—'} · "
                    f"first seen {j['first_seen_at']}"
                )
                if j.get("apply_url"):
                    st.markdown(f"[Apply →]({j['apply_url']})")
                desc = (j["description"] or "")[:1200]
                st.text_area(
                    label=f"desc-{label}-{int(j['id'])}",
                    value=desc + ("…" if len(j["description"] or "") > 1200 else ""),
                    height=200,
                    label_visibility="collapsed",
                )

        if breakdown is not None:
            st.markdown("**Score breakdown**")
            bd_cols = st.columns(5)

            def _fmt(v: object) -> str:
                return f"{float(v):.2f}" if v is not None and pd.notna(v) else "—"

            bd_cols[0].metric("Company", _fmt(breakdown["company_score"]))
            bd_cols[1].metric("Title", _fmt(breakdown["title_score"]))
            bd_cols[2].metric("Description", _fmt(breakdown["description_score"]))
            bd_cols[3].metric("Location", _fmt(breakdown["location_score"]))
            bd_cols[4].metric("Skills", _fmt(breakdown["skills_score"]))

        action_cols = st.columns([1, 1, 4])
        if action_cols[0].button("Confirm duplicate", key=f"confirm-{job_id}", type="primary"):
            repo.set_duplicate_decision(job_id=job_id, decision="duplicate")
            st.toast(f"Job #{job_id} confirmed as duplicate of #{match_id}")
            st.rerun()
        if action_cols[1].button("Reject (mark new)", key=f"reject-{job_id}"):
            repo.set_duplicate_decision(job_id=job_id, decision="new")
            st.toast(f"Job #{job_id} re-marked as new")
            st.rerun()
