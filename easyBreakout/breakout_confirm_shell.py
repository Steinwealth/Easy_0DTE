"""
Shared flat-box Breakout confirm shell for ORB1 + ORB2 Breakout.

Both paths own their level stores and windows; confirmation FSM is one function:
``process_breakout_tick`` in ``breakout_confirmation.py``.

Import from here in new call sites so greps show the shared shell explicitly.
"""

from __future__ import annotations

from easyBreakout.breakout_confirmation import (  # noqa: F401
    BreakoutConfirmResult,
    process_breakout_tick,
    should_soft_expire_entry_window,
)
from easyBreakout.breakout_models import BreakoutConfig  # noqa: F401

__all__ = [
    "BreakoutConfig",
    "BreakoutConfirmResult",
    "process_breakout_tick",
    "should_soft_expire_entry_window",
]
