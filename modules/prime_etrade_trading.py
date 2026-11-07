"""
Prime ETrade Trading Module

This module provides comprehensive ETrade API integration for the trading system.
It implements all the essential ETrade API functions based on the provided checklist:

Auth (OAuth 1.0a): getRequestToken, authorizeUrl, getAccessToken, renewAccessToken, revokeAccessToken
Accounts: listAccounts, getAccountBalances, getPortfolio, listTransactions  
Orders: listOrders, getOrderDetails, previewEquityOrder, placeEquityOrder, cancelOrder
Market: getQuotes, lookupProduct, getOptionChains
Alerts: listAlerts, getAlert, deleteAlert
"""

import os
import sys
import json
import logging
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

# Rev 00180h: Use Google Secret Manager directly (no local ETradeOAuth folder dependency)
# Tokens are managed by https://easy-trading-oauth-v2.web.app/ and stored in Secret Manager
try:
    from google.cloud import secretmanager
    SECRET_MANAGER_AVAILABLE = True
    ETradeOAuth_AVAILABLE = True  # Enable ETrade trading with Secret Manager
    logging.info("✅ Google Secret Manager available for OAuth token loading")
except ImportError:
    SECRET_MANAGER_AVAILABLE = False
    ETradeOAuth_AVAILABLE = False
    logging.error("❌ Google Secret Manager not available - ETrade trading disabled")

# Import requests-oauthlib for correct OAuth 1.0a implementation
try:
    import requests
    from requests_oauthlib import OAuth1
    REQUESTS_OAUTH_AVAILABLE = True
except ImportError:
    REQUESTS_OAUTH_AVAILABLE = False
    logging.warning("requests-oauthlib not available. Using fallback OAuth implementation.")

log = logging.getLogger(__name__)

# Per-RPC timeout for Secret Manager (default API core often uses ~60s; shorten for faster fallback).
_SECRET_MANAGER_ACCESS_TIMEOUT_SEC = float(os.environ.get("ETRADE_SM_ACCESS_TIMEOUT_SEC", "20"))
_ETRADE_HTTP_WARN_LAST: Dict[str, float] = {}


def _frozen_oauth_http_budget(
    *,
    timeout_sec: Optional[float] = None,
    max_attempts: Optional[int] = None,
    instance: Any = None,
) -> Tuple[float, int]:
    """Snapshot HTTP timeout/retries for one OAuth call.

    Monitor fail-fast used to mutate instance attrs (`_market_quote_timeout_sec`,
    `_market_quote_max_attempts`). Concurrent quote threads then re-read those
    attrs mid-retry, which produced UnboundLocalError on `response` and stole
    1.275s timeouts from 30s discovery calls.
    """
    if timeout_sec is None:
        timeout_sec = getattr(instance, "_market_quote_timeout_sec", 30) if instance is not None else 30
    if max_attempts is None:
        max_attempts = getattr(instance, "_market_quote_max_attempts", 3) if instance is not None else 3
    http_timeout = float(timeout_sec or 30)
    attempts = int(max_attempts or 3)
    return max(0.2, http_timeout), max(1, attempts)


def _etrade_http_warn(key: str, message: str, *, interval_sec: float = 30.0) -> None:
    """Throttle expected E*TRADE blips (fail-fast OCC timeout, code 300)."""
    now = time.monotonic()
    last = float(_ETRADE_HTTP_WARN_LAST.get(key, 0.0) or 0.0)
    if (now - last) < float(interval_sec):
        log.debug(message)
        return
    _ETRADE_HTTP_WARN_LAST[key] = now
    log.warning(message)


def _new_secret_manager_service_client():
    """
    Build Secret Manager client. Set ETRADE_SECRET_MANAGER_TRANSPORT=rest on networks where
    gRPC DNS to secretmanager.googleapis.com fails but HTTPS REST works.
    """
    from google.cloud import secretmanager

    kwargs: Dict[str, Any] = {}
    transport = (os.environ.get("ETRADE_SECRET_MANAGER_TRANSPORT") or "").strip().lower()
    if transport in ("rest", "grpc"):
        kwargs["transport"] = transport
    return secretmanager.SecretManagerServiceClient(**kwargs)

@dataclass
class ETradeAccount:
    """ETrade Account Information"""
    account_id: str
    account_name: Optional[str]
    account_id_key: str
    account_status: str
    institution_type: str
    account_type: str

@dataclass
class ETradeBalance:
    """ETrade Account Balance Information - Essential fields only"""
    account_value: Optional[float]
    cash_available_for_investment: Optional[float]  # Cash available for investments
    cash_buying_power: Optional[float]  # Total buying power (cash + margin)
    option_level: Optional[str]

@dataclass
class ETradePosition:
    """ETrade Position Information"""
    position_id: str
    symbol: str
    symbol_description: str
    quantity: int
    position_type: str
    market_value: float
    total_cost: float
    total_gain: float
    total_gain_pct: float
    days_gain: float
    days_gain_pct: float
    # Optional Product identity for OCC sync (populated when portfolio XML includes Product).
    product: Optional[Dict[str, Any]] = None
    osi_key: str = ""
    avg_price: float = 0.0


@dataclass
class ETradeQuote:
    """ETrade Market Quote"""
    symbol: str
    last_price: float
    change: float
    # Optional: missing broker changePercent must stay None (never invent 0.0 — that
    # short-circuits previous_close derivation and stamps fake rs_vs_spy into PO).
    change_pct: Optional[float]
    volume: int
    bid: float
    ask: float
    high: float
    low: float
    open: float
    # Option Product identity (equities leave defaults)
    security_type: str = ""
    call_put: str = ""
    expiry_year: Optional[int] = None
    expiry_month: Optional[int] = None
    expiry_day: Optional[int] = None
    strike_price: Optional[float] = None
    osi_key: str = ""
    request_symbol: str = ""
    date_time_utc: Optional[int] = None
    date_time: Optional[str] = None
    raw_product: Optional[Dict[str, Any]] = None
    raw_all: Optional[Dict[str, Any]] = None
    # Option depth when Market Quote All/Option includes it (equity quotes leave None).
    bid_size: Optional[int] = None
    ask_size: Optional[int] = None
    # Equity AllQuoteDetails — used by Liquidity Guard ADV$ (broker-first).
    average_volume: Optional[int] = None
    previous_close: Optional[float] = None
    # Option All / OptionGreeks when detailFlag=ALL (equity quotes leave None).
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    implied_volatility: Optional[float] = None

