"""
Liquidation monitor for tracking forced liquidations on Binance Futures.
Uses WebSocket stream to receive real-time liquidation data.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import config
from modules.base import Monitor, WEBSOCKET_RECV_TIMEOUT, MAX_CONSECUTIVE_ERRORS
from binance.exceptions import BinanceWebsocketQueueOverflow, BinanceWebsocketUnableToConnect

logger = logging.getLogger(__name__)

# Number of symbol streams to bundle per multiplex socket.
# Keeping this low avoids queue overflow in the shared BinanceSocketManager.
STREAMS_PER_SOCKET = 5


class LiquidationMonitor(Monitor):
    """
    Monitors forced liquidations via Binance WebSocket stream.
    Tracks long vs short liquidations to determine market bias.
    """

    def __init__(self):
        super().__init__()
        self.liquidations: Dict[str, List[Tuple[float, str, float, float, datetime]]] = {}
        self.dominance_window = timedelta(minutes=3)  # Configurable window
        self.threshold = 100_000  # Minimum liquidation to track ($100K)

    async def _on_initialize(self):
        """Initialize liquidation data storage per symbol."""
        for symbol in config.SYMBOLS:
            self.liquidations[symbol] = []

    async def _on_start(self):
        """Subscribe to forceOrder stream for all symbols, batched into multiplex sockets."""
        streams = [f"{s.lower()}@forceOrder" for s in config.SYMBOLS]
        batches = [
            streams[i:i + STREAMS_PER_SOCKET]
            for i in range(0, len(streams), STREAMS_PER_SOCKET)
        ]
        for batch in batches:
            self._create_task(self._listen_batch(batch))
            # Staggered start with jitter to avoid thundering herd
            delay = self._jitter_delay(0.05)
            await asyncio.sleep(delay)

    async def _on_stop(self):
        """Cleanup on stop - nothing specific needed."""
        pass

    async def _listen_batch(self, streams: List[str]):
        """
        Listen to a batch of forceOrder streams using ONE multiplex socket.
        All streams in the batch share the same socket to avoid overwhelming
        the shared BinanceSocketManager with per-symbol connections.
        """
        attempt = 0

        while self.running:
            try:
                # Ensure client/socket_manager are alive before connecting
                await self._ensure_client_alive()
                cm = self.get_client_manager()

                async with await cm.create_multiplex_socket(streams) as ws:
                    # Reset attempt counter on successful connection
                    attempt = 0
                    logger.info(f"Liquidation monitor: connected to {len(streams)} streams")

                    while self.running:
                        try:
                            # recv() with timeout to detect dead connections
                            msg = await asyncio.wait_for(
                                ws.recv(),
                                timeout=WEBSOCKET_RECV_TIMEOUT
                            )
                            if msg and 'data' in msg:
                                await self._process_liquidation(msg['data'])

                        except asyncio.TimeoutError:
                            # No message received within timeout — connection may be dead
                            logger.debug(
                                f"Liquidation batch WebSocket timeout, reconnecting..."
                            )
                            break  # Exit inner loop to reconnect

                        except BinanceWebsocketQueueOverflow:
                            # Queue overflow — reconnect WITHOUT resetting the shared manager.
                            # The batch design keeps the total stream count manageable.
                            logger.warning(
                                f"Liquidation batch WebSocket queue overflow, reconnecting..."
                            )
                            break  # Exit inner loop to reconnect

            except asyncio.CancelledError:
                break

            except BinanceWebsocketUnableToConnect:
                attempt += 1
                delay = self._calculate_backoff(attempt)
                logger.warning(
                    f"Liquidation batch WebSocket unable to connect "
                    f"(attempt {attempt}), retrying in {delay:.1f}s..."
                )

            except BinanceWebsocketQueueOverflow:
                # Caught here as a safety net in case the inner loop catch is bypassed
                attempt += 1
                delay = self._calculate_backoff(attempt)
                logger.warning(
                    f"Liquidation batch WebSocket queue overflow "
                    f"(attempt {attempt}), retrying in {delay:.1f}s..."
                )

            except Exception as e:
                attempt += 1
                delay = self._calculate_backoff(attempt)
                logger.warning(
                    f"Liquidation batch WebSocket error "
                    f"(attempt {attempt}): {e!r}, retrying in {delay:.1f}s..."
                )

            # Safety valve: too many consecutive errors
            if attempt >= MAX_CONSECUTIVE_ERRORS:
                logger.error(
                    f"Liquidation batch WebSocket: "
                    f"{MAX_CONSECUTIVE_ERRORS} consecutive errors, pausing 5 min"
                )
                await asyncio.sleep(300)
                attempt = 0  # Reset after long pause
                continue

            # Wait with backoff before reconnecting
            if attempt > 0:
                await asyncio.sleep(delay)

    async def _process_liquidation(self, data: dict):
        """Process a liquidation event from the forceOrder stream."""
        # forceOrder format: data['o'] contains the order fields
        order = data.get('o', data)  # fall back to flat dict for safety
        symbol = order['s']
        side = order['S']   # 'BUY' = short liquidation, 'SELL' = long liquidation
        quantity = float(order['q'])
        price = float(order['p'])
        notional = quantity * price

        if notional < self.threshold:
            return

        timestamp = datetime.fromtimestamp(order['T'] / 1000)
        liquidation_type = 'LONG' if side == 'SELL' else 'SHORT'

        if symbol not in self.liquidations:
            self.liquidations[symbol] = []
        self.liquidations[symbol].append((notional, liquidation_type, quantity, price, timestamp))

        self._clean_old_liquidations(symbol)

    def _clean_old_liquidations(self, symbol: str):
        """Remove liquidations older than window."""
        if symbol not in self.liquidations:
            return
        cutoff = datetime.now() - self.dominance_window
        self.liquidations[symbol] = [liq for liq in self.liquidations[symbol] if liq[4] > cutoff]

    def get_dominance(self, symbol: str) -> Tuple[float, float, str]:
        """
        Return total long and short liquidated notional and dominance signal.

        Returns:
            Tuple of (long_total, short_total, dominance)
            dominance: 'BULLISH' (shorts liquidated > longs), 'BEARISH', or 'NEUTRAL'
        """
        if symbol not in self.liquidations or not self.liquidations[symbol]:
            return 0.0, 0.0, 'NEUTRAL'

        long_total = sum(liq[0] for liq in self.liquidations[symbol] if liq[1] == 'LONG')
        short_total = sum(liq[0] for liq in self.liquidations[symbol] if liq[1] == 'SHORT')

        if long_total > short_total:
            dominance = 'BEARISH'  # More long positions getting liquidated = price falling
        elif short_total > long_total:
            dominance = 'BULLISH'  # More short positions getting liquidated = price rising
        else:
            dominance = 'NEUTRAL'

        return long_total, short_total, dominance

    def get_recent_liquidations(self, symbol: str, limit: int = 5) -> List[dict]:
        """Get recent liquidations for display."""
        if symbol not in self.liquidations or not self.liquidations[symbol]:
            return []
        recent = sorted(self.liquidations[symbol], key=lambda x: x[4], reverse=True)[:limit]
        return [{
            'notional': liq[0],
            'side': liq[1],
            'quantity': liq[2],
            'price': liq[3],
            'time': liq[4].strftime('%H:%M:%S')
        } for liq in recent]
