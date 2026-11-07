"""Strategy-neutral 0DTE exit-grade (Stage B) durability + session verification.

ORB and Trendline share Premium Engine / unified monitoring and must share the
same exit-grade promotion, alert dedupe, and lifecycle timestamps.

Metadata keys (preserved for ORB restart continuity):
  - ``orb_0dte_durability`` — Stage A/B flags, alert dedupe
  - ``0dte_session_verification`` — fill→close timeline stamps
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

DURABILITY_META_KEY = "orb_0dte_durability"
SESSION_VERIFY_META_KEY = "0dte_session_verification"

# Session verification event names (Commit F)
EVT_FILL = "fill_ts"
EVT_REGISTRY = "registry_ts"
EVT_FIRST_MONITOR = "first_monitor_ts"
EVT_FIRST_EXACT_QUOTE = "first_exact_quote_ts"
EVT_FIRST_EXIT_GRADE = "first_exit_grade_ts"
EVT_STAGE_B = "stage_b_ts"
EVT_MFE_ARMED = "mfe_armed_ts"
EVT_EXIT_SIGNAL = "exit_signal_ts"
EVT_CLOSE_SUBMIT = "close_submission_ts"
EVT_CLOSE_CONFIRM = "close_confirmation_ts"
EVT_SHADOW_STATUS = "shadow_tracking_status"

STRATEGY_LABEL_0DTE = "Easy 0DTE"
# Back-compat aliases (unified branding — Stage B is strategy-neutral).
STRATEGY_LABEL_ORB = STRATEGY_LABEL_0DTE
STRATEGY_LABEL_TRENDLINE = STRATEGY_LABEL_0DTE


def strategy_alert_label(strategy_type: str) -> str:
    """Stage B alert brand for all 0DTE paths (ORB + Trendline)."""
    _ = strategy_type  # retained for call-site compatibility
    return STRATEGY_LABEL_0DTE


def quote_source_is_exact_contract(source: str) -> bool:
    """True only for exact Market Quote sources — never nearest/chain.

    Bare ``exact`` is ambiguous (legacy chain strike match) and must not promote
    Stage B. Accept canonical ``exact_quote`` and legacy ``exact_contract*`` tokens.
    """
    src_l = str(source or "").strip().lower()
    if not src_l:
        return False
    if "nearest" in src_l or "fresh_chain" in src_l or "force_refresh" in src_l or "bridge" in src_l:
        return False
    if "chain_strike" in src_l:
        return False
    if src_l == "exact_quote" or src_l.startswith("exact_quote_"):
        return True
    if "exact_contract" in src_l:
        return True
    return False


# Canonical Market Quote source string — the only value new code should emit for
# identity-matched exact quotes. Never emit bare ``exact``.
CANONICAL_EXACT_CONTRACT_SOURCE = "exact_quote"
LEGACY_EXACT_CONTRACT_SOURCE = "exact_contract_quote"

# Aggregation / research bucket name (not a wire source).
PREMIUM_SOURCE_BUCKET_EXACT = "exact"


def canonicalize_quote_source(source: str) -> str:
    """Normalize inbound quote/premium source aliases.

    - Bare ``exact`` → ``chain_strike_match`` (legacy chain exact-strike tag; not Market Quote)
    - ``exact_contract`` / ``exact_contract_quote`` / ``exact_quote`` → canonical ``exact_quote``
    New producers must emit ``exact_quote`` for Market Quote identity matches.
    """
    src = str(source or "").strip()
    src_l = src.lower()
    if not src_l:
        return ""
    if src_l == "exact":
        # Historical overload: chain strike match, not exact-contract Market Quote.
        return "chain_strike_match"
    if src_l in ("exact_contract", LEGACY_EXACT_CONTRACT_SOURCE, CANONICAL_EXACT_CONTRACT_SOURCE):
        return CANONICAL_EXACT_CONTRACT_SOURCE
    return src


# Canonical live sources trusted for trailing / stealth / degraded-state clear.
# Distinct from Stage B promotion (which rejects bare ``exact`` / chain_strike).
TRUSTED_TRAIL_PREMIUM_SOURCES = frozenset(
    {
        CANONICAL_EXACT_CONTRACT_SOURCE,
        LEGACY_EXACT_CONTRACT_SOURCE,
        "exit_grade",
    }
)


def premium_source_is_trusted_exact(
    source: str,
    *,
    mark_is_exit_grade: bool = False,
) -> bool:
    """Trusted exact marks for trail, stealth eligibility, and degraded-state clear.

    Accepts canonical ``exact_quote`` / ``exit_grade`` (and legacy ``exact_contract_quote``)
    and any ``quote_source_is_exact_contract`` match. Bare ``exact`` is not trusted
    (canonicalize to chain_strike_match). When ``mark_is_exit_grade`` is already
    true, the mark was Stage-B validated.
    """
    if bool(mark_is_exit_grade):
        return True
    src = canonicalize_quote_source(source).strip().lower()
    if not src:
        return False
    if src in TRUSTED_TRAIL_PREMIUM_SOURCES:
        return True
    if "exit_grade" in src:
        return True
    return quote_source_is_exact_contract(src)


def meta_exact_quote(meta: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Read exact quote blob from position metadata (new key, legacy fallback)."""
    if not isinstance(meta, dict):
        return None
    blob = meta.get("exact_quote")
    if isinstance(blob, dict):
        return blob
    legacy = meta.get("exact_contract_quote")
    return legacy if isinstance(legacy, dict) else None


