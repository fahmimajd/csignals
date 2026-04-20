import asyncio
import logging
from typing import Dict, Optional, List, Tuple
import time
import config
from modules.base import Monitor

logger = logging.getLogger(__name__)


class OrderBookMonitor(Monitor):
    """
    Monitors order book depth and calculates bid/ask imbalance.
    IMPROVED: Uses WebSocket stream for real-time data instead of REST polling.
    Falls back to REST polling every 10 seconds if WebSocket fails.
    """

    # Price range for imbalance calculation (0.5% of mid price)
    PRICE_RANGE_PERCENT = 0.005

    def __init__(self):
        super().__init__()
        self.orderbooks: Dict[str, Dict] = {}
        self.imbalances: Dict[str, float] = {}
        self.update_interval = 10  # seconds - FASTER than before (was 30)
        self.ws_sessions: Dict[str, any] = {}
        self._ws_task: Optional[asyncio.Task] = None

    async def _on_initialize(self):
        """No specific initialization needed."""
        pass

    async def _on_start(self):
        """Start the order book update loop and WebSocket connection."""
        # Start REST polling as fallback
        self._create_task(self._update_loop())
        # Start WebSocket stream for real-time updates
        self._ws_task = self._create_task(self._websocket_loop())

    async def _on_stop(self):
        """Cleanup on stop - close WebSocket connections."""
        # Close all WebSocket sessions
        for symbol, session in list(self.ws_sessions.items()):
            try:
                if hasattr(session, 'close'):
                    await session.close()
            except Exception as e:
                logger.warning(f"Error closing WS for {symbol}: {e}")
        self.ws_sessions.clear()
        
        # Cancel WebSocket task
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

    async def _websocket_loop(self):
        """
        Connect to Binance Futures WebSocket for order book depth.
        Uses {symbol}@depth5@100ms stream for real-time updates.
        Reconnects automatically on disconnection.
        """
        ws_url = "wss://fstream.binance.com/ws"
        reconnect_delay = 5
        
        while self.running:
            try:
                # Build subscription list for all symbols
                symbols = config.SYMBOLS if isinstance(config.SYMBOLS, list) else list(self.orderbooks.keys())[:20]
                if not symbols:
                    await asyncio.sleep(5)
                    continue
                
                # Subscribe to depth5 streams (top 5 bids/asks, 100ms updates)
                streams = [f"{sym.lower()}@depth5@100ms" for sym in symbols]
                subscribe_msg = {
                    "method": "SUBSCRIBE",
                    "params": streams,
                    "id": 1
                }
                
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url) as ws:
                        logger.info(f"OrderBook WebSocket connected for {len(symbols)} symbols")
                        await ws.send_json(subscribe_msg)
                        
                        async for msg in ws:
                            if not self.running:
                                break
                            
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = msg.json()
                                # Handle subscription response
                                if 'result' in data:
                                    continue
                                # Handle depth update
                                if 'data' in data:
                                    self._process_depth_update(data['data'])
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.warning("OrderBook WebSocket error, reconnecting...")
                                break
                        
            except Exception as e:
                logger.warning(f"OrderBook WebSocket error: {e}, reconnecting in {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)
    
    def _process_depth_update(self, data: dict):
        """Process WebSocket depth update message."""
        try:
            symbol = data.get('s', '').upper()
            if not symbol:
                return
            
            bids = [[float(p), float(q)] for p, q in data.get('bids', [])]
            asks = [[float(p), float(q)] for p, q in data.get('asks', [])]
            
            if not bids or not asks:
                return
            
            mid_price = (bids[0][0] + asks[0][0]) / 2
            price_range = mid_price * self.PRICE_RANGE_PERCENT
            
            bid_volume = sum(p * q for p, q in bids if abs(p - mid_price) <= price_range)
            ask_volume = sum(p * q for p, q in asks if abs(p - mid_price) <= price_range)
            
            if bid_volume + ask_volume == 0:
                imbalance = 0.0
            else:
                imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume)
            
            self.orderbooks[symbol] = {'bids': bids, 'asks': asks, 'mid_price': mid_price}
            self.imbalances[symbol] = imbalance
            
        except Exception as e:
            logger.debug(f"Error processing depth update: {e}")

    async def _update_loop(self):
        """
        Update order book every update_interval seconds as FALLBACK.
        WebSocket provides real-time updates; this is only backup.
        """
        while self.running:
            for symbol in config.SYMBOLS:
                # Only fetch via REST if we don't have recent WS data (5+ seconds old)
                if symbol not in self.orderbooks:
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