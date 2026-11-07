"""ORB1 Breakout durability Stage A latch (observability only).

Once a fill is ``execution_durable_confirmed`` / ``durable_position_confirmed``,
later ``monitor_attached`` / first-quote ticks must not regress the registry.
Closed trades must not fire the durability terminal notice.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

STAGE_A_STATES = frozenset(
    {
        "execution_durable_confirmed",
        "durable_position_confirmed",
    }
)

PRE_STAGE_A_STATES = frozenset(
    {
        "monitor_attached",
        "first_quote_hydrated",
        "monitoring_active",
        "position_registered",
        "submitted_not_durable",
        "unknown",
    }
)


def stage_a_latch_blocks(prior_state: str, new_state: str) -> bool:
    """True when ``new_state`` would demote an already Stage-A durable fill."""
    prior = str(prior_state or "").strip()
    nxt = str(new_state or "").strip()
    if prior not in STAGE_A_STATES:
        return False
    if nxt == "durability_failed":
        return False
    if nxt in STAGE_A_STATES:
        return False
    if nxt in PRE_STAGE_A_STATES:
        return True
    return bool(nxt) and nxt != prior


def durability_row_already_closed(
    row: Optional[Mapping[str, Any]],
    *,
    exit_alerted: Iterable[str],
    closed_ids: Iterable[str],
    lifecycle_state: str = "",
) -> bool:
    tid = str((row or {}).get("position_id") or "").strip()
    if not tid:
        return False
    if tid in set(exit_alerted) or tid in set(closed_ids):
        return True
    return str(lifecycle_state or "").strip() == "closed"


def filter_durability_terminal_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    exit_alerted: Iterable[str],
    closed_ids: Iterable[str] = (),
    lifecycle_by_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> list:
    """Drop rows that already sent an exit Telegram or are lifecycle-closed."""
    out = []
    life = lifecycle_by_id or {}
    for row in rows:
        tid = str((row or {}).get("position_id") or "").strip()
        rec = life.get(tid) if tid else None
        st = str((rec or {}).get("state") or "")
        if durability_row_already_closed(
            row,
            exit_alerted=exit_alerted,
            closed_ids=closed_ids,
            lifecycle_state=st,
        ):
            continue
        out.append(row)
    return out
