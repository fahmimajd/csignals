"""
Order Book monitor for tracking bid/ask imbalance on Binance Futures.
Calculates order book imbalance within 0.5% of mid price.
"""
import asyncio
import logging
from typing import Dict

import config
from modules.base import Monitor

logger = logging.getLogger(__name__)


class OrderBookMonitor(Monitor):
    """
    Monitors order book depth and calculates bid/ask imbalance.
    Uses REST API polling since Binance doesn't offer a dedicated
    order book WebSocket stream for futures.
    """

    # Price range for imbalance calculation (0.5% of mid price)
    PRICE_RANGE_PERCENT = 0.005

    def __init__(self):
        super().__init__()
        self.orderbooks: Dict[str, Dict] = {}
        self.imbalances: Dict[str, float] = {}
        self.update_interval = 30  # seconds

    async def _on_initialize(self):
        """No specific initialization needed."""
        pass

    async def _on_start(self):
        """Start the order book update loop."""
        self._create_task(self._update_loop())

    async def _on_stop(self):
        """Cleanup on stop."""
        pass

    async def _update_loop(self):
        """Update order book every update_interval seconds."""
        while self.running:
            for symbol in config.SYMBOLS:
                try:
                    await self._fetch_orderbook(symbol)
                except Exception as e:
                    logger.warning(f"Order book error for {symbol}: {e}")
            await asyncio.sleep(self.update_interval)

    async def _fetch_orderbook(self, symbol: str):
        """Fetch order book depth and calculate imbalance."""
        depth = await self.client.futures_order_book(symbol=symbol, limit=500)
        bids = depth['bids']
        asks = depth['asks']

        mid_price = (float(bids[0][0]) + float(asks[0][0])) / 2
        price_range = mid_price * self.PRICE_RANGE_PERCENT

        bid_volume = 0.0
        ask_volume = 0.0

        for price, volume in bids:
            p = float(price)
            if abs(p - mid_price) <= price_range:
                bid_volume += float(volume) * p

        for price, volume in asks:
            p = float(price)
            if abs(p - mid_price) <= price_range:
                ask_volume += float(volume) * p

        if bid_volume + ask_volume == 0:
            imbalance = 0.0
        else:
            imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume)

        self.orderbooks[symbol] = {'bids': bids, 'asks': asks, 'mid_price': mid_price}
        self.imbalances[symbol] = imbalance

    def get_imbalance(self, symbol: str) -> float:
        """Get current order book imbalance (-1 to 1 scale)."""
        return self.imbalances.get(symbol, 0.0)

    def get_imbalance_signal(self, symbol: str) -> str:
        """Get signal based on imbalance threshold."""
        imbalance = self.get_imbalance(symbol)
        if imbalance > config.OB_IMBALANCE_THRESHOLD:
            return 'BUY_PRESSURE'
        elif imbalance < -config.OB_IMBALANCE_THRESHOLD:
            return 'SELL_PRESSURE'
        return 'NEUTRAL'

    def get_visual_bar(self, symbol: str, width: int = 20) -> str:
        """Generate ASCII progress bar for imbalance."""
        imbalance = self.get_imbalance(symbol)
        # Scale from [-1, 1] to [0, 1]
        scaled = (imbalance + 1) / 2
        fill = int(scaled * width)
        bar = '█' * fill + '░' * (width - fill)
        return bar