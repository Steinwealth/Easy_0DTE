"""Pre-draw Trendline defaults only (simple break mode — no post-draw profile bundle)."""

from __future__ import annotations

# Canonical switch is TRENDLINE_BREAK_MODE in Trendline0DTE.env (default true).
# Do not inject the retired TRENDLINE_SIMPLE_BREAK_MODE alias.
TRENDLINE_ENTRY_BASE_DEFAULTS: dict[str, str] = {}

# Legacy post-draw keys removed — not injected via TRENDLINE_ENTRY_PROFILE when simple mode is on.
TRENDLINE_LEGACY_POST_DRAW_PROFILE_KEYS: frozenset[str] = frozenset(
    k
    for k in (
        "TRENDLINE_ENTRY_SCORE_MIN_IMPULSE",
        "TRENDLINE_ENTRY_SCORE_MIN_CONTINUATION",
        "TRENDLINE_BOUNCEBACK_RECLAIM_MAX_BARS",
        "TRENDLINE_EMIT_BLOCK_CHOPPY_STRUCTURE",
        "TRENDLINE_REARM_ENABLED",
        "TRENDLINE_EXPANSION_NET_MOVE_RATIO_MIN",
    )
)
