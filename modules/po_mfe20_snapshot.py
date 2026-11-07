"""One-shot Priority Optimizer snapshot at the first +20% MFE.

Stage names end in ``options_20p`` so entry, 20p, and exit files stay distinct.
Hydration is the exit snapshot path with ``purpose="20p"``. Session tech is
restamped from the bars at the first +20% MFE, and the chain overlay is that
moment's chain cache rather than the gate row. A live broker pull is only the
fallback. This module does not change the exit decision.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

log = logging.getLogger(__name__)

MFE_20 = 0.20
# Process-lifetime guard. Same trade_id never writes a second 20p file, even
# if option_stealth is rebuilt after the first save. A new trade_id on the
# same symbol is a new snapshot.
_SAVED_TRADE_IDS: set = set()
_INFLIGHT_TRADE_IDS: set = set()
_CLAIM_LOCK = threading.Lock()


def _env_num(name: str, default: float) -> float:
    import os

    try:
        raw = os.environ.get(name)
        return float(raw) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        return float(default)


def dir5m_20p_prefetch_enabled() -> bool:
    import os

    raw = str(os.environ.get("DIR5M_20P_PREFETCH_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _ring_latest_t(system: Any, symbol: str) -> Optional[float]:
    try:
        from central_tech.dir5m_presample import _ring_latest_t as _rl

        return _rl(system, str(symbol).upper())
    except Exception:
        return None


def maybe_prefetch_20p_chain(system: Any, pos: Any, osnap: Any, *, now: Optional[float] = None) -> str:
    """t204u: keep a fresh chain in the ring BEFORE the first +20% cross (causal 20p now-chain).

    Runs on every 2 s monitor tick (no broker call of its own). While the trade has
    not yet reached +20% and its live return (``current_pnl_pct``) is in
    [DIR5M_20P_PREFETCH_MFE (0.08), +20%), queue one P0 chain fetch (reason
    ``dir5m_20p_prefetch``) whenever the ring's newest chain for the underlying is
    older than DIR5M_20P_PREFETCH_REFRESH_SEC (30 s), at most once per
    DIR5M_20P_PREFETCH_RETRY_SEC (15 s) per trade and DIR5M_20P_PREFETCH_MAX_PER_MIN
    (6) per rolling 60 s overall. The 20p snapshot then reads
    the newest ring chain observed <= ``po_20p_event_at`` (cap
    DIR5M_20P_NOW_MAX_AGE_SEC); a chain fetched after the event is never used.
    """
    import time as _t

    if not dir5m_20p_prefetch_enabled() or not isinstance(osnap, dict):
        return "disabled"
    if osnap.get("po_20p_snapshot_saved") or osnap.get("po_20p_event_at") not in (None, ""):
        return "past_event"
    sym = str(getattr(pos, "symbol", "") or "").strip().upper()
    if not sym:
        return "no_symbol"
    def _num(k: str) -> Optional[float]:
        try:
            v = osnap.get(k)
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    # Live return (2 s monitor quote) decides the band; MFE only says +20% was already hit.
    cur = _num("current_pnl_pct")
    mfe = max([x for x in (_num("max_pnl_pct"), _num("po_20p_first_cross_mfe")) if x is not None] or [0.0])
    live = cur if cur is not None else mfe
    lo = max(0.0, _env_num("DIR5M_20P_PREFETCH_MFE", 0.08))
    if mfe >= MFE_20 or live < lo:
        return "outside_band"
    t = float(now if now is not None else _t.time())
    lt = _ring_latest_t(system, sym)
    if lt is not None and t - lt < max(5.0, _env_num("DIR5M_20P_PREFETCH_REFRESH_SEC", 30.0)):
        return "fresh"
    last = osnap.get("po_20p_dir5m_prefetch_at")
    try:
        if last is not None and t - float(last) < max(2.0, _env_num("DIR5M_20P_PREFETCH_RETRY_SEC", 15.0)):
            return "recent"
    except (TypeError, ValueError):
        pass
    q = getattr(system, "_dir5m_20p_prefetch_q", None)
    if not isinstance(q, list):
        q = []
    q = [x for x in q if t - x <= 60.0]
    if len(q) >= int(max(0, _env_num("DIR5M_20P_PREFETCH_MAX_PER_MIN", 6))):
        return "cap_minute"
    try:
        from central_tech.chain_fetch_manager import P_POSITION, get_manager

        mgr = get_manager(system)
        if mgr is None:
            return "no_manager"
        side = str(getattr(pos, "option_side", "") or osnap.get("option_side") or "").lower() or None
        mgr.enqueue(sym, priority=P_POSITION, side=side, reason="dir5m_20p_prefetch")
        try:
            mgr.ensure_sampler()
        except Exception:
            pass
    except Exception:
        return "enqueue_failed"
    q.append(t)
    try:
        setattr(system, "_dir5m_20p_prefetch_q", q)
    except Exception:
        pass
    osnap["po_20p_dir5m_prefetch_at"] = t
    osnap["po_20p_dir5m_prefetch_n"] = int(osnap.get("po_20p_dir5m_prefetch_n") or 0) + 1
    return "queued"


def _trade_already_snapped(trade_id: str, osnap: Any, metadata: Any) -> bool:
    tid = str(trade_id or "").strip()
    if tid and tid in _SAVED_TRADE_IDS:
        return True
    if isinstance(osnap, dict) and osnap.get("po_20p_snapshot_saved"):
        return True
    if isinstance(metadata, dict) and metadata.get("po_20p_snapshot_saved"):
        return True
    return False


def mark_po_20p_due(osnap: Any, prev_mfe: Any, new_mfe: Any) -> None:
    """Keep a peak-cross due flag. The 20p write waits for first-bid capture.

    Peak MFE is not the snapshot clock. ``capture_po_20p_first_touch`` arms the
    event from the live bid and freezes one hub read. This helper only notes
    that the running peak has reached +20% so a later bid touch can persist.
    """
    if not isinstance(osnap, dict):
        return
    if osnap.get("po_20p_snapshot_saved"):
        osnap["po_20p_snapshot_due"] = False
        return
    try:
        now = float(new_mfe or 0.0)
    except (TypeError, ValueError):
        return
    if now < MFE_20:
        return
    if osnap.get("po_20p_arm_source") in (None, ""):
        osnap["po_20p_arm_source"] = "peak"
    osnap["po_20p_snapshot_due"] = True


def arm_20p_from_live_bid_return(osnap: Any, pret: Any) -> None:
    """Arm the first +20% event from the live bid return.

    Path V2 and the premium path both use this return. The stealth high-water
    mark can reject the same print. That rejection must not drop the event.
    """
    if not isinstance(osnap, dict):
        return
    try:
        bid = float(pret or 0.0)
    except (TypeError, ValueError):
        return
    if bid + 1e-12 < MFE_20:
        return
    if osnap.get("po_20p_snapshot_saved"):
        osnap["po_20p_snapshot_due"] = False
        return
    if osnap.get("po_20p_first_cross_mfe") is None:
        osnap["po_20p_first_cross_mfe"] = bid
    from modules.po_event_seal import note_20p_event

    note_20p_event(osnap)
    osnap["po_20p_snapshot_due"] = True
    osnap["po_20p_arm_source"] = "first_bid"


def capture_po_20p_first_touch(
    system: Any,
    osnap: Any,
    *,
    bid_pret: Any,
    now_utc: Any,
    symbol: str,
    max_age_s: float = 90.0,
    allow_sync_refresh: bool = True,
    refresh_timeout_s: float = 3.0,
) -> Any:
    """Priority Optimizer first +20% bid touch: arm once and freeze one hub read.

    Independent of the 20% Save Profit Capture exit. Save may reuse
    ``osnap['po_20p_tech']`` on the same tick; it does not own this write.
    """
    if not isinstance(osnap, dict):
        return None
    if osnap.get("po_20p_snapshot_saved"):
        return osnap.get("po_20p_tech") if isinstance(osnap.get("po_20p_tech"), dict) else None
    arm_20p_from_live_bid_return(osnap, bid_pret)
    if osnap.get("po_20p_arm_source") != "first_bid":
        return osnap.get("po_20p_tech") if isinstance(osnap.get("po_20p_tech"), dict) else None
    existing = osnap.get("po_20p_tech")
    if isinstance(existing, dict) and existing:
        return existing
    try:
        from modules.session_tech_indicators import read_session_tech_at

        tech = read_session_tech_at(
            system,
            str(symbol or ""),
            now_utc=now_utc,
            max_age_s=float(max_age_s),
            allow_sync_refresh=bool(allow_sync_refresh),
            refresh_timeout_s=float(refresh_timeout_s),
        )
    except Exception:
        tech = None
    if isinstance(tech, dict) and tech:
        osnap["po_20p_tech"] = tech
        return tech
    return None


def capture_po_20p_from_metadata(
    system: Any,
    pos: Any,
    metadata: Any,
    *,
    now_utc: Any = None,
) -> Any:
    if not isinstance(metadata, dict):
        return None
    osnap = metadata.get("option_stealth")
    if not isinstance(osnap, dict):
        return None
    return capture_po_20p_first_touch(
        system,
        osnap,
        bid_pret=osnap.get("current_pnl_pct"),
        now_utc=now_utc,
        symbol=str(getattr(pos, "symbol", "") or ""),
    )


def _po_20p_extra_fields(osnap: dict) -> dict:
    tech = osnap.get("po_20p_tech") if isinstance(osnap.get("po_20p_tech"), dict) else {}
    save20 = osnap.get("po_save20") if isinstance(osnap.get("po_save20"), dict) else {}
    extra = {
        "arm_source": osnap.get("po_20p_arm_source") or "first_bid",
        "save20": save20 or None,
        "save20_decision": save20.get("decision"),
        "save20_hold_reason": save20.get("hold_reason"),
        "pve3": tech.get("price_volume_efficiency_3m", save20.get("pve3")),
        "pve3_limit": save20.get("pve3_limit"),
        "pve3_bar_at": tech.get("pve3_bar_at")
        or tech.get("session_tech_bar_at")
        or save20.get("pve3_bar_at"),
        "pve3_age_s": tech.get("pve3_age_s", save20.get("pve3_age_s")),
        "tech_age_s": tech.get("tech_age_s", save20.get("tech_age_s")),
        "hub_read_id": tech.get("hub_read_id", save20.get("hub_read_id")),
        "refreshed_sync": tech.get("refreshed_sync", save20.get("refreshed_sync")),
    }
    try:
        from modules.po_snapshot_hydration import fill_missing_tp_keys_from_osnap

        fill_missing_tp_keys_from_osnap(extra, osnap)
        fill_missing_tp_keys_from_osnap(extra, tech)
    except Exception:
        pass
    return extra


def _stage_for(pos: Any, metadata: dict) -> tuple:
    hint = ""
    try:
        from modules.unified_0dte_account import _position_strategy_type

        hint = str(_position_strategy_type(pos) or "").lower()
    except Exception:
        hint = str(getattr(pos, "strategy_type", "") or "").lower()
    if not hint:
        for key in ("strategy_type", "source_path", "strategy"):
            hint = str(metadata.get(key) or "").lower()
            if hint:
                break
    if "breakout" in hint:
        return "breakout_options_20p", "easy_breakout_0dte"
    if "trendline" in hint or "tline" in hint:
        return "trendline_options_20p", "easy_trendline_0dte"
    return "orb_0dte_options_20p", "easy_orb_0dte"


def maybe_persist_po_20p_snapshot(
    system: Any,
    pos: Any,
    metadata: Any,
    current_underlying: Any = None,
    premium: Any = None,
) -> None:
    """Write the 20p snapshot once. Failures stay due so the next tick can retry."""
    if not isinstance(metadata, dict):
        return
    osnap = metadata.get("option_stealth")
    if not isinstance(osnap, dict):
        return
    try:
        maybe_prefetch_20p_chain(system, pos, osnap)
    except Exception:
        pass
    trade_id = str(getattr(pos, "position_id", "") or metadata.get("trade_id") or "").strip()
    with _CLAIM_LOCK:
        if _trade_already_snapped(trade_id, osnap, metadata):
            osnap["po_20p_snapshot_saved"] = True
            osnap["po_20p_snapshot_due"] = False
            metadata["po_20p_snapshot_saved"] = True
            if trade_id:
                _SAVED_TRADE_IDS.add(trade_id)
            return
        if osnap.get("po_20p_seal_status") in ("build_failed", "recovery_unsealed"):
            osnap["po_20p_snapshot_due"] = False
            return
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from modules.po_canonical_store import durable_event_present
        from modules.po_event_seal import clock_born_this_process_20p, sealed_20p

        if trade_id and (
            osnap.get("po_20p_snapshot_due") or osnap.get("po_20p_event_at") not in (None, "")
        ):
            day = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
            if durable_event_present(day, "mfe_20p", trade_id):
                osnap["po_20p_snapshot_saved"] = True
                osnap["po_20p_snapshot_due"] = False
                metadata["po_20p_snapshot_saved"] = True
                _SAVED_TRADE_IDS.add(trade_id)
                return
            from modules.po_canonical_store import load_sealed_payload

            sealed = load_sealed_payload(day, "mfe_20p", trade_id)
            if isinstance(sealed, dict):
                from modules.execution_feature_snapshot import persist_execution_snapshot

                persist_execution_snapshot(sealed)
                osnap["po_20p_snapshot_saved"] = True
                osnap["po_20p_snapshot_due"] = False
                osnap["po_20p_seal_status"] = "recovered_seal"
                metadata["po_20p_snapshot_saved"] = True
                _SAVED_TRADE_IDS.add(trade_id)
                return

        event_at = osnap.get("po_20p_event_at")
        if (
            event_at not in (None, "")
            and sealed_20p(trade_id) is None
            and not clock_born_this_process_20p(event_at)
        ):
            osnap["po_20p_seal_status"] = "recovery_unsealed"
            osnap["po_20p_training_eligible"] = False
            osnap["po_20p_snapshot_due"] = False
            log.warning(
                "PO_20P_SNAPSHOT | recovery_unsealed | trade_id=%s | event_at=%s",
                trade_id,
                event_at,
            )
            return
        if not osnap.get("po_20p_snapshot_due"):
            return
        if osnap.get("po_20p_arm_source") != "first_bid":
            return
        if osnap.get("po_20p_snapshot_inflight"):
            return
        if trade_id and trade_id in _INFLIGHT_TRADE_IDS:
            return
        # Claim before the write so a second tick, or a later pass back through
        # +20% on this same trade_id, cannot start another file.
        osnap["po_20p_snapshot_inflight"] = True
        if trade_id:
            _INFLIGHT_TRADE_IDS.add(trade_id)
    try:
        mfe = float(osnap.get("max_pnl_pct") or 0.0)
    except (TypeError, ValueError):
        mfe = 0.0
    try:
        crossed = float(osnap.get("po_20p_first_cross_mfe") or 0.0)
    except (TypeError, ValueError):
        crossed = 0.0
    # The live bid return can cross +20% before stealth copies it onto max_pnl_pct.
    mfe = max(mfe, crossed)
    if mfe < MFE_20:
        with _CLAIM_LOCK:
            osnap["po_20p_snapshot_inflight"] = False
            if trade_id:
                _INFLIGHT_TRADE_IDS.discard(trade_id)
        return
    snap_px = osnap.get("last_effective_premium")
    if snap_px is None and premium is not None:
        snap_px = getattr(premium, "executable_exit_price", None)
    try:
        und = float(current_underlying or 0.0)
    except (TypeError, ValueError):
        und = 0.0
    if und <= 0:
        try:
            und = float(osnap.get("current_underlying_price") or 0.0)
        except (TypeError, ValueError):
            und = 0.0
    stage, strategy = _stage_for(pos, metadata)
    symbol = str(getattr(pos, "symbol", "") or "")
    try:
        ok = system._save_0dte_exit_priority_snapshot(
            stage=stage,
            snapshot_strategy=strategy,
            symbol=symbol,
            trade_id=trade_id,
            option_side=getattr(pos, "option_side", None) or metadata.get("option_side"),
            entry_premium=getattr(pos, "entry_price", None),
            exit_underlying=und if und > 0 else None,
            position=pos,
            purpose="20p",
            extra={
                "event": "mfe_20p",
                "mfe_pct": mfe,
                "hwm_pnl_pct": mfe,
                "peak_pct": mfe,
                "current_pnl_pct": osnap.get("current_pnl_pct"),
                "snapshot_premium": snap_px,
                "first_cross_mfe": osnap.get("po_20p_first_cross_mfe"),
                "po_20p_event_at": osnap.get("po_20p_event_at"),
                "dir_pct_20p_prefetch_n": osnap.get("po_20p_dir5m_prefetch_n"),
                "via": "po_mfe20_snapshot",
                **_po_20p_extra_fields(osnap),
            },
            frozen_tech=osnap.get("po_20p_tech") if isinstance(osnap.get("po_20p_tech"), dict) else None,
        )
    except Exception as exc:
        from modules.po_event_seal import sealed_20p

        log.warning(
            "PO_20P_SNAPSHOT | failed | trade_id=%s | symbol=%s | err=%s",
            trade_id,
            symbol,
            exc,
        )
        with _CLAIM_LOCK:
            osnap["po_20p_snapshot_inflight"] = False
            if trade_id:
                _INFLIGHT_TRADE_IDS.discard(trade_id)
            log.warning(
                "PO_20P_SNAPSHOT | retry | trade_id=%s | event_at=%s",
                trade_id,
                osnap.get("po_20p_event_at"),
            )
        return
    from modules.po_event_seal import sealed_20p

    with _CLAIM_LOCK:
        osnap["po_20p_snapshot_inflight"] = False
        if trade_id:
            _INFLIGHT_TRADE_IDS.discard(trade_id)
        if not ok and sealed_20p(trade_id) is None:
            log.warning(
                "PO_20P_SNAPSHOT | retry | trade_id=%s | event_at=%s",
                trade_id,
                osnap.get("po_20p_event_at"),
            )
        if ok:
            osnap["po_20p_snapshot_saved"] = True
            osnap["po_20p_snapshot_due"] = False
            metadata["po_20p_snapshot_saved"] = True
            if trade_id:
                _SAVED_TRADE_IDS.add(trade_id)
            log.info(
                "PO_20P_SNAPSHOT | saved | trade_id=%s | symbol=%s | stage=%s | mfe=%.4f",
                trade_id,
                symbol,
                stage,
                mfe,
            )
