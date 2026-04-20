"""
Funding Rate Monitor for Binance Futures

Monitors funding rates to detect overextended positions:
- High positive funding (>0.05%) = too many longs → contrarian SHORT signal
- High negative funding (<-0.05%) = too many shorts → contrarian LONG signal

This is one of the most reliable mean-reversion signals in crypto futures.
"""
import asyncio
import logging
from typing import Dict, Optional
import time
import config
from modules.base import Monitor

logger = logging.getLogger(__name__)


class FundingRateMonitor(Monitor):
    """
    Monitors funding rates for all tracked symbols.
    Fetches latest funding rate and next funding time.
    """
    
    # Thresholds for extreme funding (contrarian signals)
    HIGH_FUNDING_THRESHOLD = 0.0005    # 0.05% - consider SHORT
    LOW_FUNDING_THRESHOLD = -0.0005    # -0.05% - consider LONG
    EXTREME_HIGH_FUNDING = 0.001       # 0.1% - strong SHORT signal
    EXTREME_LOW_FUNDING = -0.001       # -0.1% - strong LONG signal
    
    def __init__(self):
        super().__init__()
        self.funding_rates: Dict[str, float] = {}
        self.next_funding_time: Dict[str, int] = {}
        self.update_interval = 300  # 5 minutes - funding updates every 8 hours
        self._fetch_task: Optional[asyncio.Task] = None
    
    async def _on_initialize(self):
        """Initialize funding rate data."""
        pass
    
    async def _on_start(self):
        """Start the funding rate update loop."""
        self._fetch_task = self._create_task(self._update_loop())
    
    async def _on_stop(self):
        """Cleanup on stop."""
        if self._fetch_task and not self._fetch_task.done():
            self._fetch_task.cancel()
            try:
                await self._fetch_task
            except asyncio.CancelledError:
                pass
    
    async def _update_loop(self):
        """Update funding rates periodically."""
        while self.running:
            try:
                await self._fetch_all_funding_rates()
            except Exception as e:
                logger.error(f"Funding rate fetch error: {e}")
            
            await asyncio.sleep(self.update_interval)
    
    async def _fetch_all_funding_rates(self):
        """Fetch latest funding rate for all symbols."""
        symbols = config.SYMBOLS if isinstance(config.SYMBOLS, list) else []
        
        # If SYMBOLS is "ALL", we need to get symbols from elsewhere
        # For now, use a reasonable default list
        if not symbols:
            symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']
        
        for symbol in symbols:
            try:
                await self._fetch_funding_rate(symbol)
                await asyncio.sleep(0.1)  # Rate limiting
            except Exception as e:
                logger.debug(f"Funding rate error for {symbol}: {e}")
    
    async def _fetch_funding_rate(self, symbol: str):
        """Fetch latest funding rate for a single symbol."""
        try:
            # Get latest funding rate (limit=1 gives most recent)
            data = await self.client.futures_funding_rate(symbol=symbol, limit=1)
            
            if data and len(data) > 0:
                funding_rate = float(data[0]['fundingRate'])
                funding_time = data[0]['fundingTime']
                
                self.funding_rates[symbol] = funding_rate
                self.next_funding_time[symbol] = funding_time
                
                # Log extreme funding rates
                if abs(funding_rate) >= self.EXTREME_HIGH_FUNDING:
                    logger.info(
                        f"[FUNDING] EXTREME: {symbol} funding={funding_rate*100:.4f}% "
                        f"{'(SHORT signal)' if funding_rate > 0 else '(LONG signal)'}"
                    )
                elif abs(funding_rate) >= self.HIGH_FUNDING_THRESHOLD:
                    logger.debug(
                        f"[FUNDING] Elevated: {symbol} funding={funding_rate*100:.4f}%"
                    )
                    
        except Exception as e:
            logger.debug(f"Error fetching funding for {symbol}: {e}")
    
    def get_funding_rate(self, symbol: str) -> float:
        """Get current funding rate for a symbol."""
        return self.funding_rates.get(symbol, 0.0)
    
    def get_funding_signal(self, symbol: str) -> str:
        """
        Get contrarian signal based on funding rate.
        
        Returns:
            'BEARISH' - High positive funding, expect reversal down
            'BULLISH' - High negative funding, expect reversal up
            'NEUTRAL' - Normal funding, no signal
        """
        rate = self.get_funding_rate(symbol)
        
        if rate >= self.EXTREME_HIGH_FUNDING:
            return 'STRONG_BEARISH'
        elif rate <= self.EXTREME_LOW_FUNDING:
            return 'STRONG_BULLISH'
        elif rate >= self.HIGH_FUNDING_THRESHOLD:
            return 'BEARISH'
        elif rate <= self.LOW_FUNDING_THRESHOLD:
            return 'BULLISH'
        else:
            return 'NEUTRAL'
    
    def get_funding_score(self, symbol: str, signal_type: str) -> int:
        """
        Calculate score contribution based on funding rate and signal direction.
        
        Args:
            symbol: Trading pair
            signal_type: 'STRONG_LONG' or 'STRONG_SHORT'
        
        Returns:
            Score adjustment (-2 to +2)
            - Positive = supports the signal
            - Negative = contradicts the signal
        """
        rate = self.get_funding_rate(symbol)
        
        if signal_type == 'STRONG_LONG':
            # For LONG signals: negative funding is good (crowd is short)
            if rate <= self.EXTREME_LOW_FUNDING:
                return +2  # Strong support
            elif rate <= self.LOW_FUNDING_THRESHOLD:
                return +1  # Moderate support
            elif rate >= self.EXTREME_HIGH_FUNDING:
                return -2  # Strong contradiction - avoid long
            elif rate >= self.HIGH_FUNDING_THRESHOLD:
                return -1  # Moderate contradiction
            else:
                return 0
        
        else:  # STRONG_SHORT
            # For SHORT signals: positive funding is good (crowd is long)
            if rate >= self.EXTREME_HIGH_FUNDING:
                return +2  # Strong support
            elif rate >= self.HIGH_FUNDING_THRESHOLD:
                return +1  # Moderate support
            elif rate <= self.EXTREME_LOW_FUNDING:
                return -2  # Strong contradiction - avoid short
            elif rate <= self.LOW_FUNDING_THRESHOLD:
                return -1  # Moderate contradiction
            else:
                return 0
    
    def should_filter_signal(self, symbol: str, signal_type: str) -> bool:
        """
        Determine if a signal should be filtered out due to extreme funding.
        
        Returns True if signal should be SKIPPED.
        """
        rate = self.get_funding_rate(symbol)
        
        if signal_type == 'STRONG_LONG':
            # Skip LONG if funding is extremely positive (crowd overly bullish)
            return rate >= self.EXTREME_HIGH_FUNDING
        else:  # STRONG_SHORT
            # Skip SHORT if funding is extremely negative (crowd overly bearish)
            return rate <= self.EXTREME_LOW_FUNDING
    
    def get_time_to_next_funding(self, symbol: str) -> Optional[int]:
        """Get seconds until next funding payment."""
        funding_time = self.next_funding_time.get(symbol)
        if not funding_time:
            return None
        
        current_time = int(time.time() * 1000)
        return max(0, (funding_time - current_time) // 1000)
