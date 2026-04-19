"""
Whale trade monitor for tracking large trades on Binance Futures.
Uses WebSocket stream to receive real-time aggregate trade data.
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
# Lower = less queue pressure on BinanceSocketManager, fewer overflow errors.
# 3 streams per socket = 5 batches for 15 symbols (manageable message rate).
STREAMS_PER_SOCKET = 3


# Tiered whale thresholds — smaller-cap assets have lower minimums
# to ensure the whale component fires more often across altcoins.
WHALE_TIERS = {
    'large_cap':  ['BTCUSDT', 'ETHUSDT'],
    'mid_cap':    ['SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'TAOUSDT'],
    'small_cap':  [],  # all other symbols fall here
}


class WhaleTradeMonitor(Monitor):
    """
    Monitors whale trades via Binance WebSocket stream.
    Threshold is tiered by market cap: $500K (large), $200K (mid), $100K (small).
    Tracks buyer vs seller dominance to determine market pressure.
    """

    def __init__(self):
        super().__init__()
        self.whale_trades: Dict[str, List[Tuple[float, str, float, datetime]]] = {}
        self.window = timedelta(minutes=10)  # Analysis window
        # No single hardcoded threshold — use get_threshold(symbol) instead

    def get_threshold(self, symbol: str) -> float:
        """Return the whale detection threshold for a given symbol based on its tier."""
        if symbol in WHALE_TIERS['large_cap']:
            return 500_000   # $500K — BTC, ETH
        elif symbol in WHALE_TIERS['mid_cap']:
            return 200_000   # $200K — SOL, BNB, XRP, TAO
        else:
            return 100_000   # $100K — all other alts

    async def _on_initialize(self):
        """Initialize whale trade storage per symbol."""
        for symbol in config.SYMBOLS:
            self.whale_trades[symbol] = []

    async def _on_start(self):
        """Subscribe to aggregate trades stream for all symbols, batched into multiplex sockets."""
        streams = [f"{s.lower()}@aggTrade" for s in config.SYMBOLS]
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
        """Cleanup on stop."""
        pass

    async def _listen_batch(self, streams: List[str]):
        """
        Listen to a batch of aggregate-trade streams using ONE multiplex socket.
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
                    logger.info(f"Whale monitor: connected to {len(streams)} streams")

                    while self.running:
                        try:
                            msg = await asyncio.wait_for(
                                ws.recv(),
                                timeout=WEBSOCKET_RECV_TIMEOUT
                            )
                            if msg and 'data' in msg:
                                await self._process_trade(msg['data'])

                        except BinanceWebsocketQueueOverflow:
                            # Queue overflow from ws.recv() — break inner loop to reconnect
                            logger.warning(
                                f"Whale batch WebSocket queue overflow (from recv), reconnecting..."
                            )
                            break

                        except asyncio.TimeoutError:
                            # No message received within timeout — connection may be dead
                            logger.debug(
                                f"Whale batch WebSocket timeout, reconnecting..."
                            )
                            break  # Exit inner loop to reconnect

                        except BinanceWebsocketUnableToConnect:
                            logger.warning(
                                f"Whale batch WebSocket unable to connect, reconnecting..."
                            )
                            break

            except asyncio.CancelledError:
                break

            except BinanceWebsocketQueueOverflow:
                # Queue overflow from ws.recv() or __aenter__ — break inner loop to reconnect
                # Self-correcting: reset attempt so we don't count it against MAX_CONSECUTIVE_ERRORS
                logger.warning(
                    f"Whale batch WebSocket queue overflow (outer), reconnecting..."
                )
                break

            except BinanceWebsocketUnableToConnect:
                attempt += 1
                delay = self._calculate_backoff(attempt)
                logger.warning(
                    f"Whale batch WebSocket unable to connect "
                    f"(attempt {attempt}), retrying in {delay:.1f}s..."
                )

            except Exception as e:
                if isinstance(e, BinanceWebsocketQueueOverflow):
                    # Already handled above but double-check
                    break
                attempt += 1
                delay = self._calculate_backoff(attempt)
                logger.warning(
                    f"Whale batch WebSocket error "
                    f"(attempt {attempt}): {e}, retrying in {delay:.1f}s..."
                )

            # Safety valve: too many consecutive errors
            if attempt >= MAX_CONSECUTIVE_ERRORS:
                logger.error(
                    f"Whale batch WebSocket: "
                    f"{MAX_CONSECUTIVE_ERRORS} consecutive errors, pausing 5 min"
                )
                await asyncio.sleep(300)
                attempt = 0  # Reset after long pause
                continue

            # Wait with backoff before reconnecting
            if attempt > 0:
                await asyncio.sleep(delay)

    async def _process_trade(self, data: dict):
        """Process a trade event and check if it's a whale trade."""
        symbol = data['s']
        price = float(data['p'])
        quantity = float(data['q'])
        notional = price * quantity

        if notional < self.get_threshold(symbol):
            return

        # is_buyer_maker = True means seller is aggressive (taker sell)
        # is_buyer_maker = False means buyer is aggressive (taker buy)
        is_buyer_maker = data['m']
        side = 'SELLER' if is_buyer_maker else 'BUYER'
        timestamp = datetime.fromtimestamp(data['T'] / 1000)

        if symbol not in self.whale_trades:
            self.whale_trades[symbol] = []
        self.whale_trades[symbol].append((notional, side, price, timestamp))

        self._clean_old_trades(symbol)

    def _clean_old_trades(self, symbol: str):
        """Remove trades older than window."""
        if symbol not in self.whale_trades:
            return
        cutoff = datetime.now() - self.window
        self.whale_trades[symbol] = [trade for trade in self.whale_trades[symbol] if trade[3] > cutoff]

    def get_dominance(self, symbol: str) -> Tuple[int, int, str]:
        """
        Return count of buyer vs seller whales and dominance signal.

        Returns:
            Tuple of (buyer_count, seller_count, dominance)
            dominance: 'BUYER_DOMINANCE', 'SELLER_DOMINANCE', or 'NEUTRAL'
        """
        if symbol not in self.whale_trades or not self.whale_trades[symbol]:
            return 0, 0, 'NEUTRAL'

        buyers = sum(1 for trade in self.whale_trades[symbol] if trade[1] == 'BUYER')
        sellers = sum(1 for trade in self.whale_trades[symbol] if trade[1] == 'SELLER')

        if buyers > sellers:
            dominance = 'BUYER_DOMINANCE'
        elif sellers > buyers:
            dominance = 'SELLER_DOMINANCE'
        else:
            dominance = 'NEUTRAL'

        return buyers, sellers, dominance

    def get_recent_whales(self, symbol: str, limit: int = 5) -> List[dict]:
        """Get recent whale trades for display."""
        if symbol not in self.whale_trades or not self.whale_trades[symbol]:
            return []
        recent = sorted(self.whale_trades[symbol], key=lambda x: x[3], reverse=True)[:limit]
        return [{
            'notional': trade[0],
            'side': trade[1],
            'price': trade[2],
            'time': trade[3].strftime('%H:%M:%S')
        } for trade in recent]

    def check_alert(self, symbol: str) -> bool:
        """Check if 3+ whale trades in same direction within window."""
        if symbol not in self.whale_trades or not self.whale_trades[symbol]:
            return False

        buyers = [trade for trade in self.whale_trades[symbol] if trade[1] == 'BUYER']
        sellers = [trade for trade in self.whale_trades[symbol] if trade[1] == 'SELLER']

        return len(buyers) >= 3 or len(sellers) >= 3
