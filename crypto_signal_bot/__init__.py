"""Crypto Signal Bot Package"""

__version__ = "2.0.0"
__author__ = "Crypto Signal Team"

from crypto_signal_bot.core.config import Config
from crypto_signal_bot.core.events import EventBus, EventType
from crypto_signal_bot.core.application import CryptoSignalApp


__all__ = [
    "Config",
    "EventBus",
    "EventType",
    "CryptoSignalApp",
]
