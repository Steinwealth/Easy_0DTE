"""In-session chain-volume ring for the central tech hub.

Owns call/put volume samples and the five-minute deltas the Trade Profile has
used since before 10.1 (`dir_pct_change_5m`, `*_chain_volume_delta_5m`). No
broker fetch. No Priority Optimizer import. Not a Dynamic Volume rolling key.
The ring dict is `system._central_tech_chain_ring`. Older names
`_dynvol_chain_ring` and `_po_chain_skew_ring` point at the same dict.
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from typing import Tuple, Any, Dict, List, Optional, Sequence

_CHAIN_RING_MAX = 64
_CHAIN_RING_MIN_SPACING_SEC = 15.0

# t166u (Oct 14 2026): dir_pct_change_5m ring v2. Zero broker calls.
#  F1 every chain read records at its true observation time (never a cached
#     row at wall-clock now()).
#  F2 nearest-prior fallback 180-600 s when the strict 4-7 min window is empty;
#     the age and the window used are stamped.
#  F3 a reason code for every missing dir_pct_change_5m.
#  TTL: samples older than DIR5M_RING_TTL_SEC (3600) are pruned on record.
# DIR5M_RING_V2_ENABLED=false restores the pre-t166u behavior exactly.
DIR5M_STAMP_KEYS: Tuple[str, ...] = (
    "dir_pct_prior_age_s",
    "dir_pct_prior_window",
    "dir_pct_missing_reason",
    # t167u/t168u: seconds from the first pre-sample (approach / armed / open
    # seed) to the decision, and that sample's trigger source.
    "dir_pct_approach_lead_s",
    "dir_pct_approach_source",
)
RING_DIRTY_ATTR = "_central_tech_chain_ring_dirty"

# t217u (Oct 17 2026): the dir5m value and its stamps move as one group. A
# re-derivation without a value (e.g. the executed row's chain clock after the
# freeze, or a later exit pass) must not attach its missing reason / clocks to
# a row that already holds a value or a reason. Used by the PO observe stamp.
# Approved by Eric 10/17 12:25 PT: ON by default (code default, not a config
# file key, so Cloud Run --update-env-vars DIR5M_TELEMETRY_ATOMIC=false rolls back).
DIR5M_GROUP_KEYS: Tuple[str, ...] = (
    "dir_pct_change_5m",
    "dir_pct_change_5m_prior_age_sec",
    "dir_pct_prior_observed_at",
    "dir_pct_now_chain_observed_at",
    "dir_pct_now_chain_age_s",
) + DIR5M_STAMP_KEYS


def dir5m_telemetry_atomic_enabled() -> bool:
    return _env_flag("DIR5M_TELEMETRY_ATOMIC", True)


def merge_dir5m_group(row: Dict[str, Any], blob: Dict[str, Any], *, overwrite: bool) -> Tuple[int, frozenset]:
    """Write the dir5m group from ``blob`` onto ``row`` all-or-nothing.

    Returns ``(keys_changed, keys_owned)``; the caller skips ``keys_owned`` in
    its generic merge. Rules:
      * blob has a value: write the whole group when ``overwrite`` or the row has
        no value yet (a frozen handoff value is never replaced on carry-forward);
      * blob has no value: never touch a row that already has a value, nor (on
        carry-forward) a row that already carries a reason; otherwise write the
        group (value None, its reason and clocks).
    Values are never invented: every key comes from the same derivation.
    """
    if not isinstance(row, dict) or not isinstance(blob, dict):
        return 0, frozenset()
    if not any(k in blob for k in DIR5M_GROUP_KEYS):
        return 0, frozenset()
    owned = frozenset(DIR5M_GROUP_KEYS)
    blob_has = blob.get("dir_pct_change_5m") is not None
    row_has = row.get("dir_pct_change_5m") is not None
    if blob_has:
        if row_has and not overwrite:
            return 0, owned
    else:
        if row_has:
            return 0, owned
        if not overwrite and row.get("dir_pct_missing_reason") not in (None, ""):
            return 0, owned
    n = 0
    for k in DIR5M_GROUP_KEYS:
        if k not in blob and not blob_has:
            continue
        v = blob.get(k)
        if row.get(k) != v:
            n += 1
        if v is not None or k in row:
            row[k] = v
    return n, owned


def _env_flag(name: str, default: bool) -> bool:
    import os

    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _env_num(name: str, default: float) -> float:
    import os

    try:
        raw = os.environ.get(name)
        return float(raw) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        return float(default)


def ring_v2_enabled() -> bool:
    return _env_flag("DIR5M_RING_V2_ENABLED", True)


def ring_ttl_sec() -> float:
    return max(600.0, _env_num("DIR5M_RING_TTL_SEC", 3600.0))


def dir5m_fallback_enabled() -> bool:
    return ring_v2_enabled() and _env_flag("DIR5M_FALLBACK_ENABLED", True)


def dir5m_fallback_window_sec() -> Tuple[float, float]:
    lo = _env_num("DIR5M_FALLBACK_MIN_AGE_SEC", 180.0)
    hi = _env_num("DIR5M_FALLBACK_MAX_AGE_SEC", 600.0)
    if hi <= lo:
        return (180.0, 600.0)
    return (max(1.0, lo), hi)


def open_short_in_window(now: Optional[float]) -> bool:
    """t202u: session-start / path-activation exception windows.

    DIR5M_OPEN_SHORT_WINDOW_PT: list of PT windows (separator , ; or |), e.g.
    "06:30-06:36" or "06:30-06:36|06:45-06:51|07:30-07:36" (TL0 lines build ~06:45, TL1 lines and
    the Breakout watch ~07:30). Empty = off.
    """
    raw = str(os.getenv("DIR5M_OPEN_SHORT_WINDOW_PT") or "").strip()
    if not raw or now is None or not ring_v2_enabled():
        return False
    try:
        from zoneinfo import ZoneInfo

        lt = datetime.fromtimestamp(float(now), tz=ZoneInfo("America/Los_Angeles"))
        m = lt.hour * 60 + lt.minute + lt.second / 60.0
    except Exception:
        return False
    import re as _re

    for part in _re.split(r"[,;|]", raw):
        try:
            a, b = [x.strip() for x in part.split("-", 1)]
            ah, am = (int(x) for x in a.split(":"))
            bh, bm = (int(x) for x in b.split(":"))
        except Exception:
            continue
        if ah * 60 + am <= m < bh * 60 + bm:
            return True
    return False


def open_short_min_age_sec() -> float:
    return max(15.0, _env_num("DIR5M_OPEN_SHORT_MIN_AGE_SEC", 60.0))


def iso_utc(epoch: Optional[float]) -> Optional[str]:
    """t204u: ISO-8601 UTC for a clock stamp (None stays None)."""
    try:
        if epoch is None:
            return None
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()
    except Exception:
        return None


def decision_chain_max_age_sec() -> float:
    """t204u: the decision now-chain must be <= this old at the freeze (TP_FREEZE_CHAIN_MAX_AGE_SEC)."""
    try:
        from central_tech.decision_freeze import chain_max_age_sec

        return float(chain_max_age_sec())
    except Exception:
        return max(5.0, _env_num("TP_FREEZE_CHAIN_MAX_AGE_SEC", 60.0))


def snapshot_now_max_age_for(row: Dict[str, Any]) -> float:
    """t204u: 20p uses DIR5M_20P_NOW_MAX_AGE_SEC when set; other snapshots DIR5M_SNAPSHOT_NOW_MAX_AGE_SEC."""
    base = snapshot_now_max_age_sec()
    try:
        tag = " ".join(str(row.get(k) or "") for k in ("stage", "po_snapshot_stage", "event", "purpose")).lower()
    except Exception:
        tag = ""
    if "20p" in tag:
        raw = os.getenv("DIR5M_20P_NOW_MAX_AGE_SEC")
        if raw not in (None, ""):
            try:
                return max(5.0, float(raw))
            except (TypeError, ValueError):
                return base
    return base


def _iso_epoch(raw: Any) -> Optional[float]:
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        if v > 1e12:
            v /= 1000.0
        return v if v > 1e9 else None
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00").replace(" ", "T", 1))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def chain_observation_epoch(skew: Optional[Dict[str, Any]]) -> Optional[float]:
    """The broker observation clock of the chain carried on ``skew`` (epoch s).

    Order: ``tp_chain_observed_epoch`` (gate enrich), ``chain_observed_at`` /
    ``po_chain_asof_at`` (freeze / PO snapshot). None when the row carries no
    chain clock. ``chain_age_seconds`` is not used: on a carried row it was
    measured at an unknown earlier time.
    """
    if not isinstance(skew, dict):
        return None
    v = _f(skew.get("tp_chain_observed_epoch"))
    if v is not None and v > 1e9:
        return v
    for k in ("chain_observed_at", "po_chain_asof_at"):
        e = _iso_epoch(skew.get(k))
        if e is not None:
            return e
    return None


def _mark_ring_dirty(system: Any) -> None:
    try:
        setattr(system, RING_DIRTY_ATTR, True)
    except Exception:
        pass


def _ring_stats(system: Any) -> Dict[str, int]:
    st = getattr(system, "_central_tech_chain_ring_stats", None)
    if not isinstance(st, dict):
        st = {}
        try:
            setattr(system, "_central_tech_chain_ring_stats", st)
        except Exception:
            pass
    return st


def _bump(system: Any, key: str) -> None:
    st = _ring_stats(system)
    st[key] = int(st.get(key, 0)) + 1


def prune_ring_ttl(buf: List[Dict[str, Any]], *, now: float, ttl: Optional[float] = None) -> int:
    """Drop samples older than the ring TTL (in place). Returns samples dropped."""
    ttl_s = float(ttl if ttl is not None else ring_ttl_sec())
    keep = [s for s in buf if _f(s.get("t")) is not None and float(now) - float(s["t"]) <= ttl_s]
    dropped = len(buf) - len(keep)
    if dropped:
        buf[:] = keep
    return dropped

def _f(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if out != out or math.isinf(out):
        return None
    return out


def _option_side_token(row: Optional[Dict[str, Any]]) -> str:
    if not isinstance(row, dict):
        return ""
    raw = str(
        row.get("option_side")
        or row.get("expected_option_side")
        or row.get("side")
        or row.get("option_type")
        or row.get("option_type_label")
        or ""
    ).strip().upper()
    if "PUT" in raw or raw in ("SHORT", "BEAR", "BEARISH", "P"):
        return "PUT"
    if "CALL" in raw or raw in ("LONG", "BULL", "BULLISH", "C", "UP"):
        return "CALL"
    return ""


def _same_side_volume_share(
    call_v: Optional[float],
    put_v: Optional[float],
    side_tok: str,
) -> Optional[float]:
    """Gate-accurate same-side chain volume share (CALL→call%, PUT→put%)."""
    if call_v is None or put_v is None:
        return None
    tot = float(call_v) + float(put_v)
    if tot <= 0:
        return None
    if side_tok == "PUT":
        return float(put_v) / tot
    if side_tok == "CALL":
        return float(call_v) / tot
    return None


def _same_side_share_from_sample(
    sample: Optional[Dict[str, Any]],
    side_tok: str,
) -> Optional[float]:
    """Recompute same-side dir% from call/put on a ring sample for this trade side.

    Discovery stamps often omit dir% or carry a side-agnostic value. For QG/SS
    remine, dir_pct_change_5m must compare same-side shares at both endpoints.
    """
    if not isinstance(sample, dict) or not side_tok:
        return None
    return _same_side_volume_share(
        _f(sample.get("call_chain_volume")),
        _f(sample.get("put_chain_volume")),
        side_tok,
    )


def _sample_owned_vol(sample: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(sample, dict):
        return None
    return _f(sample.get("cumulative_volume") or sample.get("representative_volume"))


def _sample_oi(sample: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(sample, dict):
        return None
    return _f(sample.get("open_interest") or sample.get("representative_open_interest"))


def _ensure_chain_skew_ring(system: Any) -> Dict[str, List[Dict[str, Any]]]:
    ring = getattr(system, "_central_tech_chain_ring", None)
    if not isinstance(ring, dict):
        for legacy_name in ("_dynvol_chain_ring", "_po_chain_skew_ring"):
            legacy = getattr(system, legacy_name, None)
            if isinstance(legacy, dict):
                ring = legacy
                break
    if not isinstance(ring, dict):
        ring = {}
    try:
        setattr(system, "_central_tech_chain_ring", ring)
        setattr(system, "_dynvol_chain_ring", ring)
        setattr(system, "_po_chain_skew_ring", ring)
    except Exception:
        pass
    return ring


def note_chain_skew_warm_candidate(system: Any, symbol: str) -> None:
    """Track symbols that need periodic pre-execute chain ring refresh.

    Held-position refresh alone cannot warm first-touch executes. Layer C /
    admit / options enrich register here so discovery can sparse-refresh
    CONFIRM / imminent names even when the book is flat.
    """
    if system is None:
        return
    sym = str(symbol or "").strip().upper()
    if not sym:
        return
    warm = getattr(system, "_po_chain_skew_warm_symbols", None)
    if not isinstance(warm, dict):
        warm = {}
        setattr(system, "_po_chain_skew_warm_symbols", warm)
    warm.setdefault(sym, 0.0)


def record_chain_skew_sample(
    system: Any,
    symbol: str,
    skew: Optional[Dict[str, Any]],
    *,
    ts: Optional[float] = None,
    force: bool = False,
) -> None:
    """Append chain hydrate stamp for trajectory deltas (existing hydrates only)."""
    if system is None or not isinstance(skew, dict):
        return
    sym = str(symbol or "").strip().upper()
    if not sym:
        return
    call_v = _f(skew.get("call_chain_volume"))
    put_v = _f(skew.get("put_chain_volume"))
    dir_v = _f(skew.get("direction_volume_pct"))
    # Same-side dir% only when trade side is known. Never invent call-share for
    # side-unknown discovery stamps — that poisons PUT dir_pct_change_5m when
    # gates remine from decision snaps (call share vs put same-side).
    if dir_v is None and call_v is not None and put_v is not None:
        side_tok = _option_side_token(skew)
        share = _same_side_volume_share(call_v, put_v, side_tok)
        if share is not None:
            dir_v = share
    v2 = ring_v2_enabled()
    if v2:
        # F1: the sample time is the chain's own observation clock. An explicit
        # ts (fetch clock, freeze clock) never makes a cached chain look newer.
        obs = chain_observation_epoch(skew)
        if ts is not None and obs is not None:
            sample_t = min(float(ts), float(obs))
        elif ts is not None:
            sample_t = float(ts)
        elif obs is not None:
            sample_t = float(obs)
        else:
            src = str(skew.get("chain_fetch_source") or skew.get("chain_attempt") or "").strip().lower()
            if src.startswith("cache") or src.startswith("row_seed") or src == "cached":
                # A cached / carried chain with no clock: recording it at now()
                # would invent a later observation. R6 already holds the
                # original sample when it was recorded at fetch.
                _bump(system, "skip_cached_no_clock")
                return
            sample_t = time.time()
    else:
        sample_t = float(ts if ts is not None else time.time())
    sample = {
        "t": sample_t,
        "call_chain_volume": call_v,
        "put_chain_volume": put_v,
        "direction_volume_pct": dir_v,
        "representative_volume": _f(skew.get("representative_volume")),
        "cumulative_volume": _f(skew.get("cumulative_volume")),
        "open_interest": _f(skew.get("open_interest")),
        "representative_open_interest": _f(skew.get("representative_open_interest")),
        "representative_bid": _f(skew.get("representative_bid") or skew.get("bid") or skew.get("option_bid")),
        "representative_ask": _f(skew.get("representative_ask") or skew.get("ask") or skew.get("option_ask")),
        "iv": _f(skew.get("iv") or skew.get("implied_volatility")),
        "delta": _f(skew.get("delta")),
        "underlying_price": _f(skew.get("underlying_price") or skew.get("current_price")),
        # Strike of the contract whose bid/ask this sample carries. A
        # representative_* quote belongs to representative_strike, not to the
        # selected strike on the same row.
        "strike": _f(
            (skew.get("representative_strike") or skew.get("strike") or skew.get("po_options_owned_strike"))
            if skew.get("representative_bid") is not None
            else (skew.get("strike") or skew.get("po_options_owned_strike") or skew.get("representative_strike"))
        ),
        "expiry": _expiry_iso(skew),
    }
    if all(sample.get(k) is None for k in (
        "call_chain_volume",
        "put_chain_volume",
        "direction_volume_pct",
        "representative_volume",
        "cumulative_volume",
    )):
        return
    note_chain_skew_warm_candidate(system, sym)
    ring = _ensure_chain_skew_ring(system)
    buf = ring.setdefault(sym, [])
    # R6 (Oct 5, part 2): chain volumes are session-cumulative. A sample whose
    # call AND put chain volumes equal any sample already in the ring is that
    # same chain observation re-recorded (row seed, cached chain re-read, entry
    # cache carry, ORB1 Layer C re-stamp). Appending it at a new time invents a
    # later observation and lets a 5m delta be measured against a stale copy.
    # Keep the original sample and its time. ``force`` does not bypass this.
    if call_v is not None and put_v is not None and buf:
        for prior in reversed(buf):
            if _f(prior.get("call_chain_volume")) == call_v and _f(prior.get("put_chain_volume")) == put_v:
                # Do not back-fill the prior with this row's quote either: the
                # quote may be newer than the chain observation's timestamp.
                if v2:
                    _bump(system, "dup_r6")
                    try:
                        # Same observation seen with an older true clock: keep the
                        # earlier time (never move a sample later).
                        if sample_t < float(prior.get("t") or sample_t):
                            prior["t"] = sample_t
                            buf.sort(key=lambda x: float(x.get("t") or 0.0))
                            _mark_ring_dirty(system)
                    except (TypeError, ValueError):
                        pass
                return
    if not force and buf:
        last = buf[-1]
        try:
            dt = sample_t - float(last.get("t") or 0.0)
        except (TypeError, ValueError):
            dt = 1e9
        if dt < _CHAIN_RING_MIN_SPACING_SEC:
            same_vols = (
                sample.get("call_chain_volume") == last.get("call_chain_volume")
                and sample.get("put_chain_volume") == last.get("put_chain_volume")
                and sample.get("representative_volume") == last.get("representative_volume")
                and sample.get("cumulative_volume") == last.get("cumulative_volume")
                and sample.get("representative_bid") == last.get("representative_bid")
            )
            if same_vols:
                # Keep earlier t so prior age toward min_age_sec is preserved.
                return
            # Volumes moved. Append. Do not overwrite the prior sample's
            # volumes: that copied the current chain total onto an older
            # timestamp and destroyed the 5-minute observation.
    if v2 and buf and _strict_5m_window():
        # TTL (after R6, so a duplicate observation never prunes the ring).
        # Strict mode only: the legacy loose picker (TP_FREEZE_5M_STRICT=false)
        # still reads same-session priors up to 6 h old.
        newest = max((float(x.get("t") or 0.0) for x in buf), default=sample_t)
        prune_ring_ttl(buf, now=max(newest, sample_t))
    buf.append(sample)
    if v2 and len(buf) > 1 and float(buf[-2].get("t") or 0.0) > sample_t:
        # An older observation recorded late (observation clock): keep time order.
        buf.sort(key=lambda x: float(x.get("t") or 0.0))
    if len(buf) > _CHAIN_RING_MAX:
        del buf[: len(buf) - _CHAIN_RING_MAX]
    if v2:
        _bump(system, "recorded")
        _mark_ring_dirty(system)


def chain_ring_premium_samples(system: Any, symbol: str) -> List[Dict[str, Any]]:
    """Prior chain bids for this symbol. Decision handoffs have no open premium path yet."""
    sym = str(symbol or "").strip().upper()
    if system is None or not sym:
        return []
    try:
        ring = _ensure_chain_skew_ring(system)
    except Exception:
        return []
    buf = ring.get(sym) if isinstance(ring, dict) else None
    if not isinstance(buf, list):
        return []
    out: List[Dict[str, Any]] = []
    for sample in buf:
        if not isinstance(sample, dict):
            continue
        bid = sample.get("representative_bid")
        if bid is None:
            continue
        ask = sample.get("representative_ask")
        mid = None
        try:
            if ask is not None:
                mid = (float(bid) + float(ask)) / 2.0
        except (TypeError, ValueError):
            mid = None
        out.append(
            {
                "t": sample.get("t"),
                "premium": bid,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "iv": sample.get("iv"),
                "delta": sample.get("delta"),
                "underlying": sample.get("underlying_price"),
                # normalize_quote_sample reads underlying_price, not "underlying".
                # Without this key every symbol-ring prior lost its spot.
                "underlying_price": sample.get("underlying_price"),
                # Contract identity so readers can refuse another strike's quote.
                "strike": sample.get("strike"),
            }
        )
    return out


def _pick_chain_prior_sample(
    samples: Sequence[Dict[str, Any]],
    *,
    now: float,
    horizon_sec: float,
    field_get,
    min_age_sec: float = 60.0,
    max_age_sec: float = 21600.0,
    max_dist_sec: Optional[float] = None,
    fallback_oldest: bool = True,
) -> Optional[Dict[str, Any]]:
    """Pick a prior ring sample that actually carries ``field_get(sample)``.

    Sep 8 desk: a single nearest-to-t−5m sample often had call/put but null
    owned/dir, so owned_Δ / dir_Δ stayed null while call/put Δ filled. Prefer
    the sample nearest the horizon that has the needed field; fall back to the
    oldest eligible sample (≥ min_age) that has it.

    Sep 9: ``max_age_sec`` must cover the full RTH session (~6h). A 900s cap
    dropped valid same-session priors for re-entries after ~15m (fill collapsed
    from ~96% at 5–15m to ~22% beyond 15m despite ring history existing).
    """
    target = float(now) - float(horizon_sec)
    best: Optional[Dict[str, Any]] = None
    best_dist: Optional[float] = None
    oldest_with: Optional[Dict[str, Any]] = None
    oldest_t: Optional[float] = None
    for s in samples:
        if not isinstance(s, dict):
            continue
        t = _f(s.get("t"))
        if t is None:
            continue
        age = float(now) - float(t)
        if age < float(min_age_sec) or age > float(max_age_sec):
            continue
        if field_get(s) is None:
            continue
        dist = abs(float(t) - target)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = s
        if oldest_t is None or float(t) < float(oldest_t):
            oldest_t = float(t)
            oldest_with = s
    if max_dist_sec is not None:
        # Oct 7 2026 strict 5m window: the prior must sit within
        # horizon ± max_dist_sec. No oldest-sample fallback (a 6h-old prior is
        # not a 5-minute delta); no qualifying prior -> None (warm-up).
        if best is not None and best_dist is not None and best_dist <= float(max_dist_sec):
            return best
        return None
    if best is not None:
        # Accept nearest-to-horizon when within ~horizon (allow early-session
        # rings that only reach 2–4 minutes).
        if best_dist is not None and best_dist <= max(90.0, float(horizon_sec) * 0.75):
            return best
    return oldest_with if fallback_oldest else None


def _path_samples_for_symbol(system: Any, symbol: str, row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Merge ring + premium_path ticks that already carry chain skew."""
    samples: List[Dict[str, Any]] = []
    ring = _ensure_chain_skew_ring(system).get(str(symbol).upper()) or []
    samples.extend(dict(s) for s in ring if isinstance(s, dict))

    # premium_path on profit optimizer record when trade_id known.
    tid = str(row.get("trade_id") or "").strip()
    if tid:
        try:
            po = getattr(system, "profit_optimizer", None) or getattr(system, "_profit_optimizer", None)
            rec = None
            if po is not None and getattr(po, "recorder", None) is not None:
                rec = po.recorder._records.get(tid)
            path = getattr(rec, "premium_path", None) if rec is not None else None
            if isinstance(path, list):
                for tick in path:
                    if not isinstance(tick, dict):
                        continue
                    ts = tick.get("observation_timestamp") or tick.get("t") or tick.get("timestamp")
                    t_epoch = None
                    if isinstance(ts, (int, float)):
                        t_epoch = float(ts)
                        if t_epoch > 1e12:
                            t_epoch /= 1000.0
                    elif isinstance(ts, str):
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            t_epoch = dt.timestamp()
                        except Exception:
                            t_epoch = None
                    if t_epoch is None:
                        continue
                    samples.append(
                        {
                            "t": t_epoch,
                            "call_chain_volume": _f(tick.get("call_chain_volume")),
                            "put_chain_volume": _f(tick.get("put_chain_volume")),
                            "direction_volume_pct": _f(tick.get("direction_volume_pct")),
                            "representative_volume": _f(
                                tick.get("representative_volume") or tick.get("cumulative_volume")
                            ),
                            "cumulative_volume": _f(tick.get("cumulative_volume")),
                            "open_interest": _f(tick.get("open_interest")),
                        }
                    )
        except Exception:
            pass

    # Dedupe by timestamp bucket.
    samples.sort(key=lambda s: float(s.get("t") or 0.0))
    return samples


