#!/usr/bin/env python3
"""
Improved Main Entry Point
High-performance entry point using the integrated trading system
Consolidates all functionality and eliminates redundancy
"""

from __future__ import annotations
import os
import shutil
import sys
import logging
import argparse
import asyncio
import signal
from typing import Optional
from datetime import datetime, timedelta

# --- Prime System Imports ---
from modules.prime_trading_system import (
    get_prime_trading_system, PrimeTradingSystem, TradingConfig, SystemMode
)
from modules.prime_market_manager import (
    get_prime_market_manager, PrimeMarketManager, MarketSession
)
# ARCHIVED (Rev 00173): Production signal generator no longer used - ORB manager generates signals directly
# DELETED (Oct 20, 2025): Removed production_signal_generator import - ORB strategy handles signals directly
from modules.etrade_oauth_integration import get_etrade_oauth_integration
from modules.prime_etrade_trading import PrimeETradeTrading
from modules.prime_models import StrategyMode
from modules.config_loader import load_configuration, get_config_value
from modules.prime_alert_manager import (
    ALERT_LABEL_EASY_ORB_ETF,
    ALERT_LABEL_EASY_ORB_0DTE_OPTIONS,
)

# OAuth keep-alive handled by Cloud Scheduler (no local keep-alive needed)

# --- Google Cloud specific imports ---
try:
    from google.cloud import logging as gcp_logging
    GCP_LOGGING_AVAILABLE = True
except ImportError:
    GCP_LOGGING_AVAILABLE = False

# --- Load configuration based on command line args or environment ---
def load_app_config():
    parser = argparse.ArgumentParser(description='ETrade Strategy Trading Bot - Improved')
    parser.add_argument('--strategy-mode', default=os.getenv('STRATEGY_MODE', 'standard'),
                       choices=['standard', 'advanced', 'quantum'],
                       help='Trading strategy mode')
    parser.add_argument('--system-mode', default=os.getenv('SYSTEM_MODE', 'full_trading'),
                       choices=['signal_only', 'scanner_only', 'full_trading', 'alert_only'],
                       help='System operation mode')
    parser.add_argument('--environment', default=os.getenv('ENVIRONMENT', 'development'),
                       choices=['development', 'production', 'sandbox'],
                       help='Deployment environment')
    _canonical_mode_default = (os.getenv('MODE') or 'demo').strip().lower()
    parser.add_argument(
        '--mode',
        '--etrade-mode',
        dest='mode',
        default=_canonical_mode_default,
        choices=['demo', 'live'],
        help='Trading mode: demo (sim ledger + live broker data) or live (real broker account)',
    )
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT', 8080)),
                       help='Port for HTTP server (cloud mode)')
    parser.add_argument('--host', default=os.getenv('HOST', '0.0.0.0'),
                       help='Host for HTTP server (cloud mode)')
    parser.add_argument('--cloud-mode', action='store_true',
                       help='Enable cloud deployment mode with HTTP server')
    parser.add_argument('--enable-premarket', action='store_true',
                       help='Enable pre-market news analysis')
    parser.add_argument('--enable-confluence', action='store_true',
                       help='Enable confluence trading system')
    parser.add_argument('--enable-multi-strategy', action='store_true',
                       help='Enable multi-strategy engine')
    parser.add_argument('--enable-news-sentiment', action='store_true',
                       help='Enable news sentiment analysis')
    parser.add_argument('--enable-enhanced-signals', action='store_true',
                       help='Enable enhanced signal generation')
    parser.add_argument('--enable-production-signals', action='store_true',
                       help='Enable Production Signal Generator (THE ONE AND ONLY)')
    parser.add_argument('--enable-signal-optimization', action='store_true',
                       help='Enable signal optimization and quality monitoring')
    parser.add_argument('--log-level', default=os.getenv('LOG_LEVEL', 'INFO'),
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                       help='Set logging level')
    parser.add_argument('--max-positions', type=int, default=int(os.getenv('MAX_POSITIONS', '10')),
                       help='Maximum number of positions')
    parser.add_argument('--scan-frequency', type=int, default=int(os.getenv('SCAN_FREQUENCY', '30')),
                       help='Scan frequency in seconds')
    parser.add_argument('--live-readiness-probe', action='store_true',
                       help='Run no-trade Live readiness checks and exit')
    parser.add_argument('--broker-certification-probe', action='store_true',
                       help='Run read-only E*TRADE broker certification audit and exit')
    
    args = parser.parse_args()

    _cloud_overrides = {
        k: os.environ[k]
        for k in (
            "MODE",
            "TRADING_MODE",
            "DEPLOYMENT_MODE",
            "AUTOMATION_MODE",
            "BROKER",
            "LIVE_SPREADS_ENABLED",
            "ENABLE_ORB_STRATEGY",
            "ENABLE_0DTE_STRATEGY",
            "ENABLE_TRENDLINE_STRATEGY",
            "ENABLE_BREAKOUT_STRATEGY",
            "ENABLE_PROFIT_OPTIMIZER",
        )
        if os.getenv(k) not in (None, "")
    }

    canonical_mode = (
        _cloud_overrides.get("MODE")
        or args.mode
    ).strip().lower()
    args.mode = canonical_mode

    # Load unified configuration
    automation_mode = 'live' if canonical_mode == 'live' else 'demo'
    config = load_configuration(args.strategy_mode, automation_mode, args.environment)
    
    for key, value in config.items():
        if key in _cloud_overrides:
            continue
        os.environ[key] = str(value)
    for key, value in _cloud_overrides.items():
        os.environ[key] = str(value)

    os.environ["MODE"] = canonical_mode
    try:
        from modules.runtime_mode import sync_legacy_mode_env, RuntimeMode

        sync_legacy_mode_env(RuntimeMode.LIVE if canonical_mode == "live" else RuntimeMode.DEMO)
    except Exception:
        os.environ["TRADING_MODE"] = canonical_mode
        os.environ["DEPLOYMENT_MODE"] = canonical_mode
    
    return config, args

# --- Initialize configuration ---
try:
    CONFIG, ARGS = load_app_config()
    try:
        from modules.runtime_mode import audit_startup_mode

        audit_startup_mode(strict=True)
    except ValueError as mode_err:
        print(f"Mode configuration error: {mode_err}")
        sys.exit(1)
except Exception as e:
    print(f"Failed to load configuration: {e}")
    sys.exit(1)

