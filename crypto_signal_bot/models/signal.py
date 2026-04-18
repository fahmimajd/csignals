"""Data models for signals and trades."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any


class SignalType(str, Enum):
    """Type of trading signal."""
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class SignalStrength(str, Enum):
    """Signal strength levels."""
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"
    VERY_STRONG = "VERY_STRONG"


class TradeStatus(str, Enum):
    """Trade lifecycle status."""
    PENDING = "PENDING"
    OPEN = "OPEN"
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    TRAILING_STOP = "TRAILING_STOP"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


@dataclass
class Signal:
    """
    Represents a trading signal.
    
    Attributes:
        symbol: Trading pair (e.g., BTCUSDT)
        signal_type: LONG or SHORT
        strength: Signal strength
        score: Composite score (1-5)
        entry_price: Recommended entry price
        tp: Take profit level
        sl: Stop loss level
        rr_ratio: Risk/reward ratio
        timestamp: When signal was generated
        expires_at: When signal becomes invalid
        metadata: Additional signal data
    """
    
    symbol: str
    signal_type: SignalType
    strength: SignalStrength
    score: int
    entry_price: float
    tp: float
    sl: float
    rr_ratio: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None
    
    @property
    def is_expired(self) -> bool:
        """Check if signal has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at
    
    @property
    def time_to_expiry(self) -> Optional[float]:
        """Get seconds until expiry."""
        if self.expires_at is None:
            return None
        return (self.expires_at - datetime.utcnow()).total_seconds()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "symbol": self.symbol,
            "signal_type": self.signal_type.value,
            "strength": self.strength.value,
            "score": self.score,
            "entry_price": self.entry_price,
            "tp": self.tp,
            "sl": self.sl,
            "rr_ratio": self.rr_ratio,
            "timestamp": self.timestamp.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_expired": self.is_expired,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Signal":
        """Create Signal from dictionary."""
        return cls(
            id=data.get("id"),
            symbol=data["symbol"],
            signal_type=SignalType(data["signal_type"]),
            strength=SignalStrength(data["strength"]),
            score=data["score"],
            entry_price=float(data["entry_price"]),
            tp=float(data["tp"]),
            sl=float(data["sl"]),
            rr_ratio=float(data["rr_ratio"]),
            timestamp=datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else datetime.utcnow(),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            metadata=data.get("metadata", {}),
        )


@dataclass
class Trade:
    """
    Represents an executed trade.
    
    Attributes:
        symbol: Trading pair
        signal_id: Reference to originating signal
        status: Current trade status
        entry_price: Actual entry price
        quantity: Position size
        tp: Take profit level
        sl: Stop loss level
        trailing_stop_active: Whether trailing stop is active
        trailing_stop_price: Current trailing stop price
        opened_at: When trade was opened
        closed_at: When trade was closed
        close_price: Actual close price
        pnl: Profit/loss in quote currency
        pnl_percent: Profit/loss percentage
        exit_reason: Why trade was closed
        metadata: Additional trade data
    """
    
    symbol: str
    signal_id: int
    status: TradeStatus
    entry_price: float
    quantity: float
    tp: float
    sl: float
    trailing_stop_active: bool = False
    trailing_stop_price: Optional[float] = None
    opened_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    close_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_percent: Optional[float] = None
    exit_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None
    
    @property
    def is_open(self) -> bool:
        """Check if trade is still open."""
        return self.status in [TradeStatus.PENDING, TradeStatus.OPEN]
    
    @property
    def duration_seconds(self) -> Optional[float]:
        """Get trade duration in seconds."""
        end_time = self.closed_at or datetime.utcnow()
        return (end_time - self.opened_at).total_seconds()
    
    @property
    def duration_hours(self) -> Optional[float]:
        """Get trade duration in hours."""
        if self.duration_seconds is None:
            return None
        return self.duration_seconds / 3600
    
    def update_pnl(self, current_price: float) -> None:
        """
        Update PnL based on current price.
        
        Args:
            current_price: Current market price
        """
        if self.status == TradeStatus.LONG:
            price_diff = current_price - self.entry_price
        else:  # SHORT
            price_diff = self.entry_price - current_price
        
        self.pnl = price_diff * self.quantity
        self.pnl_percent = (price_diff / self.entry_price) * 100
    
    def close(
        self,
        close_price: float,
        status: TradeStatus,
        exit_reason: str,
    ) -> None:
        """
        Close the trade.
        
        Args:
            close_price: Price at which trade was closed
            status: Final trade status
            exit_reason: Reason for closing
        """
        self.close_price = close_price
        self.closed_at = datetime.utcnow()
        self.status = status
        self.exit_reason = exit_reason
        
        # Calculate final PnL
        if status == TradeStatus.LONG:
            price_diff = close_price - self.entry_price
        else:  # SHORT
            price_diff = self.entry_price - close_price
        
        self.pnl = price_diff * self.quantity
        self.pnl_percent = (price_diff / self.entry_price) * 100
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "symbol": self.symbol,
            "signal_id": self.signal_id,
            "status": self.status.value,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "tp": self.tp,
            "sl": self.sl,
            "trailing_stop_active": self.trailing_stop_active,
            "trailing_stop_price": self.trailing_stop_price,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "close_price": self.close_price,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "exit_reason": self.exit_reason,
            "duration_hours": self.duration_hours,
            "is_open": self.is_open,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Trade":
        """Create Trade from dictionary."""
        return cls(
            id=data.get("id"),
            symbol=data["symbol"],
            signal_id=data["signal_id"],
            status=TradeStatus(data["status"]),
            entry_price=float(data["entry_price"]),
            quantity=float(data["quantity"]),
            tp=float(data["tp"]),
            sl=float(data["sl"]),
            trailing_stop_active=data.get("trailing_stop_active", False),
            trailing_stop_price=data.get("trailing_stop_price"),
            opened_at=datetime.fromisoformat(data["opened_at"]) if data.get("opened_at") else datetime.utcnow(),
            closed_at=datetime.fromisoformat(data["closed_at"]) if data.get("closed_at") else None,
            close_price=data.get("close_price"),
            pnl=data.get("pnl"),
            pnl_percent=data.get("pnl_percent"),
            exit_reason=data.get("exit_reason"),
            metadata=data.get("metadata", {}),
        )
