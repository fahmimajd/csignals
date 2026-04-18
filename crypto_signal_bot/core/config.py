"""Validated configuration for the crypto signal bot."""

import os
from typing import List, Optional, Union
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables
load_dotenv()


@dataclass
class BinanceConfig:
    """Binance API configuration."""
    
    api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET", ""))
    testnet: bool = field(
        default_factory=lambda: os.getenv("BINANCE_TESTNET", "False").lower() == "true"
    )
    
    @property
    def base_url(self) -> str:
        """Get appropriate Binance API base URL."""
        if self.testnet:
            return "https://testnet.binancefuture.com"
        return "https://fapi.binance.com"
    
    @property
    def ws_url(self) -> str:
        """Get appropriate Binance WebSocket URL."""
        if self.testnet:
            return "wss://stream.binancefuture.com"
        return "wss://fstream.binance.com"
    
    def validate(self) -> None:
        """Validate Binance configuration."""
        if not self.api_key or not self.api_secret:
            # Allow running without API keys for read-only operations
            pass


@dataclass
class SymbolConfig:
    """Symbol monitoring configuration."""
    
    symbols: Union[str, List[str]] = field(default="ALL")
    pattern: str = "USDT"
    top_n_by_volume: int = 20
    
    def get_symbols_list(self, all_symbols: List[str]) -> List[str]:
        """Get final list of symbols to monitor."""
        if isinstance(self.symbols, list):
            return self.symbols
        
        if self.symbols.upper() == "ALL":
            # Filter by pattern and take top N
            filtered = [s for s in all_symbols if self.pattern in s]
            return filtered[:self.top_n_by_volume]
        
        # Pattern matching
        return [s for s in all_symbols if self.symbols in s]


@dataclass
class ThresholdConfig:
    """Signal threshold configuration."""
    
    strong_threshold: int = 3
    whale_threshold: int = 200_000
    liquidation_threshold: int = 100_000
    ob_imbalance_threshold: float = 0.15
    whale_window_minutes: int = 10
    liquidation_window_minutes: int = 5
    
    def validate(self) -> None:
        """Validate threshold values."""
        if self.strong_threshold < 1:
            raise ValueError("strong_threshold must be at least 1")
        if self.whale_threshold < 0:
            raise ValueError("whale_threshold must be non-negative")
        if self.liquidation_threshold < 0:
            raise ValueError("liquidation_threshold must be non-negative")
        if not 0 <= self.ob_imbalance_threshold <= 1:
            raise ValueError("ob_imbalance_threshold must be between 0 and 1")


@dataclass
class RiskConfig:
    """Risk management configuration."""
    
    atr_period: int = 14
    atr_timeframe: str = "1h"
    sl_multiplier: float = 1.5
    tp_multiplier: float = 3.0
    trail_trigger_atr: float = 1.0
    trail_distance_atr: float = 0.75
    min_rr_ratio: float = 1.2
    
    def validate(self) -> None:
        """Validate risk parameters."""
        if self.atr_period < 1:
            raise ValueError("atr_period must be at least 1")
        if self.sl_multiplier <= 0:
            raise ValueError("sl_multiplier must be positive")
        if self.tp_multiplier <= 0:
            raise ValueError("tp_multiplier must be positive")
        if self.min_rr_ratio < 0:
            raise ValueError("min_rr_ratio must be non-negative")


@dataclass
class TelegramConfig:
    """Telegram notification configuration."""
    
    bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    enabled: bool = field(default=False)
    
    def __post_init__(self):
        """Set enabled based on token presence."""
        self.enabled = bool(self.bot_token and self.chat_id)


@dataclass
class ConfirmationConfig:
    """Signal confirmation configuration."""
    
    confirmation_minutes: int = 1
    confirmation_candles: int = 2
    signal_cooldown_minutes: int = 15


@dataclass
class HoldDurationConfig:
    """Hold duration configuration."""
    
    base_hours: int = 6
    min_hold_hours: int = 2
    max_hold_hours: int = 12
    
    def validate(self) -> None:
        """Validate hold duration parameters."""
        if self.min_hold_hours < 0:
            raise ValueError("min_hold_hours must be non-negative")
        if self.max_hold_hours < self.min_hold_hours:
            raise ValueError("max_hold_hours must be >= min_hold_hours")
        if not (self.min_hold_hours <= self.base_hours <= self.max_hold_hours):
            raise ValueError("base_hours must be between min and max")


@dataclass
class DatabaseConfig:
    """Database configuration."""
    
    path: str = field(default_factory=lambda: os.getenv("DB_PATH", "signals.db"))
    pool_size: int = 10
    max_overflow: int = 5
    
    @property
    def is_postgresql(self) -> bool:
        """Check if using PostgreSQL."""
        return self.path.startswith("postgresql://")


@dataclass
class WebServerConfig:
    """Web server configuration."""
    
    host: str = "0.0.0.0"
    port: int = 5000
    debug: bool = False


@dataclass
class LoggingConfig:
    """Logging configuration."""
    
    level: str = "INFO"
    file: str = "logs/crypto_signal.log"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    max_bytes: int = 10_485_760  # 10MB
    backup_count: int = 5
    
    def __post_init__(self):
        """Ensure log directory exists."""
        log_dir = Path(self.file).parent
        log_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class Config:
    """Main configuration class."""
    
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    symbols: SymbolConfig = field(default_factory=SymbolConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    confirmation: ConfirmationConfig = field(default_factory=ConfirmationConfig)
    hold_duration: HoldDurationConfig = field(default_factory=HoldDurationConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    web_server: WebServerConfig = field(default_factory=WebServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    def validate(self) -> None:
        """Validate all configuration sections."""
        self.binance.validate()
        self.thresholds.validate()
        self.risk.validate()
        self.hold_duration.validate()
    
    @classmethod
    def load(cls) -> "Config":
        """Load configuration from environment and defaults."""
        config = cls()
        config.validate()
        return config


# Global configuration instance
config = Config.load()
