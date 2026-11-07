"""Unified, path-agnostic 5-minute chain pre-sampler for dir_pct_change_5m (t168u).

ONE mechanism serves every path and side (ORB1, TL0/TL1/TL2+, ORB2/Breakout,
CALL/PUT). There are no path hooks: the unified discovery scan
(``modules.discovery_watch_batch.run_unified_0dte_discovery_scan``) calls
``on_discovery_quotes(system, quotes)`` once per 10 s tick, after its batch
quote. That function walks every registered *level source* (the live candidate
registries all paths already keep), reads each candidate's levels through one
common interface (``candidate_levels``), and queues at most a capped number of
low-priority (P2) chain samples through the chain fetch manager. The logic is
identical for every path; the path only appears as a label in the trigger
source.

Common level interface (``candidate_levels``), first match wins:
  1. ``cand.dir5m_levels(ts)`` -> iterable of (level, side)   (opt-in for new paths)
  2. ``cand.trendline.value_at(ts)``  (any drawn line; side from the setup payload
     ``expected_option_side`` / ``option_side`` or line direction BEAR->call, BULL->put)
  3. ``cand.orb_high`` / ``cand.orb_low`` (any range: ORB1 range, ORB2 range)
     -> (orb_high, call) and (orb_low, put)
A candidate in a must-run state (WAITING_FOR_CONFIRMATION / READY_TO_EXECUTE /
BREAK_DETECTED) counts as an approach at distance 0 (trigger ``<src>:armed``).

Per tick, the in-band symbols of the tick (|price - level| <= band, band =
max(DIR5M_APPROACH_ATR x candidate ATR, DIR5M_APPROACH_PCT x level)) are ranked
the same way for every path (``DIR5M_APPROACH_RANK``, default ``close``:
closest to its level first; ``toward`` / ``toward_repeat`` / ``repeat`` are
research alternatives that scored lower in the t168u 9/15-10/14 replay), then
queued until the caps bind. A symbol whose ring already holds a sample younger than
DIR5M_APPROACH_RESAMPLE_SEC is skipped (no API).

Caps (unchanged from t166u/t167u): DIR5M_APPROACH_MAX_PER_MIN (6) per rolling
60 s; one per symbol per DIR5M_APPROACH_RESAMPLE_SEC (300 s); re-arm after
DIR5M_APPROACH_TTL_SEC (1800 s); P2 (``P_RESAMPLE``) through the chain fetch
manager, which keeps CHAIN_FETCH_LOW_PRIORITY_RESERVE tokens for P0/P1.

Open seed: the session-tech chain pre-warm (``modules.session_tech_warmup``,
CHAIN_RING_PREWARM_TIMES_PT, default 06:40/06:50/07:00/07:15/07:26/07:33)
samples every watchlist symbol (later slots: only symbols with no sample in
4 min) and calls ``note_open_seed`` so the ORB1 / TL0 first hour and the 07:30
TL1 / ORB2 build have priors and the trigger source is stamped.

Telemetry (stamped by ``chain_ring.derive_chain_trajectory_features`` on every
decision while DIR5M_RING_V2_ENABLED): ``dir_pct_approach_lead_s`` (decision
time minus the first sampler/seed sample for the symbol inside the TTL, else
None) and ``dir_pct_approach_source`` (``<src>:approach`` / ``<src>:armed`` /
``open_seed`` / ``none``).

Keep-alive (t202u/t203u, DIR5M_KEEPALIVE_ENABLED): the handoff guarantee is
anchored on the discovery scan -> must-execute flow. The earliest reliable
pre-must-execute signal for every path is the candidate's registration in its
live registry (``LEVEL_SOURCES``: ORB1 0DTE watch, TL engine candidates, ORB2
breakout watch), which precedes the arm / READY_TO_EXECUTE state that lasts
only seconds (10/15 median 24 s). From that tick on, every live non-terminal
candidate symbol (DIR5M_KEEPALIVE_SCOPE=live; ``band`` = armed + in-band only)
gets one P2 chain sample whenever its newest ring sample is older than
DIR5M_KEEPALIVE_SEC (210 s, armed / in-band) or DIR5M_KEEPALIVE_WIDE_SEC (360 s,
other live candidates). Any gap between ring samples <= 420 s keeps a 180-600 s
prior available at every moment, so the ring holds one until the candidate
executes, expires or retires. Order:
armed (must-execute) first, then in-band, then the rest; oldest ring sample
first inside a tier. An armed symbol with no sample (or none in
DIR5M_ARM_NOW_SAMPLE_SEC) is sampled on that tick. Own rolling cap
DIR5M_KEEPALIVE_MAX_PER_MIN (24), separate from the approach cap; still P2,
behind the P0/P1 reserve. Trigger source ``<src>:keepalive`` (or
``<src>:armed``); ``dir_pct_approach_lead_s`` then measures the lead from the
first pre-must-execute sample.
"""

