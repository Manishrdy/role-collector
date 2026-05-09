from __future__ import annotations

import json

import httpx

from job_agent.config import load_config
from job_agent.extract.llm import OllamaExtractor, _coerce_to_extracted


def test_coerce_to_extracted_handles_minimal_payload() -> None:
    raw = json.dumps(
        {
            "title": "Software Engineer",
            "company_name": "Acme",
            "location": "Remote",
            "skills": ["Python", "Postgres", 42],
            "remote_type": "remote",
        }
    )
    job = _coerce_to_extracted(raw)
    assert job is not None
    assert job.skills == ["Python", "Postgres"]
    assert job.extraction_source == "llm"


def test_coerce_to_extracted_rejects_missing_required_fields() -> None:
    assert _coerce_to_extracted(json.dumps({"title": "Foo"})) is None
    assert _coerce_to_extracted("not json") is None


def test_extractor_calls_ollama_with_json_format() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "response": json.dumps(
                    {
                        "title": "Backend Engineer",
                        "company_name": "TestCo",
                        "location": "NYC",
                        "skills": ["Go"],
                    }
                )
            },
        )

    cfg = load_config()
    transport = httpx.MockTransport(handler)
    with OllamaExtractor(cfg, transport=transport) as ext:
        job = ext.extract(html="<html><body>Hi</body></html>", url="https://x.test")

    assert job is not None
    assert job.title == "Backend Engineer"
    assert len(captured) == 1
    body = json.loads(captured[0].content)
    assert body["format"] == "json"
    assert body["stream"] is False
    assert body["model"] == cfg.llm.model


def test_extractor_returns_none_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, text="boom")

    cfg = load_config()
    with OllamaExtractor(cfg, transport=httpx.MockTransport(handler)) as ext:
        assert ext.extract(html="<html/>", url="https://x.test") is None
