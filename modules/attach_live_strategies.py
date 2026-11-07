#!/usr/bin/env python3
"""Attach 0DTE, Trendline, and Breakout once a live broker session exists.

Startup and OAuth renewal share this function. If the process booted before
the token was valid, engines stay deferred and this runs again when live
data access is restored. It does not rebuild engines that are already up.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("improved_main")


async def attach_live_strategies(system: Any, etrade_trader: Optional[Any]) -> None:
    """Wire strategy engines that require live broker data. Idempotent when already attached."""
    from modules.live_data_access import engines_missing, strategies_requiring_live_data

    if not engines_missing(system):
        logger.info("LIVE_DATA_ACCESS | already_attached=true")
        system._live_data_deferred = False
        return

    required = strategies_requiring_live_data()
    if required and etrade_trader is None:
        system._live_data_deferred = True
        system._live_data_deferred_reason = "broker_session_unavailable"
        logger.warning(
            "LIVE_DATA_DEFERRED | reason=broker_session_unavailable | engines=%s | "
            "retry_on=oauth_token_renewed",
            ",".join(required),
        )
        return

    options_chain_manager = None
    require_live_option_data = os.getenv("REQUIRE_LIVE_OPTION_DATA", "true").lower() == "true"
    etrade_options_api = None
    options_executor = None
    dte0_manager = None

    # Initialize Easy 0DTE (if enabled)
    dte0_manager = None
    etrade_options_api = None  # shared for 0DTE + optional Trendline live option quotes
    options_executor = None
    ENABLE_0DTE_STRATEGY = os.getenv('ENABLE_0DTE_STRATEGY', 'false').lower() == 'true'

    # Debug logging for 0DTE strategy enablement
    logger.info(f"🔧 ENABLE_0DTE_STRATEGY environment variable: {os.getenv('ENABLE_0DTE_STRATEGY', 'NOT_SET')}")
    logger.info(f"🔧 ENABLE_0DTE_STRATEGY parsed value: {ENABLE_0DTE_STRATEGY}")

    if ENABLE_0DTE_STRATEGY:
        logger.info("🎯 Initializing Easy 0DTE...")
        try:
            # Add Easy 0DTE modules to Python path
            import sys
            # Try multiple path locations for Easy 0DTE
            # 1. Same directory as main.py (./easy0DTE) - NEW STRUCTURE
            # 2. Absolute path in container (/app/easy0DTE)
            # 3. Legacy path for backwards compatibility (../1. The Easy 0DTE Strategy)
            _app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            dte_strategy_paths = [
                os.path.join(_app_root, 'easy0DTE'),
                '/app/easy0DTE',
                os.path.join(_app_root, '../1. The Easy 0DTE Strategy')  # Legacy fallback
            ]
            dte_strategy_path = None
            for path in dte_strategy_paths:
                if os.path.exists(path):
                    dte_strategy_path = path
                    break
            
            if dte_strategy_path and os.path.exists(dte_strategy_path):
                # Add both the base path and modules path to sys.path
                sys.path.insert(0, dte_strategy_path)
                modules_path = os.path.join(dte_strategy_path, 'modules')
                if os.path.exists(modules_path):
                    sys.path.insert(0, modules_path)
                logger.info(f"✅ Added Easy 0DTE path: {dte_strategy_path}")
            
            # Import 0DTE modules (try both import styles)
            # Rev 00247 (Jan 20, 2026): Fixed import paths for 0DTE modules
            try:
                # Try easy0DTE.modules first (correct path)
                from easy0DTE.modules.convex_eligibility_filter import ConvexEligibilityFilter
                from easy0DTE.modules.prime_0dte_strategy_manager import Prime0DTEStrategyManager
                from easy0DTE.modules.options_chain_manager import OptionsChainManager
                from easy0DTE.modules.options_trading_executor import OptionsTradingExecutor
                from easy0DTE.modules.mock_options_executor import MockOptionsExecutor
                logger.info("✅ Loaded 0DTE modules from easy0DTE.modules")
            except ImportError:
                # Fallback: try modules path (for backward compatibility)
                try:
                    from modules.convex_eligibility_filter import ConvexEligibilityFilter
                    from modules.prime_0dte_strategy_manager import Prime0DTEStrategyManager
                    from modules.options_chain_manager import OptionsChainManager
                    from modules.options_trading_executor import OptionsTradingExecutor
                    from modules.mock_options_executor import MockOptionsExecutor
                    logger.info("✅ Loaded 0DTE modules from modules (fallback)")
                except ImportError as e:
                    logger.error(f"❌ Failed to import 0DTE modules from both paths: {e}")
                    raise
            
            # Determine Demo/Live mode for 0DTE (canonical MODE resolver)
            from modules.runtime_mode import is_demo_mode as runtime_is_demo_mode

            runtime_demo = runtime_is_demo_mode()
            require_live_option_data = True
            os.environ['REQUIRE_LIVE_OPTION_DATA'] = 'true'
            os.environ['0DTE_DEMO_SYNTHETIC_CHAIN'] = 'false'
            os.environ['0DTE_DEMO_ALLOW_DEGRADED_CHAIN'] = 'false'
            synthetic_chain_enabled = False
            
            # Initialize 0DTE components
            convex_filter = ConvexEligibilityFilter(
                orb_range_min_pct=float(os.getenv('0DTE_CONVEX_ORB_RANGE_MIN', '0.25')),
                trend_day_required=os.getenv('0DTE_CONVEX_TREND_DAY_REQUIRED', 'true').lower() == 'true',
            )
            
            # Initialize ETrade Options API for both Demo and Live modes (data fidelity parity)
            try:
                if not etrade_trader:
                    raise RuntimeError(
                        "E*TRADE trader unavailable — live option chain required; no synthetic chain"
                    )
                # Rev 00247 (Jan 20, 2026): Canonical options API module
                from easy0DTE.modules.etrade_options_api import ETradeOptionsAPI

                logger.info("✅ Loaded ETradeOptionsAPI from easy0DTE.modules")
                
                # Rev 00245: Use broker config manager for 0DTE account selection
                from modules.broker_config_manager import get_broker_config_manager
                broker_config = get_broker_config_manager()
                
                # Get Easy 0DTE account configuration
                dte_account_config = broker_config.get_dte_account_config()
                
                # Backward compatibility: Also check environment variables
                dte_account_id = os.getenv('0DTE_ETRADE_ACCOUNT_ID', None)
                if not dte_account_id and dte_account_config and dte_account_config.broker_type.value == 'etrade':
                    dte_account_id = dte_account_config.account_id

                # Demo Mode: use E*TRADE for live chains/quotes only — never select Live trading account IDs.
                if runtime_demo and dte_account_id:
                    logger.info(
                        "DEMO_ACCOUNT_POLICY | skip_0dte_account_select | account_id=%s | "
                        "reason=demo_mock_ledger_ownership | etrade_market_data_only=true",
                        dte_account_id,
                    )
                    dte_account_id = None
                
                dte_secret_name = os.getenv('0DTE_ETRADE_SECRET_NAME', None)
                
                if dte_account_id:
                    # Use separate account for Easy 0DTE
                    logger.info(f"🔗 Initializing Easy 0DTE with {dte_account_config.broker_type.value.upper()} account: {dte_account_id}")
                    
                    # Option 1: Use separate ETrade instance (if separate OAuth tokens)
                    if dte_secret_name:
                        # TODO: Support separate OAuth tokens via custom PrimeETradeTrading instance
                        # For now, use shared instance but select separate account
                        logger.info(f"   Using separate OAuth tokens from: {dte_secret_name}")
                        logger.warning("⚠️ Separate OAuth tokens not yet implemented - using shared tokens")
                    
                    # Initialize with account selection
                    etrade_options_api = ETradeOptionsAPI(
                        etrade_trading=etrade_trader,  # Can use shared instance or create separate
                        environment='prod',  # Sandbox deprecated; production tokens only
                        account_id=dte_account_id  # Select specific account
                    )
                    logger.info(
                        "✅ ETrade Options API initialized for %s mode with account: %s",
                        "DEMO" if runtime_demo else "LIVE",
                        dte_account_id,
                    )
                else:
                    # Use shared ETrade instance (default behavior)
                    etrade_options_api = ETradeOptionsAPI(
                        etrade_trading=etrade_trader,
                        environment='prod'  # Sandbox deprecated; production tokens only
                    )
                    logger.info(
                        "✅ ETrade Options API initialized for %s mode (shared account)",
                        "DEMO" if runtime_demo else "LIVE",
                    )
            except Exception as e:
                logger.error(f"❌ Failed to initialize ETrade Options API: {e}")
                raise RuntimeError(
                    "Live option chain required but ETrade Options API is unavailable"
                ) from e
            
            use_live_chain_data = bool(etrade_options_api and etrade_options_api.is_available())
            if not use_live_chain_data:
                raise RuntimeError(
                    "Live option chain data required but ETrade API is not authenticated/available"
                )
            logger.info(
                "0DTE_CHAIN_SOURCE_CONFIG | mode=%s | require_live=%s | etrade_api_available=%s | use_live_chain_data=%s | synthetic_enabled=%s",
                "demo" if runtime_demo else "live",
                require_live_option_data,
                bool(etrade_options_api and etrade_options_api.is_available()),
                use_live_chain_data,
                synthetic_chain_enabled,
            )
            from easy0DTE.modules.option_entry_liquidity_validator import (
                entry_min_open_interest,
                entry_min_volume,
                log_execution_policy_audit,
            )
            from modules.spread_policy import orb_chain_manager_spread_points

            runtime_min_oi = entry_min_open_interest()
            runtime_min_volume = entry_min_volume()
            runtime_max_spread = orb_chain_manager_spread_points()
            runtime_options_min_oi = int(os.getenv("0DTE_OPTIONS_MIN_OPEN_INTEREST", str(runtime_min_oi)))
            runtime_options_min_volume = int(os.getenv("0DTE_OPTIONS_MIN_VOLUME", str(runtime_min_volume)))
            runtime_options_max_spread = runtime_max_spread
            if (
                runtime_min_oi != runtime_options_min_oi
                or runtime_min_volume != runtime_options_min_volume
                or abs(runtime_max_spread - runtime_options_max_spread) > 1e-9
            ):
                logger.warning(
                    "ORB0DTE_CONFIG_MISMATCH | min_open_interest=%s vs options_min_open_interest=%s | "
                    "min_volume=%s vs options_min_volume=%s | max_spread=%s vs options_max_spread=%s",
                    runtime_min_oi,
                    runtime_options_min_oi,
                    runtime_min_volume,
                    runtime_options_min_volume,
                    runtime_max_spread,
                    runtime_options_max_spread,
                )
            options_chain_manager = OptionsChainManager(
                min_open_interest=runtime_min_oi,
                max_bid_ask_spread_pct=runtime_max_spread,
                min_volume=runtime_min_volume,
                options_min_open_interest=runtime_options_min_oi,
                options_max_bid_ask_spread_pct=runtime_options_max_spread,
                options_min_volume=runtime_options_min_volume,
                single_leg_min_open_interest=int(os.getenv("0DTE_SINGLE_LEG_MIN_OPEN_INTEREST", "500")),
                single_leg_min_volume=int(os.getenv("0DTE_SINGLE_LEG_MIN_VOLUME", "200")),
                single_leg_open_window_minutes=float(os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_MINUTES", "5.0")),
                single_leg_open_window_oi_mult=float(os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_OI_MULT", "0.50")),
                single_leg_open_window_volume_mult=float(os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_VOLUME_MULT", "0.50")),
                liquidity_relax_open_window_minutes=float(os.getenv("0DTE_LIQUIDITY_RELAX_OPEN_WINDOW_MINUTES", "8.0")),
                liquidity_relax_spread_mult=float(os.getenv("0DTE_LIQUIDITY_RELAX_SPREAD_MULT", "1.35")),
                liquidity_relax_open_interest_mult=float(os.getenv("0DTE_LIQUIDITY_RELAX_OI_MULT", "0.50")),
                liquidity_relax_volume_mult=float(os.getenv("0DTE_LIQUIDITY_RELAX_VOLUME_MULT", "0.50")),
                etrade_options_api=etrade_options_api,
                use_live_api=use_live_chain_data
            )
            logger.info(
                "ORB0DTE_RUNTIME_CONFIG_AUDIT | env_key=0DTE_MIN_OPEN_INTEREST | min_open_interest=%s | source=env_or_default(100)",
                runtime_min_oi,
            )
            logger.info(
                "ORB0DTE_RUNTIME_CONFIG_AUDIT | env_key=0DTE_MIN_VOLUME | min_volume=%s | source=env_or_default(50)",
                runtime_min_volume,
            )
            logger.info(
                "ORB0DTE_RUNTIME_CONFIG_AUDIT | env_key=0DTE_MAX_BID_ASK_SPREAD_PCT | max_spread_pct=%.3f | source=spread_policy.orb_chain_manager_spread_points",
                runtime_max_spread,
            )
            logger.info(
                "ORB0DTE_RUNTIME_CONFIG_AUDIT | env_key=0DTE_OPTIONS_MIN_OPEN_INTEREST | options_min_open_interest=%s | source=env_or_unified_with_0DTE_MIN_OPEN_INTEREST",
                runtime_options_min_oi,
            )
            logger.info(
                "ORB0DTE_RUNTIME_CONFIG_AUDIT | env_key=0DTE_OPTIONS_MIN_VOLUME | options_min_volume=%s | source=env_or_unified_with_0DTE_MIN_VOLUME",
                runtime_options_min_volume,
            )
            logger.info(
                "ORB0DTE_RUNTIME_CONFIG_AUDIT | env_key=0DTE_OPTIONS_MAX_BID_ASK_SPREAD_PCT | options_max_spread_pct=%.3f | source=spread_policy_unified_with_0DTE_MAX_BID_ASK_SPREAD_PCT",
                runtime_options_max_spread,
            )
            logger.info(
                "ORB0DTE_RUNTIME_CONFIG_AUDIT | selector=single_leg_directional | min_volume=%s | min_open_interest=%s | open_window_minutes=%s | "
                "open_window_oi_mult=%s | open_window_volume_mult=%s | source=env_or_defaults",
                os.getenv("0DTE_SINGLE_LEG_MIN_VOLUME", "200"),
                os.getenv("0DTE_SINGLE_LEG_MIN_OPEN_INTEREST", "500"),
                os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_MINUTES", "5.0"),
                os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_OI_MULT", "0.50"),
                os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_VOLUME_MULT", "0.50"),
            )
            log_execution_policy_audit(logger)
            
            # Initialize Priority Data Collector (DEMO research default ON; LIVE opt-in)
            priority_collector = None
            _po_collector_default = "true" if runtime_demo else "false"
            if os.getenv("0DTE_PRIORITY_COLLECTOR_ENABLED", _po_collector_default).lower() == "true":
                try:
                    from modules.options_priority_data_collector import OptionsPriorityDataCollector
                    gcs_bucket = os.getenv("GCS_BUCKET_NAME", None)
                    priority_collector = OptionsPriorityDataCollector(
                        base_dir="priority_optimizer/0dte_data",
                        gcs_bucket=gcs_bucket,
                        gcs_prefix="priority_optimizer/0dte_signals",
                    )
                    system._options_priority_collector = priority_collector
                    logger.info("✅ Priority Data Collector initialized for Easy 0DTE (PO snapshot integration)")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to initialize Priority Data Collector: {e}")
            
            # Initialize Mock Options Executor for Demo Mode (simulated ledger).
            mock_options_executor = None
            if runtime_demo:
                seeded_paths = None
                try:
                    from modules.demo_sim_ledger_seed import maybe_seed_demo_sim_ledgers_on_startup

                    seeded_paths = maybe_seed_demo_sim_ledgers_on_startup()
                except Exception as seed_err:
                    logger.warning("DEMO_SIM_LEDGER_SEED skipped: %s", seed_err)
                mock_options_executor = MockOptionsExecutor(alert_manager=system.alert_manager)
                if seeded_paths:
                    try:
                        from modules.desk_snapshot_pulse import queue_desk_snapshot

                        queue_desk_snapshot(mock_options_executor)
                        logger.info(
                            "DEMO_SIM_LEDGER_SEED | desk snapshot queued | paths=%s",
                            seeded_paths,
                        )
                    except Exception as snap_err:
                        logger.warning("DEMO_SIM_LEDGER_SEED | desk snapshot skipped: %s", snap_err)
                logger.info(
                    "✅ Mock Options Executor initialized for Demo Mode "
                    "(simulated ledger, live broker market data)"
                )
                try:
                    from modules.exit_decision_dispatch import stealth_evaluate_api_enabled

                    _eval_api = bool(stealth_evaluate_api_enabled())
                except Exception:
                    _eval_api = True
                _synth = os.getenv("0DTE_DEMO_SYNTHETIC_CHAIN", "false").lower() == "true"
                logger.warning(
                    "DEMO_OWNERSHIP_PATH | mode=demo | premium_engine=true | "
                    "premium_provider=mock_executor.active_positions | "
                    "OPTION_STEALTH_USE_EVALUATE_API=%s | "
                    "0DTE_DEMO_SYNTHETIC_CHAIN=%s | "
                    "path=mock_fill→PE→apply→stealth.evaluate→mock_close",
                    str(_eval_api).lower(),
                    str(_synth).lower(),
                )
                logger.info(f"   - Starting balance: ${mock_options_executor.starting_balance:.2f}")
            
            # Initialize Options Trading Executor
            # Rev 00231: Using only 35% max position size (matches ORB Strategy) - max_position_cost disabled
            _0dte_exec_max_pct = float(
                os.getenv(
                    "0DTE_EXECUTOR_MAX_POSITION_SIZE_PCT",
                    os.getenv("0DTE_MAX_POSITION_SIZE_PCT", "35"),
                )
            ) / 100.0
            options_executor = OptionsTradingExecutor(
                auto_partial_enabled=os.getenv('0DTE_AUTO_PARTIAL_ENABLED', 'false').lower() == 'true',
                partial_profit_pct=float(os.getenv('0DTE_PARTIAL_PROFIT_PCT', '0.50')),
                runner_profit_pct=float(os.getenv('0DTE_RUNNER_PROFIT_PCT', '2.0')),
                max_position_cost=999999.0,  # Disabled - percentage cap only (`0DTE_EXECUTOR_MAX_POSITION_SIZE_PCT`)
                max_position_size_pct=_0dte_exec_max_pct,
                demo_mode=runtime_demo,
                mock_executor=mock_options_executor,
                alert_manager=system.alert_manager,
                priority_collector=priority_collector
            )
            
            # Rev 00232: Set ETrade Options API instance if available (for Live Mode)
            # This ensures OptionsTradingExecutor uses the same ETrade instance as OptionsChainManager
            # with proper account selection (if separate 0DTE account is configured)
            if etrade_options_api:
                options_executor.set_etrade_options_api(etrade_options_api)
                logger.info("✅ ETrade Options API instance set for Options Trading Executor")

            try:
                from modules.broker_adapter import create_broker_adapter
                from modules.broker_router import get_broker_router
                from modules.runtime_mode import broker_submits_enabled, is_live_mode
                from modules.unified_0dte_portfolio_risk_manager import (
                    get_unified_0dte_portfolio_risk_manager,
                )

                live_mode_flag = broker_submits_enabled()
                router = get_broker_router(
                    system,
                    live_mode=live_mode_flag,
                    etrade_options_api=etrade_options_api,
                )
                broker_adapter = router.primary_adapter()
                if broker_adapter is None:
                    broker_adapter = create_broker_adapter(
                        etrade_options_api=etrade_options_api,
                        live_mode=live_mode_flag,
                        options_chain_manager=options_chain_manager,
                    )
                elif hasattr(broker_adapter, "set_options_chain_manager"):
                    broker_adapter.set_options_chain_manager(options_chain_manager)
                options_executor.set_broker_adapter(broker_adapter)
                options_executor.set_trading_system(system)
                system._broker_adapter = broker_adapter
                system.options_chain_manager = options_chain_manager
                unified_risk_mgr = get_unified_0dte_portfolio_risk_manager(system)
                from modules.unified_0dte_startup import validate_unified_0dte_startup

                validate_unified_0dte_startup(
                    system=system,
                    options_executor=options_executor,
                    broker_router=router,
                    broker_adapter=broker_adapter,
                    demo_mode=runtime_demo,
                )
                logger.info(
                    "✅ Broker adapter wired | mode=%s | broker_submits=%s | available=%s | unified_risk=%s",
                    "live" if is_live_mode() else "demo",
                    broker_submits_enabled(),
                    broker_adapter.is_available(),
                    unified_risk_mgr.config.enabled,
                )
                from modules.runtime_mode import audit_production_startup

                audit_production_startup(
                    broker_adapter=broker_adapter,
                    demo_mode=runtime_demo,
                )
                try:
                    from modules.options_level_audit import audit_options_level

                    option_level_raw = None
                    if broker_adapter.is_available():
                        bal = await broker_adapter.get_account_balance()
                        if bal:
                            raw = getattr(bal, "raw", {}) or {}
                            option_level_raw = raw.get("optionLevel")
                    if option_level_raw is None and etrade_options_api and etrade_options_api.etrade:
                        bal_obj = etrade_options_api.etrade.get_account_balance()
                        if bal_obj:
                            option_level_raw = getattr(bal_obj, "option_level", None)
                    audit_options_level(
                        option_level_raw,
                        account_id=os.getenv("ETRADE_0DTE_ACCOUNT_ID"),
                    )
                except Exception as level_err:
                    logger.warning("OPTIONS_LEVEL_AUDIT skipped: %s", level_err)
            except Exception as broker_err:
                logger.warning("⚠️ Broker adapter init skipped: %s", broker_err)
            
            # Initialize Easy 0DTE Manager
            # Rev 00295: Use full 0dte_list.csv (target_symbols=None) — was hardcoded to [SPX,QQQ,SPY]
            # which caused 0 0DTE signals when Convex passed TSLA/META/KOLD etc (not in 3-symbol list)
            dte0_manager = Prime0DTEStrategyManager(
                convex_filter=convex_filter,
                target_symbols=None,  # Load from 0dte_list.csv (row count varies)
                max_positions=int(
                    os.getenv(
                        'ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX',
                        os.getenv(
                            'ORB_0DTE_PRIORITY_RANK_BATCH_MAX_POSITIONS',
                            os.getenv(
                                'ORB_0DTE_BATCH_MAX_POSITIONS',
                                os.getenv('0DTE_MAX_POSITIONS', '10'),
                            ),
                        ),
                    )
                ),  # Gate 3c + sizing divisor — configs/ORB0DTE.env
                enable_lotto_sleeve=os.getenv('0DTE_LOTTO_SLEEVE_ENABLED', 'false').lower() == 'true',
                priority_collector=priority_collector,
                alert_manager=system.alert_manager
            )
            
            # Store references for options execution
            dte0_manager.options_chain_manager = options_chain_manager
            dte0_manager.options_executor = options_executor
            
            # Store reference in system for signal hooking
            system.dte0_manager = dte0_manager
            system.options_chain_manager = options_chain_manager

            logger.info("✅ Easy 0DTE initialized successfully")
            logger.info(f"   🔗 0DTE Manager assigned to system: {system.dte0_manager is not None}")
            logger.info(f"   - Mode: {'🎮 DEMO' if runtime_demo else '💰 LIVE'}")
            logger.info(f"   - Target symbols: {len(dte0_manager.target_symbols)} from 0dte_list.csv")
            logger.info(f"   - Max positions: {dte0_manager.max_positions}")
            logger.info(f"   - Lotto sleeve: {'Enabled' if dte0_manager.enable_lotto_sleeve else 'Disabled'}")
            logger.info(
                "   - Convex Filter: pc_oi (CALL pc_oi<1, PUT pc_oi>1, ORB range ≥%.2f%%)",
                convex_filter.orb_range_min_pct,
            )
        except ImportError as e:
            logger.error(f"❌ Failed to import 0DTE modules: {e}", exc_info=True)
            logger.warning(
                "LIVE_DATA_DEFERRED | engine=0dte | reason=import_error | retry_on=oauth_token_renewed"
            )
            dte0_manager = None
            system.dte0_manager = None
        except Exception as e:
            logger.error(f"❌ Failed to initialize Easy 0DTE: {e}", exc_info=True)
            logger.warning(
                "LIVE_DATA_DEFERRED | engine=0dte | reason=%s | retry_on=oauth_token_renewed",
                e,
            )
            dte0_manager = None
            system.dte0_manager = None
    else:
        logger.info("ℹ️  Easy 0DTE disabled (ENABLE_0DTE_STRATEGY=false)")

    # Live soak: wire broker + options API for /api/ready even when execution paths are off.
    from modules.runtime_mode import is_live_mode

    if is_live_mode() and not getattr(system, "_broker_adapter", None):
        try:
            from modules.live_broker_stack import wire_live_broker_stack

            soak_api, soak_adapter = await wire_live_broker_stack(
                system,
                etrade_trader=etrade_trader,
            )
            if soak_api and not etrade_options_api:
                etrade_options_api = soak_api
            logger.info(
                "LIVE_BROKER_SOAK | post-init | adapter_available=%s",
                bool(soak_adapter and soak_adapter.is_available()),
            )
        except Exception as soak_err:
            logger.warning("LIVE_BROKER_SOAK | wire failed: %s", soak_err)
    
    # Initialize Easy Trendline Strategy (third sibling path, additive)
    enable_trendline_strategy = os.getenv('ENABLE_TRENDLINE_STRATEGY', 'false').lower() == 'true'
    logger.info(f"🔧 ENABLE_TRENDLINE_STRATEGY parsed value: {enable_trendline_strategy}")
    if enable_trendline_strategy:
        try:
            from modules.runtime_mode import is_demo_mode as runtime_is_demo_mode

            runtime_demo = runtime_is_demo_mode()
            import sys as _tl_sys

            _app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            for _tl_base in (
                os.path.join(_app_root, "easy0DTE"),
                "/app/easy0DTE",
            ):
                if os.path.isdir(_tl_base):
                    if _tl_base not in _tl_sys.path:
                        _tl_sys.path.insert(0, _tl_base)
                    _tl_mod = os.path.join(_tl_base, "modules")
                    if os.path.isdir(_tl_mod) and _tl_mod not in _tl_sys.path:
                        _tl_sys.path.insert(0, _tl_mod)
                    break

            from easyTrendline import (
                TrendlineFeatureLogger,
                TrendlineOptionsExecutor,
                TrendlineSignalEngine,
            )
            from easyTrendline.trendline_config_loader import (
                load_trendline_config_from_env,
                load_trendline_option_selection_config,
                warn_unused_trendline_related_env_keys,
            )
            from modules.config_loader import get_config_value
            from modules.prime_options_stealth_trailing_tp import (
                TrendlineOptionsStealthEngine,
                load_option_stealth_config,
            )

            trendline_config = load_trendline_config_from_env(get_config_value)
            option_sel_cfg = load_trendline_option_selection_config(get_config_value)
            warn_unused_trendline_related_env_keys()
            _unified_demo_bal = float(
                __import__("modules.unified_0dte_account", fromlist=["unified_demo_starting_balance"]).unified_demo_starting_balance()
            )
            trendline_options_executor = TrendlineOptionsExecutor(
                demo_mode=runtime_demo,
                option_config=option_sel_cfg,
                option_quote_api=etrade_options_api,
                require_live_chain_data=require_live_option_data,
                trendline_signal_config=trendline_config,
                options_trading_executor=options_executor,
                options_chain_manager=options_chain_manager,
            )
            trendline_signal_engine = TrendlineSignalEngine(config=trendline_config)
            trendline_feature_logger = TrendlineFeatureLogger()
            trendline_options_stealth = TrendlineOptionsStealthEngine(
                load_option_stealth_config(get_config_value)
            )

            tl_quote_api = etrade_options_api
            if tl_quote_api is None and not runtime_demo:
                try:
                    from easy0DTE.modules.etrade_options_api import ETradeOptionsAPI as _TL_ETradeOptionsAPI

                    _tl_dte_account = (
                        os.getenv("ETRADE_0DTE_ACCOUNT_ID")
                        or os.getenv("0DTE_ETRADE_ACCOUNT_ID")
                        or None
                    )
                    tl_quote_api = _TL_ETradeOptionsAPI(
                        etrade_trading=etrade_trader,
                        environment="prod",
                        account_id=_tl_dte_account,
                    )
                    logger.info(
                        "TRENDLINE_PIPELINE | option_quote_api=initialized_for_trendline_live | account_id=%s",
                        _tl_dte_account or "default",
                    )
                except Exception as tl_q_err:
                    logger.warning(
                        "TRENDLINE_PIPELINE | option_quote_api=unavailable | reason=%s",
                        tl_q_err,
                    )
                    tl_quote_api = None
            system._trendline_options_quote_api = tl_quote_api
            if getattr(system, "trendline_options_executor", None):
                system.trendline_options_executor.option_quote_api = tl_quote_api

            system.trendline_options_executor = trendline_options_executor
            system.trendline_signal_engine = trendline_signal_engine
            system._trendline_feature_logger = trendline_feature_logger
            try:
                from modules.trendline_break_snapshot import get_trendline_break_snapshot_collector

                system._trendline_break_snapshot_collector = get_trendline_break_snapshot_collector()
                trendline_signal_engine.on_first_break_snapshot = (
                    system._capture_trendline_first_break_snapshot
                )
            except Exception as tl_snap_err:
                logger.warning(
                    "TRENDLINE_BREAK_SNAPSHOT | stage=init | status=skipped | reason=%s",
                    tl_snap_err,
                )
            system.trendline_options_stealth = trendline_options_stealth

            if not runtime_demo:
                try:
                    sync_summary = await trendline_options_executor.sync_live_positions_with_broker()
                    logger.info("TRENDLINE_BROKER_SYNC | %s", sync_summary)
                except Exception as tl_sync_err:
                    logger.warning("TRENDLINE_BROKER_SYNC | failed | %s", tl_sync_err)

            logger.info("TRENDLINE_PIPELINE | stage=init | status=ready")
            logger.info(
                f"   Trendline mode: {'DEMO' if runtime_demo else 'LIVE_READY'} | "
                f"unified_starting_balance=${_unified_demo_bal:.2f}"
            )
        except Exception as trendline_init_error:
            logger.error(
                f"TRENDLINE_PIPELINE | stage=init | status=failed | reason={trendline_init_error}",
                exc_info=True
            )
            # Avoid a half-initialized Trendline book (mirrors 0DTE dte0_manager=None on failure).
            system.trendline_options_executor = None
            system.trendline_signal_engine = None
            system._trendline_feature_logger = None
            system._trendline_break_snapshot_collector = None
            system.trendline_options_stealth = None
            system._trendline_options_quote_api = None
    else:
        logger.info("ℹ️ Easy Trendline Strategy disabled (ENABLE_TRENDLINE_STRATEGY=false)")

    # Initialize Easy Breakout Strategy (fourth sibling — in-range @ 7:30 → later ORB break)
    enable_breakout_strategy = os.getenv("ENABLE_BREAKOUT_STRATEGY", "false").lower() == "true"
    logger.info(f"🔧 ENABLE_BREAKOUT_STRATEGY parsed value: {enable_breakout_strategy}")
    if enable_breakout_strategy:
        try:
            from modules.runtime_mode import is_demo_mode as runtime_is_demo_mode_bo

            runtime_demo_bo = runtime_is_demo_mode_bo()
            from easyBreakout import BreakoutOptionsExecutor
            from easyBreakout.breakout_config_loader import load_breakout_config_from_env
            from modules.config_loader import get_config_value as _bo_get_cfg
            from easyTrendline.trendline_config_loader import load_trendline_option_selection_config

            system._breakout_config = load_breakout_config_from_env(_bo_get_cfg)
            option_sel_cfg_bo = load_trendline_option_selection_config(_bo_get_cfg)
            # Reuse shared options executor / chain manager from 0DTE init when present.
            _bo_options_executor = options_executor
            _bo_chain_mgr = locals().get("options_chain_manager")
            _bo_quote_api = etrade_options_api
            _bo_require_live = bool(
                locals().get("require_live_option_data")
                if "require_live_option_data" in locals()
                else (os.getenv("REQUIRE_LIVE_OPTION_DATA", "true").lower() == "true")
            )
            if _bo_options_executor is None and getattr(system, "dte0_manager", None):
                _bo_options_executor = getattr(
                    system.dte0_manager, "options_executor", None
                )
                _bo_chain_mgr = getattr(
                    system.dte0_manager, "options_chain_manager", None
                ) or _bo_chain_mgr
            if getattr(system, "trendline_options_executor", None) and _bo_options_executor is None:
                _tl_ex = system.trendline_options_executor
                _bo_options_executor = getattr(_tl_ex, "options_trading_executor", None)
                _bo_chain_mgr = getattr(_tl_ex, "options_chain_manager", None) or _bo_chain_mgr
                _bo_quote_api = getattr(_tl_ex, "option_quote_api", None) or _bo_quote_api
            system.breakout_options_executor = BreakoutOptionsExecutor(
                demo_mode=runtime_demo_bo,
                option_config=option_sel_cfg_bo,
                option_quote_api=_bo_quote_api,
                require_live_chain_data=_bo_require_live,
                trendline_signal_config=None,
                options_trading_executor=_bo_options_executor,
                options_chain_manager=_bo_chain_mgr,
            )
            system._enable_breakout_strategy = True
            logger.info(
                "BREAKOUT_PIPELINE | stage=init | status=ready | mode=%s",
                "DEMO" if runtime_demo_bo else "LIVE_READY",
            )
        except Exception as breakout_init_error:
            logger.error(
                "BREAKOUT_PIPELINE | stage=init | status=failed | reason=%s",
                breakout_init_error,
                exc_info=True,
            )
            system.breakout_options_executor = None
            system._enable_breakout_strategy = False
    else:
        logger.info("ℹ️ Easy Breakout Strategy disabled (ENABLE_BREAKOUT_STRATEGY=false)")
        system._enable_breakout_strategy = False

    missing = engines_missing(system)
    if missing:
        system._live_data_deferred = True
        system._live_data_deferred_reason = "engines_not_attached"
        logger.warning(
            "LIVE_DATA_DEFERRED | reason=engines_not_attached | missing=%s | "
            "retry_on=oauth_token_renewed",
            ",".join(missing),
        )
    else:
        system._live_data_deferred = False
        system._live_data_deferred_reason = ""
        logger.info(
            "LIVE_DATA_ACCESS | ready=true | engines=%s",
            ",".join(required) or "none",
        )