from __future__ import annotations

import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Tuple

STATE_ATTR = "_dir5m_approach_state"
ARMED_STATE_TOKENS = ("WAITING_FOR_CONFIRMATION", "READY_TO_EXECUTE", "BREAK_DETECTED")


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _num(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name)
        return float(raw) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        return float(default)


def sampler_enabled() -> bool:
    return _flag("DIR5M_APPROACH_SAMPLER_ENABLED", False)


def approach_atr() -> float:
    return _num("DIR5M_APPROACH_ATR", 0.25)


def approach_pct() -> float:
    return max(0.0, _num("DIR5M_APPROACH_PCT", 0.004))


def max_per_min(now: Optional[float] = None) -> int:
    base = int(max(0, _num("DIR5M_APPROACH_MAX_PER_MIN", 6)))
    boost = int(max(0, _num("DIR5M_APPROACH_BOOST_MAX_PER_MIN", 0)))
    if boost > base and _in_boost_window(now):
        return boost
    return base


def _in_boost_window(now: Optional[float]) -> bool:
    """t194u: DIR5M_APPROACH_BOOST_WINDOW_PT ("06:30-09:00") in America/Los_Angeles."""
    raw = str(os.environ.get("DIR5M_APPROACH_BOOST_WINDOW_PT") or "06:30-09:00").strip()
    try:
        from zoneinfo import ZoneInfo

        a, b = [x.strip() for x in raw.split("-", 1)]
        lt = datetime.fromtimestamp(float(now if now is not None else time.time()), tz=ZoneInfo("America/Los_Angeles"))
        m = lt.hour * 60 + lt.minute
        ah, am = (int(x) for x in a.split(":"))
        bh, bm = (int(x) for x in b.split(":"))
        return ah * 60 + am <= m < bh * 60 + bm
    except Exception:
        return False


def aging_first() -> bool:
    """t194u: rank in-band symbols whose last ring sample is about to age out first."""
    return _flag("DIR5M_APPROACH_AGING_FIRST", True)


def aging_window_sec() -> Tuple[float, float]:
    lo = _num("DIR5M_APPROACH_AGING_MIN_SEC", 240.0)
    hi = _num("DIR5M_FALLBACK_MAX_AGE_SEC", 600.0)
    return lo, hi


def keepalive_enabled() -> bool:
    return _flag("DIR5M_KEEPALIVE_ENABLED", False)


def keepalive_sec() -> float:
    """Keep-alive cadence; capped at 400 s so two samples always straddle 180-600 s."""
    return min(400.0, max(60.0, _num("DIR5M_KEEPALIVE_SEC", 210.0)))


def keepalive_wide_sec() -> float:
    """Cadence for live candidates outside the band (tier 2). Any sample gap <= 420 s
    keeps a 180-600 s prior available at every moment, so <= 400 s (incl. fetch lag)."""
    return min(400.0, max(keepalive_sec(), _num("DIR5M_KEEPALIVE_WIDE_SEC", 360.0)))


def keepalive_max_per_min() -> int:
    return int(max(0, _num("DIR5M_KEEPALIVE_MAX_PER_MIN", 24)))


def keepalive_scope() -> str:
    v = str(os.environ.get("DIR5M_KEEPALIVE_SCOPE") or "live").strip().lower()
    return v if v in ("live", "band") else "live"


def keepalive_retry_sec() -> float:
    return max(15.0, _num("DIR5M_KEEPALIVE_RETRY_SEC", 60.0))


