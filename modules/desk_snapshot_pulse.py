"""In-memory desk snapshot + tiny head — same moment as Telegram, not the 33MB ledger.

Cloud Run already has the live book in MockOptionsExecutor. This writes
``desk_public/snapshot.json`` (~desk-sized, from memory) and ``head.json``
(~200 bytes) on a background thread so hosted web.app does not wait on the
Mac sidecar pull. Fill/close write immediately. While the book is open,
a 1.0s heartbeat republishes all live Cur% / $ from memory — off the
monitor loop.

PnL/tape rows reuse ``desk_action_pulse.compact_action`` so the hosted tape
matches Telegram / executor fills.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from modules.equity_curve_book import curve_is_seed_only, rebase_without_seed_adjusts

log = logging.getLogger(__name__)

PT = ZoneInfo("America/Los_Angeles")
CACHE_CONTROL = "public, max-age=0, no-cache"
VERSION = "2.47.0"
SCHEMA = 2
SESSION_OPEN = (6, 30, 0)
SESSION_CLOSE = (12, 55, 0)  # Telegram EOD 12:55 PT, not NYSE 13:00
SESSION_ORB1_CAPTURE = (6, 45, 0)
SESSION_ORB2_CAPTURE = (7, 30, 0)
SESSION_OPEN_DECO = "☁️ ☁️ ☁️ ☁️ ☁️ 🌤️ ☁️ ☁️ 🕊️ ☁️ ☁️"
SESSION_OPEN_TAIL = "🌻 🛎️ ✍️ ⏰ 🎾 📰 🗞️"
SESSION_CLOSE_DECO = "✨ ✨ ✨ ✨ ☁️ ✨ 🌖 ✨ ☁️ ✨ ✨"
SESSION_CLOSE_TAIL = "🌊 🌊 🌊 🏖️ 🌊 🏝️ ⛱️"

PATH_UI = {
    "TL": ("📏", "Trendline"),
    "ORB1": ("🔮", "ORB1"),
    "ORB2": ("⛓️", "Breakout"),
}

_LOCK = threading.Lock()
_WORKER: Optional[threading.Thread] = None
_HEARTBEAT: Optional[threading.Thread] = None
_PENDING: Optional[Dict[str, Any]] = None
_EXECUTOR: Any = None
_SEQ = 0
_LAST_OPEN_BOOK_SIG = ""
_DEFAULT_MARK_INTERVAL_SEC = 1.0


def _mark_interval_sec() -> float:
    raw = os.getenv("DESK_SNAPSHOT_MARK_INTERVAL_SEC", str(_DEFAULT_MARK_INTERVAL_SEC))
    try:
        return max(0.5, float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_MARK_INTERVAL_SEC


def desk_snapshot_gcs_path() -> str:
    try:
        from modules.runtime_mode import gcs_namespace

        ns = gcs_namespace()
    except Exception:
        ns = "research_app"
    return f"{ns}/desk_public/snapshot.json"


def desk_head_gcs_path() -> str:
    try:
        from modules.runtime_mode import gcs_namespace

        ns = gcs_namespace()
    except Exception:
        ns = "research_app"
    return f"{ns}/desk_public/head.json"


def _num(raw: Any) -> Optional[float]:
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # noqa: PLR0124


def _frac_to_display_pct(frac: Optional[float], cur_pct: float) -> float:
    if frac is None:
        return max(cur_pct, 0.0)
    return max(round(float(frac) * 1000.0) / 10.0, cur_pct)


def _first_frac(*raws: Any) -> Optional[float]:
    for raw in raws:
        n = _num(raw)
        if n is not None:
            return n
    return None


def _executable_bid_mfe_display_pct(position: Any, cur_pct: float = 0.0) -> float:
    """PATH_V2 / executable-bid peak as display percent. Never a chain mark."""
    del cur_pct
    md = getattr(position, "metadata", None)
    if not isinstance(md, dict):
        md = {}
    snap = md.get("premium_snapshot") if isinstance(md.get("premium_snapshot"), dict) else {}
    stealth = md.get("option_stealth") if isinstance(md.get("option_stealth"), dict) else {}
    pv = stealth.get("path_v2") if isinstance(stealth.get("path_v2"), dict) else {}
    snap_pv = snap.get("path_v2") if isinstance(snap.get("path_v2"), dict) else {}
    md_pv = md.get("path_v2") if isinstance(md.get("path_v2"), dict) else {}
    frac = _first_frac(
        stealth.get("path_v2_peak"),
        md.get("path_v2_peak"),
        snap.get("path_v2_peak"),
        pv.get("peak"),
        snap_pv.get("peak"),
        md_pv.get("peak"),
        md.get("bid_mfe_pct"),
        stealth.get("bid_mfe_pct"),
    )
    if frac is None:
        return 0.0
    return round(float(frac) * 1000.0) / 10.0


def _mark_mfe_display_pct(position: Any, deployed: float, cur_pct: float) -> float:
    """Mark / tracker HWM as display percent. Separate from executable-bid MFE."""
    md = getattr(position, "metadata", None)
    if not isinstance(md, dict):
        md = {}
    snap = md.get("premium_snapshot") if isinstance(md.get("premium_snapshot"), dict) else {}
    stealth = md.get("option_stealth") if isinstance(md.get("option_stealth"), dict) else {}
    frac = _first_frac(
        md.get("mark_mfe_pct"),
        snap.get("mfe_pct"),
        snap.get("max_pnl_pct"),
        stealth.get("max_pnl_pct"),
        md.get("mfe_pct"),
        getattr(position, "mfe_pct", None),
    )
    if frac is not None:
        return _frac_to_display_pct(frac, cur_pct)
    dollars = _num(getattr(position, "max_favorable", None))
    if deployed > 0 and dollars is not None and dollars > 0:
        return round((dollars / deployed) * 1000.0) / 10.0
    entry = _num(getattr(position, "entry_price", None)) or 0.0
    hwm = _num(snap.get("premium_hwm")) or _num(stealth.get("premium_hwm"))
    if entry > 0 and hwm is not None and hwm > entry:
        return _frac_to_display_pct((hwm / entry) - 1.0, cur_pct)
    return max(cur_pct, 0.0)


def _peak_mfe_display_pct(position: Any, deployed: float, cur_pct: float) -> float:
    """Headline desk MFE: executable bid peak, not mark HWM."""
    return _executable_bid_mfe_display_pct(position, cur_pct)


def _parse_ts(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _ms(raw: Any) -> int:
    dt = _parse_ts(raw)
    if dt is None:
        return 0
    return int(dt.timestamp() * 1000)


def _fmt_time(raw: Any) -> str:
    dt = _parse_ts(raw)
    if dt is None:
        return "—"
    return dt.astimezone(PT).strftime("%I:%M:%S %p")


def _fmt_clock(now: Optional[datetime] = None) -> str:
    dt = (now or datetime.now(timezone.utc)).astimezone(PT)
    return dt.strftime("%a, %b ") + str(dt.day) + dt.strftime(", %I:%M:%S %p")


def _money(amount: float, signed: bool = False) -> str:
    n = float(amount or 0.0)
    core = f"${abs(n):,.2f}"
    if not signed:
        return core
    if n > 0:
        return f"+{core}"
    if n < 0:
        return f"-{core}"
    return core


def _signal_money(amount: float, signed: bool = False) -> str:
    """Signal panel: `$-303.00` / `+$12.00` (signed) or `$590.00`."""
    n = float(amount or 0.0)
    abs_core = f"{abs(n):,.2f}"
    if not signed:
        return f"${abs_core}"
    if n > 0:
        return f"+${abs_core}"
    if n < 0:
        return f"$-{abs_core}"
    return f"${abs_core}"


def _pct_label(pct: Optional[float]) -> Optional[str]:
    if pct is None:
        return None
    abs_n = abs(float(pct))
    formatted = f"{abs_n:,.2f}%"
    if pct > 0:
        return f"+{formatted}"
    if pct < 0:
        return f"-{formatted}"
    return "0.00%"


def _qty_label(qty: Optional[float]) -> str:
    if qty is None:
        return "0"
    if float(qty) == int(qty):
        return str(int(qty))
    return str(qty)


def _ymd_pt(raw: Any) -> Optional[str]:
    dt = _parse_ts(raw)
    if dt is None:
        return None
    return dt.astimezone(PT).date().isoformat()


def _tape_identity(side: Any, trade_id: Any) -> str:
    bits = []
    label = str(side or "").upper()
    if label in ("CALL", "PUT"):
        bits.append(label)
    shown = _display_trade_id(trade_id)
    if shown and shown != "—":
        bits.append(shown)
    return " · ".join(bits)


def _display_trade_id(tid: Any) -> str:
    s = str(tid or "").strip() or "—"
    if len(s) >= 2 and s[1] == "-" and s[0] in "DLdl":
        return s[2:] or "—"
    return s


def _pt_wall(ymd: str, h: int, m: int, s: int = 0) -> datetime:
    y, mo, d = [int(x) for x in ymd.split("-")]
    return datetime(y, mo, d, h, m, s, tzinfo=PT)


def _is_session_bell_ymd(ymd: str) -> bool:
    """Same gate as Good Morning / capture: weekdays that `should_skip_trading` does not skip."""
    try:
        y, m, d = (int(x) for x in str(ymd).split("-")[:3])
        dt = date(y, m, d)
    except (TypeError, ValueError):
        return False
    if dt.weekday() >= 5:
        return False
    try:
        from modules.dynamic_holiday_calculator import should_skip_trading

        skip, _, _ = should_skip_trading(dt)
    except Exception:
        skip = False
    return not skip


def _real_capture_stat(node: Any) -> Optional[tuple]:
    if not isinstance(node, dict):
        return None
    raw = node.get("captured_count")
    if raw is None or isinstance(raw, bool):
        flag = node.get("captured")
        raw = None if isinstance(flag, bool) else flag
    try:
        captured = int(raw)
    except (TypeError, ValueError):
        return None
    if captured <= 0:
        return None
    duration = None
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    for raw_dur in (node.get("duration_sec"), meta.get("capture_seconds"), node.get("capture_seconds")):
        if raw_dur is None:
            continue
        try:
            duration = float(raw_dur)
        except (TypeError, ValueError):
            duration = None
            continue
        if duration is not None and duration <= 0:
            duration = None
            continue
        break
    return captured, duration


def _is_ymd(s: str) -> bool:
    return bool(s) and len(s) == 10 and s[4] == "-" and s[7] == "-" and s.replace("-", "").isdigit()


def _desk_capture_from_marker(marker: Optional[Dict[str, Any]], ymd: Optional[str]) -> Optional[Dict[str, Any]]:
    """Daily-marker ``orb`` / ``orb2`` → desk ``{ymd, orb1, orb2}`` with integer counts."""
    if not isinstance(marker, dict):
        return None
    tagged = str(marker.get("ymd") or marker.get("date") or ymd or "").strip()[:10]
    if not _is_ymd(tagged):
        return None
    orb_block = marker.get("orb") if isinstance(marker.get("orb"), dict) else None
    orb1_block = marker.get("orb1") if isinstance(marker.get("orb1"), dict) else None
    orb1 = _real_capture_stat(orb_block or orb1_block)
    orb2 = _real_capture_stat(marker.get("orb2") if isinstance(marker.get("orb2"), dict) else None)
    if not orb1 and not orb2:
        return None
    out: Dict[str, Any] = {"ymd": tagged}
    if orb1:
        out["orb1"] = {"captured": orb1[0], "duration_sec": orb1[1]}
    if orb2:
        out["orb2"] = {"captured": orb2[0], "duration_sec": orb2[1]}
    return out


def _load_daily_desk_capture(ymd: Optional[str]) -> Optional[Dict[str, Any]]:
    try:
        from modules.daily_run_tracker import get_daily_run_tracker

        marker = get_daily_run_tracker().get_today_state()
        try:
            today = datetime.now(PT).date().isoformat()
        except Exception:
            today = date.today().isoformat()
        marker_day = str(marker.get("ymd") or marker.get("date") or today or "").strip()[:10]
        if ymd and marker_day and marker_day != ymd:
            return None
        return _desk_capture_from_marker(marker, ymd or today)
    except Exception:
        return None


def _capture_for_ymd(capture: Optional[Dict[str, Any]], ymd: str, extra_ymd: Optional[str]) -> Dict[str, Any]:
    cap = capture if isinstance(capture, dict) else {}
    tagged = str(cap.get("ymd") or cap.get("date") or "").strip()[:10]
    if not tagged:
        orb1 = cap.get("orb1") if isinstance(cap.get("orb1"), dict) else {}
        orb2 = cap.get("orb2") if isinstance(cap.get("orb2"), dict) else {}
        tagged = str(orb1.get("ymd") or orb2.get("ymd") or "").strip()[:10]
    if tagged != ymd:
        return {"orb1": None, "orb2": None}
    return {
        "orb1": _real_capture_stat(cap.get("orb1")),
        "orb2": _real_capture_stat(cap.get("orb2")),
    }


def _orb_capture_body(captured: int, duration_sec: Optional[float]) -> str:
    dur = ""
    if duration_sec and float(duration_sec) > 0:
        dur = f" [Duration: {float(duration_sec):.1f} seconds]"
    return (
        "✅ Status: All opening ranges captured successfully!<br>"
        f"[Captured: {int(captured)}]{dur} ⚛️"
    )


def _session_rows(
    ymds: List[str],
    now: Optional[datetime] = None,
    *,
    capture: Optional[Dict[str, Any]] = None,
    extra_ymd: Optional[str] = None,
) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    rows: List[Dict[str, Any]] = []
    seen = set()
    for ymd in ymds:
        if not ymd or ymd in seen:
            continue
        seen.add(ymd)
        if not _is_session_bell_ymd(ymd):
            continue
        day_cap = _capture_for_ymd(capture, ymd, extra_ymd)
        day = _pt_wall(ymd, 12, 0, 0)
        day_label = day.strftime("%a, %b ") + str(day.day)
        open_dt = _pt_wall(ymd, *SESSION_OPEN)
        orb1_dt = _pt_wall(ymd, *SESSION_ORB1_CAPTURE)
        orb2_dt = _pt_wall(ymd, *SESSION_ORB2_CAPTURE)
        close_dt = _pt_wall(ymd, *SESSION_CLOSE)
        if open_dt <= now:
            rows.append(
                {
                    "id": f"session-open-{ymd}",
                    "sort": int(open_dt.timestamp() * 1000),
                    "ts": open_dt.strftime("%I:%M:%S %p"),
                    "kind": "session-open",
                    "header": f"Market open {SESSION_OPEN_DECO}",
                    "body": f"RTH 06:30 PT · {day_label} {SESSION_OPEN_TAIL}",
                    "pnl": None,
                    "pnl_pct": None,
                    "trade_id": None,
                    "flash": False,
                    "source": "session.clock",
                }
            )
        orb1_stat = day_cap.get("orb1")
        if orb1_dt <= now and orb1_stat:
            rows.append(
                {
                    "id": f"session-orb1-capture-{ymd}",
                    "sort": int(orb1_dt.timestamp() * 1000),
                    "ts": orb1_dt.strftime("%I:%M:%S %p"),
                    "kind": "session-orb1-capture",
                    "header": "🔮 ORB Capture 1 · [15min] 9:30-9:45 AM ET",
                    "body": _orb_capture_body(*orb1_stat),
                    "pnl": None,
                    "pnl_pct": None,
                    "trade_id": None,
                    "flash": False,
                    "source": "session.clock",
                }
            )
        orb2_stat = day_cap.get("orb2")
        if orb2_dt <= now and orb2_stat:
            rows.append(
                {
                    "id": f"session-orb2-capture-{ymd}",
                    "sort": int(orb2_dt.timestamp() * 1000),
                    "ts": orb2_dt.strftime("%I:%M:%S %p"),
                    "kind": "session-orb2-capture",
                    "header": "🔮 ORB Capture 2 · [1hr] 9:30-10:30 AM ET",
                    "body": _orb_capture_body(*orb2_stat),
                    "pnl": None,
                    "pnl_pct": None,
                    "trade_id": None,
                    "flash": False,
                    "source": "session.clock",
                }
            )
        if close_dt <= now:
            rows.append(
                {
                    "id": f"session-close-{ymd}",
                    "sort": int(close_dt.timestamp() * 1000),
                    "ts": close_dt.strftime("%I:%M:%S %p"),
                    "kind": "session-close",
                    "header": f"Market close {SESSION_CLOSE_DECO}",
                    "body": f"EOD 12:55 PT · {day_label} {SESSION_CLOSE_TAIL}",
                    "pnl": None,
                    "pnl_pct": None,
                    "trade_id": None,
                    "flash": False,
                    "source": "session.clock",
                }
            )
    return rows


def _path_ui(path: str) -> tuple:
    return PATH_UI.get(path, ("", path or "—"))


def _position_expired_open(position: Any) -> bool:
    """Same rule as the open-position monitor: a past expiry is not a live open."""
    try:
        from easy0DTE.modules.mock_options_executor import _mock_position_expired_for_today

        return bool(_mock_position_expired_for_today(position))
    except Exception:
        return False


def _active_list(executor: Any) -> List[Any]:
    src = getattr(executor, "active_positions", None) or {}
    if isinstance(src, dict):
        rows = list(src.values())
    else:
        rows = list(src)
    return [p for p in rows if str(getattr(p, "status", "") or "").lower() != "closed"]


def _live_open_list(executor: Any) -> List[Any]:
    return [p for p in _active_list(executor) if not _position_expired_open(p)]


def _closed_list(executor: Any) -> List[Any]:
    return list(getattr(executor, "closed_positions", None) or [])


def _tape_entry(position: Any) -> Optional[Dict[str, Any]]:
    from modules.desk_action_pulse import compact_action, path_from_position

    row = compact_action("entry", position)
    if not row.get("id"):
        return None
    path = row.get("path") or path_from_position(position)
    emoji, label = _path_ui(path)
    qty = _num(getattr(position, "quantity", None)) or 0.0
    entry = _num(row.get("premium")) or 0.0
    deployed = round(entry * qty * 100.0, 2)
    ts_raw = row.get("ts") or getattr(position, "entry_time", None)
    closed = getattr(position, "closed_at", None) or getattr(position, "exit_time", None)
    ident = _tape_identity(row.get("side"), row.get("trade_id"))
    money = f"{_qty_label(qty)} × {_money(entry)} · {_money(deployed)}"
    return {
        "id": row["id"],
        "sort": _ms(ts_raw),
        "ts": _fmt_time(ts_raw),
        "kind": "entry",
        "header": f"{emoji} {label} Fill".strip(),
        "body": f"{ident} · {money}" if ident else money,
        "pnl": None,
        "pnl_pct": None,
        "trade_id": row.get("trade_id"),
        "symbol": row.get("symbol"),
        "path": path,
        "side": row.get("side"),
        "flash": False,
        "source": "ledger.closed_positions.entry_time" if closed else "ledger.active_positions.entry_time",
    }


def _tape_exit(position: Any) -> Optional[Dict[str, Any]]:
    from modules.desk_action_pulse import compact_action, path_from_position

    row = compact_action("exit", position, {"pnl": getattr(position, "realized_pnl", None)})
    if not row.get("id"):
        return None
    path = row.get("path") or path_from_position(position)
    emoji, label = _path_ui(path)
    pnl = _num(row.get("pnl")) or 0.0
    pct = row.get("pnl_pct")
    pct_label = _pct_label(pct if pct is not None else None)
    money_bit = _money(pnl, signed=True)
    money = f"{pct_label} · {money_bit}" if pct_label else money_bit
    ts_raw = row.get("ts") or getattr(position, "closed_at", None)
    ident = _tape_identity(row.get("side"), row.get("trade_id"))
    return {
        "id": row["id"],
        "sort": _ms(ts_raw),
        "ts": _fmt_time(ts_raw),
        "kind": "exit",
        "header": f"{emoji} {label} Closed".strip(),
        "body": f"{money} · {ident}" if ident else money,
        "pnl": pnl,
        "pnl_pct": pct,
        "trade_id": row.get("trade_id"),
        "symbol": row.get("symbol"),
        "side": row.get("side"),
        "flash": False,
        "source": "ledger.closed_positions",
    }


def _map_open(position: Any) -> Dict[str, Any]:
    from modules.desk_action_pulse import path_from_position, side_from_position

    path = path_from_position(position)
    emoji, _label = _path_ui(path)
    entry = _num(getattr(position, "entry_price", None)) or 0.0
    qty = _num(getattr(position, "quantity", None)) or 0.0
    deployed = round(entry * qty * 100.0, 2)
    mark = _num(getattr(position, "current_value", None)) or entry
    qty_i = int(qty) if qty else 0
    pt = str(getattr(position, "normalized_position_type", None) or getattr(position, "position_type", None) or "lotto")
    try:
        from modules.options_trade_economics import net_result_pnl_dollars, net_result_pnl_fraction

        unreal = net_result_pnl_dollars(
            entry_fill=entry,
            exit_fill=float(mark or entry),
            quantity=qty_i,
            is_credit_spread="credit" in pt.lower(),
            position_type=pt,
        ) if entry and qty_i else (_num(getattr(position, "unrealized_pnl", None)) or 0.0)
        frac = net_result_pnl_fraction(
            entry_fill=entry,
            exit_fill=float(mark or entry),
            quantity=qty_i,
            is_credit_spread="credit" in pt.lower(),
            position_type=pt,
        ) if entry and qty_i else None
        cur_pct = round(float(frac) * 1000.0) / 10.0 if frac is not None else 0.0
    except Exception:
        unreal = _num(getattr(position, "unrealized_pnl", None)) or 0.0
        cur_pct = round((unreal / deployed) * 1000.0) / 10.0 if deployed else 0.0
    return {
        "trade_id": getattr(position, "position_id", None) or "—",
        "path": path,
        "path_emoji": emoji,
        "symbol": getattr(position, "symbol", None) or "—",
        "side": side_from_position(position),
        "premium": entry,
        "mark": mark,
        "cur_pct": cur_pct,
        "mfe_pct": _executable_bid_mfe_display_pct(position, cur_pct),
        "bid_mfe_pct": _executable_bid_mfe_display_pct(position, cur_pct),
        "mark_mfe_pct": _mark_mfe_display_pct(position, deployed, cur_pct),
        "deployed": int(round(deployed)) if deployed else 0,
        "unrealized_pnl": unreal,
        "status": getattr(position, "status", None) or "open",
        "source": "executor.memory",
    }


def _signal(active: List[Any], closed: List[Any]) -> Optional[Dict[str, Any]]:
    from modules.desk_action_pulse import path_from_position, side_from_position

    def latest(rows: List[Any], attr: str) -> Optional[Any]:
        best = None
        best_ms = 0
        for row in rows:
            t = _ms(getattr(row, attr, None))
            if t > best_ms:
                best_ms = t
                best = row
        return best

    exit_row = latest(closed, "closed_at")
    entry_row = latest([*active, *closed], "entry_time")
    if not exit_row and not entry_row:
        return None
    out: Dict[str, Any] = {"exit": None, "entry": None}
    if exit_row:
        tape = _tape_exit(exit_row)
        path = path_from_position(exit_row)
        _, label = _path_ui(path)
        pnl = tape["pnl"] if tape else 0.0
        money = _signal_money(pnl, signed=True)
        out["exit"] = {
            "status": "EXIT",
            "headline": "Last ledger close",
            "money": money,
            "detail": f"Last ledger close · {getattr(exit_row, 'symbol', '')} · {money}",
            "path": path,
            "path_label": label,
            "symbol": getattr(exit_row, "symbol", None) or "—",
            "side": side_from_position(exit_row),
            "reason": "ledger_close",
            "ts": _fmt_time(getattr(exit_row, "closed_at", None)),
            "trade_id": getattr(exit_row, "position_id", None),
            "pnl": pnl,
            "pnl_pct": tape.get("pnl_pct") if tape else None,
            "source": "executor.memory",
        }
    if entry_row:
        entry = _num(getattr(entry_row, "entry_price", None)) or 0.0
        qty = _num(getattr(entry_row, "quantity", None)) or 0.0
        path = path_from_position(entry_row)
        _, label = _path_ui(path)
        qty_label = f"{_qty_label(qty)} × {_money(entry)}"
        out["entry"] = {
            "status": "ENTRY",
            "headline": "Last ledger fill",
            "money": qty_label,
            "qty_label": qty_label,
            "detail": (
                f"Last ledger fill · {getattr(entry_row, 'symbol', '')} · {qty_label}"
            ),
            "path": path,
            "path_label": label,
            "symbol": getattr(entry_row, "symbol", None) or "—",
            "side": side_from_position(entry_row),
            "reason": "ledger_entry",
            "ts": _fmt_time(getattr(entry_row, "entry_time", None)),
            "trade_id": getattr(entry_row, "position_id", None),
            "source": "executor.memory",
        }
    return out


def _today_outcome_stats(closed: List[Any], stats_date: Optional[str]) -> Dict[str, Any]:
    """Telegram EOD P&L (TODAY) fields, updated as each session close lands."""
    want = str(stats_date or "")[:10]
    pnls: List[float] = []
    for pos in closed or []:
        y = _ymd_pt(getattr(pos, "closed_at", None) or getattr(pos, "exit_time", None))
        if want and y != want:
            continue
        if not y:
            continue
        pnl = _num(getattr(pos, "realized_pnl", None))
        if pnl is None:
            continue
        pnls.append(float(pnl))
    if not pnls:
        return {
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "profit_factor": "0.00",
        }
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    win_sum = sum(wins)
    loss_abs = abs(sum(losses))
    avg_win = (win_sum / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    if loss_abs > 0:
        pf = f"{win_sum / loss_abs:.2f}"
    elif win_sum > 0:
        pf = "∞"
    else:
        pf = "0.00"
    return {
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
        "profit_factor": pf,
    }


def _lifetime_outcome_stats(closed: List[Any]) -> Dict[str, Any]:
    """Telegram EOD Account Balances (All Time) from the whole ledger book."""
    pnls: List[float] = []
    for pos in closed or []:
        pnl = _num(getattr(pos, "realized_pnl", None))
        if pnl is None:
            continue
        pnls.append(float(pnl))
    if not pnls:
        return {
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "profit_factor": "0.00",
            "lifetime_wins": 0,
            "lifetime_losses": 0,
            "lifetime_hit_rate": 0.0,
        }
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    win_sum = sum(wins)
    loss_abs = abs(sum(losses))
    avg_win = (win_sum / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    total = len(pnls)
    win_n = len(wins)
    loss_n = total - win_n
    if loss_abs > 0:
        pf = f"{win_sum / loss_abs:.2f}"
    elif win_sum > 0:
        pf = "∞"
    else:
        pf = "0.00"
    return {
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
        "profit_factor": pf,
        "lifetime_wins": win_n,
        "lifetime_losses": loss_n,
        "lifetime_hit_rate": round((win_n / total) * 1000.0) / 10.0 if total else 0.0,
    }


_CURVE_SKIP_KINDS = ("seed", "seed_adjust", "withdraw", "withdrawal", "deposit", "adjust")
_CURVE_EXIT_KINDS = ("exit", "close", "closed")


def _lifetime_from_curve(curve: Any, closed: List[Any]) -> Dict[str, Any]:
    """All Time stats from equity_curve exits since the latest seed (closed_positions keep 2 days)."""
    points = [p for p in (curve or []) if isinstance(p, dict)] if isinstance(curve, list) else []
    start = 0
    for i, p in enumerate(points):
        if str(p.get("kind") or "").strip().lower() == "seed":
            start = i
    pnls: List[float] = []
    prev_eq = _num(points[start].get("equity")) if points else None
    for p in points[start + 1 :]:
        kind = str(p.get("kind") or "exit").strip().lower()
        eq = _num(p.get("equity"))
        if kind in _CURVE_EXIT_KINDS:
            delta = _num(p.get("delta"))
            if delta is None and eq is not None and prev_eq is not None:
                delta = eq - prev_eq
            if delta is not None and round(delta, 2) != 0:
                pnls.append(round(delta, 2))
        if eq is not None:
            prev_eq = eq
    if not pnls:
        out = _lifetime_outcome_stats(closed)
        out.update(
            {
                "lifetime_trades": int(out["lifetime_wins"]) + int(out["lifetime_losses"]),
                "lifetime_source": "closed_positions",
            }
        )
        return out
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    win_sum = sum(wins)
    loss_abs = abs(sum(losses))
    if loss_abs > 0:
        pf = f"{win_sum / loss_abs:.2f}"
    elif win_sum > 0:
        pf = "∞"
    else:
        pf = "0.00"
    total = len(wins) + len(losses)
    return {
        "avg_win": round(win_sum / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
        "profit_factor": pf,
        "lifetime_wins": len(wins),
        "lifetime_losses": len(losses),
        "lifetime_hit_rate": round((len(wins) / total) * 1000.0) / 10.0 if total else 0.0,
        "lifetime_trades": total,
        "lifetime_source": "equity_curve",
        "lifetime_epoch_start": points[start].get("t"),
    }


def _cfg_source(key: str) -> Optional[Dict[str, str]]:
    if key in os.environ:
        return {"value": str(os.environ.get(key)), "from": "os.environ"}
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(key, None, convert_type=False)
    except Exception:
        raw = None
    return None if raw is None else {"value": str(raw), "from": "configs"}


def _desk_sizing(now: datetime) -> Optional[Dict[str, Any]]:
    """Runtime 0DTE sizing (modules/unified_0dte_account resolvers) for the desk cap."""
    try:
        from modules.config_loader import get_config_value
        from modules.unified_0dte_account import (
            unified_0dte_slot_count,
            unified_max_position_size_sum,
            unified_trading_capital_pct,
        )

        pct = min(100.0, max(0.0, float(unified_trading_capital_pct())))
        slots = max(1, int(unified_0dte_slot_count()))
        sum_cap = max(0.0, float(unified_max_position_size_sum()))
        sum_raw = str(get_config_value("0DTE_MAX_POSITION_SIZE_SUM", "0", convert_type=False))
    except Exception as err:
        log.debug("desk sizing unavailable | %s", err)
        return None
    slot_keys = (
        "0DTE_SLOT_COUNT",
        "TRENDLINE_SLOT_COUNT",
        "TOTAL_0DTE_MAX_POSITIONS",
        "UNIFIED_0DTE_MAX_TOTAL_POSITIONS",
        "MAX_TOTAL_OPTION_POSITIONS",
    )
    keys: Dict[str, Dict[str, str]] = {}
    for key in (*slot_keys, "0DTE_MAX_POSITION_SIZE_SUM", "0DTE_TRADING_CAPITAL_PCT"):
        hit = _cfg_source(key)
        if hit:
            keys[key] = hit
    slot_key = "fail_closed"
    for key in slot_keys:
        try:
            if key in keys and int(float(keys[key]["value"])) == slots:
                slot_key = key
                break
        except (TypeError, ValueError):
            continue
    service = os.environ.get("K_SERVICE") or None
    revision = os.environ.get("K_REVISION") or None
    stamp = now.astimezone(timezone.utc).isoformat()
    try:
        from modules.runtime_mode import is_demo_mode

        mode = "demo" if is_demo_mode() else "live"
    except Exception:
        mode = "demo"
    return {
        "kind": "research_sizing",
        "schema": 1,
        "app": "RESEARCH",
        "mode": mode,
        "service": service,
        "revision": revision,
        "fetched_at": stamp,
        "as_of": stamp,
        "source": f"Cloud Run {service or 'local'} os.environ ({revision or 'no revision'})",
        "sum_cap_raw": sum_raw,
        "sum_cap_usd": round(sum_cap, 2),
        "sum_cap_on": sum_cap > 0,
        "trading_capital_pct": pct,
        "buffer_pct": round(100.0 - pct, 2),
        "slot_count": slots,
        "slot_count_key": slot_key,
        "keys": keys,
    }


def apply_rejects(snapshot: Dict[str, Any], block: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Put today's rejects block on the snapshot (top level + metrics). None stays —."""
    if not isinstance(snapshot, dict) or not isinstance(block, dict):
        return snapshot
    snapshot["rejects"] = block
    metrics = snapshot.setdefault("metrics", {})
    metrics["rejects"] = block.get("count")
    metrics["rejects_source"] = block.get("source")
    metrics["rejects_breakdown"] = {
        "ymd": block.get("ymd"),
        "by_stage": block.get("by_stage"),
        "by_reason": block.get("by_reason"),
        "updated_at": block.get("as_of"),
        "as_of": block.get("as_of"),
    }
    return snapshot


