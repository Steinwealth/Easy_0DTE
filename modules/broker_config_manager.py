#!/usr/bin/env python3
"""
Broker Configuration Manager

Provides centralized broker and account configuration management.
Supports E*TRADE, Interactive Brokers, and TastyTrade (Live Mode product scope)
with per-strategy account selection. Robinhood remains as legacy placeholder.

Rev 00245 (Jan 19, 2026): Multi-broker support with account mapping
Rev 00382 (Jun 5, 2026): Align with canonical BROKER + BrokerKind (etrade | interactive_brokers | tastytrade)
"""

import os
import logging
from typing import Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum

from .config_loader import get_config_value

log = logging.getLogger(__name__)

def _load_broker_config_file() -> Dict[str, str]:
    """Load broker-config.env directly if config loader not initialized"""
    config = {}
    config_file = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'configs', 'Data.env'
    )
    
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        # Remove quotes if present
                        if value.startswith('"') and value.endswith('"'):
                            value = value[1:-1]
                        elif value.startswith("'") and value.endswith("'"):
                            value = value[1:-1]
                        config[key] = value
        except Exception as e:
            log.debug(f"Could not load Data.env: {e}")
    
    return config

class BrokerType(Enum):
    """Supported broker types (canonical tokens match BrokerKind)."""
    ETRADE = 'etrade'
    INTERACTIVE_BROKERS = 'interactive_brokers'
    TASTYTRADE = 'tastytrade'
    ROBINHOOD = 'robinhood'  # legacy — not in Live product scope


def _broker_type_from_token(raw: Optional[str], *, default: BrokerType = BrokerType.ETRADE) -> BrokerType:
    """Map BROKER / BROKER_TYPE env tokens to BrokerType."""
    token = (raw or "").strip().lower()
    broker_map = {
        'etrade': BrokerType.ETRADE,
        'e*trade': BrokerType.ETRADE,
        'ib': BrokerType.INTERACTIVE_BROKERS,
        'ibkr': BrokerType.INTERACTIVE_BROKERS,
        'interactivebrokers': BrokerType.INTERACTIVE_BROKERS,
        'interactive_brokers': BrokerType.INTERACTIVE_BROKERS,
        'tastytrade': BrokerType.TASTYTRADE,
        'tasty': BrokerType.TASTYTRADE,
        'robinhood': BrokerType.ROBINHOOD,
        'rh': BrokerType.ROBINHOOD,
    }
    if token in broker_map:
        return broker_map[token]
    if token:
        log.warning("⚠️ Unknown broker type '%s', defaulting to %s", token, default.value)
    return default

@dataclass
class BrokerAccountConfig:
    """Broker account configuration for a strategy"""
    broker_type: BrokerType
    account_id: str
    account_name: Optional[str] = None
    account_id_key: Optional[str] = None  # Broker-specific account key
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'broker_type': self.broker_type.value,
            'account_id': self.account_id,
            'account_name': self.account_name,
            'account_id_key': self.account_id_key
        }

