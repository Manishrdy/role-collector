"""Tool permission decorators.

Tools that touch the network or browser run through @guarded. Phase-1 only
needs URL-allowlist enforcement; richer guardrails (rate limiting, cooldowns
after captcha, per-domain page caps) attach in Phase 2.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from job_agent.browser.safety import UrlSafetyError, check_url

log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def guarded_url_arg(arg_name: str = "url") -> Callable[[F], F]:
    """Reject calls whose `arg_name` does not pass URL safety checks.

    Used by every browser-touching tool. Raises UrlSafetyError on rejection.
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            url = kwargs.get(arg_name)
            if url is None:
                raise UrlSafetyError(f"missing required '{arg_name}' argument")
            decision = check_url(url)
            if not decision.allowed:
                log.warning("blocked tool call: %s (%s)", url, decision.reason)
                raise UrlSafetyError(f"{decision.reason}: {url}")
            kwargs[arg_name] = decision.canonical_url
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