# --- Logging Configuration ---
def setup_logging():
    """Setup optimized logging for all environments"""
    # Get log level from args or config
    log_level = ARGS.log_level.upper() if hasattr(ARGS, 'log_level') else get_config_value("SESSION_LOG_LEVEL", "INFO").upper()
    
    # Create logger
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Create formatter
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ"
    )
    
    # Console handler (required for all environments)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)
    
    # Optional file handler for local development
    if ARGS.environment == 'development' and get_config_value("FILE_LOGGING", True):
        log_path = get_config_value("LOG_PATH", "logs/signals.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    
    return logger

# --- Google Cloud Logging Setup ---
def setup_cloud_logging():
    """Setup Google Cloud Logging if available"""
    if GCP_LOGGING_AVAILABLE and ARGS.environment == 'production':
        try:
            client = gcp_logging.Client()
            client.setup_logging()
            # Keep a stdout StreamHandler. Aug 5 desk: validation-candle-700/715 returned
            # HTTP 200 and wrote GCS, but PIPELINE/STEP text never appeared in Cloud Logging
            # when only the GCP API handler remained (stdout handlers had been removed).
            # Cloud Run always captures stdout — prefer possible duplicate lines over silent loss.
            root = logging.getLogger()
            has_stdout = any(
                isinstance(h, logging.StreamHandler)
                and getattr(h, "stream", None) is sys.stdout
                for h in root.handlers
            )
            if not has_stdout:
                sh = logging.StreamHandler(sys.stdout)
                sh.setFormatter(
                    logging.Formatter(
                        "%(asctime)s %(levelname)s %(name)s %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%SZ",
                    )
                )
                root.addHandler(sh)
            print("Google Cloud Logging initialized")
            return True
        except Exception as e:
            print(f"Failed to initialize GCP logging: {e}")
            return False
    return False

# --- Global System Instance ---
_system_instance = None

def _to_strategy_mode(mode_str: str) -> StrategyMode:
    """Safely convert string to StrategyMode enum with a sensible default."""
    mapping = {
        "standard": StrategyMode.STANDARD,
        "advanced": StrategyMode.ADVANCED,
        "quantum": StrategyMode.QUANTUM,
    }
    return mapping.get(str(mode_str).lower(), StrategyMode.STANDARD)

def get_integrated_system():
    """Get or create the integrated system instance"""
    global _system_instance
    if _system_instance is None:
        from modules.runtime_mode import resolve_runtime_mode, RuntimeMode

        runtime = resolve_runtime_mode(force_refresh=True)
        resolved_mode = (
            SystemMode.LIVE_MODE
            if runtime is RuntimeMode.LIVE
            else SystemMode.DEMO_MODE
        )

        # Create system configuration
        system_config = TradingConfig(
            mode=resolved_mode,
            strategy_mode=_to_strategy_mode(ARGS.strategy_mode),
            enable_premarket_analysis=ARGS.enable_premarket,
            enable_confluence_trading=ARGS.enable_confluence,
            enable_multi_strategy=ARGS.enable_multi_strategy,
            enable_news_sentiment=ARGS.enable_news_sentiment,
            enable_enhanced_signals=ARGS.enable_enhanced_signals,
            max_positions=ARGS.max_positions,
            scan_frequency=ARGS.scan_frequency
        )
        _system_instance = get_prime_trading_system(system_config)
    return _system_instance

# --- Health Check Endpoint ---
async def health_check():
    """Comprehensive health check endpoint using integrated system"""
    try:
        # Cloud Run: return 200 immediately if system not yet built (allows fast container startup)
        if _system_instance is None:
            return {
                "status": "healthy",
                "message": "starting",
                "timestamp": datetime.utcnow().isoformat(),
                "environment": ARGS.environment,
            }
        # Get integrated system instance
        system = get_integrated_system()
        
        # Get system metrics
        metrics = system.get_metrics()
        
        # Determine health status
        health_status = "healthy"
        if metrics["system_metrics"]["errors"] > 10:
            health_status = "degraded"
        if metrics["system_metrics"]["errors"] > 50:
            health_status = "unhealthy"
        
        # Check if deployment test file exists
        import os
        test_file_exists = os.path.exists("DEPLOYMENT_TEST_00078.txt")
        test_file_content = ""
        if test_file_exists:
            try:
                with open("DEPLOYMENT_TEST_00078.txt", "r") as f:
                    test_file_content = f.read().strip()
            except:
                test_file_content = "Error reading file"
        
        return {
            "status": health_status,
            "timestamp": datetime.utcnow().isoformat(),
            "environment": ARGS.environment,
            "strategy_mode": ARGS.strategy_mode,
            "system_mode": ARGS.system_mode,
            "uptime_hours": metrics["system_metrics"]["uptime_hours"],
            "current_phase": metrics["current_phase"],
            "running": metrics["running"],
            "system_metrics": metrics["system_metrics"],
            "trading_metrics": metrics["trading_metrics"],
            "scanner_metrics": metrics["scanner_metrics"],
            "deployment_test": {
                "file_exists": test_file_exists,
                "content": test_file_content
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }

# --- HTTP Server for Cloud Mode ---
async def check_and_build_stale_watchlist():
    """Deprecated: Dynamic watchlist build removed. Using static 0dte_list.csv."""
    logger = logging.getLogger("improved_main")
    logger.info("🗒️ Watchlist builder disabled - using static 0dte_list.csv")
    return

async def start_http_server():
    """Start HTTP server for cloud deployment"""
    try:
        try:
            from aiohttp import web
        except ImportError:
            logger = logging.getLogger("improved_main")
            logger.error("aiohttp not available. Install with: pip install aiohttp")
            return None
        
        async def handle_health(request):
            # Keepalive + (legacy) HTTP liveness must not pull trading metrics.
            # If the asyncio loop is blocked this still cannot run — Cloud Run
            # liveness must be TCP :8080. This handler stays cheap when the loop is free
            # and never 503s on error-count "unhealthy" (that used to recycle the instance).
            return web.json_response(
                {"status": "healthy", "probe": "ok", "timestamp": datetime.utcnow().isoformat()},
                status=200,
            )

        async def handle_ready(request):
            try:
                from modules.runtime_mode import (
                    audit_production_startup,
                    gcs_namespace,
                    is_demo_mode,
                    mode_label,
                    resolve_broker_kind,
                )

                system = get_integrated_system() if _system_instance else None
                broker_adapter = getattr(system, "_broker_adapter", None) if system else None
                etrade_oauth = getattr(system, "etrade_oauth", None) if system else None
                etrade_options_api = None
                if system and getattr(system, "dte0_manager", None):
                    etrade_options_api = getattr(
                        getattr(system.dte0_manager, "options_chain_manager", None),
                        "etrade_options_api",
                        None,
                    )
                if is_demo_mode():
                    payload = {
                        "ready": True,
                        "mode": mode_label(),
                        "broker": resolve_broker_kind(),
                        "gcs_namespace": gcs_namespace(),
                        "probe": "demo_ready",
                    }
                    return web.json_response(payload, status=200)
                from modules.live_preflight import run_live_preflight

                etrade_trader = getattr(system, "etrade_trader", None) if system else None
                probe = await run_live_preflight(
                    broker_adapter=broker_adapter,
                    etrade_oauth=etrade_oauth,
                    etrade_options_api=etrade_options_api,
                    etrade_trader=etrade_trader,
                )
                status_code = 200 if probe.get("ready") else 503
                return web.json_response(probe, status=status_code)
            except Exception as exc:
                return web.json_response({"ready": False, "error": str(exc)}, status=503)

        async def handle_broker_certification(request):
            try:
                from modules.broker_certification_probe import run_broker_certification_probe

                system = get_integrated_system() if _system_instance else None
                broker_adapter = getattr(system, "_broker_adapter", None) if system else None
                etrade_oauth = getattr(system, "etrade_oauth", None) if system else None
                etrade_options_api = None
                if system and getattr(system, "dte0_manager", None):
                    etrade_options_api = getattr(
                        getattr(system.dte0_manager, "options_chain_manager", None),
                        "etrade_options_api",
                        None,
                    )
                result = await run_broker_certification_probe(
                    broker_adapter=broker_adapter,
                    etrade_oauth=etrade_oauth,
                    etrade_options_api=etrade_options_api,
                    market_manager=get_prime_market_manager(),
                )
                status_code = 200 if result.get("certified") else 503
                return web.json_response(result, status=status_code)
            except Exception as exc:
                return web.json_response(
                    {
                        "certified": False,
                        "status": "broker_certification_failed",
                        "error": str(exc),
                    },
                    status=503,
                )
        
        async def handle_metrics(request):
            system = get_integrated_system()
            metrics = system.get_metrics()
            return web.json_response(metrics)
        
        async def handle_status(request):
            health_data = await health_check()
            return web.json_response(health_data)
        
        async def handle_control(request):
            """Control endpoint for system management"""
            data = await request.json()
            action = data.get('action')
            
            if action == 'shutdown':
                system = get_integrated_system()
                await system.shutdown()
                return web.json_response({"status": "shutdown_initiated"})
            elif action == 'restart':
                # Restart would be handled by the container orchestrator
                return web.json_response({"status": "restart_initiated"})
            elif action == 'force_close_symbol':
                system = get_integrated_system()
                symbol = str(data.get('symbol', '') or '').strip().upper()
                if not symbol:
                    return web.json_response({"error": "symbol_required"}, status=400)
                if not system or not hasattr(system, "stealth_trailing") or not system.stealth_trailing:
                    return web.json_response({"error": "stealth_trailing_unavailable"}, status=500)

                position_state = system.stealth_trailing.active_positions.get(symbol)
                if not position_state:
                    return web.json_response({
                        "error": "position_not_found",
                        "symbol": symbol,
                        "active_symbols": sorted(list(system.stealth_trailing.active_positions.keys()))
                    }, status=404)

                # Reuse existing close + remove flow so exit telemetry/alerts stay consistent.
                from modules.prime_stealth_trailing_tp import ExitReason
                close_reason = str(data.get('reason', 'manual_force_exit') or 'manual_force_exit')
                if getattr(system.stealth_trailing, "exec", None):
                    await system.stealth_trailing.exec.close_position(position_state, close_reason)
                await system.stealth_trailing._remove_position(symbol, ExitReason.TIME_EXIT, send_alert=True)

                return web.json_response({
                    "status": "force_closed",
                    "symbol": symbol,
                    "reason": close_reason,
                    "pnl": float(getattr(position_state, "unrealized_pnl", 0.0) or 0.0),
                    "pnl_pct": float(getattr(position_state, "unrealized_pnl_pct", 0.0) or 0.0),
                })
            else:
                return web.json_response({"error": "unknown_action"}, status=400)
        

        async def handle_build_watchlist(request):
            """Deprecated endpoint; static 0dte_list.csv is used. Returns success no-op."""
            logger = logging.getLogger("improved_main")
            logger.info("📋 /api/build-watchlist called - dynamic builder disabled; using 0dte_list.csv")
            return web.json_response({
                "status": "success",
                "message": "Dynamic watchlist disabled. Using static 0dte_list.csv",
                "timestamp": datetime.utcnow().isoformat(),
                "force_rebuild": False,
                "symbol_count": None
            })
        
        async def handle_watchlist_status(request):
            """Return static watchlist status indicating 0dte_list.csv usage."""
            logger = logging.getLogger("improved_main")
            file_path = "data/watchlist/0dte_list.csv"
            info = {
                "using_static_0dte_list": True,
                "file_exists": os.path.exists(file_path),
                "timestamp": datetime.utcnow().isoformat()
            }
            if info["file_exists"]:
                try:
                    mod_time = os.path.getmtime(file_path)
                    info["last_modified"] = datetime.fromtimestamp(mod_time).isoformat()
                    try:
                        import pandas as pd
                        df = pd.read_csv(file_path)
                        info["symbol_count"] = len(df)
                    except Exception:
                        info["symbol_count"] = None
                except Exception:
                    pass
            return web.json_response({"status": "success", "watchlist": info})
        
        async def handle_oauth_token_renewed(request):
            """Handle OAuth token renewal webhook — reload in-memory creds; alerts owned by OAuth service."""
            try:
                import asyncio

                logger = logging.getLogger("improved_main")
                data = await request.json()
                environment = data.get("environment", "prod")
                oauth_verified = bool(data.get("oauth_verified", False))
                
                logger.info(
                    "🔄 OAuth token renewal webhook env=%s oauth_verified=%s",
                    environment,
                    oauth_verified,
                )
                
                system = get_integrated_system()
                token_reloaded = False
                token_api_valid = False
                engines_restored = False
                from modules.live_data_access import restore_live_data_access

                if system:
                    for attempt in range(5):
                        restored = await restore_live_data_access(system)
                        token_reloaded = bool(restored.get("reloaded"))
                        token_api_valid = bool(restored.get("live_api_ok"))
                        engines_restored = bool(restored.get("engines_restored")) or not restored.get(
                            "engines_missing_after"
                        )
                        if token_api_valid and not restored.get("engines_missing_after"):
                            break
                        if attempt < 4:
                            await asyncio.sleep(1.5)
                logger.info(
                    "OAuth token reload: reloaded=%s api_valid=%s engines_restored=%s oauth_verified=%s",
                    token_reloaded,
                    token_api_valid,
                    engines_restored,
                    oauth_verified,
                )

                # Do NOT prune Secret Manager here — OAuth store_tokens already pruned;
                # re-prune during SM propagation lag caused false failures and policy errors.

                if not token_api_valid and not oauth_verified:
                    err_msg = (
                        "Token reloaded from Secret Manager but E*TRADE rejected live quote (SPY). "
                        "Renew again at https://easy-trading-oauth-v2.web.app"
                    )
                    if system.alert_manager:
                        await system.alert_manager.send_oauth_renewal_error(environment, err_msg)
                    logger.error("OAuth renewal webhook: api_valid=False — error alert sent")
                    return web.json_response(
                        {"status": "error", "message": err_msg, "api_valid": False},
                        status=502,
                    )

                if not token_api_valid and oauth_verified:
                    logger.warning(
                        "Trading SPY probe failed after reload but OAuth live probe passed — "
                        "skipping error alert (credentials in SM are valid)"
                    )
                    return web.json_response({
                        "status": "success",
                        "message": "OAuth verified; trading in-memory reload pending",
                        "api_valid": False,
                        "oauth_verified": True,
                    })

                if token_api_valid and system.alert_manager and not oauth_verified:
                    await system.alert_manager.send_oauth_renewal_success(
                        environment, token_valid=True
                    )

                return web.json_response({
                    "status": "success",
                    "message": f"OAuth token reload complete for {environment}",
                    "api_valid": token_api_valid,
                    "oauth_verified": oauth_verified,
                })
                    
            except Exception as e:
                logger.error(f"❌ Error handling OAuth token renewal webhook: {e}")
                return web.json_response({
                    "status": "error", 
                    "message": f"Error handling OAuth token renewal webhook: {str(e)}"
                }, status=500)

        async def handle_oauth_test_alert(request):
            """Test OAuth alert functionality"""
            try:
                logger = logging.getLogger("improved_main")
                logger.info("🔄 Testing OAuth alert functionality")
                
                # Get the integrated trading system
                system = get_integrated_system()
                
                if system.alert_manager:
                    # Production tokens only (sandbox deprecated)
                    success_prod = await system.alert_manager.send_oauth_renewal_success("prod")
                    if success_prod:
                        logger.info("✅ OAuth test alert (production) sent successfully")
                        return web.json_response({
                            "status": "success",
                            "message": "OAuth test alert sent (production only)"
                        })
                    else:
                        logger.error("❌ OAuth test alert failed")
                        return web.json_response({
                            "status": "error",
                            "message": "OAuth test alert failed"
                        })
                else:
                    logger.error("❌ Alert manager not available")
                    return web.json_response({
                        "status": "error", 
                        "message": "Alert manager not available"
                    }, status=500)
                    
            except Exception as e:
                logger.error(f"❌ Error testing OAuth alerts: {e}")
                return web.json_response({
                    "status": "error", 
                    "message": f"Error testing OAuth alerts: {str(e)}"
                }, status=500)
        
        async def handle_market_open_alert(request):
            """
            Market open alert endpoint (Cloud Scheduler at 8:30 AM ET / 5:30 AM PT)
            Sends Good Morning alert with token status 1 hour before market open
            
            RESTORED (Oct 24, 2025): Cloud Scheduler job IS calling this endpoint
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("🌅 Market open alert triggered by Cloud Scheduler (8:30 AM ET)")
                
                # Get the system instance
                system = get_integrated_system()
                if not system or not system.alert_manager:
                    logger.warning("System or alert_manager not available for market open alert")
                    return web.json_response({
                        "status": "error",
                        "message": "System not available"
                    }, status=503)
                
                # Reload the live-data gate before Good Morning. A fresh token also
                # attaches engines that were skipped when this process booted unauthorized.
                token_api_ok = None
                from modules.live_data_access import restore_live_data_access

                restored = await restore_live_data_access(system)
                token_api_ok = bool(restored.get("live_api_ok"))
                logger.info(
                    "Good Morning token prep: api_valid=%s engines_restored=%s missing=%s",
                    token_api_ok,
                    restored.get("engines_restored"),
                    restored.get("engines_missing_after"),
                )
                
                # Send Good Morning alert via alert manager
                success = await system.alert_manager.send_oauth_morning_alert(
                    live_api_ok=token_api_ok,
                )
                
                if success:
                    logger.info("✅ Good Morning alert sent successfully")
                    return web.json_response({
                        "status": "success",
                        "message": "Good Morning alert sent"
                    })
                else:
                    logger.warning("⚠️ Good Morning alert failed to send")
                    return web.json_response({
                        "status": "warning",
                        "message": "Alert send failed (non-critical)"
                    }, status=200)  # 200 to prevent Cloud Scheduler retries
                    
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Error in market open alert endpoint: {e}")
                return web.json_response({
                    "status": "error",
                    "message": f"Market open alert error: {str(e)}"
                }, status=500)
        
        async def handle_validation_candle_700(request):
            """
            7:00 AM PT: Capture broker prices as open for 7:00-7:15 validation candle.
            Call via Cloud Scheduler at 7:00 AM PT so 7:15 prefetch can combine with 7:15 close for GREEN/RED.
            Broker-agnostic (E*TRADE today; same flow for Interactive Brokers etc.).
            """
            try:
                logger = logging.getLogger("improved_main")
                # Diagnostic: log immediately so cloud logs show 7:00 job ran even if capture fails (diagnose_zero_signals_cloud.py)
                logger.info("PIPELINE | STEP 2 VALIDATION OPEN (7:00) | endpoint hit | validation-candle-700")
                logger.info("📊 Validation candle 7:00 AM PT trigger (Cloud Scheduler)")
                system = get_integrated_system()
                if not system or not hasattr(system, 'capture_validation_open_700'):
                    logger.warning("System or capture_validation_open_700 not available")
                    return web.json_response({
                        "status": "error",
                        "message": "System not available"
                    }, status=503)
                count = await system.capture_validation_open_700()
                logger.info(f"VALIDATION_CANDLE | 7:00 OPEN | recorded={count} symbols | ready for 7:15 close")
                return web.json_response({
                    "status": "success",
                    "message": f"7:00 AM PT open prices stored for {count} symbols",
                    "symbols_captured": count
                })
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Error in validation-candle-700 endpoint: {e}")
                return web.json_response({
                    "status": "error",
                    "message": str(e)
                }, status=500)
        
        async def handle_prefetch_validation_715(request):
            """
            7:15 AM PT: Run validation candle prefetch (7:00 open + 7:15 close → GREEN/RED).
            Call via Cloud Scheduler at 7:15 AM PT so prefetch runs even if trading loop is not yet in window (scale-to-zero).
            Ensures validation candle is ready for signal collection at 7:30.
            """
            try:
                logger = logging.getLogger("improved_main")
                # Diagnostic: log immediately so cloud logs show 7:15 job ran even if prefetch fails (diagnose_zero_signals_cloud.py)
                logger.info("PIPELINE | STEP 3 VALIDATION CANDLE (7:00-7:15) | endpoint hit | prefetch-validation-715")
                logger.info("📊 Validation candle 7:15 AM PT prefetch trigger (Cloud Scheduler)")
                system = get_integrated_system()
                if not system or not hasattr(system, '_prefetch_previous_candle_data'):
                    logger.warning("System or _prefetch_previous_candle_data not available")
                    return web.json_response({"status": "error", "message": "System not available"}, status=503)
                await system._prefetch_previous_candle_data()
                system._prev_candle_prefetched_today = True
                logger.info("VALIDATION_CANDLE | 7:15 PREFETCH | endpoint completed | ready for signal collection")
                return web.json_response({
                    "status": "success",
                    "message": "7:15 AM PT validation candle prefetch completed (7:00 open + 7:15 close)"
                })
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Error in prefetch-validation-715 endpoint: {e}")
                return web.json_response({"status": "error", "message": str(e)}, status=500)
        
        async def handle_end_of_day_report(request):
            """
            End of day report endpoint (Cloud Scheduler at 3:55 PM ET / 12:55 PM PT)
            Sends EOD summary with daily P&L and performance metrics
            
            Rev 00260 (Jan 22, 2026): SINGLE SOURCE for all EOD reports
            - Easy ORB (ETF) EOD: only when ENABLE_ORB_STRATEGY=true; else skipped:so_etf_retired
            - Unified 0DTE EOD: one Telegram for ORB1 Breakout + Trendline + Breakout
            - Internal scheduler: Already DISABLED
            - GCS deduplication: Active as safety net
            
            RESTORED (Oct 24, 2025): Cloud Scheduler job IS calling this endpoint
            FIXED (Nov 2, 2025 - Rev 00093): Added weekday/holiday checking to prevent weekend alerts
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("📊 End of day report triggered by Cloud Scheduler (12:55 PM PT / 3:55 PM ET)")
                
                # Rev 00093: Check if it's a trading day BEFORE sending EOD report
                from modules.dynamic_holiday_calculator import should_skip_trading
                from modules.eod_report_helpers import pt_trading_date

                today = pt_trading_date()  # Rev 00362: PT session day (matches desk)
                is_weekend = today.weekday() >= 5  # Saturday=5, Sunday=6
                should_skip, skip_reason, holiday_name = should_skip_trading(today)
                
                if is_weekend:
                    logger.info(f"📅 Weekend ({today.strftime('%A')}) - Skipping EOD report")
                    return web.json_response({
                        "status": "skipped",
                        "message": f"Weekend ({today.strftime('%A')}) - No EOD report sent"
                    })
                elif should_skip:
                    logger.info(f"📅 Holiday ({holiday_name}) - Skipping EOD report")
                    return web.json_response({
                        "status": "skipped",
                        "message": f"Holiday ({holiday_name}) - No EOD report sent"
                    })
                
                # Get the system instance
                system = get_integrated_system()
                if not system or not system.alert_manager:
                    logger.warning("System or alert_manager not available for EOD report")
                    return web.json_response({
                        "status": "error",
                        "message": "System not available"
                    }, status=503)
                
                # Sole sender of 🏦 END-OF-DAY 0DTE: Cloud Scheduler `end-of-day-report`
                # → POST /api/end-of-day-report. Flatten leftovers first; GCS marker skips retries.
                try:
                    if hasattr(system, "flatten_all_paths_until_book_empty"):
                        leftover = await system.flatten_all_paths_until_book_empty()
                        if leftover:
                            logger.error(
                                "EOD_REPORT | flatten_incomplete | remaining_open=%s",
                                leftover,
                            )
                    elif hasattr(system, "flatten_all_paths_for_eod_scheduler"):
                        await system.flatten_all_paths_for_eod_scheduler()
                    else:
                        logger.warning("EOD_REPORT | flatten_all_paths_for_eod_scheduler missing on system")
                except Exception as flatten_all_err:
                    logger.error(
                        "EOD_REPORT | flatten_all_paths_failed | err=%s",
                        flatten_all_err,
                        exc_info=True,
                    )
                
                eod_results = {}

                # ORB SO ETF EOD only when ENABLE_ORB_STRATEGY=true; desk keeps SO paused →
                # skip 🛂 END-OF-DAY REPORT and send only unified 🏦 END-OF-DAY 0DTE.
                from modules.eod_report_helpers import orb_so_eod_enabled

                if orb_so_eod_enabled():
                    logger.info("EOD_REPORT | path=ORB_SO_DEMO | sending...")
                    try:
                        await system.alert_manager._send_demo_eod_summary()
                        eod_results["orb_so_demo"] = "sent"
                    except Exception as so_demo_err:
                        eod_results["orb_so_demo"] = f"error:{so_demo_err}"
                        logger.error(
                            "EOD_REPORT | path=ORB_SO_DEMO | failed | %s",
                            so_demo_err,
                            exc_info=True,
                        )

                    if hasattr(system, "unified_trade_manager") and system.unified_trade_manager:
                        logger.info("EOD_REPORT | path=ORB_SO_LIVE | sending...")
                        try:
                            await system.alert_manager._send_live_eod_summary()
                            eod_results["orb_so_live"] = "sent"
                        except Exception as so_live_err:
                            eod_results["orb_so_live"] = f"error:{so_live_err}"
                            logger.error(
                                "EOD_REPORT | path=ORB_SO_LIVE | failed | %s",
                                so_live_err,
                                exc_info=True,
                            )
                    else:
                        eod_results["orb_so_live"] = "skipped:no_unified_trade_manager"
                else:
                    eod_results["orb_so_demo"] = "skipped:so_etf_retired"
                    eod_results["orb_so_live"] = "skipped:so_etf_retired"
                    logger.info(
                        "EOD_REPORT | path=ORB_SO | skipped:so_etf_retired "
                        "(ENABLE_ORB_STRATEGY=false)"
                    )

                # Unified 0DTE (ORB + Trendline + Breakout on one ledger)
                logger.info("EOD_REPORT | path=UNIFIED_0DTE | sending...")
                try:
                    ok_0dte = await system.alert_manager.send_scheduled_unified_0dte_eod(system)
                    eod_results["unified_0dte"] = "sent" if ok_0dte else "telegram_failed"
                except Exception as orb_0dte_err:
                    eod_results["unified_0dte"] = f"error:{orb_0dte_err}"
                    logger.error("EOD_REPORT | path=UNIFIED_0DTE | failed | %s", orb_0dte_err, exc_info=True)

                logger.info("EOD_REPORT | complete | results=%s", eod_results)
                any_hard_error = any(
                    str(v).startswith("error:") for v in eod_results.values()
                )
                return web.json_response({
                    "status": "partial" if any_hard_error else "success",
                    "message": "EOD reports processed",
                    "results": eod_results,
                })
                    
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Error in EOD report endpoint: {e}")
                return web.json_response({
                    "status": "error",
                    "message": f"EOD report error: {str(e)}"
                }, status=500)
        
        async def handle_cleanup_historical_data(request):
            """
            Historical data cleanup endpoint (Cloud Scheduler at 4:05 PM ET)
            
            UPDATED (Oct 11, 2025 - Rev 00151):
            - No historical data to clean up (ORB strategy uses intraday bars only)
            - Endpoint kept for backward compatibility with Cloud Scheduler
            - Returns success immediately (no-op)
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("🗑️ Cleanup endpoint called (no-op for ORB strategy)")
                
                # ORB strategy doesn't use 100-day historical data
                # Only uses intraday 15-minute bars (fetched on-demand)
                # No cleanup needed - endpoint kept for Cloud Scheduler compatibility
                
                return web.json_response({
                    "status": "success",
                    "message": "No cleanup needed (ORB strategy uses intraday bars only)",
                    "removed_count": 0,
                    "timestamp": datetime.now().isoformat(),
                    "note": "Historical data caching removed in Rev 00151 - ORB strategy optimization"
                })
                    
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Cleanup endpoint error: {e}")
                return web.json_response({
                    "status": "error",
                    "message": f"Cleanup endpoint error: {str(e)}"
                }, status=500)
        
        async def handle_cleanup_images(request):
            """
            Container image and Cloud Run revision cleanup endpoint
            Rev 00259: Weekly cleanup of old images and revisions
            
            This endpoint runs the cleanup scripts to remove old container images
            and Cloud Run revisions to optimize storage costs.
            """
            try:
                import subprocess
                import json
                
                logger = logging.getLogger("improved_main")
                logger.info("🧹 Image cleanup endpoint called")
                
                # Get request data
                try:
                    data = await request.json()
                except:
                    data = {}
                
                cleanup_images = data.get('cleanup_images', True)
                cleanup_revisions = data.get('cleanup_revisions', True)
                cleanup_secrets = data.get('cleanup_secrets', True)
                
                results = {
                    "status": "success",
                    "timestamp": datetime.now().isoformat(),
                    "cleanup_images": cleanup_images,
                    "cleanup_revisions": cleanup_revisions,
                    "cleanup_secrets": cleanup_secrets,
                    "results": {}
                }
                
                # Get script directory (project root in container = /app)
                script_dir = os.path.dirname(os.path.abspath(__file__))
                # Fallback: Cloud Run uses /app; dirname can be / when __file__ is /main.py
                def _scripts_base():
                    candidates = [
                        script_dir,
                        "/app" if os.environ.get("PORT") else None,  # Cloud Run
                        os.getcwd(),
                    ]
                    for base in candidates:
                        if base and os.path.exists(os.path.join(base, "scripts", "cleanup_old_revisions.sh")):
                            return base
                    return script_dir  # use original even if missing
                script_dir = _scripts_base()
                
                # Run image cleanup if requested (requires gcloud - not in container)
                if cleanup_images:
                    gcloud_available = shutil.which("gcloud") is not None
                    if not gcloud_available:
                        logger.info("📦 Skipping image cleanup (gcloud not in container); revision cleanup will run")
                        results["results"]["images"] = {
                            "success": True,
                            "skipped": True,
                            "reason": "gcloud not in container; run ./scripts/cleanup_old_images.sh manually"
                        }
                    else:
                        logger.info("📦 Running image cleanup script...")
                        cleanup_images_script = os.path.join(script_dir, "scripts", "cleanup_old_images.sh")
                        if os.path.exists(cleanup_images_script):
                            try:
                                result = subprocess.run(
                                    ["bash", cleanup_images_script],
                                    capture_output=True,
                                    text=True,
                                    timeout=600,
                                    cwd=script_dir
                                )
                                results["results"]["images"] = {
                                    "success": result.returncode == 0,
                                    "stdout": result.stdout[-1000:] if result.stdout else "",
                                    "stderr": result.stderr[-1000:] if result.stderr else "",
                                    "returncode": result.returncode
                                }
                                logger.info(f"✅ Image cleanup completed (return code: {result.returncode})")
                            except subprocess.TimeoutExpired:
                                logger.error("❌ Image cleanup timed out after 10 minutes")
                                results["results"]["images"] = {"success": False, "error": "Timeout after 10 minutes"}
                            except Exception as e:
                                logger.error(f"❌ Image cleanup error: {e}")
                                results["results"]["images"] = {"success": False, "error": str(e)}
                        else:
                            results["results"]["images"] = {"success": False, "error": f"Script not found: {cleanup_images_script}"}
                
                # Run revision cleanup if requested
                # Rev 00294: Use Python Cloud Run API first (works in container without gcloud)
                if cleanup_revisions:
                    logger.info("📦 Running revision cleanup...")
                    try:
                        from modules.cloud_cleanup import cleanup_cloud_run_revisions
                        rev_results = cleanup_cloud_run_revisions()
                        deleted = rev_results.get("deleted", 0)
                        errors = rev_results.get("errors", [])
                        success = len(errors) == 0 or deleted > 0
                        results["results"]["revisions"] = {
                            "success": success,
                            "deleted": deleted,
                            "kept": rev_results.get("kept", 0),
                            "per_service": rev_results.get("per_service", {}),
                            "errors": errors[:5] if errors else []
                        }
                        if deleted > 0:
                            logger.info(f"✅ Revision cleanup completed: deleted {deleted}, kept {rev_results.get('kept', 0)}")
                        elif errors:
                            logger.warning(f"⚠️ Revision cleanup had errors: {errors[:3]}")
                    except ImportError as e:
                        # Fallback to script if cloud_cleanup not available
                        cleanup_revisions_script = os.path.join(script_dir, "scripts", "cleanup_old_revisions.sh")
                        if os.path.exists(cleanup_revisions_script):
                            try:
                                result = subprocess.run(
                                    ["bash", cleanup_revisions_script],
                                    capture_output=True,
                                    text=True,
                                    timeout=600,
                                    cwd=script_dir
                                )
                                results["results"]["revisions"] = {
                                    "success": result.returncode == 0,
                                    "stdout": result.stdout[-1000:] if result.stdout else "",
                                    "stderr": result.stderr[-1000:] if result.stderr else "",
                                    "returncode": result.returncode
                                }
                                logger.info(f"✅ Revision cleanup (script) completed (return code: {result.returncode})")
                            except Exception as script_err:
                                results["results"]["revisions"] = {"success": False, "error": str(script_err)}
                        else:
                            results["results"]["revisions"] = {
                                "success": False,
                                "error": f"cloud_cleanup import failed: {e}; script not found at {cleanup_revisions_script} (script_dir={script_dir})"
                            }
                    except Exception as e:
                        logger.error(f"❌ Revision cleanup error: {e}")
                        results["results"]["revisions"] = {"success": False, "error": str(e)}
                
                if cleanup_secrets:
                    try:
                        from modules.cloud_cleanup import cleanup_secret_manager_versions
                        from modules.secret_version_cleanup import audit_oauth_token_secrets
                        secret_result = cleanup_secret_manager_versions()
                        audit = audit_oauth_token_secrets()
                        destroyed = secret_result.get("destroyed", 0)
                        results["results"]["secrets"] = {
                            "success": audit.get("all_ok", False) or not secret_result.get("errors"),
                            "destroyed": destroyed,
                            "audit": audit,
                            "kept": secret_result.get("kept", 0),
                            "per_secret": secret_result.get("per_secret", {}),
                            "estimated_monthly_savings_usd": secret_result.get(
                                "estimated_monthly_savings_usd", 0
                            ),
                            "errors": secret_result.get("errors", [])[:5],
                        }
                        logger.info(
                            "✅ Secret cleanup: destroyed=%s est_savings=$%s/mo",
                            destroyed,
                            secret_result.get("estimated_monthly_savings_usd", 0),
                        )
                    except Exception as e:
                        logger.error(f"❌ Secret cleanup error: {e}")
                        results["results"]["secrets"] = {"success": False, "error": str(e)}
                
                # Determine overall success: revision cleanup is primary (works in-container)
                # Return 200 if revision cleanup succeeded; image cleanup may be skipped (needs gcloud)
                rev_ok = results["results"].get("revisions", {}).get("success", False)
                all_ok = all(r.get("success", False) for r in results["results"].values()) if results["results"] else False
                # Scheduler-friendly: 200 when revision cleanup succeeded (main cost optimization)
                http_ok = rev_ok if cleanup_revisions else (all_ok or not results["results"])
                if not all_ok:
                    results["status"] = "partial_success" if rev_ok else "error"
                return web.json_response(results, status=200 if http_ok else 500)
                    
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Image cleanup endpoint error: {e}", exc_info=True)
                return web.json_response({
                    "status": "error",
                    "message": f"Cleanup endpoint error: {str(e)}"
                }, status=500)
        
        async def handle_pending_signals(request):
            """
            Pending signals endpoint (Oct 27, 2025)
            Shows accumulated SO signals during collection window (7:15-7:44 AM PT)
            Useful for real-time monitoring and window optimization
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("📡 Pending signals request received")
                
                # Get the system instance
                system = get_integrated_system()
                if not system:
                    return web.json_response({
                        "status": "error",
                        "message": "System not initialized"
                    }, status=503)
                
                # Check if ORB strategy manager exists and has signals
                if not hasattr(system, 'orb_strategy_manager') or not system.orb_strategy_manager:
                    return web.json_response({
                        "status": "ok",
                        "message": "ORB strategy manager not available",
                        "pending_signals": [],
                        "count": 0
                    })
                
                # Get accumulated SO signals
                orb_manager = system.orb_strategy_manager
                accumulated_signals = []
                
                if hasattr(orb_manager, 'accumulated_so_signals'):
                    accumulated_signals = orb_manager.accumulated_so_signals or []
                
                # Format signal preview
                signal_preview = []
                for idx, sig in enumerate(accumulated_signals[:20], 1):  # Show top 20
                    signal_preview.append({
                        "rank": idx,
                        "symbol": sig.get('symbol', 'UNKNOWN'),
                        "price": round(sig.get('price', 0), 2),
                        "confidence": round(sig.get('confidence', 0), 3),
                        "priority_score": round(sig.get('priority_score', 0), 3),
                        "orb_range": round(sig.get('orb_range_pct', 0), 2),
                        "volume_ratio": round(sig.get('volume_ratio', 0), 2)
                    })
                
                return web.json_response({
                    "status": "ok",
                    "timestamp": datetime.now().isoformat(),
                    "collection_window": "7:15-7:44 AM PT (10:15-10:44 AM ET)",
                    "total_signals": len(accumulated_signals),
                    "signals_preview": signal_preview,
                    "max_trades": 15,
                    "will_execute": min(len(accumulated_signals), 15)
                })
                    
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Error in pending signals endpoint: {e}")
                return web.json_response({
                    "status": "error",
                    "message": f"Pending signals error: {str(e)}"
                }, status=500)
        
        async def handle_market_holiday_check(request):
            """
            Market holiday check endpoint (Cloud Scheduler at 5:30 AM PT / 8:30 AM ET)
            Sends alert if today is a holiday - 1 hour before market would open
            Rev 00163
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("🏖️ Market holiday check triggered by Cloud Scheduler")
                
                # Get the system instance
                system = get_integrated_system()
                
                if not system or not system.market_manager:
                    logger.error("❌ System or market manager not available")
                    return web.json_response({
                        "status": "error",
                        "message": "System not initialized"
                    }, status=500)
                
                # Check if today is a trading day
                # Use module-level datetime import (line 16)
                today = datetime.now().date()
                is_trading_day = system.market_manager.is_trading_day()
                
                if is_trading_day:
                    logger.info("✅ Today is a trading day - no holiday alert needed")
                    return web.json_response({
                        "status": "success",
                        "message": "Today is a trading day",
                        "is_holiday": False,
                        "timestamp": datetime.now().isoformat()
                    })
                
                # Today is NOT a trading day - check if it's a holiday (not just weekend)
                day_of_week = today.weekday()  # 0=Monday, 6=Sunday
                
                if day_of_week >= 5:  # Saturday or Sunday
                    logger.info("⏸️ Today is a weekend - no holiday alert needed")
                    return web.json_response({
                        "status": "success",
                        "message": "Today is a weekend",
                        "is_holiday": False,
                        "is_weekend": True,
                        "timestamp": datetime.now().isoformat()
                    })
                
                # It's a weekday but not a trading day = HOLIDAY
                # Rev 00087: Use unified holiday checker
                logger.info("🏖️ Today is a holiday - checking type")
                
                # Use unified holiday checker
                from modules.dynamic_holiday_calculator import should_skip_trading
                
                should_skip, skip_reason, holiday_name = should_skip_trading(today)
                
                if not should_skip:
                    # Shouldn't happen (market_manager said not trading day), but handle gracefully
                    holiday_name = "Market Holiday"
                    skip_reason = "MARKET_CLOSED"
                    logger.warning("Holiday detected by market_manager but not by holiday calculator")
                
                # Send holiday alert (unified method)
                # NOTE: This endpoint is called by Cloud Scheduler, but the morning alert (5:30 AM PT)
                # now also checks for holidays. This is a redundant check for backwards compatibility.
                if system.alert_manager:
                    success = await system.alert_manager.send_holiday_alert(
                        holiday_name=holiday_name,
                        skip_reason=skip_reason
                    )
                    
                    if success:
                        logger.info(f"✅ Market holiday alert sent - {holiday_name}")
                        return web.json_response({
                            "status": "success",
                            "message": f"Holiday alert sent - {holiday_name}",
                            "holiday_name": holiday_name,
                            "holiday_date": holiday_date,
                            "is_holiday": True,
                            "timestamp": datetime.now().isoformat()
                        })
                    else:
                        logger.error("❌ Failed to send holiday alert")
                        return web.json_response({
                            "status": "error",
                            "message": "Failed to send holiday alert"
                        }, status=500)
                else:
                    logger.error("❌ Alert manager not available")
                    return web.json_response({
                        "status": "error",
                        "message": "Alert manager not available"
                    }, status=500)
                
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"Error in market holiday check: {e}")
                return web.json_response({
                    "status": "error",
                    "message": str(e)
                }, status=500)
        
        async def handle_manual_orb_capture(request):
            """Manually trigger ORB capture (Rev 00173 - for testing/recovery)"""
            try:
                logger = logging.getLogger("improved_main")
                logger.info("📊 Manual ORB capture triggered")
                
                # Get the integrated trading system
                system = get_integrated_system()
                
                if not system:
                    return web.json_response({
                        "status": "error",
                        "message": "Trading system not initialized"
                    }, status=500)
                
                # Check if ORB capture is available
                if not hasattr(system, '_capture_orb_for_all_symbols'):
                    return web.json_response({
                        "status": "error",
                        "message": "ORB capture method not available"
                    }, status=500)
                
                # Check if ORB strategy manager is initialized
                if not hasattr(system, 'orb_strategy_manager') or not system.orb_strategy_manager:
                    return web.json_response({
                        "status": "error",
                        "message": "ORB Strategy Manager not initialized"
                    }, status=500)
                
                if hasattr(system, "data_manager") and system.data_manager:
                    if hasattr(system.data_manager, "reload_broker_tokens"):
                        system.data_manager.reload_broker_tokens()
                    if hasattr(system.data_manager, "ensure_valid_token_before_orb_capture"):
                        if not system.data_manager.ensure_valid_token_before_orb_capture():
                            return web.json_response({
                                "status": "error",
                                "message": "E*TRADE token invalid after Secret Manager reload — renew at oauth app and retry",
                            }, status=401)
                
                # Optional ?force=true bypasses 6:30-7:30 PT window (recovery after missed capture)
                force = False
                try:
                    force = (request.rel_url.query.get("force") or "").lower() in ("1", "true", "yes")
                except Exception:
                    pass
                system._orb_capture_force_session = force
                try:
                    logger.info(
                        f"🎯 Manually capturing ORB for {len(system.symbol_list)} symbols (force={force})..."
                    )
                    await system._capture_orb_for_all_symbols()
                finally:
                    system._orb_capture_force_session = False
                
                # Get capture results
                symbols_captured = len(system.orb_strategy_manager.orb_data)
                
                logger.info(f"✅ Manual ORB capture complete: {symbols_captured} symbols")
                
                return web.json_response({
                    "status": "success",
                    "message": f"ORB captured for {symbols_captured} symbols",
                    "symbols_captured": symbols_captured,
                    "symbol_list_size": len(system.symbol_list),
                    "timestamp": datetime.now().isoformat()
                })
                
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"Error in manual ORB capture: {e}")
                return web.json_response({
                    "status": "error",
                    "message": str(e)
                }, status=500)
        
        async def handle_positions(request):
            """
            Get current open positions with full details (Rev 00068)
            Returns position data from stealth trailing system (source of truth for monitoring)
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("📊 /api/positions called")
                
                # Get the integrated trading system
                system = get_integrated_system()
                
                if not system:
                    return web.json_response({
                        "error": "Trading system not initialized",
                        "total_positions": 0,
                        "positions": []
                    }, status=500)
                
                # Check if stealth trailing is available (source of truth for position monitoring)
                if not hasattr(system, 'stealth_trailing') or not system.stealth_trailing:
                    return web.json_response({
                        "error": "Stealth trailing system not available",
                        "total_positions": 0,
                        "positions": []
                    })
                
                # Get positions from stealth trailing
                positions_data = []
                for symbol, pos_state in system.stealth_trailing.active_positions.items():
                    time_held_sec = (datetime.now() - pos_state.entry_time).total_seconds()
                    time_held_min = time_held_sec / 60.0
                    
                    # Rev 00071: Fix field names to match PositionState dataclass
                    position_value = pos_state.quantity * pos_state.entry_price
                    max_favorable_pct = (pos_state.max_favorable / pos_state.entry_price) * 100 if pos_state.entry_price > 0 else 0.0
                    
                    positions_data.append({
                        'symbol': symbol,
                        'entry_price': pos_state.entry_price,
                        'current_price': pos_state.current_price,
                        'quantity': pos_state.quantity,
                        'position_value': position_value,  # Rev 00071: Calculate (was accessing non-existent field)
                        'unrealized_pnl': pos_state.unrealized_pnl,  # Rev 00071: Fixed field name (was unrealized_pl)
                        'unrealized_pnl_pct': pos_state.unrealized_pnl_pct * 100,  # Rev 00071: Convert to percentage (was unrealized_pl_pct)
                        'current_stop': pos_state.current_stop_loss,
                        'take_profit': pos_state.take_profit,
                        'time_held_min': round(time_held_min, 1),
                        'max_favorable': pos_state.max_favorable,
                        'max_favorable_pct': max_favorable_pct,  # Rev 00071: Calculate (was accessing non-existent field)
                        'entry_bar_protection': False,  # Rev 00071: Placeholder (field doesn't exist in PositionState)
                        'breakeven_activated': pos_state.breakeven_achieved,  # Rev 00071: Fixed field name (was breakeven_activated)
                        'trailing_activated': pos_state.trailing_activated
                    })
                
                logger.info(f"✅ Returning {len(positions_data)} positions from stealth trailing")
                
                return web.json_response({
                    'total_positions': len(positions_data),
                    'positions': positions_data,
                    'timestamp': datetime.now().isoformat(),
                    'mode': system.config.mode.value if hasattr(system.config, 'mode') else 'unknown'
                })
                
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"Error in /api/positions endpoint: {e}")
                return web.json_response({
                    'error': str(e),
                    'total_positions': 0,
                    'positions': []
                }, status=500)

        async def handle_options_positions(request):
            """
            Get current open options positions for ORB1 Breakout and Trendline 0DTE.
            Uses in-memory position + option_stealth metadata only (no extra market-data fan-out).
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("📊 /api/options-positions called")

                system = get_integrated_system()
                if not system:
                    empty_orb1 = {"total_positions": 0, "positions": [], "summary": {}}
                    return web.json_response({
                        "error": "Trading system not initialized",
                        "orb1_breakout": empty_orb1,
                        "orb_0dte": empty_orb1,
                        "trendline_0dte": {"total_positions": 0, "positions": [], "summary": {}},
                    }, status=500)

                orb_positions = []
                trendline_positions = []

                # ORB1 Breakout open positions
                try:
                    executor = getattr(getattr(system, "dte0_manager", None), "options_executor", None)
                    for pos in (executor.get_open_positions() or []) if executor else []:
                        md = getattr(pos, "metadata", None)
                        md = md if isinstance(md, dict) else {}
                        osnap = md.get("option_stealth") if isinstance(md.get("option_stealth"), dict) else {}
                        orb_positions.append({
                            "position_id": getattr(pos, "position_id", None),
                            "symbol": getattr(pos, "symbol", None),
                            "position_type": getattr(pos, "position_type", None),
                            "quantity": getattr(pos, "quantity", None),
                            "entry_price": getattr(pos, "entry_price", None),
                            "entry_time": getattr(getattr(pos, "entry_time", None), "isoformat", lambda: None)(),
                            "current_value": getattr(pos, "current_value", None),
                            "unrealized_pnl": getattr(pos, "unrealized_pnl", None),
                            "status": getattr(pos, "status", None),
                            "premium_source": osnap.get("premium_source"),
                            "current_effective_premium": osnap.get("current_effective_premium"),
                            "current_pnl_pct_per_contract": osnap.get("current_pnl_pct_per_contract"),
                            "stop_effective": osnap.get("stop_effective"),
                            "trail_active": osnap.get("trail_active"),
                            "be_active": osnap.get("be_active"),
                            "degraded_data_active": osnap.get("degraded_data_active"),
                        })
                except Exception as orb_err:
                    logger.warning(f"Failed reading ORB1 Breakout open options: {orb_err}")

                # Trendline 0DTE open positions (unified registry)
                try:
                    from modules.unified_0dte_account import (
                        list_trendline_open_positions,
                        normalize_trendline_position_attrs,
                        trendline_position_entry_cost,
                        trendline_position_option_side,
                    )

                    tl_open = list_trendline_open_positions(system)
                    for pos in tl_open:
                        pos = normalize_trendline_position_attrs(pos)
                        md = getattr(pos, "metadata", None)
                        md = md if isinstance(md, dict) else {}
                        osnap = md.get("option_stealth") if isinstance(md.get("option_stealth"), dict) else {}
                        snap = md.get("signal_snapshot") if isinstance(md.get("signal_snapshot"), dict) else {}
                        entry_cost = getattr(pos, "entry_cost", None)
                        if not entry_cost:
                            try:
                                entry_cost = trendline_position_entry_cost(pos)
                            except Exception:
                                entry_cost = None
                        opened_at = getattr(pos, "opened_at", None) or getattr(pos, "entry_time", None)
                        trendline_positions.append({
                            "position_id": getattr(pos, "position_id", None),
                            "symbol": getattr(pos, "symbol", None),
                            "option_side": trendline_position_option_side(pos) or getattr(pos, "option_side", None),
                            "direction": str(
                                getattr(pos, "direction", "")
                                or snap.get("direction")
                                or ""
                            ),
                            "quantity": getattr(pos, "quantity", None),
                            "entry_cost": entry_cost,
                            "opened_at": (
                                opened_at.isoformat()
                                if hasattr(opened_at, "isoformat")
                                else None
                            ),
                            "status": getattr(pos, "status", None),
                            "premium_source": osnap.get("premium_source"),
                            "current_effective_premium": osnap.get("current_effective_premium"),
                            "current_pnl_pct_per_contract": osnap.get("current_pnl_pct_per_contract"),
                            "stop_effective": osnap.get("stop_effective"),
                            "trail_active": osnap.get("trail_active"),
                            "be_active": osnap.get("be_active"),
                            "degraded_data_active": osnap.get("degraded_data_active"),
                        })
                except Exception as tl_err:
                    logger.warning(f"Failed reading Trendline 0DTE open options: {tl_err}")

                def _summary(rows):
                    pnl_rows = [r.get("current_pnl_pct_per_contract") for r in rows if isinstance(r.get("current_pnl_pct_per_contract"), (int, float))]
                    degraded = sum(1 for r in rows if bool(r.get("degraded_data_active")))
                    return {
                        "open_count": len(rows),
                        "pnl_count": len(pnl_rows),
                        "avg_open_pnl_pct_per_contract": (sum(float(x) for x in pnl_rows) / len(pnl_rows)) if pnl_rows else None,
                        "degraded_data_positions": degraded,
                    }

                orb1_payload = {
                    "total_positions": len(orb_positions),
                    "positions": orb_positions,
                    "summary": _summary(orb_positions),
                }
                return web.json_response({
                    "timestamp": datetime.now().isoformat(),
                    "mode": system.config.mode.value if hasattr(system.config, "mode") else "unknown",
                    "orb1_breakout": orb1_payload,
                    "orb_0dte": orb1_payload,
                    "trendline_0dte": {
                        "total_positions": len(trendline_positions),
                        "positions": trendline_positions,
                        "summary": _summary(trendline_positions),
                    },
                })
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"Error in /api/options-positions endpoint: {e}")
                empty_orb1 = {"total_positions": 0, "positions": [], "summary": {}}
                return web.json_response({
                    "error": str(e),
                    "orb1_breakout": empty_orb1,
                    "orb_0dte": empty_orb1,
                    "trendline_0dte": {"total_positions": 0, "positions": [], "summary": {}},
                }, status=500)
        
        app = web.Application()
        app.router.add_get('/health', handle_health)
        app.router.add_get('/ready', handle_ready)
        app.router.add_get('/api/health', handle_health)  # Alias for Cloud Scheduler keep-alive
        app.router.add_get('/api/ready', handle_ready)
        app.router.add_get('/api/broker-certification', handle_broker_certification)
        app.router.add_get('/metrics', handle_metrics)
        app.router.add_get('/status', handle_status)
        app.router.add_post('/control', handle_control)
        app.router.add_post('/api/build-watchlist', handle_build_watchlist)  # Cloud Scheduler endpoint
        app.router.add_get('/api/watchlist-status', handle_watchlist_status)  # Watchlist status endpoint
        app.router.add_post('/api/cleanup-historical-data', handle_cleanup_historical_data)  # Historical data cleanup endpoint (4:05 PM ET)
        app.router.add_post('/api/cleanup/images', handle_cleanup_images)  # Container image and revision cleanup endpoint (Rev 00259 - Weekly)
        app.router.add_post('/api/oauth/token-renewed', handle_oauth_token_renewed)  # OAuth webhook endpoint
        app.router.add_get('/api/oauth/test-alert', handle_oauth_test_alert)  # OAuth test endpoint
        app.router.add_post('/api/end-of-day-report', handle_end_of_day_report)  # EOD report endpoint (12:55 PM PT)
        app.router.add_post('/api/alerts/market-open', handle_market_open_alert)  # Good Morning alert (8:30 AM ET) - RESTORED Oct 24
        app.router.add_post('/api/alerts/validation-candle-700', handle_validation_candle_700)  # 7:00 AM PT: capture open for 7:00-7:15 bar (Cloud Scheduler)
        app.router.add_post('/api/alerts/prefetch-validation-715', handle_prefetch_validation_715)  # 7:15 AM PT: prefetch 7:00 open + 7:15 close (Cloud Scheduler)
        # NOTE: /api/alerts/midnight-token-expiry → OAuth backend handles midnight alert (separate service)
        app.router.add_get('/api/pending-signals', handle_pending_signals)  # Pending signals endpoint (Oct 27, 2025) - Real-time signal monitoring
        app.router.add_get('/api/positions', handle_positions)  # Position tracking endpoint (Rev 00068 - Oct 30, 2025) - Real-time position monitoring
        app.router.add_get('/api/options-positions', handle_options_positions)  # ORB1 Breakout + Trendline 0DTE open options PnL snapshot
        app.router.add_post('/api/alerts/market-holiday-check', handle_market_holiday_check)  # Market holiday check endpoint (5:30 AM PT)
        app.router.add_post('/api/manual-orb-capture', handle_manual_orb_capture)  # Manual ORB capture endpoint (Rev 00173)
        app.router.add_get('/', handle_health)  # Root endpoint
        
        # Start server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, ARGS.host, ARGS.port)
        await site.start()
        
        logger = logging.getLogger("improved_main")
        logger.info(f"HTTP server started on {ARGS.host}:{ARGS.port}")
        
        return runner
        
    except Exception as e:
        logger = logging.getLogger("improved_main")
        logger.error(f"Failed to start HTTP server: {e}")
        return None

# --- Graceful Shutdown ---
async def graceful_shutdown(http_runner=None, trading_task=None):
    """Graceful shutdown for all services"""
    logger = logging.getLogger("improved_main")
    logger.info("Initiating graceful shutdown...")
    
    try:
        # Cancel trading task if running
        if trading_task and not trading_task.done():
            logger.info("🛑 Stopping trading system...")
            trading_task.cancel()
            try:
                await trading_task
            except asyncio.CancelledError:
                logger.info("✅ Trading system stopped")
            except Exception as e:
                logger.error(f"❌ Error stopping trading system: {e}")
        
        # OAuth keep-alive handled by Cloud Scheduler (no shutdown needed)
        logger.info("ℹ️  OAuth keep-alive runs automatically via Cloud Scheduler")
        
        # Shutdown HTTP server
        if http_runner:
            await http_runner.cleanup()
        
        # Shutdown integrated system
        system = get_integrated_system()
        await system.shutdown()
        
        logger.info("Graceful shutdown completed")
        
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")

# --- Signal Handlers ---
# Global shutdown flag
shutdown_event = asyncio.Event()

def setup_signal_handlers(http_runner=None, trading_task=None):
    """Setup signal handlers for graceful shutdown"""
    def signal_handler(signum, frame):
        logger = logging.getLogger("improved_main")
        logger.info(f"Received signal {signum}, setting shutdown flag...")
        
        # Set the shutdown event to trigger graceful shutdown in the main loop
        shutdown_event.set()
    
    # Only setup signal handlers in the main thread
    try:
        import logging
        log = logging.getLogger("improved_main")
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        log.info("Signal handlers setup successfully")
    except ValueError as e:
        log.warning(f"Cannot setup signal handlers: {e} (not in main thread)")
        # In Cloud Run, signal handling is managed by the platform

# --- Main Function ---
async def main():
    """Main async function"""
    # Setup logging
    logger = setup_logging()
    
    # Cloud Run: start HTTP server before any GCP/network so container passes startup probe
    # (Skip when cloud_run_entry.py already started a minimal server.)
    http_runner = None
    trading_task = None
    if ARGS.cloud_mode and os.getenv("CLOUD_RUN_SERVER_ALREADY") != "1":
        logger.info("☁️  Cloud mode: Starting HTTP server first (fast startup)...")
        http_runner = await start_http_server()
        if http_runner:
            logger.info("✅ HTTP server listening on PORT (Cloud Run startup OK)")
    
    # Setup Google Cloud logging if available (server already listening in cloud mode)
    setup_cloud_logging()

    # Detect cloud deployment
    is_cloud_deployment = (
        ARGS.cloud_mode or 
        os.getenv('K_SERVICE') or  # Cloud Run
        os.getenv('GAE_APPLICATION') or  # App Engine
        os.getenv('FUNCTION_NAME') or  # Cloud Functions
        os.getenv('CLOUD_RUN_JOB')  # Cloud Run Jobs
    )

    if is_cloud_deployment:
        try:
            from modules.telemetry_startup import run_telemetry_startup_hygiene

            run_telemetry_startup_hygiene()
        except Exception as exc:
            logger.warning("Telemetry startup hygiene skipped: %s", exc)
    
    logger.info("🚀 Starting ETrade Strategy - Improved")
    
    # Log version to verify fresh code deployment
    try:
        with open("VERSION.txt", "r") as f:
            version = f.read().strip()
            logger.info(f"📦 CODE VERSION: {version}")
    except:
        logger.warning("⚠️ VERSION.txt not found - cache might be stale")
    
    logger.info(f"Strategy Mode: {ARGS.strategy_mode}")
    logger.info(f"System Mode: {ARGS.system_mode}")
    logger.info(f"Environment: {ARGS.environment}")
    logger.info(f"ETrade Mode: {ARGS.mode}")
    logger.info(f"Cloud Mode: {ARGS.cloud_mode}")
    logger.info(f"Cloud Deployment Detected: {is_cloud_deployment}")
    logger.info(f"Pre-market Analysis: {ARGS.enable_premarket}")
    logger.info(f"Confluence Trading: {ARGS.enable_confluence}")
    logger.info(f"Multi-Strategy: {ARGS.enable_multi_strategy}")
    logger.info(f"News Sentiment: {ARGS.enable_news_sentiment}")
    logger.info(f"Enhanced Signals: {ARGS.enable_enhanced_signals}")
    logger.info(f"Production Signals: {ARGS.enable_production_signals}")
    logger.info(f"Signal Optimization: {ARGS.enable_signal_optimization}")
    logger.info(f"OAuth Keep-Alive: Managed by Cloud Scheduler")
    
    # Initialize ETrade OAuth and Trader
    logger.info("Initializing ETrade integration...")
    etrade_trader = None
    etrade_oauth = None
    try:
        # Rev 00256 (Jan 22, 2026): CRITICAL FIX - Always use production tokens for both Demo and Live
        # Sandbox tokens are being phased out - both modes use production API
        # Demo mode uses sim account, Live mode uses live account
        secret_manager_env = 'prod'  # Always use production tokens
        logger.info(f"ETrade Mode: {ARGS.mode} → Using PRODUCTION tokens (sandbox phased out)")
        logger.info(f"   Demo mode: Uses sim account with production tokens")
        logger.info(f"   Live mode: Uses live account with production tokens")
        
        etrade_oauth = get_etrade_oauth_integration(secret_manager_env)
        
        # Check OAuth status
        oauth_status = etrade_oauth.get_auth_status()
        logger.info(f"OAuth Status: {oauth_status}")
        
        if not etrade_oauth.is_authenticated():
            logger.warning("⚠️  OAuth not authenticated. Please setup tokens first.")
            logger.info(f"Run: cd modules && python3 keepalive_oauth.py {ARGS.mode}")
            if ARGS.mode == 'live' and not ARGS.cloud_mode:
                logger.warning("⚠️  Live trading requires proper OAuth setup")
                return
            # In cloud mode, continue to start HTTP server even without OAuth
            if ARGS.cloud_mode:
                logger.warning("☁️  Cloud mode: Starting HTTP server despite OAuth issue")
                logger.warning("   System will wait for OAuth tokens from Secret Manager")
        
        logger.info("✅ OAuth authentication ready")
        
        # Rev 00245: Use broker config manager for account selection
        from modules.broker_config_manager import get_broker_config_manager
        broker_config = get_broker_config_manager()
        
        # Get ORB Strategy account configuration
        orb_account_config = broker_config.get_orb_account_config()
        if orb_account_config:
            logger.info(f"📊 ORB Strategy: {orb_account_config.broker_type.value.upper()} account {orb_account_config.account_id} ({orb_account_config.account_name or 'Unnamed'})")
        
        # Rev 00256: Always use production environment for tokens
        # Account selection (sim vs live) follows canonical MODE via runtime_mode
        # Rev 00362: Retry on cold start (SM token may lag behind Cloud Run instance boot)
        etrade_trader = None
        import time as _etrade_retry_time
        _trader_attempts = 3 if ARGS.cloud_mode else 1
        for _trader_attempt in range(1, _trader_attempts + 1):
            try:
                _candidate = PrimeETradeTrading(environment='prod')
                if _candidate.initialize():
                    etrade_trader = _candidate
                    logger.info(
                        "✅ ETrade %s trader initialized (attempt %d/%d)",
                        ARGS.mode,
                        _trader_attempt,
                        _trader_attempts,
                    )
                    break
                logger.error(
                    "❌ ETrade %s trader initialize() failed (attempt %d/%d)",
                    ARGS.mode,
                    _trader_attempt,
                    _trader_attempts,
                )
            except Exception as _trader_init_err:
                logger.error(
                    "❌ ETrade trader construction failed (attempt %d/%d): %s",
                    _trader_attempt,
                    _trader_attempts,
                    _trader_init_err,
                )
            if etrade_trader:
                break
            if ARGS.cloud_mode and _trader_attempt < _trader_attempts:
                _sleep_sec = 2 * _trader_attempt
                logger.warning(
                    "☁️  Cloud cold-start: retrying E*TRADE trader in %ds (tokens may not be visible yet)",
                    _sleep_sec,
                )
                _etrade_retry_time.sleep(_sleep_sec)

        if etrade_trader:
            # Rev 00245: Select ORB Strategy account if configured (Live Mode only).
            # Demo authenticates for true-to-live chains/quotes but must not select Live trading account IDs.
            if (
                ARGS.mode != 'demo'
                and orb_account_config
                and orb_account_config.broker_type.value == 'etrade'
            ):
                if etrade_trader.select_account(orb_account_config.account_id):
                    logger.info(f"✅ Selected ORB Strategy account: {orb_account_config.account_id}")
                else:
                    logger.warning(f"⚠️ Failed to select ORB account {orb_account_config.account_id}, using default")
            elif ARGS.mode == 'demo':
                logger.info(
                    "DEMO_ACCOUNT_POLICY | skip_orb_account_select | reason=demo_uses_mock_ledger_balances | "
                    "etrade_auth_for_market_data_only=true"
                )
        else:
            logger.error(f"❌ Failed to initialize ETrade {ARGS.mode} trader after {_trader_attempts} attempt(s)")
            if ARGS.mode == 'live' and not ARGS.cloud_mode:
                logger.warning("⚠️  Live trading requires proper OAuth setup")
                logger.info("Run: cd modules && python3 keepalive_oauth.py prod")
                return
        
        # OAuth keep-alive handled automatically by Cloud Scheduler
        # No local keep-alive needed - Cloud Scheduler hits backend every hour
        logger.info("ℹ️  OAuth keep-alive managed by Cloud Scheduler (hourly at :00 and :30)")
    except Exception as e:
        logger.error(f"ETrade initialization failed: {e}")
        if ARGS.mode == 'live' and not ARGS.cloud_mode:
            logger.warning("⚠️  Live trading requires proper OAuth setup")
            logger.info("Run: cd modules && python3 keepalive_oauth.py prod")
            return
        # In cloud mode, continue to start HTTP server even with OAuth errors
        if ARGS.cloud_mode:
            logger.warning("☁️  Cloud mode: Continuing despite ETrade initialization error")
            logger.warning("   HTTP server will start, system will retry OAuth later")
    
    if not ARGS.cloud_mode:
        http_runner = None
    # trading_task already set above; ensure defined for finally
    if trading_task is None:
        trading_task = None
    
    try:
        resolved_mode = SystemMode.DEMO_MODE if ARGS.mode == 'demo' else SystemMode.LIVE_MODE

        # Create system configuration
        system_config = TradingConfig(
            mode=resolved_mode,
            strategy_mode=_to_strategy_mode(ARGS.strategy_mode),
            enable_premarket_analysis=ARGS.enable_premarket,
            enable_confluence_trading=ARGS.enable_confluence,
            enable_multi_strategy=ARGS.enable_multi_strategy,
            enable_news_sentiment=ARGS.enable_news_sentiment,
            enable_enhanced_signals=ARGS.enable_enhanced_signals,
            max_positions=ARGS.max_positions,
            scan_frequency=ARGS.scan_frequency
        )
        
        # Rev 00063: Use get_integrated_system() to ensure single instance (fixes health endpoint)
        system = get_integrated_system()
        system.etrade_oauth = etrade_oauth
        
        # trading_task already defined above for safe shutdown
        
        # Initialize integrated trading system (without UnifiedServicesManager)
        # Note: UnifiedServicesManager disabled to avoid duplicate alert systems
        logger.info("🔧 Initializing integrated trading system...")
        
        # Initialize system with minimal components (system will create its own)
        # Rev 00236: Support configurable broker (backward compatible with etrade_oauth)
        broker_type = os.getenv('BROKER_TYPE', 'etrade').lower()
        minimal_components = {
            'data_manager': None,  # System will initialize
            'broker_oauth': etrade_oauth,  # Rev 00236: Generic broker OAuth (backward compatible)
            'etrade_oauth': etrade_oauth,  # Backward compatibility
            'broker_type': broker_type,  # Rev 00236: Configurable broker type
            # ARCHIVED (Rev 00173): Signal generator no longer used - ORB generates directly
            # 'signal_generator': None,
            'risk_manager': None,  # System will initialize
            'trade_manager': None,  # System will initialize
            'stealth_trailing': None,  # System will initialize
            'alert_manager': None  # System will initialize with proper config
        }
        
        try:
            await system.initialize(minimal_components)
            logger.info("✅ Trading system initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize trading system: {e}")
            raise
        
        system.etrade_trader = etrade_trader
        from modules.attach_live_strategies import attach_live_strategies

        await attach_live_strategies(system, etrade_trader)
        dte0_manager = getattr(system, "dte0_manager", None)

        if getattr(ARGS, "broker_certification_probe", False):
            from modules.broker_certification_probe import run_broker_certification_probe
            import json

            etrade_options_api_probe = None
            if getattr(system, "dte0_manager", None):
                etrade_options_api_probe = getattr(
                    getattr(system.dte0_manager, "options_chain_manager", None),
                    "etrade_options_api",
                    None,
                )
            cert = await run_broker_certification_probe(
                broker_adapter=getattr(system, "_broker_adapter", None),
                etrade_oauth=etrade_oauth,
                etrade_options_api=etrade_options_api_probe,
                market_manager=get_prime_market_manager(),
            )
            print(json.dumps(cert, indent=2, default=str))
            sys.exit(0 if cert.get("certified") else 1)

        if getattr(ARGS, "live_readiness_probe", False):
            from modules.live_readiness_probe import run_live_readiness_probe
            import json

            etrade_options_api_probe = None
            if getattr(system, "dte0_manager", None):
                etrade_options_api_probe = getattr(
                    getattr(system.dte0_manager, "options_chain_manager", None),
                    "etrade_options_api",
                    None,
                )
            probe = await run_live_readiness_probe(
                broker_adapter=getattr(system, "_broker_adapter", None),
                etrade_oauth=etrade_oauth,
                etrade_options_api=etrade_options_api_probe,
                market_manager=get_prime_market_manager(),
            )
            print(json.dumps(probe, indent=2, default=str))
            sys.exit(0 if probe.get("ready") else 1)
        
        # Dynamic watchlist build removed; unified 0dte_list.csv is used
        
        # Start trading system in background thread to avoid blocking HTTP server
        logger.info("🚀 Starting prime trading system with ORB strategy...")
        logger.info("📊 Using unified 0dte_list.csv (ORB1 Breakout + Trendline; row count from CSV at load)")
        logger.info("🔍 ORB capture at 6:30 AM PT, SO batch at 7:30 AM PT")
        if dte0_manager:
            logger.info("🎯 Easy 0DTE enabled - will run ORB1 Breakout, Trendline, and ORB2 Breakout options")
        if getattr(system, "trendline_signal_engine", None):
            logger.info("📈 Easy Trendline strategy enabled - post-7:30 break+momentum execution path active")

        from modules.runtime_mode import assert_config_mode_consistency

        _opts_executor = None
        if getattr(system, "dte0_manager", None):
            _opts_executor = getattr(system.dte0_manager, "options_executor", None)
        assert_config_mode_consistency(
            config_mode=system.config.mode,
            options_executor_demo_mode=(
                getattr(_opts_executor, "demo_mode", None)
                if _opts_executor is not None
                else None
            ),
            strict=True,
        )
        
        # Start trading system in background task
        trading_task = asyncio.create_task(system.start())
        
        # Setup signal handlers with trading task
        setup_signal_handlers(http_runner, trading_task)
        
        logger.info("✅ Trading system started in background thread")
        
        # Keep main thread alive to handle HTTP requests
        if ARGS.cloud_mode:
            logger.info("🌐 HTTP server running, keeping main thread alive...")
            try:
                # Wait for shutdown signal instead of infinite loop
                await shutdown_event.wait()
                logger.info("Shutdown signal received, initiating graceful shutdown...")
            except KeyboardInterrupt:
                logger.info("Received KeyboardInterrupt, shutting down...")
        else:
            # In non-cloud mode, wait for trading task to complete
            await trading_task
        
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, shutting down...")
    except Exception as e:
        logger.exception(f"Fatal error in main: {e}")
        import sys  # Ensure sys is available in exception handler
        sys.exit(1)
    finally:
        # Only attempt graceful shutdown if HTTP runner was started
        if http_runner is not None:
            await graceful_shutdown(http_runner, trading_task)

# --- Entry Point ---
if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