class PrimeETradeTrading:
    """
    Prime ETrade Trading System
    
    Comprehensive ETrade API integration for the trading strategy.
    Handles authentication, account management, portfolio tracking, and trading operations.
    """
    
    def __init__(self, environment: str = 'prod'):
        consumer_key = os.getenv("ETRADE_CONSUMER_KEY")
        consumer_secret = os.getenv("ETRADE_CONSUMER_SECRET")
        access_token = os.getenv("ETRADE_ACCESS_TOKEN")
        access_token_secret = os.getenv("ETRADE_ACCESS_TOKEN_SECRET")

        env_creds_available = all([
            consumer_key,
            consumer_secret,
            access_token,
            access_token_secret
        ])

        if env_creds_available:
            self.environment = "production"
            self.config = {
                "consumer_key": consumer_key,
                "consumer_secret": consumer_secret,
                "base_url": "https://api.etrade.com",
            }
            self.tokens = {
                "oauth_token": access_token,
                "oauth_token_secret": access_token_secret,
                "last_used": datetime.now(timezone.utc).isoformat()
            }
            self.accounts = []
            self.accounts_loaded = True
            self.selected_account = None
            self.resolved_market_data_account = None
            self.resolved_trading_account = None
            self.balance = None
            self.portfolio = []
            self._warned_missing_account_ids = set()
            self._auth_source_environment = True
            self._skip_secret_manager = True
            log.info("OPTION_AUTH_SOURCE | source=env | mode=early_override")
            return

        # Sandbox deprecated: data and API always use production. Normalize so token/API loading never uses sandbox.
        self.environment = 'prod' if environment in ('prod', 'demo', 'live', 'sandbox') else environment
        self.config = None
        self.tokens = None
        self.accounts: List[ETradeAccount] = []
        self.selected_account: Optional[ETradeAccount] = None
        self.resolved_market_data_account: Optional[ETradeAccount] = None
        self.resolved_trading_account: Optional[ETradeAccount] = None
        self.balance: Optional[ETradeBalance] = None
        self.portfolio: List[ETradePosition] = []
        # One warning per stale account_id per process (ORB + 0DTE may call select_account repeatedly)
        self._warned_missing_account_ids: set = set()
        # True when all four ETRADE_* env vars supplied at init — no Secret Manager on this process.
        self._auth_source_environment: bool = False
        self._skip_secret_manager: bool = False
        
        if not ETradeOAuth_AVAILABLE:
            raise Exception("ETradeOAuth not available. Please set up ETradeOAuth system first.")
        
        if self._apply_environment_credentials_bundle():
            pass
        else:
            self._load_credentials()
            self._load_tokens()
            log.info("OPTION_AUTH_SOURCE | source=gcp_secret_manager")
        # Rev 00244 (Jan 12, 2026): CRITICAL FIX - Don't fail initialization if account loading fails
        # Account loading can fail with HTTP 401 if tokens are invalid, but we still need
        # etrade_trader to be initialized for data fetching (quotes, intraday bars)
        try:
            self._load_accounts()
        except Exception as e:
            log.warning(f"⚠️ Failed to load accounts during initialization: {e}")
            log.warning(f"   Continuing without accounts - data fetching will still work")
            log.warning(f"   Accounts can be loaded later via initialize() method")
            # Don't raise - allow initialization to continue for data fetching
    
    def initialize(self) -> bool:
        """
        Initialize the ETrade trading system
        
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            if getattr(self, "_skip_secret_manager", False):
                log.info("OPTION_AUTH_SOURCE | source=env | mode=initialize_skip")
                # Minimal validation only for hard env override.
                if not self.config or not self.tokens:
                    raise RuntimeError("Missing env-based credentials")
                return True
            log.info(f"🔧 Initializing ETrade {self.environment} trading system...")
            
            # Validate OAuth tokens
            if not self._validate_oauth_tokens():
                log.error("❌ OAuth token validation failed")
                return False
            
            # Test API connection
            if not self._test_api_connection():
                log.error("❌ API connection test failed")
                return False
            
            # Load account data
            if not self.accounts:
                log.error("❌ No accounts found")
                return False
            
            # Select primary account
            self._select_primary_account()
            
            log.info(f"✅ ETrade {self.environment} trading system initialized successfully")
            log.info(f"   Selected account: {self.selected_account.account_name if self.selected_account else 'None'}")
            log.info(f"   Total accounts: {len(self.accounts)}")
            
            return True
            
        except Exception as e:
            log.error(f"❌ Failed to initialize ETrade trading system: {e}")
            return False
    
    def _validate_oauth_tokens(self) -> bool:
        """
        Validate OAuth tokens
        
        Returns:
            True if tokens are valid, False otherwise
        """
        try:
            if not self.tokens:
                log.error("No OAuth tokens found")
                return False
            
            # Check if tokens have required fields
            required_fields = ['oauth_token', 'oauth_token_secret']
            for field in required_fields:
                if field not in self.tokens or not self.tokens[field]:
                    log.error(f"Missing required token field: {field}")
                    return False
            
            # Check if tokens are expired (past midnight ET)
            if self._is_token_expired():
                log.error("OAuth tokens expired at midnight ET")
                return False
            
            # Check if tokens need renewal (idle for 2+ hours)
            if self._needs_token_renewal():
                log.warning("OAuth tokens idle for 2+ hours, renewal needed")
                if not self._renew_tokens():
                    log.error("Failed to renew OAuth tokens")
                    return False
            
            log.info("✅ OAuth tokens validated successfully")
            return True
            
        except Exception as e:
            log.error(f"OAuth token validation failed: {e}")
            return False
    
    def _is_token_expired(self) -> bool:
        """Check if OAuth tokens are expired (check last_used for validity)"""
        try:
            if not self.tokens:
                return True
            
            # Check last_used timestamp - if used recently, tokens are valid
            last_used = self.tokens.get('last_used')
            if last_used:
                try:
                    last_used_dt = datetime.fromisoformat(last_used.replace('Z', '+00:00'))
                    now = datetime.now(timezone.utc)
                    
                    # If used within last 2 hours, tokens are definitely valid
                    hours_since_use = (now - last_used_dt).total_seconds() / 3600
                    if hours_since_use < 2:
                        log.debug(f"Tokens used {hours_since_use:.1f} hours ago - still valid")
                        return False
                except Exception as e:
                    log.debug(f"Could not parse last_used timestamp: {e}")
            
            # Check timestamp field (from Secret Manager)
            timestamp = self.tokens.get('timestamp')
            if timestamp:
                try:
                    timestamp_dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    now = datetime.now(timezone.utc)
                    
                    # If created/updated today (within last 24 hours), tokens are valid
                    hours_since_creation = (now - timestamp_dt).total_seconds() / 3600
                    if hours_since_creation < 24:
                        log.debug(f"Tokens created {hours_since_creation:.1f} hours ago - still valid")
                        return False
                except Exception as e:
                    log.debug(f"Could not parse timestamp: {e}")
            
            # Fallback: Assume tokens are valid (don't block the system)
            log.warning("Could not determine token expiration - assuming valid")
            return False
            
        except Exception as e:
            log.error(f"Error checking token expiration: {e}")
            # Default to valid to avoid blocking
            return False
    
    def _needs_token_renewal(self) -> bool:
        """Check if OAuth tokens need renewal (idle for 2+ hours)"""
        try:
            if not self.tokens:
                return True
            
            # Check timestamp field first (from Secret Manager)
            timestamp = self.tokens.get('timestamp')
            if timestamp:
                try:
                    timestamp_dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    hours_since = (datetime.now(timezone.utc) - timestamp_dt).total_seconds() / 3600
                    if hours_since < 2:
                        log.debug(f"Tokens renewed {hours_since:.1f} hours ago - fresh, no renewal needed")
                        return False
                except Exception as e:
                    log.debug(f"Could not parse timestamp: {e}")
            
            # Check last_used field (legacy)
            last_used = self.tokens.get('last_used')
            if not last_used:
                # Secret Manager bundles often omit metadata; do not force renewal on Cloud Run
                # (central_oauth_manager may be unavailable and would fail validation).
                log.debug(
                    "No timestamp or last_used on tokens — skip idle renewal (use SM tokens as-is)"
                )
                return False
            
            # Parse last used time
            last_used_dt = datetime.fromisoformat(last_used.replace('Z', '+00:00'))
            
            # Make timezone-aware if needed
            if last_used_dt.tzinfo is None:
                last_used_dt = last_used_dt.replace(tzinfo=timezone.utc)
            
            # Check if idle for 2+ hours
            idle_hours = (datetime.now(timezone.utc) - last_used_dt).total_seconds() / 3600
            return idle_hours >= 2
            
        except Exception as e:
            log.error(f"Error checking renewal need: {e}")
            # Default to NOT needing renewal to avoid breaking fresh tokens
            return False
    
    def _renew_tokens(self) -> bool:
        """
        Renew OAuth tokens if needed
        
        Returns:
            True if renewal successful or not needed
        """
        try:
            # Import the OAuth manager (Rev 00266: optional - may not be in path on Cloud Run)
            try:
                from central_oauth_manager import CentralOAuthManager, Environment
            except ImportError:
                import sys
                from pathlib import Path
                etrade_oauth_modules = Path(__file__).resolve().parents[1] / "ETradeOAuth" / "modules"
                if etrade_oauth_modules.exists() and str(etrade_oauth_modules) not in sys.path:
                    sys.path.insert(0, str(etrade_oauth_modules))
                    from central_oauth_manager import CentralOAuthManager, Environment
                else:
                    # Expected on Cloud Run when optional OAuth helper package is not present.
                    log.info(
                        "central_oauth_manager not available - token renewal skipped "
                        "(tokens from Secret Manager only)"
                    )
                    return True
            
            oauth_manager = CentralOAuthManager()
            # Sandbox deprecated: always use production tokens for data and trading
            success = oauth_manager.renew_if_needed(Environment.PROD)
            
            if success:
                log.info("✅ OAuth tokens renewed successfully")
                self._load_tokens()
                return True
            else:
                log.warning("⚠️ OAuth token renewal failed")
                return False
                
        except Exception as e:
            log.error(f"Error renewing tokens: {e}")
            return False
    
    def _test_api_connection(self) -> bool:
        """
        Test API connection with a simple call
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            log.info("🔍 Testing API connection...")
            
            # Test with account list call
            response = self._make_etrade_api_call(
                method='GET',
                url="/v1/accounts/list",
                params={}
            )
            
            # Check if response contains error
            if isinstance(response, dict) and 'error' in response:
                log.error(f"API test failed: {response['error']}")
                return False
            
            # Check if response contains account data
            if isinstance(response, dict) and 'AccountListResponse' in response:
                log.info("✅ API connection test successful")
                return True
            elif isinstance(response, str) and 'AccountListResponse' in response:
                log.info("✅ API connection test successful")
                return True
            else:
                log.error(f"Unexpected API response: {response}")
                return False
                
        except Exception as e:
            log.error(f"API connection test failed: {e}")
            return False
    
    def _select_primary_account(self):
        """Select the primary trading account"""
        try:
            if not self.accounts:
                log.warning("No accounts available for selection")
                return
            
            # Select the first account as primary
            self.selected_account = self.accounts[0]
            log.info(f"Selected primary account: {self.selected_account.account_name} ({self.selected_account.account_id})")
            
        except Exception as e:
            log.error(f"Failed to select primary account: {e}")
    
    def is_authenticated(self) -> bool:
        """
        Check if the system is properly authenticated
        
        Returns:
            True if authenticated and ready for trading
        """
        try:
            if getattr(self, "_auth_source_environment", False):
                return bool(self.tokens) and bool(self.config)
            return (
                self.tokens is not None and
                self.accounts is not None and
                len(self.accounts) > 0 and
                self.selected_account is not None
            )
        except Exception as e:
            log.error(f"Error checking authentication status: {e}")
            return False
    
    def _make_etrade_api_call(
        self,
        method: str,
        url: str,
        params: Dict = None,
        timeout_sec: Optional[float] = None,
        max_attempts: Optional[int] = None,
    ):
        """Make ETrade API call with correct OAuth 1.0a implementation"""
        if REQUESTS_OAUTH_AVAILABLE:
            return self._make_correct_oauth_call(
                method,
                url,
                params,
                timeout_sec=timeout_sec,
                max_attempts=max_attempts,
            )
        else:
            # Fallback to original implementation
            return self._make_legacy_oauth_call(method, url, params)
    
    def _make_correct_oauth_call(
        self,
        method: str,
        url: str,
        params: Dict = None,
        timeout_sec: Optional[float] = None,
        max_attempts: Optional[int] = None,
    ):
        """Make ETrade API call with correct OAuth 1.0a HMAC-SHA1 signature"""
        try:
            # Ensure all OAuth parameters are strings and not None
            consumer_key = str(self.config.get('consumer_key', '')) if self.config.get('consumer_key') else ''
            consumer_secret = str(self.config.get('consumer_secret', '')) if self.config.get('consumer_secret') else ''
            oauth_token = str(self.tokens.get('oauth_token', '')) if self.tokens.get('oauth_token') else ''
            oauth_token_secret = str(self.tokens.get('oauth_token_secret', '')) if self.tokens.get('oauth_token_secret') else ''
            
            # Validate that all required OAuth parameters are present
            if not all([consumer_key, consumer_secret, oauth_token, oauth_token_secret]):
                log.error(f"Missing OAuth parameters: consumer_key={bool(consumer_key)}, consumer_secret={bool(consumer_secret)}, oauth_token={bool(oauth_token)}, oauth_token_secret={bool(oauth_token_secret)}")
                return {"error": "Missing OAuth parameters"}
            
            # Set up OAuth 1.0a with HMAC-SHA1
            oauth = OAuth1(
                client_key=consumer_key,
                client_secret=consumer_secret,
                resource_owner_key=oauth_token,
                resource_owner_secret=oauth_token_secret,
                signature_method="HMAC-SHA1",
                signature_type="AUTH_HEADER",  # OAuth params in Authorization header
            )
            
            # Make the request
            if url.startswith('/'):
                full_url = f"{self.config['base_url']}{url}"
            else:
                full_url = url
            
            # Add Accept header for JSON responses
            headers = {"Accept": "application/json"}

            # Retry transient network failures (e.g., read timeouts) to reduce false-negative API errors.
            # Freeze budget locally so concurrent fail-fast quotes cannot shrink retries mid-loop.
            http_timeout, max_attempts = _frozen_oauth_http_budget(
                timeout_sec=timeout_sec,
                max_attempts=max_attempts,
                instance=self,
            )
            response = None
            last_transient = None
            for attempt in range(1, max_attempts + 1):
                try:
                    response = requests.request(
                        method=method,
                        url=full_url,
                        params=params or {},
                        headers=headers,
                        auth=oauth,
                        timeout=http_timeout,
                    )
                    last_transient = None
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    last_transient = e
                    if attempt >= max_attempts:
                        raise
                    backoff_seconds = 0.75 * attempt
                    log.warning(
                        f"OAuth API transient failure (attempt {attempt}/{max_attempts}) for {method} {full_url}: {e}. "
                        f"Retrying in {backoff_seconds:.2f}s"
                    )
                    time.sleep(backoff_seconds)

            if response is None:
                if last_transient is not None:
                    raise last_transient
                return {"error": "OAuth API call failed: no response"}
            
            # Handle response (Rev 00266: 204 No Content is success for some E*TRADE endpoints)
            if response.status_code == 200:
                try:
                    return response.json()
                except Exception:
                    return response.text
            if response.status_code == 204:
                return {}  # No Content - success with empty body
            else:
                body = response.text or ""
                body_l = body.lower()
                # E*TRADE 10031 = no options listed for that expiry/month — expected on
                # empty calendar days during forward walks. Do not flood ERROR logs.
                if response.status_code == 400 and (
                    "10031" in body or "no options for the given month" in body_l
                ):
                    log.info(
                        "API empty expiry (10031): %s - %s",
                        response.status_code,
                        body.replace("\n", " ").strip()[:240],
                    )
                elif response.status_code == 400 and (
                    "service is not available" in body_l
                    or '"code":300' in body.replace(" ", "")
                    or "code>300<" in body_l
                ):
                    _etrade_http_warn(
                        "http_400_unavailable",
                        f"ETRADE_HTTP | status=400 | code=300 | url={full_url} | "
                        f"body={body.replace(chr(10), ' ').strip()[:180]}",
                    )
                else:
                    log.error(f"API call failed: {response.status_code} - {response.text}")
                return {
                    "error": f"HTTP {response.status_code}",
                    "message": response.text,
                    "error_code": "10031" if "10031" in body else (
                        "300" if "service is not available" in body_l else None
                    ),
                }
                
        except Exception as e:
            err = str(e)
            err_l = err.lower()
            if "timed out" in err_l or "timeout" in err_l:
                _etrade_http_warn(
                    f"read_timeout:{http_timeout if 'http_timeout' in locals() else '?'}",
                    f"ETRADE_HTTP | read_timeout | url={full_url} | error={err}",
                )
            else:
                log.error(f"OAuth API call failed: {e}")
            return {"error": str(e)}
    
    def _make_legacy_oauth_call(self, method: str, url: str, params: Dict = None):
        """Fallback OAuth implementation using original method"""
        original_cwd = os.getcwd()
        etrade_oauth_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ETradeOAuth')
        
        try:
            # Ensure all OAuth parameters are strings and not None
            consumer_key = str(self.config.get('consumer_key', '')) if self.config.get('consumer_key') else ''
            consumer_secret = str(self.config.get('consumer_secret', '')) if self.config.get('consumer_secret') else ''
            oauth_token = str(self.tokens.get('oauth_token', '')) if self.tokens.get('oauth_token') else ''
            oauth_token_secret = str(self.tokens.get('oauth_token_secret', '')) if self.tokens.get('oauth_token_secret') else ''
            
            # Validate that all required OAuth parameters are present
            if not all([consumer_key, consumer_secret, oauth_token, oauth_token_secret]):
                log.error(f"Missing OAuth parameters in legacy call: consumer_key={bool(consumer_key)}, consumer_secret={bool(consumer_secret)}, oauth_token={bool(oauth_token)}, oauth_token_secret={bool(oauth_token_secret)}")
                return {"error": "Missing OAuth parameters"}
            
            os.chdir(etrade_oauth_dir)
            return make_oauth_request(
                method=method,
                url=url,
                params=params or {},
                consumer_key=consumer_key,
                consumer_secret=consumer_secret,
                oauth_token=oauth_token,
                oauth_token_secret=oauth_token_secret
            )
        finally:
            os.chdir(original_cwd)
        
    def _apply_environment_credentials_bundle(self) -> bool:
        """
        Local override: when all four ETRADE_* variables are set, populate config + tokens and
        skip Secret Manager entirely. Production deploys omit any of these and use existing SM paths.
        """
        consumer_key = os.getenv("ETRADE_CONSUMER_KEY")
        consumer_secret = os.getenv("ETRADE_CONSUMER_SECRET")
        access_token = os.getenv("ETRADE_ACCESS_TOKEN")
        access_token_secret = os.getenv("ETRADE_ACCESS_TOKEN_SECRET")
        env_creds_available = all(
            [
                consumer_key,
                consumer_secret,
                access_token,
                access_token_secret,
            ]
        )
        if not env_creds_available:
            return False

        if not isinstance(self.config, dict):
            self.config = {}
        self.config["consumer_key"] = consumer_key
        self.config["consumer_secret"] = consumer_secret
        self.config["base_url"] = "https://api.etrade.com"
        self.config["token_file"] = "tokens_prod.json"
        self.tokens = {
            "oauth_token": access_token,
            "oauth_token_secret": access_token_secret,
            "last_used": datetime.now(timezone.utc).isoformat(),
        }
        self._auth_source_environment = True
        self._skip_secret_manager = True
        log.info("OPTION_AUTH_SOURCE | source=env | mode=forced_override")
        return True

    def _oauth_tokens_dict_from_environment(self) -> Optional[Dict[str, Any]]:
        """Rebuild OAuth token pair from env (reload_tokens / _load_tokens when env bundle active)."""
        access_token = os.getenv("ETRADE_ACCESS_TOKEN")
        access_token_secret = os.getenv("ETRADE_ACCESS_TOKEN_SECRET")
        if not access_token or not access_token_secret:
            return None
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "oauth_token": access_token,
            "oauth_token_secret": access_token_secret,
            "last_used": now_iso,
        }

    def _load_credentials(self):
        """Load ETrade credentials from Google Secret Manager"""
        if getattr(self, "_skip_secret_manager", False):
            return
        try:
            # Try to load from Google Secret Manager first
            self.config = self._load_credentials_from_secret_manager()
            
            if not self.config:
                # Fallback to original method
                log.info("Falling back to file-based credential loading...")
                self._load_credentials_from_file()
            
            if not self.config:
                raise Exception(f"Failed to load config for {self.environment}")
            
            # Validate that we have the required credentials
            if not self.config.get('consumer_key') or not self.config.get('consumer_secret'):
                # Try mock credentials for local testing
                if not os.getenv('K_SERVICE'):  # Not running in Cloud Run
                    log.info("Attempting to use mock credentials for local testing...")
                    self._use_mock_credentials_for_local_testing()
                else:
                    raise Exception(f"Missing consumer_key or consumer_secret for {self.environment}")
            
            log.info(f"✅ Loaded ETrade credentials for {self.environment}")
            
        except Exception as e:
            log.error(f"Failed to load ETrade credentials: {e}")
            
            # For local testing, try to use environment variables or mock credentials
            if not os.getenv('K_SERVICE'):  # Not running in Cloud Run
                log.info("Attempting to use mock credentials for local testing...")
                self._use_mock_credentials_for_local_testing()
            else:
                raise
    
    def _use_mock_credentials_for_local_testing(self):
        """Use mock credentials for local testing when Secret Manager is not available"""
        try:
            log.info("Setting up mock credentials for local testing...")
            
            # Rev 00256: Always use production mock credentials (sandbox phased out)
            # Both Demo and Live modes use production API
            self.config = {
                'consumer_key': 'mock_prod_consumer_key',
                'consumer_secret': 'mock_prod_consumer_secret',
                'base_url': 'https://api.etrade.com',
                'token_file': 'tokens_prod.json'
            }
            
            log.info(f"✅ Mock credentials configured for {self.environment} (local testing)")
            
        except Exception as e:
            log.error(f"Failed to setup mock credentials: {e}")
            raise
    
    def _load_credentials_from_secret_manager(self) -> Optional[Dict[str, str]]:
        """Load credentials directly from Google Secret Manager"""
        if getattr(self, "_skip_secret_manager", False):
            return None
        # Rev 00245: Skip Python library if USE_GCLOUD_CLI env var is set (faster for local testing)
        use_gcloud_cli = os.getenv('USE_GCLOUD_CLI', '').lower() in ('true', '1', 'yes')
        
        if not use_gcloud_cli:
            try:
                # Try Python library first
                from .config_loader import get_cloud_config

                client = _new_secret_manager_service_client()
                # Rev 00190: Use centralized cloud config instead of hardcoded project_id
                cloud_config = get_cloud_config()
                project_id = cloud_config["project_id"]
                
                # Rev 00256 (Jan 22, 2026): CRITICAL FIX - Always use production tokens
                # Sandbox tokens are being phased out - both Demo and Live use production API
                # Demo mode uses sim account, Live mode uses live account
                consumer_key_secret = f"projects/{project_id}/secrets/etrade-prod-consumer-key/versions/latest"
                consumer_secret_secret = f"projects/{project_id}/secrets/etrade-prod-consumer-secret/versions/latest"
                base_url = 'https://api.etrade.com'  # Always use production API
                token_file = 'tokens_prod.json'
                
                # Access secrets directly
                log.info(f"Loading consumer credentials from Secret Manager for {self.environment}")
                consumer_key_response = client.access_secret_version(
                    request={"name": consumer_key_secret},
                    timeout=_SECRET_MANAGER_ACCESS_TIMEOUT_SEC,
                )
                consumer_secret_response = client.access_secret_version(
                    request={"name": consumer_secret_secret},
                    timeout=_SECRET_MANAGER_ACCESS_TIMEOUT_SEC,
                )
                
                consumer_key = consumer_key_response.payload.data.decode("UTF-8")
                consumer_secret = consumer_secret_response.payload.data.decode("UTF-8")
                
                if consumer_key and consumer_secret:
                    log.info(f"✅ Loaded consumer credentials from Secret Manager for {self.environment}")
                    return {
                        'consumer_key': consumer_key,
                        'consumer_secret': consumer_secret,
                        'base_url': base_url,
                        'token_file': token_file
                    }
                
                return None
                
            except Exception as e:
                # FALLBACK: Use gcloud CLI if Python library has DNS issues
                log.warning(f"Secret Manager Python library failed: {e}")
                log.info("Trying gcloud CLI fallback for Secret Manager access...")
        else:
            log.info("USE_GCLOUD_CLI set - using gcloud CLI directly (skipping Python library)")
        
        # Use gcloud CLI (either as fallback or if USE_GCLOUD_CLI is set)
        try:
            import subprocess
            from .config_loader import get_cloud_config
            
            # Rev 00190: Use centralized cloud config instead of hardcoded project_id
            cloud_config = get_cloud_config()
            project_id = cloud_config["project_id"]
            
            # Rev 00277: Production token only - sandbox token no longer used
            # Demo mode = simulated Demo account (production API); Live mode = live broker account
            key_secret = "etrade-prod-consumer-key"
            secret_secret = "etrade-prod-consumer-secret"
            base_url = 'https://api.etrade.com'
            token_file = 'tokens_prod.json'
            
            # Use gcloud CLI
            key_result = subprocess.run(
                ['gcloud', 'secrets', 'versions', 'access', 'latest',
                 f'--secret={key_secret}', f'--project={project_id}'],
                capture_output=True, text=True, timeout=10
            )
            
            secret_result = subprocess.run(
                ['gcloud', 'secrets', 'versions', 'access', 'latest',
                 f'--secret={secret_secret}', f'--project={project_id}'],
                capture_output=True, text=True, timeout=10
            )
            
            if key_result.returncode == 0 and secret_result.returncode == 0:
                consumer_key = key_result.stdout.strip()
                consumer_secret = secret_result.stdout.strip()
                log.info(f"✅ Loaded credentials via gcloud CLI for {self.environment}")
                return {
                    'consumer_key': consumer_key,
                    'consumer_secret': consumer_secret,
                    'base_url': base_url,
                    'token_file': token_file
                }
            
        except Exception as cli_error:
            log.error(f"gcloud CLI also failed: {cli_error}")
        
        return None
    
    def _load_secret_manager_credential(self, secret_manager, secret_name: str) -> Optional[str]:
        """Load a specific credential from Secret Manager"""
        try:
            # Load the secret
            tokens = secret_manager.load_tokens(secret_name.replace('etrade-', '').replace('-consumer-key', '').replace('-consumer-secret', ''))
            
            if tokens and secret_name.endswith('-consumer-key'):
                return tokens.get('consumer_key')
            elif tokens and secret_name.endswith('-consumer-secret'):
                return tokens.get('consumer_secret')
            
            # Try direct credential loading
            parent = f"projects/{secret_manager.project_id}/secrets/{secret_name}/versions/latest"
            response = secret_manager.client.access_secret_version(
                request={"name": parent},
                timeout=_SECRET_MANAGER_ACCESS_TIMEOUT_SEC,
            )
            return response.payload.data.decode("UTF-8")
            
        except Exception as e:
            log.debug(f"Could not load credential {secret_name}: {e}")
            return None
    
    def _load_credentials_from_file(self):
        """Fallback method to load credentials from file"""
        try:
            # Temporarily change to ETradeOAuth directory for config loading
            original_cwd = os.getcwd()
            etrade_oauth_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ETradeOAuth')
            etrade_oauth_modules_dir = os.path.join(etrade_oauth_dir, 'modules')
            
            try:
                # Ensure ETradeOAuth/modules import path is available in local/dev contexts.
                if os.path.isdir(etrade_oauth_modules_dir) and etrade_oauth_modules_dir not in sys.path:
                    sys.path.insert(0, etrade_oauth_modules_dir)
                os.chdir(etrade_oauth_dir)
                from simple_oauth_cli import load_config
                self.config = load_config(self.environment)
            finally:
                os.chdir(original_cwd)
            
        except Exception as e:
            log.error(f"Failed to load credentials from file: {e}")
            self.config = None
    
    def _load_tokens(self):
        """Load ETrade tokens from Google Secret Manager"""
        if getattr(self, "_skip_secret_manager", False):
            self.tokens = self._oauth_tokens_dict_from_environment()
            if not self.tokens:
                raise Exception("OPTION_AUTH_SOURCE env bundle: ETRADE_ACCESS_TOKEN or ETRADE_ACCESS_TOKEN_SECRET unset")
            return
        try:
            if self._auth_source_environment:
                self.tokens = self._oauth_tokens_dict_from_environment()
                if not self.tokens:
                    raise Exception("OPTION_AUTH_SOURCE env bundle: ETRADE_ACCESS_TOKEN or ETRADE_ACCESS_TOKEN_SECRET unset")
                return

            # Try to load from Google Secret Manager first
            self.tokens = self._load_tokens_from_secret_manager()
            
            if not self.tokens:
                # Fallback to file-based loading
                log.info("Falling back to file-based token loading...")
                self._load_tokens_from_file()
            
            if not self.tokens:
                raise Exception(f"No tokens found for {self.environment}")
            
            log.info(f"✅ Loaded ETrade tokens for {self.environment}")
            
        except Exception as e:
            from .runtime_mode import is_demo_mode

            if is_demo_mode() and str(self.environment).lower() == "prod":
                log.warning(
                    "E*TRADE prod tokens unavailable in DEMO (market-data init optional): %s",
                    e,
                )
            else:
                log.error(f"Failed to load ETrade tokens: {e}")
            raise
    
    def reload_tokens(self) -> bool:
        """
        Reload OAuth tokens from Secret Manager (e.g. after user renews via web app).
        Call before critical API paths (ORB capture, validation candle) so the running
        process uses the latest tokens instead of stale in-memory credentials.
        Returns True if reload succeeded, False otherwise.
        """
        try:
            self._load_tokens()
            if self._auth_source_environment:
                log.info("✅ E*TRADE tokens reloaded from environment variables")
            else:
                log.info("✅ E*TRADE tokens reloaded from Secret Manager")
            return True
        except Exception as e:
            src = "environment variables" if self._auth_source_environment else "Secret Manager"
            log.warning(f"⚠️ Failed to reload E*TRADE tokens from {src}: {e}")
            return False
    
    def is_token_valid(self) -> bool:
        """
        Verify current token works with a single quote call (SPY). Use before ORB capture
        to decide whether to reload from Secret Manager (user may have renewed after morning alert).
        Returns True if API returns data, False if empty/error (e.g. 401).
        """
        if self.environment == 'sandbox':
            return False
        try:
            quotes = self.get_quotes(['SPY'])
            valid = bool(quotes) and len(quotes) > 0
            if not valid:
                log.warning("⚠️ E*TRADE token check failed: quote probe returned no data (token may be expired)")
            return valid
        except Exception as e:
            log.warning(f"⚠️ E*TRADE token check failed: {e}")
            return False
    
    def _load_tokens_from_secret_manager(self) -> Optional[Dict[str, Any]]:
        """Load OAuth tokens from Google Secret Manager (Rev 00180h)"""
        if getattr(self, "_skip_secret_manager", False):
            return None
        # Rev 00245: Skip Python library if USE_GCLOUD_CLI env var is set (faster for local testing)
        use_gcloud_cli = os.getenv('USE_GCLOUD_CLI', '').lower() in ('true', '1', 'yes')
        
        if not use_gcloud_cli:
            try:
                # Rev 00180h: Load tokens directly from Secret Manager (no local folder dependency)
                # Tokens managed by https://easy-trading-oauth-v2.web.app/
                if not SECRET_MANAGER_AVAILABLE:
                    log.warning("Secret Manager not available for token loading")
                    return None
                
                from .config_loader import get_cloud_config

                client = _new_secret_manager_service_client()
                # Rev 00190: Use centralized cloud config instead of hardcoded project_id
                cloud_config = get_cloud_config()
                project_id = cloud_config["project_id"]
                
                # Rev 00256: Always use production tokens. Rev 00361: newest ENABLED, not latest alias.
                from .secret_version_cleanup import access_latest_enabled_secret

                log.info(f"📥 Loading OAuth tokens from Secret Manager for {self.environment}...")
                token_data = access_latest_enabled_secret(
                    "etrade-oauth-prod",
                    project_id=project_id,
                    client=client,
                )
                tokens = json.loads(token_data)
                
                if tokens and 'oauth_token' in tokens and 'oauth_token_secret' in tokens:
                    log.info(f"✅ Loaded OAuth tokens from Secret Manager for {self.environment}")
                    log.info(f"   Token expires: {tokens.get('expires_at', 'unknown')}")
                    return tokens
                else:
                    log.warning(f"⚠️ Tokens loaded but missing required fields")
                    return None
                    
            except Exception as e:
                # FALLBACK: Use gcloud CLI if Python library has DNS issues
                log.warning(f"Secret Manager Python library failed for tokens: {e}")
                log.info("Trying gcloud CLI fallback for token access...")
        else:
            log.info("USE_GCLOUD_CLI set - using gcloud CLI directly for tokens (skipping Python library)")
        
        # Use gcloud CLI (either as fallback or if USE_GCLOUD_CLI is set)
        try:
            import subprocess
            from .config_loader import get_cloud_config
            
            # Rev 00190: Use centralized cloud config instead of hardcoded project_id
            cloud_config = get_cloud_config()
            project_id = cloud_config["project_id"]
            
            # Rev 00256 (Jan 22, 2026): CRITICAL FIX - Always use production tokens
            # Sandbox tokens are being phased out - both Demo and Live use production API
            oauth_secret = "etrade-oauth-prod"
            
            # Newest ENABLED version (``latest`` alias can point at a DESTROYED version)
            list_result = subprocess.run(
                [
                    'gcloud', 'secrets', 'versions', 'list', oauth_secret,
                    f'--project={project_id}', '--filter=state:ENABLED',
                    '--format=value(name)',
                ],
                capture_output=True, text=True, timeout=10,
            )
            enabled_versions = [
                line.strip() for line in (list_result.stdout or '').splitlines() if line.strip()
            ]
            version_id = enabled_versions[-1].rsplit('/', 1)[-1] if enabled_versions else 'latest'
            token_result = subprocess.run(
                ['gcloud', 'secrets', 'versions', 'access', version_id,
                 f'--secret={oauth_secret}', f'--project={project_id}'],
                capture_output=True, text=True, timeout=10
            )
            
            if token_result.returncode == 0:
                tokens = json.loads(token_result.stdout.strip())
                if tokens and 'oauth_token' in tokens and 'oauth_token_secret' in tokens:
                    log.info(f"✅ Loaded OAuth tokens via gcloud CLI for {self.environment}")
                    return tokens
            
        except Exception as cli_error:
            log.error(f"gcloud CLI also failed for tokens: {cli_error}")
        
        return None
    
    def _load_tokens_from_file(self):
        """Fallback method to load tokens from file"""
        try:
            # Temporarily change to ETradeOAuth directory for token loading
            original_cwd = os.getcwd()
            etrade_oauth_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ETradeOAuth')
            etrade_oauth_modules_dir = os.path.join(etrade_oauth_dir, 'modules')
            
            try:
                # Ensure ETradeOAuth/modules import path is available in local/dev contexts.
                if os.path.isdir(etrade_oauth_modules_dir) and etrade_oauth_modules_dir not in sys.path:
                    sys.path.insert(0, etrade_oauth_modules_dir)
                os.chdir(etrade_oauth_dir)
                from simple_oauth_cli import load_tokens
                self.tokens = load_tokens(self.environment)
            finally:
                os.chdir(original_cwd)
            
        except Exception as e:
            log.error(f"Failed to load tokens from file: {e}")
            self.tokens = None
    
    def _load_accounts(self):
        """Load and parse account list"""
        try:
            log.info("📋 Loading ETrade accounts...")
            
            response = self._make_etrade_api_call(
                method='GET',
                url="/v1/accounts/list",
                params={}
            )
            
            # Handle different response formats
            if isinstance(response, dict):
                # JSON response from new OAuth implementation
                if 'AccountListResponse' in response:
                    self._parse_account_list_json(response)
                elif 'Accounts' in response:
                    self._parse_account_list_json(response)
                elif 'error' in response:
                    log.error(f"API error: {response['error']}")
                    raise Exception(f"API error: {response['error']}")
                else:
                    log.error(f"Unexpected JSON response format: {response}")
                    raise Exception("Unexpected JSON response format")
            elif isinstance(response, str):
                # XML response (legacy)
                if '<?xml' in response:
                    self._parse_account_list_xml(response)
                else:
                    log.error(f"Unexpected string response: {response[:200]}...")
                    raise Exception("Unexpected string response format")
            else:
                log.error(f"Unexpected response type: {type(response)}")
                raise Exception(f"Unexpected response type: {type(response)}")
                
        except Exception as e:
            log.error(f"Failed to load accounts: {e}")
            raise
    
    def _parse_account_list_json(self, response_data: dict):
        """Parse account list JSON response"""
        try:
            # Handle both response formats
            if 'AccountListResponse' in response_data:
                accounts_data = response_data['AccountListResponse']['Accounts']['Account']
            elif 'Accounts' in response_data:
                accounts_data = response_data['Accounts']['Account']
            else:
                raise Exception("No accounts data found in response")
            
            if not isinstance(accounts_data, list):
                accounts_data = [accounts_data]
            
            for account_data in accounts_data:
                account = ETradeAccount(
                    account_id=self._normalize_account_ref(account_data.get('accountId')),
                    account_name=account_data.get('accountName'),
                    account_id_key=self._normalize_account_ref(account_data.get('accountIdKey')),
                    account_status=account_data.get('accountStatus'),
                    institution_type=account_data.get('institutionType'),
                    account_type=account_data.get('accountType')
                )
                self.accounts.append(account)
                log.info(f"✅ Loaded account: {account.account_name} ({account.account_id})")

            if not self.selected_account:
                self._resolve_accounts_for_mode()

            log.info(f"✅ Loaded {len(self.accounts)} accounts")
            
        except Exception as e:
            log.error(f"Failed to parse account list JSON: {e}")
            raise
    
    def _parse_account_list_xml(self, xml_data: str):
        """Parse account list XML response"""
        try:
            # Clean up the XML data
            xml_data = xml_data.strip()
            
            if xml_data.startswith('"1.0" encoding="UTF-8" standalone="yes"?>'):
                xml_data = '<?xml version=' + xml_data
            elif not xml_data.startswith('<?xml version'):
                xml_data = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + xml_data
            
            root = ET.fromstring(xml_data)
            
            self.accounts = []
            for account in root.findall('.//Account'):
                account_data = ETradeAccount(
                    account_id=account.find('accountId').text if account.find('accountId') is not None else None,
                    account_name=account.find('accountName').text if account.find('accountName') is not None else None,
                    account_id_key=account.find('accountIdKey').text if account.find('accountIdKey') is not None else None,
                    account_status=account.find('accountStatus').text if account.find('accountStatus') is not None else None,
                    institution_type=account.find('institutionType').text if account.find('institutionType') is not None else None,
                    account_type=account.find('accountType').text if account.find('accountType') is not None else None,
                )
                self.accounts.append(account_data)
            
            active_accounts = [acc for acc in self.accounts if acc.account_status == 'ACTIVE']
            self._resolve_accounts_for_mode()
            log.info(f"✅ Loaded {len(self.accounts)} accounts ({len(active_accounts)} active)")
            
        except Exception as e:
            log.error(f"Failed to parse account list XML: {e}")
            raise
    

    def _configured_0dte_account_refs(self) -> tuple:
        """Return (account_id, account_id_key) from config/env — identity only."""
        import os
        try:
            from .config_loader import get_config_value
            aid = get_config_value("ETRADE_0DTE_ACCOUNT_ID", "") or ""
            akey = get_config_value("ETRADE_0DTE_ACCOUNT_ID_KEY", "") or ""
        except Exception:
            aid = ""
            akey = ""
        aid = self._normalize_account_ref(aid or os.getenv("ETRADE_0DTE_ACCOUNT_ID", ""))
        akey = self._normalize_account_ref(akey or os.getenv("ETRADE_0DTE_ACCOUNT_ID_KEY", ""))
        return aid, akey

    def _find_account_by_configured_identity(self) -> Optional[ETradeAccount]:
        aid, akey = self._configured_0dte_account_refs()
        if not aid and not akey:
            return None
        for acc in self.accounts:
            if str(acc.account_status or "").upper() != "ACTIVE":
                continue
            if aid and self._account_ref_matches(acc, aid):
                return acc
            if akey and self._account_ref_matches(acc, akey):
                return acc
        return None

    def select_market_data_account(self) -> bool:
        """Resolve production market-data account. Demo trading ledger remains mock."""
        from .runtime_mode import is_demo_mode

        configured = self._find_account_by_configured_identity()
        active = [a for a in self.accounts if str(a.account_status or "").upper() == "ACTIVE"]
        non_sim = [a for a in active if not self._is_sim_account(a)]
        chosen = configured
        if chosen is None:
            # Never prefer SIM/PAPER for Demo market data.
            chosen = non_sim[0] if non_sim else None
        if chosen is None:
            log.error(
                "MARKET_DATA_ACCOUNT_UNRESOLVED | active=%d | configured_missing=true",
                len(active),
            )
            self.resolved_market_data_account = None
            self.resolved_trading_account = None
            self.selected_account = None
            return False
        if self._is_sim_account(chosen):
            log.warning(
                "MARKET_DATA_ACCOUNT_SIM_WARNING | account=%s | id=%s | "
                "demo_should_use_production_market_data",
                chosen.account_name,
                chosen.account_id,
            )
        self.resolved_market_data_account = chosen
        self.selected_account = chosen
        if is_demo_mode():
            self.resolved_trading_account = None
            log.info(
                "DEMO_ACCOUNT_POLICY | resolved_market_data_account=%s (%s) | "
                "resolved_trading_account=absent | ledger=mock",
                chosen.account_name,
                chosen.account_id,
            )
        else:
            log.info(
                "MARKET_DATA_ACCOUNT | resolved=%s (%s)",
                chosen.account_name,
                chosen.account_id,
            )
        return True

    def select_live_trading_account(self) -> bool:
        """Live Mode: exact configured account identity; fail closed — no name/first-active."""
        from .runtime_mode import is_live_mode

        if not is_live_mode():
            return self.select_market_data_account()

        aid, akey = self._configured_0dte_account_refs()
        if not aid and not akey:
            log.error(
                "LIVE_ACCOUNT_FAIL_CLOSED | reason=missing_ETRADE_0DTE_ACCOUNT_ID"
            )
            self.resolved_trading_account = None
            self.resolved_market_data_account = None
            self.selected_account = None
            return False

        configured = self._find_account_by_configured_identity()
        if configured is None:
            log.error(
                "LIVE_ACCOUNT_FAIL_CLOSED | reason=configured_account_not_found | "
                "account_id=%s | account_id_key=%s | active_ids=%s",
                aid,
                akey,
                [a.account_id for a in self.accounts if str(a.account_status or "").upper() == "ACTIVE"][:8],
            )
            self.resolved_trading_account = None
            self.resolved_market_data_account = None
            self.selected_account = None
            return False
        if self._is_sim_account(configured):
            log.error(
                "LIVE_ACCOUNT_FAIL_CLOSED | reason=configured_account_is_sim | "
                "account=%s | id=%s",
                configured.account_name,
                configured.account_id,
            )
            self.resolved_trading_account = None
            self.resolved_market_data_account = None
            self.selected_account = None
            return False

        self.resolved_trading_account = configured
        self.resolved_market_data_account = configured
        self.selected_account = configured
        log.info(
            "LIVE_ACCOUNT_RESOLVED | trading=%s (%s) | market_data=same",
            configured.account_name,
            configured.account_id,
        )
        return True

    def _resolve_accounts_for_mode(self) -> None:
        from .runtime_mode import is_live_mode

        if is_live_mode():
            self.select_live_trading_account()
        else:
            self.select_market_data_account()

    def _is_sim_account(self, account: ETradeAccount) -> bool:
        """
        Determine if an account is a sim/paper trading account
        
        Rev 00256: Identifies sim accounts by checking account name patterns
        Common patterns: 'SIM', 'PAPER', 'TEST', 'DEMO', 'SANDBOX'
        """
        if not account.account_name:
            return False
        
        account_name_upper = account.account_name.upper()
        sim_keywords = ['SIM', 'PAPER', 'TEST', 'DEMO', 'SANDBOX', 'VIRTUAL']
        return any(keyword in account_name_upper for keyword in sim_keywords)
    
    @staticmethod
    def _normalize_account_ref(account_ref: Any) -> str:
        return str(account_ref or "").strip()

    def _account_ref_matches(self, account: ETradeAccount, account_ref: Any) -> bool:
        """Match numeric accountId or opaque accountIdKey (E*TRADE API)."""
        target = self._normalize_account_ref(account_ref)
        if not target:
            return False
        for field in (account.account_id, account.account_id_key):
            if field is not None and self._normalize_account_ref(field) == target:
                return True
        return False

    def select_account(self, account_id: str) -> bool:
        """Select a specific account for trading"""
        target = self._normalize_account_ref(account_id)
        if not target:
            return False
        if (
            self.selected_account
            and self.selected_account.account_status == 'ACTIVE'
            and self._account_ref_matches(self.selected_account, target)
        ):
            return True
        from .runtime_mode import is_demo_mode, is_live_mode

        for account in self.accounts:
            if self._account_ref_matches(account, target) and account.account_status == 'ACTIVE':
                # Rev 00256: Verify account type matches trading mode
                is_sim = self._is_sim_account(account)

                if is_demo_mode() and is_sim:
                    log.warning(
                        "DEMO_ACCOUNT_POLICY | sim_account_for_market_data_only | "
                        "account_id=%s | account_name=%s | trading_ledger=mock",
                        target,
                        account.account_name,
                    )
                elif is_live_mode() and is_sim:
                    log.error(f"❌ Live Mode: Selected account {account.account_name} appears to be a sim account!")
                    log.error(f"   This is a safety violation - Live mode should not use sim accounts")
                    return False
                
                self.selected_account = account
                log.info(f"✅ Selected account: {account.account_name} ({account.account_id})")
                return True
        active_ids = [acc.account_id for acc in self.accounts if acc.account_status == 'ACTIVE']
        first_warn = target not in self._warned_missing_account_ids
        if first_warn:
            self._warned_missing_account_ids.add(target)
        if is_demo_mode():
            # Demo: missing Live account IDs are expected — keep quiet (debug only).
            log.debug(
                "DEMO_ACCOUNT_POLICY | account_id_not_selected | account_id=%s | "
                "reason=demo_mock_ledger | active_ids=%s",
                target,
                active_ids[:5],
            )
            return False
        log_fn = log.warning if first_warn else log.debug
        if self.selected_account and self.selected_account.account_status == 'ACTIVE':
            log_fn(
                f"⚠️ Account {target} not found or not active; "
                f"keeping currently selected account {self.selected_account.account_id}"
            )
        else:
            log_fn(
                f"⚠️ Account {target} not found or not active. "
                f"Active account IDs: {active_ids[:5]}{'...' if len(active_ids) > 5 else ''}"
            )
        return False
    
    def get_account_balance(self) -> ETradeBalance:
        """Get account balance information using correct OAuth 1.0a"""
        if not self.selected_account:
            raise Exception("No account selected")
        
        try:
            log.info(f"💰 Fetching balance for account {self.selected_account.account_id}...")
            
            # Use correct OAuth implementation with required query parameters
            params = {'instType': 'BROKERAGE', 'realTimeNAV': 'true'}
            url = f"/v1/accounts/{self.selected_account.account_id_key}/balance"
            
            response = self._make_etrade_api_call(
                method='GET',
                url=url,
                params=params
            )
            
            if response and not isinstance(response, dict) or 'error' not in response:
                self.balance = self._parse_balance_response(response)
                log.info(f"✅ Retrieved balance: Cash Available for Investment: ${self.balance.cash_available_for_investment}")
                return self.balance
            else:
                log.warning(f"Balance API failed: {response}")
                
                # Fallback: Try to get balance from portfolio
                log.info("🔄 Attempting to get balance from portfolio data...")
                portfolio = self.get_portfolio()
                if portfolio:
                    # Calculate balance from portfolio positions
                    total_value = sum(pos.market_value for pos in portfolio if pos.market_value)
                    cash_positions = [pos for pos in portfolio 
                                    if 'CASH' in pos.symbol.upper() or 'USD' in pos.symbol.upper()]
                    cash_available = sum(pos.market_value for pos in cash_positions if pos.market_value) if cash_positions else 0.0
                    
                    self.balance = ETradeBalance(
                        account_value=total_value,
                        cash_available_for_investment=cash_available,
                        cash_buying_power=cash_available,
                        option_level=None
                    )
                    log.info(f"✅ Retrieved balance from portfolio: Cash Available for Investment: ${self.balance.cash_available_for_investment}")
                    return self.balance
            
            # If all methods fail, return empty balance
            self.balance = ETradeBalance(
                account_value=None,
                cash_available_for_investment=None,
                cash_buying_power=None,
                option_level=None
            )
            return self.balance
            
        except Exception as e:
            log.error(f"Failed to get account balance: {e}")
            raise
    
    def _parse_balance_response(self, response) -> ETradeBalance:
        """Parse balance response (JSON or XML)"""
        try:
            # Handle JSON response first (preferred)
            if isinstance(response, dict):
                if 'BalanceResponse' in response:
                    # JSON response structure
                    balance_response = response['BalanceResponse']
                    computed = balance_response.get('Computed', {}) or balance_response.get('ComputedBalance', {})
                    
                    def safe_float(value):
                        try:
                            return float(value) if value else None
                        except (ValueError, TypeError):
                            return None
                    
                    # optionLevel is on BalanceResponse (e.g. "LEVEL_2"), not always in Computed
                    option_level = balance_response.get('optionLevel') or computed.get('optionLevel')
                    account_value = safe_float(
                        computed.get('totalAccountValue')
                        or (computed.get('RealTimeValues') or {}).get('totalAccountValue')
                    )
                    return ETradeBalance(
                        account_value=account_value,
                        cash_available_for_investment=safe_float(computed.get('cashAvailableForInvestment')),
                        cash_buying_power=safe_float(computed.get('cashBuyingPower')),
                        option_level=option_level,
                    )
            
            # Handle XML response (legacy)
            if isinstance(response, str) and '<?xml' in response:
                xml_data = response.strip()
                
                # Clean up XML data
                if xml_data.startswith('"1.0" encoding="UTF-8" standalone="yes"?>'):
                    xml_data = '<?xml version=' + xml_data
                elif not xml_data.startswith('<?xml version'):
                    xml_data = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + xml_data
                
                root = ET.fromstring(xml_data)
                
                # Extract balance information
                balance_data = {}
                account_balance = root.find('.//AccountBalance')
                if account_balance is not None:
                    for child in account_balance:
                        balance_data[child.tag] = child.text
                
                # Map to ETradeBalance
                def safe_float(value):
                    try:
                        return float(value) if value else None
                    except (ValueError, TypeError):
                        return None
                
                return ETradeBalance(
                    account_value=safe_float(balance_data.get('accountValue', balance_data.get('accountBalance'))),
                    cash_available_for_investment=safe_float(balance_data.get('cashAvailableForInvestment')),
                    cash_buying_power=safe_float(balance_data.get('cashBuyingPower', balance_data.get('buyingPower'))),
                    option_level=balance_data.get('optionLevel')
                )
            
            return ETradeBalance(None, None, None, None)
            
        except Exception as e:
            log.error(f"Failed to parse balance response: {e}")
            return ETradeBalance(None, None, None, None)
    
    def get_portfolio(self) -> List[ETradePosition]:
        """Get portfolio positions.

        A failed fetch retries once. If that also fails, the previous book is
        kept. An empty response is a failed fetch, not an empty account.
        Prefer ``get_positions()`` for Live sync — that path raises
        ``broker_positions_unavailable`` instead of returning a book.
        """
        if not self.selected_account:
            raise Exception("No account selected")

        previous = list(self.portfolio or [])
        last_error: Any = "empty_response"
        url = f"{self.config['base_url']}/v1/accounts/{self.selected_account.account_id_key}/portfolio"
        for attempt in (1, 2):
            try:
                log.info(
                    "📊 Fetching portfolio for account %s | attempt=%s",
                    self.selected_account.account_id,
                    attempt,
                )
                response = self._make_etrade_api_call(
                    method='GET',
                    url=url,
                    params={}
                )
                if response:
                    self.portfolio = self._parse_portfolio_response(response)
                    self._last_portfolio_fetch_ok = True
                    log.info("✅ Retrieved %s portfolio positions", len(self.portfolio))
                    return self.portfolio
                last_error = "empty_response"
                log.warning(
                    "Failed to get portfolio: empty_response | attempt=%s",
                    attempt,
                )
            except Exception as e:
                last_error = e
                log.warning("Failed to get portfolio: %s | attempt=%s", e, attempt)
            if attempt == 1:
                time.sleep(0.25)

        self._last_portfolio_fetch_ok = False
        self.portfolio = previous
        log.error(
            "Failed to get portfolio: %s | action=keep_last_book | positions=%s",
            last_error,
            len(previous),
        )
        return self.portfolio

    def get_positions(self) -> List[Dict[str, Any]]:
        """Live sync contract: normalized rows, raise on fetch failure (never silent [])."""
        from modules.broker_adapter import (
            BROKER_POSITIONS_UNAVAILABLE,
            normalize_broker_position_rows,
        )

        if not self.selected_account:
            raise RuntimeError(f"{BROKER_POSITIONS_UNAVAILABLE}:no_account")

        try:
            log.info(
                "BROKER_POSITIONS | fetching portfolio for account %s",
                getattr(self.selected_account, "account_id", ""),
            )
            response = self._make_etrade_api_call(
                method="GET",
                url=f"{self.config['base_url']}/v1/accounts/{self.selected_account.account_id_key}/portfolio",
                params={},
            )
        except Exception as err:
            raise RuntimeError(f"{BROKER_POSITIONS_UNAVAILABLE}:{err}") from err

        if response is None:
            raise RuntimeError(f"{BROKER_POSITIONS_UNAVAILABLE}:empty_response")

        try:
            self.portfolio = self._parse_portfolio_response(response)
            self._last_portfolio_fetch_ok = True
        except Exception as err:
            self._last_portfolio_fetch_ok = False
            raise RuntimeError(f"{BROKER_POSITIONS_UNAVAILABLE}:parse_failed:{err}") from err

        rows = normalize_broker_position_rows(self.portfolio)
        log.info("BROKER_POSITIONS | retrieved=%d | normalized=%d", len(self.portfolio), len(rows))
        return rows
    
    def _parse_portfolio_response(self, response: Dict) -> List[ETradePosition]:
        """Parse portfolio response"""
        try:
            xml_key = '<?xml version'
            if xml_key in response:
                xml_data = response[xml_key]
                
                # Clean up XML data
                xml_data = xml_data.strip()
                if xml_data.startswith('"1.0" encoding="UTF-8" standalone="yes"?>'):
                    xml_data = '<?xml version=' + xml_data
                elif not xml_data.startswith('<?xml version'):
                    xml_data = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + xml_data
                
                root = ET.fromstring(xml_data)
                
                positions = []
                for position in root.findall('.//Position'):
                    pos_data: Dict[str, Any] = {}
                    product: Dict[str, Any] = {}
                    for child in position:
                        tag = str(child.tag or "")
                        if tag == "Product":
                            for pchild in child:
                                product[str(pchild.tag)] = pchild.text
                            # Also capture attributes if present.
                            for ak, av in (child.attrib or {}).items():
                                product.setdefault(str(ak), av)
                            continue
                        pos_data[tag] = child.text
                    
                    # Map to ETradePosition
                    def safe_float(value):
                        try:
                            return float(value) if value else 0.0
                        except (ValueError, TypeError):
                            return 0.0
                    
                    def safe_int(value):
                        try:
                            return int(value) if value else 0
                        except (ValueError, TypeError):
                            return 0

                    qty = safe_int(pos_data.get("quantity"))
                    total_cost = safe_float(pos_data.get("totalCost"))
                    avg_price = safe_float(
                        pos_data.get("pricePaid")
                        or pos_data.get("costPerShare")
                        or pos_data.get("averagePrice")
                    )
                    if avg_price <= 0 and qty > 0 and total_cost:
                        avg_price = abs(total_cost) / abs(qty)

                    # Prefer Product.symbol (underlier) + structured identity for OCC later;
                    # keep description as fallback display symbol.
                    underlier = str(product.get("symbol") or "").strip()
                    desc = str(pos_data.get("symbolDescription") or "").strip()
                    osi_key = str(
                        product.get("osiKey")
                        or pos_data.get("osiKey")
                        or ""
                    ).strip()
                    display_symbol = underlier or desc

                    # When Product is complete, stamp spaced OCC into symbol for sync.
                    if product:
                        try:
                            from modules.option_contract_identity import (
                                identity_from_product,
                                occ_from_identity,
                            )

                            ident = identity_from_product(product, osi_key=osi_key)
                            if ident is not None:
                                display_symbol = occ_from_identity(ident)
                        except Exception:
                            pass
                    
                    positions.append(ETradePosition(
                        position_id=pos_data.get('positionId', ''),
                        symbol=display_symbol,
                        symbol_description=desc,
                        quantity=qty,
                        position_type=pos_data.get('positionType', ''),
                        market_value=safe_float(pos_data.get('marketValue')),
                        total_cost=total_cost,
                        total_gain=safe_float(pos_data.get('totalGain')),
                        total_gain_pct=safe_float(pos_data.get('totalGainPct')),
                        days_gain=safe_float(pos_data.get('daysGain')),
                        days_gain_pct=safe_float(pos_data.get('daysGainPct')),
                        product=product or None,
                        osi_key=osi_key,
                        avg_price=avg_price,
                    ))
                
                return positions
            
            return []
            
        except Exception as e:
            log.error(f"Failed to parse portfolio response: {e}")
            raise

    def get_quotes(
        self,
        symbols: List[str],
        fail_fast: bool = False,
        timeout_sec: Optional[float] = None,
    ) -> List[ETradeQuote]:
        """
        Get market quotes for symbols

        Rev 00247 (Jan 20, 2026): CRITICAL FIX - Enforce 25 symbol limit per API call
        ETrade API has a hard limit of 25 symbols per request (error 1023 if exceeded)

        fail_fast: monitor-critical path — one attempt, no 1s/2s/4s sleep retry.
        timeout_sec: HTTP timeout override (monitor uses 0DTE_EXIT_QUOTE_TIMEOUT_SEC).
        """
        try:
            if not symbols:
                return []
            
            # Rev 00252 (Jan 21, 2026): CRITICAL FIX - ALWAYS use production API for market data
            # Sandbox API only returns 4 test symbols, production returns full market data
            # This ensures complete symbol collection for ORB and 0DTE strategies
            # Trading operations can still use sandbox (Demo mode), but market data always uses production
            # Rev 00247: Enforce 25 symbols per request (error 1023 if exceeded without override).
            from modules.broker_api_pacing import etrade_batch_quote_size, pace_market_api_after_request

            MAX_SYMBOLS_PER_REQUEST = etrade_batch_quote_size()
            all_quotes = []
            call_timeout_sec = None
            call_max_attempts = None
            if fail_fast:
                http_timeout = float(timeout_sec) if timeout_sec and float(timeout_sec) > 0 else 1.5
                # HTTP must finish before asyncio.wait_for so the executor cannot outlive the monitor.
                # Pass per-call; do not mutate instance attrs (concurrent quotes race).
                call_timeout_sec = max(0.2, http_timeout * 0.85)
                call_max_attempts = 1
            
            # Rev 00255 (Jan 22, 2026): CRITICAL FIX - Remove credential switching
            # OptimizedETradeDataProvider already initializes with production environment
            # No need to switch credentials - this was causing 401 errors with invalid tokens
            # If this instance is sandbox, it should NOT be used for market data
            # Market data should use a separate production instance (via OptimizedETradeDataProvider)
            if self.environment == 'sandbox':
                log.error(f"❌ CRITICAL: get_quotes() called on sandbox instance!")
                log.error(f"   Market data should use production instance via OptimizedETradeDataProvider")
                log.error(f"   This instance should only be used for trading operations in Demo mode")
                log.error(f"   Returning empty quotes to prevent invalid API calls")
                return []
            
            # Rev 00251: Track which symbols we've successfully retrieved
            retrieved_symbols_set = set()
            failed_batches = []  # Track batches that failed completely for retry
            
            # Split into batches
            for i in range(0, len(symbols), MAX_SYMBOLS_PER_REQUEST):
                batch = symbols[i:i+MAX_SYMBOLS_PER_REQUEST]
                batch_num = i//MAX_SYMBOLS_PER_REQUEST + 1
                total_batches = (len(symbols) + MAX_SYMBOLS_PER_REQUEST - 1) // MAX_SYMBOLS_PER_REQUEST
                
                log.info(f"📈 Fetching quotes for batch {batch_num}/{total_batches} ({len(batch)} symbols: {batch[:5]}...)")
                
                # ETrade API accepts comma-separated symbols.
                # Always URL-encode each symbol: OCC spaces and option colon-forms both need it.
                from urllib.parse import quote as _url_quote

                symbol_param = ",".join(_url_quote(str(s).strip(), safe=":") for s in batch)
                quote_url = f"{self.config['base_url']}/v1/market/quote/{symbol_param}"
                
                # Rev 00249: Log the exact URL being called for debugging
                log.info(f"📞 Calling ETrade API: {quote_url}")
                log.info(f"   Symbols requested: {batch}")
                
                try:
                    # Rev 00251 (Jan 21, 2026): CRITICAL FIX - Ensure we get ALL quotes
                    # Add retry logic with exponential backoff for rate limiting
                    max_retries = 1 if fail_fast else 3
                    retry_delay = 1  # Start with 1 second
                    batch_quotes = []
                    response = None
                    
                    for retry_attempt in range(max_retries):
                        # Rev 00249: Add overrideSymbolCount to allow up to 50 symbols per request
                        # ETrade API default is 25, but can be increased to 50 with this parameter
                        try:
                            response = self._make_etrade_api_call(
                                method='GET',
                                url=quote_url,
                                params={
                                    'detailFlag': 'ALL',
                                },
                                timeout_sec=call_timeout_sec,
                                max_attempts=call_max_attempts,
                            )
                        except Exception as api_error:
                            log.warning(
                                "GET_QUOTE_RETRY | symbol=%s | lane=market_quote | attempt=%d | "
                                "sleep_s=%s | wall_ms=0 | error=%s",
                                ",".join(str(s) for s in batch[:5]),
                                retry_attempt + 1,
                                0 if fail_fast else retry_delay,
                                api_error,
                            )
                            if retry_attempt < max_retries - 1 and not fail_fast:
                                time.sleep(retry_delay)
                                retry_delay *= 2
                                continue
                            else:
                                log.error(f"❌ Failed to get response after {max_retries} attempts")
                                break
                        
                        # Check if we got a valid response
                        if not response:
                            log.warning(f"⚠️ Empty response (attempt {retry_attempt + 1}/{max_retries})")
                            if retry_attempt < max_retries - 1 and not fail_fast:
                                time.sleep(retry_delay)
                                retry_delay *= 2
                                continue
                            else:
                                log.error(f"❌ No response after {max_retries} attempts for batch {batch[:5]}...")
                                break
                        
                        # Check for rate limiting or market data agreement errors
                        if 'QuoteResponse' in response:
                            quote_resp = response['QuoteResponse']
                            if 'Messages' in quote_resp:
                                messages = quote_resp['Messages']
                                if isinstance(messages, dict) and 'Message' in messages:
                                    msg_list = messages['Message'] if isinstance(messages['Message'], list) else [messages['Message']]
                                    should_retry = False
                                    for msg in msg_list:
                                        if isinstance(msg, dict):
                                            error_code = msg.get('code', 0)
                                            error_desc = msg.get('description', '')
                                            # Check for rate limiting (429) or market data agreement errors
                                            if error_code == 429 or 'rate limit' in error_desc.lower():
                                                should_retry = True
                                                if retry_attempt < max_retries - 1 and not fail_fast:
                                                    log.warning(
                                                        "GET_QUOTE_RETRY | symbol=%s | lane=market_quote | attempt=%d | "
                                                        "sleep_s=%s | wall_ms=0 | error=rate_limit",
                                                        ",".join(str(s) for s in batch[:5]),
                                                        retry_attempt + 1,
                                                        retry_delay,
                                                    )
                                                    time.sleep(retry_delay)
                                                    retry_delay *= 2  # Exponential backoff
                                                    break
                                                should_retry = False
                                            elif 'market data' in error_desc.lower() or 'agreement' in error_desc.lower():
                                                log.error(f"❌ Market data agreement required! Please accept market data agreements on ETrade website.")
                                                log.error(f"   Visit: https://us.etrade.com > Settings > Market Data Agreements")
                                                # Don't retry for agreement errors, but still try to parse what we got
                                                break
                                    
                                    if should_retry:
                                        continue
                        
                        # If we got here, we have a response (even if with some errors)
                        # Parse it and extract all valid quotes
                        log.debug(f"📊 E*TRADE response received for batch of {len(batch)} symbols")
                        
                        # Rev 00249: Log full response structure for debugging
                        if 'QuoteResponse' in response:
                            quote_resp = response['QuoteResponse']
                            log.info(f"📊 QuoteResponse structure: {list(quote_resp.keys())}")
                            if 'Messages' in quote_resp:
                                log.warning(f"⚠️ ETrade API Messages: {quote_resp['Messages']}")
                        
                        # Parse quotes - this will extract ALL valid quotes from the response
                        batch_quotes = self._parse_quotes_response(response)
                        
                        # Success - we got quotes (even if fewer than requested due to invalid symbols)
                        break
                    
                    # Process the quotes we got (even if response had errors or was partial)
                    if batch_quotes:
                        if len(batch_quotes) < len(batch):
                            # Some symbols were invalid - that's OK, we got what we could
                            missing_count = len(batch) - len(batch_quotes)
                            retrieved_symbols = [q.symbol for q in batch_quotes]
                            missing_symbols = [s for s in batch if s not in retrieved_symbols]
                            log.info(f"✅ Retrieved {len(batch_quotes)}/{len(batch)} quotes for batch (missing {missing_count} invalid symbols)")
                            if missing_symbols:
                                log.debug(f"   Missing symbols: {missing_symbols[:10]}{'...' if len(missing_symbols) > 10 else ''}")
                        else:
                            log.info(f"✅ Retrieved {len(batch_quotes)} quotes from batch (all symbols valid)")
                        
                        # Track retrieved symbols and add quotes
                        for quote in batch_quotes:
                            retrieved_symbols_set.add(quote.symbol)
                        all_quotes.extend(batch_quotes)
                    elif response:
                        # We got a response but parsing returned 0 quotes - log for debugging
                        log.warning(f"⚠️ E*TRADE parsing returned 0 quotes for batch: {batch[:5]}...")
                        log.warning(f"📊 Response structure: {list(response.keys()) if isinstance(response, dict) else type(response)}")
                        if 'QuoteResponse' in response:
                            quote_resp = response['QuoteResponse']
                            log.warning(f"📊 QuoteResponse keys: {list(quote_resp.keys()) if isinstance(quote_resp, dict) else 'not a dict'}")
                            if 'QuoteData' in quote_resp:
                                quote_data = quote_resp['QuoteData']
                                log.warning(f"📊 QuoteData type: {type(quote_data)}, length: {len(quote_data) if isinstance(quote_data, (list, tuple)) else 'N/A'}")
                        # Mark this batch as failed for potential retry
                        failed_batches.append((batch, batch_num))
                    else:
                        # No response at all - this batch failed completely
                        log.error(f"❌ Failed to get any response for batch {batch[:5]}... after {max_retries} attempts")
                        # Mark this batch as failed for potential retry
                        failed_batches.append((batch, batch_num))
                    
                    if i + MAX_SYMBOLS_PER_REQUEST < len(symbols):
                        pace_market_api_after_request(reason="batch_quotes")
                        
                except Exception as batch_error:
                    log.error(f"❌ Failed to fetch quotes for batch {batch[:5]}...: {batch_error}", exc_info=True)
                    # Mark this batch as failed for potential retry
                    failed_batches.append((batch, batch_num))
                    # Continue with next batch instead of failing completely
                    continue
            
            # Rev 00251: Retry failed batches with smaller batch sizes
            if failed_batches:
                log.warning(f"⚠️ {len(failed_batches)} batches failed completely. Retrying with smaller batch size...")
                # Retry failed batches with smaller size (25 symbols instead of 50)
                for failed_batch, batch_num in failed_batches:
                    # Filter out symbols we already retrieved
                    remaining_symbols = [s for s in failed_batch if s not in retrieved_symbols_set]
                    if not remaining_symbols:
                        log.debug(f"   All symbols from batch {batch_num} already retrieved, skipping retry")
                        continue
                    
                    log.info(f"🔄 Retrying batch {batch_num} with {len(remaining_symbols)} remaining symbols: {remaining_symbols[:5]}...")
                    # Use smaller batch size for retry
                    retry_batch_size = min(25, len(remaining_symbols))
                    for j in range(0, len(remaining_symbols), retry_batch_size):
                        retry_batch = remaining_symbols[j:j+retry_batch_size]
                        symbol_param = ','.join(retry_batch)
                        quote_url = f"{self.config['base_url']}/v1/market/quote/{symbol_param}"
                        
                        try:
                            response = self._make_etrade_api_call(
                                method='GET',
                                url=quote_url,
                                params={
                                    'detailFlag': 'ALL',
                                    'overrideSymbolCount': 'true'
                                },
                                timeout_sec=call_timeout_sec,
                                max_attempts=call_max_attempts,
                            )
                            
                            if response:
                                retry_quotes = self._parse_quotes_response(response)
                                if retry_quotes:
                                    log.info(f"✅ Retry successful: Retrieved {len(retry_quotes)} quotes")
                                    for quote in retry_quotes:
                                        retrieved_symbols_set.add(quote.symbol)
                                    all_quotes.extend(retry_quotes)
                                else:
                                    log.warning(f"⚠️ Retry returned 0 quotes for {retry_batch[:5]}...")
                            else:
                                log.warning(f"⚠️ Retry returned no response for {retry_batch[:5]}...")
                            
                            time.sleep(0.2)  # Longer delay for retries
                        except Exception as retry_error:
                            log.error(f"❌ Retry failed for {retry_batch[:5]}...: {retry_error}")
            
            # Rev 00255: Credential switching removed - no restoration needed
            
            if all_quotes:
                # Option Market Quote returns Product.symbol = underlier only.
                # Colon requests (CRM:2026:7:31:CALL:185) never equal quote.symbol —
                # match structured Product identity before calling them "missing".
                try:
                    from modules.exact_contract_option_quotes import identity_from_quote_object
                    from modules.option_contract_identity import (
                        identities_equal,
                        identity_from_any_option_symbol,
                    )

                    quote_idents = []
                    for q in all_quotes:
                        ident = identity_from_quote_object(q)
                        if ident is not None:
                            quote_idents.append(ident)

                    def _request_matched(req: str) -> bool:
                        if req in retrieved_symbols_set:
                            return True
                        req_ident = identity_from_any_option_symbol(req)
                        if req_ident is None:
                            return False
                        return any(identities_equal(req_ident, qi) for qi in quote_idents)

                    matched_count = sum(1 for s in symbols if _request_matched(s))
                    missing_symbols = [s for s in symbols if not _request_matched(s)]
                except Exception:
                    matched_count = len(all_quotes)
                    missing_symbols = [s for s in symbols if s not in retrieved_symbols_set]

                success_rate = (matched_count / len(symbols) * 100) if symbols else 0.0
                
                log.info(f"✅ Retrieved {len(all_quotes)} total quotes from {len(symbols)} symbols (batched) - {success_rate:.1f}% success rate")
                
                if missing_symbols:
                    log.warning(f"⚠️ Missing {len(missing_symbols)} symbols: {missing_symbols[:20]}{'...' if len(missing_symbols) > 20 else ''}")
                    log.warning(f"   These symbols may be invalid or unavailable in ETrade")
                
                if success_rate < 90:
                    log.warning(f"⚠️ Lower than expected success rate ({success_rate:.1f}%)")
                    log.warning(f"   Possible causes:")
                    log.warning(f"   1. Some symbols may be invalid (see missing symbols above)")
                    log.warning(f"   2. Market data agreements may need to be accepted")
                    log.warning(f"   3. Rate limiting may have occurred")
                    log.warning(f"   Visit: https://us.etrade.com > Settings > Market Data Agreements")
                else:
                    log.info(f"✅ Success rate {success_rate:.1f}% - All valid symbols retrieved!")
            else:
                log.warning(f"⚠️ No quotes retrieved for {len(symbols)} symbols")
                log.warning(f"   Check:")
                log.warning(f"   1. Market data agreements are accepted")
                log.warning(f"   2. API keys have proper permissions")
                log.warning(f"   3. Network connectivity to ETrade API")
                    
            return all_quotes
                
        except Exception as e:
            log.error(f"Failed to get quotes: {e}")
            return []

    def get_option_contract_quotes(
        self,
        symbols: List[str],
        fail_fast: bool = False,
        timeout_sec: Optional[float] = None,
    ) -> List[ETradeQuote]:
        """Exact-contract quotes via Market Quote API.

        Converts spaced OCC (``SLV 260720C00051500``) to E*TRADE quote path form
        (``SLV:2026:7:20:CALL:51.5``) before requesting. Response matching is done by
        structured Product identity in ``exact_contract_option_quotes``.
        """
        from modules.exact_contract_option_quotes import prepare_exact_quote_requests

        request_symbols, _request_to_occ, _idents = prepare_exact_quote_requests(symbols)
        return self.get_quotes(
            list(request_symbols or []),
            fail_fast=bool(fail_fast),
            timeout_sec=timeout_sec,
        )
    
    def get_intraday_bars(self, symbols: List[str], interval: str = "15m", bars: int = 10) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get intraday bars for multiple symbols (Rev 20251024 - ETrade with proper bars)
        
        Rev 00252 (Jan 21, 2026): ALWAYS uses production API for market data
        ETrade supports: 5m, 15m, 30m, 1h intervals
        
        Rev 20251024: Fixed to return SINGLE bar (today's OHLC) since ETrade quote API
        doesn't provide historical intraday bars. The detailFlag='INTRADAY' doesn't
        return multiple historical bars - it just returns today's aggregated OHLC.
        
        For multi-bar intraday history prefer the shared session-tech 1m hub
        (broker quote deltas). Do not call Yahoo from this path.
        
        Args:
            symbols: List of symbols to fetch
            interval: Bar interval (5m, 15m, 30m, 1h) - not actually used by ETrade quotes
            bars: Number of bars requested - ETrade quotes only return 1 bar (today's OHLC)
        
        Returns:
            Dict mapping symbol → list with SINGLE bar (today's OHLC only)
        """
        try:
            if not symbols:
                return {}
            
            log.info(f"📊 ETrade: Fetching current OHLC for {len(symbols)} symbols...")
            log.info(f"   Note: ETrade quotes return today's aggregated OHLC only (not historical bars)")
            log.info(f"   Rev 00252: Using production API for market data (automatic)")
            
            # Rev 00252: get_quotes() already handles production API switching automatically
            # Use batch quotes API (more efficient than intraday endpoint)
            import pytz
            PT_TZ = pytz.timezone('America/Los_Angeles')
            ET_TZ = pytz.timezone('America/New_York')
            
            all_bars = {}
            batch_size = 50  # Use same batch size as get_quotes (with overrideSymbolCount)
            
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i+batch_size]
                
                try:
                    # Get current quotes (today's OHLC) - get_quotes() handles production API automatically
                    quotes = self.get_quotes(batch)
                    
                    if quotes:
                        # Create timestamp for today's market open (9:30 AM ET)
                        today_et = datetime.now(ET_TZ).date()
                        market_open_et = ET_TZ.localize(datetime(today_et.year, today_et.month, today_et.day, 9, 30, 0))
                        market_open_pt = market_open_et.astimezone(PT_TZ)
                        
                        for quote in quotes:
                            # ETrade quote has today's OHLC (aggregated from market open)
                            bar = {
                                'timestamp': market_open_pt,
                                'datetime': market_open_pt,
                                'open': quote.open,
                                'high': quote.high,
                                'low': quote.low,
                                'close': quote.last_price,
                                'volume': quote.volume
                            }
                            # Return single bar (ETrade doesn't provide historical intraday)
                            all_bars[quote.symbol] = [bar]
                        
                        log.debug(f"✅ ETrade batch: {len(quotes)} symbols (today's OHLC only)")
                    
                except Exception as batch_error:
                    log.warning(f"ETrade batch failed: {batch_error}")
            
            if all_bars:
                log.info(f"✅ ETrade intraday: {len(all_bars)} symbols (single bar each - today's OHLC)")
            
            return all_bars
            
        except Exception as e:
            log.error(f"Failed to get ETrade intraday bars: {e}")
            return {}
    
    def get_historical_data(self, symbol: str, period: str = "1d", count: int = 200) -> List[Dict[str, Any]]:
        """
        Get historical data for technical analysis
        
        Args:
            symbol: Stock symbol
            period: Time period (1d, 5d, 1m, 3m, 6m, 1y, 2y, 5y)
            count: Number of data points (max 200)
        """
        try:
            log.info(f"📊 Fetching historical data for {symbol} ({period}, {count} points)...")
            
            # E*TRADE doesn't provide historical data directly
            # This would need to be implemented with a different provider
            # For now, return empty list
            log.warning(f"E*TRADE doesn't provide historical data for {symbol}")
            return []
            
        except Exception as e:
            log.error(f"Failed to get historical data for {symbol}: {e}")
            return []
    
    def get_market_data_for_strategy(self, symbol: str) -> Dict[str, Any]:
        """
        Get comprehensive market data for strategy analysis
        
        This method provides all the data that strategies need by:
        1. Getting real-time data from E*TRADE quotes
        2. Computing indicators from the quote (+ optional broker hist if present)
        3. Never calling Yahoo unless ``ETRADE_ALLOW_YAHOO_HISTORICAL=1``
           (0DTE session indicators come from the broker session-tech hub instead)
        """
        try:
            # Get real-time quote from E*TRADE
            quotes = self.get_quotes([symbol])
            if not quotes:
                log.warning(f"No quote data available for {symbol}")
                return self._get_fallback_market_data(symbol)
            
            quote = quotes[0]
            
            # Broker-first: empty hist by default (Yahoo disabled). Quote-only TA is OK;
            # session hub overwrites VWAP/MACD/RSI/ATR/BB for 0DTE paths.
            historical_data = self._get_historical_data_for_symbol(symbol)
            prev_day_high = None
            prev_day_low = None
            if historical_data and len(historical_data) >= 2:
                # Use prior completed daily candle (index -2) for previous-day range context.
                prev_candle = historical_data[-2]
                if isinstance(prev_candle, dict):
                    prev_day_high = prev_candle.get('high')
                    prev_day_low = prev_candle.get('low')
            
            # Calculate comprehensive technical indicators
            technical_indicators = self._calculate_technical_indicators(quote, historical_data)
            
            # Build comprehensive market data
            market_data = {
                'symbol': symbol,
                'current_price': quote.last_price,
                'bid': quote.bid,
                'ask': quote.ask,
                'open': quote.open,
                'high': quote.high,
                'low': quote.low,
                'volume': quote.volume,
                'change': quote.change,
                'change_pct': quote.change_pct,
                'prev_day_high': prev_day_high,
                'prev_day_low': prev_day_low,
                'timestamp': datetime.utcnow().isoformat(),
                
                # Price arrays for technical analysis (enhanced)
                'prices': self._build_price_array(quote, historical_data),
                'volumes': self._build_volume_array(quote, historical_data),
                'closes': self._build_closes_array(quote, historical_data),
                'highs': self._build_highs_array(quote, historical_data),
                'lows': self._build_lows_array(quote, historical_data),
                'opens': self._build_opens_array(quote, historical_data),
                
                # Technical indicators (comprehensive)
                'rsi': technical_indicators['rsi'],
                'rsi_14': technical_indicators['rsi_14'],
                'rsi_21': technical_indicators['rsi_21'],
                'macd': technical_indicators['macd'],
                'macd_signal': technical_indicators['macd_signal'],
                'macd_histogram': technical_indicators['macd_histogram'],
                'sma_20': technical_indicators['sma_20'],
                'sma_50': technical_indicators['sma_50'],
                'sma_200': technical_indicators['sma_200'],
                'ema_12': technical_indicators['ema_12'],
                'ema_26': technical_indicators['ema_26'],
                'atr': technical_indicators['atr'],
                'bollinger_upper': technical_indicators['bollinger_upper'],
                'bollinger_middle': technical_indicators['bollinger_middle'],
                'bollinger_lower': technical_indicators['bollinger_lower'],
                'bollinger_width': technical_indicators['bollinger_width'],
                'bollinger_position': technical_indicators.get('bollinger_position'),
                'vwap': technical_indicators['vwap'],
                'vwap_distance_pct': technical_indicators['vwap_distance_pct'],
                
                # Volume analysis
                'volume_ratio': technical_indicators['volume_ratio'],
                'volume_sma': technical_indicators['volume_sma'],
                'obv': technical_indicators['obv'],
                'ad_line': technical_indicators['ad_line'],
                
                # Pattern recognition
                'doji': technical_indicators['doji'],
                'hammer': technical_indicators['hammer'],
                'engulfing': technical_indicators['engulfing'],
                'morning_star': technical_indicators['morning_star'],
                
                # Market data metadata
                'data_source': 'ETRADE',
                'data_quality': technical_indicators['data_quality'],
                'historical_points': len(historical_data) if historical_data else 0,
                'last_updated': datetime.utcnow().isoformat()
            }
            
            # Rev 00181: Calculate RS vs SPY (Relative Strength) - Enhanced with better fallback logic
            # RS vs SPY = (Symbol change % - SPY change %)
            # This indicates how much the symbol is outperforming/underperforming the market
            try:
                spy_quotes = self.get_quotes(['SPY'])
                if spy_quotes and len(spy_quotes) > 0:
                    spy_quote = spy_quotes[0]
                    
                    # Missing change_pct must stay None (never invent 0.0 — PO densify
                    # treats 0.0 as a real RS fill). Derive from previous_close when present.
                    spy_change_pct = getattr(spy_quote, "change_pct", None)
                    try:
                        if spy_change_pct is not None and float(spy_change_pct) == 0.0:
                            spy_change_pct = None
                    except (TypeError, ValueError):
                        spy_change_pct = None
                    if spy_change_pct is None:
                        try:
                            prev = getattr(spy_quote, "previous_close", None)
                            last = getattr(spy_quote, "last_price", None)
                            if prev and last and float(prev) > 0:
                                spy_change_pct = ((float(last) - float(prev)) / float(prev)) * 100.0
                        except (TypeError, ValueError):
                            spy_change_pct = None

                    symbol_change_pct = getattr(quote, "change_pct", None)
                    try:
                        if symbol_change_pct is not None and float(symbol_change_pct) == 0.0:
                            symbol_change_pct = None
                    except (TypeError, ValueError):
                        symbol_change_pct = None

                    # Enhanced fallback - open/last vs previous close (quote field or hist)
                    symbol_change_calculated = False
                    if symbol_change_pct is None and quote.last_price:
                        prev_close = getattr(quote, "previous_close", None)
                        if prev_close and float(prev_close) > 0:
                            try:
                                base_price = quote.open if quote.open and quote.open > 0 else quote.last_price
                                symbol_change_pct = ((float(base_price) - float(prev_close)) / float(prev_close)) * 100.0
                                symbol_change_calculated = True
                            except (TypeError, ValueError):
                                pass
                        if symbol_change_pct is None and historical_data and len(historical_data) >= 2:
                            try:
                                prev_close = historical_data[-2].get('close', None) if isinstance(historical_data[-2], dict) else (getattr(historical_data[-2], 'close', None) if hasattr(historical_data[-2], 'close') else None)
                                if prev_close and prev_close > 0:
                                    base_price = quote.open if quote.open and quote.open > 0 else quote.last_price
                                    symbol_change_pct = ((base_price - prev_close) / prev_close) * 100
                                    symbol_change_calculated = True
                                    log.debug(f"📊 Calculated symbol change % from open vs prev close for {symbol}: {symbol_change_pct:.2f}%")
                            except Exception as hist_error:
                                log.debug(f"⚠️ Could not calculate symbol change % from historical data for {symbol}: {hist_error}")

                    if symbol_change_pct is None or spy_change_pct is None:
                        market_data["rs_vs_spy"] = None
                        market_data["spy_price"] = getattr(spy_quote, "last_price", None)
                        market_data["spy_change_pct"] = spy_change_pct
                    else:
                        rs_vs_spy = float(symbol_change_pct) - float(spy_change_pct)
                        market_data["rs_vs_spy"] = rs_vs_spy
                        market_data["spy_price"] = spy_quote.last_price
                        market_data["spy_change_pct"] = spy_change_pct
                        if symbol_change_calculated:
                            log.info(f"✅ Calculated RS vs SPY for {symbol} (using fallback): {rs_vs_spy:.2f}% (symbol: {symbol_change_pct:.2f}%, SPY: {spy_change_pct:.2f}%)")
                        else:
                            log.debug(f"✅ Calculated RS vs SPY for {symbol}: {rs_vs_spy:.2f}% (symbol: {symbol_change_pct:.2f}%, SPY: {spy_change_pct:.2f}%)")
                else:
                    log.warning(f"⚠️ Could not fetch SPY quote for RS vs SPY calculation for {symbol}")
                    # Never invent 0.0 — densify treats it as a real fill and desks misread flat RS.
                    market_data['rs_vs_spy'] = None
                    market_data['spy_price'] = None
                    market_data['spy_change_pct'] = None
            except Exception as rs_error:
                log.warning(f"⚠️ Error calculating RS vs SPY for {symbol}: {rs_error}", exc_info=True)
                market_data['rs_vs_spy'] = None
                market_data['spy_price'] = None
                market_data['spy_change_pct'] = None
            
            log.debug(f"📊 Generated comprehensive market data for {symbol}: {technical_indicators['data_quality']} quality")
            return market_data
            
        except Exception as e:
            log.error(f"Failed to get market data for strategy: {e}", exc_info=True)
            log.warning(f"   • Exception in get_market_data_for_strategy({symbol}): {type(e).__name__}: {e}")
            log.warning(
                "   • Returning placeholder frame (RSI/MACD/volume null + missing flags) — "
                "session-tech hub is authoritative for 0DTE"
            )
            fallback_data = self._get_fallback_market_data(symbol)
            log.debug(
                "   ✅ Fallback frame for %s: data_quality=%s rsi=%s vol=%s",
                symbol,
                fallback_data.get("data_quality"),
                fallback_data.get("rsi"),
                fallback_data.get("volume_ratio"),
            )
            return fallback_data
    
    def _calculate_basic_rsi(self, quote: ETradeQuote) -> Optional[float]:
        """No invent RSI — return None so session-tech hub / missing flags win."""
        _ = quote
        return None
    
    def _calculate_basic_macd(self, quote: ETradeQuote) -> Optional[float]:
        """No invent MACD — return None when history is too thin."""
        _ = quote
        return None
    
    def _calculate_basic_atr(self, quote: ETradeQuote) -> Optional[float]:
        """ATR from quote high-low only when both sides exist; else None."""
        try:
            hi = float(quote.high)
            lo = float(quote.low)
            if hi > lo > 0:
                return hi - lo
        except Exception:
            pass
        return None
    
    def _get_historical_data_for_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Historical bars for comprehensive TA.

        Broker-first policy: do **not** call Yahoo/yfinance when E*TRADE quote +
        session-tech hub already cover 0DTE indicators. Yahoo is opt-in only via
        ``ETRADE_ALLOW_YAHOO_HISTORICAL=1`` as an absolute last resort.
        """
        try:
            allow_yahoo = str(
                os.environ.get("ETRADE_ALLOW_YAHOO_HISTORICAL", "0") or "0"
            ).strip().lower() in {"1", "true", "yes", "on"}
            if not allow_yahoo:
                log.debug(
                    "HISTORICAL_SKIP_YAHOO | symbol=%s | reason=broker_quote_and_session_hub_preferred",
                    symbol,
                )
                return []
            historical_data = self._fetch_historical_data_on_demand(symbol, days=200)
            if historical_data and len(historical_data) > 0:
                log.debug(
                    "Fetched %s days of Yahoo historical for %s (ETRADE_ALLOW_YAHOO_HISTORICAL)",
                    len(historical_data),
                    symbol,
                )
                return historical_data
            log.debug("No historical data available for %s - using E*TRADE quote only", symbol)
            return []
        except Exception as e:
            log.debug("No historical data available for %s: %s", symbol, e)
            return []

    def _fetch_historical_data_on_demand(self, symbol: str, days: int = 200) -> List[Dict[str, Any]]:
        """
        Absolute last-resort Yahoo daily history (disabled unless
        ``ETRADE_ALLOW_YAHOO_HISTORICAL=1``). Prefer E*TRADE quotes + session hub.
        """
        allow_yahoo = str(
            os.environ.get("ETRADE_ALLOW_YAHOO_HISTORICAL", "0") or "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not allow_yahoo:
            return []
        try:
            import yfinance as yf

            from .prime_data_manager import resolve_yahoo_ticker

            yahoo_ticker = resolve_yahoo_ticker(symbol)
            ticker = yf.Ticker(yahoo_ticker)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days + 30)

            hist = ticker.history(start=start_date, end=end_date)

            if hist is not None and len(hist) > 0:
                formatted_data = []
                for idx, row in hist.iterrows():
                    formatted_data.append({
                        'date': idx.strftime("%Y-%m-%d"),
                        'open': float(row['Open']),
                        'high': float(row['High']),
                        'low': float(row['Low']),
                        'close': float(row['Close']),
                        'volume': int(row['Volume'])
                    })
                return formatted_data[-days:] if len(formatted_data) > days else formatted_data
            return []
        except ImportError:
            log.warning("yfinance not available for %s - using E*TRADE quote only", symbol)
            return []
        except Exception as e:
            log.debug("Could not fetch Yahoo historical for %s: %s", symbol, e)
            return []
    
    def _calculate_technical_indicators(self, quote: ETradeQuote, historical_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate comprehensive technical indicators"""
        try:
            # Build price arrays from available data
            closes = self._build_closes_array(quote, historical_data)
            highs = self._build_highs_array(quote, historical_data)
            lows = self._build_lows_array(quote, historical_data)
            volumes = self._build_volume_array(quote, historical_data)
            
            # Determine data quality
            data_quality = 'excellent' if len(historical_data) >= 200 else 'good' if len(historical_data) >= 50 else 'limited' if len(historical_data) >= 20 else 'minimal'
            
            # ETradeQuote objects may not provide `close`; for indicator calculations
            # we treat `last_price` (or `price`) as the close fallback.
            _quote_close = getattr(quote, 'close', None)
            if _quote_close is None:
                _quote_close = getattr(quote, 'last_price', None) or getattr(quote, 'price', None)
            
            # Calculate indicators based on available data — never invent 50 / 0 / 1.0
            # without missing flags. Thin series → None + flags; session hub fills 0DTE.
            rsi_ready = len(closes) >= 15
            macd_ready = len(closes) >= 26
            vol_ready = len(volumes) >= 20
            try:
                from modules.session_tech_indicators import bollinger_from_closes

                _bb = bollinger_from_closes(closes, period=20) if len(closes) >= 10 else {
                    "bollinger_upper": None,
                    "bollinger_middle": None,
                    "bollinger_lower": None,
                    "bollinger_width": None,
                    "bollinger_position": None,
                }
            except Exception:
                _bb = {
                    "bollinger_upper": None,
                    "bollinger_middle": None,
                    "bollinger_lower": None,
                    "bollinger_width": None,
                    "bollinger_position": None,
                }
            indicators = {
                # RSI calculations
                'rsi': self._calculate_rsi(closes, 14) if rsi_ready else None,
                'rsi_14': self._calculate_rsi(closes, 14) if rsi_ready else None,
                'rsi_21': self._calculate_rsi(closes, 21) if len(closes) >= 22 else None,
                'rsi_missing': not rsi_ready,
                
                # MACD calculations
                'macd': self._calculate_macd(closes) if macd_ready else None,
                'macd_signal': self._calculate_macd_signal(closes) if macd_ready else None,
                'macd_histogram': self._calculate_macd_histogram(closes) if macd_ready else None,
                'macd_missing': not macd_ready,
                
                # Moving averages
                'sma_20': self._calculate_sma(closes, 20) if len(closes) >= 20 else quote.last_price,
                'sma_50': self._calculate_sma(closes, 50) if len(closes) >= 50 else quote.last_price,
                'sma_200': self._calculate_sma(closes, 200) if len(closes) >= 200 else quote.last_price,
                'ema_12': self._calculate_ema(closes, 12) if len(closes) >= 12 else quote.last_price,
                'ema_26': self._calculate_ema(closes, 26) if len(closes) >= 26 else quote.last_price,
                
                # Volatility indicators
                'atr': self._calculate_atr(highs, lows, closes) if len(highs) >= 14 else self._calculate_basic_atr(quote),
                
                # Bollinger — session-hub math; leave None when too thin (no quote H/L fakes)
                'bollinger_upper': _bb.get('bollinger_upper'),
                'bollinger_middle': _bb.get('bollinger_middle'),
                'bollinger_lower': _bb.get('bollinger_lower'),
                'bollinger_width': _bb.get('bollinger_width'),
                'bollinger_position': _bb.get('bollinger_position'),
                'vwap': self._calculate_vwap(highs, lows, closes, volumes),
                'vwap_distance_pct': None,
                
                # Volume analysis
                'volume_ratio': self._calculate_volume_ratio(volumes) if vol_ready else None,
                'volume_ratio_missing': not vol_ready,
                'volume_sma': self._calculate_sma(volumes, 20) if vol_ready else quote.volume,
                'obv': self._calculate_obv(closes, volumes) if len(closes) >= 2 else None,
                'ad_line': self._calculate_ad_line(highs, lows, closes, volumes) if len(highs) >= 2 else None,
                
                # Pattern recognition
                'doji': self._detect_doji(quote) if quote.open and _quote_close else False,
                'hammer': self._detect_hammer(quote) if quote.open and _quote_close and quote.high and quote.low else False,
                'engulfing': False,  # Would need previous candle data
                'morning_star': False,  # Would need previous candle data
                
                # Data quality
                'data_quality': data_quality
            }

            # Keep VWAP distance stable and numeric when inputs are available.
            try:
                vwap_val = indicators.get('vwap')
                if vwap_val is not None and float(vwap_val) > 0 and _quote_close is not None:
                    indicators['vwap_distance_pct'] = ((float(_quote_close) - float(vwap_val)) / float(vwap_val)) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                indicators['vwap_distance_pct'] = None
            
            return indicators
            
        except Exception as e:
            log.error(f"Error calculating technical indicators: {e}")
            return self._get_basic_indicators(quote)
    
    def _build_price_array(self, quote: ETradeQuote, historical_data: List[Dict[str, Any]]) -> List[float]:
        """Build price array for technical analysis"""
        try:
            prices = []
            
            # Add historical data if available
            if historical_data:
                for data_point in historical_data:
                    if 'close' in data_point:
                        prices.append(float(data_point['close']))
            
            # Add current quote data
            if quote.last_price:
                prices.append(quote.last_price)
            
            return prices if prices else [quote.last_price] if quote.last_price else [100.0]
        except:
            return [quote.last_price] if quote.last_price else [100.0]
    
    def _build_volume_array(self, quote: ETradeQuote, historical_data: List[Dict[str, Any]]) -> List[int]:
        """Build volume array for technical analysis"""
        try:
            volumes = []
            
            # Add historical data if available
            if historical_data:
                for data_point in historical_data:
                    if 'volume' in data_point:
                        volumes.append(int(data_point['volume']))
            
            # Add current quote data
            if quote.volume:
                volumes.append(quote.volume)
            
            return volumes if volumes else [quote.volume] if quote.volume else [1000000]
        except:
            return [quote.volume] if quote.volume else [1000000]
    
    def _build_closes_array(self, quote: ETradeQuote, historical_data: List[Dict[str, Any]]) -> List[float]:
        """Build closes array for technical analysis"""
        return self._build_price_array(quote, historical_data)
    
    def _build_highs_array(self, quote: ETradeQuote, historical_data: List[Dict[str, Any]]) -> List[float]:
        """Build highs array for technical analysis"""
        try:
            highs = []
            
            # Add historical data if available
            if historical_data:
                for data_point in historical_data:
                    if 'high' in data_point:
                        highs.append(float(data_point['high']))
            
            # Add current quote data
            if quote.high:
                highs.append(quote.high)
            
            return highs if highs else [quote.high] if quote.high else [quote.last_price] if quote.last_price else [100.0]
        except:
            return [quote.high] if quote.high else [quote.last_price] if quote.last_price else [100.0]
    
    def _build_lows_array(self, quote: ETradeQuote, historical_data: List[Dict[str, Any]]) -> List[float]:
        """Build lows array for technical analysis"""
        try:
            lows = []
            
            # Add historical data if available
            if historical_data:
                for data_point in historical_data:
                    if 'low' in data_point:
                        lows.append(float(data_point['low']))
            
            # Add current quote data
            if quote.low:
                lows.append(quote.low)
            
            return lows if lows else [quote.low] if quote.low else [quote.last_price] if quote.last_price else [100.0]
        except:
            return [quote.low] if quote.low else [quote.last_price] if quote.last_price else [100.0]
    
    def _build_opens_array(self, quote: ETradeQuote, historical_data: List[Dict[str, Any]]) -> List[float]:
        """Build opens array for technical analysis"""
        try:
            opens = []
            
            # Add historical data if available
            if historical_data:
                for data_point in historical_data:
                    if 'open' in data_point:
                        opens.append(float(data_point['open']))
            
            # Add current quote data
            if quote.open:
                opens.append(quote.open)
            
            return opens if opens else [quote.open] if quote.open else [quote.last_price] if quote.last_price else [100.0]
        except:
            return [quote.open] if quote.open else [quote.last_price] if quote.last_price else [100.0]
    
    def _get_fallback_market_data(self, symbol: str) -> Dict[str, Any]:
        """Placeholder frame when E*TRADE data is unavailable — no invent RSI/MACD/vol."""
        try:
            return {
                'symbol': symbol,
                'current_price': None,
                'bid': None,
                'ask': None,
                'open': None,
                'high': None,
                'low': None,
                'volume': None,
                'change': None,
                'change_pct': None,
                'timestamp': datetime.utcnow().isoformat(),
                
                'prices': [],
                'volumes': [],
                'closes': [],
                'highs': [],
                'lows': [],
                'opens': [],
                
                # No invent — session-tech hub stamps real RSI/MACD/volume for 0DTE.
                'rsi': None,
                'rsi_14': None,
                'rsi_21': None,
                'rsi_missing': True,
                'macd': None,
                'macd_signal': None,
                'macd_histogram': None,
                'macd_missing': True,
                'sma_20': None,
                'sma_50': None,
                'sma_200': None,
                'ema_12': None,
                'ema_26': None,
                'atr': None,
                'bollinger_upper': None,
                'bollinger_middle': None,
                'bollinger_lower': None,
                'bollinger_width': None,
                'bollinger_position': None,
                'vwap': None,
                'vwap_distance_pct': None,
                'vwap_missing': True,
                
                'volume_ratio': None,
                'volume_ratio_missing': True,
                'volume_sma': None,
                'obv': None,
                'ad_line': None,
                
                'doji': False,
                'hammer': False,
                'engulfing': False,
                'morning_star': False,
                
                'data_source': 'FALLBACK',
                'data_quality': 'placeholder',
                'historical_points': 0,
                'last_updated': datetime.utcnow().isoformat()
            }
        except Exception as e:
            log.error(f"Error creating fallback market data: {e}")
            return {}
    
    def _get_basic_indicators(self, quote: ETradeQuote) -> Dict[str, Any]:
        """Minimal quote-only frame — never invent RSI=50 / MACD=0 / volume=1.0."""
        last = getattr(quote, "last_price", None) or getattr(quote, "price", None)
        return {
            'rsi': None,
            'rsi_14': None,
            'rsi_21': None,
            'rsi_missing': True,
            'macd': None,
            'macd_signal': None,
            'macd_histogram': None,
            'macd_missing': True,
            'sma_20': last,
            'sma_50': last,
            'sma_200': last,
            'ema_12': last,
            'ema_26': last,
            'atr': self._calculate_basic_atr(quote),
            # Never invent quote H/L as Bollinger — session hub stamps real BB for PO.
            'bollinger_upper': None,
            'bollinger_middle': None,
            'bollinger_lower': None,
            'bollinger_width': None,
            'bollinger_position': None,
            'vwap': last,
            'vwap_distance_pct': None,
            'volume_ratio': None,
            'volume_ratio_missing': True,
            'volume_sma': getattr(quote, "volume", None),
            'obv': None,
            'ad_line': None,
            'doji': False,
            'hammer': False,
            'engulfing': False,
            'morning_star': False,
            'data_quality': 'minimal',
            'data_source': 'FALLBACK',
        }
    
    # ========================================================================
    # COMPREHENSIVE TECHNICAL ANALYSIS METHODS
    # ========================================================================
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Calculate RSI with proper algorithm"""
        try:
            if len(prices) < period + 1:
                return 50.0
            
            deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
            gains = [delta if delta > 0 else 0 for delta in deltas]
            losses = [-delta if delta < 0 else 0 for delta in deltas]
            
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            
            if avg_loss == 0:
                return 100.0
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            return rsi
        except:
            return 50.0
    
    def _calculate_macd(self, prices: List[float]) -> float:
        """Calculate MACD line"""
        try:
            if len(prices) < 26:
                return 0.0
            
            ema_12 = self._calculate_ema(prices, 12)
            ema_26 = self._calculate_ema(prices, 26)
            return ema_12 - ema_26
        except:
            return 0.0
    
    def _calculate_macd_signal(self, prices: List[float]) -> float:
        """Calculate MACD signal line"""
        try:
            if len(prices) < 26:
                return 0.0
            
            macd_values = []
            for i in range(26, len(prices) + 1):
                ema_12 = self._calculate_ema(prices[:i], 12)
                ema_26 = self._calculate_ema(prices[:i], 26)
                macd_values.append(ema_12 - ema_26)
            
            if len(macd_values) < 9:
                return 0.0
            
            return self._calculate_ema(macd_values, 9)
        except:
            return 0.0
    
    def _calculate_macd_histogram(self, prices: List[float]) -> float:
        """Calculate MACD histogram"""
        try:
            macd = self._calculate_macd(prices)
            signal = self._calculate_macd_signal(prices)
            return macd - signal
        except:
            return 0.0
    
    def _calculate_sma(self, prices: List[float], period: int) -> float:
        """Calculate Simple Moving Average"""
        try:
            if len(prices) < period:
                return sum(prices) / len(prices) if prices else 0.0
            return sum(prices[-period:]) / period
        except:
            return 0.0
    
    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """Calculate Exponential Moving Average"""
        try:
            if len(prices) < period:
                return sum(prices) / len(prices) if prices else 0.0
            
            multiplier = 2 / (period + 1)
            ema = prices[0]
            
            for price in prices[1:]:
                ema = (price * multiplier) + (ema * (1 - multiplier))
            
            return ema
        except:
            return 0.0
    
    def _calculate_atr(self, highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Calculate Average True Range"""
        try:
            if len(highs) < period + 1:
                return 0.0
            
            true_ranges = []
            for i in range(1, len(highs)):
                tr1 = highs[i] - lows[i]
                tr2 = abs(highs[i] - closes[i-1])
                tr3 = abs(lows[i] - closes[i-1])
                true_ranges.append(max(tr1, tr2, tr3))
            
            if len(true_ranges) < period:
                return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0
            
            return sum(true_ranges[-period:]) / period
        except:
            return 0.0
    
    def _calculate_bollinger_upper(self, prices: List[float], period: int = 20, std_dev: float = 2.0) -> float:
        """Calculate Bollinger Bands upper band"""
        try:
            if len(prices) < period:
                return prices[-1] if prices else 0.0
            
            sma = self._calculate_sma(prices, period)
            variance = sum((price - sma) ** 2 for price in prices[-period:]) / period
            std = variance ** 0.5
            return sma + (std_dev * std)
        except:
            return prices[-1] if prices else 0.0
    
    def _calculate_bollinger_middle(self, prices: List[float], period: int = 20) -> float:
        """Calculate Bollinger Bands middle band (SMA)"""
        return self._calculate_sma(prices, period)
    
    def _calculate_bollinger_lower(self, prices: List[float], period: int = 20, std_dev: float = 2.0) -> float:
        """Calculate Bollinger Bands lower band"""
        try:
            if len(prices) < period:
                return prices[-1] if prices else 0.0
            
            sma = self._calculate_sma(prices, period)
            variance = sum((price - sma) ** 2 for price in prices[-period:]) / period
            std = variance ** 0.5
            return sma - (std_dev * std)
        except:
            return prices[-1] if prices else 0.0
    
    def _calculate_bollinger_width(self, prices: List[float], period: int = 20) -> float:
        """Calculate Bollinger Bands width"""
        try:
            upper = self._calculate_bollinger_upper(prices, period)
            lower = self._calculate_bollinger_lower(prices, period)
            middle = self._calculate_bollinger_middle(prices, period)
            return (upper - lower) / middle if middle != 0 else 0.0
        except:
            return 0.0
    
    def _calculate_volume_ratio(self, volumes: List[int], period: int = 20) -> float:
        """Calculate volume ratio (current vs average)"""
        try:
            if len(volumes) < 2:
                return 1.0
            
            current_volume = volumes[-1]
            avg_volume = self._calculate_sma(volumes, min(period, len(volumes)))
            return current_volume / avg_volume if avg_volume > 0 else 1.0
        except:
            return 1.0

    def _calculate_vwap(self, highs: List[float], lows: List[float], closes: List[float], volumes: List[int]) -> float:
        """Calculate VWAP from available OHLCV arrays."""
        try:
            n = min(len(highs), len(lows), len(closes), len(volumes))
            if n <= 0:
                return 0.0
            total_tpv = 0.0
            total_vol = 0.0
            for i in range(n):
                vol = float(volumes[i] or 0)
                if vol <= 0:
                    continue
                typical = (float(highs[i]) + float(lows[i]) + float(closes[i])) / 3.0
                total_tpv += typical * vol
                total_vol += vol
            return (total_tpv / total_vol) if total_vol > 0 else 0.0
        except Exception:
            return 0.0
    
    def _calculate_obv(self, closes: List[float], volumes: List[int]) -> float:
        """Calculate On-Balance Volume"""
        try:
            if len(closes) < 2 or len(volumes) < 2:
                return 0.0
            
            obv = 0.0
            for i in range(1, min(len(closes), len(volumes))):
                if closes[i] > closes[i-1]:
                    obv += volumes[i]
                elif closes[i] < closes[i-1]:
                    obv -= volumes[i]
            
            return obv
        except:
            return 0.0
    
    def _calculate_ad_line(self, highs: List[float], lows: List[float], closes: List[float], volumes: List[int]) -> float:
        """Calculate Accumulation/Distribution Line"""
        try:
            if len(highs) < 2 or len(volumes) < 2:
                return 0.0
            
            ad_line = 0.0
            for i in range(min(len(highs), len(lows), len(closes), len(volumes))):
                if highs[i] != lows[i]:
                    clv = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / (highs[i] - lows[i])
                    ad_line += clv * volumes[i]
            
            return ad_line
        except:
            return 0.0
    
    def _detect_doji(self, quote: ETradeQuote) -> bool:
        """Detect doji pattern"""
        try:
            close_price = getattr(quote, 'close', None)
            if close_price is None:
                close_price = getattr(quote, 'last_price', None) or getattr(quote, 'price', None)
            if not all([quote.open, close_price, quote.high, quote.low]):
                return False
            
            body_size = abs(close_price - quote.open)
            total_range = quote.high - quote.low
            
            return body_size <= (total_range * 0.1) if total_range > 0 else False
        except:
            return False
    
    def _detect_hammer(self, quote: ETradeQuote) -> bool:
        """Detect hammer pattern"""
        try:
            close_price = getattr(quote, 'close', None)
            if close_price is None:
                close_price = getattr(quote, 'last_price', None) or getattr(quote, 'price', None)
            if not all([quote.open, close_price, quote.high, quote.low]):
                return False
            
            body_size = abs(close_price - quote.open)
            lower_shadow = min(quote.open, close_price) - quote.low
            upper_shadow = quote.high - max(quote.open, close_price)
            total_range = quote.high - quote.low
            
            return (lower_shadow >= 2 * body_size and 
                   upper_shadow <= body_size * 0.5 and 
                   total_range > 0)
        except:
            return False
    
    def _parse_quotes_response(self, response: Dict) -> List[ETradeQuote]:
        """Parse quotes response (supports both JSON and XML)"""
        try:
            # Helper functions for safe type conversion
            def safe_float(value):
                try:
                    return float(value) if value else 0.0
                except (ValueError, TypeError):
                    return 0.0
            
            def safe_int(value):
                try:
                    return int(value) if value else 0
                except (ValueError, TypeError):
                    return 0
            
            # CRITICAL FIX: Handle JSON response (most common)
            if 'QuoteResponse' in response:
                log.info("📋 Parsing JSON quote response")
                quote_response = response['QuoteResponse']
                log.info(f"📋 QuoteResponse keys: {list(quote_response.keys()) if isinstance(quote_response, dict) else 'not a dict'}")
                
                # Check for error messages
                if 'Messages' in quote_response:
                    messages = quote_response['Messages']
                    if isinstance(messages, dict) and 'Message' in messages:
                        msg_list = messages['Message'] if isinstance(messages['Message'], list) else [messages['Message']]
                        for msg in msg_list:
                            log.warning(f"⚠️ E*TRADE API Message: {msg.get('description', 'Unknown')} (code: {msg.get('code', 'N/A')})")
                
                # QuoteResponse can have either 'QuoteData' (list) or single quote
                quote_data_list = quote_response.get('QuoteData', [])
                log.info(f"📋 QuoteData type: {type(quote_data_list)}, length: {len(quote_data_list) if isinstance(quote_data_list, (list, tuple)) else 'N/A'}")
                
                # Ensure it's a list
                if not isinstance(quote_data_list, list):
                    quote_data_list = [quote_data_list]
                
                quotes = []
                skipped_count = 0
                for quote_data in quote_data_list:
                    if not quote_data:
                        skipped_count += 1
                        continue
                    
                    # Extract product info (contains symbol + option identity fields)
                    product = quote_data.get('Product', {}) or {}
                    all_data = quote_data.get('All', {}) or {}
                    option_data = quote_data.get('Option', {}) or {}
                    if not isinstance(product, dict):
                        product = {}
                    if not isinstance(all_data, dict):
                        all_data = {}
                    if not isinstance(option_data, dict):
                        option_data = {}
                    
                    symbol = product.get('symbol', '')
                    if not symbol:
                        skipped_count += 1
                        log.debug(f"⚠️ Skipping quote with no symbol: {quote_data}")
                        continue

                    def _opt_int(v):
                        try:
                            return int(v) if v is not None and v != "" else None
                        except (TypeError, ValueError):
                            return None

                    def _opt_float(v):
                        try:
                            return float(v) if v is not None and v != "" else None
                        except (TypeError, ValueError):
                            return None

                    osi_key = str(
                        all_data.get("osiKey")
                        or option_data.get("osiKey")
                        or product.get("osiKey")
                        or ""
                    )
                    date_time_utc = all_data.get("dateTimeUTC") or quote_data.get("dateTimeUTC")
                    try:
                        date_time_utc_i = int(date_time_utc) if date_time_utc is not None else None
                    except (TypeError, ValueError):
                        date_time_utc_i = None
                    # All.dateTime is the secondary authoritative broker clock when UTC absent.
                    date_time_str = str(
                        all_data.get("dateTime") or quote_data.get("dateTime") or ""
                    ).strip() or None

                    def _size_or_none(*candidates: Any) -> Optional[int]:
                        for raw in candidates:
                            if raw is None or raw == "":
                                continue
                            try:
                                v = int(float(raw))
                            except (TypeError, ValueError):
                                continue
                            if v >= 0:
                                return v
                        return None

                    bid_size_v = _size_or_none(
                        all_data.get("bidSize"),
                        all_data.get("bidsize"),
                        option_data.get("bidSize"),
                        option_data.get("bidsize"),
                    )
                    ask_size_v = _size_or_none(
                        all_data.get("askSize"),
                        all_data.get("asksize"),
                        option_data.get("askSize"),
                        option_data.get("asksize"),
                    )
                    avg_vol_v = _size_or_none(
                        all_data.get("averageVolume"),
                        all_data.get("average_volume"),
                        all_data.get("avgVolume"),
                    )
                    prev_close_v = safe_float(
                        all_data.get("previousClose")
                        if all_data.get("previousClose") is not None
                        else all_data.get("previousClosePrice")
                    )
                    if prev_close_v <= 0:
                        prev_close_v = None

                    greeks_blob = all_data.get("OptionGreeks") or option_data.get("OptionGreeks") or {}
                    if not isinstance(greeks_blob, dict):
                        greeks_blob = {}
                    def _greek(*keys: str) -> Optional[float]:
                        for blob in (greeks_blob, all_data, option_data):
                            if not isinstance(blob, dict):
                                continue
                            for key in keys:
                                if blob.get(key) not in (None, ""):
                                    return _opt_float(blob.get(key))
                        return None

                    delta_v = _greek("delta")
                    gamma_v = _greek("gamma")
                    theta_v = _greek("theta")
                    vega_v = _greek("vega")
                    iv_v = _greek("iv", "impliedVolatility", "implied_volatility")

                    # Never invent changePercent=0.0 when broker omits the field.
                    _chg_pct_raw = all_data.get("changePercent")
                    if _chg_pct_raw is None or _chg_pct_raw == "":
                        change_pct_v = None
                    else:
                        try:
                            change_pct_v = float(_chg_pct_raw)
                        except (TypeError, ValueError):
                            change_pct_v = None

                    quotes.append(ETradeQuote(
                        symbol=symbol,
                        last_price=safe_float(all_data.get('lastTrade')),
                        change=safe_float(all_data.get('change')),
                        change_pct=change_pct_v,
                        volume=safe_int(all_data.get('totalVolume')),
                        bid=safe_float(all_data.get('bid')),
                        ask=safe_float(all_data.get('ask')),
                        high=safe_float(all_data.get('high')),
                        low=safe_float(all_data.get('low')),
                        open=safe_float(all_data.get('open')),
                        security_type=str(product.get("securityType") or ""),
                        call_put=str(product.get("callPut") or ""),
                        expiry_year=_opt_int(product.get("expiryYear")),
                        expiry_month=_opt_int(product.get("expiryMonth")),
                        expiry_day=_opt_int(product.get("expiryDay")),
                        strike_price=_opt_float(
                            product.get("strikePrice")
                            if product.get("strikePrice") is not None
                            else product.get("strike")
                        ),
                        osi_key=osi_key,
                        date_time_utc=date_time_utc_i,
                        date_time=date_time_str,
                        raw_product=dict(product),
                        raw_all={
                            k: all_data.get(k)
                            for k in (
                                "dateTime",
                                "dateTimeUTC",
                                "osiKey",
                                "bid",
                                "ask",
                                "bidSize",
                                "askSize",
                                "totalVolume",
                                "averageVolume",
                                "previousClose",
                                "OptionGreeks",
                                "delta",
                                "gamma",
                                "theta",
                                "vega",
                                "iv",
                                "impliedVolatility",
                            )
                            if k in all_data
                        },
                        bid_size=bid_size_v,
                        ask_size=ask_size_v,
                        average_volume=avg_vol_v,
                        previous_close=prev_close_v,
                        delta=delta_v,
                        gamma=gamma_v,
                        theta=theta_v,
                        vega=vega_v,
                        implied_volatility=iv_v,
                    ))
                
                if skipped_count > 0:
                    log.warning(f"⚠️ Skipped {skipped_count} quotes (no symbol or empty data)")
                log.info(f"📊 Parsed {len(quotes)} quotes from {len(quote_data_list)} QuoteData entries")
                return quotes
            
            # Fallback: Handle XML response (legacy)
            xml_key = '<?xml version'
            if xml_key in response:
                log.debug("Parsing XML quote response")
                xml_data = response[xml_key]
                
                # Clean up XML data
                xml_data = xml_data.strip()
                if xml_data.startswith('"1.0" encoding="UTF-8" standalone="yes"?>'):
                    xml_data = '<?xml version=' + xml_data
                elif not xml_data.startswith('<?xml version'):
                    xml_data = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + xml_data
                
                root = ET.fromstring(xml_data)
                
                quotes = []
                for quote in root.findall('.//QuoteData'):
                    quote_data = {}
                    for child in quote:
                        quote_data[child.tag] = child.text
                    
                    _xml_chg = quote_data.get('changePercent')
                    if _xml_chg is None or _xml_chg == "":
                        _xml_chg_pct = None
                    else:
                        try:
                            _xml_chg_pct = float(_xml_chg)
                        except (TypeError, ValueError):
                            _xml_chg_pct = None
                    quotes.append(ETradeQuote(
                        symbol=quote_data.get('symbol', ''),
                        last_price=safe_float(quote_data.get('lastPrice')),
                        change=safe_float(quote_data.get('change')),
                        change_pct=_xml_chg_pct,
                        volume=safe_int(quote_data.get('volume')),
                        bid=safe_float(quote_data.get('bid')),
                        ask=safe_float(quote_data.get('ask')),
                        high=safe_float(quote_data.get('high')),
                        low=safe_float(quote_data.get('low')),
                        open=safe_float(quote_data.get('open'))
                    ))
                
                log.debug(f"Parsed {len(quotes)} quotes from XML response")
                return quotes
            
            log.warning(f"Unknown response format - keys: {list(response.keys())}")
            return []
            
        except Exception as e:
            log.error(f"Failed to parse quotes response: {e}", exc_info=True)
            return []
    
    def get_cash_available_for_trading(self) -> Optional[float]:
        """Get cash available for trading (primary method)"""
        if not self.balance:
            self.get_account_balance()
        
        return self.balance.cash_available_for_investment if self.balance else None
    
    def get_cash_available_for_investment(self) -> Optional[float]:
        """Get cash specifically available for investment"""
        if not self.balance:
            self.get_account_balance()
        
        return self.balance.cash_available_for_investment if self.balance else None
    
    def get_cash_buying_power(self) -> Optional[float]:
        """Get total cash buying power (cash + margin)"""
        if not self.balance:
            self.get_account_balance()
        
        return self.balance.cash_buying_power if self.balance else None
    
    def get_available_cash_for_trading(self) -> Optional[float]:
        """
        Get the most appropriate cash amount for trading decisions
        
        Priority order:
        1. Cash available for investment (primary trading cash)
        2. Cash buying power (includes margin)
        """
        if not self.balance:
            self.get_account_balance()
        
        if not self.balance:
            return None
        
        # Priority order for trading decisions
        if self.balance.cash_available_for_investment is not None and self.balance.cash_available_for_investment > 0:
            return self.balance.cash_available_for_investment
        elif self.balance.cash_buying_power is not None and self.balance.cash_buying_power > 0:
            return self.balance.cash_buying_power
        else:
            return 0.0
    
    def get_total_portfolio_value(self) -> float:
        """Get total portfolio value from positions"""
        if not self.portfolio:
            self.get_portfolio()
        
        return sum(pos.market_value for pos in self.portfolio)
    
    def get_position_by_symbol(self, symbol: str) -> Optional[ETradePosition]:
        """Get position by symbol"""
        if not self.portfolio:
            self.get_portfolio()
        
        for position in self.portfolio:
            if position.symbol == symbol:
                return position
        return None
    
    def refresh_data(self):
        """Refresh all account data"""
        log.info("🔄 Refreshing ETrade account data...")
        self.get_account_balance()
        self.get_portfolio()
        log.info("✅ ETrade data refreshed")
    
    def get_account_summary(self) -> Dict[str, Any]:
        """Get comprehensive account summary"""
        if not self.selected_account:
            return {"error": "No account selected"}
        
        # Refresh data
        self.refresh_data()
        
        return {
            "account": {
                "id": self.selected_account.account_id,
                "name": self.selected_account.account_name,
                "status": self.selected_account.account_status,
                "type": self.selected_account.account_type
            },
            "balance": {
                "account_value": self.balance.account_value if self.balance else None,
                "cash_available_for_investment": self.balance.cash_available_for_investment if self.balance else None,
                "cash_buying_power": self.balance.cash_buying_power if self.balance else None,
                "option_level": self.balance.option_level if self.balance else None
            },
            "portfolio": {
                "total_positions": len(self.portfolio),
                "total_value": self.get_total_portfolio_value(),
                "positions": [
                    {
                        "symbol": pos.symbol,
                        "quantity": pos.quantity,
                        "market_value": pos.market_value,
                        "total_gain": pos.total_gain,
                        "total_gain_pct": pos.total_gain_pct,
                        "days_gain": pos.days_gain,
                        "days_gain_pct": pos.days_gain_pct
                    }
                    for pos in self.portfolio
                ]
            }
        }
    
    def get_orders(self, status: str = 'OPEN') -> List[Dict[str, Any]]:
        """Get account orders
        
        Args:
            status: Order status filter ('OPEN', 'EXECUTED', 'CANCELLED', 'REJECTED', 'EXPIRED')
        """
        if not self.selected_account:
            raise Exception("No account selected")
        
        try:
            log.info(f"📋 Fetching orders for account {self.selected_account.account_id}...")
            
            response = self._make_etrade_api_call(
                method='GET',
                url=f"{self.config['base_url']}/v1/accounts/{self.selected_account.account_id_key}/orders",
                params={'status': status} if status else {}
            )
            
            orders = []
            if '<?xml version' in response:
                xml_data = response['<?xml version']
                orders = self._parse_orders_response(xml_data)
            
            log.info(f"✅ Retrieved {len(orders)} orders")
            return orders
            
        except Exception as e:
            log.error(f"Failed to get orders: {e}")
            return []
    
    def _parse_orders_response(self, xml_data: str) -> List[Dict[str, Any]]:
        """Parse orders XML response"""
        try:
            # Clean up XML data
            xml_data = xml_data.strip()
            if xml_data.startswith('"1.0" encoding="UTF-8" standalone="yes"?>'):
                xml_data = '<?xml version=' + xml_data
            elif not xml_data.startswith('<?xml version'):
                xml_data = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + xml_data
            
            root = ET.fromstring(xml_data)
            
            orders = []
            for order in root.findall('.//Order'):
                order_data = {
                    'order_id': order.find('orderId').text if order.find('orderId') is not None else None,
                    'status': order.find('status').text if order.find('status') is not None else None,
                    'order_type': order.find('orderType').text if order.find('orderType') is not None else None,
                    'side': order.find('side').text if order.find('side') is not None else None,
                    'symbol': order.find('symbol').text if order.find('symbol') is not None else None,
                    'quantity': int(order.find('quantity').text) if order.find('quantity') is not None else 0,
                    'price': float(order.find('price').text) if order.find('price') is not None else None,
                    'stop_price': float(order.find('stopPrice').text) if order.find('stopPrice') is not None else None,
                    'time_in_force': order.find('timeInForce').text if order.find('timeInForce') is not None else None,
                    'created_time': order.find('createdTime').text if order.find('createdTime') is not None else None,
                    'executed_quantity': int(order.find('executedQuantity').text) if order.find('executedQuantity') is not None else 0,
                    'remaining_quantity': int(order.find('remainingQuantity').text) if order.find('remainingQuantity') is not None else 0,
                }
                orders.append(order_data)
            
            return orders
            
        except Exception as e:
            log.error(f"Failed to parse orders XML: {e}")
            return []
    
    def preview_order(self, symbol: str, quantity: int, side: str, order_type: str = 'MARKET', 
                     price: Optional[float] = None, stop_price: Optional[float] = None) -> Dict[str, Any]:
        """Preview order before placing
        
        Args:
            symbol: Stock symbol
            quantity: Number of shares
            side: 'BUY' or 'SELL'
            order_type: 'MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT'
            price: Limit price (for LIMIT orders)
            stop_price: Stop price (for STOP orders)
        """
        if not self.selected_account:
            raise Exception("No account selected")
        
        try:
            log.info(f"🔍 Previewing {side} order for {quantity} shares of {symbol}...")
            
            # Build order data (E*TRADE clientOrderId: ≤20 alphanumeric)
            try:
                from easy0DTE.modules.options_trade_id import to_broker_client_order_id

                preview_coid = to_broker_client_order_id(purpose="V", attempt=0)
            except Exception:
                preview_coid = f"PREVIEW{int(time.time())}"[:20]
            order_data = {
                'orderType': order_type,
                'clientOrderId': preview_coid,
                'Order': [{
                    'allOrNone': False,
                    'priceType': 'MARKET' if order_type == 'MARKET' else 'LIMIT',
                    'orderAction': side,
                    'quantity': quantity,
                    'symbol': symbol,
                    'orderTerm': 'GOOD_FOR_DAY'
                }]
            }
            
            # Add price if limit order
            if order_type == 'LIMIT' and price:
                order_data['Order'][0]['limitPrice'] = price
            
            # Add stop price if stop order
            if order_type in ['STOP', 'STOP_LIMIT'] and stop_price:
                order_data['Order'][0]['stopPrice'] = stop_price
            
            response = self._make_etrade_api_call(
                method='POST',
                url=f"{self.config['base_url']}/v1/accounts/{self.selected_account.account_id_key}/orders/preview",
                params=order_data
            )
            
            log.info(f"✅ Order preview successful")
            return response
            
        except Exception as e:
            log.error(f"Failed to preview order: {e}")
            raise
    
    def place_order(self, symbol: str, quantity: int, side: str, order_type: str = 'MARKET',
                   price: Optional[float] = None, stop_price: Optional[float] = None,
                   client_order_id: Optional[str] = None) -> Dict[str, Any]:
        """Place an order
        
        Args:
            symbol: Stock symbol
            quantity: Number of shares
            side: 'BUY' or 'SELL'
            order_type: 'MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT'
            price: Limit price (for LIMIT orders)
            stop_price: Stop price (for STOP orders)
            client_order_id: Custom order ID
        """
        if not self.selected_account:
            raise Exception("No account selected")
        
        try:
            log.info(f"📝 Placing {side} order for {quantity} shares of {symbol}...")
            
            # Generate client order ID if not provided (E*TRADE: ≤20 alphanumeric)
            if not client_order_id:
                try:
                    from easy0DTE.modules.options_trade_id import to_broker_client_order_id

                    client_order_id = to_broker_client_order_id(purpose="E", attempt=0)
                except Exception:
                    client_order_id = f"ETRADE{int(time.time())}"[:20]
            
            # Build order data
            order_data = {
                'orderType': order_type,
                'clientOrderId': client_order_id,
                'Order': [{
                    'allOrNone': False,
                    'priceType': 'MARKET' if order_type == 'MARKET' else 'LIMIT',
                    'orderAction': side,
                    'quantity': quantity,
                    'symbol': symbol,
                    'orderTerm': 'GOOD_FOR_DAY'
                }]
            }
            
            # Add price if limit order
            if order_type == 'LIMIT' and price:
                order_data['Order'][0]['limitPrice'] = price
            
            # Add stop price if stop order
            if order_type in ['STOP', 'STOP_LIMIT'] and stop_price:
                order_data['Order'][0]['stopPrice'] = stop_price
            
            # DIAGNOSTIC (Rev 00180): Log order details before placing
            log.info(f"📋 Order Details:")
            log.info(f"   Account: {self.selected_account.account_id_key}")
            log.info(f"   Symbol: {symbol}")
            log.info(f"   Side: {side}")
            log.info(f"   Quantity: {quantity}")
            log.info(f"   Order Type: {order_type}")
            log.info(f"   Environment: {self.environment}")
            log.info(f"   Base URL: {self.config.get('base_url', 'N/A')}")
            
            response = self._make_etrade_api_call(
                method='POST',
                url=f"{self.config['base_url']}/v1/accounts/{self.selected_account.account_id_key}/orders/place",
                params=order_data
            )
            
            # DIAGNOSTIC (Rev 00180): Log full response
            log.info(f"📬 ETrade Response: {response}")
            log.info(f"✅ Order placed successfully: {client_order_id}")
            return response
            
        except Exception as e:
            log.error(f"Failed to place order: {e}")
            raise
    
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order
        
        Args:
            order_id: Order ID to cancel
        """
        if not self.selected_account:
            raise Exception("No account selected")
        
        try:
            log.info(f"❌ Cancelling order {order_id}...")
            
            response = self._make_etrade_api_call(
                method='DELETE',
                url=f"{self.config['base_url']}/v1/accounts/{self.selected_account.account_id_key}/orders/cancel",
                params={'orderId': order_id}
            )
            
            log.info(f"✅ Order cancelled successfully")
            return response
            
        except Exception as e:
            log.error(f"Failed to cancel order: {e}")
            raise
    
    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Get order status
        
        Args:
            order_id: Order ID to check
        """
        if not self.selected_account:
            raise Exception("No account selected")
        
        try:
            log.info(f"🔍 Getting status for order {order_id}...")
            
            response = self._make_etrade_api_call(
                method='GET',
                url=f"{self.config['base_url']}/v1/accounts/{self.selected_account.account_id_key}/orders/{order_id}",
                params={}
            )
            
            log.info(f"✅ Retrieved order status")
            return response
            
        except Exception as e:
            log.error(f"Failed to get order status: {e}")
            raise
    
    def get_market_hours(self) -> Dict[str, Any]:
        """Get market hours information"""
        try:
            log.info("🕐 Fetching market hours...")
            
            response = self._make_etrade_api_call(
                method='GET',
                url=f"{self.config['base_url']}/v1/market/hours",
                params={}
            )
            
            log.info(f"✅ Retrieved market hours")
            return response
            
        except Exception as e:
            log.error(f"Failed to get market hours: {e}")
            return {}
    
    def execute_batch_orders(self, orders: List[Dict[str, Any]], max_concurrent: int = 3) -> Dict[str, Any]:
        """
        Execute multiple orders with controlled concurrency (Rev 00180T)
        
        Handles 30-40 simultaneous orders efficiently with:
        - Controlled concurrency (3 at a time)
        - Rate limiting (1.2s spacing)
        - Automatic retry on errors
        - Order verification
        
        Args:
            orders: List of order dictionaries with keys:
                    {symbol, quantity, side, order_type, signal_type, confidence}
            max_concurrent: Maximum concurrent orders (default 3)
        
        Returns:
            Dict with execution results:
                {success_count, failed_count, executed_orders, failed_orders}
        """
        try:
            import asyncio
            from concurrent.futures import ThreadPoolExecutor
            import time
            
            log.info(f"")
            log.info(f"{'='*80}")
            log.info(f"🎯 BATCH EXECUTION: {len(orders)} orders")
            log.info(f"{'='*80}")
            log.info(f"   Max Concurrent: {max_concurrent}")
            log.info(f"   Spacing: 1.2s between bursts")
            log.info(f"")
            
            executed_orders = []
            failed_orders = []
            
            # Sort by priority_rank (rank 1 first) so execution order matches signal collection ranking; fallback to confidence
            sorted_orders = sorted(
                orders,
                key=lambda x: (x.get('priority_rank', 999), -x.get('confidence', 0.85))
            )
            
            # Log prioritized queue (best trades first)
            log.info(f"📊 PRIORITIZED ORDER QUEUE (rank → best first):")
            for i, order in enumerate(sorted_orders[:10], 1):
                rk = order.get('priority_rank', '?')
                log.info(f"   {i}. #{rk} {order['symbol']} {order.get('side', 'BUY')} {order.get('quantity', 0)} (conf={order.get('confidence', 0.85):.0%})")
            if len(sorted_orders) > 10:
                log.info(f"   ... and {len(sorted_orders) - 10} more")
            log.info(f"")
            
            # Execute in batches of max_concurrent
            start_time = time.time()
            
            for i in range(0, len(sorted_orders), max_concurrent):
                batch = sorted_orders[i:i+max_concurrent]
                batch_num = i // max_concurrent + 1
                
                log.info(f"📦 Batch {batch_num}: Executing {len(batch)} orders...")
                
                # Execute this batch concurrently
                with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
                    futures = []
                    
                    for order in batch:
                        future = executor.submit(self._execute_single_order_with_retry, order)
                        futures.append((future, order))
                    
                    # Wait for all in this batch to complete
                    for future, order in futures:
                        try:
                            result = future.result(timeout=30)
                            if result.get('success'):
                                executed_orders.append(result)
                                log.info(f"   ✅ {order['symbol']}: Executed")
                            else:
                                failed_orders.append({'order': order, 'error': result.get('error', 'Unknown')})
                                log.warning(f"   ❌ {order['symbol']}: Failed - {result.get('error', 'Unknown')}")
                        except Exception as e:
                            failed_orders.append({'order': order, 'error': str(e)})
                            log.error(f"   ❌ {order['symbol']}: Exception - {e}")
                
                # Spacing between batches (rate limiting)
                if i + max_concurrent < len(sorted_orders):
                    time.sleep(1.2)
            
            duration = time.time() - start_time
            
            log.info(f"")
            log.info(f"{'='*80}")
            log.info(f"✅ BATCH EXECUTION COMPLETE")
            log.info(f"{'='*80}")
            log.info(f"   Duration: {duration:.1f}s")
            log.info(f"   Success: {len(executed_orders)}/{len(orders)} ✅")
            log.info(f"   Failed: {len(failed_orders)}/{len(orders)}")
            log.info(f"   Success Rate: {len(executed_orders)/len(orders)*100:.1f}%")
            log.info(f"")
            
            return {
                'success': True,
                'success_count': len(executed_orders),
                'failed_count': len(failed_orders),
                'executed_orders': executed_orders,
                'failed_orders': failed_orders,
                'duration': duration
            }
            
        except Exception as e:
            log.error(f"Batch execution failed: {e}", exc_info=True)
            return {
                'success': False,
                'success_count': 0,
                'failed_count': len(orders),
                'executed_orders': [],
                'failed_orders': [{'order': o, 'error': str(e)} for o in orders],
                'error': str(e)
            }
    
    def _execute_single_order_with_retry(self, order: Dict[str, Any], max_retries: int = 3) -> Dict[str, Any]:
        """
        Execute single order with retry logic
        
        Args:
            order: Order dictionary
            max_retries: Maximum retry attempts
        
        Returns:
            Execution result dict
        """
        for attempt in range(1, max_retries + 1):
            try:
                # Place order
                response = self.place_order(
                    symbol=order['symbol'],
                    quantity=order['quantity'],
                    side=order.get('side', 'BUY'),
                    order_type=order.get('order_type', 'MARKET')
                )
                
                # Check response
                if response and not isinstance(response, dict) or 'error' not in response:
                    # Success
                    order_id = self._extract_order_id_from_response(response)
                    return {
                        'success': True,
                        'symbol': order['symbol'],
                        'quantity': order['quantity'],
                        'side': order.get('side', 'BUY'),
                        'order_id': order_id,
                        'signal_type': order.get('signal_type', 'TRADE')
                    }
                else:
                    # Error response
                    error_msg = response.get('error', 'Unknown') if isinstance(response, dict) else str(response)
                    
                    # Check if retriable
                    if self._is_retriable_error(error_msg) and attempt < max_retries:
                        wait_time = 2 ** attempt
                        log.warning(f"   ⚠️ Retry {attempt}/{max_retries} for {order['symbol']}: {error_msg}")
                        time.sleep(wait_time)
                        continue
                    else:
                        return {'success': False, 'symbol': order['symbol'], 'error': error_msg}
            
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                else:
                    return {'success': False, 'symbol': order['symbol'], 'error': str(e)}
        
        return {'success': False, 'symbol': order['symbol'], 'error': 'Max retries exceeded'}
    
    def _extract_order_id_from_response(self, response) -> Optional[str]:
        """Extract order ID from E*TRADE response"""
        try:
            if isinstance(response, dict):
                for field in ['orderId', 'order_id', 'OrderId', 'id']:
                    if field in response:
                        return str(response[field])
                
                if 'OrderResponse' in response:
                    return self._extract_order_id_from_response(response['OrderResponse'])
                if 'Order' in response:
                    return self._extract_order_id_from_response(response['Order'])
            
            return None
        except:
            return None
    
    def _is_retriable_error(self, error_msg: str) -> bool:
        """Check if error should be retried"""
        retriable = ['rate limit', '429', 'timeout', 'temporary', 'unavailable', 'network', 'connection']
        error_lower = str(error_msg).lower()
        return any(keyword in error_lower for keyword in retriable)
    
    def get_option_chains(
        self,
        symbol: str,
        expiry_date: str,
        option_type: str = 'CALL',
        strike_count: int = 20,
        include_greeks: bool = True,
    ) -> Dict[str, Any]:
        """Get option chains for a symbol
        
        Args:
            symbol: Stock symbol
            expiry_date: Expiry date (YYYY-MM-DD)
            option_type: 'CALL', 'PUT', or 'CALLPUT' (both sides in one request)
        """
        try:
            side = str(option_type or "CALL").upper()
            log.info(f"🔗 Fetching option chains for {symbol} expiring {expiry_date} ({side})...")

            try:
                from modules.option_expiry_memo import is_empty as _expiry_is_empty
                from modules.option_expiry_memo import remember_empty as _expiry_remember_empty
            except Exception:
                _expiry_is_empty = None
                _expiry_remember_empty = None
            if _expiry_is_empty is not None and _expiry_is_empty(symbol, expiry_date):
                log.debug(
                    "OPTION_CHAIN_EMPTY_EXPIRY_MEMO | symbol=%s | expiry=%s | action=skip_http",
                    symbol,
                    expiry_date,
                )
                return {
                    "error": "HTTP 400",
                    "message": "10031 cached empty expiry",
                    "error_code": "10031",
                    "cached_empty": True,
                }

            # E*TRADE Market API documents expiryYear/Month/Day — not expiryDate.
            # Passing expiryDate alone is ignored → SelectedED (often weekly Friday)
            # and every "0DTE" fill becomes DTE 1–3 (Aug 4 RESEARCH session).
            raw = str(expiry_date or "").strip()
            y = m = d = 0
            if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
                try:
                    y, m, d = int(raw[0:4]), int(raw[5:7]), int(raw[8:10])
                except ValueError:
                    y = m = d = 0
            elif len(raw) == 8 and raw.isdigit():
                try:
                    y, m, d = int(raw[0:4]), int(raw[4:6]), int(raw[6:8])
                except ValueError:
                    y = m = d = 0

            params: Dict[str, Any] = {
                "symbol": symbol,
                "includeWeekly": "true",
                "noOfStrikes": int(strike_count),
                "includeGreeks": "true" if include_greeks else "false",
            }
            if y > 0 and m > 0 and d > 0:
                params["expiryYear"] = y
                params["expiryMonth"] = m
                params["expiryDay"] = d
            else:
                # Last resort — may still fall through to SelectedED.
                params["expiryDate"] = raw
                log.warning(
                    "OPTION_CHAIN_EXPIRY_PARSE_FALLBACK | symbol=%s | raw=%s | "
                    "using_expiryDate_param_may_be_ignored",
                    symbol,
                    raw,
                )
            if side in ("CALL", "PUT", "CALLPUT"):
                params["chainType"] = side
            else:
                params["optionType"] = side

            response = self._make_etrade_api_call(
                method="GET",
                url=f"{self.config['base_url']}/v1/market/optionchains",
                params=params,
            )
            if isinstance(response, dict):
                code = str(response.get("error_code") or "")
                msg = str(response.get("message") or response.get("error") or "")
                if code == "10031" or "10031" in msg or "no options for the given month" in msg.lower():
                    if _expiry_remember_empty is not None:
                        _expiry_remember_empty(symbol, expiry_date)
                elif "OptionChainResponse" in response and _expiry_is_empty is not None:
                    try:
                        from modules.option_expiry_memo import forget_empty as _expiry_forget_empty
                        _expiry_forget_empty(symbol, expiry_date)
                    except Exception:
                        pass
            
            log.info(f"✅ Retrieved option chains")
            return response
            
        except Exception as e:
            log.error(f"Failed to get option chains: {e}")
            return {}

    def get_option_expire_dates(self, symbol: str) -> List[str]:
        """Listed equity option expiries for a symbol (E*TRADE optionexpiredate)."""
        try:
            from modules.option_expiry_memo import expire_dates_for, remember_expire_dates, normalize_expiry_iso
        except Exception:
            expire_dates_for = None
            remember_expire_dates = None
            normalize_expiry_iso = None
        cached = expire_dates_for(symbol) if expire_dates_for else None
        if cached is not None:
            return cached
        try:
            response = self._make_etrade_api_call(
                method="GET",
                url=f"{self.config['base_url']}/v1/market/optionexpiredate",
                params={"symbol": str(symbol or "").strip().upper(), "expiryType": "ALL"},
            )
            dates: List[str] = []
            payload = response if isinstance(response, dict) else {}
            block = payload.get("OptionExpireDateResponse") or payload
            raw_dates = []
            if isinstance(block, dict):
                raw_dates = block.get("ExpirationDate") or block.get("expirationDate") or []
            if isinstance(raw_dates, dict):
                raw_dates = [raw_dates]
            for row in raw_dates or []:
                if not isinstance(row, dict):
                    continue
                try:
                    y = int(row.get("year") or 0)
                    m = int(row.get("month") or 0)
                    d = int(row.get("day") or 0)
                except (TypeError, ValueError):
                    continue
                if y > 0 and m > 0 and d > 0:
                    iso = f"{y:04d}-{m:02d}-{d:02d}"
                    dates.append(iso)
                elif normalize_expiry_iso is not None:
                    iso = normalize_expiry_iso(
                        row.get("expiryDate") or row.get("expirationDate") or ""
                    )
                    if iso:
                        dates.append(iso)
            dates = sorted(set(dates))
            if remember_expire_dates is not None:
                remember_expire_dates(symbol, dates)
            return dates
        except Exception as e:
            log.info("OPTION_EXPIREDATE_FAILED | symbol=%s | error=%s", symbol, e)
            return []
    
    def get_account_alerts(self) -> List[Dict[str, Any]]:
        """Get account alerts"""
        if not self.selected_account:
            raise Exception("No account selected")
        
        try:
            log.info(f"🔔 Fetching alerts for account {self.selected_account.account_id}...")
            
            response = self._make_etrade_api_call(
                method='GET',
                url=f"{self.config['base_url']}/v1/user/alerts",
                params={}
            )
            
            alerts = []
            if '<?xml version' in response:
                xml_data = response['<?xml version']
                alerts = self._parse_alerts_response(xml_data)
            
            log.info(f"✅ Retrieved {len(alerts)} alerts")
            return alerts
            
        except Exception as e:
            log.error(f"Failed to get alerts: {e}")
            return []
    
    def _parse_alerts_response(self, xml_data: str) -> List[Dict[str, Any]]:
        """Parse alerts XML response"""
        try:
            # Clean up XML data
            xml_data = xml_data.strip()
            if xml_data.startswith('"1.0" encoding="UTF-8" standalone="yes"?>'):
                xml_data = '<?xml version=' + xml_data
            elif not xml_data.startswith('<?xml version'):
                xml_data = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + xml_data
            
            root = ET.fromstring(xml_data)
            
            alerts = []
            for alert in root.findall('.//Alert'):
                alert_data = {
                    'alert_id': alert.find('alertId').text if alert.find('alertId') is not None else None,
                    'alert_name': alert.find('alertName').text if alert.find('alertName') is not None else None,
                    'alert_type': alert.find('alertType').text if alert.find('alertType') is not None else None,
                    'status': alert.find('status').text if alert.find('status') is not None else None,
                    'created_time': alert.find('createdTime').text if alert.find('createdTime') is not None else None,
                    'symbol': alert.find('symbol').text if alert.find('symbol') is not None else None,
                    'condition': alert.find('condition').text if alert.find('condition') is not None else None,
                    'trigger_price': float(alert.find('triggerPrice').text) if alert.find('triggerPrice') is not None else None,
                }
                alerts.append(alert_data)
            
            return alerts
            
        except Exception as e:
            log.error(f"Failed to parse alerts XML: {e}")
            return []

# Global instance
_etrade_trading_instance: Optional[PrimeETradeTrading] = None
_etrade_trading_lock = threading.Lock()

def get_etrade_trading(environment: str = 'prod') -> PrimeETradeTrading:
    """Get or create ETrade trading instance"""
    global _etrade_trading_instance
    with _etrade_trading_lock:
        if _etrade_trading_instance is None:
            _etrade_trading_instance = PrimeETradeTrading(environment)
        return _etrade_trading_instance

def test_etrade_trading():
    """Test ETrade trading functionality"""
    try:
        print("🔐 Testing ETrade Trading System...")
        
        etrade = get_etrade_trading('prod')
        
        print(f"✅ Environment: {etrade.environment}")
        print(f"✅ Accounts loaded: {len(etrade.accounts)}")
        
        if etrade.selected_account:
            print(f"✅ Selected account: {etrade.selected_account.account_name} ({etrade.selected_account.account_id})")
            
            # Test balance
            balance = etrade.get_account_balance()
            print(f"✅ Account value: ${balance.account_value}")
            print(f"✅ Cash available for investment: ${balance.cash_available_for_investment}")
            print(f"✅ Cash buying power: ${balance.cash_buying_power}")
            print(f"✅ Option level: {balance.option_level}")
            
            # Test portfolio
            portfolio = etrade.get_portfolio()
            print(f"✅ Portfolio positions: {len(portfolio)}")
            
            # Test quotes for first few positions
            if portfolio:
                symbols = [pos.symbol for pos in portfolio[:3]]
                quotes = etrade.get_quotes(symbols)
                print(f"✅ Retrieved quotes for {len(quotes)} symbols")
                
                # Test comprehensive market data for strategies
                if quotes:
                    test_symbol = quotes[0].symbol
                    market_data = etrade.get_market_data_for_strategy(test_symbol)
                    print(f"✅ Generated comprehensive market data for {test_symbol}:")
                    print(f"   Data quality: {market_data.get('data_quality', 'unknown')}")
                    print(f"   Historical points: {market_data.get('historical_points', 0)}")
                    print(f"   RSI: {market_data.get('rsi', 0):.2f}")
                    print(f"   MACD: {market_data.get('macd', 0):.4f}")
                    print(f"   SMA 20: {market_data.get('sma_20', 0):.2f}")
                    print(f"   Volume ratio: {market_data.get('volume_ratio', 0):.2f}")
                    print(f"   Bollinger width: {market_data.get('bollinger_width', 0):.4f}")
                    print(f"   Patterns - Doji: {market_data.get('doji', False)}, Hammer: {market_data.get('hammer', False)}")
            
            # Get summary
            summary = etrade.get_account_summary()
            print(f"✅ Account summary generated")
            
        print("🎯 ETrade trading system test completed successfully!")
        
    except Exception as e:
        log.error(f"❌ ETrade trading test failed: {e}")
        raise

if __name__ == '__main__':
    test_etrade_trading()