def _strict_5m_window() -> bool:
    import os

    return str(os.environ.get("TP_FREEZE_5M_STRICT", "true")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _dir5m_prior_window_sec() -> Tuple[float, float]:
    """Oct 13 2026 (Eric): 5m chain priors = nearest prior aged 4-7 minutes.

    ``TP_DIR5M_PRIOR_MIN_AGE_SEC`` (240) / ``TP_DIR5M_PRIOR_MAX_AGE_SEC`` (420).
    The chosen prior's age is stamped (``dir_pct_change_5m_prior_age_sec``,
    ``chain_volume_delta_5m_prior_age_sec``). No prior in the window -> None,
    never 0.0. Set both to empty to fall back to horizon +/- TP_FREEZE_5M_PRIOR_TOL_SEC.
    """
    import os

    def _g(name: str, default: float) -> Optional[float]:
        raw = os.environ.get(name)
        if raw is None:
            return default
        if str(raw).strip() == "":
            return None
        try:
            return float(raw)
        except ValueError:
            return default

    lo = _g("TP_DIR5M_PRIOR_MIN_AGE_SEC", 240.0)
    hi = _g("TP_DIR5M_PRIOR_MAX_AGE_SEC", 420.0)
    if lo is None or hi is None or hi <= lo:
        return (None, None)  # type: ignore[return-value]
    return (max(1.0, lo), hi)


def _prior_5m_tolerance_sec() -> float:
    try:
        from central_tech.decision_freeze import prior_5m_tolerance_sec

        return float(prior_5m_tolerance_sec())
    except Exception:
        return 90.0


def _expiry_iso(blob: Any) -> str:
    """Listed expiry on a chain row or ring sample (YYYY-MM-DD)."""
    if not isinstance(blob, dict):
        return ""
    try:
        from modules.option_expiry_memo import normalize_expiry_iso
    except Exception:
        normalize_expiry_iso = None
    for k in ("selected_expiry", "expiry_ymd", "selected_expiry_ymd", "expiry"):
        raw = blob.get(k)
        if normalize_expiry_iso is not None:
            iso = normalize_expiry_iso(raw)
        else:
            iso = str(raw or "").strip()
        if iso:
            return iso
    return ""


def _filter_same_expiry(
    samples: Sequence[Dict[str, Any]],
    now_exp: str,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Keep priors with no expiry (legacy) or the same listed day as now.

    A now-chain from Friday weekly must not subtract a Wednesday 0DTE prior.
    """
    if not now_exp:
        return [s for s in samples if isinstance(s, dict)], False
    kept: List[Dict[str, Any]] = []
    mismatched = False
    for s in samples:
        if not isinstance(s, dict):
            continue
        exp = _expiry_iso(s)
        if not exp or exp == now_exp:
            kept.append(s)
        else:
            mismatched = True
    return kept, mismatched


def _dir5m_missing_reason(
    samples: Sequence[Dict[str, Any]],
    distinct: Sequence[Dict[str, Any]],
    *,
    now: float,
    cur_dir: Optional[float],
    side_tok: str,
    strict_lo: Optional[float],
) -> str:
    """F3: why dir_pct_change_5m is missing (one code per decision)."""
    if cur_dir is None:
        return "no_current_chain"
    older = [s for s in samples if _f(s.get("t")) is not None and float(now) - float(s["t"]) > 1.0]
    if not older:
        return "no_prior"
    dist = [s for s in distinct if _f(s.get("t")) is not None and float(now) - float(s["t"]) > 1.0]
    if not dist:
        return "dup_sample"
    with_share = [s for s in dist if _same_side_share_from_sample(s, side_tok) is not None]
    if not with_share:
        return "prior_no_side_share"
    if dir5m_fallback_enabled():
        lo, hi = dir5m_fallback_window_sec()
    else:
        lo = float(strict_lo) if strict_lo is not None else 240.0
        hi = _dir5m_prior_window_sec()[1] or 420.0
    ages = [float(now) - float(s["t"]) for s in with_share]
    if all(a < lo for a in ages):
        return "prior_too_young"
    if all(a > hi for a in ages):
        return "prior_too_old"
    return "prior_outside_window"


def derive_chain_trajectory_features(
    system: Any,
    row: Dict[str, Any],
    *,
    symbol: str,
    horizon_sec: float = 300.0,
    freeze_ts: Optional[datetime] = None,
    asof_ts: Optional[float] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "call_chain_volume_delta_5m": None,
        "put_chain_volume_delta_5m": None,
        "same_side_chain_volume_delta_5m": None,
        "opposite_side_chain_volume_delta_5m": None,
        "dir_pct_change_5m": None,
        "owned_contract_volume_delta_5m": None,
        "owned_contract_oi_change": None,
        # Absolute endpoints for remine (now + ~5m ago) — avoid reconstructing
        # from deltas when mining accept/reject thresholds.
        "same_side_chain_volume": None,
        "same_side_chain_volume_5m_ago": None,
        "direction_volume_pct_5m_ago": None,
        "dir_pct_change_5m_prior_age_sec": None,
        "chain_volume_delta_5m_prior_age_sec": None,
    }
    v2 = ring_v2_enabled()
    if v2:
        out["dir_pct_prior_age_s"] = None
        out["dir_pct_prior_window"] = None
        out["dir_pct_missing_reason"] = "no_prior"
    side_tok = _option_side_token(row)
    # Align row.direction_volume_pct with gate same-side semantics before ring seed.
    try:
        from modules.volume_data import ensure_volume_research_fields

        ensure_volume_research_fields(row, option_side=side_tok)
    except Exception:
        pass
    # Seed ring with this row so subsequent snaps share history; derive still
    # picks priors older than min_age so we do not delta against ourselves.
    seed_ts = freeze_ts.timestamp() if isinstance(freeze_ts, datetime) else None
    # t172u: live snapshots (20p / exit / shadow) measure dir% as of their OWN
    # observation time. ``asof_ts`` is the snapshot event clock; the "now" chain
    # is this snapshot's chain (clock within the fresh cap) or the hub's latest
    # ring sample within the cap. A carried entry chain / clock is never "now".
    if asof_ts is not None and freeze_ts is None and snapshot_asof_enabled():
        return _derive_asof_snapshot(system, row, symbol=symbol, horizon_sec=horizon_sec,
                                     asof_ts=float(asof_ts), out=out, v2=v2)
    # Oct 7 2026: a cached chain is not "now". When the gate stamped the chain's
    # own observation clock, the seed sample and the delta clock use it, so a
    # 7-minute-old cached chain is neither recorded as a fresh ring sample nor
    # compared against a prior picked relative to the wall clock.
    chain_obs = _f(row.get("tp_chain_observed_epoch")) if isinstance(row, dict) else None
    if chain_obs is not None and not chain_obs > 1e9:
        chain_obs = None
    if chain_obs is None and isinstance(row, dict) and isinstance(freeze_ts, datetime):
        # t204u: handoff rows carry the gate chain clock as chain_observed_at / po_chain_asof_at.
        chain_obs = _row_chain_clock(row)
    freeze_epoch = seed_ts
    # t204u: the decision chain is recorded at its OWN observation clock, never
    # re-stamped at the freeze (that made a 50 s old chain look current). A chain
    # with no clock is not seeded on the freeze path (its time is unknown).
    if chain_obs is not None:
        seed_ts = chain_obs
    try:
        if not (freeze_epoch is not None and chain_obs is None):
            record_chain_skew_sample(system, symbol, row, ts=seed_ts)
    except Exception:
        pass
    samples = _path_samples_for_symbol(system, symbol, row)
    if freeze_epoch is not None:
        now = float(freeze_epoch)
    elif seed_ts is not None:
        now = float(seed_ts)
    else:
        now = time.time()
    asof_clock = now
    # t204u causality: now-chain observed <= the decision clock and within
    # TP_FREEZE_CHAIN_MAX_AGE_SEC; priors are strictly older than the now-chain
    # observation and their age is measured from it (not from the freeze).
    causal_block: Optional[str] = None
    if chain_obs is not None:
        if chain_obs > now + 0.25:
            causal_block = "now_chain_after_freeze"
        elif freeze_epoch is not None and now - chain_obs > decision_chain_max_age_sec() + 0.01:
            causal_block = "now_chain_stale"
    if v2:
        # t168u: stamped on every decision (all paths / sides): lead time from the
        # first pre-sample (approach / armed / open seed) and its trigger source.
        out["dir_pct_approach_lead_s"] = None
        out["dir_pct_approach_source"] = "none"
        try:
            from central_tech import dir5m_presample as _f4b

            out["dir_pct_approach_lead_s"] = _f4b.approach_lead_s(system, symbol, now=now)
            out["dir_pct_approach_source"] = _f4b.approach_source(system, symbol, now=now)
        except Exception:
            pass
        # t194u: the decision chain's age at the decision clock (snapshot telemetry).
        out["dir_pct_now_chain_age_s"] = (
            round(float(now) - float(chain_obs), 3) if chain_obs is not None else None
        )
        out["dir_pct_asof_epoch"] = asof_clock
        out["dir_pct_asof_at"] = iso_utc(asof_clock)
        out["dir_pct_now_chain_observed_at"] = iso_utc(chain_obs)
        out["dir_pct_prior_observed_at"] = None
    if causal_block is not None:
        if v2:
            out["dir_pct_missing_reason"] = causal_block
        return out
    if chain_obs is not None:
        now = min(float(now), float(chain_obs))
    if freeze_ts is not None or seed_ts is not None:
        samples = [s for s in samples if _f(s.get("t")) is not None and float(s["t"]) <= now + 0.25]
    if not samples:
        # No ring history: the current endpoints still come from the decision row.
        rc_, rp_ = _f(row.get("call_chain_volume")), _f(row.get("put_chain_volume"))
        out["same_side_chain_volume"] = rp_ if side_tok == "PUT" else rc_
        return out
    latest = samples[-1]
    # Prefer live row values as "now" when present.
    cur_call = _f(row.get("call_chain_volume"))
    cur_put = _f(row.get("put_chain_volume"))
    cur_owned_vol = _f(row.get("cumulative_volume") or row.get("representative_volume"))
    cur_oi = _f(row.get("open_interest") or row.get("representative_open_interest"))
    if cur_call is None:
        cur_call = _f(latest.get("call_chain_volume"))
    if cur_put is None:
        cur_put = _f(latest.get("put_chain_volume"))
    if cur_owned_vol is None:
        cur_owned_vol = _sample_owned_vol(latest)
    if cur_oi is None:
        cur_oi = _sample_oi(latest)
    # Dir% for deltas: always same-side share from call/put using this trade's
    # side (matches Convex / TL dir gate). Do not use stored ring dir% which may
    # be call-share or opposite-side from a prior path.
    cur_dir = _same_side_volume_share(cur_call, cur_put, side_tok)
    if cur_dir is None:
        cur_dir = _f(row.get("direction_volume_pct"))
    if cur_dir is not None and row.get("direction_volume_pct") is None and side_tok:
        row["direction_volume_pct"] = float(cur_dir)

    # R6 (Oct 5): a prior that carries exactly the current call AND put chain
    # volumes is the same chain observation re-recorded (stale row seed, cached
    # chain re-read, pre-fetch stamp). Chain volumes are session-cumulative, so
    # a delta against it is a fabricated 0.0, not a 5-minute measurement. Skip
    # such samples; with no distinct prior the delta stays None (warm-up).
    chain_samples = samples
    if cur_call is not None and cur_put is not None:
        chain_samples = [
            s
            for s in samples
            if not (
                _f(s.get("call_chain_volume")) == cur_call
                and _f(s.get("put_chain_volume")) == cur_put
            )
        ]
    now_exp = _expiry_iso(row)
    chain_samples, expiry_mismatch = _filter_same_expiry(chain_samples, now_exp)
    # Oct 7 2026 (fix A5): 5m priors only from horizon ± tolerance (default
    # 300 s ± 90 s), never the oldest-sample fallback. TP_FREEZE_5M_STRICT=false
    # restores the legacy picker.
    strict_kw: Dict[str, Any] = {}
    if _strict_5m_window():
        w_lo, w_hi = _dir5m_prior_window_sec()
        if w_lo is not None:
            # Nearest-to-5m prior inside [w_lo, w_hi] seconds old (4-7 min).
            # 0.01 s slack: an exactly-4-minute prior must not flip on float jitter.
            strict_kw = {"min_age_sec": w_lo - 0.01, "max_age_sec": w_hi + 0.01, "max_dist_sec": 1e9}
        else:
            strict_kw = {"max_dist_sec": _prior_5m_tolerance_sec()}
    prior_call = _pick_chain_prior_sample(
        chain_samples,
        now=now,
        horizon_sec=horizon_sec,
        field_get=lambda s: _f(s.get("call_chain_volume")),
        **strict_kw,
    )
    prior_put = _pick_chain_prior_sample(
        chain_samples,
        now=now,
        horizon_sec=horizon_sec,
        field_get=lambda s: _f(s.get("put_chain_volume")),
        **strict_kw,
    )
    prior_dir = _pick_chain_prior_sample(
        chain_samples,
        now=now,
        horizon_sec=horizon_sec,
        field_get=lambda s: _same_side_share_from_sample(s, side_tok),
        **strict_kw,
    )
    prior_owned = _pick_chain_prior_sample(
        samples,
        now=now,
        horizon_sec=horizon_sec,
        field_get=_sample_owned_vol,
        **strict_kw,
    )
    prior_oi = _pick_chain_prior_sample(
        samples,
        now=now,
        horizon_sec=horizon_sec,
        field_get=_sample_oi,
        **strict_kw,
    )

    def _prior_age(sample: Optional[Dict[str, Any]]) -> Optional[float]:
        if not isinstance(sample, dict):
            return None
        t = _f(sample.get("t"))
        return None if t is None else max(0.0, float(now) - float(t))

    dir_window: Optional[str] = None
    if prior_dir is not None:
        dir_window = "strict" if strict_kw.get("min_age_sec") is not None else "legacy"
    elif v2 and dir5m_fallback_enabled() and strict_kw.get("min_age_sec") is not None:
        f_lo, f_hi = dir5m_fallback_window_sec()
        prior_dir = _pick_chain_prior_sample(
            chain_samples,
            now=now,
            horizon_sec=horizon_sec,
            field_get=lambda s: _same_side_share_from_sample(s, side_tok),
            min_age_sec=f_lo - 0.01,
            max_age_sec=f_hi + 0.01,
            max_dist_sec=1e9,
        )
        if prior_dir is not None:
            dir_window = "fallback"
    if prior_dir is None and v2 and strict_kw.get("min_age_sec") is not None and open_short_in_window(now):
        # t202u: 06:30-06:36 only, a 60-180 s prior, stamped 'open_short' so training can filter it.
        prior_dir = _pick_chain_prior_sample(
            chain_samples,
            now=now,
            horizon_sec=horizon_sec,
            field_get=lambda s: _same_side_share_from_sample(s, side_tok),
            min_age_sec=open_short_min_age_sec() - 0.01,
            max_age_sec=dir5m_fallback_window_sec()[0] + 0.01,
            max_dist_sec=1e9,
        )
        if prior_dir is not None:
            dir_window = "open_short"

    out["dir_pct_change_5m_prior_age_sec"] = _prior_age(prior_dir)
    if v2:
        out["dir_pct_prior_age_s"] = _prior_age(prior_dir)
        out["dir_pct_prior_window"] = dir_window
        out["dir_pct_missing_reason"] = None
        out["dir_pct_prior_observed_at"] = iso_utc(_f(prior_dir.get("t"))) if isinstance(prior_dir, dict) else None
        if out.get("dir_pct_now_chain_observed_at") is None and _f(row.get("call_chain_volume")) is None:
            out["dir_pct_now_chain_observed_at"] = iso_utc(_f(latest.get("t")))

    out["chain_volume_delta_5m_prior_age_sec"] = _prior_age(prior_call or prior_put)

    p_call = _f(prior_call.get("call_chain_volume")) if prior_call else None
    p_put = _f(prior_put.get("put_chain_volume")) if prior_put else None
    p_dir = _same_side_share_from_sample(prior_dir, side_tok) if prior_dir else None
    p_owned = _sample_owned_vol(prior_owned)
    p_oi = _sample_oi(prior_oi)

    if cur_call is not None and p_call is not None:
        out["call_chain_volume_delta_5m"] = cur_call - p_call
    if cur_put is not None and p_put is not None:
        out["put_chain_volume_delta_5m"] = cur_put - p_put
    if cur_dir is not None and p_dir is not None:
        out["dir_pct_change_5m"] = cur_dir - p_dir
    if v2 and out["dir_pct_change_5m"] is None:
        out["dir_pct_prior_window"] = None
        out["dir_pct_prior_age_s"] = None
        out["dir_pct_prior_observed_at"] = None
        if expiry_mismatch and not any(_same_side_share_from_sample(s, side_tok) for s in chain_samples):
            out["dir_pct_missing_reason"] = "expiry_mismatch"
        else:
            out["dir_pct_missing_reason"] = _dir5m_missing_reason(
                samples, chain_samples, now=now, cur_dir=cur_dir, side_tok=side_tok,
                strict_lo=strict_kw.get("min_age_sec"),
            )
    if cur_owned_vol is not None and p_owned is not None:
        out["owned_contract_volume_delta_5m"] = cur_owned_vol - p_owned
    if cur_oi is not None and p_oi is not None:
        out["owned_contract_oi_change"] = cur_oi - p_oi

    call_d = out["call_chain_volume_delta_5m"]
    put_d = out["put_chain_volume_delta_5m"]
    if side_tok == "PUT":
        out["same_side_chain_volume_delta_5m"] = put_d
        out["opposite_side_chain_volume_delta_5m"] = call_d
        out["same_side_chain_volume"] = cur_put
        out["same_side_chain_volume_5m_ago"] = p_put
    else:
        out["same_side_chain_volume_delta_5m"] = call_d
        out["opposite_side_chain_volume_delta_5m"] = put_d
        out["same_side_chain_volume"] = cur_call
        out["same_side_chain_volume_5m_ago"] = p_call
    out["direction_volume_pct_5m_ago"] = p_dir
    return out


def snapshot_asof_enabled() -> bool:
    raw = os.getenv("DIR5M_SNAPSHOT_ASOF_ENABLED")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def snapshot_now_max_age_sec() -> float:
    try:
        return max(5.0, float(os.getenv("DIR5M_SNAPSHOT_NOW_MAX_AGE_SEC", "60") or 60))
    except (TypeError, ValueError):
        return 60.0


def _row_chain_clock(row: Dict[str, Any]) -> Optional[float]:
    """Newest chain clock on the row (a fresh snapshot fetch beats a carried gate clock)."""
    clocks = []
    v = _f(row.get("tp_chain_observed_epoch"))
    if v is not None and v > 1e9:
        clocks.append(v)
    for k in ("chain_observed_at", "po_chain_asof_at"):
        e = _iso_epoch(row.get(k))
        if e is not None:
            clocks.append(e)
    return max(clocks) if clocks else None


def _derive_asof_snapshot(
    system: Any,
    row: Dict[str, Any],
    *,
    symbol: str,
    horizon_sec: float,
    asof_ts: float,
    out: Dict[str, Any],
    v2: bool,
) -> Dict[str, Any]:
    """dir% / chain deltas for a live snapshot as of ``asof_ts`` (t172u).

    now-chain = the row's chain when its own clock is within
    ``DIR5M_SNAPSHOT_NOW_MAX_AGE_SEC`` of the event, else the hub's newest ring
    sample in that window. Prior = ring sample 240-420 s (fallback 180-600 s)
    before the now-chain's observation. Missing -> None + reason, never a copy.
    """
    side_tok = _option_side_token(row)
    cap = snapshot_now_max_age_for(row)
    samples = _path_samples_for_symbol(system, symbol, row)
    # t204u causality: nothing observed after the event, ever (no post-event slack).
    samples = [s for s in samples if _f(s.get("t")) is not None and float(s["t"]) <= asof_ts + 1e-6]
    cur_call = cur_put = None
    now = None
    stale_age = None
    rc = _row_chain_clock(row)
    rcall, rput = _f(row.get("call_chain_volume")), _f(row.get("put_chain_volume"))
    if rc is not None and rcall is not None and rput is not None and 0.0 <= asof_ts - rc + 1e-6 and asof_ts - rc <= cap:
        cur_call, cur_put, now = rcall, rput, float(rc)
    else:
        with_cp = [s for s in samples
                   if _f(s.get("call_chain_volume")) is not None and _f(s.get("put_chain_volume")) is not None]
        fresh = [s for s in with_cp if asof_ts - float(s["t"]) <= cap]
        if fresh:
            last = fresh[-1]
            cur_call, cur_put, now = _f(last.get("call_chain_volume")), _f(last.get("put_chain_volume")), float(last["t"])
        elif with_cp:
            stale_age = asof_ts - float(with_cp[-1]["t"])
    if v2:
        out["dir_pct_approach_lead_s"] = None
        out["dir_pct_approach_source"] = "none"
        try:
            from central_tech import dir5m_presample as _f4b

            out["dir_pct_approach_lead_s"] = _f4b.approach_lead_s(system, symbol, now=asof_ts)
            out["dir_pct_approach_source"] = _f4b.approach_source(system, symbol, now=asof_ts)
        except Exception:
            pass
    out["dir_pct_asof_epoch"] = asof_ts
    out["dir_pct_asof_at"] = iso_utc(asof_ts)
    out["dir_pct_now_chain_observed_at"] = iso_utc(now)
    out["dir_pct_prior_observed_at"] = None
    if now is None:
        if v2:
            # t204u: a pre-event chain exists but is older than the stage cap -> stale, not borrowed.
            out["dir_pct_missing_reason"] = "now_chain_stale" if stale_age is not None else "no_current_chain"
            out["dir_pct_now_chain_age_s"] = None if stale_age is None else round(stale_age, 3)
        return out
    out["dir_pct_now_chain_age_s"] = round(max(0.0, asof_ts - now), 3)
    cur_dir = _same_side_volume_share(cur_call, cur_put, side_tok)
    chain_samples = [
        s for s in samples
        if float(s["t"]) < now
        and not (_f(s.get("call_chain_volume")) == cur_call and _f(s.get("put_chain_volume")) == cur_put)
    ]
    now_exp = _expiry_iso(row)
    if not now_exp:
        last_exp = next((s for s in reversed(samples) if _expiry_iso(s)), None)
        now_exp = _expiry_iso(last_exp) if last_exp else ""
    chain_samples, expiry_mismatch = _filter_same_expiry(chain_samples, now_exp)
    w_lo, w_hi = _dir5m_prior_window_sec() if _strict_5m_window() else (None, None)
    kw = ({"min_age_sec": w_lo - 0.01, "max_age_sec": w_hi + 0.01, "max_dist_sec": 1e9}
          if w_lo is not None else {"max_dist_sec": _prior_5m_tolerance_sec()})
    share = lambda s: _same_side_share_from_sample(s, side_tok)  # noqa: E731
    prior = _pick_chain_prior_sample(chain_samples, now=now, horizon_sec=horizon_sec, field_get=share, **kw)
    window = ("strict" if w_lo is not None else "legacy") if prior is not None else None
    if prior is None and v2 and dir5m_fallback_enabled() and w_lo is not None:
        f_lo, f_hi = dir5m_fallback_window_sec()
        prior = _pick_chain_prior_sample(chain_samples, now=now, horizon_sec=horizon_sec, field_get=share,
                                         min_age_sec=f_lo - 0.01, max_age_sec=f_hi + 0.01, max_dist_sec=1e9)
        window = "fallback" if prior is not None else None
    if prior is None and v2 and w_lo is not None and open_short_in_window(now):
        prior = _pick_chain_prior_sample(chain_samples, now=now, horizon_sec=horizon_sec, field_get=share,
                                         min_age_sec=open_short_min_age_sec() - 0.01,
                                         max_age_sec=dir5m_fallback_window_sec()[0] + 0.01, max_dist_sec=1e9)
        window = "open_short" if prior is not None else None
    p_dir = share(prior) if prior is not None else None
    age = None if prior is None else max(0.0, now - float(prior["t"]))
    out["dir_pct_change_5m_prior_age_sec"] = age
    if prior is not None:
        pc, pp = _f(prior.get("call_chain_volume")), _f(prior.get("put_chain_volume"))
        if pc is not None:
            out["call_chain_volume_delta_5m"] = cur_call - pc
        if pp is not None:
            out["put_chain_volume_delta_5m"] = cur_put - pp
        out["chain_volume_delta_5m_prior_age_sec"] = age
    if cur_dir is not None and p_dir is not None:
        out["dir_pct_change_5m"] = cur_dir - p_dir
    if v2:
        if out["dir_pct_change_5m"] is not None:
            out["dir_pct_prior_age_s"] = age
            out["dir_pct_prior_window"] = window
            out["dir_pct_missing_reason"] = None
            out["dir_pct_prior_observed_at"] = iso_utc(float(prior["t"]))
        else:
            if expiry_mismatch and not any(_same_side_share_from_sample(s, side_tok) for s in chain_samples):
                out["dir_pct_missing_reason"] = "expiry_mismatch"
            else:
                out["dir_pct_missing_reason"] = _dir5m_missing_reason(
                    samples, chain_samples, now=now, cur_dir=cur_dir, side_tok=side_tok,
                    strict_lo=kw.get("min_age_sec"),
                )
    call_d, put_d = out["call_chain_volume_delta_5m"], out["put_chain_volume_delta_5m"]
    p_call = None if prior is None else _f(prior.get("call_chain_volume"))
    p_put = None if prior is None else _f(prior.get("put_chain_volume"))
    if side_tok == "PUT":
        out.update(same_side_chain_volume_delta_5m=put_d, opposite_side_chain_volume_delta_5m=call_d,
                   same_side_chain_volume=cur_put, same_side_chain_volume_5m_ago=p_put)
    else:
        out.update(same_side_chain_volume_delta_5m=call_d, opposite_side_chain_volume_delta_5m=put_d,
                   same_side_chain_volume=cur_call, same_side_chain_volume_5m_ago=p_call)
    out["direction_volume_pct_5m_ago"] = p_dir
    if cur_dir is not None:
        out["direction_volume_pct"] = cur_dir
    return out
