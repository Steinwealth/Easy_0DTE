#!/usr/bin/env python3
"""Safe wrappers so profit_optimizer never breaks trading hooks."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypeVar

log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def run_safe(label: str, fn: Callable[[], Any], *, default: Any = None) -> Any:
    try:
        return fn()
    except Exception as exc:
        log.warning("PROFIT_OPTIMIZER | action=%s_failed | error=%s", label, exc)
        return default