def arm_now_sample_sec() -> float:
    return max(15.0, _num("DIR5M_ARM_NOW_SAMPLE_SEC", 60.0))


def resample_sec() -> float:
    return max(60.0, _num("DIR5M_APPROACH_RESAMPLE_SEC", 300.0))


def ttl_sec() -> float:
    return max(300.0, _num("DIR5M_APPROACH_TTL_SEC", 1800.0))


def seed_ttl_sec() -> float:
    return 600.0


def rank_mode() -> str:
    return str(os.environ.get("DIR5M_APPROACH_RANK") or "close").strip().lower()


# ------------------------------------------------------------------ band
def approach_band(*, level: Optional[float], atr: Optional[float]) -> Optional[float]:
    """Absolute price band around ``level`` that counts as an approach."""
    try:
        lv = float(level) if level is not None else 0.0
    except (TypeError, ValueError):
        return None
    if not (lv > 0):
        return None
    band = approach_pct() * lv
    try:
        if atr is not None and float(atr) > 0:
            band = max(band, approach_atr() * float(atr))
    except (TypeError, ValueError):
        pass
    return band if band > 0 else None


def is_approach(*, price: Optional[float], level: Optional[float], atr: Optional[float]) -> bool:
    try:
        if price is None or level is None:
            return False
        band = approach_band(level=level, atr=atr)
        if band is None:
            return False
        return abs(float(price) - float(level)) <= band
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------ state
def _state(system: Any) -> Dict[str, Any]:
    st = getattr(system, STATE_ATTR, None)
    if not isinstance(st, dict):
        st = {}
        setattr(system, STATE_ATTR, st)
    st.setdefault("armed", {})
    st.setdefault("queued_t", deque())
    st.setdefault("seeded", {})
    st.setdefault("counts", {})
    st.setdefault("hist", {})
    st.setdefault("n_today", {})
    st.setdefault("ka_q", deque())
    st.setdefault("ka_last", {})
    return st


def _bump(system: Any, key: str, n: int = 1) -> None:
    try:
        c = _state(system)["counts"]
        c[key] = int(c.get(key, 0)) + n
    except Exception:
        pass


def note_approach(
    system: Any,
    symbol: str,
    *,
    side: Any = None,
    spot: Optional[float] = None,
    now: Optional[float] = None,
    manager: Any = None,
    trigger: str = "",
    kind: str = "approach",
    rank: Optional[int] = None,
) -> str:
    """Queue one P2 chain sample for ``symbol``. Returns the action.

    ``kind="keepalive"`` (t202u) uses its own rolling cap and retry gap; the
    caller already checked the ring age against DIR5M_KEEPALIVE_SEC.

    The only enqueue point of the sampler; every path reaches it through
    ``on_discovery_quotes``.
    """
    if not sampler_enabled():
        return "disabled"
    sym = str(symbol or "").strip().upper()
    if not sym or system is None:
        return "no_symbol"
    t = float(now if now is not None else time.time())
    st = _state(system)
    armed: Dict[str, Dict[str, Any]] = st["armed"]
    q: Deque[float] = st["queued_t"]
    while q and t - q[0] > 60.0:
        q.popleft()
    a = armed.get(sym)
    ka = kind == "keepalive"
    if ka:
        q = st["ka_q"]
        while q and t - q[0] > 60.0:
            q.popleft()
        lk = st["ka_last"].get(sym)
        if lk is not None and t - lk < keepalive_retry_sec():
            return "recent"
        if len(q) >= keepalive_max_per_min():
            return "cap_minute"
    else:
        # The per-symbol gap holds across a TTL re-arm too (t167u).
        if a is not None and t - a["last"] < resample_sec():
            return "recent"
        if len(q) >= max_per_min(t):
            return "cap_minute"
    if a is not None and t - a["first"] > (max(ttl_sec(), 7200.0) if ka else ttl_sec()):
        armed.pop(sym, None)
        a = None
    mgr = manager
    if mgr is None:
        try:
            from central_tech.chain_fetch_manager import get_manager

            mgr = get_manager(system)
        except Exception:
            mgr = None
    if mgr is None:
        return "no_manager"
    try:
        from central_tech.chain_fetch_manager import P_RESAMPLE

        _reason = "dir5m_keepalive" if ka else "dir5m_approach"
        if rank is None:
            mgr.enqueue(sym, priority=P_RESAMPLE, side=side, spot=spot, reason=_reason)
        else:
            # t217u: armed / in-band keep-alive go first inside P2 (handoff-first).
            try:
                mgr.enqueue(sym, priority=P_RESAMPLE, side=side, spot=spot, reason=_reason, rank=int(rank))
            except TypeError:
                mgr.enqueue(sym, priority=P_RESAMPLE, side=side, spot=spot, reason=_reason)
        # Off by default so every sampler chain stays inside these caps.
        if _flag("DIR5M_APPROACH_NOTE_CANDIDATE", False):
            mgr.note_candidate(sym, spot=spot, side=side)
    except Exception:
        return "enqueue_failed"
    q.append(t)
    if ka:
        st["ka_last"][sym] = t
    armed[sym] = {"first": a["first"] if a else t, "last": t, "source": trigger or (a or {}).get("source") or "approach"}
    st["n_today"][sym] = int(st["n_today"].get(sym, 0)) + 1
    return "queued"


