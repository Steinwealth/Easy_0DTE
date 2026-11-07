"""
Profit Optimizer — read-only session flight recorder and post-exit shadow tracker
for ORB SO, ORB 0DTE, and Trendline 0DTE executed trades.

Persists premium paths to GCS for post-session exit review.
Not an offline exit replay engine. Does not affect entries, exits, ranking,
sizing, or stealth trailing.
"""

from profit_optimizer.profit_optimizer import ProfitOptimizer, profit_optimizer_enabled

__all__ = ["ProfitOptimizer", "profit_optimizer_enabled"]
