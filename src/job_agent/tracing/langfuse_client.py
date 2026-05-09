"""Langfuse tracing wrapper.

Targets Langfuse v4.x (OpenTelemetry-based). The v2 ``client.trace()`` API
is gone — v4 uses ``start_observation`` / ``start_as_current_observation``.
We expose a small protocol so the rest of the codebase can call
``tracer.trace(...).update(...)`` and ``.end()`` without caring whether the
backend is real Langfuse, a no-op, or a future replacement.

Every payload that goes to Langfuse passes through ``redact()`` first when
``cfg.tracing.redact_pii`` is true (the default).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from job_agent.config import AppConfig, load_config
from job_agent.tracing.redact import redact

log = logging.getLogger(__name__)


class _TraceLike(Protocol):
    """Minimal contract over a root trace/span used by the rest of the agent."""

    id: str
    trace_id: str

    def update(self, **kwargs: Any) -> Any: ...
    def end(self, **kwargs: Any) -> Any: ...
    def span(self, *, name: str, **kwargs: Any) -> _TraceLike: ...


class _NoopTrace:
    id = "noop-span"
    trace_id = "noop-trace"

    def update(self, **kwargs: Any) -> _NoopTrace:
        return self

    def end(self, **kwargs: Any) -> _NoopTrace:
        return self

    def span(self, *, name: str, **kwargs: Any) -> _NoopTrace:
        return _NoopTrace()


class _LangfuseTraceAdapter:
    """Wraps a v4 LangfuseSpan so callers can use a stable interface."""

    def __init__(self, span: Any, redact_payloads: bool) -> None:
        self._span = span
        self._redact = redact_payloads
        # v4 spans expose .id (span id) and .trace_id (trace id).
        self.id: str = getattr(span, "id", "")
        self.trace_id: str = getattr(span, "trace_id", self.id)

    def _clean(self, payload: Any) -> Any:
        return redact(payload) if self._redact else payload

    def update(self, **kwargs: Any) -> Any:
        for key in ("metadata", "input", "output"):
            if key in kwargs and kwargs[key] is not None:
                kwargs[key] = self._clean(kwargs[key])
        return self._span.update(**kwargs)

    def end(self, **kwargs: Any) -> Any:
        return self._span.end(**kwargs)

    def span(
        self, *, name: str, metadata: dict[str, Any] | None = None, **kwargs: Any
    ) -> _LangfuseTraceAdapter:
        child = self._span.start_observation(
            name=name,
            as_type="span",
            metadata=self._clean(metadata) if metadata else None,
            **kwargs,
        )
        return _LangfuseTraceAdapter(child, self._redact)


class TracingClient:
    """Thin wrapper over the Langfuse SDK.

    All public methods are safe to call when tracing is disabled — they
    degrade to no-ops. Redaction is applied to every metadata/input/output
    payload before it leaves this process.
    """

    def __init__(self, cfg: AppConfig | None = None) -> None:
        self.cfg = cfg or load_config()
        self._client: Any | None = None
        self._enabled = self._should_enable()

        if self._enabled:
            try:
                from langfuse import Langfuse

                self._client = Langfuse(
                    public_key=self.cfg.langfuse_public_key,
                    secret_key=self.cfg.langfuse_secret_key,
                    host=self.cfg.langfuse_host,
                )
            except Exception as e:
                log.warning("langfuse init failed, tracing disabled: %s", e)
                self._enabled = False
                self._client = None

    def _should_enable(self) -> bool:
        if not self.cfg.tracing.langfuse_enabled:
            return False
        return bool(
            self.cfg.langfuse_public_key and self.cfg.langfuse_secret_key and self.cfg.langfuse_host
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _clean(self, payload: Any) -> Any:
        if self.cfg.tracing.redact_pii:
            return redact(payload)
        return payload

    def trace(
        self,
        *,
        name: str,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> _TraceLike:
        """Start a root span. v4 has no separate "trace" — the root span IS the trace.

        Tags are folded into metadata since v4's ``start_observation`` does
        not accept a ``tags`` argument directly.
        """
        if not self._enabled or self._client is None:
            return _NoopTrace()

        meta = dict(metadata or {})
        if tags:
            meta.setdefault("tags", list(tags))

        span = self._client.start_observation(
            name=name,
            as_type="span",
            metadata=self._clean(meta) if meta else None,
        )
        return _LangfuseTraceAdapter(span, redact_payloads=self.cfg.tracing.redact_pii)

    def flush(self) -> None:
        if self._enabled and self._client is not None:
            try:
                self._client.flush()
            except Exception as e:
                log.warning("langfuse flush failed: %s", e)


def get_tracer(cfg: AppConfig | None = None) -> TracingClient:
    return TracingClient(cfg)
