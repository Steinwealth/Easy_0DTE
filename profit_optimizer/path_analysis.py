#!/usr/bin/env python3
"""Premium path analytics for stop-loss and trailing-stop research (read-only)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

MFE_BYPASS = 0.05
MFE_VELOCITY_THRESHOLDS = (0.05, 0.10, 0.20, 0.30)
MIN_LIVE_TICKS = 5
MIN_POST_EXIT_SHADOW_QUOTES = 3


def _f(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _pnl_ratio(mark: float, entry: float) -> float:
    return (mark - entry) / entry


def _pts(ratio: Optional[float]) -> Optional[float]:
    """Premium P&L in percentage-point display (+965 means ratio 9.65)."""
    if ratio is None:
        return None
    return ratio * 100.0


def resolve_entry_premium(trade: Dict[str, Any]) -> Optional[float]:
    ep = _f(trade.get("entry_premium"))
    if ep and ep > 0:
        return ep
    entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
    ep = _f(entry.get("entry_premium"))
    if ep and ep > 0:
        return ep
    pm = trade.get("premium_metrics_at_exit") if isinstance(trade.get("premium_metrics_at_exit"), dict) else {}
    return _f(pm.get("entry_premium"))


def resolve_delta(trade: Dict[str, Any]) -> Optional[float]:
    entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
    selector = entry.get("selector_metadata") if isinstance(entry.get("selector_metadata"), dict) else {}
    extra = entry.get("extra") if isinstance(entry.get("extra"), dict) else {}
    for key in ("fill_delta", "delta", "target_delta"):
        v = _f(entry.get(key)) or _f(selector.get(key)) or _f(extra.get(key))
        if v is not None and v > 0:
            return abs(v)
    return None


def resolve_target_delta(trade: Dict[str, Any]) -> Optional[float]:
    entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
    selector = entry.get("selector_metadata") if isinstance(entry.get("selector_metadata"), dict) else {}
    extra = entry.get("extra") if isinstance(entry.get("extra"), dict) else {}
    for key in ("target_delta",):
        v = _f(selector.get(key)) or _f(extra.get(key)) or _f(entry.get(key))
        if v is not None and v > 0:
            return abs(v)
    return None


def _row_mark(row: Dict[str, Any]) -> Optional[float]:
    # Prefer STC mark/bid over mid for path analysis parity with true-to-live exits.
    return _f(row.get("mark")) or _f(row.get("bid")) or _f(row.get("mid"))


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def resolve_entry_time(trade: Dict[str, Any]) -> Optional[datetime]:
    entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
    for key in ("entry_time",):
        ts = _parse_timestamp(entry.get(key) or trade.get(key))
        if ts is not None:
            return ts
    return None


def path_marks(trade: Dict[str, Any], *, phase: str = "live") -> List[float]:
    return [mark for _, mark in path_timed_rows(trade, phase=phase)]


def path_timed_rows(trade: Dict[str, Any], *, phase: str = "live") -> List[Tuple[datetime, float]]:
    path = trade.get("premium_path") if isinstance(trade.get("premium_path"), list) else []
    rows: List[Tuple[datetime, float]] = []
    for row in path:
        if not isinstance(row, dict):
            continue
        row_phase = str(row.get("path_phase") or "live")
        if phase == "live":
            if row_phase == "shadow":
                continue
        elif phase == "shadow":
            if row_phase != "shadow":
                continue
        if str(row.get("sample_type") or "") == "gap":
            continue
        mark = _row_mark(row)
        if mark is None or mark <= 0:
            continue
        ts = _parse_timestamp(row.get("timestamp"))
        if ts is None:
            # Mark-only rows (tests / sparse exports) still count for tick peaks.
            ts = datetime(1970, 1, 1, tzinfo=timezone.utc)
            if rows:
                ts = rows[-1][0] + timedelta(seconds=1)
        rows.append((ts, mark))
    return rows


def _path_rows_after_exit(trade: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
    """Shadow rows after exit_time; returns (quoted_rows, gap_count)."""
    path = trade.get("premium_path") if isinstance(trade.get("premium_path"), list) else []
    exit_obs = trade.get("exit_observation") if isinstance(trade.get("exit_observation"), dict) else {}
    exit_time = str(exit_obs.get("exit_time") or trade.get("exit_time") or "")
    quoted: List[Dict[str, Any]] = []
    gaps = 0
    for row in path:
        if not isinstance(row, dict):
            continue
        if str(row.get("path_phase") or "") != "shadow":
            continue
        ts = str(row.get("timestamp") or "")
        if exit_time and ts and ts <= exit_time:
            continue
        if str(row.get("sample_type") or "") == "gap":
            gaps += 1
            continue
        if _row_mark(row) is not None:
            quoted.append(row)
    return quoted, gaps


def early_life_metrics(
    timed_rows: List[Tuple[datetime, float]],
    baseline: float,
    entry_time: Optional[datetime] = None,
    *,
    mfe_bypass: float = MFE_BYPASS,
    velocity_thresholds: Tuple[float, ...] = MFE_VELOCITY_THRESHOLDS,
) -> Dict[str, Any]:
    """
    Early-life velocity: time to +5% MFE bypass, MFE ladder (+10/+20/+30%), and worst dip before +5%.
    """
    if baseline <= 0 or not timed_rows:
        return {}
    origin = entry_time or timed_rows[0][0]
    pre5_mae = 0.0
    time_to_5pct: Optional[float] = None
    velocity: Dict[str, Optional[float]] = {}
    pending = list(velocity_thresholds)
    for ts, mark in timed_rows:
        pnl = _pnl_ratio(mark, baseline)
        elapsed = max(0.0, (ts - origin).total_seconds())
        while pending and pnl >= pending[0]:
            threshold = pending.pop(0)
            pct = int(round(threshold * 100))
            velocity[f"time_to_{pct}pct_mfe_sec"] = elapsed
        if time_to_5pct is None:
            pre5_mae = min(pre5_mae, pnl)
            if pnl >= mfe_bypass:
                time_to_5pct = elapsed
    mfe_hit = time_to_5pct is not None
    if not mfe_hit:
        pre5_mae = min((_pnl_ratio(mark, baseline) for _, mark in timed_rows), default=0.0)
    for threshold in pending:
        pct = int(round(threshold * 100))
        velocity[f"time_to_{pct}pct_mfe_sec"] = None
    out: Dict[str, Any] = {
        "mae_before_5pct_mfe_pct": pre5_mae,
        "time_to_5pct_mfe_sec": time_to_5pct,
        "mfe_5pct_bypass_hit": mfe_hit,
    }
    out.update(velocity)
    return out


def path_metrics(
    marks: List[float],
    baseline: float,
    *,
    mfe_bypass: float = MFE_BYPASS,
) -> Dict[str, Any]:
    if baseline <= 0 or not marks:
        return {}
    pnls = [_pnl_ratio(m, baseline) for m in marks]
    session_mae = min(pnls)
    session_mfe = max(pnls)
    pre_mfe_mae = 0.0
    mfe_hit = False
    for p in pnls:
        if not mfe_hit:
            if p >= mfe_bypass:
                mfe_hit = True
            else:
                pre_mfe_mae = min(pre_mfe_mae, p)
    if not mfe_hit:
        pre_mfe_mae = session_mae
    peak_idx = pnls.index(session_mfe)
    mae_before_peak = min(pnls[:peak_idx]) if peak_idx > 0 else 0.0
    return {
        "session_mae_pct": session_mae,
        "session_mfe_pct": session_mfe,
        "pre_mfe_mae_pct": pre_mfe_mae,
        "mae_before_peak_pct": mae_before_peak,
        "mfe_bypass_hit": mfe_hit,
    }


def compute_session_extension_metrics(trade: Dict[str, Any]) -> Dict[str, Any]:
    """
    Post-exit and full-session opportunity metrics (shadow path through EOD).

    Answers: if we held longer after stealth exit, how much more was available that day?
    """
    ep = resolve_entry_premium(trade)
    if not ep or ep <= 0:
        return {}

    exit_obs = trade.get("exit_observation") if isinstance(trade.get("exit_observation"), dict) else {}
    pm_exit = (
        trade.get("premium_metrics_at_exit")
        if isinstance(trade.get("premium_metrics_at_exit"), dict)
        else {}
    )
    pm = trade.get("premium_metrics") if isinstance(trade.get("premium_metrics"), dict) else {}
    pa = trade.get("path_audit") if isinstance(trade.get("path_audit"), dict) else {}

    exit_prem = _f(exit_obs.get("exit_premium")) or _f(trade.get("exit_premium"))
    exit_ratio = _f(exit_obs.get("realized_pnl_pct"))
    if exit_ratio is None and exit_prem is not None:
        exit_ratio = _pnl_ratio(exit_prem, ep)

    peak_while_open = _f(pm_exit.get("premium_mfe_pct"))
    if peak_while_open is None:
        live = path_marks(trade, phase="live")
        if live:
            peak_while_open = max(_pnl_ratio(m, ep) for m in live)

    post_rows, post_gaps = _path_rows_after_exit(trade)
    post_ratios: List[float] = []
    for row in post_rows:
        mark = _row_mark(row)
        if mark is not None and mark > 0:
            post_ratios.append(_pnl_ratio(mark, ep))

    post_exit_peak_ratio: Optional[float] = max(post_ratios) if post_ratios else None
    eod_prem = _f(pm.get("premium_at_eod"))
    post_exit_eod_ratio: Optional[float] = _pnl_ratio(eod_prem, ep) if eod_prem is not None else None

    full_session_peak_ratio = _f(pm.get("premium_mfe_pct"))
    if full_session_peak_ratio is None:
        candidates = [r for r in (peak_while_open, post_exit_peak_ratio) if r is not None]
        full_session_peak_ratio = max(candidates) if candidates else None
    elif peak_while_open is not None:
        full_session_peak_ratio = max(full_session_peak_ratio, peak_while_open)
        if post_exit_peak_ratio is not None:
            full_session_peak_ratio = max(full_session_peak_ratio, post_exit_peak_ratio)

    shadow_trusted = pa.get("shadow_path_trusted") is not False
    post_exit_shadow_trusted = bool(
        shadow_trusted and len(post_ratios) >= MIN_POST_EXIT_SHADOW_QUOTES
    )

    while_open_upside_pts: Optional[float] = None
    if peak_while_open is not None and exit_ratio is not None and peak_while_open > exit_ratio:
        while_open_upside_pts = _pts(peak_while_open - exit_ratio)

    post_exit_upside_pts: Optional[float] = None
    if post_exit_peak_ratio is not None and exit_ratio is not None and post_exit_peak_ratio > exit_ratio:
        post_exit_upside_pts = _pts(post_exit_peak_ratio - exit_ratio)

    full_session_upside_pts: Optional[float] = None
    if full_session_peak_ratio is not None and exit_ratio is not None and full_session_peak_ratio > exit_ratio:
        full_session_upside_pts = _pts(full_session_peak_ratio - exit_ratio)

    post_exit_continued_higher = bool(
        post_exit_peak_ratio is not None
        and exit_ratio is not None
        and post_exit_peak_ratio > exit_ratio + 1e-9
    )
    post_exit_exceeded_open_peak = bool(
        post_exit_peak_ratio is not None
        and peak_while_open is not None
        and post_exit_peak_ratio > peak_while_open + 1e-9
    )

    capture_vs_full_session: Optional[float] = None
    if full_session_peak_ratio and full_session_peak_ratio > 0 and exit_ratio is not None:
        capture_vs_full_session = exit_ratio / full_session_peak_ratio

    return {
        "peak_while_open_pct": peak_while_open,
        "post_exit_peak_pct": post_exit_peak_ratio,
        "post_exit_eod_pct": post_exit_eod_ratio,
        "full_session_peak_pct": full_session_peak_ratio,
        "while_open_upside_pts": while_open_upside_pts,
        "post_exit_upside_pts": post_exit_upside_pts,
        "full_session_upside_pts": full_session_upside_pts,
        "post_exit_left_on_table_pct": (
            (post_exit_peak_ratio - exit_ratio) / post_exit_peak_ratio
            if post_exit_peak_ratio and exit_ratio is not None and post_exit_peak_ratio > exit_ratio
            else None
        ),
        "post_exit_continued_higher": post_exit_continued_higher,
        "post_exit_exceeded_open_peak": post_exit_exceeded_open_peak,
        "post_exit_shadow_quote_count": len(post_ratios),
        "post_exit_shadow_gap_count": post_gaps,
        "post_exit_shadow_trusted": post_exit_shadow_trusted,
        "capture_vs_full_session_peak": capture_vs_full_session,
    }


def post_exit_metrics(trade: Dict[str, Any], *, entry_premium: Optional[float] = None) -> Dict[str, Any]:
    """Backward-compatible wrapper."""
    if entry_premium is not None:
        patched = dict(trade)
        entry = dict(patched.get("entry") or {})
        entry["entry_premium"] = entry_premium
        patched["entry"] = entry
        return compute_session_extension_metrics(patched)
    return compute_session_extension_metrics(trade)


def analyze_trade_path(trade: Dict[str, Any]) -> Dict[str, Any]:
    """Compute drawdown + capture path fields for cohort export and guides."""
    ep = resolve_entry_premium(trade)
    live = path_marks(trade, phase="live")
    pm_exit = (
        trade.get("premium_metrics_at_exit")
        if isinstance(trade.get("premium_metrics_at_exit"), dict)
        else {}
    )
    pm = trade.get("premium_metrics") if isinstance(trade.get("premium_metrics"), dict) else {}
    exit_obs = trade.get("exit_observation") if isinstance(trade.get("exit_observation"), dict) else {}

    exit_pnl = _f(exit_obs.get("realized_pnl_pct"))
    if exit_pnl is None:
        exit_prem = _f(exit_obs.get("exit_premium")) or _f(trade.get("exit_premium"))
        if exit_prem and ep:
            exit_pnl = _pnl_ratio(exit_prem, ep)

    row: Dict[str, Any] = {
        "delta": resolve_delta(trade),
        "target_delta": resolve_target_delta(trade),
        "live_tick_count": len(live),
        "mfe_pct_at_exit": _f(pm_exit.get("premium_mfe_pct")),
        "mae_pct_at_exit": _f(pm_exit.get("premium_mae_pct")),
        "realized_pnl_pct": exit_pnl,
        "capture_vs_session_peak_at_exit": _f(pm.get("capture_vs_session_peak_at_exit")),
        "left_on_table_at_exit_pct": _f(pm.get("left_on_table_at_exit_pct")),
        "path_quality_sparse": len(live) < MIN_LIVE_TICKS,
    }

    timed_live = path_timed_rows(trade, phase="live")
    if ep and live:
        entry_metrics = path_metrics(live, ep)
        row["pre_mfe_mae_pct"] = entry_metrics.get("pre_mfe_mae_pct")
        row["mae_before_peak_pct"] = entry_metrics.get("mae_before_peak_pct")
        first_mark = live[0]
        row["first_mark_baseline"] = first_mark
        path_metrics_row = path_metrics(live, first_mark)
        row["path_pre_mfe_mae_pct"] = path_metrics_row.get("pre_mfe_mae_pct")
        row.update(early_life_metrics(timed_live, ep, resolve_entry_time(trade)))
        pm_exit_time = _f(pm_exit.get("time_to_mfe_seconds"))
        if pm_exit_time is not None:
            row["time_to_session_peak_sec"] = pm_exit_time

    ext = compute_session_extension_metrics(trade)
    row["shadow_tick_count"] = ext.get("post_exit_shadow_quote_count")
    row.update(ext)
    return row
