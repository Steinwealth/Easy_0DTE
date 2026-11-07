"""Calendar DTE + expiry-selection metadata for 0DTE trade records.

Prefer same-day expiry; otherwise pick the nearest listed expiry toward 0DTE
(``nearest_to_0dte``). Never hardcode ``dte: 0`` or ``is_0dte: True`` — derive
from dates:

    calendar_dte = (expiry_date - session_date).days
    is_0dte = expiry_date == session_date
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

EXPIRY_MODE_SAME_DAY = "same_day"
EXPIRY_MODE_NEAREST_TO_0DTE = "nearest_to_0dte"
# Legacy persisted value (pre Aug 10 rename) — normalize on read/write.
EXPIRY_MODE_NEAREST_FORWARD_LEGACY = "nearest_forward_fallback"
EXPIRY_MODE_NEAREST_FORWARD = EXPIRY_MODE_NEAREST_TO_0DTE  # alias
EXPIRY_MODE_SELECTED_ED = "selected_ed_override"
EXPIRY_MODE_EXPLICIT = "explicit"
EXPIRY_MODE_NONE = "none"
EXPIRY_MODE_UNKNOWN = "unknown"

_LEGACY_NEAREST_MODE_ALIASES = frozenset(
    {
        EXPIRY_MODE_NEAREST_FORWARD_LEGACY,
        "nearest_forward",
        "NEAREST_FALLBACK",
        "nearest_fallback",
    }
)


def normalize_expiry_selection_mode(mode: Any) -> str:
    """Canonicalize selection-mode labels (legacy → ``nearest_to_0dte``)."""
    m = str(mode or "").strip()
    if not m:
        return ""
    if m in _LEGACY_NEAREST_MODE_ALIASES or m.lower() in {
        "nearest_forward_fallback",
        "nearest_forward",
        "nearest_fallback",
    }:
        return EXPIRY_MODE_NEAREST_TO_0DTE
    return m

FALLBACK_REASON_SAME_DAY_NOT_LISTED = "same_day_expiry_not_listed"
FALLBACK_REASON_SELECTED_ED = "selected_ed_differs_from_request"

# Keys persisted on trade / normalize / stealth / PO records.
EXPIRY_TRADE_FIELD_KEYS = (
    "session_date",
    "expiry_ymd",
    "calendar_dte",
    "is_0dte",
    "expiry_selection_mode",
    "requested_expiry_ymd",
    "selected_expiry_ymd",
    "fallback_days",
    "fallback_reason",
    "dte",  # alias of calendar_dte for legacy readers
)


def us_equity_session_date_iso(now: Optional[datetime] = None) -> str:
    """U.S. equity session calendar day (America/New_York) as YYYY-MM-DD."""
    dt = now or datetime.now(_ET)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(_ET)
    else:
        dt = dt.astimezone(_ET)
    return dt.date().isoformat()


def normalize_expiry_iso(raw: Any, *, default: str = "") -> str:
    """Normalize expiry-like values to YYYY-MM-DD (empty string if unknown)."""
    if raw is None:
        return default
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw.isoformat()
    if isinstance(raw, datetime):
        return raw.astimezone(_ET).date().isoformat() if raw.tzinfo else raw.date().isoformat()
    s = str(raw or "").strip()
    if not s:
        return default
    digits = s.replace("-", "").replace("/", "")
    if len(digits) >= 8 and digits[:8].isdigit():
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return default


def normalize_expiry_compact(raw: Any, *, default: str = "") -> str:
    """Normalize to YYYYMMDD (empty if unknown)."""
    iso = normalize_expiry_iso(raw, default="")
    return iso.replace("-", "") if iso else default


def parse_iso_date(raw: Any) -> Optional[date]:
    iso = normalize_expiry_iso(raw, default="")
    if not iso:
        return None
    try:
        return date.fromisoformat(iso)
    except ValueError:
        return None


def calendar_dte(
    expiry: Any,
    session: Any,
) -> Optional[int]:
    """(expiry_date - session_date).days, or None if either date is missing."""
    exp_d = parse_iso_date(expiry)
    ses_d = parse_iso_date(session)
    if exp_d is None or ses_d is None:
        return None
    return (exp_d - ses_d).days


def is_calendar_0dte(expiry: Any, session: Any) -> bool:
    """True only when listed expiry equals the session calendar day."""
    dte = calendar_dte(expiry, session)
    return dte == 0


def _days_between(a: Any, b: Any) -> Optional[int]:
    """selected - requested in calendar days."""
    return calendar_dte(a, b)


def map_chain_selected_type_to_mode(selected_type: str) -> str:
    st = str(selected_type or "").strip().upper()
    if st in ("0DTE", "SAME_DAY", "SAME"):
        return EXPIRY_MODE_SAME_DAY
    if st in ("NEAREST", "NEAREST_FALLBACK", "FALLBACK", "NEAREST_TO_0DTE"):
        return EXPIRY_MODE_NEAREST_TO_0DTE
    if st in ("SELECTED_ED", "SELECTED"):
        return EXPIRY_MODE_SELECTED_ED
    if st == "EXPLICIT":
        return EXPIRY_MODE_EXPLICIT
    if st == "NONE":
        return EXPIRY_MODE_NONE
    return EXPIRY_MODE_UNKNOWN


def build_expiry_trade_fields(
    *,
    selected_expiry: Any = None,
    requested_expiry: Any = None,
    session_date: Any = None,
    expiry_selection_mode: Optional[str] = None,
    fallback_reason: Optional[str] = None,
    fallback_days: Optional[int] = None,
    selected_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the canonical expiry block for trade records."""
    session_iso = normalize_expiry_iso(session_date) or us_equity_session_date_iso()
    selected_iso = normalize_expiry_iso(selected_expiry) or ""
    requested_iso = normalize_expiry_iso(requested_expiry) or session_iso

    mode = normalize_expiry_selection_mode(expiry_selection_mode)
    if not mode and selected_type:
        mode = map_chain_selected_type_to_mode(selected_type)
    if not mode:
        if selected_iso and selected_iso == requested_iso:
            mode = EXPIRY_MODE_SAME_DAY
        elif selected_iso and selected_iso != requested_iso:
            mode = EXPIRY_MODE_NEAREST_TO_0DTE
        else:
            mode = EXPIRY_MODE_UNKNOWN
    else:
        mode = normalize_expiry_selection_mode(mode) or mode

    dte = calendar_dte(selected_iso, session_iso) if selected_iso else None
    is_0dte = bool(dte == 0) if dte is not None else False

    fb_days = fallback_days
    if fb_days is None and selected_iso and requested_iso:
        fb_days = _days_between(selected_iso, requested_iso)
    if fb_days is None:
        fb_days = 0

    reason = fallback_reason
    if reason is None:
        if mode == EXPIRY_MODE_NEAREST_TO_0DTE and int(fb_days or 0) != 0:
            reason = FALLBACK_REASON_SAME_DAY_NOT_LISTED
        elif mode == EXPIRY_MODE_SELECTED_ED:
            reason = FALLBACK_REASON_SELECTED_ED
        else:
            reason = ""

    return {
        "session_date": session_iso,
        "expiry_ymd": selected_iso or requested_iso,
        "calendar_dte": dte if dte is not None else None,
        "is_0dte": is_0dte,
        "expiry_selection_mode": mode,
        "requested_expiry_ymd": requested_iso,
        "selected_expiry_ymd": selected_iso or requested_iso,
        "fallback_days": int(fb_days or 0),
        "fallback_reason": reason or "",
        "dte": dte if dte is not None else None,
    }


