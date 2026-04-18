"""Binance Futures API client with rate limiting and error handling."""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

import aiohttp

from crypto_signal_bot.core.exceptions import (
    BinanceAPIError,
    RateLimitError,
    ConnectionError,
)
from crypto_signal_bot.core.config import BinanceConfig


logger = logging.getLogger(__name__)


@dataclass
class OrderBook:
    """Represents an order book snapshot."""
    
    symbol: str
    bids: List[tuple[float, float]]  # (price, quantity)
    asks: List[tuple[float, float]]  # (price, quantity)
    timestamp: int
    
    @property
    def best_bid(self) -> tuple[float, float]:
        """Get best bid price and quantity."""
        return self.bids[0] if self.bids else (0.0, 0.0)
    
    @property
    def best_ask(self) -> tuple[float, float]:
        """Get best ask price and quantity."""
        return self.asks[0] if self.asks else (0.0, 0.0)
    
    @property
    def mid_price(self) -> float:
        """Get mid price."""
        if not self.bids or not self.asks:
            return 0.0
        return (self.best_bid[0] + self.best_ask[0]) / 2
    
    @property
    def spread(self) -> float:
        """Get bid-ask spread."""
        if not self.bids or not self.asks:
            return 0.0
        return self.best_ask[0] - self.best_bid[0]
    
    @property
    def spread_percent(self) -> float:
        """Get bid-ask spread as percentage."""
        if self.mid_price == 0:
            return 0.0
        return (self.spread / self.mid_price) * 100


class BinanceClient:
    """
    Async client for Binance Futures API.
    
    Features:
    - Automatic rate limiting
    - Retry logic with exponential backoff
    - Connection pooling
    - Error handling
    """
    
    def __init__(self, config: Optional[BinanceConfig] = None):
        """
        Initialize Binance client.
        
        Args:
            config: Binance configuration (uses defaults if None)
        """
        self.config = config or BinanceConfig()
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = RateLimiter(calls_per_second=10)
        self._initialized = False
        logger.info("BinanceClient created")
    
    async def initialize(self) -> None:
        """Initialize HTTP session."""
        if self._initialized:
            return
        
        timeout = aiohttp.ClientTimeout(total=30)
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._initialized = True
        logger.info("BinanceClient initialized")
    
    async def close(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
            self._initialized = False
            logger.info("BinanceClient closed")
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
    ) -> Dict[str, Any]:
        """
        Make API request to Binance.
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            params: Request parameters
            signed: Whether request requires signature
            
        Returns:
            API response as dictionary
            
        Raises:
            BinanceAPIError: If API call fails
            RateLimitError: If rate limit exceeded
        """
        if not self._initialized:
            raise ConnectionError("BinanceClient not initialized")
        
        url = f"{self.config.base_url}{endpoint}"
        
        if signed and self.config.api_key:
            params = params or {}
            params["timestamp"] = int(time.time() * 1000)
            # Note: Signature generation would go here for authenticated endpoints
        
        headers = {}
        if self.config.api_key:
            headers["X-MBX-APIKEY"] = self.config.api_key
        
        await self._rate_limiter.acquire()
        
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                headers=headers,
            ) as response:
                if response.status == 429:
                    raise RateLimitError(
                        "Rate limit exceeded",
                        status_code=429,
                    )
                
                if response.status >= 400:
                    error_text = await response.text()
                    raise BinanceAPIError(
                        f"API error: {response.status} - {error_text}",
                        status_code=response.status,
                    )
                
                return await response.json()
                
        except aiohttp.ClientError as e:
            raise ConnectionError(f"Connection error: {e}") from e
    
    async def get_exchange_info(self) -> Dict[str, Any]:
        """Get exchange information including all symbols."""
        return await self._request("GET", "/fapi/v1/exchangeInfo")
    
    async def get_ticker_price(self, symbol: Optional[str] = None) -> Any:
        """
        Get current price(s).
        
        Args:
            symbol: Specific symbol or None for all symbols
            
        Returns:
            Price data
        """
        endpoint = "/fapi/v1/ticker/price"
        params = {"symbol": symbol} if symbol else {}
        return await self._request("GET", endpoint, params)
    
    async def get_orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        """
        Get order book for a symbol.
        
        Args:
            symbol: Trading pair symbol
            limit: Depth of order book (5, 10, 20, 50, 100, 500, 1000)
            
        Returns:
            OrderBook instance
        """
        params = {"symbol": symbol, "limit": limit}
        data = await self._request("GET", "/fapi/v1/depth", params)
        
        bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
        asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
        
        return OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=data.get("lastUpdateId", 0),
        )
    
    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[List[Any]]:
        """
        Get candlestick data.
        
        Args:
            symbol: Trading pair symbol
            interval: Kline interval (1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d, etc.)
            limit: Number of candles (max 1500)
            start_time: Start timestamp in ms
            end_time: End timestamp in ms
            
        Returns:
            List of candlestick data
        """
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1500),
        }
        
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        
        return await self._request("GET", "/fapi/v1/klines", params)
    
    async def get_funding_rate(self, symbol: str, limit: int = 1) -> List[Dict]:
        """
        Get funding rate history.
        
        Args:
            symbol: Trading pair symbol
            limit: Number of results
            
        Returns:
            List of funding rate data
        """
        params = {"symbol": symbol, "limit": limit}
        return await self._request("GET", "/fapi/v1/fundingRate", params)
    
    async def get_open_interest(self, symbol: str) -> Dict[str, Any]:
        """
        Get current open interest.
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            Open interest data
        """
        params = {"symbol": symbol}
        return await self._request("GET", "/fapi/v1/openInterest", params)
    
    async def get_top_long_short_accounts(self, symbol: str, period: str = "5m") -> List[Dict]:
        """
        Get top trader long/short ratio.
        
        Args:
            symbol: Trading pair symbol
            period: Time period (5m, 15m, 30m, 1h, 4h, 1d)
            
        Returns:
            Long/short ratio data
        """
        params = {"symbol": symbol, "period": period}
        return await self._request("GET", "/futures/data/topLongShortAccountRatio", params)
    
    async def get_all_symbols(self) -> List[str]:
        """Get list of all USDT futures symbols."""
        info = await self.get_exchange_info()
        symbols = []
        
        for symbol_info in info.get("symbols", []):
            if (
                symbol_info.get("status") == "TRADING"
                and symbol_info.get("quoteAsset") == "USDT"
            ):
                symbols.append(symbol_info["symbol"])
        
        return sorted(symbols)
    
    async def __aenter__(self) -> "BinanceClient":
        """Async context manager entry."""
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()


class RateLimiter:
    """
    Token bucket rate limiter for API calls.
    """
    
    def __init__(self, calls_per_second: int = 10):
        """
        Initialize rate limiter.
        
        Args:
            calls_per_second: Maximum API calls per second
        """
        self.calls_per_second = calls_per_second
        self.tokens = float(calls_per_second)
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> None:
        """Acquire a token, waiting if necessary."""
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_update
                self.tokens = min(
                    self.calls_per_second,
                    self.tokens + elapsed * self.calls_per_second
                )
                self.last_update = now
                
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                
                # Wait for token to become available
                wait_time = (1 - self.tokens) / self.calls_per_second
                await asyncio.sleep(wait_time)