def set_meta_exact_quote(meta: Dict[str, Any], quote: Dict[str, Any]) -> None:
    """Write exact quote blob under the canonical metadata key."""
    meta["exact_quote"] = quote
    # Drop legacy key on write so persisted state converges.
    meta.pop("exact_contract_quote", None)


def premium_source_is_live_contract_quote(source: str) -> bool:
    """True for live contract marks usable for clocks / non-synthetic protection.

    Includes canonical exact-contract Market Quote and common live chain tags.
    Does not treat bare ``exact`` as Market Quote (maps to chain_strike_match).
    """
    src = canonicalize_quote_source(source).strip().lower()
    if not src:
        return False
    if quote_source_is_exact_contract(src) or src == "exit_grade":
        return True
    return src in {
        "nearest",
        "cached_quote",
        "chain_strike_match",
        "nearest_strike",
        "nearest_listed",
        "nearest_exchange_strike",
        "spread_leg_quotes",
        "spread_mtm_mid_last",
        "premium_snapshot",
        "mark_or_mid",
        "fresh_chain_refresh",
        "fresh_chain_force_refresh",  # legacy
        "fresh_chain_bridge",
        "live_chain_monitor",
        "bid_executable",
    }


def ensure_durability_dict(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    dur = meta.get(DURABILITY_META_KEY)
    if not isinstance(dur, dict):
        dur = {}
        meta[DURABILITY_META_KEY] = dur
    return dur


def ensure_session_verification(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    sv = meta.get(SESSION_VERIFY_META_KEY)
    if not isinstance(sv, dict):
        sv = {}
        meta[SESSION_VERIFY_META_KEY] = sv
    return sv


def stamp_session_event(
    meta: Optional[Dict[str, Any]],
    event: str,
    *,
    value: Any = None,
    only_once: bool = True,
    now_ts: Optional[float] = None,
) -> bool:
    """Stamp a session-verification event. Returns True when written."""
    sv = ensure_session_verification(meta)
    if not isinstance(sv, dict) or not event:
        return False
    if only_once and sv.get(event) not in (None, "", 0, 0.0):
        return False
    if event == EVT_SHADOW_STATUS:
        sv[event] = value if value is not None else "unknown"
    else:
        sv[event] = float(value if value is not None else (now_ts if now_ts is not None else time.time()))
    return True


def session_verification_snapshot(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    sv = ensure_session_verification(meta)
    keys = (
        EVT_FILL,
        EVT_REGISTRY,
        EVT_FIRST_MONITOR,
        EVT_FIRST_EXACT_QUOTE,
        EVT_FIRST_EXIT_GRADE,
        EVT_STAGE_B,
        EVT_MFE_ARMED,
        EVT_EXIT_SIGNAL,
        EVT_CLOSE_SUBMIT,
        EVT_CLOSE_CONFIRM,
        EVT_SHADOW_STATUS,
    )
    return {k: sv.get(k) for k in keys}


@dataclass
class ExitGradeEvalResult:
    exit_grade_durable_ok: bool
    newly_confirmed: bool
    should_alert: bool
    promotion_blocker: str = ""
    good_mark_ticks: int = 0
    min_exit_ticks: int = 3
    quote_source: str = "unknown"
    strategy_type: str = "orb_0dte"
    alert_label: str = STRATEGY_LABEL_0DTE
    durability: Dict[str, Any] = field(default_factory=dict)


def evaluate_exit_grade_durability(
    *,
    position_metadata: Optional[Dict[str, Any]],
    trade_id: str,
    symbol: str,
    strategy_type: str,
    osnap: Optional[Dict[str, Any]] = None,
    min_exit_ticks: int = 3,
    quote_source: Optional[str] = None,
    seconds_since_fill: float = 0.0,
    now_ts: Optional[float] = None,
    stage_b_audit_interval_sec: float = 60.0,
    stage_b_audit_window_sec: float = 600.0,
    log_prefix: Optional[str] = None,
) -> ExitGradeEvalResult:
    """Evaluate Stage B exit-grade durability; mutate durability + session stamps.

    Owns: good-mark thresholding, promotion state, dedupe flags, Stage B audit
    telemetry, and session verification stamps for exit-grade / Stage B.
    Does not send Telegram — caller uses ``should_alert`` + ``alert_label``.
    """
    now = float(now_ts if now_ts is not None else time.time())
    meta = position_metadata if isinstance(position_metadata, dict) else {}
    dur = ensure_durability_dict(meta)
    snap = osnap if isinstance(osnap, dict) else {}
    st = str(strategy_type or "orb_0dte").strip().lower() or "orb_0dte"
    prefix = str(log_prefix or ("TRENDLINE" if "trendline" in st else "ORB_0DTE"))
    label = strategy_alert_label(st)

    mark_exit = bool(snap.get("mark_is_exit_grade"))
    good_ticks = int(snap.get("good_mark_tick_count") or 0)
    min_ticks = max(1, int(min_exit_ticks or 3))
    source = str(
        quote_source
        or snap.get("last_premium_source")
        or snap.get("quote_source")
        or "unknown"
    )
    mark_quality = str(snap.get("mark_quality") or "").strip().lower()
    timeout_frozen = bool(snap.get("timeout_clock_frozen"))

    # Fail-closed Exit-grade enrollment: frozen quote wire required.
    enrolled = meta.get("exit_grade_enrollment_eligible")
    if enrolled is None:
        # Backward compatible: treat explicit frozen wire on meta as enrolled.
        enrolled = bool(
            str(meta.get("broker_quote_request_symbol") or meta.get("osi_key") or "").strip()
        )
    enrolled = bool(enrolled)

    # Exact-quote session stamp when source is exact-contract.
    src_l = source.lower()
    exact_src = quote_source_is_exact_contract(source) and enrolled
    if exact_src:
        stamp_session_event(meta, EVT_FIRST_EXACT_QUOTE, now_ts=now)

    if mark_exit and exact_src:
        stamp_session_event(meta, EVT_FIRST_EXIT_GRADE, now_ts=now)

    # MFE / trail arming hint from stealth snapshot
    trailing = str(snap.get("trailing_mode") or "").strip().lower()
    if trailing and trailing not in {"inactive", "none", "off", ""}:
        stamp_session_event(meta, EVT_MFE_ARMED, now_ts=now)

    # Stage B requires exact-contract marks — fresh_chain / bridge are monitoring
    # fallbacks only and must not promote or alert as exit-grade durable.
    exit_ok = bool(enrolled and mark_exit and good_ticks >= min_ticks and exact_src)
    dur["exit_grade_durable_confirmed"] = bool(exit_ok)
    dur["last_exit_grade_eval_ts"] = now
    dur["last_good_mark_ticks"] = int(good_ticks)
    dur["last_quote_source"] = source
    dur["exit_grade_enrollment_eligible"] = enrolled

    newly = False
    should_alert = False
    blocker = ""

    if not exit_ok:
        if float(seconds_since_fill or 0.0) <= float(stage_b_audit_window_sec or 600.0):
            if not enrolled:
                blocker = "missing_frozen_quote_wire"
            elif not exact_src:
                blocker = f"quote_source_not_exact_contract:{src_l or 'unknown'}"
            elif int(good_ticks) < min_ticks:
                if timeout_frozen:
                    blocker = "timeout_clock_frozen"
                elif not mark_exit:
                    blocker = f"mark_not_exit_grade:{mark_quality or 'unknown'}"
                else:
                    blocker = f"insufficient_good_ticks:{good_ticks}/{min_ticks}"
            else:
                blocker = "unknown"
            last_audit = float(dur.get("_last_stage_b_audit_ts") or 0.0)
            if (now - last_audit) >= float(stage_b_audit_interval_sec or 60.0):
                dur["_last_stage_b_audit_ts"] = now
                log.info(
                    "%s_STAGE_B_PROMOTION_AUDIT | position_id=%s | symbol=%s | "
                    "seconds_since_fill=%.1f | quote_source=%s | quote_quality=%s | "
                    "promotion_blocker=%s | strategy_type=%s",
                    prefix,
                    str(trade_id or ""),
                    str(symbol or ""),
                    float(seconds_since_fill or 0.0),
                    source,
                    mark_quality or "unknown",
                    blocker or "unknown",
                    st,
                )
    else:
        if not dur.get("exit_grade_durable_logged"):
            dur["exit_grade_durable_logged"] = True
            newly = True
            stamp_session_event(meta, EVT_STAGE_B, now_ts=now)
            log.info(
                "%s_EXIT_GRADE_DURABLE_CONFIRMED | trade_id=%s | symbol=%s | "
                "good_mark_ticks=%d | strategy_type=%s",
                prefix,
                str(trade_id or ""),
                str(symbol or ""),
                int(good_ticks),
                st,
            )
            log.info(
                "%s_EXIT_GRADE_READY | trade_id=%s | symbol=%s | good_mark_ticks=%d | "
                "quote_source=%s | strategy_type=%s",
                prefix,
                str(trade_id or ""),
                str(symbol or ""),
                int(good_ticks),
                source,
                st,
            )
        if not dur.get("exit_grade_durable_alerted"):
            should_alert = True
            # Caller sets alerted=True after successful / attempted send to dedupe.

    return ExitGradeEvalResult(
        exit_grade_durable_ok=bool(exit_ok),
        newly_confirmed=bool(newly),
        should_alert=bool(should_alert),
        promotion_blocker=blocker,
        good_mark_ticks=int(good_ticks),
        min_exit_ticks=int(min_ticks),
        quote_source=source,
        strategy_type=st,
        alert_label=label,
        durability=dur,
    )


def mark_exit_grade_alert_sent(position_metadata: Optional[Dict[str, Any]]) -> None:
    dur = ensure_durability_dict(position_metadata)
    dur["exit_grade_durable_alerted"] = True


def alert_row_trade_id(row: Optional[Dict[str, Any]]) -> str:
    """Canonical trade_id from a fill or Stage B Telegram row."""
    if not isinstance(row, dict):
        return ""
    tid = str(row.get("position_id") or row.get("trade_id") or "").strip()
    if tid.lower() in ("", "n/a", "none"):
        return ""
    return tid


def upsert_pending_alert_row(buf: List[Dict[str, Any]], row: Dict[str, Any]) -> None:
    """Replace an existing pending row for the same trade_id, else append."""
    if not isinstance(buf, list) or not isinstance(row, dict):
        return
    tid = alert_row_trade_id(row)
    if not tid:
        buf.append(row)
        return
    for idx, existing in enumerate(buf):
        if alert_row_trade_id(existing) == tid:
            buf[idx] = row
            return
    buf.append(row)


def pop_pending_alert_rows(buf: List[Dict[str, Any]], trade_id: str) -> List[Dict[str, Any]]:
    """Remove and return pending rows for ``trade_id`` (FIFO among matches)."""
    if not isinstance(buf, list):
        return []
    tid = str(trade_id or "").strip()
    if not tid:
        return []
    kept: List[Dict[str, Any]] = []
    popped: List[Dict[str, Any]] = []
    for row in buf:
        if alert_row_trade_id(row) == tid:
            popped.append(row)
        else:
            kept.append(row)
    buf[:] = kept
    return popped