class BrokerConfigManager:
    """
    Centralized broker configuration manager
    
    Manages broker selection and account mapping for ORB Strategy and 0DTE Strategy.
    Supports easy broker switching via configuration files.
    """
    
    def __init__(self):
        """Initialize broker configuration manager"""
        # Current broker settings
        self.primary_broker: BrokerType = self._get_primary_broker()
        self.orb_broker: BrokerType = self._get_orb_broker()
        self.dte_broker: BrokerType = self._get_dte_broker()
        
        # Account configurations (lazy-loaded)
        self.orb_account: Optional[BrokerAccountConfig] = None
        self.dte_account: Optional[BrokerAccountConfig] = None
        
        log.info(f"✅ Broker Config Manager initialized")
        log.info(f"   Primary Broker: {self.primary_broker.value.upper()}")
        log.info(f"   ORB Broker: {self.orb_broker.value.upper()}")
        log.info(f"   0DTE Broker: {self.dte_broker.value.upper()}")
    
    def _get_config_value(self, key: str, default: Any = None) -> Any:
        """Get config value with fallback to direct file reading"""
        # Try get_config_value first (uses environment and config loader)
        value = get_config_value(key, None)
        if value is not None:
            return value
        
        # Fallback: Load Data.env directly (broker section merged May 2026)
        broker_config = _load_broker_config_file()
        if key in broker_config:
            return broker_config[key]
        
        # Check environment variables
        if key in os.environ:
            return os.environ[key]
        
        return default
    
    def _get_primary_broker(self) -> BrokerType:
        """Get primary broker type (BROKER canonical, BROKER_TYPE fallback)."""
        broker_str = (
            self._get_config_value('BROKER', None)
            or self._get_config_value('BROKER_TYPE', 'etrade')
        )
        try:
            return _broker_type_from_token(str(broker_str))
        except Exception as e:
            log.error("Error parsing broker type: %s, defaulting to ETrade", e)
            return BrokerType.ETRADE
    
    def _get_orb_broker(self) -> BrokerType:
        """Get ORB Strategy broker (defaults to primary broker)"""
        orb_broker_str = self._get_config_value('ORB_BROKER_TYPE', None)
        if orb_broker_str:
            try:
                return _broker_type_from_token(str(orb_broker_str), default=self.primary_broker)
            except Exception:
                pass
        return self.primary_broker
    
    def _get_dte_broker(self) -> BrokerType:
        """Get 0DTE Strategy broker (defaults to primary broker)"""
        dte_broker_str = self._get_config_value('0DTE_BROKER_TYPE', None)
        if dte_broker_str:
            try:
                return _broker_type_from_token(str(dte_broker_str), default=self.primary_broker)
            except Exception:
                pass
        return self.primary_broker
    
    def get_orb_account_config(self) -> BrokerAccountConfig:
        """
        Get ORB Strategy account configuration
        
        Returns:
            BrokerAccountConfig for ORB Strategy
        """
        broker = self.orb_broker
        
        # Get account ID from config
        if broker == BrokerType.ETRADE:
            account_id = self._get_config_value(
                'ETRADE_ORB_ACCOUNT_ID',
                self._get_config_value('ETRADE_DEFAULT_ACCOUNT_ID', None)
            )
            account_name = self._get_config_value('ETRADE_ORB_ACCOUNT_NAME', None)
            
        elif broker == BrokerType.INTERACTIVE_BROKERS:
            account_id = self._get_config_value(
                'IB_ORB_ACCOUNT_ID',
                self._get_config_value('IB_DEFAULT_ACCOUNT_ID', None)
            )
            account_name = self._get_config_value('IB_ORB_ACCOUNT_NAME', None)
            
        elif broker == BrokerType.TASTYTRADE:
            account_id = self._get_config_value(
                'TASTYTRADE_ORB_ACCOUNT_ID',
                self._get_config_value('TASTYTRADE_DEFAULT_ACCOUNT_ID', None)
            )
            account_name = self._get_config_value('TASTYTRADE_ORB_ACCOUNT_NAME', None)

        elif broker == BrokerType.ROBINHOOD:
            account_id = self._get_config_value(
                'RH_ORB_ACCOUNT_ID',
                self._get_config_value('RH_DEFAULT_ACCOUNT_ID', None)
            )
            account_name = self._get_config_value('RH_ORB_ACCOUNT_NAME', None)
            
        else:
            log.error(f"❌ Unsupported broker type: {broker}")
            raise ValueError(f"Unsupported broker type: {broker}")
        
        if not account_id:
            log.warning(f"⚠️ No account ID configured for ORB Strategy with broker {broker.value}")
            return None
        
        config = BrokerAccountConfig(
            broker_type=broker,
            account_id=account_id,
            account_name=account_name
        )
        
        self.orb_account = config
        log.info(f"✅ ORB Strategy account: {account_id} ({account_name or 'Unnamed'}) on {broker.value.upper()}")
        return config
    
    def get_dte_account_config(self) -> BrokerAccountConfig:
        """
        Get 0DTE Strategy account configuration
        
        Returns:
            BrokerAccountConfig for 0DTE Strategy
        """
        broker = self.dte_broker
        
        # Get account ID from config
        if broker == BrokerType.ETRADE:
            account_id = self._get_config_value(
                'ETRADE_0DTE_ACCOUNT_ID',
                self._get_config_value('ETRADE_DEFAULT_ACCOUNT_ID', None)
            )
            # Also check easy0DTE/configs/0dte.env for backward compatibility
            if not account_id:
                account_id = self._get_config_value('0DTE_ETRADE_ACCOUNT_ID', None)
            account_name = self._get_config_value('ETRADE_0DTE_ACCOUNT_NAME', None)
            
        elif broker == BrokerType.INTERACTIVE_BROKERS:
            account_id = self._get_config_value(
                'IB_0DTE_ACCOUNT_ID',
                self._get_config_value('IB_DEFAULT_ACCOUNT_ID', None)
            )
            account_name = self._get_config_value('IB_0DTE_ACCOUNT_NAME', None)
            
        elif broker == BrokerType.TASTYTRADE:
            account_id = self._get_config_value(
                'TASTYTRADE_0DTE_ACCOUNT_ID',
                self._get_config_value('TASTYTRADE_DEFAULT_ACCOUNT_ID', None)
            )
            account_name = self._get_config_value('TASTYTRADE_0DTE_ACCOUNT_NAME', None)

        elif broker == BrokerType.ROBINHOOD:
            account_id = self._get_config_value(
                'RH_0DTE_ACCOUNT_ID',
                self._get_config_value('RH_DEFAULT_ACCOUNT_ID', None)
            )
            account_name = self._get_config_value('RH_0DTE_ACCOUNT_NAME', None)
            
        else:
            log.error(f"❌ Unsupported broker type: {broker}")
            raise ValueError(f"Unsupported broker type: {broker}")
        
        if not account_id:
            log.warning(f"⚠️ No account ID configured for 0DTE Strategy with broker {broker.value}")
            return None
        
        config = BrokerAccountConfig(
            broker_type=broker,
            account_id=account_id,
            account_name=account_name
        )
        
        self.dte_account = config
        log.info(f"✅ 0DTE Strategy account: {account_id} ({account_name or 'Unnamed'}) on {broker.value.upper()}")
        return config
    
    def get_broker_environment(self, broker_type: Optional[BrokerType] = None) -> str:
        """
        Get broker environment (sandbox/paper vs live/prod).
        E*TRADE: Always returns 'prod' — sandbox is deprecated; data and API use production tokens only.
        
        Args:
            broker_type: Broker type (defaults to primary broker)
        
        Returns:
            Environment string ('sandbox', 'paper', 'prod', 'live')
        """
        if broker_type is None:
            broker_type = self.primary_broker
        
        if broker_type == BrokerType.ETRADE:
            # E*TRADE: Production only for data and API. Sandbox deprecated; config ETRADE_ENVIRONMENT ignored for API.
            return 'prod'
            
        elif broker_type == BrokerType.INTERACTIVE_BROKERS:
            env = self._get_config_value('IB_ENVIRONMENT', 'paper')
            return env
            
        elif broker_type == BrokerType.TASTYTRADE:
            return self._get_config_value('TASTYTRADE_ENVIRONMENT', 'demo')

        elif broker_type == BrokerType.ROBINHOOD:
            env = self._get_config_value('ROBINHOOD_ENVIRONMENT', 'sandbox')
            return env
        
        return 'sandbox'  # Default to sandbox
    
    def is_broker_enabled(self, broker_type: Optional[BrokerType] = None) -> bool:
        """
        Check if broker is enabled
        
        Args:
            broker_type: Broker type (defaults to primary broker)
        
        Returns:
            True if broker is enabled
        """
        if broker_type is None:
            broker_type = self.primary_broker
        
        if broker_type == BrokerType.ETRADE:
            return self._get_config_value('ETRADE_ENABLED', True)
        elif broker_type == BrokerType.INTERACTIVE_BROKERS:
            return self._get_config_value('IB_ENABLED', False)
        elif broker_type == BrokerType.TASTYTRADE:
            return self._get_config_value('TASTYTRADE_ENABLED', False)

        elif broker_type == BrokerType.ROBINHOOD:
            return self._get_config_value('ROBINHOOD_ENABLED', False)
        
        return False
    
    def get_config_summary(self) -> Dict[str, Any]:
        """
        Get configuration summary
        
        Returns:
            Dictionary with broker and account configuration summary
        """
        orb_account = self.get_orb_account_config()
        dte_account = self.get_dte_account_config()
        
        return {
            'primary_broker': self.primary_broker.value,
            'orb_broker': self.orb_broker.value,
            'dte_broker': self.dte_broker.value,
            'orb_account': orb_account.to_dict() if orb_account else None,
            'dte_account': dte_account.to_dict() if dte_account else None,
            'orb_environment': self.get_broker_environment(self.orb_broker),
            'dte_environment': self.get_broker_environment(self.dte_broker)
        }


# Global instance
_broker_config_manager: Optional[BrokerConfigManager] = None

def get_broker_config_manager() -> BrokerConfigManager:
    """Get global broker configuration manager instance"""
    global _broker_config_manager
    if _broker_config_manager is None:
        _broker_config_manager = BrokerConfigManager()
    return _broker_config_manager
