"""
Open Interest tracker for monitoring OI changes and trader positioning on Binance Futures.
Uses REST API polling to track open interest and long/short ratios.
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Tuple

import config
from modules.base import Monitor

logger = logging.getLogger(__name__)


class OpenInterestTracker(Monitor):
    """
    Monitors Open Interest changes and trader positioning.
    Uses REST API polling since OI data is not available via WebSocket.
    """

    # Update intervals
    OI_UPDATE_INTERVAL = 60  # 1 minute
    RATIO_UPDATE_INTERVAL = 300  # 5 minutes

    # Price change thresholds for trend detection
    PRICE_CHANGE_THRESHOLD = 0.001  # 0.1%

    # OI change threshold (%) for signal classification
    OI_SIGNAL_THRESHOLD = 1.0  # 1%

    def __init__(self):
        super().__init__()
        self.oi_data: Dict[str, Dict] = {}
        self.price_data: Dict[str, float] = {}
        self.prev_price: Dict[str, float] = {}
        self.price_trend: Dict[str, str] = {}
        self.long_short_ratio: Dict[str, float] = {}
        self.taker_volume_ratio: Dict[str, float] = {}
        # Problem 3 fix: stores price-confirmation state per symbol
        # used by aggregator to get additional context on OI moves
        self.oi_confirmed: Dict[str, bool] = {}

    async def _on_initialize(self):
        """Initialize default values for all symbols."""
        for symbol in config.SYMBOLS:
            self.oi_data[symbol] = {'value': 0, 'change': 0, 'timestamp': None}
            self.price_data[symbol] = 0
            self.prev_price[symbol] = 0
            self.price_trend[symbol] = 'FLAT'
            self.long_short_ratio[symbol] = 0.5  # Neutral default
            self.taker_volume_ratio[symbol] = 1.0  # Neutral default
            self.oi_confirmed[symbol] = False  # price-confirmation flag

    async def _on_start(self):
        """Start the update loops."""
        self._create_task(self._update_oi_loop())
        self._create_task(self._update_ratio_loop())
        # Block until price data is populated for all symbols before returning.
        # This ensures the main loop can read valid prices immediately.
        await self._wait_for_initial_data()

    async def _on_stop(self):
        """Cleanup on stop."""
        pass

    async def _wait_for_initial_data(self):
        """
        Block until price data is populated for at least one symbol.
        Polls every 0.5s for up to 30 seconds before giving up (fail-open).
        This ensures the main loop has valid price data immediately.
        """
        timeout = 30.0
        interval = 0.5
        elapsed = 0.0
        while elapsed < timeout:
            # Check if at least one symbol has a non-zero price
            has_data = any(p > 0 for p in self.price_data.values())
            if has_data:
                logger.debug(f"Initial price data ready after {elapsed:.1f}s")
                return
            await asyncio.sleep(interval)
            elapsed += interval
        logger.warning(
            f"Timeout ({timeout}s) waiting for initial price data — continuing anyway"
        )

    async def _update_oi_loop(self):
        """Update open interest every OI_UPDATE_INTERVAL seconds."""
        # Initial fetch immediately (before sleeping) so data is available right away
        for symbol in config.SYMBOLS:
            try:
                await self._fetch_open_interest(symbol)
                await self._fetch_price(symbol)
            except Exception as e:
                logger.warning(f"OI/Price fetch error for {symbol}: {e}")

        while self.running:
            await asyncio.sleep(self.OI_UPDATE_INTERVAL)
            for symbol in config.SYMBOLS:
                try:
                    await self._fetch_open_interest(symbol)
                    await self._fetch_price(symbol)
                except Exception as e:
                    logger.warning(f"OI/Price fetch error for {symbol}: {e}")

    async def _update_ratio_loop(self):
        """Update long/short ratio and taker volume every RATIO_UPDATE_INTERVAL seconds."""
        while self.running:
            for symbol in config.SYMBOLS:
                try:
                    await self._fetch_long_short_ratio(symbol)
                    await self._fetch_taker_volume_ratio(symbol)
                except Exception as e:
                    logger.warning(f"Ratio fetch error for {symbol}: {e}")
            await asyncio.sleep(self.RATIO_UPDATE_INTERVAL)

    async def _fetch_open_interest(self, symbol: str):
        """Fetch open interest data and calculate change."""
        oi = await self.client.futures_open_interest(symbol=symbol)
        current_oi = float(oi['openInterest'])
        timestamp = datetime.fromtimestamp(oi['time'] / 1000)

        if symbol in self.oi_data and self.oi_data[symbol]['value'] > 0:
            prev_oi = self.oi_data[symbol]['value']
            change = ((current_oi - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0
            self.oi_data[symbol] = {
                'value': current_oi,
                'change': change,
                'timestamp': timestamp
            }
        else:
            self.oi_data[symbol] = {
                'value': current_oi,
                'change': 0,
                'timestamp': timestamp
            }

    async def _fetch_price(self, symbol: str):
        """Fetch current price and update trend."""
        ticker = await self.client.futures_symbol_ticker(symbol=symbol)
        current_price = float(ticker['price'])

        if symbol in self.prev_price and self.prev_price[symbol] > 0:
            prev = self.prev_price[symbol]
            if current_price > prev * (1 + self.PRICE_CHANGE_THRESHOLD):
                self.price_trend[symbol] = 'UP'
            elif current_price < prev * (1 - self.PRICE_CHANGE_THRESHOLD):
                self.price_trend[symbol] = 'DOWN'
            else:
                self.price_trend[symbol] = 'FLAT'
        else:
            self.price_trend[symbol] = 'FLAT'

        self.prev_price[symbol] = current_price
        self.price_data[symbol] = current_price

    # ─────────────────────────────────────────────────────────────────────────
    # Problem 1 fix: _fetch_long_short_ratio — was hardcoded to 0.5
    # ─────────────────────────────────────────────────────────────────────────
    async def _fetch_long_short_ratio(self, symbol: str):
        """Fetch top trader long/short ratio from Binance Futures API.

        Endpoint: GET /futures/data/globalLongShortAccountRatio
        Parses 'longShortRatio' from response (e.g. 0.52 = 52% long).
        Falls back to last valid value on failure; 0.5 if none available.
        """
        url = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
        params = {"symbol": symbol, "period": "5m", "limit": 1}
        try:
            data = await self.client.futures_global_longshort_ratio(
                symbol=symbol, period="5m", limit=1
            )
            if data and len(data) > 0:
                # 'longShortRatio' is a string like "0.5231" (ratio of long / total)
                ratio = float(data[0]["longShortRatio"])
                self.long_short_ratio[symbol] = ratio
                logger.debug(
                    f"{symbol} long/short ratio: {ratio:.4f} (API)"
                )
            else:
                raise ValueError("Empty response from longShortAccountRatio")
        except Exception as e:
            prev = self.long_short_ratio.get(symbol)
            fallback = prev if prev is not None else 0.5
            self.long_short_ratio[symbol] = fallback
            logger.warning(
                f"Failed to fetch long/short ratio for {symbol}: {e}. "
                f"Using fallback {fallback:.4f}"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Problem 2 fix: _fetch_taker_volume_ratio — was hardcoded to 1.0
    # Calculates buyVol / (buyVol + sellVol) → normalised range [0.0, 1.0]
    # ─────────────────────────────────────────────────────────────────────────
    async def _fetch_taker_volume_ratio(self, symbol: str):
        """Fetch taker buy/sell volume ratio from Binance Futures API.

        Endpoint: GET /futures/data/takerbuyselsvol
        Calculates buyVol / (buyVol + sellVol) → range [0.0, 1.0].
        Falls back to last valid value on failure; 0.5 if none available.
        """
        url = "https://fapi.binance.com/futures/data/takerbuyselsvol"
        params = {"symbol": symbol, "period": "5m", "limit": 1}
        try:
            data = await self.client.futures_taker_longshort_ratio(
                symbol=symbol, period="5m", limit=1
            )
            if data and len(data) > 0:
                buy_vol = float(data[0]["buyVol"])    # USDT buy volume
                sell_vol = float(data[0]["sellVol"])  # USDT sell volume
                total = buy_vol + sell_vol
                # Normalised ratio: 0.0 = all sell, 0.5 = balanced, 1.0 = all buy
                ratio = buy_vol / total if total > 0 else 0.5
                self.taker_volume_ratio[symbol] = ratio
                logger.debug(
                    f"{symbol} taker volume ratio: {ratio:.4f} "
                    f"(buy={buy_vol:.0f}, sell={sell_vol:.0f})"
                )
            else:
                raise ValueError("Empty response from takerbuyselsvol")
        except Exception as e:
            prev = self.taker_volume_ratio.get(symbol)
            fallback = prev if prev is not None else 0.5
            self.taker_volume_ratio[symbol] = fallback
            logger.warning(
                f"Failed to fetch taker volume ratio for {symbol}: {e}. "
                f"Using fallback {fallback:.4f}"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Problem 3 fix: get_oi_signal — was too tight, returned NEUTRAL too often
    #
    # Layer 1 — OI direction only (always produces a value):
    #   oi_change >  +1%  → base_signal = +1  (positions being opened)
    #   oi_change <  -1%  → base_signal = -1  (positions being closed / reversal)
    #   -1% ≤ oi ≤ +1%   → base_signal =  0  (no significant change)
    #
    # Layer 2 — Price confirmation (bonus):
    #   base_signal > 0 and price_trend == 'UP'   → confirmed = True
    #   base_signal < 0 and price_trend == 'DOWN' → confirmed = True
    #   else                                       → confirmed = False
    #
    # Return: (base_signal, confirmed)
    #   base_signal → used by aggregator for scoring component 4
    #   confirmed   → stored in self.oi_confirmed[symbol] for aggregator context
    # ─────────────────────────────────────────────────────────────────────────
    def get_oi_signal(self, symbol: str) -> Tuple[int, bool]:
        """
        Get OI direction signal with price confirmation.

        Returns:
            Tuple of (base_signal, confirmed):
            - base_signal: +1 (OI up >1%), -1 (OI down <-1%), 0 (no significant move)
            - confirmed: True if OI direction aligns with price trend, False otherwise

        The confirmed flag is also stored in self.oi_confirmed[symbol] so the
        aggregator can read it as additional context without calling this method.
        """
        if symbol not in self.oi_data or symbol not in self.price_data:
            self.oi_confirmed[symbol] = False
            return 0, False

        oi_change = self.oi_data[symbol]['change']
        price_trend = self.price_trend.get(symbol, 'FLAT')

        # ── Layer 1: OI direction threshold ──────────────────────────────────
        if oi_change > self.OI_SIGNAL_THRESHOLD:
            base_signal = 1   # significant OI increase — positions being opened
        elif oi_change < -self.OI_SIGNAL_THRESHOLD:
            base_signal = -1  # significant OI decrease — positions closing / reversal
        else:
            base_signal = 0   # within dead-band — no directional signal

        # ── Layer 2: Price confirmation bonus ────────────────────────────────
        confirmed = (
            (base_signal > 0 and price_trend == 'UP') or
            (base_signal < 0 and price_trend == 'DOWN')
        )

        # Persist confirmed state so aggregator can read it independently
        self.oi_confirmed[symbol] = confirmed

        return base_signal, confirmed

    def get_oi_change(self, symbol: str) -> float:
        """Get OI change percentage for the symbol (helper for callers)."""
        return self.oi_data.get(symbol, {}).get('change', 0)

    def get_oi_confirmed(self, symbol: str) -> bool:
        """Get price-confirmation flag for the symbol (set by get_oi_signal)."""
        return self.oi_confirmed.get(symbol, False)

    def get_long_short_ratio(self, symbol: str) -> float:
        """Get current long/short ratio (0.5 = neutral)."""
        return self.long_short_ratio.get(symbol, 0.5)

    def get_taker_volume_ratio(self, symbol: str) -> float:
        """Get taker buy/sell volume ratio (0.5 = balanced)."""
        return self.taker_volume_ratio.get(symbol, 0.5)
