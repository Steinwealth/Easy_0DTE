# Trade Profile overview

**Last updated:** November 7, 2026  
**Spec:** [../EXECUTIVE_SUMMARY.md](../EXECUTIVE_SUMMARY.md) · **Architecture:** [ARCHITECTURE.md](ARCHITECTURE.md)

This is the public description of how Easy 0DTE admits a discovered CALL or PUT. It covers structure, methodology, and labeled research outcomes. **Per-cell min/max bounds are proprietary and are not published here.**

Serving admit is **Trade Profile Wide**. **Trade Profile 10.23** is the current in-sample research successor. It is not the live admit.

---

## 1. What a Trade Profile is

Every discovered signal takes one admit path. There is no second quality stack on the serving image.

1. **Must Execute** checks feasibility: geometry, cooldown, book-full (no chain call when full), one chain read, data readiness, Call Skip Signal and Put Skip Signal, then one contract pick.
2. **One stamp.** `decision_stamp` freezes the keys the profile will read. `decision_digest` is the hash of that dict. A digest mismatch rejects `data_unavailable:stamp_parity`.
3. **One profile evaluation** against the loaded JSON template.
4. **Accept or reject** (`tp_admit:<cell>` or `tp_reject:<key>:<min|max|missing|not_and>`).
5. **Size** after accept: $600 sum cap and the 50-slot book.

Call Skip Signal and Put Skip Signal stay **outside** the profile and are hard. A missing share or directional-percent input holds the break for up to **90 seconds**, then retires it with no cooldown.

---

## 2. Structure — 10 cells × 27 keys

10.23 is a flat **`flat-27-key-cells-v2`** profile: **10 path/side cells** × **27 keys**.

**Cells (5 path groups × CALL / PUT)**

| Group | CALL cell | PUT cell |
|-------|-----------|----------|
| ORB1 | ORB1 CALL | ORB1 PUT |
| Trendline TL0 | TL0 CALL | TL0 PUT |
| Trendline TL1 | TL1 CALL | TL1 PUT |
| Trendline TL2+ | TL2+ CALL | TL2+ PUT |
| Breakout (ORB2) | Breakout CALL | Breakout PUT |

Each cell uses the official 27-key template (approved 17 October 2026). A key may be a min, a max, a min–max range, or **PASS**. Listing a key does not activate a bound. `call_share` / `put_share` are derived from stamped chain volumes. PUT `delta` is `abs(delta)`. `dir_pct_change_5m` stays on the template and is not a 10.23 gate.

### CALL template

| Group | Keys |
|-------|------|
| Chain Participation | `call_chain_volume`, `put_chain_volume`, `total_chain_vol_oi`, `call_chain_vol_oi`, `put_chain_vol_oi`, `put_chain_open_interest`, `call_chain_open_interest`, `call_share`, `put_share` |
| Option / Market Quality | `theta`, `chain_sanity_best_same_side_spread_pct`, `atr`, `vega` |
| Trade / Break Structure | `price_volume_efficiency_3m`, `break_distance_pct`, `otm_pct`, `iv`, `gamma` |
| Directional / Market Context | `spy_change_pct`, `dir_pct_change_5m`, `distance_from_vwap_atr`, `rsi`, `bollinger_position`, `bollinger_width` |
| Order-Book / Contract Context | `underlying_l1_imbalance`, `option_l1_imbalance`, `delta` |

### PUT template

Same 27 names; order inside the groups differs:

| Group | Keys |
|-------|------|
| Chain Participation | `call_chain_volume`, `put_chain_volume`, `total_chain_vol_oi`, `put_chain_vol_oi`, `call_chain_vol_oi`, `put_chain_open_interest`, `call_chain_open_interest`, `put_share`, `call_share` |
| Option / Market Quality | `theta`, `atr`, `chain_sanity_best_same_side_spread_pct`, `vega` |
| Trade / Break Structure | `price_volume_efficiency_3m`, `break_distance_pct`, `gamma`, `otm_pct`, `iv` |
| Directional / Market Context | `dir_pct_change_5m`, `spy_change_pct`, `distance_from_vwap_atr`, `rsi`, `bollinger_position`, `bollinger_width` |
| Order-Book / Contract Context | `option_l1_imbalance`, `underlying_l1_imbalance`, `delta` |

---

## 3. Serving admit — Wide

Cloud Run loads `easy0DTE/configs/trade_profile_wide.json`. `TRADE_PROFILE_VERSION` stays `wide` until an explicit admit deploy selects 10.23.

Wide is the **collection** profile: a small set of same-side share, break-distance, and volume-ratio gates so sessions remain collectable for later replay. A missing key generally passes, except `equity_volume_ratio` (reject) and the share key (defer). Exact serving numbers live in [../EXECUTIVE_SUMMARY.md](../EXECUTIVE_SUMMARY.md#5-admit-trade-profile-wide-training-toward-1023).

The contract pick uses the live chain already in memory. Spread percents are 8 (ORB1), 30 (ORB2), and 30 (Trendline), or an ask−bid of at most $0.01. Premium band **$0.03–$2.50**.

---

## 4. Research successor — 10.23

`easy0DTE/configs/trade_profile_10.23.json` is the working research profile (local **S0_maxnet**). Bounds were mined against Wide-collected sessions and then replayed through the same PATH_V2 exit engine the service uses.

**Latest verified in-sample replay (August 19–November 6, 2026):**

| | |
|--|--|
| Trades | 542 |
| Wins / losses | 436 / 106 |
| Win rate | 80.4% |
| Fee-inclusive modeled net P&L | +$159,400.39 |

These figures are **historical in-sample replay** on Wide-collected sessions. They are **not** the running Demo ledger and **not** live investment returns. Forward validation has not passed. Live certification requires **60 profitable Pre trained sessions**. Current Pre trained streak: **0/11**.

Per-cell threshold bounds are not published. Operator bound logs stay in the private research tree.

---

## 5. Related guides

| Topic | Document |
|-------|----------|
| Product specification | [../EXECUTIVE_SUMMARY.md](../EXECUTIVE_SUMMARY.md) |
| Session order | [ProcessFlow.md](ProcessFlow.md) |
| Strike to fill | [0.OrderExecution.md](0.OrderExecution.md) |
| Slots, sizing, calendar | [0DTERisk.md](0DTERisk.md) |
| Exits | [0DTEProfitCapture.md](0DTEProfitCapture.md) |
| Training snapshots | [PriorityOptimizer.md](PriorityOptimizer.md) |
| Desk / Training Calendar | [Frontend.md](Frontend.md) |
