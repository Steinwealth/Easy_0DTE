"""Delta band the single contract picker uses.

|δ| must sit in [entry_delta_min, entry_delta_max + TRENDLINE_DELTA_TOLERANCE].
"""

from __future__ import annotations

from typing import Tuple


def executor_delta_band() -> Tuple[float, float]:
    """(min, max) |δ| the TL / BO executors accept at entry (max incl. tolerance)."""
    from easy0DTE.modules.dte0_entry_selection import (
        entry_delta_min,
        entry_delta_max,
        trendline_delta_tolerance,
    )

    dmin = float(entry_delta_min())
    dmax = float(entry_delta_max()) + float(trendline_delta_tolerance())
    if dmax < dmin:
        dmax = dmin
    return dmin, dmax
