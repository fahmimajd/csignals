"""
TP/SL Calculator for calculating take profit and stop loss levels.
Uses ATR-based calculation with optional liquidity zone detection.
"""
import asyncio
import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

import config
from modules.base import Monitor

logger = logging.getLogger(__name__)


class TPSLCalculator(Monitor):
    """
    Calculates TP/SL levels based on ATR and market structure.
    """

    def __init__(self):
        super().__init__()
        self.atr_cache: Dict[str, float] = {}

    async def _on_initialize(self):
        """No specific initialization needed."""
        pass

    async def _on_start(self):
        """No background tasks needed."""
        pass

    async def _on_stop(self):
        """No cleanup needed."""
        pass

    async def calculate_atr(self, symbol: str) -> float:
        """
        Calculate ATR for given symbol.

        Args:
            symbol: Trading pair symbol
            period: ATR period (default from config)
            timeframe: Candle timeframe (default from config)

        Returns:
            ATR value
        """
        try:
            klines = await self.client.futures_klines(
                symbol=symbol,
                interval=config.ATR_TIMEFRAME,
                limit=config.ATR_PERIOD + 1
            )

            high = [float(k[2]) for k in klines]
            low = [float(k[3]) for k in klines]
            close = [float(k[4]) for k in klines]

            tr = []
            for i in range(1, len(close)):
                hl = high[i] - low[i]
                hc = abs(high[i] - close[i-1])
                lc = abs(low[i] - close[i-1])
                tr.append(max(hl, hc, lc))

            atr = np.mean(tr[-config.ATR_PERIOD:]) if len(tr) >= config.ATR_PERIOD else 0
            self.atr_cache[symbol] = atr
            return atr

        except Exception as e:
            logger.warning(f"ATR calculation error for {symbol}: {e}")
            return 0.0

    def calculate_stop_loss(self, symbol: str, entry_price: float, side: str) -> Tuple[float, float]:
        """
        Calculate stop loss level.

        Args:
            symbol: Trading pair symbol
            entry_price: Entry price
            side: 'LONG' or 'SHORT'

        Returns:
            Tuple of (sl_price, sl_percent)
        """
        atr = self.atr_cache.get(symbol, 0)
        if atr == 0:
            atr = entry_price * 0.01  # Fallback: 1% of price

        sl_distance = atr * config.SL_MULTIPLIER
        
        # Enforce maximum SL cap (e.g., 10%)
        max_sl_dist = entry_price * config.MAX_SL_PERCENT
        if sl_distance > max_sl_dist:
            logger.info(f"SL distance for {symbol} capped at {config.MAX_SL_PERCENT*100}%")
            sl_distance = max_sl_dist

        min_sl = entry_price * 0.005  # 0.5% minimum protection

        if sl_distance < min_sl:
            sl_distance = min_sl

        if side == 'LONG':
            sl_price = entry_price - sl_distance
        else:  # SHORT
            sl_price = entry_price + sl_distance

        sl_percent = (sl_price - entry_price) / entry_price * 100
        return sl_price, sl_percent

    async def calculate_take_profit(self, symbol: str, entry_price: float, side: str) -> Tuple[float, float, str]:
        """
        Calculate take profit based on liquidity zones or ATR.

        Args:
            symbol: Trading pair symbol
            entry_price: Entry price
            side: 'LONG' or 'SHORT'

        Returns:
            Tuple of (tp_price, tp_percent, tp_source)
        """
        atr = self.atr_cache.get(symbol, 0)
        if atr == 0:
            atr = await self.calculate_atr(symbol)

        # Try to get liquidity zones from open interest history
        liquidity_zones = await self._get_liquidity_zones(symbol, side)

        tp_price = None
        tp_source = 'ATR'

        if liquidity_zones:
            if side == 'LONG':
                zones_above = [z for z in liquidity_zones if z > entry_price]
                if zones_above:
                    tp_price = min(zones_above)
                    tp_source = 'LIQUIDITY_ZONE'
            else:  # SHORT
                zones_below = [z for z in liquidity_zones if z < entry_price]
                if zones_below:
                    tp_price = max(zones_below)
                    tp_source = 'LIQUIDITY_ZONE'

        # Fallback to ATR-based TP if no liquidity zone found
        if tp_price is None:
            tp_distance = atr * config.TP_MULTIPLIER
            if side == 'LONG':
                tp_price = entry_price + tp_distance
            else:
                tp_price = entry_price - tp_distance

        # Validate R:R ratio
        sl_price, _ = self.calculate_stop_loss(symbol, entry_price, side)
        if side == 'LONG':
            profit = tp_price - entry_price
            loss = entry_price - sl_price
        else:
            profit = entry_price - tp_price
            loss = sl_price - entry_price

        rr_ratio = profit / loss if loss > 0 else 0

        if rr_ratio < config.MIN_RR_RATIO:
            # Adjust TP to meet minimum R:R
            required_profit = loss * config.MIN_RR_RATIO
            if side == 'LONG':
                tp_price = entry_price + required_profit
            else:
                tp_price = entry_price - required_profit
            tp_source = 'ADJUSTED_RR'

        tp_percent = abs((tp_price - entry_price) / entry_price * 100)
        return tp_price, tp_percent, tp_source

    async def _get_liquidity_zones(self, symbol: str, side: str) -> List[float]:
        """Get liquidity zones from open interest history."""
        zones = []

        try:
            oi_hist = await self.client.futures_open_interest_hist(
                symbol=symbol,
                period='1h',
                limit=24
            )

            if oi_hist:
                max_oi = max(float(item['sumOpenInterest']) for item in oi_hist)
                threshold = max_oi * 0.8  # Top 20%
                for item in oi_hist:
                    if float(item['sumOpenInterest']) >= threshold:
                        zones.append(float(item['price']))

        except Exception as e:
            logger.debug(f"Liquidity zones error for {symbol}: {e}")

        # Deduplicate and sort
        zones = sorted(set(zones))
        return zones

    def get_trail_levels(self, symbol: str, entry_price: float, side: str, current_price: float) -> Tuple[Optional[float], Optional[float]]:
        """
        Calculate trailing stop levels.

        Args:
            symbol: Trading pair symbol
            entry_price: Entry price
            side: 'LONG' or 'SHORT'
            current_price: Current market price

        Returns:
            Tuple of (trigger_distance, trail_stop) or (None, None)
        """
        atr = self.atr_cache.get(symbol, 0)
        if atr == 0:
            return None, None

        if side == 'LONG':
            profit_move = current_price - entry_price
            trigger_distance = atr * config.TRAIL_TRIGGER_ATR
            if profit_move >= trigger_distance:
                trail_stop = current_price - (atr * config.TRAIL_DISTANCE_ATR)
                return trigger_distance, trail_stop
        else:  # SHORT
            profit_move = entry_price - current_price
            trigger_distance = atr * config.TRAIL_TRIGGER_ATR
            if profit_move >= trigger_distance:
                trail_stop = current_price + (atr * config.TRAIL_DISTANCE_ATR)
                return trigger_distance, trail_stop

        return None, None