def note_open_seed(system: Any, symbol: str, *, now: Optional[float] = None) -> None:
    """Record that the open seed (chain pre-warm) sampled ``symbol`` (no API)."""
    try:
        sym = str(symbol or "").strip().upper()
        if sym and system is not None:
            _state(system)["seeded"][sym] = float(now if now is not None else time.time())
    except Exception:
        pass


def approach_lead_s(system: Any, symbol: str, *, now: Optional[float] = None) -> Optional[float]:
    """Seconds from the first sampler (or open-seed) sample to ``now``; None if none."""
    try:
        st = getattr(system, STATE_ATTR, None)
        if not isinstance(st, dict):
            return None
        sym = str(symbol or "").strip().upper()
        t = float(now if now is not None else time.time())
        a = (st.get("armed") or {}).get(sym)
        if a:
            lead = t - float(a["first"])
            if 0 <= lead <= max(ttl_sec(), 7200.0 if keepalive_enabled() else 0.0):
                return round(lead, 1)
        s = (st.get("seeded") or {}).get(sym)
        if s is not None:
            lead = t - float(s)
            if 0 <= lead <= seed_ttl_sec():
                return round(lead, 1)
        return None
    except Exception:
        return None


def approach_source(system: Any, symbol: str, *, now: Optional[float] = None) -> str:
    """Trigger source of the newest sampler / seed sample for ``symbol``."""
    try:
        st = getattr(system, STATE_ATTR, None)
        if not isinstance(st, dict):
            return "none"
        sym = str(symbol or "").strip().upper()
        t = float(now if now is not None else time.time())
        a = (st.get("armed") or {}).get(sym)
        s = (st.get("seeded") or {}).get(sym)
        a_ok = bool(a) and 0 <= t - float(a["first"]) <= max(ttl_sec(), 7200.0 if keepalive_enabled() else 0.0)
        s_ok = s is not None and 0 <= t - float(s) <= seed_ttl_sec()
        if a_ok and (not s_ok or float(a["last"]) >= float(s)):
            return str(a.get("source") or "approach")
        if s_ok:
            return "open_seed"
        return "none"
    except Exception:
        return "none"


# ------------------------------------------------------------------ level sources
def _values(container: Any) -> List[Any]:
    if container is None:
        return []
    if isinstance(container, dict):
        return list(container.values())
    try:
        return list(container)
    except TypeError:
        return []


LevelSource = Tuple[str, Callable[[Any], Iterable[Any]]]
LEVEL_SOURCES: List[LevelSource] = [
    ("orb1", lambda s: _values(getattr(s, "_orb1_0dte_watch_candidates", None))),
    ("tl", lambda s: _values(getattr(getattr(s, "trendline_signal_engine", None), "candidates", None))),
    ("orb2", lambda s: _values(getattr(s, "_breakout_watch_candidates", None))),
]


