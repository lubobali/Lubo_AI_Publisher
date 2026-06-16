"""Langfuse observability — single, optional tracing layer.

Langfuse is OFF by default. The rest of the code always imports observe/get_client
from here and calls them the same way; when disabled, observe() is a pass-through
decorator and get_client() returns a no-op stub — so there are zero Langfuse API
calls, no cost, and no warnings.

Enable by setting LANGFUSE_ENABLED=true (plus LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
/ LANGFUSE_HOST). Tests turn it on (with a mocked SDK) to validate the wiring.
"""

import os

_ENABLED = os.getenv("LANGFUSE_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")

if _ENABLED:
    from langfuse import get_client, observe  # noqa: F401
else:

    class _NoopClient:
        """Absorbs any Langfuse call (score_current_trace, update_current_span, ...).

        Every attribute access returns a callable that ignores its args and returns
        None — so e.g. get_current_trace_id() is None and the callers skip cleanly.
        """

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    _NOOP_CLIENT = _NoopClient()

    def get_client():
        return _NOOP_CLIENT

    def observe(*decorator_args, **decorator_kwargs):
        """Pass-through stand-in for langfuse.observe — supports @observe and @observe(...)."""
        if len(decorator_args) == 1 and callable(decorator_args[0]) and not decorator_kwargs:
            return decorator_args[0]  # bare @observe

        def _decorator(fn):
            return fn

        return _decorator


__all__ = ["observe", "get_client"]
