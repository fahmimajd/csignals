"""Custom exceptions for the crypto signal bot."""

from typing import Optional, Dict, Any


class CryptoSignalError(Exception):
    """Base exception for all crypto signal bot errors."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for logging."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "details": self.details,
        }


class ConfigurationError(CryptoSignalError):
    """Raised when configuration is invalid."""
    pass


class BinanceAPIError(CryptoSignalError):
    """Raised when Binance API call fails."""
    
    def __init__(self, message: str, status_code: Optional[int] = None, **kwargs):
        super().__init__(message, kwargs)
        self.status_code = status_code


class DatabaseError(CryptoSignalError):
    """Raised when database operation fails."""
    pass


class SignalProcessingError(CryptoSignalError):
    """Raised when signal processing fails."""
    pass


class WebSocketError(CryptoSignalError):
    """Raised when WebSocket connection fails."""
    pass


class ValidationError(CryptoSignalError):
    """Raised when data validation fails."""
    pass


class RateLimitError(BinanceAPIError):
    """Raised when rate limit is exceeded."""
    pass


class ConnectionError(CryptoSignalError):
    """Raised when connection to external service fails."""
    pass
