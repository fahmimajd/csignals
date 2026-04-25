"""
technical_indicators.py - Technical Indicators for Signal Confirmation

Adds RSI, MACD, and Stochastic indicators to improve signal accuracy.
These indicators work as additional confirmation layers before signals are executed.

Features:
  - RSI (Relative Strength Index): Identifies overbought/oversold conditions
  - MACD (Moving Average Convergence Divergence): Trend momentum indicator
  - Stochastic Oscillator: Momentum indicator comparing closing price to price range
  - Divergence detection: Spot potential reversals

All indicators fetch data from Binance Futures API and cache results.
"""

import asyncio
import time
import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from collections import deque

import numpy as np
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class IndicatorResult:
    """Result of technical indicator calculation."""
    rsi: float  # 0-100
    macd_line: float
    macd_signal: float
    macd_histogram: float
    stoch_k: float  # 0-100
    stoch_d: float  # 0-100
    timestamp: float


@dataclass
class SignalConfirmation:
    """Confirmation status from technical indicators."""
    rsi_signal: str  # "OVERBOUGHT", "OVERSOLD", "NEUTRAL"
    macd_signal: str  # "BULLISH", "BEARISH", "NEUTRAL"
    stoch_signal: str  # "OVERBOUGHT", "OVERSOLD", "NEUTRAL"
    divergence: Optional[str]  # "BULLISH_DIV", "BEARISH_DIV", None
    overall_bias: str  # "BULLISH", "BEARISH", "NEUTRAL"
    confidence: float  # 0.0-1.0


