"""Compact, turn-correlated terminal tracing for the Gemini Live pipeline."""

from __future__ import annotations

import contextvars
import logging
import uuid
from typing import Any


TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")
logger = logging.getLogger("lumi.trace")
_turn_id: contextvars.ContextVar[str] = contextvars.ContextVar("lumi_trace_turn", default="----")


def begin_turn() -> str:
    """Create a short identifier inherited by all async work in this turn."""

    turn_id = uuid.uuid4().hex[:4]
    _turn_id.set(turn_id)
    return turn_id


def turn_id() -> str:
    return _turn_id.get()


def trace(event: str, *args: Any) -> None:
    logger.log(TRACE_LEVEL, "turn=%s | " + event, turn_id(), *args)


def warning(event: str, *args: Any) -> None:
    logger.warning("turn=%s | " + event, turn_id(), *args)


def error(event: str, *args: Any) -> None:
    logger.error("turn=%s | " + event, turn_id(), *args)