def register_level_source(name: str, getter: Callable[[Any], Iterable[Any]]) -> None:
    """Add a candidate registry (a new path). The sampler logic does not change."""
    for i, (n, _g) in enumerate(LEVEL_SOURCES):
        if n == name:
            LEVEL_SOURCES[i] = (name, getter)
            return
    LEVEL_SOURCES.append((name, getter))


def _side_token(raw: Any) -> str:
    s = str(getattr(raw, "value", raw) or "").strip().lower()
    return s if s in ("call", "put") else ""


def _state_text(cand: Any) -> str:
    st = getattr(cand, "state", None)
    bits = [str(st or "")]
    for attr in ("name", "value"):
        v = getattr(st, attr, None)
        if v:
            bits.append(str(v))
    return " ".join(bits).upper()


def is_armed_candidate(cand: Any) -> bool:
    txt = _state_text(cand)
    if any(tok in txt for tok in ARMED_STATE_TOKENS):
        return True
    setup = getattr(cand, "setup_payload", None) or {}
    simple = setup.get("simple_mode") if isinstance(setup, dict) else None
    if isinstance(simple, dict):
        s2 = str(simple.get("state") or "").upper()
        if any(tok in s2 for tok in ARMED_STATE_TOKENS):
            return True
    return False


def _is_terminal(cand: Any) -> bool:
    txt = _state_text(cand)
    return any(tok in txt for tok in ("EXECUTED", "EXPIRED", "REJECTED", "CANCELLED", "COMPLETED"))


def _cand_atr(cand: Any) -> Optional[float]:
    for v in (getattr(cand, "atr", None), (getattr(cand, "metadata", None) or {}).get("atr")
              if isinstance(getattr(cand, "metadata", None), dict) else None):
        try:
            if v is not None and float(v) > 0:
                return float(v)
        except (TypeError, ValueError):
            continue
    return None


def candidate_levels(cand: Any, *, ts: Optional[datetime] = None) -> List[Tuple[float, str]]:
    """Common level interface for every path: [(level, side)]."""
    out: List[Tuple[float, str]] = []
    ts = ts or datetime.now(timezone.utc)
    fn = getattr(cand, "dir5m_levels", None)
    if callable(fn):
        try:
            for lv, sd in fn(ts) or ():
                if lv and float(lv) > 0:
                    out.append((float(lv), _side_token(sd)))
            return out
        except Exception:
            return []
    tl = getattr(cand, "trendline", None)
    if tl is not None and callable(getattr(tl, "value_at", None)):
        try:
            lv = float(tl.value_at(ts))
        except Exception:
            lv = 0.0
        if lv > 0:
            side = ""
            pl = getattr(cand, "setup_payload", None) or {}
            if isinstance(pl, dict):
                side = _side_token(pl.get("expected_option_side")) or _side_token(pl.get("option_side"))
            if not side:
                d = str(getattr(getattr(cand, "direction", None), "value", getattr(cand, "direction", "")) or "").lower()
                side = "call" if d == "bear" else ("put" if d == "bull" else _side_token(getattr(cand, "option_side", None)))
            out.append((lv, side))
        return out
    try:
        hi = float(getattr(cand, "orb_high", 0.0) or 0.0)
        lo = float(getattr(cand, "orb_low", 0.0) or 0.0)
    except (TypeError, ValueError):
        return out
    if hi > 0 and lo > 0 and hi > lo:
        out.append((hi, "call"))
        out.append((lo, "put"))
    return out