def build_desk_snapshot(executor: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Desk snapshot from live executor memory. Same fills as Telegram."""
    now = now or datetime.now(timezone.utc)
    closed = _closed_list(executor)
    active = _active_list(executor)
    seed = _num(getattr(executor, "starting_balance", None)) or 0.0
    equity = _num(getattr(executor, "account_balance", None))
    if equity is None:
        try:
            equity = float(executor.account_equity())
        except Exception:
            equity = seed
    daily = getattr(executor, "daily_stats", None) if isinstance(getattr(executor, "daily_stats", None), dict) else {}
    weekly = getattr(executor, "weekly_stats", None) if isinstance(getattr(executor, "weekly_stats", None), dict) else {}
    stats_date = None
    try:
        stats_date = now.astimezone(PT).date().isoformat()
    except Exception:
        stats_date = now.date().isoformat()

    tape = []
    for pos in closed:
        exit_row = _tape_exit(pos)
        if exit_row:
            tape.append(exit_row)
        entry_row = _tape_entry(pos)
        if entry_row:
            tape.append(entry_row)
    for pos in active:
        entry_row = _tape_entry(pos)
        if entry_row:
            tape.append(entry_row)

    ymds = {stats_date} if stats_date else set()
    for pos in [*closed, *active]:
        for attr in ("entry_time", "closed_at", "exit_time"):
            y = _ymd_pt(getattr(pos, attr, None))
            if y:
                ymds.add(y)
    capture = None
    for attr in ("orb_capture", "desk_orb_capture"):
        capture = _desk_capture_from_marker(getattr(executor, attr, None), stats_date)
        if capture:
            break
    if not capture:
        capture = _load_daily_desk_capture(stats_date)
    tape.extend(_session_rows(sorted(ymds), now, capture=capture, extra_ymd=stats_date))
    tape.sort(key=lambda r: (-int(r.get("sort") or 0), str(r.get("id") or "")))

    live_open = _live_open_list(executor)
    positions = sorted((_map_open(p) for p in live_open), key=lambda p: float(p.get("cur_pct") or 0), reverse=True)
    deployed = round(sum(float(p.get("deployed") or 0) for p in positions), 2)
    unreal = round(sum(float(p.get("unrealized_pnl") or 0) for p in positions), 2)
    day_pnl = _num(daily.get("total_pnl")) or 0.0
    wins = int(daily.get("winning_trades") or 0)
    losses = int(daily.get("losing_trades") or 0)
    day_closed = int(daily.get("positions_closed") or 0)
    hit_rate = round((wins / day_closed) * 1000.0) / 10.0 if day_closed else 0.0

    curve = getattr(executor, "equity_curve", None)
    booked = _num(getattr(executor, "booked_equity", None))
    stored_peak = _num(getattr(executor, "peak_equity", None))
    withdraw_sum = round(float(getattr(executor, "withdrawals_total", 0.0) or 0.0), 2)
    used_curve = isinstance(curve, list) and curve and booked is not None
    if used_curve:
        repaired = rebase_without_seed_adjusts(float(seed or 0.0), curve)
        if repaired is not None:
            curve = repaired["equity_curve"]
            booked = float(repaired["booked_equity"])
            stored_peak = float(repaired["peak_equity"])
    if used_curve:
        history = []
        for raw in curve:
            if not isinstance(raw, dict):
                continue
            eq = _num(raw.get("equity"))
            t = raw.get("t")
            if eq is None or not t:
                continue
            history.append(
                {
                    "t": str(t),
                    "equity": round(float(eq), 2),
                    "delta": round(float(_num(raw.get("delta")) or 0.0), 2),
                    "kind": str(raw.get("kind") or "exit"),
                }
            )
        history.append({"t": now.astimezone(timezone.utc).isoformat(), "equity": round(float(booked), 2), "kind": "now"})
        equity = round(float(booked), 2)
        peak = round(float(stored_peak if stored_peak is not None else booked), 2)
        all_time = round(equity - seed, 2)
        all_pct = round((all_time / seed) * 10000.0) / 100.0 if seed else 0.0
        # Booked net equity is seed + closed P&L. A seed-only curve that does not
        # land on the seed is a bad seed_adjust stack (fallback $5k migrations),
        # not a loss. Hold the line at the seed until a close books.
        if curve_is_seed_only(curve) and abs(float(booked) - float(seed)) > 0.01:
            equity = round(float(seed), 2)
            t0 = history[0]["t"] if history else now.astimezone(timezone.utc).isoformat()
            history = [
                {"t": t0, "equity": equity},
                {"t": now.astimezone(timezone.utc).isoformat(), "equity": equity},
            ]
            all_time = 0.0
            all_pct = 0.0
            peak = round(max(float(peak), equity), 2)
    else:
        start_ms = 0
        for pos in (*closed, *active):
            for attr in ("entry_time", "opened_at", "closed_at", "exit_time"):
                t = _ms(getattr(pos, attr, None))
                if t and (start_ms == 0 or t < start_ms):
                    start_ms = t
        seed_t = (
            datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).isoformat()
            if start_ms
            else now.astimezone(timezone.utc).isoformat()
        )
        withdrawals = getattr(executor, "withdrawals", None)
        if not isinstance(withdrawals, list):
            withdrawals = []
        events = []
        withdraw_sum = 0.0
        for pos in closed:
            t = getattr(pos, "closed_at", None) or getattr(pos, "exit_time", None)
            if not t:
                continue
            kind = str(getattr(pos, "kind", None) or getattr(pos, "event_type", None) or "").lower()
            if kind in ("withdraw", "withdrawal"):
                amt = _num(getattr(pos, "amount", None))
                if amt is None:
                    amt = _num(getattr(pos, "realized_pnl", None)) or 0.0
                delta = -abs(float(amt))
                withdraw_sum += abs(float(amt))
            else:
                booked_pnl = _num(getattr(pos, "realized_pnl", None))
                if booked_pnl is None:
                    exit_row = _tape_exit(pos)
                    booked_pnl = float(exit_row["pnl"]) if exit_row else 0.0
                delta = float(booked_pnl)
            events.append((str(t), delta))
        for w in withdrawals:
            if not isinstance(w, dict):
                continue
            t = w.get("t") or w.get("at") or w.get("time") or w.get("withdrawn_at") or w.get("ts")
            amt = _num(w.get("amount"))
            if amt is None:
                amt = _num(w.get("delta"))
            if not t or amt is None or amt == 0:
                continue
            events.append((str(t), -abs(float(amt))))
            withdraw_sum += abs(float(amt))
        events.sort(key=lambda e: e[0])
        history = [{"t": seed_t, "equity": round(seed, 2)}]
        running = seed
        for t, delta in events:
            running = round(running + float(delta), 2)
            history.append({"t": t, "equity": running})
        history.append({"t": now.astimezone(timezone.utc).isoformat(), "equity": round(running, 2)})
        equity = round(running, 2)
        all_time = round(equity - seed, 2)
        all_pct = round((all_time / seed) * 10000.0) / 100.0 if seed else 0.0
        peak = max((h["equity"] for h in history), default=equity)
        withdraw_sum = round(withdraw_sum, 2)

    # No 500-point stride any more: the page downsamples itself (v2.46.0+).
    sizing = _desk_sizing(now)
    if sizing and equity and equity > 0:
        tradable = float(equity) * float(sizing["trading_capital_pct"]) / 100.0
        per_slot = tradable / int(sizing["slot_count"])
        if float(sizing["sum_cap_usd"]) > 0:
            per_slot = min(per_slot, float(sizing["sum_cap_usd"]))
        deploy_cap: Optional[float] = round(per_slot, 2)
        cap_mode = "on" if sizing["sum_cap_on"] else "off"
        util_pct = round((deployed / float(equity)) * 1000.0) / 10.0
        cap_extra = {
            "deploy_slot_cap": deploy_cap,
            "deploy_tradable": round(tradable, 2),
            "deploy_slot_count": int(sizing["slot_count"]),
            "deploy_capital_pct": sizing["trading_capital_pct"],
            "deploy_sum_cap": sizing["sum_cap_usd"],
            "deploy_cap_basis": "net_equity",
        }
    else:
        deploy_cap, cap_mode, util_pct, cap_extra = None, "unknown", None, {}

    return {
        "schema": SCHEMA,
        "version": VERSION,
        "title": "€£$¥ 0DTE",
        "badge": "RESEARCH",
        "mode": "DEMO",
        "online": True,
        "data_quality": "ledger",
        "disclaimer": "Values from DEMO executor memory at fill/close (same moment as Telegram). No simulated fills.",
        "clock": _fmt_clock(now),
        "clock_short": now.astimezone(PT).strftime("%I:%M:%S %p"),
        "tz": "America/Los_Angeles",
        "tick": int(now.timestamp() * 1000),
        "ledger": {
            "path": desk_snapshot_gcs_path(),
            "mtime": now.isoformat(),
            "source": "executor.memory",
            "closed_count": len(closed),
            "active_count": len(active),
        },
        "lanes": [
            {"id": "ORB1", "label": "ORB1", "state": "ledger-only"},
            {"id": "TL", "label": "Trendline", "state": "ledger-only"},
            {"id": "ORB2", "label": "ORB2/Breakout", "state": "ledger-only"},
        ],
        "signal": _signal(active, closed),
        "tape": tape,
        "stats_trading_date": stats_date,
        "orb_capture": capture,
        "positions": positions,
        "risk": {
            "paths_armed": None,
            "discovery": None,
            "liq_guard": None,
            "note": "Process arming status unavailable in ledger-mirror mode (not invented).",
        },
        "metrics": {
            "open_positions": len(positions),
            "day_pnl": day_pnl,
            "rejects": None,
            "deployed": deployed,
            "deploy_cap": deploy_cap,
            "deploy_cap_mode": cap_mode,
            "deploy_slot_cap": deploy_cap,
            "util_pct": util_pct,
            "util_base": "net_equity",
        },
        "sizing": sizing,
        "eod": {
            "day_pnl": day_pnl,
            "wins": wins,
            "losses": losses,
            "label": "Today from executor daily_stats",
            "week_pnl": _num(weekly.get("total_pnl")) if weekly else None,
        },
        "account": {
            "seed": seed,
            "equity": round(equity, 2),
            "total_pnl": all_time,
            "total_pnl_pct": all_pct,
            "peak_equity": round(float(peak), 2),
            "withdrawals": withdraw_sum,
            "day_pnl": day_pnl,
            "unrealized_pnl": unreal,
            "deployed": deployed,
            "deploy_cap": deploy_cap,
            "deploy_cap_mode": cap_mode,
            **cap_extra,
            "open_positions": len(positions),
            "wins": wins,
            "losses": losses,
            "hit_rate": hit_rate,
            "history": history,
            "history_complete": True,
            "history_total_points": len(history),
            "source": "DEMO synthetic ledger, GCS",
            **_lifetime_from_curve(curve if used_curve else None, closed),
        },
        "discovery": {
            "items": [],
            "count": 0,
            "ready": 0,
            "confirm": 0,
            "watch": 0,
            "summary": "scan polled separately",
            "note": "Discovery names come from desk_public/discovery.json.",
            "source": None,
            "updated_at": None,
        },
    }


def build_head(snapshot: Dict[str, Any], seq: int) -> Dict[str, Any]:
    tape = snapshot.get("tape") if isinstance(snapshot.get("tape"), list) else []
    top = tape[0] if tape else {}
    acct = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    return {
        "seq": seq,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "tape_top_id": top.get("id"),
        "tape_top_ts": top.get("ts"),
        "open_positions": metrics.get("open_positions") or 0,
        "equity": acct.get("equity"),
        "clock": snapshot.get("clock"),
    }


def _flush_sync(snapshot: Dict[str, Any], seq: int) -> None:
    head = build_head(snapshot, seq)
    try:
        from modules.gcs_persistence import get_gcs_persistence

        gcs = get_gcs_persistence()
        if not gcs or not gcs.enabled:
            return
        try:
            from modules.desk_rejects import rejects_block

            apply_rejects(snapshot, rejects_block())
        except Exception as rej_err:
            log.debug("DESK_SNAPSHOT_PULSE | rejects skipped | %s", rej_err)
        snap_body = json.dumps(snapshot, separators=(",", ":"), default=str)
        head_body = json.dumps(head, separators=(",", ":"), default=str)
        ok_snap = gcs.upload_string(
            desk_snapshot_gcs_path(),
            snap_body,
            cache_control=CACHE_CONTROL,
            content_type="application/json; charset=utf-8",
            public_read=True,
        )
        ok_head = gcs.upload_string(
            desk_head_gcs_path(),
            head_body,
            cache_control=CACHE_CONTROL,
            content_type="application/json; charset=utf-8",
            public_read=True,
        )
        if ok_snap:
            log.info(
                "DESK_SNAPSHOT_PULSE | wrote snapshot %dB head=%s | seq=%s | tape=%s",
                len(snap_body),
                "ok" if ok_head else "fail",
                seq,
                (snapshot.get("tape") or [{}])[0].get("id") if snapshot.get("tape") else None,
            )
        else:
            log.warning("DESK_SNAPSHOT_PULSE | snapshot upload failed | seq=%s", seq)
    except Exception as err:
        log.warning("DESK_SNAPSHOT_PULSE | upload failed | %s", err)


def _open_book_sig(snapshot: Dict[str, Any]) -> str:
    acct = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
    bits = [
        str(acct.get("unrealized_pnl")),
        str(acct.get("open_positions")),
        str(acct.get("day_pnl")),
        str(acct.get("wins")),
        str(acct.get("losses")),
    ]
    for pos in snapshot.get("positions") or []:
        if not isinstance(pos, dict):
            continue
        bits.append(
            f"{pos.get('trade_id')}:{pos.get('cur_pct')}:{pos.get('unrealized_pnl')}:"
            f"{pos.get('mfe_pct')}:{pos.get('mark_mfe_pct')}"
        )
    return "|".join(bits)


def _publish_snapshot(executor: Any, *, always: bool) -> bool:
    global _SEQ, _PENDING, _LAST_OPEN_BOOK_SIG
    try:
        snapshot = build_desk_snapshot(executor)
    except Exception as err:
        log.debug("desk_snapshot_pulse build failed | %s", err)
        return False
    opens = bool(snapshot.get("positions"))
    sig = _open_book_sig(snapshot)
    with _LOCK:
        if not always and (sig == _LAST_OPEN_BOOK_SIG or not opens):
            return opens
        _LAST_OPEN_BOOK_SIG = sig
        _SEQ += 1
        seq = _SEQ
        _PENDING = snapshot
    _flush_sync(snapshot, seq)
    return opens


def _flush_worker() -> None:
    global _WORKER
    with _LOCK:
        executor = _EXECUTOR
        _WORKER = None
    if executor is not None:
        _publish_snapshot(executor, always=True)


def _heartbeat_worker() -> None:
    global _HEARTBEAT
    while True:
        time.sleep(_mark_interval_sec())
        with _LOCK:
            executor = _EXECUTOR
        if executor is None:
            _HEARTBEAT = None
            return
        if not _publish_snapshot(executor, always=False):
            _HEARTBEAT = None
            return


def _ensure_heartbeat() -> None:
    global _HEARTBEAT
    beat = None
    with _LOCK:
        if _HEARTBEAT is None or not _HEARTBEAT.is_alive():
            beat = threading.Thread(target=_heartbeat_worker, name="desk-snapshot-heartbeat", daemon=True)
            _HEARTBEAT = beat
    if beat is not None:
        beat.start()


def queue_desk_snapshot(executor: Any, *, force: bool = True) -> None:
    """Off-thread snapshot + head write. Fill/close call this once.

    While any name is open, a single 1.0s heartbeat republishes the full book
    from executor memory. Monitor ticks do not call this.
    """
    global _EXECUTOR, _WORKER
    if executor is None:
        return
    worker = None
    with _LOCK:
        _EXECUTOR = executor
        if _WORKER is None or not _WORKER.is_alive():
            worker = threading.Thread(target=_flush_worker, name="desk-snapshot-pulse", daemon=True)
            _WORKER = worker
    if worker is not None:
        worker.start()
    _ensure_heartbeat()