class TechnicalIndicators:
    """
    Technical indicator calculator with caching.
    
    Fetches OHLCV data from Binance and calculates:
      - RSI (14-period)
      - MACD (12, 26, 9)
      - Stochastic (14, 3, 3)
      - Divergence detection (RSI & Price)
    
    Cache TTL: 2 minutes for real-time accuracy
    """
    
    CACHE_TTL = 120  # 2 minutes
    BINANCE_KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"
    
    # RSI thresholds
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD = 30
    
    # Stochastic thresholds
    STOCH_OVERBOUGHT = 80
    STOCH_OVERSOLD = 20
    
    def __init__(self):
        self._cache: Dict[str, Tuple[IndicatorResult, float]] = {}
        self._price_history: Dict[str, deque] = {}  # For divergence detection
        self._max_history = 50  # Keep last 50 candles for divergence
    
    async def get_indicators(self, symbol: str, interval: str = "1h") -> IndicatorResult:
        """
        Get all technical indicators for a symbol.
        
        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            interval: Candle interval (default: "1h")
            
        Returns:
            IndicatorResult with RSI, MACD, Stochastic values
            
        If fetch fails, returns neutral values (RSI=50, MACD=0, etc.)
        """
        now = time.time()
        
        # Check cache
        if symbol in self._cache:
            result, cached_at = self._cache[symbol]
            if now - cached_at < self.CACHE_TTL:
                logger.debug(f"[{symbol}] Using cached indicators")
                return result
        
        # Fetch fresh data
        try:
            result = await self._fetch_and_calculate(symbol, interval)
            self._cache[symbol] = (result, now)
            
            # Update price history for divergence detection
            self._update_price_history(symbol, result)
            
            logger.info(
                f"[{symbol}] RSI: {result.rsi:.1f}, "
                f"MACD: {result.macd_histogram:+.4f}, "
                f"Stoch: {result.stoch_k:.1f}/{result.stoch_d:.1f}"
            )
            return result
            
        except Exception as e:
            logger.warning(f"[{symbol}] Indicator calculation failed: {e}")
            # Return neutral values on error
            return IndicatorResult(
                rsi=50.0,
                macd_line=0.0,
                macd_signal=0.0,
                macd_histogram=0.0,
                stoch_k=50.0,
                stoch_d=50.0,
                timestamp=now
            )
    
    async def get_confirmation(self, symbol: str, signal_type: str) -> SignalConfirmation:
        """
        Get confirmation status for a proposed signal.
        
        Args:
            symbol: Trading pair
            signal_type: Proposed signal ("STRONG_LONG" or "STRONG_SHORT")
            
        Returns:
            SignalConfirmation with bias and confidence score
        """
        indicators = await self.get_indicators(symbol)
        
        # Analyze RSI
        if indicators.rsi > self.RSI_OVERBOUGHT:
            rsi_signal = "OVERBOUGHT"
        elif indicators.rsi < self.RSI_OVERSOLD:
            rsi_signal = "OVERSOLD"
        else:
            rsi_signal = "NEUTRAL"
        
        # Analyze MACD
        if indicators.macd_histogram > 0:
            macd_signal = "BULLISH"
        elif indicators.macd_histogram < 0:
            macd_signal = "BEARISH"
        else:
            macd_signal = "NEUTRAL"
        
        # Analyze Stochastic
        if indicators.stoch_k > self.STOCH_OVERBOUGHT:
            stoch_signal = "OVERBOUGHT"
        elif indicators.stoch_k < self.STOCH_OVERSOLD:
            stoch_signal = "OVERSOLD"
        else:
            stoch_signal = "NEUTRAL"
        
        # Detect divergence
        divergence = self._detect_divergence(symbol, signal_type)
        
        # Calculate overall bias and confidence
        bias, confidence = self._calculate_bias(
            signal_type, rsi_signal, macd_signal, stoch_signal, divergence
        )
        
        return SignalConfirmation(
            rsi_signal=rsi_signal,
            macd_signal=macd_signal,
            stoch_signal=stoch_signal,
            divergence=divergence,
            overall_bias=bias,
            confidence=confidence
        )
    
    async def _fetch_and_calculate(self, symbol: str, interval: str) -> IndicatorResult:
        """Fetch kline data and calculate all indicators."""
        params = {
            "symbol": symbol.replace("/", ""),
            "interval": interval,
            "limit": 100  # Need enough data for MACD (26+ periods)
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(self.BINANCE_KLINE_URL, params=params) as response:
                if response.status != 200:
                    raise Exception(f"Binance API error: {response.status}")
                data = await response.json()
        
        if not data or len(data) < 30:
            raise Exception("Insufficient candle data")
        
        # Parse OHLCV
        closes = np.array([float(candle[4]) for candle in data])
        highs = np.array([float(candle[2]) for candle in data])
        lows = np.array([float(candle[3]) for candle in data])
        
        # Calculate indicators
        rsi = self._calculate_rsi(closes, period=14)
        macd_line, macd_signal_line, macd_hist = self._calculate_macd(closes)
        stoch_k, stoch_d = self._calculate_stochastic(highs, lows, closes)
        
        return IndicatorResult(
            rsi=float(rsi[-1]) if not np.isnan(rsi[-1]) else 50.0,
            macd_line=float(macd_line[-1]) if not np.isnan(macd_line[-1]) else 0.0,
            macd_signal=float(macd_signal_line[-1]) if not np.isnan(macd_signal_line[-1]) else 0.0,
            macd_histogram=float(macd_hist[-1]) if not np.isnan(macd_hist[-1]) else 0.0,
            stoch_k=float(stoch_k[-1]) if not np.isnan(stoch_k[-1]) else 50.0,
            stoch_d=float(stoch_d[-1]) if not np.isnan(stoch_d[-1]) else 50.0,
            timestamp=time.time()
        )
    
    def _calculate_rsi(self, closes: np.ndarray, period: int = 14) -> np.ndarray:
        """Calculate Relative Strength Index."""
        n = len(closes)
        rsi = np.zeros(n)
        
        # Calculate price changes
        delta = np.diff(closes)
        delta = np.insert(delta, 0, 0)  # Pad to match length
        
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        
        # Initial average gain/loss (SMA)
        avg_gain = np.zeros(n)
        avg_loss = np.zeros(n)
        
        # First valid RSI at index 'period'
        if n > period:
            avg_gain[period] = np.mean(gain[1:period+1])
            avg_loss[period] = np.mean(loss[1:period+1])
            
            # Smoothed averages (Wilder's smoothing)
            for i in range(period + 1, n):
                avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i]) / period
                avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i]) / period
            
            # Calculate RS and RSI
            rs = np.zeros(n)
            mask = avg_loss != 0
            rs[mask] = avg_gain[mask] / avg_loss[mask]
            rsi[mask] = 100 - (100 / (1 + rs[mask]))
            rsi[~mask] = 100  # No loss = RSI 100
        
        return rsi
    
    def _calculate_macd(self, closes: np.ndarray, 
                        fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Calculate MACD line, signal line, and histogram."""
        n = len(closes)
        macd_line = np.zeros(n)
        macd_signal_line = np.zeros(n)
        macd_hist = np.zeros(n)
        
        # Calculate EMAs
        ema_fast = self._calculate_ema(closes, fast)
        ema_slow = self._calculate_ema(closes, slow)
        
        # MACD line = Fast EMA - Slow EMA
        macd_line = ema_fast - ema_slow
        
        # Signal line = EMA of MACD line
        macd_signal_line = self._calculate_ema(macd_line, signal)
        
        # Histogram = MACD line - Signal line
        macd_hist = macd_line - macd_signal_line
        
        return macd_line, macd_signal_line, macd_hist
    
    def _calculate_ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """Calculate Exponential Moving Average."""
        n = len(data)
        ema = np.zeros(n)
        multiplier = 2 / (period + 1)
        
        # First EMA is SMA
        if n >= period:
            ema[period-1] = np.mean(data[:period])
            
            # Calculate rest using EMA formula
            for i in range(period, n):
                ema[i] = (data[i] - ema[i-1]) * multiplier + ema[i-1]
        
        return ema
    
    def _calculate_stochastic(self, highs: np.ndarray, lows: np.ndarray, 
                              closes: np.ndarray, k_period: int = 14, 
                              d_period: int = 3) -> Tuple[np.ndarray, np.ndarray]:
        """Calculate Stochastic Oscillator (%K and %D)."""
        n = len(closes)
        stoch_k = np.zeros(n)
        stoch_d = np.zeros(n)
        
        for i in range(k_period - 1, n):
            window_high = np.max(highs[i-k_period+1:i+1])
            window_low = np.min(lows[i-k_period+1:i+1])
            
            if window_high != window_low:
                stoch_k[i] = ((closes[i] - window_low) / (window_high - window_low)) * 100
            else:
                stoch_k[i] = 50.0
        
        # %D is SMA of %K
        for i in range(d_period - 1, n):
            if i >= k_period - 1:
                stoch_d[i] = np.mean(stoch_k[i-d_period+1:i+1])
            else:
                stoch_d[i] = stoch_k[i]
        
        return stoch_k, stoch_d
    
    def _update_price_history(self, symbol: str, result: IndicatorResult):
        """Update price history for divergence detection."""
        if symbol not in self._price_history:
            self._price_history[symbol] = deque(maxlen=self._max_history)
        
        self._price_history[symbol].append({
            'timestamp': result.timestamp,
            'rsi': result.rsi,
            'macd_hist': result.macd_histogram
        })
    
    def _detect_divergence(self, symbol: str, signal_type: str) -> Optional[str]:
        """
        Detect bullish/bearish divergence between price and RSI.
        
        Bullish divergence: Price makes lower low, RSI makes higher low
        Bearish divergence: Price makes higher high, RSI makes lower high
        
        Note: Simplified detection without actual price data (would need OHLCV storage)
        """
        if symbol not in self._price_history:
            return None
        
        history = list(self._price_history[symbol])
        if len(history) < 10:
            return None
        
        # Simple RSI divergence detection (without price comparison)
        recent_rsi = [h['rsi'] for h in history[-5:]]
        
        if signal_type == "STRONG_LONG":
            # Look for RSI making higher lows (potential bullish divergence)
            if len(recent_rsi) >= 3:
                if recent_rsi[-1] > recent_rsi[-3] and recent_rsi[-1] < 40:
                    return "BULLISH_DIV"
        
        elif signal_type == "STRONG_SHORT":
            # Look for RSI making lower highs (potential bearish divergence)
            if len(recent_rsi) >= 3:
                if recent_rsi[-1] < recent_rsi[-3] and recent_rsi[-1] > 60:
                    return "BEARISH_DIV"
        
        return None
    
    def _calculate_bias(self, signal_type: str, rsi_signal: str, 
                       macd_signal: str, stoch_signal: str,
                       divergence: Optional[str]) -> Tuple[str, float]:
        """
        Calculate overall bias and confidence score.
        
        Returns: (bias, confidence) where bias is "BULLISH"/"BEARISH"/"NEUTRAL"
        """
        score = 0
        max_score = 4  # RSI, MACD, Stoch, Divergence
        
        if signal_type == "STRONG_LONG":
            # For long signals, we want bullish confirmation
            if rsi_signal == "OVERSOLD":
                score += 1  # Good for long
            elif rsi_signal == "OVERBOUGHT":
                score -= 1  # Bad for long
            
            if macd_signal == "BULLISH":
                score += 1
            elif macd_signal == "BEARISH":
                score -= 1
            
            if stoch_signal == "OVERSOLD":
                score += 1
            elif stoch_signal == "OVERBOUGHT":
                score -= 1
            
            if divergence == "BULLISH_DIV":
                score += 1
            
            bias = "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL"
            
        else:  # STRONG_SHORT
            # For short signals, we want bearish confirmation
            if rsi_signal == "OVERBOUGHT":
                score += 1  # Good for short
            elif rsi_signal == "OVERSOLD":
                score -= 1  # Bad for short
            
            if macd_signal == "BEARISH":
                score += 1
            elif macd_signal == "BULLISH":
                score -= 1
            
            if stoch_signal == "OVERBOUGHT":
                score += 1
            elif stoch_signal == "OVERSOLD":
                score -= 1
            
            if divergence == "BEARISH_DIV":
                score += 1
            
            bias = "BEARISH" if score > 0 else "BULLISH" if score < 0 else "NEUTRAL"
        
        # Confidence is absolute score normalized to 0-1
        confidence = abs(score) / max_score
        
        return bias, confidence
    
    def clear_cache(self, symbol: Optional[str] = None):
        """Clear cache for specific symbol or all symbols."""
        if symbol:
            self._cache.pop(symbol, None)
        else:
            self._cache.clear()
