from __future__ import annotations

import json

import httpx

from job_agent.agent.classifiers import OllamaClassifierClient
from job_agent.agent.intelligence import classify_job_with_source
from job_agent.config import load_config
from job_agent.extract.schema import ExtractedJob


def _sample_job() -> ExtractedJob:
    return ExtractedJob(
        title="Senior Backend Engineer",
        company_name="Acme",
        location="Los Angeles, California, United States",
        description="Design and build backend systems.",
        extraction_source="dom",
        extraction_confidence=0.7,
    )


def test_classifier_parses_job_intelligence_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "response": json.dumps(
                    {
                        "role_family": "backend",
                        "role_match_status": "adjacent",
                        "level": "senior",
                        "level_confidence": 0.9,
                        "location": {
                            "raw": "Los Angeles, California, United States",
                            "city": "Los Angeles",
                            "region": "California",
                            "country": "United States",
                            "display": "Los Angeles, United States",
                            "remote_type": None,
                            "confidence": 0.9,
                        },
                    }
                )
            },
        )

    cfg = load_config()
    client = OllamaClassifierClient(cfg, transport=httpx.MockTransport(handler))
    try:
        intel = client.classify_job(job=_sample_job(), configured_roles=cfg.search.roles)
    finally:
        client.close()
    assert intel is not None
    assert intel.role_family == "backend"
    assert intel.level == "senior"


def test_classifier_reflection_parses_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "response": json.dumps(
                    {
                        "summary": "Google blocked; watchlist produced results.",
                        "next_cycle_focus": ["prioritize watchlist", "reduce google query fanout"],
                        "risk_flags": ["google rate limit"],
                        "confidence": 0.8,
                    }
                )
            },
        )

    cfg = load_config()
    client = OllamaClassifierClient(cfg, transport=httpx.MockTransport(handler))
    try:
        reflection = client.reflect_cycle(summary={"status": "succeeded"})
    finally:
        client.close()
    assert reflection is not None
    assert reflection.next_cycle_focus


def test_classify_job_with_source_falls_back_on_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"response": "{not json"})

    cfg = load_config()
    client = OllamaClassifierClient(cfg, transport=httpx.MockTransport(handler))
    try:
        intel, source = classify_job_with_source(_sample_job(), cfg, classifier=client)
    finally:
        client.close()
    assert source == "deterministic"
    assert intel.level in {"senior", "unknown"}
