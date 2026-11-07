"""Day-session discovery exclusion after Trendline / Breakout execution rejects.

Policy (Aug 16):
  - Trendline: hard QG / post-exec rejects **expire that slot/side envelope**
    (one attempt). A1/A2 stay frozen for plots. Opposite envelope and sibling
    slots stay eligible. Soft window / capital waits still re-arm. TL0 hard-expires
    at 07:25. TL1–TL10 first-break clock is 3h from ``built_at``.
  - ORB1: 06:45–07:25 only. Reject = 1h same-side cooldown (other H/L still
    eligible until cutoff). Fill = one trade; no range re-entry.
  - ORB2 Breakout: reject or fill arms 1h same-side cooldown and requires price
    back **inside** ORB2 before that same H/L can qualify again. Opposite edge
    stays open.
  - **Post-exit:** ORB2 same as reject (1h + re-entry). ORB1 fill stays one-trade.
    TL fill leaves the envelope EXECUTED.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Set

log = logging.getLogger(__name__)

PATH_BREAKOUT = "breakout"
PATH_TRENDLINE = "trendline"

# Timed reject cooldown store for TL: { "SYM:trendline1:call": iso_until, ... }
_TRENDLINE_SLOT_SIDE_REJECT_UNTIL_ATTR = "_trendline_slot_side_reject_until"

# Structural post-exec / handoff rejects (BO side-block; TL = current-candidate only).
TERMINAL_PATH_EXEC_REJECT_REASONS = frozenset(
    {
        "option_premium_too_low",
        "option_premium_too_high",
        "premium_out_of_band",  # legacy alias
        "option_low_volume",
        "option_no_bid",
        "no_affordable_contract_for_slot",
        "option_spread_too_wide",
        "spread_too_wide",
        "option_no_contract",
        "no_viable_contract",
        "no_contract_in_band",
        "contract_otm_too_deep",
        "chain_data_too_old",
        "contract_too_expensive_for_slot",
        "option_too_expensive_for_budget",
        "contract_selection_failed",
        "option_liquidity_failed",
        "low_open_interest",
        "option_low_open_interest",
        "low_volume",
        "delta_out_of_band",
        "trade_profile_reject",
        "trade_profile",
        "pc_oi_filter_reject",
        "pc_oi_filter",
        "dir_pct_filter_reject",
        "dir_pct_filter",
        "chain_skew_filter_reject",
        "chain_skew_filter",
        "quality_reject",
        "quality_fail",
        "exec_ready_ttl_expired",
        # Market-quality / live-entry (Aug 10): same 1h side/slot cooldown as structural.
        "ask_size_too_small",
        "bid_size_too_small",
        "ask_size_missing",
        "bid_size_missing",
        "missing_bid_or_ask",
        "market_quality_reject",
    }
)

# Pure broker / capital blips — BO may re-arm same side; TL re-arms for retry.
TRANSIENT_PATH_EXEC_REJECT_REASONS = frozenset(
    {
        "timeout",
        "rate_limited",
        "broker_timeout",
        "temporary_broker_error",
        "live_chain_required_unavailable",
        "option_live_chain_required",
        "execution_skipped_due_to_slot_sizing",
        "buffer_reserve_exhausted",
        "insufficient_balance_or_invalid_cost",
        "option_quote_stale",
        "global_slot_unavailable",
        "global_daily_cap",
        "entry_kill_switch_active",
        "option_entry_cooldown",
        # Oct 13 2026: book-full is capacity, not a signal reject (retry, no 1h cooldown).
        "max_open_positions_reached",
        "combined_cap_reached",
        "trendline_cap_reached",
        "breakout_cap_reached",
        "orb_cap_reached",
    }
)

# TL: explicit broker blips that re-arm the emitted candidate (also see TRANSIENT set).
TRENDLINE_EXEC_RETRY_ALLOWLIST = frozenset(
    {
        "rate_limited",
        "temporary_broker_error",
    }
)


def _attr_for_path(path: str) -> str:
    p = str(path or "").strip().lower()
    if p == PATH_BREAKOUT:
        return "_breakout_exec_rejects_today"
    if p == PATH_TRENDLINE:
        return "_trendline_exec_rejects_today"
    raise ValueError(f"unknown path for exec day-exclude: {path!r}")


def normalize_exec_reject_reason(err: Any) -> str:
    raw = str(err or "execute_failed").strip()
    code = raw.split("|")[0].strip().lower() or "execute_failed"
    # "pc_oi_filter_reject: detail" → pc_oi_filter_reject
    if ":" in code:
        head = code.split(":", 1)[0].strip()
        if head:
            code = head
    # Legacy → current canonical codes (cooldown / terminal matching).
    aliases = {
        "option_low_open_interest": "low_open_interest",
    }
    return aliases.get(code, code)


def is_terminal_path_exec_reject(err: Any, *, selected_delta: Any = None) -> bool:
    """BO structural / contract / pc_oi / market-quality — side-block, not whole-symbol.

    Aug 10: any non-transient post-exec reject (e.g. ``ask_size_too_small``) arms
    the 1h same-side cooldown even when the code is not in the explicit set.
    """
    code = normalize_exec_reject_reason(err)
    if code in TRANSIENT_PATH_EXEC_REJECT_REASONS:
        return False
    # False / missing δ readings are retryable (Jun 4 class); true band miss stays terminal.
    if code == "delta_out_of_band":
        try:
            from easy0DTE.modules.unified_contract_select import missing_or_zero_delta

            if selected_delta is not None and missing_or_zero_delta(selected_delta):
                return False
            if selected_delta is None:
                # No delta attached — treat as transient missing quote/δ.
                return False
        except Exception:
            return False
    if code in TERMINAL_PATH_EXEC_REJECT_REASONS:
        return True
    if any(
        code.startswith(f"{r}:") or code.startswith(f"{r}_")
        for r in TERMINAL_PATH_EXEC_REJECT_REASONS
    ):
        return True
    # Unknown non-transient executor reject → cooldown (do not re-spam same side).
    return True


def is_trendline_exec_retryable(err: Any, *, selected_delta: Any = None) -> bool:
    """TL: re-arm current emitted candidate (broker blip / capital / missing δ)."""
    code = normalize_exec_reject_reason(err)
    if code in TRENDLINE_EXEC_RETRY_ALLOWLIST:
        return True
    if code in TRANSIENT_PATH_EXEC_REJECT_REASONS:
        return True
    if code == "delta_out_of_band":
        try:
            from easy0DTE.modules.unified_contract_select import missing_or_zero_delta

            if selected_delta is not None and missing_or_zero_delta(selected_delta):
                return True
            if selected_delta is None:
                return True
        except Exception:
            return True
    return False


def is_trendline_candidate_only_exec_reject(err: Any, *, selected_delta: Any = None) -> bool:
    """TL hard post-exec class: reject this TL1/TL2/TL3 only; siblings stay live.

    Covers no-contract / premium band / spread / liquidity / pc_oi / quality / TTL —
    same structural set as BO terminal, but TL scope is **candidate**, not symbol.
    """
    if is_trendline_exec_retryable(err, selected_delta=selected_delta):
        return False
    if is_terminal_path_exec_reject(err, selected_delta=selected_delta):
        return True
    # Unknown non-retryable execute failure: still candidate-only (do not day-kill TL2/TL3).
    return True


def is_trendline_session_exec_reject(err: Any, *, selected_delta: Any = None) -> bool:
    """TL symbol day-exclude after post-exec — **retired Aug 2**.

    Always False. Contract / premium / spread / etc. use
    ``is_trendline_candidate_only_exec_reject`` + current-candidate invalidate.
    Kept for call-site / test compatibility.
    """
    _ = (err, selected_delta)
    return False


def path_exec_rejects_today(system: Any, path: str) -> Set[str]:
    attr = _attr_for_path(path)
    raw = getattr(system, attr, None)
    if not isinstance(raw, set):
        raw = set()
        setattr(system, attr, raw)
    return raw


def is_symbol_exec_rejected_today(system: Any, path: str, symbol: str) -> bool:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    return sym in path_exec_rejects_today(system, path)


def mark_symbol_exec_rejected_today(
    system: Any,
    path: str,
    symbol: str,
    *,
    reason: str = "",
) -> bool:
    """Mark ``symbol`` out of ``path`` discovery for the rest of the session.

    Returns True when newly added.
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    blocked = path_exec_rejects_today(system, path)
    if sym in blocked:
        return False
    blocked.add(sym)
    reason_s = normalize_exec_reject_reason(reason) or str(reason or "")[:120]
    log.warning(
        "PATH_EXEC_DAY_EXCLUDE | path=%s | symbol=%s | reason=%s | "
        "action=remove_from_discovery_for_session | blocked_count=%d",
        str(path or "").strip().lower(),
        sym,
        reason_s or "execute_failed",
        len(blocked),
    )
    return True


