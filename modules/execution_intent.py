from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Optional, Dict, Any, Union


@dataclass
class ExecutionIntent:
    symbol: str
    side: str  # "LONG"
    strategy_type: str  # "ORB_SO", "lotto", "long_call", etc.
    asset_type: str  # "equity" or "option"
    structure_type: str  # "equity_long", "single_leg_long_call", "debit_spread", etc.
    confidence: float
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def execution_intent_to_dict(intent: Union[ExecutionIntent, Dict[str, Any], None]) -> Optional[Dict[str, Any]]:
    """JSON-safe dict for ledger / snapshot persistence."""
    if intent is None:
        return None
    if isinstance(intent, dict):
        return dict(intent)
    if is_dataclass(intent):
        return asdict(intent)
    return None
