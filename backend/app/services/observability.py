from __future__ import annotations

import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class Observation:
    def __init__(self, delegate: Any | None = None) -> None:
        self._delegate = delegate

    @property
    def trace_id(self) -> str | None:
        value = getattr(self._delegate, "trace_id", None)
        return str(value) if value else None

    def update(self, **attributes: Any) -> None:
        if self._delegate is None:
            return
        try:
            self._delegate.update(**attributes)
        except Exception:
            logger.warning("Langfuse observation update failed", exc_info=True)


@lru_cache(maxsize=1)
def _langfuse_client() -> Any | None:
    settings = get_settings()
    if not (
        settings.langfuse_public_key
        and settings.langfuse_secret_key
        and settings.langfuse_host
    ):
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_host,
        )
    except Exception:
        logger.warning("Langfuse initialization failed; using no-op observer", exc_info=True)
        return None


@contextmanager
def observe(
    name: str,
    *,
    as_type: str = "span",
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Generator[Observation, None, None]:
    client = _langfuse_client()
    if client is None:
        yield Observation()
        return
    attributes: dict[str, Any] = {
        "as_type": as_type,
        "name": name,
        "metadata": metadata or {},
    }
    if model:
        attributes["model"] = model
    try:
        manager = client.start_as_current_observation(**attributes)
        delegate = manager.__enter__()
    except Exception:
        logger.warning("Langfuse observation start failed; using no-op observer", exc_info=True)
        yield Observation()
        return
    try:
        yield Observation(delegate)
    finally:
        try:
            manager.__exit__(*sys.exc_info())
        except Exception:
            logger.warning("Langfuse observation close failed", exc_info=True)