def clear_path_exec_rejects_today(system: Any, path: Optional[str] = None) -> None:
    paths = [path] if path else [PATH_BREAKOUT, PATH_TRENDLINE]
    for p in paths:
        try:
            setattr(system, _attr_for_path(p), set())
        except Exception:
            pass
    if path is None or str(path or "").strip().lower() == PATH_TRENDLINE:
        clear_trendline_slot_side_cooldowns(system)


def path_side_reject_cooldown_seconds(system: Any = None) -> float:
    """Shared 1h reject / post-exit cooldown (BO side / TL slot+side / ORB1 side)."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS", 3600)
    except Exception:
        raw = os.getenv("BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS", "3600")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 3600.0


# Canonical reason stamped when a filled trade closes (same duration as reject).
POST_EXIT_COOLDOWN_REASON = "post_exit"


def normalize_option_side(side: Any) -> str:
    s = str(side or "").strip().lower()
    if s in ("c", "call", "long", "bullish", "bull"):
        return "call"
    if s in ("p", "put", "short", "bearish", "bear"):
        return "put"
    return s


def trendline_slot_side_cooldown_key(
    symbol: str, *, slot: Any = None, side: Any = None
) -> str:
    from easyTrendline.trendline_models import normalize_trendline_slot

    sym = str(symbol or "").strip().upper()
    slot_n = normalize_trendline_slot(slot)
    side_n = normalize_option_side(side)
    return f"{sym}:{slot_n}:{side_n}"


def _trendline_slot_side_until_map(system: Any) -> Dict[str, Any]:
    raw = getattr(system, _TRENDLINE_SLOT_SIDE_REJECT_UNTIL_ATTR, None)
    if not isinstance(raw, dict):
        raw = {}
        setattr(system, _TRENDLINE_SLOT_SIDE_REJECT_UNTIL_ATTR, raw)
    return raw


def _parse_cooldown_until(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        text = str(raw).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def should_arm_trendline_slot_side_cooldown(reason: Any) -> bool:
    """True for hard TL rejects / TTL / post-exit; false for slot waits and broker blips."""
    raw = str(reason or "")
    try:
        from modules.path_exec_ready_ttl import (
            is_exec_ready_ttl_reason,
            ttl_retire_no_cooldown_enabled,
        )

        if is_exec_ready_ttl_reason(raw) and ttl_retire_no_cooldown_enabled():
            return False  # Oct 13 2026: TTL retire re-arms, no 1h cooldown
    except Exception:
        pass
    code = normalize_exec_reject_reason(raw)
    if code == POST_EXIT_COOLDOWN_REASON or code == "post_exit_cooldown":
        return True
    if is_trendline_exec_retryable(code):
        return False
    if code in (
        "slot_side_cooldown",
        "open_same_side_dedupe",
        "slot_unavailable_reject",
    ):
        return False
    if raw.startswith("slot_unavailable_reject:"):
        return False
    return True


def arm_trendline_post_exit_cooldown(
    system: Any,
    symbol: str,
    side: Any,
    *,
    slot: Any = None,
) -> Optional[datetime]:
    """Arm 1h TL slot+side cooldown after a filled exit (reject-parity)."""
    return mark_trendline_slot_side_cooldown(
        system,
        symbol,
        side,
        slot=slot,
        reason=POST_EXIT_COOLDOWN_REASON,
    )


def mark_trendline_slot_side_cooldown(
    system: Any,
    symbol: str,
    side: Any,
    *,
    slot: Any = None,
    reason: str = "",
    seconds: Optional[float] = None,
) -> Optional[datetime]:
    """Arm 1h cooldown for one TL slot + symbol + side (siblings stay open)."""
    sym = str(symbol or "").strip().upper()
    side_n = normalize_option_side(side)
    if not sym or not side_n:
        return None
    try:
        from easyTrendline.trendline_models import (
            normalize_trendline_slot,
            trendline_slot_short_label,
        )

        slot_n = normalize_trendline_slot(slot)
        slot_label = trendline_slot_short_label(slot_n)
    except Exception:
        slot_n = str(slot or "trendline1")
        slot_label = slot_n
    key = trendline_slot_side_cooldown_key(sym, slot=slot_n, side=side_n)
    cooldown_sec = (
        float(seconds)
        if seconds is not None
        else path_side_reject_cooldown_seconds(system)
    )
    store = _trendline_slot_side_until_map(system)
    now = datetime.now(timezone.utc)
    existing = _parse_cooldown_until(store.get(key))
    # Repeat quality rejects must not slide the 1h window forward.
    if existing is not None and existing > now:
        reason_s = normalize_exec_reject_reason(reason) or str(reason or "")[:120]
        log.info(
            "TRENDLINE_SLOT_SIDE_COOLDOWN_BLOCK | symbol=%s | trendline_slot=%s | "
            "slot_label=%s | side=%s | reason=%s | cooldown_sec=%.0f | blocked_until=%s | "
            "action=keep_existing_until | key=%s",
            sym,
            slot_n,
            slot_label,
            side_n,
            reason_s or "execute_failed",
            cooldown_sec,
            existing.isoformat(),
            key,
        )
        return existing
    until = now + timedelta(seconds=max(0.0, cooldown_sec))
    store[key] = until.isoformat()
    reason_s = normalize_exec_reject_reason(reason) or str(reason or "")[:120]
    log.warning(
        "TRENDLINE_SLOT_SIDE_COOLDOWN_BLOCK | symbol=%s | trendline_slot=%s | "
        "slot_label=%s | side=%s | reason=%s | cooldown_sec=%.0f | blocked_until=%s | "
        "action=block_slot_side_keep_sibling_slots_open | key=%s",
        sym,
        slot_n,
        slot_label,
        side_n,
        reason_s or "execute_failed",
        cooldown_sec,
        until.isoformat(),
        key,
    )
    return until


def is_trendline_slot_side_on_cooldown(
    system: Any,
    symbol: str,
    side: Any,
    *,
    slot: Any = None,
) -> bool:
    """True while this symbol+TL-slot+side is inside the 1h reject cooldown."""
    sym = str(symbol or "").strip().upper()
    side_n = normalize_option_side(side)
    if not sym or not side_n:
        return False
    key = trendline_slot_side_cooldown_key(sym, slot=slot, side=side_n)
    store = _trendline_slot_side_until_map(system)
    until = _parse_cooldown_until(store.get(key))
    if until is None:
        return False
    now = datetime.now(timezone.utc)
    if until > now:
        return True
    store.pop(key, None)
    return False


def is_trendline_candidate_on_slot_side_cooldown(system: Any, candidate: Any) -> bool:
    """True when this TL candidate's symbol+slot+expected side is inside the 1h cooldown."""
    if system is None or candidate is None:
        return False
    try:
        from easyTrendline.trendline_models import trendline_slot_from_payload
        from easyTrendline.trendline_simple_mode import candidate_expected_option_side

        slot = trendline_slot_from_payload(getattr(candidate, "setup_payload", None) or {})
        side = candidate_expected_option_side(candidate)
        return is_trendline_slot_side_on_cooldown(
            system,
            getattr(candidate, "symbol", ""),
            side,
            slot=slot,
        )
    except Exception:
        return False


def clear_trendline_slot_side_cooldowns(system: Any) -> None:
    try:
        setattr(system, _TRENDLINE_SLOT_SIDE_REJECT_UNTIL_ATTR, {})
    except Exception:
        pass
