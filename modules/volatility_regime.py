"""
volatility_regime.py — Volatility Regime Detector for Crypto Signal System

Classifies market conditions into 3 regimes:
  TRENDING  → signals more accurate, continue normal scoring
  RANGING   → signals valid but reduce confidence
  CHOPPY    → skip signals, too much noise

This is the FIRST GATE before 6 scoring components run.
If CHOPPY → return score=0 immediately, don't proceed to other components.
"""

import asyncio
import time
import logging
from typing import Dict, Optional
from dataclasses import dataclass, field

import numpy as np
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class RegimeCacheEntry:
    """Cached regime detection result."""
    regime: str
    adx: float
    atr_percentile: float
    bbw_percentile: float
    confidence: float
    timestamp: float


@dataclass
class RegimeResult:
    """Result of regime detection."""
    regime: str  # "TRENDING" | "RANGING" | "CHOPPY"
    adx: float
    atr_percentile: float
    bbw_percentile: float
    confidence: float


class VolatilityRegimeDetector:
    """
    Detects market volatility regime using ATR Percentile, Bollinger Band Width,
    and Directional Movement Index (DMI/ADX).
    
    Cache results for 5 minutes per symbol since regime doesn't change as fast as price.
    """
    
    CACHE_TTL = 300  # 5 minutes cache
    BINANCE_KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"
    
    def __init__(self):
        self._cache: Dict[str, RegimeCacheEntry] = {}
    
    async def detect(self, symbol: str) -> Dict:
        """
        Detect market regime for a symbol.
        
        Returns dict with:
          regime: "TRENDING" | "RANGING" | "CHOPPY"
          adx: float (0-100)
          atr_percentile: float (0.0-1.0)
          bbw_percentile: float (0.0-1.0)
          confidence: float (0.0-1.0)
        
        If fetch fails, fallback to RANGING regime (don't block signals).
        """
        # Check cache first
        now = time.time()
        if symbol in self._cache:
            entry = self._cache[symbol]
            if now - entry.timestamp < self.CACHE_TTL:
                logger.debug(f"[{symbol}] Using cached regime: {entry.regime}")
                return {
                    "regime": entry.regime,
                    "adx": entry.adx,
                    "atr_percentile": entry.atr_percentile,
                    "bbw_percentile": entry.bbw_percentile,
                    "confidence": entry.confidence
                }
        
        # Fetch fresh data
        try:
            result = await self._fetch_and_calculate(symbol)
            
            # Update cache
            self._cache[symbol] = RegimeCacheEntry(
                regime=result.regime,
                adx=result.adx,
                atr_percentile=result.atr_percentile,
                bbw_percentile=result.bbw_percentile,
                confidence=result.confidence,
                timestamp=now
            )
            
            logger.info(f"[{symbol}] Regime detected: {result.regime} (ADX: {result.adx:.1f}, "
                       f"ATR%: {result.atr_percentile:.2f}, BBW%: {result.bbw_percentile:.2f})")
            
            return {
                "regime": result.regime,
                "adx": result.adx,
                "atr_percentile": result.atr_percentile,
                "bbw_percentile": result.bbw_percentile,
                "confidence": result.confidence
            }
            
        except Exception as e:
            logger.warning(f"[{symbol}] Regime detection failed: {e}. Fallback to RANGING.")
            # Fallback: don't block signals if data unavailable
            return {
                "regime": "RANGING",
                "adx": 0.0,
                "atr_percentile": 0.5,
                "bbw_percentile": 0.5,
                "confidence": 0.5
            }
    
    async def _fetch_and_calculate(self, symbol: str) -> RegimeResult:
        """Fetch kline data and calculate regime indicators."""
        # Fetch 50 candles of 1h interval
        params = {
            "symbol": symbol.replace("/", ""),  # Remove slash if present
            "interval": "1h",
            "limit": 50
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(self.BINANCE_KLINE_URL, params=params) as response:
                if response.status != 200:
                    raise Exception(f"Binance API error: {response.status}")
                
                data = await response.json()
        
        if not data or len(data) < 20:
            raise Exception("Insufficient candle data")
        
        # Parse OHLCV data
        # Binance format: [open_time, open, high, low, close, volume, ...]
        highs = np.array([float(candle[2]) for candle in data])
        lows = np.array([float(candle[3]) for candle in data])
        closes = np.array([float(candle[4]) for candle in data])
        
        # Calculate indicators
        atr_values = self._calculate_atr(highs, lows, closes, period=14)
        atr_percentile = self._calculate_percentile(atr_values)
        
        bbw_values = self._calculate_bollinger_band_width(closes, period=20, std_dev=2)
        bbw_percentile = self._calculate_percentile(bbw_values)
        
        adx = self._calculate_adx(highs, lows, closes, period=14)
        
        # Classify regime
        regime, confidence = self._classify_regime(adx, atr_percentile, bbw_percentile)
        
        return RegimeResult(
            regime=regime,
            adx=adx,
            atr_percentile=atr_percentile,
            bbw_percentile=bbw_percentile,
            confidence=confidence
        )
    
    def _calculate_atr(self, highs: np.ndarray, lows: np.ndarray, 
                       closes: np.ndarray, period: int = 14) -> np.ndarray:
        """Calculate Average True Range."""
        n = len(closes)
        tr = np.zeros(n)
        
        for i in range(n):
            if i == 0:
                tr[i] = highs[i] - lows[i]
            else:
                tr[i] = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]),
                    abs(lows[i] - closes[i-1])
                )
        
        # Use EMA-like calculation for ATR
        atr = np.zeros(n)
        atr[0] = tr[0]
        
        for i in range(1, n):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
        
        return atr
    
    def _calculate_bollinger_band_width(self, closes: np.ndarray, 
                                         period: int = 20, 
                                         std_dev: float = 2) -> np.ndarray:
        """Calculate Bollinger Band Width (BBW)."""
        n = len(closes)
        bbw = np.zeros(n)
        
        for i in range(period - 1, n):
            window = closes[i-period+1:i+1]
            middle = np.mean(window)
            std = np.std(window)
            upper = middle + std_dev * std
            lower = middle - std_dev * std
            
            if middle > 0:
                bbw[i] = (upper - lower) / middle
            else:
                bbw[i] = 0
        
        return bbw
    
    def _calculate_adx(self, highs: np.ndarray, lows: np.ndarray, 
                       closes: np.ndarray, period: int = 14) -> float:
        """Calculate Average Directional Index (ADX)."""
        n = len(closes)
        if n < period * 2:
            return 0.0
        
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        
        for i in range(1, n):
            up_move = highs[i] - highs[i-1]
            down_move = lows[i-1] - lows[i]
            
            if up_move > down_move and up_move > 0:
                plus_dm[i] = up_move
            else:
                plus_dm[i] = 0
            
            if down_move > up_move and down_move > 0:
                minus_dm[i] = down_move
            else:
                minus_dm[i] = 0
        
        tr = np.zeros(n)
        for i in range(n):
            if i == 0:
                tr[i] = highs[i] - lows[i]
            else:
                tr[i] = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]),
                    abs(lows[i] - closes[i-1])
                )
        
        # Smooth DM and TR using Wilder's smoothing
        plus_di = np.zeros(n)
        minus_di = np.zeros(n)
        
        # First sum for initial values
        plus_tr_sum = np.sum(tr[1:period+1])
        plus_dm_sum = np.sum(plus_dm[1:period+1])
        minus_dm_sum = np.sum(minus_dm[1:period+1])
        
        for i in range(period, n):
            if i == period:
                plus_tr = plus_tr_sum
                plus_dmi = plus_dm_sum
                minus_dmi = minus_dm_sum
            else:
                plus_tr = plus_tr - plus_tr / period + tr[i]
                plus_dmi = plus_dmi - plus_dmi / period + plus_dm[i]
                minus_dmi = minus_dmi - minus_dmi / period + minus_dm[i]
            
            if plus_tr > 0:
                plus_di[i] = (plus_dmi / plus_tr) * 100
            if plus_tr > 0:
                minus_di[i] = (minus_dmi / plus_tr) * 100
        
        # Calculate DX and ADX
        dx = np.zeros(n)
        for i in range(period, n):
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = abs(plus_di[i] - minus_di[i]) / di_sum * 100
        
        # ADX is SMA of DX
        adx = np.mean(dx[period:period*2]) if n >= period * 2 else 0.0
        
        # Use more recent values for better accuracy
        recent_dx = dx[period*2:] if len(dx) > period * 2 else dx[period:]
        if len(recent_dx) > 0:
            adx = np.mean(recent_dx)
        
        return float(adx)
    
    def _calculate_percentile(self, values: np.ndarray) -> float:
        """Calculate percentile of the most recent value vs historical range."""
        if len(values) < 2:
            return 0.5
        
        current = values[-1]
        historical = values[:-1]  # Exclude current from historical comparison
        
        min_val = np.min(historical)
        max_val = np.max(historical)
        
        if max_val == min_val:
            return 0.5
        
        percentile = (current - min_val) / (max_val - min_val)
        return float(np.clip(percentile, 0.0, 1.0))
    
    def _classify_regime(self, adx: float, atr_pct: float, bbw_pct: float) -> tuple:
        """
        Classify market regime based on ADX, ATR percentile, and BBW percentile.
        
        Returns: (regime, confidence)
        """
        # TRENDING: ADX > 25 AND (ATR% > 0.4 OR BBW% > 0.4)
        if adx > 25 and (atr_pct > 0.4 or bbw_pct > 0.4):
            # Confidence based on how strong the signals are
            confidence = min(1.0, (adx / 50 + atr_pct + bbw_pct) / 3)
            return "TRENDING", confidence
        
        # CHOPPY: ADX < 20 AND ATR% < 0.3 AND BBW% < 0.3
        if adx < 20 and atr_pct < 0.3 and bbw_pct < 0.3:
            # Confidence based on how weak the market is
            choppy_score = (1 - adx / 20 + (1 - atr_pct / 0.3) + (1 - bbw_pct / 0.3)) / 3
            confidence = min(1.0, choppy_score)
            return "CHOPPY", confidence
        
        # RANGING: everything else
        # Moderate confidence for ranging market
        confidence = 0.7  # Default moderate confidence
        return "RANGING", confidence
    
    def clear_cache(self, symbol: Optional[str] = None):
        """Clear cache for a specific symbol or all symbols."""
        if symbol:
            self._cache.pop(symbol, None)
        else:
            self._cache.clear()
