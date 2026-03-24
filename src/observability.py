"""Langfuse observability — single place for tracing config.

Langfuse auto-initializes from environment variables:
  LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST

Import observe and get_client from this module instead of langfuse directly.
"""

from langfuse import get_client, observe  # noqa: F401

__all__ = ["observe", "get_client"]
