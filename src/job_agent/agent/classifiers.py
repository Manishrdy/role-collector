"""Bounded Ollama classifiers for job intelligence and cycle reflection."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from job_agent.agent.schemas import CycleReflection, JobIntelligence
from job_agent.config import AppConfig
from job_agent.extract.schema import ExtractedJob

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a strict JSON classifier for autonomous job sourcing. "
    "Only return valid JSON matching the requested schema. "
    "Do not include markdown."
)

_JOB_TEMPLATE = """Classify this job into normalized fields.

Configured target roles:
{roles}

Job:
{job}

Return ONLY JSON with this exact schema:
{{
  "role_family": string,
  "role_match_status": "exact_match" | "adjacent" | "irrelevant" | "unknown",
  "level": "intern" | "entry" | "mid" | "senior" | "staff" | "principal" | "manager" | "unknown",
  "level_confidence": number,
  "location": {{
    "raw": string|null,
    "city": string|null,
    "region": string|null,
    "country": string|null,
    "display": string|null,
    "remote_type": string|null,
    "confidence": number
  }}
}}
"""

_REFLECTION_TEMPLATE = """Reflect on this completed worker cycle summary and suggest next focus.

Cycle summary:
{summary}

Return ONLY JSON with this exact schema:
{{
  "summary": string,
  "next_cycle_focus": [string],
  "risk_flags": [string],
  "confidence": number
}}
"""


class OllamaClassifierClient:
    """Shared Ollama client for agentic classification tasks."""

    def __init__(self, cfg: AppConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        self._cfg = cfg
        self._client = httpx.Client(
            base_url=cfg.ollama_base_url,
            timeout=cfg.llm.request_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def classify_job(self, *, job: ExtractedJob, configured_roles: list[str]) -> JobIntelligence | None:
        prompt = _JOB_TEMPLATE.format(
            roles=json.dumps(configured_roles),
            job=json.dumps(
                {
                    "title": job.title,
                    "company_name": job.company_name,
                    "location": job.location,
                    "remote_type": job.remote_type,
                    "description": (job.description or "")[:4000],
                    "description_summary": job.description_summary,
                    "seniority": job.seniority,
                }
            ),
        )
        raw = self._generate_json(prompt)
        if raw is None:
            return None
        try:
            model = JobIntelligence.model_validate_json(raw)
        except Exception as e:
            log.warning("job intelligence classifier JSON failed validation: %s", e)
            return None
        if model.location.raw is None:
            model.location.raw = job.location
        if model.location.remote_type is None:
            model.location.remote_type = job.remote_type
        return model

    def reflect_cycle(self, *, summary: dict[str, Any]) -> CycleReflection | None:
        prompt = _REFLECTION_TEMPLATE.format(summary=json.dumps(summary, sort_keys=True))
        raw = self._generate_json(prompt)
        if raw is None:
            return None
        try:
            return CycleReflection.model_validate_json(raw)
        except Exception as e:
            log.warning("cycle reflection classifier JSON failed validation: %s", e)
            return None

    def _generate_json(self, prompt: str) -> str | None:
        payload: dict[str, Any] = {
            "model": self._cfg.llm.model,
            "prompt": prompt,
            "system": _SYSTEM_PROMPT,
            "format": "json",
            "stream": False,
            "options": {"temperature": self._cfg.llm.temperature},
        }
        try:
            resp = self._client.post("/api/generate", json=payload)
            resp.raise_for_status()
            body = resp.json()
        except Exception as e:
            log.warning("ollama classifier call failed: %s", e)
            return None
        raw = body.get("response")
        if not isinstance(raw, str):
            return None
        return raw