def extract_expiry_fields_from_mapping(src: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Pull known expiry keys from any mapping (chain / payload / norm / stealth)."""
    if not isinstance(src, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in EXPIRY_TRADE_FIELD_KEYS:
        if key in src and src.get(key) is not None:
            out[key] = src.get(key)
    # Chain-manager aliases
    if "selected_expiry" in src and "selected_expiry_ymd" not in out:
        out["selected_expiry_ymd"] = normalize_expiry_iso(src.get("selected_expiry"))
    if "requested_expiry" in src and "requested_expiry_ymd" not in out:
        out["requested_expiry_ymd"] = normalize_expiry_iso(src.get("requested_expiry"))
    if "expiry_selection_mode" not in out and src.get("selected_type"):
        out["expiry_selection_mode"] = map_chain_selected_type_to_mode(str(src.get("selected_type")))
    if "expiry_selection_mode" in out:
        out["expiry_selection_mode"] = (
            normalize_expiry_selection_mode(out.get("expiry_selection_mode"))
            or out.get("expiry_selection_mode")
        )
    return {k: v for k, v in out.items() if v is not None and v != ""}


def merge_expiry_trade_fields(
    target: Dict[str, Any],
    *,
    selected_expiry: Any = None,
    requested_expiry: Any = None,
    session_date: Any = None,
    expiry_selection_mode: Optional[str] = None,
    fallback_reason: Optional[str] = None,
    fallback_days: Optional[int] = None,
    selected_type: Optional[str] = None,
    prefer_existing: bool = True,
) -> Dict[str, Any]:
    """Merge computed expiry fields into ``target`` (mutates and returns it)."""
    existing = extract_expiry_fields_from_mapping(target) if prefer_existing else {}
    selected = (
        selected_expiry
        or existing.get("selected_expiry_ymd")
        or existing.get("expiry_ymd")
        or target.get("expiration_ymd")
        or target.get("expiry")
        or target.get("expiration")
    )
    requested = (
        requested_expiry
        or existing.get("requested_expiry_ymd")
        or target.get("requested_expiry")
        or session_date
        or existing.get("session_date")
    )
    session = session_date or existing.get("session_date") or us_equity_session_date_iso()
    fields = build_expiry_trade_fields(
        selected_expiry=selected,
        requested_expiry=requested,
        session_date=session,
        expiry_selection_mode=expiry_selection_mode or existing.get("expiry_selection_mode"),
        fallback_reason=fallback_reason if fallback_reason is not None else existing.get("fallback_reason"),
        fallback_days=fallback_days if fallback_days is not None else existing.get("fallback_days"),
        selected_type=selected_type or target.get("selected_type"),
    )
    # Prefer explicit existing selection metadata when re-merging with incomplete kwargs.
    if prefer_existing:
        for k in (
            "expiry_selection_mode",
            "fallback_reason",
            "fallback_days",
            "requested_expiry_ymd",
            "selected_expiry_ymd",
            "session_date",
        ):
            if k in existing and existing.get(k) not in (None, ""):
                if k == "fallback_days" or fields.get(k) in (None, "", EXPIRY_MODE_UNKNOWN):
                    fields[k] = existing[k]
                elif k in ("expiry_selection_mode", "fallback_reason") and not expiry_selection_mode:
                    fields[k] = existing[k]
        # Recompute calendar truth from final selected/session.
        fields.update(
            build_expiry_trade_fields(
                selected_expiry=fields.get("selected_expiry_ymd") or selected,
                requested_expiry=fields.get("requested_expiry_ymd") or requested,
                session_date=fields.get("session_date") or session,
                expiry_selection_mode=str(fields.get("expiry_selection_mode") or ""),
                fallback_reason=str(fields.get("fallback_reason") or ""),
                fallback_days=fields.get("fallback_days"),
            )
        )
    target.update(fields)
    return target


def expiry_fields_from_chain(chain: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Build trade fields from an options-chain manager dict."""
    if not isinstance(chain, dict):
        return {}
    return build_expiry_trade_fields(
        selected_expiry=chain.get("selected_expiry") or chain.get("expiry") or chain.get("selected_expiry_ymd"),
        requested_expiry=chain.get("requested_expiry") or chain.get("requested_expiry_ymd"),
        session_date=chain.get("session_date"),
        expiry_selection_mode=chain.get("expiry_selection_mode"),
        fallback_reason=chain.get("fallback_reason"),
        fallback_days=chain.get("fallback_days"),
        selected_type=chain.get("selected_type"),
    )


def resolve_expiry_selection_from_context(
    *,
    contract: Any = None,
    trading_system: Any = None,
    chain_manager: Any = None,
) -> Dict[str, Any]:
    """Best-effort resolve of last chain expiry selection for ORB/Trendline fills."""
    sel = getattr(contract, "expiry_selection", None) if contract is not None else None
    if isinstance(sel, dict) and sel:
        return dict(sel)
    if isinstance(contract, dict) and isinstance(contract.get("expiry_selection"), dict):
        return dict(contract.get("expiry_selection") or {})
    mgr = chain_manager
    if mgr is None and trading_system is not None:
        dte0 = getattr(trading_system, "dte0_manager", None)
        mgr = getattr(dte0, "options_chain_manager", None) if dte0 else None
        if mgr is None:
            mgr = getattr(trading_system, "options_chain_manager", None)
    last = getattr(mgr, "last_expiry_selection", None) if mgr is not None else None
    if isinstance(last, dict) and last:
        return dict(last)
    return {}


def copy_expiry_fields(
    src: Optional[Mapping[str, Any]],
    dest: Dict[str, Any],
) -> Dict[str, Any]:
    """Copy EXPIRY_TRADE_FIELD_KEYS from src into dest when present."""
    extracted = extract_expiry_fields_from_mapping(src)
    if not extracted:
        return dest
    # Ensure calendar truth if we have enough dates.
    if extracted.get("selected_expiry_ymd") or extracted.get("expiry_ymd"):
        merge_expiry_trade_fields(
            dest,
            selected_expiry=extracted.get("selected_expiry_ymd") or extracted.get("expiry_ymd"),
            requested_expiry=extracted.get("requested_expiry_ymd"),
            session_date=extracted.get("session_date"),
            expiry_selection_mode=extracted.get("expiry_selection_mode"),
            fallback_reason=extracted.get("fallback_reason"),
            fallback_days=extracted.get("fallback_days"),
            prefer_existing=False,
        )
    else:
        dest.update(extracted)
    return dest