def _quote_px(q: Any) -> float:
    if not isinstance(q, dict):
        return 0.0
    for k in ("last", "lastTrade", "close", "price", "current_price"):
        try:
            v = float(q.get(k) or 0.0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return 0.0


def _ring_latest_t(system: Any, sym: str) -> Optional[float]:
    try:
        from central_tech.chain_ring import _ensure_chain_skew_ring

        buf = _ensure_chain_skew_ring(system).get(sym) or []
        ts = [float(s.get("t")) for s in buf if isinstance(s, dict) and s.get("t") is not None]
        return max(ts) if ts else None
    except Exception:
        return None


def _rank_score(st: Dict[str, Any], key: Tuple[str, str, str], sym: str, ratio: float, t: float) -> float:
    mode = rank_mode()
    if mode == "close":
        return ratio
    hist: Dict[Any, Deque[Tuple[float, float]]] = st["hist"]
    h = hist.get(key)
    toward = True
    if h:
        older = [r for (tt, r) in h if t - tt >= 150.0]
        if older:
            toward = ratio < older[-1]
    score = ratio + (0.0 if toward else 1.0)
    if mode in ("toward_repeat", "repeat"):
        score += 0.5 * int(st["n_today"].get(sym, 0))
    if mode == "repeat":
        score = ratio + 0.5 * int(st["n_today"].get(sym, 0))
    return score


def on_discovery_quotes(
    system: Any,
    quotes: Any,
    *,
    now: Optional[float] = None,
    manager: Any = None,
) -> Dict[str, int]:
    """THE single entry point: one call per unified discovery tick, all paths.

    No broker call of its own; queues at most the capped P2 chain samples.
    """
    if not sampler_enabled():
        return {"disabled": 1}
    if system is None or not isinstance(quotes, dict) or not quotes:
        return {"no_quotes": 1}
    t = float(now if now is not None else time.time())
    ts = datetime.fromtimestamp(t, tz=timezone.utc)
    st = _state(system)
    day = time.strftime("%Y-%m-%d", time.localtime(t))
    if st.get("day") != day:
        # New trading day: drop yesterday's arm / seed / rank memory (caps restart).
        for k in ("armed", "hist", "n_today"):
            st[k] = {}
        st["seeded"] = {
            k: v for k, v in (st.get("seeded") or {}).items()
            if time.strftime("%Y-%m-%d", time.localtime(float(v))) == day
        }
        st["queued_t"] = deque()
        st["ka_q"] = deque()
        st["ka_last"] = {}
        st["day"] = day
    px_by = {str(k).strip().upper(): _quote_px(v) for k, v in quotes.items()}
    best: Dict[str, Tuple[float, float, str, str, float]] = {}
    live: Dict[str, Tuple[str, str, float]] = {}  # t202u: every live candidate symbol
    seen_keys = set()
    for src, getter in list(LEVEL_SOURCES):
        try:
            cands = list(getter(system) or ())
        except Exception:
            cands = []
        for cand in cands:
            sym = str(getattr(cand, "symbol", "") or "").strip().upper()
            px = px_by.get(sym, 0.0)
            if not sym or px <= 0 or _is_terminal(cand):
                continue
            if sym not in live:
                live[sym] = (src, _side_token(getattr(cand, "option_side", None)), px)
            if is_armed_candidate(cand):
                levels = candidate_levels(cand, ts=ts)
                side = levels[0][1] if len(levels) == 1 else _side_token(getattr(cand, "option_side", None))
                cur = (-1.0, 0.0, f"{src}:armed", side, px)  # armed ranks ahead of any approach
                if sym not in best or cur[0] < best[sym][0]:
                    best[sym] = cur
                continue
            atr = _cand_atr(cand)
            for lv, side in candidate_levels(cand, ts=ts):
                band = approach_band(level=lv, atr=atr)
                if band is None:
                    continue
                ratio = abs(px - lv) / band
                key = (sym, src, side)
                h = st["hist"].setdefault(key, deque(maxlen=8))
                if key not in seen_keys:
                    seen_keys.add(key)
                    score = _rank_score(st, key, sym, ratio, t)
                    h.append((t, ratio))
                else:
                    score = _rank_score(st, key, sym, ratio, t)
                if ratio > 1.0:
                    continue
                cur = (score, ratio, f"{src}:approach", side, px)
                if sym not in best or cur[0] < best[sym][0]:
                    best[sym] = cur
    out: Dict[str, int] = {"candidates": len(best)}
    last_by = {sym: _ring_latest_t(system, sym) for sym in set(best) | set(live)}
    ka_done = set()
    if keepalive_enabled():
        ka_done = _keepalive_pass(system, best, live, last_by, t=t, manager=manager, out=out)
    a_lo, a_hi = aging_window_sec()
    use_aging = aging_first()

    def _order(kv: Tuple[str, Tuple[float, float, str, str, float]]) -> Tuple[int, float]:
        sym, (score, *_rest) = kv
        if score < 0:  # armed first, always
            return (0, score)
        lt = last_by.get(sym)
        # t194u: a prior 4-10 min old is about to age out of the fallback window;
        # refreshing it keeps the symbol stamped (morning prior_too_old misses).
        if use_aging and lt is not None and a_lo <= t - lt <= a_hi:
            return (1, score)
        return (2, score)

    for sym, (score, _ratio, trig, side, px) in sorted(best.items(), key=_order):
        if sym in ka_done:
            continue
        last_t = last_by.get(sym)
        if last_t is not None and 0 <= t - last_t < resample_sec():
            out["ring_fresh"] = out.get("ring_fresh", 0) + 1
            continue
        act = note_approach(system, sym, side=side or None, spot=px, now=t, manager=manager, trigger=trig)
        out[act] = out.get(act, 0) + 1
        _bump(system, f"{trig}:{act}")
        if act == "cap_minute":
            break
    return out


def _keepalive_pass_rank_enabled() -> bool:
    return _flag("DIR5M_KEEPALIVE_TIER_RANK", True)


def _keepalive_rank(tier: int) -> Optional[int]:
    """t217u: P2 rank for a keep-alive tier (0 armed, 1 in-band); wide keeps the reason default."""
    if not _keepalive_pass_rank_enabled() or tier not in (0, 1):
        return None
    try:
        from central_tech.chain_fetch_manager import P2_RANK_ARMED, P2_RANK_BAND

        return P2_RANK_ARMED if tier == 0 else P2_RANK_BAND
    except Exception:
        return None


def _keepalive_pass(
    system: Any,
    best: Dict[str, Tuple[float, float, str, str, float]],
    live: Dict[str, Tuple[str, str, float]],
    last_by: Dict[str, Optional[float]],
    *,
    t: float,
    manager: Any,
    out: Dict[str, int],
) -> set:
    """t202u keep-alive tier: queue the due symbols, armed first. Returns the queued set."""
    k_sec, w_sec, arm_sec = keepalive_sec(), keepalive_wide_sec(), arm_now_sample_sec()
    rows = []
    for sym, (src, side, px) in live.items():
        b = best.get(sym)
        if b is not None and b[0] < 0:
            tier, trig, side, px = 0, b[2], b[3] or side, b[4]
        elif b is not None:
            tier, trig, side, px = 1, f"{src}:keepalive", b[3] or side, b[4]
        else:
            if keepalive_scope() != "live":
                continue
            tier, trig = 2, f"{src}:keepalive"
        lt = last_by.get(sym)
        age = None if lt is None else t - lt
        due_gap = arm_sec if tier == 0 else (k_sec if tier == 1 else w_sec)
        if age is not None and 0 <= age < due_gap:
            continue
        rows.append((tier, -(age if age is not None else 1e9), sym, trig, side, px))
    done = set()
    out["keepalive_due"] = len(rows)
    for tier, _na, sym, trig, side, px in sorted(rows):
        act = note_approach(system, sym, side=side or None, spot=px, now=t, manager=manager,
                            trigger=trig, kind="keepalive", rank=_keepalive_rank(tier))
        out[f"keepalive_{act}"] = out.get(f"keepalive_{act}", 0) + 1
        _bump(system, f"{trig}:ka_{act}")
        if act == "queued":
            done.add(sym)
        if act == "cap_minute":
            break
    return done


def maybe_note_level_approach(
    system: Any,
    symbol: str,
    *,
    price: Optional[float],
    level: Optional[float],
    atr: Optional[float] = None,
    side: Any = None,
    source: str = "",
    now: Optional[float] = None,
    manager: Any = None,
) -> str:
    """Single-level helper (tests / tools). Same band and caps as the tick path."""
    if not sampler_enabled():
        return "disabled"
    try:
        if not is_approach(price=price, level=level, atr=atr):
            return "not_near"
        action = note_approach(system, symbol, side=side, spot=price, now=now, manager=manager,
                               trigger=f"{source or 'na'}:approach")
        _bump(system, f"{source or 'na'}:{action}")
        return action
    except Exception:
        return "error"


def reset_daily_state(system: Any) -> None:
    try:
        setattr(system, STATE_ATTR, None)
    except Exception:
        pass
