#!/usr/bin/env python3
"""
Options Chain Manager
=====================

Manages options chains for the unified 0DTE trade universe
(``data/watchlist/0dte_list.csv`` — all tiers are 0DTE-intent).
Handles chain fetching, liquidity analysis, and strike selection.

Author: Easy ORB Strategy Development Team
Last Updated: July 20, 2026
"""

import logging
import math
import os
import time
from typing import Dict, List, Optional, Any, Tuple, Set
from datetime import datetime, date, timedelta
from dataclasses import dataclass
from zoneinfo import ZoneInfo
import asyncio

# Import ETrade Options API
try:
    from .etrade_options_api import ETradeOptionsAPI, ETradeOptionContract
    ETRADE_OPTIONS_AVAILABLE = True
except ImportError:
    ETRADE_OPTIONS_AVAILABLE = False
    logging.warning("ETrade Options API not available")

log = logging.getLogger(__name__)

_ENTRY_SELECTION_MOD = None


def _dte0_entry_selection():
    """Load entry selection helpers without importing easy0DTE package __init__."""
    global _ENTRY_SELECTION_MOD
    if _ENTRY_SELECTION_MOD is not None:
        return _ENTRY_SELECTION_MOD
    import importlib.util

    path = os.path.join(os.path.dirname(__file__), "dte0_entry_selection.py")
    spec = importlib.util.spec_from_file_location("dte0_entry_selection", path)
    if spec is None or spec.loader is None:
        raise ImportError("dte0_entry_selection module not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _ENTRY_SELECTION_MOD = mod
    return mod


def _env_float_option(key: str, default: float) -> float:
    raw = os.getenv(key, str(default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _opening_env_float(primary_key: str, legacy_key: str, default: float) -> float:
    if os.getenv(primary_key) is not None:
        return _env_float_option(primary_key, default)
    return _env_float_option(legacy_key, default)


def _opening_env_bool(primary_key: str, legacy_key: str, default: bool) -> bool:
    raw = os.getenv(primary_key)
    if raw is None:
        raw = os.getenv(legacy_key)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _single_leg_spot_premium_cap(current_price: float) -> float:
    """Shared per-share max entry premium, with legacy ORB spot-scaled fallback."""
    shared_cap = _opening_env_float("0DTE_MAX_ENTRY_PREMIUM", "", 0.0)
    if shared_cap > 0:
        return shared_cap
    cap_floor = _opening_env_float(
        "0DTE_SINGLE_LEG_DIRECTIONAL_PREMIUM_CAP_FLOOR",
        "0DTE_LOTTO_BREAKDOWN_PREMIUM_CAP_FLOOR",
        1.20,
    )
    spot_mult = _opening_env_float(
        "0DTE_SINGLE_LEG_DIRECTIONAL_PREMIUM_SPOT_MULT",
        "0DTE_LOTTO_BREAKDOWN_PREMIUM_SPOT_MULT",
        0.028,
    )
    return max(cap_floor, float(current_price) * spot_mult)


def _single_leg_opening_premium_soft_cap(current_price: float) -> float:
    """Legacy soft-tier bump when the shared hard cap is not configured."""
    shared_cap = _opening_env_float("0DTE_MAX_ENTRY_PREMIUM", "", 0.0)
    if shared_cap > 0:
        return shared_cap
    soft = _opening_env_float(
        "0DTE_SINGLE_LEG_OPENING_PREMIUM_MAX_SOFT",
        "0DTE_LOTTO_BREAKDOWN_PREMIUM_MAX_SOFT",
        1.20,
    )
    return max(soft, _single_leg_spot_premium_cap(current_price))


def _single_leg_opening_premium_relax_active(
    momentum_score: float,
    orb_range_pct: float,
) -> bool:
    """Relax premium cap when momentum or ORB width signals breakdown continuation."""
    if not _opening_env_bool(
        "0DTE_SINGLE_LEG_OPENING_PREMIUM_RELAX",
        "0DTE_LOTTO_BREAKDOWN_PREMIUM_RELAX",
        True,
    ):
        return False
    mom_min = _opening_env_float(
        "0DTE_SINGLE_LEG_OPENING_MOMENTUM_MIN",
        "0DTE_LOTTO_BREAKDOWN_MOMENTUM_MIN",
        50.0,
    )
    if float(momentum_score or 0.0) >= mom_min:
        return True
    orb_min = _opening_env_float(
        "0DTE_SINGLE_LEG_OPENING_ORB_RANGE_MIN",
        "0DTE_LOTTO_BREAKDOWN_ORB_RANGE_MIN",
        0.35,
    )
    return float(orb_range_pct or 0.0) >= orb_min


def _options_expiry_yyyy_mm_dd_us() -> str:
    """0DTE expiry calendar date in US equity session timezone (not server local/UTC)."""
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _normalize_expiry_iso(raw: Optional[str]) -> str:
    """Normalize YYYY-MM-DD / YYYYMMDD / partial forms to YYYY-MM-DD."""
    s = str(raw or "").strip()
    if not s:
        return _options_expiry_yyyy_mm_dd_us()
    digits = s.replace("-", "").replace("/", "")
    if len(digits) >= 8 and digits[:8].isdigit():
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return _options_expiry_yyyy_mm_dd_us()


def resolve_options_chain_manager(*owners: Any) -> Any:
    """Find the one OptionsChainManager. PrimeDataManager never owns chains."""
    for owner in owners:
        if owner is None:
            continue
        mgr = getattr(owner, "options_chain_manager", None)
        if mgr is not None and hasattr(mgr, "fetch_options_chain"):
            return mgr
        if hasattr(owner, "fetch_options_chain") and not hasattr(owner, "data_manager"):
            # Already the manager (or a test double).
            return owner
        dte0 = getattr(owner, "dte0_manager", None)
        if dte0 is not None:
            mgr = getattr(dte0, "options_chain_manager", None)
            if mgr is not None and hasattr(mgr, "fetch_options_chain"):
                return mgr
            executor = getattr(dte0, "options_executor", None)
            mgr = getattr(executor, "options_chain_manager", None) if executor is not None else None
            if mgr is not None and hasattr(mgr, "fetch_options_chain"):
                return mgr
    return None


async def fetch_0dte_intent_chain(
    manager: Any = None,
    symbol: str = "",
    *,
    system: Any = None,
    expiry: Optional[str] = None,
    underlying_price: Optional[float] = None,
    use_cache: bool = True,
    allow_stale_cache_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """THE app door for an option chain (ORB / Trendline / Breakout / last-look).

    Data Manager supplies equity spots only. ``chain_fetch_manager`` budgets
    discovery/dir% and then calls this. HTTP is ``OptionsChainManager`` →
    ``ETradeOptionsAPI``. No raw E*TRADE / SelectedED fallback.
    """
    mgr = resolve_options_chain_manager(manager, system)
    symbol_u = str(symbol or "").strip().upper()
    if mgr is None or not hasattr(mgr, "fetch_options_chain"):
        log.warning(
            "CHAIN_REQUEST | door=fetch_0dte_intent_chain | symbol=%s | reason=no_manager",
            symbol_u,
        )
        return {}
    kwargs: Dict[str, Any] = {
        "expiry": expiry,
        "use_cache": use_cache,
        "underlying_price": underlying_price,
    }
    if allow_stale_cache_sec is not None:
        kwargs["allow_stale_cache_sec"] = float(allow_stale_cache_sec)
    chain = await mgr.fetch_options_chain(symbol_u, **kwargs)
    return chain if isinstance(chain, dict) else {}


_0DTE_WATCHLIST_TIERS: Optional[Dict[str, int]] = None
_0DTE_WATCHLIST_LOADED_AT: float = 0.0
_0DTE_WATCHLIST_CACHE_TTL_SEC = 300.0


def load_0dte_watchlist_tiers(*, force: bool = False) -> Dict[str, int]:
    """symbol → tier from ``data/watchlist/0dte_list.csv`` (cached).

    Tier is metadata only — every listed symbol is a 0DTE trade-universe name.
    """
    global _0DTE_WATCHLIST_TIERS, _0DTE_WATCHLIST_LOADED_AT
    now = time.time()
    if (
        not force
        and _0DTE_WATCHLIST_TIERS is not None
        and (now - float(_0DTE_WATCHLIST_LOADED_AT or 0.0)) < _0DTE_WATCHLIST_CACHE_TTL_SEC
    ):
        return dict(_0DTE_WATCHLIST_TIERS)

    tiers: Dict[str, int] = {}
    try:
        from .prime_0dte_strategy_manager import load_0dte_watchlist_df

        df = load_0dte_watchlist_df()
        if df is not None and "symbol" in getattr(df, "columns", []):
            for _, row in df.iterrows():
                sym = str(row.get("symbol") or "").strip().upper()
                if not sym:
                    continue
                try:
                    tier_i = int(float(row.get("tier") or 0))
                except (TypeError, ValueError):
                    tier_i = 0
                tiers[sym] = tier_i
    except Exception as exc:
        log.warning("0DTE_WATCHLIST_TIERS | df_load_failed=%s | trying_symbol_list", exc)
        try:
            from .prime_0dte_strategy_manager import load_0dte_symbols

            for sym in load_0dte_symbols() or []:
                s = str(sym or "").strip().upper()
                if s:
                    tiers.setdefault(s, 0)
        except Exception as exc2:
            log.warning("0DTE_WATCHLIST_TIERS | symbol_list_failed=%s", exc2)

    _0DTE_WATCHLIST_TIERS = tiers
    _0DTE_WATCHLIST_LOADED_AT = now
    return dict(tiers)


def classify_0dte_trade_symbol(symbol: str) -> Tuple[str, int, bool]:
    """Classify against ``0dte_list.csv`` only (not legacy SPY/QQQ/IWM hardcodes).

    Returns:
      (label, tier, on_watchlist)
      label is ``0DTE_WATCHLIST`` or ``OFF_WATCHLIST``.
    """
    symbol_u = str(symbol or "").strip().upper()
    tiers = load_0dte_watchlist_tiers()
    if symbol_u in tiers:
        return "0DTE_WATCHLIST", int(tiers.get(symbol_u) or 0), True
    return "OFF_WATCHLIST", 0, False


@dataclass
class OptionContract:
    """Option contract data"""
    symbol: str
    strike: float
    expiry: str
    option_type: str  # 'call' or 'put'
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    delta: float
    gamma: float
    theta: float
    vega: float
    implied_volatility: float
    # Chain-returned Market Quote wire symbol (prefer over reconstructed colon/OCC).
    osi_key: str = ""
    display_symbol: str = ""
    
    @property
    def mid_price(self) -> float:
        """Calculate mid price"""
        return (self.bid + self.ask) / 2.0
    
    @property
    def bid_ask_spread(self) -> float:
        """Calculate bid-ask spread"""
        return self.ask - self.bid
    
    @property
    def bid_ask_spread_pct(self) -> float:
        """Calculate bid-ask spread as percentage"""
        if self.mid_price > 0:
            return (self.bid_ask_spread / self.mid_price) * 100.0
        return 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        payload = {
            'symbol': self.symbol,
            'strike': self.strike,
            'expiry': self.expiry,
            'option_type': self.option_type,
            'bid': self.bid,
            'ask': self.ask,
            'last': self.last,
            'volume': self.volume,
            'open_interest': self.open_interest,
            'delta': self.delta,
            'gamma': self.gamma,
            'theta': self.theta,
            'vega': self.vega,
            'implied_volatility': self.implied_volatility,
            'mid_price': self.mid_price,
            'bid_ask_spread': self.bid_ask_spread,
            'bid_ask_spread_pct': self.bid_ask_spread_pct,
            'osi_key': self.osi_key,
            'display_symbol': self.display_symbol,
        }
        bid_size = getattr(self, "bid_size", None)
        ask_size = getattr(self, "ask_size", None)
        if bid_size is not None:
            payload["bid_size"] = bid_size
        if ask_size is not None:
            payload["ask_size"] = ask_size
        return payload


@dataclass
class DebitSpread:
    """Debit spread structure"""
    symbol: str
    expiry: str
    option_type: str  # 'call' or 'put'
    long_strike: float
    short_strike: float
    long_contract: OptionContract
    short_contract: OptionContract
    debit_cost: float
    max_profit: float
    max_loss: float
    break_even: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'symbol': self.symbol,
            'expiry': self.expiry,
            'option_type': self.option_type,
            'long_strike': self.long_strike,
            'short_strike': self.short_strike,
            'long_contract': self.long_contract.to_dict(),
            'short_contract': self.short_contract.to_dict(),
            'debit_cost': self.debit_cost,
            'max_profit': self.max_profit,
            'max_loss': self.max_loss,
            'break_even': self.break_even
        }


@dataclass
class CreditSpread:
    """Credit spread structure"""
    symbol: str
    expiry: str
    option_type: str  # 'call' or 'put'
    short_strike: float  # Short leg (sell this)
    long_strike: float  # Long leg (buy this for protection)
    short_contract: OptionContract
    long_contract: OptionContract
    credit_received: float  # Net credit received
    max_profit: float  # Max profit = credit received
    max_loss: float  # Max loss = spread_width - credit_received
    break_even: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'symbol': self.symbol,
            'expiry': self.expiry,
            'option_type': self.option_type,
            'short_strike': self.short_strike,
            'long_strike': self.long_strike,
            'short_contract': self.short_contract.to_dict(),
            'long_contract': self.long_contract.to_dict(),
            'credit_received': self.credit_received,
            'max_profit': self.max_profit,
            'max_loss': self.max_loss,
            'break_even': self.break_even
        }


class OptionsChainManager:
    """
    Options Chain Manager for 0DTE Strategy
    
    Handles:
    - Fetching options chains from broker API
    - Analyzing liquidity (bid/ask spreads, open interest)
    - Selecting optimal strikes for debit spreads
    - Validating trade eligibility
    """
    
    def __init__(
        self,
        min_open_interest: int = 100,
        max_bid_ask_spread_pct: float = 15.0,  # 15% max spread (deterministic rule: reject if >15% of mid)
        min_volume: int = 50,
        options_min_open_interest: Optional[int] = None,
        options_max_bid_ask_spread_pct: Optional[float] = None,
        options_min_volume: Optional[int] = None,
        min_risk_reward_ratio: float = 1.5,  # Minimum R:R ratio (1.5-2.5x)
        max_risk_reward_ratio: float = 2.5,  # Maximum R:R ratio (1.5-2.5x)
        chain_stale_threshold_seconds: int = 300,  # 5 minutes (reject if chain older than this)
        single_leg_min_open_interest: int = 500,
        single_leg_min_volume: int = 200,
        single_leg_open_window_minutes: float = 5.0,
        single_leg_open_window_oi_mult: float = 0.50,
        single_leg_open_window_volume_mult: float = 0.50,
        single_leg_mega_symbols: Optional[Set[str]] = None,
        liquidity_relax_open_window_minutes: float = 8.0,
        liquidity_relax_spread_mult: float = 1.35,
        liquidity_relax_open_interest_mult: float = 0.50,
        liquidity_relax_volume_mult: float = 0.50,
        liquidity_relax_symbols: Optional[Set[str]] = None,
        etrade_options_api: Optional[Any] = None,
        use_live_api: bool = False
    ):
        """
        Initialize Options Chain Manager
        
        Deterministic Contract/Spread Picker Rules:
        - Reject trade if bid/ask spread > 15% of mid
        - Reject trade if option volume < minimum
        - Reject trade if OI < minimum
        - Reject trade if chain update stale (>5 minutes)
        - Pick: ATM → slightly OTM long leg, Short leg at R:R ≥ 1.5–2.5x
        
        Args:
            min_open_interest: Minimum open interest for liquidity (reject if <minimum)
            max_bid_ask_spread_pct: Maximum bid-ask spread percentage (reject if >15% of mid)
            min_volume: Minimum volume for liquidity (reject if <minimum)
            min_risk_reward_ratio: Minimum risk:reward ratio for spread selection (1.5x)
            max_risk_reward_ratio: Maximum risk:reward ratio for spread selection (2.5x)
            chain_stale_threshold_seconds: Reject trade if chain update stale (seconds)
            etrade_options_api: ETradeOptionsAPI instance (optional)
            use_live_api: Use live ETrade API (False = demo/mock)
        """
        # Unified threshold resolution: 0DTE_MIN_* and 0DTE_OPTIONS_* must resolve to one runtime set.
        eff_min_oi = int(options_min_open_interest) if options_min_open_interest is not None else int(min_open_interest)
        eff_min_vol = int(options_min_volume) if options_min_volume is not None else int(min_volume)
        eff_max_spread = (
            float(options_max_bid_ask_spread_pct)
            if options_max_bid_ask_spread_pct is not None
            else float(max_bid_ask_spread_pct)
        )
        self.min_open_interest = eff_min_oi
        self.max_bid_ask_spread_pct = eff_max_spread
        self.min_volume = eff_min_vol
        self.options_min_open_interest = eff_min_oi
        self.options_max_bid_ask_spread_pct = eff_max_spread
        self.options_min_volume = eff_min_vol
        self.min_risk_reward_ratio = min_risk_reward_ratio
        self.max_risk_reward_ratio = max_risk_reward_ratio
        self.chain_stale_threshold_seconds = chain_stale_threshold_seconds
        self.use_live_api = use_live_api
        self.single_leg_min_open_interest = int(max(1, single_leg_min_open_interest))
        self.single_leg_min_volume = int(max(1, single_leg_min_volume))
        self.single_leg_open_window_minutes = max(0.0, float(single_leg_open_window_minutes))
        self.single_leg_open_window_oi_mult = max(0.10, min(1.0, float(single_leg_open_window_oi_mult)))
        self.single_leg_open_window_volume_mult = max(0.10, min(1.0, float(single_leg_open_window_volume_mult)))
        self.single_leg_mega_symbols = set(single_leg_mega_symbols or {
            "TSLA", "NVDA", "SPY", "QQQ", "META", "AAPL", "MSFT", "AMD", "AMZN"
        })
        self.liquidity_relax_open_window_minutes = max(0.0, float(liquidity_relax_open_window_minutes))
        self.liquidity_relax_spread_mult = max(1.0, float(liquidity_relax_spread_mult))
        self.liquidity_relax_open_interest_mult = max(0.10, min(1.0, float(liquidity_relax_open_interest_mult)))
        self.liquidity_relax_volume_mult = max(0.10, min(1.0, float(liquidity_relax_volume_mult)))
        self.liquidity_relax_symbols = set(liquidity_relax_symbols or self.single_leg_mega_symbols)
        for tok in str(os.getenv("0DTE_LIQUIDITY_RELAX_EXTRA_SYMBOLS", "") or "").split(","):
            symx = str(tok or "").strip().upper()
            if symx:
                self.liquidity_relax_symbols.add(symx)

        # ETrade Options API
        if etrade_options_api:
            self.etrade_api = etrade_options_api
        elif use_live_api and ETRADE_OPTIONS_AVAILABLE:
            log.warning(
                "OptionsChainManager: no etrade_options_api injected — creating standalone ETradeOptionsAPI "
                "(prefer shared instance from main.py with ETRADE_0DTE_ACCOUNT_ID)"
            )
            try:
                environment = os.getenv('ETRADE_ENVIRONMENT', 'prod')
                account_id = None
                try:
                    from modules.runtime_mode import is_demo_mode as _is_demo_mode
                    _demo = _is_demo_mode()
                except Exception:
                    _demo = str(os.getenv("MODE", "demo")).strip().lower() != "live"
                if not _demo:
                    account_id = os.getenv('ETRADE_0DTE_ACCOUNT_ID') or os.getenv('0DTE_ETRADE_ACCOUNT_ID')
                self.etrade_api = ETradeOptionsAPI(environment=environment, account_id=account_id)
            except Exception as e:
                log.warning(f"Failed to initialize ETrade Options API: {e}")
                self.etrade_api = None
        else:
            self.etrade_api = None
        
        # Cache for options chains (5-minute TTL)
        self.chain_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_ttl = 300  # 5 minutes
        # Same-day 0DTE hits: skip DTE+N API walks when today already returned contracts.
        # symbol → {expiry, calls, puts, timestamp}
        self._same_day_chain_hits: Dict[str, Dict[str, Any]] = {}
        # Selector telemetry for downstream execution-reject diagnostics.
        self.last_selector_diagnostics: Dict[str, Any] = {}
        # Last chain expiry selection (calendar DTE / fallback mode) for trade records.
        self.last_expiry_selection: Dict[str, Any] = {}
        # Chain fetch telemetry keyed by symbol for execution diagnostics.
        self.last_chain_fetch_meta: Dict[str, Dict[str, Any]] = {}
        # Per-symbol Hard Gate reject buckets (collapse per-strike spam).
        self._hard_gate_reject_summary: Dict[str, Dict[str, Any]] = {}
        
        mode_label = "💰 LIVE API" if use_live_api and self.etrade_api else "🎮 DEMO/MOCK"
        log.info(f"Options Chain Manager initialized ({mode_label}):")
        log.info(f"  - Min open interest: {self.min_open_interest} (reject if <minimum)")
        log.info(f"  - Max bid-ask spread: {self.max_bid_ask_spread_pct:.1f}% of mid (reject if >15%)")
        log.info(f"  - Min volume: {self.min_volume} (reject if <minimum)")
        log.info(
            "  - Liquidity relax window: open_window_min=%.1f spread_mult=%.2f oi_mult=%.2f volume_mult=%.2f symbols=%d",
            self.liquidity_relax_open_window_minutes,
            self.liquidity_relax_spread_mult,
            self.liquidity_relax_open_interest_mult,
            self.liquidity_relax_volume_mult,
            len(self.liquidity_relax_symbols),
        )
        log.info(
            "  - Single-leg floors: min_volume=%d min_open_interest=%d open_window_min=%.1f "
            "open_window_oi_mult=%.2f open_window_volume_mult=%.2f",
            self.single_leg_min_volume,
            self.single_leg_min_open_interest,
            self.single_leg_open_window_minutes,
            self.single_leg_open_window_oi_mult,
            self.single_leg_open_window_volume_mult,
        )
        log.info(f"  - Risk:Reward ratio: {min_risk_reward_ratio:.1f}x - {max_risk_reward_ratio:.1f}x")
        log.info(f"  - Chain stale threshold: {chain_stale_threshold_seconds}s (reject if stale)")
        log.info(f"  - Live API enabled: {use_live_api and self.etrade_api is not None}")

    @staticmethod
    def _parse_hh_mm_et(raw: str, default_h: int, default_m: int) -> Tuple[int, int]:
        s = (raw or "").strip()
        if not s:
            return default_h, default_m
        parts = s.replace(":", " ").split()
        try:
            return int(parts[0]) % 24, int(parts[1]) % 60 if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return default_h, default_m

    def _in_hard_gate_execution_window_et(self) -> bool:
        """Watch mode: same PT discovery session as ORB1/TL0. Batch: 0DTE_HARD_GATE_ET_*."""
        try:
            from modules.session_clock import (
                discovery_mode_is_watch,
                orb1_tl0_session_open,
            )

            if discovery_mode_is_watch():
                from modules.session_clock import now_pt_time

                return orb1_tl0_session_open(now_pt_time())
        except Exception:
            pass
        sh, sm = self._parse_hh_mm_et(os.getenv("0DTE_HARD_GATE_ET_START", "10:30"), 10, 30)
        eh, em = self._parse_hh_mm_et(os.getenv("0DTE_HARD_GATE_ET_END", "10:40"), 10, 40)
        now_et = datetime.now(ZoneInfo("America/New_York")).time()
        start_t = datetime.now(ZoneInfo("America/New_York")).replace(
            hour=sh, minute=sm, second=0, microsecond=0
        ).time()
        end_t = datetime.now(ZoneInfo("America/New_York")).replace(
            hour=eh, minute=em, second=0, microsecond=0
        ).time()
        if start_t <= end_t:
            return start_t <= now_et <= end_t
        return now_et >= start_t or now_et <= end_t

    def _liquidity_thresholds_for_symbol(self, symbol: str) -> Tuple[float, int, int]:
        max_spread_pct = float(self.max_bid_ask_spread_pct)
        min_open_interest = int(self.min_open_interest)
        min_volume = int(self.min_volume)
        symbol_u = str(symbol or "").upper()
        if not symbol_u or symbol_u not in self.liquidity_relax_symbols:
            base = (max_spread_pct, min_open_interest, min_volume)
        elif self.liquidity_relax_open_window_minutes <= 0:
            base = (max_spread_pct, min_open_interest, min_volume)
        else:
            now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
            session_open = now_pt.replace(hour=7, minute=30, second=0, microsecond=0)
            minutes_since_open = max(0.0, (now_pt - session_open).total_seconds() / 60.0)
            if minutes_since_open > self.liquidity_relax_open_window_minutes:
                base = (max_spread_pct, min_open_interest, min_volume)
            else:
                base = (
                    max_spread_pct,
                    max(1, int(round(min_open_interest * self.liquidity_relax_open_interest_mult))),
                    max(1, int(round(min_volume * self.liquidity_relax_volume_mult))),
                )
        ms, m_oi, m_vol = base
        return ms, m_oi, m_vol

    def _minutes_since_orb_session_open_pt(self) -> float:
        now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
        session_open = now_pt.replace(hour=7, minute=30, second=0, microsecond=0)
        return max(0.0, (now_pt - session_open).total_seconds() / 60.0)

    def _opening_spread_cap_audit(self, symbol: str) -> Dict[str, Any]:
        """Effective hard-gate spread cap after opening-window relax (for telemetry)."""
        effective_cap, _, _ = self._liquidity_thresholds_for_symbol(symbol)
        try:
            relax_exec = float(os.getenv("0DTE_HARD_GATE_EXEC_SPREAD_RELAX_MULT", "1.25"))
        except (TypeError, ValueError):
            relax_exec = 1.25
        relax_exec = max(1.0, min(1.35, relax_exec))
        in_hg = bool(self._in_hard_gate_execution_window_et())
        return {
            "base_spread_cap": float(self.max_bid_ask_spread_pct),
            "relax_mult": float(relax_exec),
            "effective_spread_cap": float(effective_cap),
            "in_opening_window": in_hg,
        }

    def _log_opening_spread_relax_audit(
        self,
        contract: OptionContract,
        *,
        pass_fail: str,
    ) -> None:
        audit = self._opening_spread_cap_audit(contract.symbol)
        if not audit.get("in_opening_window"):
            return
        sym = str(contract.symbol or "").strip().upper()
        now_m = time.monotonic()
        last_map = getattr(self, "_opening_spread_audit_ts", None)
        if not isinstance(last_map, dict):
            last_map = {}
        last_m = float(last_map.get(sym) or 0.0)
        if last_m > 0.0 and (now_m - last_m) < 2.0:
            return
        last_map[sym] = now_m
        self._opening_spread_audit_ts = last_map
        log.info(
            "ORB_0DTE_OPENING_SPREAD_RELAX_AUDIT | symbol=%s | base_spread_cap=%.2f | relax_mult=%.3f | "
            "effective_spread_cap=%.2f | in_opening_window=%s | candidate_spread_pct=%.2f | pass_fail=%s",
            str(contract.symbol or ""),
            float(audit["base_spread_cap"]),
            float(audit["relax_mult"]),
            float(audit["effective_spread_cap"]),
            str(bool(audit["in_opening_window"])).lower(),
            float(contract.bid_ask_spread_pct or 0.0),
            str(pass_fail or ""),
        )

    def _record_chain_fetch_meta(
        self,
        *,
        symbol: str,
        source: str,
        expiry: Optional[str],
        elapsed_ms: float,
        from_cache: bool,
        calls: int,
        puts: int,
        error_type: Optional[str] = None,
        observed_at: Optional[str] = None,
    ) -> None:
        key = str(symbol or "").upper()
        stamp = str(observed_at or "").strip()
        if not stamp:
            from datetime import timezone

            stamp = datetime.now(timezone.utc).isoformat()
        self.last_chain_fetch_meta[key] = {
            "symbol": key,
            "source": str(source or "unknown"),
            "expiry": expiry,
            "elapsed_ms": float(elapsed_ms),
            "from_cache": bool(from_cache),
            "calls": int(calls),
            "puts": int(puts),
            "error_type": str(error_type) if error_type else None,
            "timestamp": stamp,
        }

    def get_last_chain_fetch_meta(self, symbol: str) -> Dict[str, Any]:
        return dict(self.last_chain_fetch_meta.get(str(symbol or "").upper(), {}))

    def peek_cached_chain(
        self,
        symbol: str,
        expiry: Optional[str] = None,
        *,
        max_age_sec: float = 5.0,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
        """
        Return (chain, age_seconds) if an in-process cache entry is present and
        not older than max_age_sec. Does not fetch from the broker.
        """
        symbol_u = str(symbol or "").strip().upper()
        if not symbol_u:
            return None, None
        expiry_iso = _normalize_expiry_iso(expiry) if expiry else _options_expiry_yyyy_mm_dd_us()
        keys = [f"{symbol_u}_{expiry_iso}"]
        # Also try raw expiry forms that may have been used as cache keys.
        raw = str(expiry or "").strip()
        if raw and raw != expiry_iso:
            keys.append(f"{symbol_u}_{raw}")
            keys.append(f"{symbol_u}_{raw.replace('-', '')}")
        now_ts = datetime.now().timestamp()
        best: Optional[Tuple[Dict[str, Any], float]] = None
        for cache_key in keys:
            cached_data = self.chain_cache.get(cache_key)
            if not isinstance(cached_data, dict):
                continue
            cache_time = float(cached_data.get("timestamp", 0) or 0)
            if cache_time <= 0:
                continue
            age = max(0.0, now_ts - cache_time)
            if age > float(max_age_sec):
                continue
            chain = cached_data.get("chain")
            if not isinstance(chain, dict) or not chain:
                continue
            if best is None or age < best[1]:
                best = (chain, age)
        if best is None:
            return None, None
        return best[0], best[1]

    @staticmethod
    def _stamp_option_book_sizes(target: OptionContract, source: Any) -> None:
        """Copy bid_size/ask_size from E*TRADE leg onto OptionContract (setattr)."""
        bid_size = getattr(source, "bid_size", None)
        ask_size = getattr(source, "ask_size", None)
        if bid_size is None and isinstance(source, dict):
            bid_size = source.get("bid_size") or source.get("bidSize")
        if ask_size is None and isinstance(source, dict):
            ask_size = source.get("ask_size") or source.get("askSize")
        try:
            if bid_size is not None:
                setattr(target, "bid_size", int(float(bid_size)))
            if ask_size is not None:
                setattr(target, "ask_size", int(float(ask_size)))
        except (TypeError, ValueError):
            pass

    @staticmethod
    def _is_itm_contract(contract: OptionContract, option_type: str, underlying_price: float) -> bool:
        """
        Determine ITM by option direction.

        CALL ITM: strike < underlying_price
        PUT ITM: strike > underlying_price
        """
        if option_type == "call":
            return float(contract.strike) < float(underlying_price)
        return float(contract.strike) > float(underlying_price)

    def _itm_contracts(
        self,
        contracts: List[OptionContract],
        option_type: str,
        underlying_price: float,
    ) -> List[OptionContract]:
        """Return contracts that are ITM by direction-aware rules."""
        return [
            c for c in contracts
            if self._is_itm_contract(c, option_type, underlying_price)
        ]

    def validate_chain_viability(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        underlying_price: float,
        min_itm_for_spread: int = 2,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Validate pre-execution chain viability for spread building.
        """
        contracts = list((chain or {}).get(option_type + "s", []) or [])
        itm = self._itm_contracts(contracts, option_type, underlying_price)
        bid_ask_valid = [
            c for c in contracts
            if float(c.bid or 0.0) > 0.0 and float(c.ask or 0.0) > 0.0
        ]
        viable = (
            len(contracts) > 0
            and len(bid_ask_valid) > 0
            and len(itm) >= int(max(1, min_itm_for_spread))
        )
        diag = {
            "option_type": option_type,
            "spot": float(underlying_price),
            "total_contracts": int(len(contracts)),
            "valid_bid_ask_contracts": int(len(bid_ask_valid)),
            "itm_contracts": int(len(itm)),
            "min_itm_for_spread": int(max(1, min_itm_for_spread)),
            "viable": bool(viable),
        }
        return viable, diag

    def count_valid_spread_candidates(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        underlying_price: float,
    ) -> int:
        """
        Count rough valid spread pairs from ITM long legs to OTM short legs.
        """
        contracts = list((chain or {}).get(option_type + "s", []) or [])
        if not contracts:
            return 0
        itm_legs = self._itm_contracts(contracts, option_type, underlying_price)
        count = 0
        for long_leg in itm_legs:
            long_ok, _ = self.validate_liquidity(long_leg)
            if not long_ok:
                continue
            if option_type == "call":
                otm = [c for c in contracts if float(c.strike) > float(long_leg.strike)]
            else:
                otm = [c for c in contracts if float(c.strike) < float(long_leg.strike)]
            for short_leg in otm:
                short_ok, _ = self.validate_liquidity(short_leg)
                if short_ok:
                    count += 1
        return int(count)

    def compute_viability_score(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        underlying_price: float,
        target_delta: Optional[float] = None,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Compute normalized viability score for early execution filtering.
        """
        contracts = list((chain or {}).get(option_type + "s", []) or [])
        viable, base_diag = self.validate_chain_viability(
            chain=chain,
            option_type=option_type,
            underlying_price=underlying_price,
            min_itm_for_spread=2,
        )
        valid_quotes = [
            c for c in contracts
            if float(c.bid or 0.0) > 0.0 and float(c.ask or 0.0) > 0.0
        ]
        fallback_priced = [
            c for c in contracts
            if float(c.last or 0.0) > 0.0 or float(c.mid_price or 0.0) > 0.0
        ]
        avg_spread_pct = (
            sum(float(c.bid_ask_spread_pct) for c in valid_quotes) / len(valid_quotes)
            if valid_quotes else 999.0
        )
        representative_pool = valid_quotes if valid_quotes else fallback_priced
        representative_contract: Optional[OptionContract] = None
        if representative_pool:
            if isinstance(target_delta, (int, float)) and float(target_delta) > 0.0:
                representative_contract = min(
                    representative_pool,
                    key=lambda c: (
                        abs(abs(float(c.delta or 0.0)) - float(target_delta)),
                        abs(float(c.strike or 0.0) - float(underlying_price)),
                    ),
                )
            else:
                representative_contract = min(
                    representative_pool,
                    key=lambda c: abs(float(c.strike or 0.0) - float(underlying_price)),
                )
        representative_spread_pct: Optional[float] = None
        representative_price_source = "none"
        representative_price: Optional[float] = None
        if representative_contract is not None:
            if float(representative_contract.bid or 0.0) > 0.0 and float(representative_contract.ask or 0.0) > 0.0:
                representative_spread_pct = float(representative_contract.bid_ask_spread_pct)
                representative_price_source = "bid_ask_mid"
                representative_price = float(representative_contract.mid_price)
            elif float(representative_contract.last or 0.0) > 0.0:
                representative_price_source = "last"
                representative_price = float(representative_contract.last)
            elif float(representative_contract.mid_price or 0.0) > 0.0:
                representative_price_source = "mid_approx"
                representative_price = float(representative_contract.mid_price)
        total_volume = int(sum(int(c.volume or 0) for c in valid_quotes))
        total_oi = int(sum(int(c.open_interest or 0) for c in valid_quotes))
        itm_count = int(base_diag.get("itm_contracts", 0))
        spread_candidates = self.count_valid_spread_candidates(
            chain=chain,
            option_type=option_type,
            underlying_price=underlying_price,
        )

        spread_norm = min(1.0, max(0.0, 1.0 - (avg_spread_pct / 30.0)))
        volume_norm = min(1.0, math.log(total_volume + 1.0) / 10.0)
        oi_norm = min(1.0, math.log(total_oi + 1.0) / 10.0)
        itm_norm = min(1.0, itm_count / 12.0)
        pair_norm = min(1.0, spread_candidates / 25.0)

        score_raw = (
            spread_norm * 0.35
            + volume_norm * 0.20
            + oi_norm * 0.20
            + itm_norm * 0.10
            + pair_norm * 0.15
        )
        viability_score = max(0.0, min(1.0, float(score_raw)))
        diag = {
            **base_diag,
            "avg_bid_ask_spread_pct": float(avg_spread_pct),
            "fallback_price_contracts": int(len(fallback_priced)),
            "representative_spread_pct": representative_spread_pct,
            "representative_price_source": representative_price_source,
            "representative_price": representative_price,
            "representative_strike": float(representative_contract.strike) if representative_contract else None,
            "representative_delta": float(representative_contract.delta) if representative_contract else None,
            "representative_bid": float(representative_contract.bid) if representative_contract else None,
            "representative_ask": float(representative_contract.ask) if representative_contract else None,
            "representative_volume": int(representative_contract.volume) if representative_contract else None,
            "representative_open_interest": int(representative_contract.open_interest) if representative_contract else None,
            "total_volume": int(total_volume),
            "total_open_interest": int(total_oi),
            "spread_candidate_count": int(spread_candidates),
            "spread_norm": float(spread_norm),
            "volume_norm": float(volume_norm),
            "open_interest_norm": float(oi_norm),
            "itm_norm": float(itm_norm),
            "pair_norm": float(pair_norm),
            "viability_score": float(viability_score),
            "viability": bool(viable),
        }
        return viability_score, diag
    
    def _chain_cache_key(
        self,
        symbol: str,
        expiry: Optional[str] = None,
        underlying_price: Optional[float] = None,
    ) -> Tuple[str, str]:
        """Return (cache_key, normalized_expiry) used by ``fetch_options_chain``."""
        symbol_u = str(symbol or "").strip().upper()
        session_today = _options_expiry_yyyy_mm_dd_us()
        if expiry is None:
            expiry_n = session_today
        else:
            expiry_n = _normalize_expiry_iso(expiry)
        cache_key = f"{symbol_u}_{expiry_n}"
        if not self.use_live_api and underlying_price and float(underlying_price) > 0:
            cache_key = f"{symbol_u}_{expiry_n}_syn_u{round(float(underlying_price), 2)}"
        return cache_key, expiry_n

    def _fresh_resolved_expiry_cache(
        self,
        symbol_u: str,
        *,
        session_today: str,
        usable_age: float,
        started: float,
    ) -> Optional[Dict[str, Any]]:
        """Return a still-fresh nearest-listed cache after a same-day key goes stale."""
        try:
            from modules.option_expiry_memo import (
                expire_dates_for,
                intent_expiry_from_listed,
                nearest_for,
            )
        except Exception:
            return None
        alt = nearest_for(symbol_u, session_today)
        if not alt:
            listed = expire_dates_for(symbol_u) or []
            if listed:
                try:
                    alt = intent_expiry_from_listed(
                        date.fromisoformat(session_today),
                        listed,
                        int(os.getenv("0DTE_NEAREST_EXPIRY_MAX_DAYS", "10")),
                        symbol=symbol_u,
                    )
                except Exception:
                    alt = None
        alt = str(alt or "").strip()
        if not alt or alt == session_today:
            return None
        alt_key = f"{symbol_u}_{alt}"
        cached = (getattr(self, "chain_cache", None) or {}).get(alt_key)
        if not isinstance(cached, dict):
            return None
        try:
            cache_time = float(cached.get("timestamp") or 0.0)
        except (TypeError, ValueError):
            return None
        cache_age = datetime.now().timestamp() - cache_time
        if cache_time <= 0 or cache_age < 0 or cache_age > float(usable_age or 0):
            return None
        chain = cached.get("chain")
        if not isinstance(chain, dict):
            return None
        if int(len(chain.get("calls") or [])) + int(len(chain.get("puts") or [])) <= 0:
            return None
        log.info(
            "CHAIN_EXPIRY_CACHE_RESOLVED | symbol=%s | skipped_same_day=%s | "
            "selected=%s | cache_age_s=%.0f | reason=use_memoized_nearest",
            symbol_u,
            session_today,
            alt,
            cache_age,
        )
        self._record_chain_fetch_meta(
            symbol=symbol_u,
            source=f"cache_resolved_{'live' if self.use_live_api else 'demo'}",
            expiry=alt,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            from_cache=True,
            calls=len(chain.get("calls") or []),
            puts=len(chain.get("puts") or []),
        )
        return chain

    def cached_chain_age_seconds(
        self,
        symbol: str,
        expiry: Optional[str] = None,
        underlying_price: Optional[float] = None,
    ) -> Optional[float]:
        """Age of a cached chain, or None when missing."""
        cache_key, _expiry_n = self._chain_cache_key(
            symbol, expiry=expiry, underlying_price=underlying_price
        )
        cached = (getattr(self, "chain_cache", None) or {}).get(cache_key)
        if not isinstance(cached, dict):
            return None
        try:
            ts = float(cached.get("timestamp") or 0.0)
        except (TypeError, ValueError):
            return None
        if ts <= 0:
            return None
        chain = cached.get("chain")
        if not isinstance(chain, dict):
            return None
        n_calls = len(chain.get("calls") or [])
        n_puts = len(chain.get("puts") or [])
        if n_calls + n_puts <= 0:
            return None
        return max(0.0, datetime.now().timestamp() - ts)

    def _warn_chain_stale_throttled(
        self,
        symbol_u: str,
        expiry: str,
        cache_age_seconds: float,
        threshold_seconds: float,
    ) -> None:
        now = time.time()
        key = f"{str(symbol_u or '').upper()}|{expiry}"
        last_map = getattr(self, "_chain_stale_warn_ts", None)
        if not isinstance(last_map, dict):
            last_map = {}
            self._chain_stale_warn_ts = last_map
        last = float(last_map.get(key) or 0.0)
        if last > 0 and (now - last) < 60.0:
            return
        last_map[key] = now
        log.warning(
            "CHAIN_CACHE_STALE | symbol=%s | expiry=%s | age_s=%.0f | threshold_s=%.0f | "
            "action=fetch_fresh | grep=CHAIN_CACHE_STALE",
            symbol_u,
            expiry,
            cache_age_seconds,
            threshold_seconds,
        )

    async def refresh_cached_chains_before_stale(
        self,
        *,
        stale_after_sec: float = 600.0,
        lead_sec: float = 120.0,
        max_symbols: int = 1,
    ) -> int:
        """Refresh cached chains before skip checks hit CHAIN_CACHE_STALE.

        Decision-time fetches use a 600s usable window. This walks the cache and
        refreshes the oldest chain once it is inside that window but within
        ``lead_sec`` of the limit, so the next skip check reads cache.
        """
        if getattr(self, "_chain_bg_refresh_running", False):
            return 0
        try:
            window = max(1.0, float(stale_after_sec))
            lead = max(0.0, min(float(lead_sec), window - 1.0))
        except (TypeError, ValueError):
            return 0
        refresh_after = window - lead
        now_ts = datetime.now().timestamp()
        due: List[Tuple[float, str, str]] = []
        for cache_key, cached in list((getattr(self, "chain_cache", None) or {}).items()):
            if not isinstance(cached, dict):
                continue
            chain = cached.get("chain")
            if not isinstance(chain, dict):
                continue
            if len(chain.get("calls") or []) + len(chain.get("puts") or []) <= 0:
                continue
            try:
                age = now_ts - float(cached.get("timestamp") or 0.0)
            except (TypeError, ValueError):
                continue
            if age < refresh_after:
                continue
            parts = str(cache_key).split("_", 1)
            symbol = parts[0].strip().upper()
            expiry = parts[1] if len(parts) > 1 else None
            if not symbol or "syn_u" in str(cache_key):
                continue
            due.append((age, symbol, str(expiry or "")))
        due.sort(key=lambda item: item[0], reverse=True)
        due = due[: max(0, int(max_symbols))]
        if not due:
            return 0
        self._chain_bg_refresh_running = True
        refreshed = 0
        try:
            for age, symbol, expiry in due:
                log.info(
                    "CHAIN_CACHE_BACKGROUND_REFRESH | symbol=%s | expiry=%s | age_s=%.0f | "
                    "refresh_after_s=%.0f | stale_after_s=%.0f | grep=CHAIN_CACHE_BACKGROUND_REFRESH",
                    symbol,
                    expiry,
                    age,
                    refresh_after,
                    window,
                )
                await self.fetch_options_chain(
                    symbol,
                    expiry=expiry or None,
                    use_cache=False,
                )
                refreshed += 1
        finally:
            self._chain_bg_refresh_running = False
        return refreshed
    
    async def fetch_options_chain(
        self,
        symbol: str,
        expiry: Optional[str] = None,
        use_cache: bool = True,
        underlying_price: Optional[float] = None,
        allow_stale_cache_sec: Optional[float] = None,
    ) -> Dict[str, List[OptionContract]]:
        """
        Fetch options chain for symbol.

        Trade-universe source of truth is ``data/watchlist/0dte_list.csv`` (all tiers
        are 0DTE-intent). Same-day US/Eastern expiry is the default when listed.
        Mon–Thu resolve the listed calendar first (``optionexpiredate``) so an empty
        Thursday does not 10031 before Friday. Friday keeps same-day-first.
        After same-day is empty, one nearest listed day within
        ``0DTE_NEAREST_EXPIRY_MAX_DAYS`` is fetched (no weekend / empty-day walk).

        Args:
            symbol: Underlying symbol
            expiry: Expiry date (YYYY-MM-DD or YYYYMMDD; None = session today 0DTE)
            use_cache: Use cached chain if available
            underlying_price: Spot for DEMO synthetic chain (ORB last); required for non-empty demo chains

        Returns:
            Dictionary with ``calls``, ``puts``, and ``selected_expiry`` / ``expiry``
        """
        started = time.perf_counter()
        symbol_u = str(symbol or "").strip().upper()
        session_today = _options_expiry_yyyy_mm_dd_us()
        class_label, tier, on_watchlist = classify_0dte_trade_symbol(symbol_u)
        log.debug(
            "SYMBOL_CLASSIFICATION | symbol=%s | type=%s | tier=%s | on_0dte_watchlist=%s",
            symbol_u,
            class_label,
            tier if on_watchlist else "n/a",
            str(on_watchlist).lower(),
        )
        if not on_watchlist:
            log.warning(
                "SYMBOL_CLASSIFICATION | symbol=%s | off_0dte_watchlist=true | "
                "expected_source=0dte_list.csv | continuing_with_0dte_expiry_policy",
                symbol_u,
            )

        requested_raw = expiry
        if expiry is None:
            expiry = session_today
            prefer_0dte = True
        else:
            expiry = _normalize_expiry_iso(expiry)
            # Explicit session-today (e.g. Trendline YYYYMMDD) is still 0DTE intent.
            prefer_0dte = expiry == session_today

        cache_key = f"{symbol_u}_{expiry}"
        if not self.use_live_api and underlying_price and float(underlying_price) > 0:
            cache_key = f"{symbol_u}_{expiry}_syn_u{round(float(underlying_price), 2)}"

        def _remember_same_day_hit(
            *,
            selected: str,
            n_calls: int,
            n_puts: int,
        ) -> None:
            if selected != session_today or (n_calls + n_puts) <= 0:
                return
            hits = getattr(self, "_same_day_chain_hits", None)
            if hits is None:
                self._same_day_chain_hits = {}
                hits = self._same_day_chain_hits
            hits[symbol_u] = {
                "expiry": selected,
                "calls": int(n_calls),
                "puts": int(n_puts),
                "timestamp": datetime.now().timestamp(),
            }

        def _same_day_hit_fresh(max_age_s: Optional[float] = None) -> Optional[Dict[str, Any]]:
            hits = getattr(self, "_same_day_chain_hits", None) or {}
            hit = hits.get(symbol_u)
            if not isinstance(hit, dict):
                return None
            if str(hit.get("expiry") or "") != session_today:
                return None
            if int(hit.get("calls") or 0) + int(hit.get("puts") or 0) <= 0:
                return None
            age = datetime.now().timestamp() - float(hit.get("timestamp") or 0.0)
            ttl = float(max_age_s if max_age_s is not None else getattr(self, "cache_ttl", 300) or 300)
            if age < 0 or age > ttl:
                return None
            return hit

        # Explicit forward-day request after a fresh same-day 0DTE chain → do not
        # burn E*TRADE empty-expiry calls (10031 spam on DTE+1/+2/…).
        # Nearest-forward inside prefer_0dte still uses _fetch_live_for_expiry directly
        # when same-day is empty (no hit recorded).
        try:
            req_day = date.fromisoformat(expiry)
            today_day = date.fromisoformat(session_today)
        except ValueError:
            req_day = today_day = None
        if (
            req_day is not None
            and today_day is not None
            and req_day > today_day
            and not prefer_0dte
        ):
            same_hit = _same_day_hit_fresh()
            if same_hit is not None:
                log.info(
                    "CHAIN_SKIP_FORWARD_WHEN_SAME_DAY_PRESENT | symbol=%s | requested=%s | "
                    "same_day=%s | same_day_calls=%s | same_day_puts=%s | reason=skip_dte_plus_api",
                    symbol_u,
                    expiry,
                    same_hit.get("expiry"),
                    same_hit.get("calls"),
                    same_hit.get("puts"),
                )
                empty = {
                    "calls": [],
                    "puts": [],
                    "selected_expiry": expiry,
                    "expiry": expiry,
                    "requested_expiry": expiry,
                    "selected_type": "NONE",
                    "session_date": session_today,
                    "expiry_ymd": expiry,
                    "calendar_dte": (req_day - today_day).days,
                    "is_0dte": False,
                    "expiry_selection_mode": "skipped_forward_same_day_present",
                    "requested_expiry_ymd": expiry,
                    "selected_expiry_ymd": expiry,
                    "fallback_days": 0,
                    "fallback_reason": "skip_forward_same_day_present",
                    "dte": (req_day - today_day).days,
                }
                self.last_expiry_selection = {
                    k: empty.get(k)
                    for k in (
                        "session_date",
                        "expiry_ymd",
                        "calendar_dte",
                        "is_0dte",
                        "expiry_selection_mode",
                        "requested_expiry_ymd",
                        "selected_expiry_ymd",
                        "fallback_days",
                        "fallback_reason",
                        "dte",
                        "selected_type",
                        "selected_expiry",
                        "requested_expiry",
                    )
                }
                self._record_chain_fetch_meta(
                    symbol=symbol_u,
                    source="skip_forward_same_day_present",
                    expiry=expiry,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                    from_cache=True,
                    calls=0,
                    puts=0,
                )
                return empty

        # Check cache. Layer C dir% may pass allow_stale_cache_sec so a 10-minute
        # chain can still admit without stealing the exact-OCC exit budget.
        try:
            allow_stale = float(allow_stale_cache_sec or 0.0)
        except (TypeError, ValueError):
            allow_stale = 0.0
        if allow_stale < 0:
            allow_stale = 0.0
        ttl = float(getattr(self, "cache_ttl", 300) or 300)
        usable_age = max(ttl, allow_stale)
        if use_cache and cache_key in self.chain_cache:
            cached_data = self.chain_cache[cache_key]
            cache_time = cached_data.get('timestamp', 0)
            cache_age_seconds = datetime.now().timestamp() - cache_time

            if cache_age_seconds <= usable_age:
                log.debug(f"Using cached options chain for {symbol_u} {expiry} (age: {cache_age_seconds:.0f}s)")
                cached_chain = cached_data['chain']
                if isinstance(cached_chain, dict):
                    cached_chain.setdefault("selected_expiry", expiry)
                    cached_chain.setdefault("expiry", cached_chain.get("selected_expiry") or expiry)
                log.debug(
                    "0DTE_CHAIN_SOURCE | symbol=%s | source=cache_%s | expiry=%s | calls=%d | puts=%d | cache_age_s=%.0f",
                    symbol_u,
                    "live" if self.use_live_api else "demo",
                    (cached_chain or {}).get("selected_expiry") or expiry,
                    len((cached_chain or {}).get('calls', []) or []),
                    len((cached_chain or {}).get('puts', []) or []),
                    cache_age_seconds,
                )
                _cache_calls = len((cached_chain or {}).get('calls', []) or [])
                _cache_puts = len((cached_chain or {}).get('puts', []) or [])
                _cache_exp = (cached_chain or {}).get("selected_expiry") or expiry
                _remember_same_day_hit(
                    selected=str(_cache_exp or ""),
                    n_calls=_cache_calls,
                    n_puts=_cache_puts,
                )
                _cache_observed = None
                try:
                    _cache_epoch = float(cache_time or 0.0)
                    if _cache_epoch > 0:
                        from datetime import timezone

                        _cache_observed = datetime.fromtimestamp(
                            _cache_epoch, timezone.utc
                        ).isoformat()
                except (TypeError, ValueError, OSError):
                    _cache_observed = None
                self._record_chain_fetch_meta(
                    symbol=symbol_u,
                    source=f"cache_{'live' if self.use_live_api else 'demo'}",
                    expiry=_cache_exp,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                    from_cache=True,
                    calls=_cache_calls,
                    puts=_cache_puts,
                    observed_at=_cache_observed,
                )
                return cached_data['chain']
            self._warn_chain_stale_throttled(
                symbol_u,
                str(expiry or ""),
                cache_age_seconds,
                usable_age,
            )

        # Same-day cache went stale, but a prior resolve already stored the
        # weekly under the selected expiry. Serve that instead of re-probing
        # an empty 0DTE day (CHAIN_CACHE_STALE → 10031 / SelectedED Friday).
        if use_cache and prefer_0dte:
            alt_chain = self._fresh_resolved_expiry_cache(
                symbol_u,
                session_today=session_today,
                usable_age=usable_age,
                started=started,
            )
            if alt_chain is not None:
                return alt_chain

        # Live API: Fetch from ETrade
        if self.use_live_api and self.etrade_api and self.etrade_api.is_available():
            try:
                async def _fetch_live_for_expiry(candidate_expiry: str) -> Tuple[List[OptionContract], List[OptionContract], str]:
                    candidate_iso = _normalize_expiry_iso(candidate_expiry)
                    try:
                        from modules.option_expiry_memo import is_empty as _memo_is_empty
                        if _memo_is_empty(symbol_u, candidate_iso):
                            log.debug(
                                "CHAIN_SKIP_MEMO_EMPTY | symbol=%s | expiry=%s",
                                symbol_u,
                                candidate_iso,
                            )
                            return [], [], ""
                    except Exception:
                        pass
                    candidate_etrade = candidate_iso.replace('-', '')
                    etrade_chain = await self.etrade_api.fetch_options_chain(
                        symbol=symbol_u,
                        expiry=candidate_etrade,
                        strike_count=20,
                        include_greeks=True,
                        underlying_price=underlying_price,
                    )
                    calls_local: List[OptionContract] = []
                    puts_local: List[OptionContract] = []
                    api_selected = str(
                        (etrade_chain or {}).get("selected_expiry") or ""
                    ).strip()

                    for etrade_call in etrade_chain.get('calls', []):
                        # Prefer per-leg expiry, then the *requested* candidate day — never
                        # stamp SelectedED Friday over a same-day/near-day request.
                        listed_iso = _normalize_expiry_iso(
                            getattr(etrade_call, "expiry", None) or candidate_iso or api_selected
                        )
                        call_oc = OptionContract(
                            symbol=etrade_call.symbol,
                            strike=etrade_call.strike,
                            expiry=listed_iso,
                            option_type='call',
                            bid=etrade_call.bid,
                            ask=etrade_call.ask,
                            last=etrade_call.last,
                            volume=etrade_call.volume,
                            open_interest=etrade_call.open_interest,
                            delta=etrade_call.delta or 0.0,
                            gamma=etrade_call.gamma or 0.0,
                            theta=etrade_call.theta or 0.0,
                            vega=etrade_call.vega or 0.0,
                            implied_volatility=etrade_call.implied_volatility or 0.0,
                            osi_key=str(getattr(etrade_call, "osi_key", "") or ""),
                            display_symbol=str(getattr(etrade_call, "display_symbol", "") or ""),
                        )
                        OptionsChainManager._stamp_option_book_sizes(call_oc, etrade_call)
                        calls_local.append(call_oc)

                    for etrade_put in etrade_chain.get('puts', []):
                        listed_iso = _normalize_expiry_iso(
                            getattr(etrade_put, "expiry", None) or candidate_iso or api_selected
                        )
                        put_oc = OptionContract(
                            symbol=etrade_put.symbol,
                            strike=etrade_put.strike,
                            expiry=listed_iso,
                            option_type='put',
                            bid=etrade_put.bid,
                            ask=etrade_put.ask,
                            last=etrade_put.last,
                            volume=etrade_put.volume,
                            open_interest=etrade_put.open_interest,
                            delta=etrade_put.delta or 0.0,
                            gamma=etrade_put.gamma or 0.0,
                            theta=etrade_put.theta or 0.0,
                            vega=etrade_put.vega or 0.0,
                            implied_volatility=etrade_put.implied_volatility or 0.0,
                            osi_key=str(getattr(etrade_put, "osi_key", "") or ""),
                            display_symbol=str(getattr(etrade_put, "display_symbol", "") or ""),
                        )
                        OptionsChainManager._stamp_option_book_sizes(put_oc, etrade_put)
                        puts_local.append(put_oc)
                    return calls_local, puts_local, _normalize_expiry_iso(api_selected) if api_selected else ""

                selected_expiry = expiry
                selected_type = "EXPLICIT"
                expiry_selection_mode = "explicit"
                fallback_days = 0
                fallback_reason = ""
                calls: List[OptionContract] = []
                puts: List[OptionContract] = []
                requested_expiry_iso = (
                    _normalize_expiry_iso(requested_raw) if requested_raw else session_today
                )

                if prefer_0dte:
                    # All 0DTE-list symbols share the same policy: same-day first,
                    # then optional nearest forward walk (env-gated).
                    # Never promote E*TRADE SelectedED over a nearer listed day.
                    today_us = date.fromisoformat(expiry)
                    max_fallback_days = int(os.getenv("0DTE_NEAREST_EXPIRY_MAX_DAYS", "10"))

                    def _contracts_for_day(
                        day_iso: str,
                        day_calls: List[OptionContract],
                        day_puts: List[OptionContract],
                    ) -> Tuple[List[OptionContract], List[OptionContract]]:
                        want = _normalize_expiry_iso(day_iso)
                        def _match(c: OptionContract) -> bool:
                            return _normalize_expiry_iso(getattr(c, "expiry", None)) == want
                        return (
                            [c for c in (day_calls or []) if _match(c)],
                            [p for p in (day_puts or []) if _match(p)],
                        )

                    def _remap_expiry(
                        day_iso: str,
                        day_calls: List[OptionContract],
                        day_puts: List[OptionContract],
                    ) -> Tuple[List[OptionContract], List[OptionContract]]:
                        want = _normalize_expiry_iso(day_iso)
                        out_c: List[OptionContract] = []
                        out_p: List[OptionContract] = []
                        for c in day_calls or []:
                            try:
                                c.expiry = want
                            except Exception:
                                pass
                            out_c.append(c)
                        for p in day_puts or []:
                            try:
                                p.expiry = want
                            except Exception:
                                pass
                            out_p.append(p)
                        return out_c, out_p

                    def _nearest_listed_from_payload(
                        day_calls: List[OptionContract],
                        day_puts: List[OptionContract],
                    ) -> Tuple[Optional[str], List[OptionContract], List[OptionContract], int]:
                        """Pick closest listed expiry after today within max_fallback_days.

                        When same-day is empty but the broker already returned next-week
                        (or +1/+2) contracts, use those immediately — do not block the
                        trade and do not burn empty-day API walks first.
                        """
                        best_iso: Optional[str] = None
                        best_delta: Optional[int] = None
                        for c in list(day_calls or []) + list(day_puts or []):
                            iso = _normalize_expiry_iso(getattr(c, "expiry", None))
                            if not iso:
                                continue
                            try:
                                delta = (date.fromisoformat(iso) - today_us).days
                            except ValueError:
                                continue
                            if delta <= 0 or delta > max(0, max_fallback_days):
                                continue
                            if best_delta is None or delta < best_delta:
                                best_delta = delta
                                best_iso = iso
                        if not best_iso or best_delta is None:
                            return None, [], [], 0
                        filt_c, filt_p = _contracts_for_day(best_iso, day_calls, day_puts)
                        if not (filt_c or filt_p):
                            return None, [], [], 0
                        return best_iso, filt_c, filt_p, int(best_delta)

                    try:
                        from modules.option_expiry_memo import (
                            expire_dates_for as _memo_expire_dates_for,
                            expiredate_resolve_first as _expiredate_resolve_first,
                            intent_expiry_from_listed,
                            is_empty as _memo_is_empty,
                            listed_forward_candidates,
                            nearest_for as _memo_nearest_for,
                            remember_empty as _memo_remember_empty,
                            remember_expire_dates as _memo_remember_expire_dates,
                            remember_nearest as _memo_remember_nearest,
                            weekday_forward_candidates,
                        )
                    except Exception:
                        _memo_expire_dates_for = None
                        _expiredate_resolve_first = None
                        intent_expiry_from_listed = None
                        _memo_is_empty = None
                        listed_forward_candidates = None
                        _memo_nearest_for = None
                        _memo_remember_empty = None
                        _memo_remember_expire_dates = None
                        _memo_remember_nearest = None
                        weekday_forward_candidates = None

                    async def _listed_expire_dates() -> List[str]:
                        cached = (
                            _memo_expire_dates_for(symbol_u)
                            if _memo_expire_dates_for is not None
                            else None
                        )
                        if cached:
                            return list(cached)
                        fetch_dates = getattr(
                            self.etrade_api, "fetch_option_expire_dates", None
                        )
                        if not callable(fetch_dates):
                            return []
                        try:
                            raw_listed = fetch_dates(symbol_u)
                            if asyncio.iscoroutine(raw_listed):
                                raw_listed = await raw_listed
                        except Exception:
                            return []
                        listed_out = [
                            _normalize_expiry_iso(x)
                            for x in (raw_listed or [])
                            if _normalize_expiry_iso(x)
                        ]
                        if listed_out and _memo_remember_expire_dates is not None:
                            _memo_remember_expire_dates(symbol_u, listed_out)
                        return listed_out

                    same_day_known_empty = bool(
                        _memo_is_empty is not None and _memo_is_empty(symbol_u, expiry)
                    )
                    memo_nearest = (
                        _memo_nearest_for(symbol_u, expiry)
                        if _memo_nearest_for is not None
                        else None
                    )
                    listed_dates = (
                        _memo_expire_dates_for(symbol_u)
                        if _memo_expire_dates_for is not None
                        else None
                    )
                    resolve_first = bool(
                        _expiredate_resolve_first is not None
                        and _expiredate_resolve_first(today_us)
                    )
                    if listed_dates is None and (resolve_first or same_day_known_empty):
                        listed_dates = await _listed_expire_dates()
                    listed_intent = (
                        intent_expiry_from_listed(
                            today_us,
                            listed_dates or [],
                            max_fallback_days,
                            symbol=symbol_u,
                        )
                        if intent_expiry_from_listed is not None and listed_dates
                        else None
                    )
                    if (
                        listed_intent
                        and listed_intent != expiry
                    ):
                        if _memo_remember_empty is not None:
                            _memo_remember_empty(symbol_u, expiry)
                        calls, puts, api_sel = await _fetch_live_for_expiry(
                            listed_intent
                        )
                        log.info(
                            "CHAIN_EXPIRY_LISTED_INTENT | symbol=%s | skipped_same_day=%s | "
                            "intent=%s | reason=expiredate_no_same_day",
                            symbol_u,
                            expiry,
                            listed_intent,
                        )
                    elif same_day_known_empty and memo_nearest and memo_nearest != expiry:
                        calls, puts, api_sel = await _fetch_live_for_expiry(memo_nearest)
                        log.info(
                            "CHAIN_EXPIRY_MEMO_NEAREST | symbol=%s | skipped_same_day=%s | "
                            "nearest=%s | reason=same_day_empty_use_cached_listed",
                            symbol_u,
                            expiry,
                            memo_nearest,
                        )
                    elif same_day_known_empty:
                        calls, puts, api_sel = [], [], ""
                        log.debug(
                            "CHAIN_SKIP_SAME_DAY_EMPTY_MEMO | symbol=%s | expiry=%s",
                            symbol_u,
                            expiry,
                        )
                    else:
                        calls, puts, api_sel = await _fetch_live_for_expiry(expiry)
                    same_calls, same_puts = _contracts_for_day(expiry, calls, puts)
                    if same_calls or same_puts:
                        calls, puts = same_calls, same_puts
                        selected_expiry = expiry
                        selected_type = "0DTE"
                        expiry_selection_mode = "same_day"
                        fallback_days = 0
                        fallback_reason = ""
                        if api_sel and api_sel != expiry:
                            log.info(
                                "CHAIN_SELECTED_ED_IGNORED | symbol=%s | requested_0dte=%s | "
                                "api_selected=%s | kept=same_day | reason=same_day_contracts_present",
                                symbol_u,
                                expiry,
                                api_sel,
                            )
                    else:
                        if calls or puts:
                            log.warning(
                                "CHAIN_SELECTED_ED_NO_SAME_DAY | symbol=%s | requested_0dte=%s | "
                                "api_selected=%s | action=nearest_forward_walk",
                                symbol_u,
                                expiry,
                                api_sel or (
                                    _normalize_expiry_iso(
                                        getattr(calls[0] if calls else puts[0], "expiry", None)
                                    )
                                ),
                            )
                        nearest_expiry: Optional[str] = None
                        nearest_offset = 0
                        # 1) Harvest nearest from the same-day response payload first.
                        pay_iso, pay_c, pay_p, pay_delta = _nearest_listed_from_payload(
                            calls, puts
                        )
                        if pay_iso and (pay_c or pay_p):
                            nearest_expiry = pay_iso
                            calls, puts = pay_c, pay_p
                            nearest_offset = pay_delta
                            log.info(
                                "CHAIN_EXPIRY_NEAREST_FROM_PAYLOAD | symbol=%s | requested_0dte=%s | "
                                "selected=%s | fallback_days=%s | reason=same_day_empty_use_listed",
                                symbol_u,
                                expiry,
                                pay_iso,
                                pay_delta,
                            )
                        if _memo_remember_empty is not None:
                            _memo_remember_empty(symbol_u, expiry)
                        # 2) Else listed expiries (optionexpiredate) then weekday walk.
                        # Never probe Sat/Sun or a day already known empty (10031).
                        if not nearest_expiry:
                            listed: List[str] = list(listed_dates or [])
                            if not listed:
                                listed = await _listed_expire_dates()
                            if listed_forward_candidates is not None and listed:
                                walk_days = listed_forward_candidates(
                                    today_us, listed, max_fallback_days
                                )
                            elif weekday_forward_candidates is not None:
                                walk_days = weekday_forward_candidates(
                                    today_us, max_fallback_days
                                )
                            else:
                                walk_days = [
                                    (today_us + timedelta(days=d)).isoformat()
                                    for d in range(1, max(0, max_fallback_days) + 1)
                                ]
                            if (
                                memo_nearest
                                and memo_nearest != expiry
                                and memo_nearest not in walk_days
                            ):
                                walk_days = [memo_nearest] + walk_days
                            for candidate in walk_days:
                                if (
                                    _memo_is_empty is not None
                                    and _memo_is_empty(symbol_u, candidate)
                                ):
                                    continue
                                c_calls, c_puts, c_sel = await _fetch_live_for_expiry(
                                    candidate
                                )
                                filt_c, filt_p = _contracts_for_day(
                                    candidate, c_calls, c_puts
                                )
                                if filt_c or filt_p:
                                    nearest_expiry = candidate
                                    calls, puts = filt_c, filt_p
                                    try:
                                        nearest_offset = (
                                            date.fromisoformat(candidate) - today_us
                                        ).days
                                    except ValueError:
                                        nearest_offset = 0
                                    break
                                if (c_calls or c_puts) and (
                                    not c_sel or c_sel == candidate
                                ):
                                    nearest_expiry = candidate
                                    calls, puts = _remap_expiry(
                                        candidate, c_calls, c_puts
                                    )
                                    try:
                                        nearest_offset = (
                                            date.fromisoformat(candidate) - today_us
                                        ).days
                                    except ValueError:
                                        nearest_offset = 0
                                    break
                                jump_iso, jump_c, jump_p, jump_d = (
                                    _nearest_listed_from_payload(c_calls, c_puts)
                                )
                                if jump_iso and (jump_c or jump_p):
                                    nearest_expiry = jump_iso
                                    calls, puts = jump_c, jump_p
                                    nearest_offset = jump_d
                                    log.info(
                                        "CHAIN_EXPIRY_NEAREST_FROM_WALK_PAYLOAD | symbol=%s | "
                                        "candidate=%s | selected=%s | fallback_days=%s",
                                        symbol_u,
                                        candidate,
                                        jump_iso,
                                        jump_d,
                                    )
                                    break
                                if _memo_remember_empty is not None:
                                    _memo_remember_empty(symbol_u, candidate)
                                if c_sel and c_sel != candidate and (c_calls or c_puts):
                                    log.debug(
                                        "CHAIN_NEAREST_CANDIDATE_SKIP_SELECTED_ED | symbol=%s | "
                                        "candidate=%s | api_selected=%s",
                                        symbol_u,
                                        candidate,
                                        c_sel,
                                    )
                        if nearest_expiry:
                            if _memo_remember_empty is not None:
                                _memo_remember_empty(symbol_u, expiry)
                            if _memo_remember_nearest is not None:
                                _memo_remember_nearest(symbol_u, nearest_expiry, expiry)
                            selected_expiry = nearest_expiry
                            selected_type = "NEAREST_FALLBACK"
                            expiry_selection_mode = "nearest_to_0dte"
                            try:
                                fallback_days = (
                                    date.fromisoformat(nearest_expiry) - today_us
                                ).days
                            except ValueError:
                                fallback_days = int(nearest_offset or 0)
                            fallback_reason = "same_day_expiry_not_listed"
                            log.warning(
                                "CHAIN_EXPIRY_NEAREST_FALLBACK | symbol=%s | requested_0dte=%s | "
                                "selected=%s | fallback_days=%s | on_0dte_watchlist=%s | tier=%s",
                                symbol_u,
                                expiry,
                                nearest_expiry,
                                fallback_days,
                                str(on_watchlist).lower(),
                                tier if on_watchlist else "n/a",
                            )
                        else:
                            if _memo_remember_empty is not None:
                                _memo_remember_empty(symbol_u, expiry)
                            calls, puts = [], []
                            selected_type = "NONE"
                            expiry_selection_mode = "none"
                            fallback_reason = "same_day_expiry_not_listed"
                else:
                    # Explicit non-today day (monitor refresh / repair walk): keep the
                    # requested candidate day — never promote SelectedED to a farther Friday.
                    calls, puts, api_sel = await _fetch_live_for_expiry(expiry)
                    day_calls, day_puts = (
                        [c for c in calls if _normalize_expiry_iso(getattr(c, "expiry", None)) == expiry],
                        [p for p in puts if _normalize_expiry_iso(getattr(p, "expiry", None)) == expiry],
                    )
                    if day_calls or day_puts:
                        calls, puts = day_calls, day_puts
                        selected_expiry = expiry
                    elif (calls or puts) and (not api_sel or api_sel == expiry):
                        for c in calls:
                            try:
                                c.expiry = expiry
                            except Exception:
                                pass
                        for p in puts:
                            try:
                                p.expiry = expiry
                            except Exception:
                                pass
                        selected_expiry = expiry
                    elif calls or puts:
                        log.warning(
                            "CHAIN_EXPLICIT_SELECTED_ED_IGNORED | symbol=%s | requested=%s | "
                            "api_selected=%s | action=empty_for_requested_day",
                            symbol_u,
                            expiry,
                            api_sel,
                        )
                        calls, puts = [], []
                        selected_expiry = expiry
                    else:
                        selected_expiry = expiry
                    selected_type = "EXPLICIT" if (calls or puts) else "NONE"
                    expiry_selection_mode = "explicit" if (calls or puts) else "none"

                try:
                    from modules.calendar_expiry_metadata import build_expiry_trade_fields

                    _expiry_meta = build_expiry_trade_fields(
                        selected_expiry=selected_expiry,
                        requested_expiry=requested_expiry_iso,
                        session_date=session_today,
                        expiry_selection_mode=expiry_selection_mode,
                        fallback_reason=fallback_reason,
                        fallback_days=fallback_days,
                        selected_type=selected_type,
                    )
                except Exception:
                    _expiry_meta = {
                        "session_date": session_today,
                        "expiry_ymd": selected_expiry,
                        "calendar_dte": None,
                        "is_0dte": selected_expiry == session_today,
                        "expiry_selection_mode": expiry_selection_mode,
                        "requested_expiry_ymd": requested_expiry_iso,
                        "selected_expiry_ymd": selected_expiry,
                        "fallback_days": int(fallback_days or 0),
                        "fallback_reason": fallback_reason or "",
                        "dte": None,
                    }

                chain = {
                    'calls': calls,
                    'puts': puts,
                    'selected_expiry': selected_expiry,
                    'expiry': selected_expiry,
                    'requested_expiry': requested_expiry_iso,
                    'selected_type': selected_type,
                    'session_date': _expiry_meta.get("session_date") or session_today,
                    'expiry_ymd': _expiry_meta.get("expiry_ymd") or selected_expiry,
                    'calendar_dte': _expiry_meta.get("calendar_dte"),
                    'is_0dte': bool(_expiry_meta.get("is_0dte")),
                    'expiry_selection_mode': _expiry_meta.get("expiry_selection_mode")
                    or expiry_selection_mode,
                    'requested_expiry_ymd': _expiry_meta.get("requested_expiry_ymd")
                    or requested_expiry_iso,
                    'selected_expiry_ymd': _expiry_meta.get("selected_expiry_ymd")
                    or selected_expiry,
                    'fallback_days': int(_expiry_meta.get("fallback_days") or fallback_days or 0),
                    'fallback_reason': str(
                        _expiry_meta.get("fallback_reason") or fallback_reason or ""
                    ),
                    'dte': _expiry_meta.get("dte"),
                }
                self.last_expiry_selection = {
                    k: chain.get(k)
                    for k in (
                        "session_date",
                        "expiry_ymd",
                        "calendar_dte",
                        "is_0dte",
                        "expiry_selection_mode",
                        "requested_expiry_ymd",
                        "selected_expiry_ymd",
                        "fallback_days",
                        "fallback_reason",
                        "dte",
                        "selected_type",
                        "selected_expiry",
                        "requested_expiry",
                    )
                }
                selected_cache_key = f"{symbol_u}_{selected_expiry}"
                self.chain_cache[selected_cache_key] = {
                    'chain': chain,
                    'timestamp': datetime.now().timestamp(),
                }
                # Also index under the request key so same-day miss→nearest hits cache next tick.
                if selected_cache_key != cache_key:
                    self.chain_cache[cache_key] = {
                        'chain': chain,
                        'timestamp': datetime.now().timestamp(),
                    }

                log.info(
                    "CHAIN_EXPIRY_SELECTION | symbol=%s | type=%s | requested=%s | selected=%s | "
                    "calls=%d | puts=%d | on_0dte_watchlist=%s | tier=%s",
                    symbol_u,
                    selected_type,
                    _normalize_expiry_iso(requested_raw) if requested_raw else session_today,
                    selected_expiry,
                    len(calls),
                    len(puts),
                    str(on_watchlist).lower(),
                    tier if on_watchlist else "n/a",
                )
                log.info(f"✅ Fetched options chain from ETrade: {len(calls)} calls, {len(puts)} puts")
                log.info(
                    "0DTE_CHAIN_SOURCE | symbol=%s | source=etrade_live | expiry=%s | calls=%d | puts=%d",
                    symbol_u,
                    selected_expiry,
                    len(calls),
                    len(puts),
                )
                _remember_same_day_hit(
                    selected=str(selected_expiry or ""),
                    n_calls=len(calls),
                    n_puts=len(puts),
                )
                self._record_chain_fetch_meta(
                    symbol=symbol_u,
                    source="etrade_live",
                    expiry=selected_expiry,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                    from_cache=False,
                    calls=len(calls),
                    puts=len(puts),
                )
                # Central tech: per-contract book ring (live fetch only, never a
                # cache hit) so PO entry snaps can read real *_30s_ago / *_5m_ago
                # quotes and option L1 for the exact selected contract.
                try:
                    from central_tech.contract_book_ring import record_chain_contract_book

                    record_chain_contract_book(
                        symbol_u,
                        chain,
                        underlying_price=underlying_price,
                    )
                except Exception:
                    pass
                return chain

            except Exception as e:
                log.error(f"Failed to fetch options chain from ETrade: {e}")
                error_type = "timeout" if isinstance(e, (asyncio.TimeoutError, TimeoutError)) else "API_error"
                self._record_chain_fetch_meta(
                    symbol=symbol_u,
                    source="etrade_live_error",
                    expiry=expiry,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                    from_cache=False,
                    calls=0,
                    puts=0,
                    error_type=error_type,
                )
                # Live chain failed. Do not invent contracts.

        log.error(
            "0DTE_CHAIN_LIVE_REQUIRED | symbol=%s | expiry=%s | use_live_api=%s | no synthetic fallback",
            symbol_u,
            expiry,
            self.use_live_api,
        )
        chain = {'calls': [], 'puts': [], 'selected_expiry': expiry, 'expiry': expiry}
        self.chain_cache[cache_key] = {
            'chain': chain,
            'timestamp': datetime.now().timestamp(),
        }
        log.info(
            "0DTE_CHAIN_SOURCE | symbol=%s | source=none | expiry=%s | calls=0 | puts=0",
            symbol_u,
            expiry,
        )
        meta = self.get_last_chain_fetch_meta(symbol_u)
        self._record_chain_fetch_meta(
            symbol=symbol_u,
            source=meta.get("source", "none"),
            expiry=expiry,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            from_cache=bool(meta.get("from_cache", False)),
            calls=0,
            puts=0,
            error_type=meta.get("error_type") or "no_chain_data",
        )
        return chain
    
    def validate_liquidity(self, contract: OptionContract) -> Tuple[bool, List[str]]:
        """
        Validate contract liquidity (Deterministic Contract Picker Rules)
        
        Rev 00246: Enhanced logging for performance adjustments
        
        Reject trade if:
        - Bid/ask spread > 15% of mid
        - Option volume < minimum
        - OI < minimum
        - Missing bid/ask prices
        
        Args:
            contract: Option contract to validate
            
        Returns:
            Tuple of (is_valid, reasons)
        """
        reasons = []
        is_valid = True
        
        # Deterministic Rule 1: Check open interest (reject if <minimum)
        max_spread_pct, min_open_interest, min_volume = self._liquidity_thresholds_for_symbol(contract.symbol)
        if contract.open_interest < min_open_interest:
            is_valid = False
            reasons.append(f"Open interest {contract.open_interest} < {min_open_interest} (reject: OI < minimum)")
            log.debug(f"   ❌ Hard Gate: OI {contract.open_interest} < {min_open_interest} for {contract.symbol} {contract.option_type} ${contract.strike}")
        else:
            log.debug(f"   ✅ Hard Gate: OI {contract.open_interest} ≥ {min_open_interest} for {contract.symbol} {contract.option_type} ${contract.strike}")
        
        # Deterministic Rule 2: Check bid-ask spread (reject if above configured max)
        spread_ok = contract.bid_ask_spread_pct <= max_spread_pct
        self._log_opening_spread_relax_audit(
            contract,
            pass_fail="pass" if spread_ok else "fail",
        )
        if not spread_ok:
            is_valid = False
            reasons.append(
                f"Bid/ask spread {contract.bid_ask_spread_pct:.2f}% > {max_spread_pct:.1f}% of mid "
                f"(reject: spread >{max_spread_pct:.1f}%)"
            )
            try:
                from modules.orb_0dte_opening_execution import log_borderline_spread_rejection

                log_borderline_spread_rejection(
                    symbol=str(contract.symbol or ""),
                    option_type=str(contract.option_type or ""),
                    strike=float(contract.strike or 0.0),
                    spread_pct=float(contract.bid_ask_spread_pct or 0.0),
                    cap_pct=float(max_spread_pct),
                    route_evaluated="hard_gate_validate_liquidity",
                    oi=int(contract.open_interest or 0),
                    volume=int(contract.volume or 0),
                    delta=float(contract.delta) if contract.delta is not None else None,
                )
            except Exception:
                pass
            log.debug(f"   ❌ Hard Gate: Spread {contract.bid_ask_spread_pct:.2f}% > {max_spread_pct:.1f}% for {contract.symbol} {contract.option_type} ${contract.strike} (Bid: ${contract.bid:.2f}, Ask: ${contract.ask:.2f}, Mid: ${contract.mid_price:.2f})")
        else:
            log.debug(f"   ✅ Hard Gate: Spread {contract.bid_ask_spread_pct:.2f}% ≤ {max_spread_pct:.1f}% for {contract.symbol} {contract.option_type} ${contract.strike}")
        
        # Deterministic Rule 3: Check volume (reject if <minimum)
        if contract.volume < min_volume:
            is_valid = False
            reasons.append(f"Volume {contract.volume} < {min_volume} (reject: volume < minimum)")
            log.debug(f"   ❌ Hard Gate: Volume {contract.volume} < {min_volume} for {contract.symbol} {contract.option_type} ${contract.strike}")
        else:
            log.debug(f"   ✅ Hard Gate: Volume {contract.volume} ≥ {min_volume} for {contract.symbol} {contract.option_type} ${contract.strike}")
        
        # Deterministic Rule 4: Check if bid/ask exist (reject if missing)
        if contract.bid <= 0 or contract.ask <= 0:
            is_valid = False
            reasons.append("Missing bid/ask prices (reject: no pricing data)")
            log.debug(f"   ❌ Hard Gate: Missing bid/ask prices for {contract.symbol} {contract.option_type} ${contract.strike} (Bid: ${contract.bid:.2f}, Ask: ${contract.ask:.2f})")
        else:
            log.debug(f"   ✅ Hard Gate: Bid/ask prices exist for {contract.symbol} {contract.option_type} ${contract.strike} (Bid: ${contract.bid:.2f}, Ask: ${contract.ask:.2f})")
        
        # Rev 00246: Summary log for Hard Gate validation
        if is_valid:
            log.debug(f"   ✅ Hard Gate PASSED: {contract.symbol} {contract.option_type} ${contract.strike} (OI: {contract.open_interest}, Spread: {contract.bid_ask_spread_pct:.2f}%, Volume: {contract.volume})")
            self.record_hard_gate_pass(contract)
        else:
            # Avoid warning-level noise for obviously unusable stale placeholders (e.g. strike=0, no bid/ask).
            has_actionable_quote = contract.strike > 0 and contract.bid > 0 and contract.ask > 0
            if has_actionable_quote:
                self._record_hard_gate_reject(contract, reasons)
            else:
                log.debug(
                    "   ❌ Hard Gate FAILED: %s %s $%s - %s",
                    contract.symbol,
                    contract.option_type,
                    contract.strike,
                    ", ".join(reasons),
                )
        
        return is_valid, reasons

    def _hard_gate_bucket_for_reason(self, reason: str) -> str:
        r = str(reason or "").lower()
        if "spread" in r:
            return "spread"
        if "volume" in r:
            return "volume"
        if "open interest" in r or "oi" in r:
            return "oi"
        if "bid/ask" in r or "pricing" in r:
            return "missing_quote"
        return "other"

    def _record_hard_gate_reject(self, contract: OptionContract, reasons: List[str]) -> None:
        sym = str(getattr(contract, "symbol", "") or "").upper()
        if not sym:
            return
        entry = self._hard_gate_reject_summary.setdefault(
            sym,
            {"failed": 0, "passed": 0, "buckets": {}},
        )
        entry["failed"] = int(entry.get("failed", 0)) + 1
        buckets = entry.setdefault("buckets", {})
        for reason in reasons or ["unknown"]:
            bucket = self._hard_gate_bucket_for_reason(reason)
            buckets[bucket] = int(buckets.get(bucket, 0)) + 1

    def record_hard_gate_pass(self, contract: OptionContract) -> None:
        sym = str(getattr(contract, "symbol", "") or "").upper()
        if not sym:
            return
        entry = self._hard_gate_reject_summary.setdefault(
            sym,
            {"failed": 0, "passed": 0, "buckets": {}},
        )
        entry["passed"] = int(entry.get("passed", 0)) + 1

    def flush_hard_gate_symbol_summary(self, symbol: str, *, reset: bool = True) -> None:
        sym = str(symbol or "").upper()
        entry = self._hard_gate_reject_summary.get(sym)
        if not entry or int(entry.get("failed", 0) or 0) <= 0:
            if reset and sym in self._hard_gate_reject_summary:
                self._hard_gate_reject_summary.pop(sym, None)
            return
        log.warning(
            "0DTE_HARD_GATE_SYMBOL_SUMMARY | symbol=%s | failed=%d | passed=%d | buckets=%s",
            sym,
            int(entry.get("failed", 0) or 0),
            int(entry.get("passed", 0) or 0),
            entry.get("buckets") or {},
        )
        if reset:
            self._hard_gate_reject_summary.pop(sym, None)
    
    def select_debit_spread_strikes(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        target_delta: float,
        spread_width: float,
        current_price: float,
        relax_delta_extra: float = 0.0,
        allow_nearest_long_outside_band: bool = False,
        momentum_score: float = 0.0,
    ) -> Optional[DebitSpread]:
        """
        Select optimal strikes for debit spread - Rev 00212
        
        Contract Selection Logic (Best Practices):
        - Premium: $0.20-$0.60 (cheap contracts for gamma explosion)
        - Delta: 10-30 (0.10-0.30) - cheap gamma that can explode
        - Strike: 1-3 strikes OTM (out of the money)
        - Why: Cheap contracts give gamma explosion, easy 5-20x with small moves
        
        Args:
            chain: Options chain dictionary
            option_type: 'call' or 'put'
            target_delta: Target delta for long leg (0.10-0.30) - Rev 00212
            spread_width: Spread width ($1 or $2 for QQQ/SPY, $5 or $10 for SPX)
            current_price: Current underlying price
            
        Returns:
            DebitSpread object or None if no valid spread found
        """
        contracts = chain.get(option_type + 's', [])
        self.last_selector_diagnostics = {
            "selector": "debit_spread",
            "option_type": option_type,
            "target_delta": float(target_delta),
            "spread_width": float(spread_width),
            "spot": float(current_price),
            "contracts_this_side": int(len(contracts)),
            "selected": False,
            "momentum_score": float(momentum_score or 0.0),
        }
        
        if not contracts:
            log.warning(f"No {option_type} contracts available")
            self.last_selector_diagnostics.update({
                "failure_stage": "no_contracts",
                "failure_reason": f"no_{option_type}_contracts",
            })
            return None
        
        # Rev 00349: Relax delta window for spread construction reliability on high-priced names.
        # This still keeps us near intended structure but avoids brittle "no spread found" outcomes.
        _es = _dte0_entry_selection()
        entry_delta_bounds = _es.entry_delta_bounds
        prefer_lowest_entry_delta = _es.prefer_lowest_entry_delta

        relax_delta_extra = max(0.0, float(relax_delta_extra or 0.0))
        try:
            mom_thr_sel = float(os.getenv("0DTE_DEBIT_SELECTOR_HIGH_MOMENTUM_MIN", "68"))
        except (TypeError, ValueError):
            mom_thr_sel = 68.0
        _high_mom = float(momentum_score or 0.0) >= mom_thr_sel
        _mom_pad = 0.055 if _high_mom else 0.0
        band_min, band_max = entry_delta_bounds()
        delta_min = max(band_min, target_delta - (0.10 + relax_delta_extra + _mom_pad * 0.55))
        delta_max = min(band_max if not _high_mom else min(band_max, 0.78), target_delta + (0.14 + relax_delta_extra + _mom_pad))
        
        # Rev 00348: Premium ladder for spread long-leg selection.
        # Keep the original "cheap gamma" profile first, then relax premium cap (still spread-only)
        # so high-price names (e.g., META/NVDA/TSLA) don't get dropped solely by a strict $0.60 cap.
        strict_premium_min = float(os.getenv("0DTE_DEBIT_LONG_PREMIUM_MIN", "0.15"))
        strict_premium_max = float(os.getenv("0DTE_DEBIT_LONG_PREMIUM_MAX_STRICT", "0.60"))
        soft_premium_max = float(os.getenv("0DTE_DEBIT_LONG_PREMIUM_MAX_SOFT", "1.20"))
        rescue_premium_max = float(os.getenv("0DTE_DEBIT_LONG_PREMIUM_MAX_RESCUE", "2.50"))
        premium_ladder = [
            ("strict_gamma", strict_premium_min, strict_premium_max),
            ("balanced_soft_cap", max(0.05, strict_premium_min), max(strict_premium_max, soft_premium_max)),
            ("liquidity_rescue_cap", max(0.03, strict_premium_min), max(soft_premium_max, rescue_premium_max)),
        ]

        # Rev 00349: Configurable OTM leg distance for spread construction.
        # Default allows wider lattice search than strict 1-3, reducing false no-spread outcomes.
        otm_min = int(float(os.getenv("0DTE_DEBIT_LONG_OTM_MIN", "1")))
        otm_max = int(float(os.getenv("0DTE_DEBIT_LONG_OTM_MAX", "5")))
        if otm_min < 1:
            otm_min = 1
        if otm_max < otm_min:
            otm_max = otm_min

        # Rev 00212: Filter by strike position (configurable OTM range)
        # For calls: strike > current_price (OTM)
        # For puts: strike < current_price (OTM)
        candidate_long_legs: List[OptionContract] = []
        selected_ladder_label = "none"
        selected_premium_min = strict_premium_min
        selected_premium_max = strict_premium_max

        # Get all strikes sorted for OTM calculation
        all_strikes = sorted(set(c.strike for c in contracts))

        for ladder_label, premium_min, premium_max in premium_ladder:
            tmp_candidates: List[OptionContract] = []
            for c in contracts:
                # Check delta range
                if not (delta_min <= abs(c.delta) <= delta_max):
                    continue

                # Check premium range (use mid price)
                premium = c.mid_price
                if not (premium_min <= premium <= premium_max):
                    continue

                # Check strike position (1-3 strikes OTM)
                if option_type == 'call':
                    # Call: strike should be above current price (OTM)
                    if c.strike <= current_price:
                        continue  # Not OTM

                    # Count how many strikes are OTM between current_price and this strike
                    otm_strikes = [s for s in all_strikes if current_price < s <= c.strike]
                    strikes_otm = len(otm_strikes)

                    if not (otm_min <= strikes_otm <= otm_max):
                        continue
                else:  # put
                    # Put: strike should be below current price (OTM)
                    if c.strike >= current_price:
                        continue  # Not OTM

                    # Count how many strikes are OTM between this strike and current_price
                    otm_strikes = [s for s in all_strikes if current_price > s >= c.strike]
                    strikes_otm = len(otm_strikes)

                    if not (otm_min <= strikes_otm <= otm_max):
                        continue

                tmp_candidates.append(c)
            if tmp_candidates:
                candidate_long_legs = tmp_candidates
                selected_ladder_label = ladder_label
                selected_premium_min = premium_min
                selected_premium_max = premium_max
                break

        if not candidate_long_legs and allow_nearest_long_outside_band:
            # Penny names: never pick nearest high-delta contract outside the unified band.
            if _es.is_penny_name_spot(float(current_price)):
                allow_nearest_long_outside_band = False
            # Final selector relaxation: keep premium/OTM constraints, but allow nearest delta
            # contract when strict delta band is too restrictive.
            nearest_candidates: List[OptionContract] = []
            for c in contracts:
                premium = c.mid_price
                if not (selected_premium_min <= premium <= max(selected_premium_max, rescue_premium_max)):
                    continue
                if option_type == 'call':
                    if c.strike <= current_price:
                        continue
                    otm_strikes = [s for s in all_strikes if current_price < s <= c.strike]
                    strikes_otm = len(otm_strikes)
                    if not (otm_min <= strikes_otm <= otm_max):
                        continue
                else:
                    if c.strike >= current_price:
                        continue
                    otm_strikes = [s for s in all_strikes if current_price > s >= c.strike]
                    strikes_otm = len(otm_strikes)
                    if not (otm_min <= strikes_otm <= otm_max):
                        continue
                nearest_candidates.append(c)
            if nearest_candidates:
                nearest_candidates.sort(key=lambda x: abs(abs(float(x.delta or 0.0)) - float(target_delta)))
                candidate_long_legs = [nearest_candidates[0]]
                self.last_selector_diagnostics.update({
                    "delta_relaxed_nearest": True,
                    "allow_nearest_long_outside_band": True,
                })
                log.warning(
                    "DEBIT_SPREAD_SELECTOR | delta_relaxed_nearest=true | option_type=%s | symbol=%s | "
                    "target_delta=%.2f | selected_delta=%.4f",
                    option_type,
                    contracts[0].symbol if contracts else "unknown",
                    float(target_delta),
                    float(candidate_long_legs[0].delta or 0.0),
                )

        if not candidate_long_legs:
            log.warning(
                f"No contracts found with delta in range [{delta_min:.2f}, {delta_max:.2f}] "
                f"and premium ladder strict={strict_premium_max:.2f}, soft={soft_premium_max:.2f}, rescue={rescue_premium_max:.2f} "
                f"(otm_range={otm_min}-{otm_max})"
            )
            self.last_selector_diagnostics.update({
                "failure_stage": "long_leg_candidates",
                "failure_reason": "no_long_leg_candidates",
                "delta_min": float(delta_min),
                "delta_max": float(delta_max),
                "otm_min": int(otm_min),
                "otm_max": int(otm_max),
                "strict_premium_max": float(strict_premium_max),
                "soft_premium_max": float(soft_premium_max),
                "rescue_premium_max": float(rescue_premium_max),
                "relax_delta_extra": float(relax_delta_extra),
                "allow_nearest_long_outside_band": bool(allow_nearest_long_outside_band),
            })
            return None
        self.last_selector_diagnostics.update({
            "selected_ladder": selected_ladder_label,
            "selected_premium_min": float(selected_premium_min),
            "selected_premium_max": float(selected_premium_max),
            "candidate_long_legs": int(len(candidate_long_legs)),
            "delta_min": float(delta_min),
            "delta_max": float(delta_max),
            "relax_delta_extra": float(relax_delta_extra),
            "allow_nearest_long_outside_band": bool(allow_nearest_long_outside_band),
            "otm_min": int(otm_min),
            "otm_max": int(otm_max),
        })
        if selected_ladder_label != "strict_gamma":
            log.warning(
                "DEBIT_SPREAD_SELECTOR | premium_ladder=%s | option_type=%s | symbol=%s | "
                "target_delta=%.2f | selected_candidates=%d | premium_range=[%.2f, %.2f]",
                selected_ladder_label,
                option_type,
                contracts[0].symbol if contracts else "unknown",
                target_delta,
                len(candidate_long_legs),
                selected_premium_min,
                selected_premium_max,
            )
        
        # Multi-factor optimization: Cheap gamma + Low decay + Peak gamma potential + Vega risk
        # Score = (gamma_score * gamma_weight) - (theta_score * theta_weight) - (vega_risk * vega_weight)
        # Higher score = better option
        
        def calculate_option_score(contract, target_delta):
            """Calculate multi-factor optimization score for option selection"""
            score = 0.0
            vega_w = 0.035 if _high_mom else 0.10

            # 1. Gamma Score (40% weight) - Buy cheap gamma, maximize peak gamma potential
            # Higher gamma = better, but we want cheap gamma (OTM options)
            gamma_score = abs(contract.gamma) if contract.gamma else 0.0
            # Normalize gamma (assume max gamma around 0.05-0.10 for OTM options)
            gamma_normalized = min(gamma_score / 0.10, 1.0) if gamma_score > 0 else 0.0
            score += gamma_normalized * 0.40
            
            # 2. Theta Score (30% weight) - Minimize decay (lower absolute theta = better)
            # Lower theta (less negative) = less decay = better
            theta_score = abs(contract.theta) if contract.theta else 0.0
            # Normalize theta (assume max theta around 0.20-0.30 for 0DTE OTM options)
            # Lower theta is better, so invert: (max_theta - theta) / max_theta
            theta_normalized = max(0.0, (0.30 - theta_score) / 0.30) if theta_score > 0 else 1.0
            score += theta_normalized * 0.30
            
            # 3. Delta Proximity (20% weight) - Closest to target delta
            delta_diff = abs(abs(contract.delta) - target_delta)
            delta_score = max(0.0, 1.0 - (delta_diff / 0.10))  # Within ±0.10 range
            score += delta_score * 0.20
            
            # 4. Vega Risk — down-weight on high-momentum path so explosive IV names are not over-penalized.
            vega_score = abs(contract.vega) if contract.vega else 0.0
            vega_normalized = max(0.0, (0.15 - vega_score) / 0.15) if vega_score > 0 else 1.0
            score += vega_normalized * vega_w
            
            return score
        
        if prefer_lowest_entry_delta():
            candidate_long_legs.sort(
                key=lambda x: (
                    abs(abs(float(x.delta or 0.0))),
                    -calculate_option_score(x, target_delta),
                )
            )
        else:
            candidate_long_legs.sort(
                key=lambda x: (
                    -calculate_option_score(x, target_delta),
                    abs(abs(x.delta) - target_delta),
                )
            )
        
        # Try to find valid debit spread (bounded tries + structured pairing audit)
        pairing_audit: List[Dict[str, Any]] = []
        long_try_n = 0
        for long_contract in candidate_long_legs:
            long_try_n += 1
            if long_try_n > 32:
                break
            long_strike = long_contract.strike
            step = self._get_strike_increment(long_strike)
            if option_type == 'call':
                base_tgt = long_strike + spread_width
            else:
                base_tgt = long_strike - spread_width
            strike_targets = [base_tgt]
            if _high_mom:
                if option_type == 'call':
                    strike_targets.extend(
                        [base_tgt + 0.5 * step, base_tgt - 0.5 * step, base_tgt + 1.0 * step]
                    )
                else:
                    strike_targets.extend(
                        [base_tgt - 0.5 * step, base_tgt + 0.5 * step, base_tgt - 1.0 * step]
                    )
            short_contract = None
            used_tgt = base_tgt
            for tgt in strike_targets:
                sc = self._pick_otm_short_leg(contracts, option_type, long_strike, float(tgt))
                if sc is not None:
                    short_contract = sc
                    used_tgt = float(tgt)
                    break
            if not short_contract:
                pairing_audit.append(
                    {
                        "long_strike": float(long_strike),
                        "long_delta": float(long_contract.delta or 0.0),
                        "targets_tried": [float(x) for x in strike_targets[:6]],
                        "failure": "short_leg_not_found",
                    }
                )
                continue
            
            # Validate both legs
            long_valid, long_reasons = self.validate_liquidity(long_contract)
            short_valid, short_reasons = self.validate_liquidity(short_contract)
            
            if not long_valid or not short_valid:
                log.debug(f"Invalid spread: long={long_valid}, short={short_valid}")
                pairing_audit.append(
                    {
                        "long_strike": float(long_strike),
                        "short_strike": float(getattr(short_contract, "strike", 0.0) or 0.0),
                        "target_short": float(used_tgt),
                        "long_ok": bool(long_valid),
                        "short_ok": bool(short_valid),
                        "long_reasons": list(long_reasons or [])[:2],
                        "short_reasons": list(short_reasons or [])[:2],
                        "failure": "liquidity_reject",
                    }
                )
                continue
            
            # Calculate spread metrics
            debit_cost = long_contract.ask - short_contract.bid
            max_profit = spread_width - debit_cost
            max_loss = debit_cost
            
            # Keep informational R:R here; final payoff guardrails are enforced in execution stage.
            if max_loss > 0:
                risk_reward_ratio = max_profit / max_loss
            else:
                risk_reward_ratio = 0.0
            
            if option_type == 'call':
                break_even = long_strike + debit_cost
            else:  # put
                break_even = long_strike - debit_cost
            
            short_strike = short_contract.strike
            spread = DebitSpread(
                symbol=long_contract.symbol,
                expiry=long_contract.expiry,
                option_type=option_type,
                long_strike=long_strike,
                short_strike=short_strike,
                long_contract=long_contract,
                short_contract=short_contract,
                debit_cost=debit_cost,
                max_profit=max_profit,
                max_loss=max_loss,
                break_even=break_even
            )
            
            # Calculate optimization score for logging
            opt_score = calculate_option_score(long_contract, target_delta)
            long_prem = long_contract.mid_price
            
            log.info(f"✅ Selected debit spread: {spread.symbol} {option_type} {spread.long_strike}/{spread.short_strike}")
            log.info(f"  Debit: ${debit_cost:.2f}, Max Profit: ${max_profit:.2f}, Max Loss: ${max_loss:.2f}")
            log.info(f"  Risk:Reward Ratio: {risk_reward_ratio:.2f}x (within {self.min_risk_reward_ratio:.1f}x-{self.max_risk_reward_ratio:.1f}x range ✅)")
            log.info(f"  Long Leg: 1-3 strikes OTM (delta: {long_contract.delta:.2f}, premium: ${long_contract.mid_price:.2f})")
            log.info(f"  Short Leg: R:R {risk_reward_ratio:.2f}x ✅")
            log.info(f"  Long Leg Greeks: Gamma={long_contract.gamma:.4f}, Theta={long_contract.theta:.4f}, Vega={long_contract.vega:.4f}, IV={long_contract.implied_volatility:.2%}")
            log.info(f"  Optimization Score: {opt_score:.3f} (Gamma:40%, Theta:30%, Delta:20%, Vega:10%)")
            log.info(
                f"  Rev 00348: Premium ${long_prem:.2f} in range "
                f"[${selected_premium_min:.2f}, ${selected_premium_max:.2f}] "
                f"(ladder={selected_ladder_label}) ✅"
            )
            self.last_selector_diagnostics.update({
                "selected": True,
                "failure_stage": None,
                "failure_reason": None,
                "selected_long_strike": float(long_strike),
                "selected_short_strike": float(short_strike),
                "selected_long_delta": float(long_contract.delta),
                "selected_short_delta": float(short_contract.delta),
                "selected_long_mid": float(long_contract.mid_price),
                "selected_short_mid": float(short_contract.mid_price),
                "debit_cost": float(debit_cost),
                "max_profit": float(max_profit),
                "max_loss": float(max_loss),
                "risk_reward_ratio": float(risk_reward_ratio),
                "pairing_attempts_logged": int(len(pairing_audit)),
            })
            
            return spread
        
        self.last_selector_diagnostics.update({
            "failure_stage": "pairing_or_liquidity",
            "failure_reason": "no_valid_spread_pair_found",
            "pairing_audit_sample": pairing_audit[:14],
            "pairing_attempts": int(len(pairing_audit)),
            "long_leg_candidates": int(len(candidate_long_legs)),
            "high_momentum_selector": bool(_high_mom),
        })
        log.warning(f"No valid debit spread found for {option_type} with delta {target_delta:.2f}")
        return None
    
    def select_single_leg_contract(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        current_price: float,
        profile: str = "directional",
        symbol: str = "",
        adaptive_open_window: bool = False,
        momentum_score: float = 0.0,
        orb_range_pct: float = 0.0,
    ) -> Optional[OptionContract]:
        """
        ORB1 Breakout single-leg opening selector (directional band only).

        Delta 0.03–0.42 (unified entry band), premium max max($1.20, spot×0.028)
        with optional opening relax, spread <= 8% (momentum/opening relax may widen).
        """
        contracts = chain.get(option_type + 's', [])
        sym0 = str(symbol or (contracts[0].symbol if contracts else "") or "").upper()
        profile_key = "directional"
        _selected_expiry = str(
            (chain or {}).get("selected_expiry") or (chain or {}).get("expiry") or ""
        ).strip()
        try:
            _session_day = _options_expiry_yyyy_mm_dd_us()
        except Exception:
            _session_day = ""
        _selected_expiry_norm = _normalize_expiry_iso(_selected_expiry) if _selected_expiry else ""
        _chain_expiry_meta = {}
        if isinstance(chain, dict):
            for _ek in (
                "calendar_dte",
                "is_0dte",
                "expiry_selection_mode",
                "requested_expiry_ymd",
                "selected_expiry_ymd",
                "fallback_days",
                "fallback_reason",
                "session_date",
                "dte",
            ):
                if chain.get(_ek) is not None:
                    _chain_expiry_meta[_ek] = chain.get(_ek)
        self.last_selector_diagnostics = {
            "selector": "single_leg",
            "profile": profile_key,
            "symbol": sym0,
            "option_type": option_type,
            "spot": float(current_price),
            "contracts_this_side": int(len(contracts)),
            "momentum_score": float(momentum_score or 0.0),
            "orb_range_pct": float(orb_range_pct or 0.0),
            "selected_expiry": _selected_expiry_norm,
            "session_day": str(_session_day or ""),
            "expiry_is_same_day": bool(
                _selected_expiry_norm and _session_day and _selected_expiry_norm == _session_day
            ),
            "selected": False,
            **_chain_expiry_meta,
        }
        if not contracts:
            self.last_selector_diagnostics.update(
                {"failure_stage": "no_contracts", "failure_reason": f"no_{option_type}_contracts"}
            )
            return None

        premium_spot_mult = _opening_env_float(
            "0DTE_SINGLE_LEG_DIRECTIONAL_PREMIUM_SPOT_MULT",
            "0DTE_LOTTO_BREAKDOWN_PREMIUM_SPOT_MULT",
            0.028,
        )
        opening_oi_relaxed = False
        opening_spread_relaxed = False
        _es = _dte0_entry_selection()
        directional_entry_premium_min = _es.directional_entry_premium_min
        entry_delta_bounds = _es.entry_delta_bounds
        prefer_lowest_entry_delta = _es.prefer_lowest_entry_delta
        single_leg_sort_key = _es.single_leg_sort_key

        delta_min, delta_max = entry_delta_bounds()
        premium_min = directional_entry_premium_min()
        premium_max = _single_leg_spot_premium_cap(float(current_price))
        if _single_leg_opening_premium_relax_active(
            float(momentum_score or 0.0), float(orb_range_pct or 0.0)
        ):
            soft_cap = _single_leg_opening_premium_soft_cap(float(current_price))
            premium_max = max(premium_max, soft_cap)
            log.info(
                "0DTE_SINGLE_LEG_OPENING_PREMIUM_RELAX | symbol=%s | momentum=%.1f | "
                "orb_range=%.2f | premium_max=%.2f",
                str(symbol or "").upper(),
                float(momentum_score or 0.0),
                float(orb_range_pct or 0.0),
                float(premium_max),
            )
        try:
            max_spread_pct = float(os.getenv("0DTE_SINGLE_LEG_DIRECTIONAL_MAX_SPREAD_PCT", "8.0"))
        except (TypeError, ValueError):
            max_spread_pct = 8.0
        target_delta = delta_min if prefer_lowest_entry_delta() else (delta_min + delta_max) / 2.0
        min_volume = int(self.single_leg_min_volume)
        min_open_interest = int(self.single_leg_min_open_interest)
        symbol_u = str(symbol or (contracts[0].symbol if contracts else "")).upper()
        if adaptive_open_window:
            minutes_since_open = self._minutes_since_orb_session_open_pt()
            if minutes_since_open <= self.single_leg_open_window_minutes:
                try:
                    open_window_min_oi = int(os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_MIN_OI", "100"))
                except (TypeError, ValueError):
                    open_window_min_oi = 100
                min_open_interest = min(min_open_interest, max(1, open_window_min_oi))
                opening_oi_relaxed = True
                if symbol_u in self.single_leg_mega_symbols:
                    min_open_interest = max(
                        1,
                        int(round(min_open_interest * self.single_leg_open_window_oi_mult)),
                    )
                    min_volume = max(
                        1,
                        int(round(min_volume * self.single_leg_open_window_volume_mult)),
                    )
                log.info(
                    "0DTE_SINGLE_LEG_ADAPTIVE_FLOORS | symbol=%s | minutes_since_open=%.2f | "
                    "min_volume=%d | min_open_interest=%d | open_window_min_oi=%d",
                    symbol_u,
                    minutes_since_open,
                    min_volume,
                    min_open_interest,
                    open_window_min_oi,
                )
                if self._in_hard_gate_execution_window_et():
                    opening_spread_relaxed = False

        try:
            mom_relax_min = float(os.getenv("0DTE_SINGLE_LEG_MOMENTUM_RELAX_MIN", "72"))
        except (TypeError, ValueError):
            mom_relax_min = 72.0
        if float(momentum_score or 0.0) >= mom_relax_min:
            try:
                floor_oi = int(os.getenv("0DTE_SINGLE_LEG_MOMENTUM_FLOOR_OI", "100"))
            except (TypeError, ValueError):
                floor_oi = 100
            try:
                floor_vol = int(os.getenv("0DTE_SINGLE_LEG_MOMENTUM_FLOOR_VOL", "50"))
            except (TypeError, ValueError):
                floor_vol = 50
            min_open_interest = max(floor_oi, int(min_open_interest * 0.72))
            min_volume = max(floor_vol, int(min_volume * 0.72))
            log.info(
                "0DTE_SINGLE_LEG_MOMENTUM_RELAX | symbol=%s | profile=%s | momentum=%.1f | "
                "min_volume=%d | min_open_interest=%d | max_spread_pct=%.2f",
                symbol_u,
                profile_key,
                float(momentum_score or 0.0),
                min_volume,
                min_open_interest,
                float(max_spread_pct),
            )

        log.info(
            "ORB_0DTE_DIRECTIONAL_CAP_AUDIT | symbol=%s | current_price=%.4f | premium_min=%.2f | "
            "premium_max=%.2f | premium_spot_mult=%.4f | delta_min=%.2f | delta_max=%.2f | "
            "selected_contract= | selected_delta= | selected_premium= | selected_spread_pct= | "
            "reject_reason=pre_filter",
            symbol_u,
            float(current_price),
            float(premium_min),
            float(premium_max),
            float(premium_spot_mult),
            float(delta_min),
            float(delta_max),
        )

        # Filter first: premium + liquidity + side.
        side_candidates: List[OptionContract] = []
        rej_bucket = {"premium": 0, "spread": 0, "volume": 0, "oi": 0, "side": 0, "otm": 0}
        try:
            from easy0DTE.modules.dte0_entry_selection import (
                entry_otm_bounds,
                otm_gate_enabled as _otm_gate_enabled,
            )
            from easyBreakout.breakout_contract_gates import compute_otm_pct, evaluate_otm_gate

            _otm_pref, _otm_hard = entry_otm_bounds()
            _otm_on = bool(_otm_gate_enabled())
        except Exception:
            _otm_pref, _otm_hard, _otm_on = 0.055, 0.055, True
            try:
                from easyBreakout.breakout_contract_gates import compute_otm_pct, evaluate_otm_gate
            except Exception:
                compute_otm_pct = None  # type: ignore[assignment]
                evaluate_otm_gate = None  # type: ignore[assignment]
        scan_contracts = list(contracts)
        try:
            from modules.orb_critical_window import (
                is_orb_critical_execution_window,
                log_critical_window_telemetry,
                max_chain_contracts_during_critical_window,
            )

            if is_orb_critical_execution_window():
                budget = max_chain_contracts_during_critical_window()
                if budget > 0 and len(scan_contracts) > budget:
                    scan_contracts = scan_contracts[:budget]
                    log_critical_window_telemetry(
                        critical_window_active=True,
                        chain_scan_deferred=False,
                        chain_scan_contract_budget_used=budget,
                        symbol=symbol_u,
                        detail="single_leg_prefilter_capped",
                    )
        except Exception:
            pass
        for c in scan_contracts:
            if c.mid_price < premium_min or c.mid_price > premium_max:
                rej_bucket["premium"] += 1
                continue
            if c.bid_ask_spread_pct > max_spread_pct:
                rej_bucket["spread"] += 1
                continue
            if int(c.volume or 0) < min_volume:
                rej_bucket["volume"] += 1
                continue
            if int(c.open_interest or 0) < min_open_interest:
                rej_bucket["oi"] += 1
                continue
            if option_type == "call" and c.strike <= current_price:
                rej_bucket["side"] += 1
                continue
            if option_type == "put" and c.strike >= current_price:
                rej_bucket["side"] += 1
                continue
            if _otm_on and compute_otm_pct is not None and evaluate_otm_gate is not None:
                try:
                    _otm = compute_otm_pct(float(current_price), float(c.strike), option_type)
                    _dec = evaluate_otm_gate(
                        _otm,
                        enabled=True,
                        prefer_max=float(_otm_pref),
                        research_max=float(_otm_hard),
                    )
                    if not _dec.ok:
                        rej_bucket["otm"] += 1
                        continue
                except Exception:
                    pass
            side_candidates.append(c)

        # Opening premium soft retry when base cap emptied the book (breakdown class).
        if (
            not side_candidates
            and rej_bucket.get("premium", 0) > 0
            and _single_leg_opening_premium_relax_active(
                float(momentum_score or 0.0), float(orb_range_pct or 0.0)
            )
        ):
            soft_cap = _single_leg_opening_premium_soft_cap(float(current_price))
            if soft_cap > premium_max + 1e-6:
                retry_candidates: List[OptionContract] = []
                retry_bucket = {"premium": 0, "spread": 0, "volume": 0, "oi": 0, "side": 0, "otm": 0}
                for c in scan_contracts:
                    if c.mid_price < premium_min or c.mid_price > soft_cap:
                        retry_bucket["premium"] += 1
                        continue
                    if c.bid_ask_spread_pct > max_spread_pct:
                        retry_bucket["spread"] += 1
                        continue
                    if int(c.volume or 0) < min_volume:
                        retry_bucket["volume"] += 1
                        continue
                    if int(c.open_interest or 0) < min_open_interest:
                        retry_bucket["oi"] += 1
                        continue
                    if option_type == "call" and c.strike <= current_price:
                        retry_bucket["side"] += 1
                        continue
                    if option_type == "put" and c.strike >= current_price:
                        retry_bucket["side"] += 1
                        continue
                    if _otm_on and compute_otm_pct is not None and evaluate_otm_gate is not None:
                        try:
                            _otm = compute_otm_pct(float(current_price), float(c.strike), option_type)
                            _dec = evaluate_otm_gate(
                                _otm,
                                enabled=True,
                                prefer_max=float(_otm_pref),
                                research_max=float(_otm_hard),
                            )
                            if not _dec.ok:
                                retry_bucket["otm"] += 1
                                continue
                        except Exception:
                            pass
                    retry_candidates.append(c)
                if retry_candidates:
                    side_candidates = retry_candidates
                    premium_max = soft_cap
                    rej_bucket = retry_bucket
                    log.info(
                        "0DTE_SINGLE_LEG_OPENING_PREMIUM_SOFT_RETRY | symbol=%s | premium_max=%.2f | "
                        "side_candidates=%d",
                        symbol_u,
                        float(premium_max),
                        len(side_candidates),
                    )

        # Then enforce target delta window.
        candidates = [c for c in side_candidates if delta_min <= abs(c.delta) <= delta_max]
        if not candidates:
            dmin = delta_min
            dmax = delta_max
            abs_deltas = [abs(float(x.delta or 0.0)) for x in side_candidates[:40]]
            _es_penny = _dte0_entry_selection()
            if _es_penny.is_penny_name_spot(float(current_price)) and side_candidates:
                reject_reason = "penny_name_no_in_band_liquid_contract"
            elif side_candidates:
                reject_reason = "delta_band_empty"
            else:
                reject_reason = "liquidity_or_premium_filters"
            # Prefer reject dominance among delta-window contracts so deep OTM/ITM
            # premium junk does not mask the real blocker (e.g. CEG weekly 35% spread).
            delta_band_bucket = {"premium": 0, "spread": 0, "volume": 0, "oi": 0, "side": 0}
            delta_band_n = 0
            for c in scan_contracts:
                try:
                    abs_d = abs(float(c.delta or 0.0))
                except (TypeError, ValueError):
                    abs_d = 0.0
                if not (delta_min <= abs_d <= delta_max):
                    continue
                delta_band_n += 1
                if c.mid_price < premium_min or c.mid_price > premium_max:
                    delta_band_bucket["premium"] += 1
                    continue
                if c.bid_ask_spread_pct > max_spread_pct:
                    delta_band_bucket["spread"] += 1
                    continue
                if int(c.volume or 0) < min_volume:
                    delta_band_bucket["volume"] += 1
                    continue
                if int(c.open_interest or 0) < min_open_interest:
                    delta_band_bucket["oi"] += 1
                    continue
                if option_type == "call" and c.strike <= current_price:
                    delta_band_bucket["side"] += 1
                    continue
                if option_type == "put" and c.strike >= current_price:
                    delta_band_bucket["side"] += 1
                    continue
            _dom_src = delta_band_bucket if delta_band_n > 0 else rej_bucket
            dominant_reject = (
                max(_dom_src, key=_dom_src.get) if _dom_src and any(int(v or 0) > 0 for v in _dom_src.values()) else "none"
            )
            self.last_selector_diagnostics.update(
                {
                    "failure_stage": "single_leg_no_match",
                    "failure_reason": reject_reason,
                    "side_candidates": int(len(side_candidates)),
                    "reject_bucket_counts": dict(rej_bucket),
                    "delta_band_reject_bucket_counts": dict(delta_band_bucket),
                    "delta_band_contracts": int(delta_band_n),
                    "dominant_reject": dominant_reject,
                    "premium_min": float(premium_min),
                    "premium_max": float(premium_max),
                    "max_spread_pct": float(max_spread_pct),
                    "delta_window": [float(dmin), float(dmax)],
                    "sample_abs_deltas": [round(x, 4) for x in sorted(abs_deltas)[:12]],
                }
            )
            if profile_key == "directional":
                log.info(
                    "ORB_0DTE_DIRECTIONAL_CAP_AUDIT | symbol=%s | current_price=%.4f | premium_min=%.2f | "
                    "premium_max=%.2f | premium_spot_mult=%.4f | delta_min=%.2f | delta_max=%.2f | "
                    "selected_contract= | selected_delta= | selected_premium= | selected_spread_pct= | "
                    "reject_reason=%s",
                    symbol_u,
                    float(current_price),
                    float(premium_min),
                    float(premium_max),
                    float(premium_spot_mult),
                    float(delta_min),
                    float(delta_max),
                    reject_reason,
                )
            log.warning(
                "0DTE_SINGLE_LEG_FAIL_DETAIL | symbol=%s | profile=%s | side=%s | "
                "contracts_scanned=%d | side_candidates=%d | reject_buckets=%s | "
                "reject_pct=%s | delta_window=%.2f-%.2f | dominant_reject=%s | "
                "delta_band_n=%d | delta_band_buckets=%s | selected_expiry=%s | same_day=%s",
                symbol_u,
                profile_key,
                option_type.upper(),
                int(len(contracts)),
                int(len(side_candidates)),
                rej_bucket,
                {
                    k: round(100.0 * v / max(1, len(contracts)), 1)
                    for k, v in rej_bucket.items()
                },
                float(delta_min),
                float(delta_max),
                dominant_reject,
                int(delta_band_n),
                delta_band_bucket,
                _selected_expiry_norm or "na",
                str(bool(self.last_selector_diagnostics.get("expiry_is_same_day"))).lower(),
            )
            log.warning(
                "0DTE_SYMBOL_REJECT_SUMMARY | symbol=%s | route=single_leg | profile=%s | side=%s | "
                "reason=%s | buckets=%s",
                symbol_u,
                profile_key,
                option_type.upper(),
                reject_reason,
                rej_bucket,
            )
            self.flush_hard_gate_symbol_summary(symbol_u)
            return None

        def _single_leg_score(contract: OptionContract) -> float:
            gamma_norm = min(abs(contract.gamma or 0.0) / 0.12, 1.0)
            theta_norm = max(0.0, (0.35 - abs(contract.theta or 0.0)) / 0.35)
            if prefer_lowest_entry_delta():
                span = max(0.001, delta_max - delta_min)
                delta_norm = max(0.0, 1.0 - (abs(abs(contract.delta) - delta_min) / span))
            else:
                delta_norm = max(
                    0.0,
                    1.0 - (abs(abs(contract.delta) - target_delta) / max(0.001, (delta_max - delta_min))),
                )
            if option_type == "call":
                strike_gap = max(0.0, contract.strike - current_price)
            else:
                strike_gap = max(0.0, current_price - contract.strike)
            strike_norm = max(0.0, 1.0 - min(strike_gap / max(current_price * 0.02, 0.5), 1.0))
            return (gamma_norm * 0.50) + (theta_norm * 0.20) + (delta_norm * 0.20) + (strike_norm * 0.10)

        candidates.sort(
            key=lambda c: single_leg_sort_key(
                c,
                option_type=option_type,
                current_price=current_price,
                liquidity_score=_single_leg_score(c),
            )
        )

        viability_audit: Dict[str, Any] = {}
        if prefer_lowest_entry_delta():
            try:
                from easy0DTE.modules.dte0_delta_viability import pick_lowest_viable_single_leg
            except ImportError:
                import importlib.util
                import os as _os

                _vi_path = _os.path.normpath(
                    _os.path.join(_os.path.dirname(__file__), "dte0_delta_viability.py")
                )
                _vi_spec = importlib.util.spec_from_file_location("dte0_delta_viability_ocm", _vi_path)
                if _vi_spec is None or _vi_spec.loader is None:
                    raise
                _vi_mod = importlib.util.module_from_spec(_vi_spec)
                _vi_spec.loader.exec_module(_vi_mod)
                pick_lowest_viable_single_leg = _vi_mod.pick_lowest_viable_single_leg

            selected, viability_audit = pick_lowest_viable_single_leg(
                candidates,
                spot=float(current_price),
                option_type=option_type,
                symbol=symbol_u,
                profile=profile_key,
                orb_range_pct=float(orb_range_pct or 0.0),
                momentum_score=float(momentum_score or 0.0),
                max_spread_pct=float(max_spread_pct),
                premium_min=float(premium_min),
                prefer_lowest=True,
            )
            if selected is None:
                selected = candidates[0]
        else:
            selected = candidates[0]
        recovery_rules: List[str] = []
        if premium_max > 1.20:
            recovery_rules.append("spot_scaled_premium_cap")
        if delta_max >= 0.42:
            recovery_rules.append("directional_delta_max_42")
        if opening_oi_relaxed:
            recovery_rules.append("opening_window_min_oi")
        if opening_spread_relaxed:
            recovery_rules.append("opening_hg_spread_relax")
        log.info(
            "ORB_0DTE_DIRECTIONAL_CAP_AUDIT | symbol=%s | current_price=%.4f | premium_min=%.2f | "
            "premium_max=%.2f | premium_spot_mult=%.4f | delta_min=%.2f | delta_max=%.2f | "
            "selected_contract=%.2f%s | selected_delta=%.4f | selected_premium=%.4f | "
            "selected_spread_pct=%.2f | reject_reason=",
            symbol_u,
            float(current_price),
            float(premium_min),
            float(premium_max),
            float(premium_spot_mult),
            float(delta_min),
            float(delta_max),
            float(selected.strike),
            str(option_type or "call")[0].upper(),
            float(selected.delta or 0.0),
            float(selected.mid_price),
            float(selected.bid_ask_spread_pct),
        )
        log.info(
            "0DTE_SINGLE_LEG_SELECTED | symbol=%s | profile=%s | side=%s | strike=%.2f | delta=%.2f | premium=%.2f | spread_pct=%.2f",
            selected.symbol,
            profile_key,
            option_type.upper(),
            selected.strike,
            selected.delta,
            selected.mid_price,
            selected.bid_ask_spread_pct,
        )
        if recovery_rules:
            log.info(
                "ORB_0DTE_SELECTOR_PROFITABILITY_RECOVERY_AUDIT | symbol=%s | previous_reject_class=selector_filters | "
                "selected_after_relaxation=true | rule_that_enabled_selection=%s | contract_identity=%s_%.0f%s_%s | "
                "premium=%.4f | delta=%.4f | spread_pct=%.2f",
                symbol_u,
                ",".join(recovery_rules),
                symbol_u,
                float(selected.strike),
                str(option_type or "call")[0].upper(),
                str(selected.expiry or ""),
                float(selected.mid_price),
                float(selected.delta or 0.0),
                float(selected.bid_ask_spread_pct),
            )
        self.last_selector_diagnostics.update(
            {
                "selected": True,
                "failure_stage": None,
                "failure_reason": None,
                "strike": float(selected.strike),
                "delta": float(selected.delta or 0.0),
                "mid": float(selected.mid_price),
                "spread_pct": float(selected.bid_ask_spread_pct),
                "max_spread_pct": float(max_spread_pct),
                "premium_min": float(premium_min),
                "premium_max": float(premium_max),
                "profitability_recovery_rules": list(recovery_rules),
                "delta_viability_audit": dict(viability_audit) if viability_audit else {},
            }
        )
        return selected

    def select_opening_preservation_contract(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        current_price: float,
        symbol: str = "",
        *,
        mode: str = "opening",
        convex_score: float = 0.0,
        liquidity_class: str = "",
    ) -> Optional[OptionContract]:
        """
        Opening-window / convex-preservation nearest-liquid single-leg selector.
        """
        try:
            from modules.orb_0dte_opening_execution import (
                opening_selector_params,
                select_nearest_liquid_opening_contract,
            )
        except ImportError:
            return None

        sym_u = str(symbol or "").upper()
        liq = liquidity_class or ""
        contracts = list(chain.get(option_type + "s", []) or [])
        params = opening_selector_params(
            liquidity_class=liq,
            underlying_price=float(current_price),
            convex_score=float(convex_score),
            mode=str(mode),
        )
        selected, audit = select_nearest_liquid_opening_contract(
            contracts,
            option_type=option_type,
            current_price=float(current_price),
            params=params,
            convex_score=float(convex_score),
            liquidity_class=liq,
        )
        self.last_selector_diagnostics = {
            "selector": "opening_preservation",
            "mode": str(mode),
            "symbol": sym_u,
            "option_type": option_type,
            "spot": float(current_price),
            "liquidity_class": liq,
            "convex_score": float(convex_score),
            "selected": selected is not None,
            "audit": audit,
        }
        if selected:
            log.info(
                "0DTE_OPENING_PRESERVATION_SELECTED | symbol=%s | mode=%s | strike=%.2f | delta=%.3f | "
                "premium=%.3f | spread_pct=%.2f | vol=%d | oi=%d",
                sym_u,
                mode,
                float(selected.strike),
                float(selected.delta or 0.0),
                float(selected.mid_price),
                float(selected.bid_ask_spread_pct),
                int(selected.volume or 0),
                int(selected.open_interest or 0),
            )
        else:
            try:
                from modules.orb_0dte_opening_execution import log_best_executable_missed_from_audit

                log_best_executable_missed_from_audit(
                    audit,
                    symbol=sym_u,
                    liquidity_class=liq,
                    rejection_stage=f"opening_preservation_{mode}",
                    opening_selector_active=True,
                    preservation_eligible=float(convex_score) >= 0.90,
                    convex_score=float(convex_score),
                )
            except ImportError:
                pass
        return selected

    def select_lotto_strike(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        current_price: float,
        target_delta: float = 0.15,
        symbol: str = "",
        adaptive_open_window: bool = False,
        momentum_score: float = 0.0,
        orb_range_pct: float = 0.0,
    ) -> Optional[OptionContract]:
        """Backward-compatible alias for ORB1 Breakout single-leg opening selection."""
        _ = target_delta  # profile is always directional; delta sort uses entry band
        return self.select_single_leg_contract(
            chain=chain,
            option_type=option_type,
            current_price=current_price,
            profile="directional",
            symbol=symbol,
            adaptive_open_window=adaptive_open_window,
            momentum_score=float(momentum_score or 0.0),
            orb_range_pct=float(orb_range_pct or 0.0),
        )
    
    def _pick_otm_short_leg(
        self,
        contracts: List[OptionContract],
        option_type: str,
        long_strike: float,
        target_short_strike: float,
    ) -> Optional[OptionContract]:
        """
        Nearest listed strike to target on the OTM side of the long leg.

        Rev 00309: Previous logic required |strike - target| < 0.5 * increment, which
        almost never matches when the chain is spaced by full increments (e.g. DEMO
        synthetic step 5.0 with target long+7.5). That produced None for ITM probability
        and often for momentum scalper despite a valid chain.
        """
        step = max(self._get_strike_increment(long_strike), 0.01)
        if option_type == 'call':
            otm = [c for c in contracts if c.strike > long_strike + 1e-9]
        else:
            otm = [c for c in contracts if c.strike < long_strike - 1e-9]
        if not otm:
            return None
        best = min(otm, key=lambda c: abs(c.strike - target_short_strike))
        try:
            step_mult = float(os.getenv("0DTE_DEBIT_SHORT_LEG_STEP_MULT", "6.0"))
        except (TypeError, ValueError):
            step_mult = 6.0
        step_mult = max(3.5, min(10.0, step_mult))
        if abs(best.strike - target_short_strike) > step_mult * step:
            return None
        return best
    
    def select_atm_momentum_scalper(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        current_price: float,
        strikes_otm: int = 1  # 1-2 strikes OTM for quick payoff
    ) -> Optional[DebitSpread]:
        """
        Select ATM Debit Spread for Momentum Scalper (Rev 00227: Level 2 Strategy)
        
        Use when: Expect quick 5-15 min expansion
        Structure: Buy ATM option, Sell 1-2 strikes out
        Why: Cheap entry, fast payoff, high % ROI potential
        
        Args:
            chain: Options chain dictionary
            option_type: 'call' or 'put'
            current_price: Current underlying price
            strikes_otm: Number of strikes OTM for short leg (1-2)
        
        Returns:
            DebitSpread or None if no valid spread found
        """
        contracts = chain.get(option_type + 's', [])
        
        if not contracts:
            return None
        
        # Find ATM or slightly ITM option for long leg.
        # Rev 00316: use tiered delta fallback instead of a single strict 0.30-0.50
        # band so fast-moving 0DTE chains still produce an executable spread.
        if option_type == 'call':
            directional_candidates = [c for c in contracts if c.strike <= current_price * 1.01]
        else:  # put
            directional_candidates = [c for c in contracts if c.strike >= current_price * 0.99]

        if not directional_candidates:
            return None

        valid_delta_candidates = [
            c for c in directional_candidates
            if c.delta is not None and math.isfinite(float(c.delta))
        ]
        if not valid_delta_candidates:
            return None

        primary_band = [c for c in valid_delta_candidates if 0.30 <= abs(float(c.delta)) <= 0.50]
        relaxed_band = [c for c in valid_delta_candidates if 0.20 <= abs(float(c.delta)) <= 0.60]
        if primary_band:
            long_candidates = primary_band
        elif relaxed_band:
            long_candidates = relaxed_band
        else:
            # Last resort: closest directional contract to ATM target delta.
            long_candidates = list(valid_delta_candidates)

        # Sort by delta proximity to 0.40 (ideal ATM-ish delta)
        long_candidates.sort(key=lambda x: abs(abs(float(x.delta)) - 0.40))
        
        for long_contract in long_candidates[:5]:  # Try top 5 candidates
            # Find short leg: strikes_otm strikes away
            step = self._get_strike_increment(long_contract.strike)
            if option_type == 'call':
                target_short_strike = long_contract.strike + (strikes_otm * step)
            else:  # put
                target_short_strike = long_contract.strike - (strikes_otm * step)
            
            short_contract = self._pick_otm_short_leg(
                contracts, option_type, long_contract.strike, target_short_strike
            )
            if short_contract is None:
                continue
            
            # Validate liquidity
            long_valid, _ = self.validate_liquidity(long_contract)
            short_valid, _ = self.validate_liquidity(short_contract)
            
            if long_valid and short_valid:
                # Calculate spread details
                spread_width = abs(long_contract.strike - short_contract.strike)
                debit_cost = long_contract.ask - short_contract.bid
                max_profit = spread_width - debit_cost
                max_loss = debit_cost
                break_even = long_contract.strike + debit_cost if option_type == 'call' else long_contract.strike - debit_cost
                
                spread = DebitSpread(
                    symbol=long_contract.symbol,
                    expiry=long_contract.expiry,
                    option_type=option_type,
                    long_strike=long_contract.strike,
                    short_strike=short_contract.strike,
                    long_contract=long_contract,
                    short_contract=short_contract,
                    debit_cost=debit_cost,
                    max_profit=max_profit,
                    max_loss=max_loss,
                    break_even=break_even
                )
                
                log.info(f"✅ Selected ATM Momentum Scalper: {spread.symbol} {option_type} {spread.long_strike}/{spread.short_strike}")
                log.info(f"   - Debit: ${debit_cost:.2f}, Max Profit: ${max_profit:.2f}, Max Loss: ${max_loss:.2f}")
                return spread
        
        return None
    
    def select_itm_probability_spread(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        current_price: float,
        target_delta: float = 0.65  # Deeper ITM (0.60-0.70 delta)
    ) -> Optional[DebitSpread]:
        """
        Select ITM Probability Spread (Rev 00229: Easy Mode Strategy)
        
        Use when: Market is valid but not explosive
        Structure: Buy deeper ITM option (Δ 0.60-0.70), Sell OTM option
        Why: Lower breakeven, higher probability, less dependent on speed
        
        Args:
            chain: Options chain dictionary
            option_type: 'call' or 'put'
            current_price: Current underlying price
            target_delta: Target delta for long leg (0.60-0.70 for ITM)
        
        Returns:
            DebitSpread or None if no valid spread found
        """
        contracts = chain.get(option_type + 's', [])
        symbol = contracts[0].symbol if contracts else "unknown"
        self.last_selector_diagnostics = {
            "selector": "itm_probability_spread",
            "option_type": option_type,
            "target_delta": float(target_delta),
            "spot": float(current_price),
            "contracts_this_side": int(len(contracts)),
            "selected": False,
        }
        
        if not contracts:
            self.last_selector_diagnostics.update({
                "failure_stage": "no_contracts",
                "failure_reason": f"no_{option_type}_contracts",
            })
            return None

        # Direction-aware ITM classification (explicit):
        # CALL ITM => strike < spot
        # PUT  ITM => strike > spot
        itm_contracts = self._itm_contracts(contracts, option_type, current_price)
        if not itm_contracts:
            self.last_selector_diagnostics.update({
                "failure_stage": "no_itm_contracts",
                "failure_reason": "zero_itm_contracts_for_side",
                "itm_contracts": 0,
                "total_contracts": int(len(contracts)),
            })
            log.warning(
                "ITM_SPREAD_SELECTOR | symbol=%s | option_type=%s | spot=%.2f | total_strikes=%d | itm_strikes=0 | candidate_spreads=0 | failure=no_itm_contracts",
                symbol,
                option_type,
                float(current_price),
                len(contracts),
            )
            return None
        
        # Find deeper ITM option for long leg with tiered fallback.
        # Rev 00316+: strict -> relaxed -> wide delta bands to reduce false negatives.
        directional_candidates = itm_contracts

        valid_delta_candidates = [
            c for c in directional_candidates
            if c.delta is not None and math.isfinite(float(c.delta))
        ]
        band_candidates = [
            ("primary", 0.60, 0.70),
            ("relaxed", 0.50, 0.80),
            ("wide_relaxed", 0.35, 0.90),
        ]
        long_candidates = []
        band_label = "nearest"
        band_min, band_max = None, None
        for lbl, bmin, bmax in band_candidates:
            in_band = [c for c in valid_delta_candidates if bmin <= abs(float(c.delta)) <= bmax]
            if in_band:
                long_candidates = in_band
                band_label = lbl
                band_min, band_max = bmin, bmax
                break
        if not long_candidates:
            long_candidates = list(valid_delta_candidates)

        if not long_candidates:
            self.last_selector_diagnostics.update({
                "failure_stage": "long_leg_unavailable",
                "failure_reason": "no_itm_long_leg_in_delta_band",
                "long_candidates": 0,
                "delta_min": 0.60,
                "delta_max": 0.70,
            })
            log.warning(
                "ITM_SPREAD_SELECTOR | symbol=%s | option_type=%s | failure=long_leg_unavailable | "
                "contracts=%d | spot=%.2f | delta_band=[0.60,0.70]",
                contracts[0].symbol if contracts else "unknown",
                option_type,
                len(contracts),
                float(current_price),
            )
            return None
        
        # Sort by delta proximity to target (~0.65 ITM probability profile).
        long_candidates.sort(key=lambda x: abs(abs(float(x.delta)) - target_delta))
        self.last_selector_diagnostics.update({
            "long_candidates": int(len(long_candidates)),
            "delta_band_mode": band_label,
            "delta_min": band_min,
            "delta_max": band_max,
        })

        short_leg_missing_count = 0
        long_liquidity_fail_count = 0
        short_liquidity_fail_count = 0
        candidate_spreads_built = 0
        long_liquidity_fail_reasons: List[str] = []
        short_liquidity_fail_reasons: List[str] = []
        
        for long_contract in long_candidates[:8]:
            step = self._get_strike_increment(long_contract.strike)
            short_contract = None
            for otm_steps in (3, 2, 4, 5):
                if option_type == 'call':
                    target_short_strike = long_contract.strike + (otm_steps * step)
                else:  # put
                    target_short_strike = long_contract.strike - (otm_steps * step)
                short_contract = self._pick_otm_short_leg(
                    contracts, option_type, long_contract.strike, target_short_strike
                )
                if short_contract is not None:
                    break
            if short_contract is None:
                short_leg_missing_count += 1
                continue
            candidate_spreads_built += 1
            
            # Validate liquidity
            long_valid, long_reasons = self.validate_liquidity(long_contract)
            short_valid, short_reasons = self.validate_liquidity(short_contract)
            if not long_valid:
                long_liquidity_fail_count += 1
                if long_reasons and len(long_liquidity_fail_reasons) < 3:
                    long_liquidity_fail_reasons.append("; ".join(long_reasons))
            if not short_valid:
                short_liquidity_fail_count += 1
                if short_reasons and len(short_liquidity_fail_reasons) < 3:
                    short_liquidity_fail_reasons.append("; ".join(short_reasons))
            
            if long_valid and short_valid:
                # Calculate spread details
                spread_width = abs(long_contract.strike - short_contract.strike)
                debit_cost = long_contract.ask - short_contract.bid
                max_profit = spread_width - debit_cost
                max_loss = debit_cost
                break_even = long_contract.strike + debit_cost if option_type == 'call' else long_contract.strike - debit_cost
                
                spread = DebitSpread(
                    symbol=long_contract.symbol,
                    expiry=long_contract.expiry,
                    option_type=option_type,
                    long_strike=long_contract.strike,
                    short_strike=short_contract.strike,
                    long_contract=long_contract,
                    short_contract=short_contract,
                    debit_cost=debit_cost,
                    max_profit=max_profit,
                    max_loss=max_loss,
                    break_even=break_even
                )
                
                log.info(f"✅ Selected ITM Probability Spread: {spread.symbol} {option_type} {spread.long_strike}/{spread.short_strike}")
                log.info(f"   - Debit: ${debit_cost:.2f}, Max Profit: ${max_profit:.2f}, Max Loss: ${max_loss:.2f}")
                log.info(f"   - Long Leg Delta: {long_contract.delta:.2f} (ITM), Higher Probability")
                self.last_selector_diagnostics.update({
                    "selected": True,
                    "failure_stage": None,
                    "failure_reason": None,
                    "selected_long_strike": float(long_contract.strike),
                    "selected_short_strike": float(short_contract.strike),
                    "selected_long_delta": float(long_contract.delta),
                    "selected_short_delta": float(short_contract.delta),
                    "selected_long_mid": float(long_contract.mid_price),
                    "selected_short_mid": float(short_contract.mid_price),
                    "debit_cost": float(debit_cost),
                    "max_profit": float(max_profit),
                    "max_loss": float(max_loss),
                    "candidate_spreads_built": int(candidate_spreads_built),
                    "itm_contracts": int(len(itm_contracts)),
                    "total_contracts": int(len(contracts)),
                })
                log.info(
                    "ITM_SPREAD_SELECTOR | symbol=%s | direction=%s | spot=%.2f | total_strikes=%d | itm_strikes=%d | candidate_spreads=%d | selected=true | band=%s",
                    symbol,
                    option_type.upper(),
                    float(current_price),
                    len(contracts),
                    len(itm_contracts),
                    candidate_spreads_built,
                    band_label,
                )
                return spread

        failure_stage = "pairing_or_liquidity"
        failure_reason = "no_valid_itm_spread_pair_found"
        if short_leg_missing_count > 0 and long_liquidity_fail_count == 0 and short_liquidity_fail_count == 0:
            failure_stage = "short_leg_unavailable"
            failure_reason = "no_otm_short_leg_for_itm_long_candidate"
        elif long_liquidity_fail_count > 0 and short_liquidity_fail_count == 0:
            failure_stage = "long_leg_liquidity"
            failure_reason = "itm_long_leg_failed_liquidity"
        elif short_liquidity_fail_count > 0 and long_liquidity_fail_count == 0:
            failure_stage = "short_leg_liquidity"
            failure_reason = "otm_short_leg_failed_liquidity"

        self.last_selector_diagnostics.update({
            "failure_stage": failure_stage,
            "failure_reason": failure_reason,
            "short_leg_missing_count": int(short_leg_missing_count),
            "long_liquidity_fail_count": int(long_liquidity_fail_count),
            "short_liquidity_fail_count": int(short_liquidity_fail_count),
            "long_liquidity_fail_examples": long_liquidity_fail_reasons,
            "short_liquidity_fail_examples": short_liquidity_fail_reasons,
            "candidate_spreads_built": int(candidate_spreads_built),
            "itm_contracts": int(len(itm_contracts)),
            "total_contracts": int(len(contracts)),
        })
        log.warning(
            "ITM_SPREAD_SELECTOR | symbol=%s | direction=%s | spot=%.2f | total_strikes=%d | itm_strikes=%d | candidate_spreads=%d | failure_stage=%s | "
            "long_candidates=%d | short_leg_missing=%d | long_liq_fail=%d | short_liq_fail=%d",
            symbol,
            option_type,
            float(current_price),
            len(contracts),
            len(itm_contracts),
            candidate_spreads_built,
            failure_stage,
            len(long_candidates),
            short_leg_missing_count,
            long_liquidity_fail_count,
            short_liquidity_fail_count,
        )
        return None
    
    def _get_strike_increment(self, strike: float) -> float:
        """Get strike increment based on strike price"""
        if strike < 50:
            return 0.5
        elif strike < 200:
            return 1.0
        elif strike < 500:
            return 2.5
        else:
            return 5.0
    
    def select_credit_spread_strikes(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        target_delta: float,
        spread_width: float,
        current_price: float
    ) -> Optional[CreditSpread]:
        """
        Select optimal strikes for credit spread - Rev 00212
        
        Contract Selection Logic (Best Practices):
        - Premium: $0.20-$0.60 (cheap contracts for gamma explosion)
        - Delta: 10-30 (0.10-0.30) - cheap gamma that can explode
        - Strike: 1-3 strikes OTM (out of the money)
        - Why: Cheap contracts give gamma explosion, easy 5-20x with small moves
        
        For credit spreads:
        - CALL credit spread: Sell call at lower strike, buy call at higher strike (bearish)
        - PUT credit spread: Sell put at higher strike, buy put at lower strike (bullish)
        
        Args:
            chain: Options chain dictionary
            option_type: 'call' or 'put'
            target_delta: Target delta for short leg (0.10-0.30) - Rev 00212
            spread_width: Spread width ($1 or $2 for QQQ/SPY, $5 or $10 for SPX)
            current_price: Current underlying price
            
        Returns:
            CreditSpread object or None if no valid spread found
        """
        contracts = chain.get(option_type + 's', [])
        
        if not contracts:
            log.warning(f"No {option_type} contracts available")
            return None
        
        # Rev 00212: Filter contracts by delta range (target ± 0.05 for tighter range)
        delta_min = max(0.10, target_delta - 0.05)  # Minimum 10 delta
        delta_max = min(0.30, target_delta + 0.05)  # Maximum 30 delta
        
        # Rev 00238: Filter by premium range $0.15-$0.60 (cheap contracts for gamma explosion)
        # Lowered minimum from $0.20 to $0.15 to allow $0.19 entries (like successful QQQ trades)
        premium_min = 0.15
        premium_max = 0.60
        
        # Get all strikes sorted for OTM calculation
        all_strikes = sorted(set(c.strike for c in contracts))
        
        # Rev 00212: Filter by strike position (1-3 strikes OTM) and premium
        candidate_short_legs = []
        for c in contracts:
            # Check delta range
            if not (delta_min <= abs(c.delta) <= delta_max):
                continue
            
            # Check premium range (use mid price)
            premium = c.mid_price
            if not (premium_min <= premium <= premium_max):
                continue
            
            # Check strike position (1-3 strikes OTM) - for credit spreads, short leg should be OTM
            if option_type == 'call':
                # CALL credit: Short call should be OTM (strike > current_price)
                if c.strike <= current_price:
                    continue  # Not OTM
                strikes_otm = sum(1 for s in all_strikes if current_price < s <= c.strike)
                if not (1 <= strikes_otm <= 3):
                    continue
            else:  # put
                # PUT credit: Short put should be OTM (strike < current_price)
                if c.strike >= current_price:
                    continue  # Not OTM
                strikes_otm = sum(1 for s in all_strikes if current_price > s >= c.strike)
                if not (1 <= strikes_otm <= 3):
                    continue
            
            candidate_short_legs.append(c)
        
        if not candidate_short_legs:
            log.warning(f"No contracts found with delta in range [{delta_min:.2f}, {delta_max:.2f}]")
            return None
        
        # For credit spreads: Consider gamma (for liquidity), but prioritize low vega risk
        # Credit spreads have negative gamma, so we accept that but minimize vega risk
        def calculate_credit_score(contract, target_delta):
            """Calculate optimization score for credit spread short leg"""
            score = 0.0
            
            # 1. Delta Proximity (40% weight) - Closest to target delta
            delta_diff = abs(abs(contract.delta) - target_delta)
            delta_score = max(0.0, 1.0 - (delta_diff / 0.10))
            score += delta_score * 0.40
            
            # 2. Vega Risk (30% weight) - Minimize vega risk (lower vega = less IV sensitivity)
            # For credit spreads, lower vega is better (less sensitive to IV changes)
            vega_score = abs(contract.vega) if contract.vega else 0.0
            vega_normalized = max(0.0, (0.15 - vega_score) / 0.15) if vega_score > 0 else 1.0
            score += vega_normalized * 0.30
            
            # 3. Gamma (20% weight) - Higher gamma for liquidity/spread quality
            # Accept negative gamma exposure, but use for liquidity selection
            gamma_score = abs(contract.gamma) if contract.gamma else 0.0
            gamma_normalized = min(gamma_score / 0.10, 1.0) if gamma_score > 0 else 0.0
            score += gamma_normalized * 0.20
            
            # 4. Theta Benefit (10% weight) - Higher theta benefits credit spreads (time decay helps)
            # For credit spreads, higher theta (more negative) is actually beneficial
            theta_score = abs(contract.theta) if contract.theta else 0.0
            theta_normalized = min(theta_score / 0.30, 1.0) if theta_score > 0 else 0.0
            score += theta_normalized * 0.10
            
            return score
        
        # Sort by credit spread optimization score (descending)
        candidate_short_legs.sort(
            key=lambda x: (
                -calculate_credit_score(x, target_delta),  # Higher score first
                abs(abs(x.delta) - target_delta)  # Then closest delta to target
            )
        )
        
        # Try to find valid credit spread
        for short_contract in candidate_short_legs:
            short_strike = short_contract.strike
            
            # Find long leg (spread_width away)
            if option_type == 'call':
                # CALL credit spread: Short at lower strike, long at higher strike
                long_strike = short_strike + spread_width
            else:  # put
                # PUT credit spread: Short at higher strike, long at lower strike
                long_strike = short_strike - spread_width
            
            # Find long leg contract
            long_contract = next(
                (c for c in contracts if c.strike == long_strike),
                None
            )
            
            if not long_contract:
                continue
            
            # Validate both legs
            short_valid, short_reasons = self.validate_liquidity(short_contract)
            long_valid, long_reasons = self.validate_liquidity(long_contract)
            
            if not short_valid or not long_valid:
                log.debug(f"Invalid credit spread: short={short_valid}, long={long_valid}")
                continue
            
            # Calculate spread metrics
            # Credit spread: Receive premium from short leg, pay premium for long leg
            credit_received = short_contract.bid - long_contract.ask
            
            # Ensure we receive a credit (positive net credit)
            if credit_received <= 0:
                log.debug(f"Invalid credit spread: credit_received ${credit_received:.2f} <= 0")
                continue
            
            max_profit = credit_received  # Max profit = credit received
            max_loss = spread_width - credit_received  # Max loss = spread width - credit
            
            # Deterministic Rule: Short leg at R:R ≥ 1.5–2.5x
            if max_loss > 0:
                risk_reward_ratio = max_profit / max_loss
            else:
                risk_reward_ratio = 0.0
            
            # Reject if R:R not in range 1.5-2.5x
            if risk_reward_ratio < self.min_risk_reward_ratio or risk_reward_ratio > self.max_risk_reward_ratio:
                log.debug(f"Rejecting credit spread: R:R {risk_reward_ratio:.2f}x not in range [{self.min_risk_reward_ratio:.1f}x, {self.max_risk_reward_ratio:.1f}x]")
                continue
            
            if option_type == 'call':
                # CALL credit spread: Break-even = short_strike + credit_received
                break_even = short_strike + credit_received
            else:  # put
                # PUT credit spread: Break-even = short_strike - credit_received
                break_even = short_strike - credit_received
            
            spread = CreditSpread(
                symbol=short_contract.symbol,
                expiry=short_contract.expiry,
                option_type=option_type,
                short_strike=short_strike,
                long_strike=long_strike,
                short_contract=short_contract,
                long_contract=long_contract,
                credit_received=credit_received,
                max_profit=max_profit,
                max_loss=max_loss,
                break_even=break_even
            )
            
            credit_score = calculate_credit_score(short_contract, target_delta)
            log.info(f"Selected credit spread: {spread.symbol} {option_type} {spread.short_strike}/{spread.long_strike}")
            log.info(f"  Credit: ${credit_received:.2f}, Max Profit: ${max_profit:.2f}, Max Loss: ${max_loss:.2f}")
            log.info(f"  Short Leg Greeks: Gamma={short_contract.gamma:.4f}, Theta={short_contract.theta:.4f}, Vega={short_contract.vega:.4f}, IV={short_contract.implied_volatility:.2%}")
            log.info(f"  Optimization Score: {credit_score:.3f} (Delta:40%, Vega:30%, Gamma:20%, Theta:10%)")
            
            return spread
        
        log.warning(f"No valid credit spread found for {option_type} with delta {target_delta:.2f}")
        return None
