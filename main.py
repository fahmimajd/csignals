"""
Crypto Signal Monitor - Main Application Entry Point
Monitors Binance Futures for trading signals.
"""
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta
from typing import Dict

import config
from modules.base import BinanceClientManager
from modules.liquidation import LiquidationMonitor
from modules.orderbook import OrderBookMonitor
from modules.whale import WhaleTradeMonitor
from modules.openinterest import OpenInterestTracker
from modules.tp_sl_calculator import TPSLCalculator
from modules.trailing_stop import TrailingStopManager
from modules.aggregator import SignalAggregator
from modules.confirmation import SignalConfirmation
from modules.database import Database
from modules.telegram_bot import TelegramNotifier
from modules.display import TerminalDisplay
from modules.hold_duration import HoldDurationCalculator
from modules.exit_monitor import ExitMonitor
from modules.monte_carlo import MonteCarloFilter

logger = logging.getLogger(__name__)


class CryptoSignalApp:
    """Main application class managing all monitors and signal processing."""

    def __init__(self):
        self.running = False
        self.monitors = {}
        self.display = TerminalDisplay()
        self.aggregator = SignalAggregator()
        self.confirmation = SignalConfirmation()
        self.database = Database()
        self.trailing_manager = TrailingStopManager()
        self.telegram_notifier = TelegramNotifier()
        self.client_manager = BinanceClientManager()
        self.hold_calculator = HoldDurationCalculator()
        self.monte_carlo_filter = MonteCarloFilter()
        
        # CRITICAL FIX: Per-symbol locks to prevent race conditions
        self._symbol_locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        
        # Track active signals for hold duration monitoring
        self.active_signals: Dict[int, Dict] = {}

    def _get_symbol_lock(self, symbol: str) -> asyncio.Lock:
        """Get or create a lock for a specific symbol."""
        if symbol not in self._symbol_locks:
            self._symbol_locks[symbol] = asyncio.Lock()
        return self._symbol_locks[symbol]

    async def initialize(self):
        """Initialize all modules and connections."""
        logger.info("Initializing Crypto Signal App...")

        # Ensure logs directory exists
        os.makedirs(os.path.dirname(config.LOG_FILE.rstrip('/')), exist_ok=True)

        # Fetch available symbols if needed
        await self._fetch_available_symbols()

        # Initialize monitors
        self.monitors['liquidation'] = LiquidationMonitor()
        self.monitors['orderbook'] = OrderBookMonitor()
        self.monitors['whale'] = WhaleTradeMonitor()
        self.monitors['openinterest'] = OpenInterestTracker()
        self.monitors['tp_sl'] = TPSLCalculator()

        # Initialize all monitors (they share the client manager)
        for name, monitor in self.monitors.items():
            await monitor.initialize()
            logger.info(f"{name} monitor initialized")

        # Initialize database
        try:
            await self.database.initialize()
            logger.info("Database initialized")
        except Exception as e:
            logger.warning(f"Database initialization failed: {e}")

        # Initialize telegram
        await self.telegram_notifier.initialize()

        logger.info(f"Initialization complete. Monitoring {len(config.SYMBOLS)} symbols.")

    async def _fetch_available_symbols(self):
        """Fetch available symbols from Binance if SYMBOLS is 'ALL'."""
        if isinstance(config.SYMBOLS, str) and config.SYMBOLS.upper() == 'ALL':
            try:
                client = await self.client_manager.get_client()

                # Get all USDT futures symbols
                exchange_info = await client.futures_exchange_info()
                symbols = []

                for s in exchange_info.get('symbols', []):
                    if s.get('status') == 'TRADING' and s.get('quoteAsset') == 'USDT':
                        symbol = s.get('symbol')
                        if config.SYMBOL_PATTERN and config.SYMBOL_PATTERN not in symbol:
                            continue
                        # Skip non-ASCII symbols (Chinese characters, etc.)
                        if not symbol.isascii():
                            logger.debug(f"Skipping non-ASCII symbol: {symbol}")
                            continue
                        symbols.append(symbol)

                # Filter by volume if configured
                if config.TOP_N_BY_VOLUME > 0:
                    try:
                        ticker = await client.futures_ticker()
                        volume_dict = {}
                        for t in ticker:
                            if t.get('symbol') in symbols:
                                volume_dict[t.get('symbol')] = float(t.get('quoteVolume', 0))

                        symbols = sorted(
                            symbols,
                            key=lambda x: volume_dict.get(x, 0),
                            reverse=True
                        )[:config.TOP_N_BY_VOLUME]
                        logger.info(f"Selected top {len(symbols)} symbols by 24h volume")
                    except Exception as e:
                        logger.warning(f"Could not filter by volume: {e}")

                config.SYMBOLS = symbols
                logger.info(f"Loaded {len(symbols)} symbols from Binance")

            except Exception as e:
                logger.error(f"Failed to fetch symbols: {e}")
                logger.info("Using default symbols")

    async def start(self):
        """Start all monitors and the main loop."""
        self.running = True
        logger.info("Starting monitoring...")

        # Start all monitors
        for name, monitor in self.monitors.items():
            await monitor.start()

        # Main loop
        asyncio.create_task(self._main_loop())

        # Hold duration monitor loop (runs every 60 seconds)
        asyncio.create_task(self._hold_duration_monitor())

        # Handle shutdown signals
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

    async def _main_loop(self):
        """Main update loop that processes signals and updates display."""
        while self.running:
            try:
                # Collect data from all monitors
                symbol_data = await self._collect_data()

                # Update aggregator
                await self._update_aggregator(symbol_data)

                # Process signals for each symbol (with per-symbol locking to prevent race conditions)
                signals = {}
                for symbol in config.SYMBOLS:
                    async with self._get_symbol_lock(symbol):
                        signal_type, score_100, raw_score, details, emoji = await self.aggregator.get_signal(symbol)
                        signals[symbol] = {
                            'signal': signal_type,
                            'score_100': score_100,
                            'raw_score': raw_score,
                            'details': details,
                            'emoji': emoji
                        }

                        # Check confirmation status
                        is_confirmed, confirmed_signal, confirmed_score = \
                            await self.confirmation.update(symbol, raw_score)

                        signals[symbol]['is_confirmed'] = is_confirmed
                        signals[symbol]['confirmed_signal'] = confirmed_signal

                        progress, total = self.confirmation.get_confirmation_progress(symbol)
                        signals[symbol]['confirmation_progress'] = progress
                        signals[symbol]['confirmation_total'] = total

                        # Process confirmed strong signals
                        if is_confirmed and confirmed_signal in ['STRONG_LONG', 'STRONG_SHORT']:
                            tp_sl_info = await self._calculate_tp_sl(
                                symbol, confirmed_signal, symbol_data.get(symbol, {})
                            )

                            signals[symbol]['tp_sl'] = tp_sl_info

                            # Check R:R ratio
                            rr_value = self._extract_rr_value(tp_sl_info)

                            if rr_value >= config.MIN_RR_RATIO:
                                price = symbol_data.get(symbol, {}).get('openinterest', {}).get('price', 0)
                                if price > 0:
                                    signal_data = {
                                        'signal_type': confirmed_signal,
                                        'score': confirmed_score,
                                        'entry_price': price,
                                        'stop_loss': tp_sl_info.get('stop_loss', 0),
                                        'take_profit': tp_sl_info.get('take_profit', 0),
                                        'atr_value': tp_sl_info.get('atr', 0),
                                        'rr_ratio': rr_value,
                                        'tp_source': tp_sl_info.get('tp_source', 'ATR'),
                                        'trail_start': tp_sl_info.get('trail_start', 0),
                                        'trail_stop': tp_sl_info.get('trail_stop', 0),
                                        'confirmed_at': datetime.now()
                                    }

                                    # Calculate hold duration
                                    oi_data = symbol_data.get(symbol, {}).get('openinterest', {})
                                    whale_data = symbol_data.get(symbol, {}).get('whale', {})

                                    hold_result = self.hold_calculator.calculate(
                                        atr_value=tp_sl_info.get('atr', 0),
                                        entry_price=price,
                                        raw_score=confirmed_score,
                                        taker_buy_ratio=oi_data.get('taker_ratio', 0.5),
                                        oi_change_pct=oi_data.get('change', 0),
                                        whale_trade_count=whale_data.get('buyers', 0) + whale_data.get('sellers', 0)
                                    )

                                    # Calculate deadline
                                    hold_deadline = datetime.now() + timedelta(hours=hold_result['hold_hours'])

                                    # === MONTE CARLO FILTER (after TP/SL calculated, before Telegram/DB) ===
                                    mc_result = await self.monte_carlo_filter.evaluate(
                                        symbol=symbol,
                                        entry_price=price,
                                        take_profit=tp_sl_info.get('take_profit', 0),
                                        stop_loss=tp_sl_info.get('stop_loss', 0),
                                        hold_hours=hold_result['hold_hours'],
                                        signal_type=confirmed_signal
                                    )

                                    # Check if MC says to skip
                                    if mc_result.get('skipped', False):
                                        logger.info(
                                            f"[MC] {symbol} skipped: prob_tp={mc_result['prob_tp']}% "
                                            f"< {config.MC_MIN_PROB_TP}% threshold"
                                        )
                                        continue  # Skip this signal, don't send Telegram or save to DB

                                    # Format hold_duration for telegram
                                    hold_duration_tg = {
                                        'formatted_duration': self.hold_calculator.format_duration(hold_result['hold_hours']),
                                        'deadline_str': hold_deadline.strftime('%H:%M WIB'),
                                        'formula_str': hold_result['formula_str'],
                                        'atr_factor': hold_result['atr_factor'],
                                        'score_factor': hold_result['score_factor'],
                                        'volume_factor': hold_result['volume_factor']
                                    }

                                    # Add MC info to tp_sl_info for display
                                    tp_sl_info['mc_prob_tp'] = mc_result['prob_tp']
                                    tp_sl_info['mc_prob_sl'] = mc_result['prob_sl']
                                    tp_sl_info['mc_prob_expire'] = mc_result['prob_expire']
                                    tp_sl_info['mc_confidence'] = mc_result['confidence']

                                    # === SEND TELEGRAM FIRST (most important) ===
                                    try:
                                        await self.telegram_notifier.send_signal_alert(
                                            symbol, confirmed_signal,
                                            self.aggregator.raw_to_100(confirmed_score),
                                            confirmed_score,
                                            details, tp_sl_info, hold_duration_tg
                                        )
                                    except Exception as tg_err:
                                        logger.error(f"Telegram send failed for {symbol}: {tg_err}")

                                    # === THEN SAVE TO DB (non-critical, wrap each in try/except) ===
                                    signal_id = None
                                    try:
                                        # Add MC data to signal_data
                                        signal_data['mc_prob_tp'] = mc_result['prob_tp']
                                        signal_data['mc_prob_sl'] = mc_result['prob_sl']
                                        signal_data['mc_prob_expire'] = mc_result['prob_expire']
                                        signal_data['mc_confidence'] = mc_result['confidence']

                                        signal_id = await self.database.save_signal(symbol, signal_data)
                                        logger.info(f"Signal saved to DB: {symbol} {confirmed_signal} (ID: {signal_id})")
                                    except Exception as db_err:
                                        logger.error(f"Failed to save signal to DB for {symbol}: {db_err}")

                                    if signal_id:
                                        try:
                                            await self.database.save_hold_duration(
                                                signal_id=signal_id,
                                                hold_hours=hold_result['hold_hours'],
                                                hold_deadline=hold_deadline,
                                                atr_factor=hold_result['atr_factor'],
                                                score_factor=hold_result['score_factor'],
                                                volume_factor=hold_result['volume_factor'],
                                                volume_score=hold_result['volume_score']
                                            )
                                        except Exception as db_err:
                                            logger.error(f"Failed to save hold duration for signal {signal_id}: {db_err}")

                                        # Track active signal (in-memory, no DB dependency)
                                        self.active_signals[signal_id] = {
                                            'symbol': symbol,
                                            'signal_type': confirmed_signal,
                                            'score_100': self.aggregator.raw_to_100(confirmed_score),
                                            'raw_score': confirmed_score,
                                            'entry_price': price,
                                            'hold_hours': hold_result['hold_hours'],
                                            'hold_deadline': hold_deadline,
                                            'extended': False,
                                            'atr_value': tp_sl_info.get('atr', 0),
                                            'details': details,
                                            'tp_sl': tp_sl_info,
                                            'hold_result': hold_result
                                        }
                        else:
                            if not is_confirmed:
                                logger.debug(f"Signal skipped for {symbol}: not yet confirmed")
                            elif confirmed_signal not in ['STRONG_LONG', 'STRONG_SHORT']:
                                logger.debug(f"Signal skipped for {symbol}: signal={confirmed_signal}, not strong")
                            else:
                                # R:R ratio below minimum — extract it safely
                                rr_v = self._extract_rr_value(
                                    signals[symbol].get('tp_sl', {}) if 'tp_sl' in signals[symbol] else {}
                                )
                                logger.info(
                                    f"Signal skipped for {symbol}: "
                                    f"R:R ratio {rr_v:.2f} below minimum {config.MIN_RR_RATIO}"
                                )

                # Prepare and update display
                display_data = await self._prepare_display_data(symbol_data, signals)
                try:
                    self.display.update_display(display_data)
                except (BrokenPipeError, SystemExit, OSError) as disp_err:
                    logger.warning(f"Display error (non-fatal): {disp_err}, continuing without display update")

                # Periodically decay aggregator scores (every ~60 seconds)
                if datetime.now().second < config.UPDATE_INTERVAL:
                    self.aggregator.reset_scores()

                await asyncio.sleep(config.UPDATE_INTERVAL)

            except SystemExit:
                # SystemExit from display/rich should NOT kill the main loop
                logger.warning("SystemExit caught in main loop (likely BrokenPipe from display), continuing...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(5)

    async def _hold_duration_monitor(self):
        """
        Exit Monitor — menggunakan ExitMonitor class yang query DB langsung.
        Tidak bergantung pada self.active_signals yang hilang saat restart.
        """
        # Buat async price fetcher dari OI monitor
        async def price_fetcher(symbol: str) -> float:
            return self._get_current_price(symbol)

        exit_monitor = ExitMonitor(
            database=self.database,
            price_fetcher=price_fetcher,
            confirmation=self.confirmation,
            aggregator=self.aggregator,
            hold_calculator=self.hold_calculator,
            trailing_manager=self.trailing_manager,
            telegram_notifier=self.telegram_notifier
        )
        await exit_monitor.run()

    def _get_current_price(self, symbol: str) -> float:
        """Get current price for a symbol from monitors."""
        try:
            oi = self.monitors.get('openinterest')
            if oi is None:
                return 0
            return oi.price_data.get(symbol, 0) or 0
        except Exception:
            return 0

    def _extract_rr_value(self, tp_sl_info: Dict) -> float:
        """Extract R:R ratio as float from tp_sl_info."""
        if not tp_sl_info or 'rr_ratio' not in tp_sl_info:
            return 0
        try:
            rr_str = str(tp_sl_info['rr_ratio'])
            # Handle '1:X.X' format by splitting on ':'
            if ':' in rr_str:
                rr_str = rr_str.split(':', 1)[1]
            if rr_str.upper() == 'N/A' or rr_str.strip() == '':
                return 0
            return float(rr_str)
        except (ValueError, AttributeError, IndexError):
            return 0

    async def _collect_data(self) -> Dict:
        """Collect data from all monitors."""
        data = {}

        for symbol in config.SYMBOLS:
            symbol_data = {}

            # Liquidation data
            liq = self.monitors['liquidation']
            long_total, short_total, dominance = liq.get_dominance(symbol)
            symbol_data['liquidation'] = {
                'long': long_total,
                'short': short_total,
                'dominance': dominance
            }

            # Order book data
            ob = self.monitors['orderbook']
            symbol_data['orderbook'] = {
                'imbalance': ob.get_imbalance(symbol),
                'signal': ob.get_imbalance_signal(symbol),
                'bar': ob.get_visual_bar(symbol)
            }

            # Whale data
            whale = self.monitors['whale']
            buyers, sellers, whale_dominance = whale.get_dominance(symbol)
            symbol_data['whale'] = {
                'buyers': buyers,
                'sellers': sellers,
                'dominance': whale_dominance
            }

            # Open interest data
            oi = self.monitors['openinterest']
            oi_signal, _confirmed = oi.get_oi_signal(symbol)   # (base_signal, confirmed)
            oi_change = oi.get_oi_change(symbol)
            price = oi.price_data.get(symbol, 0)
            symbol_data['openinterest'] = {
                'signal': oi_signal,
                'change': oi_change,
                'price': price,
                'long_short_ratio': oi.get_long_short_ratio(symbol),
                'taker_ratio': oi.get_taker_volume_ratio(symbol)
            }

            data[symbol] = symbol_data

        return data

    async def _update_aggregator(self, symbol_data: Dict):
        """Update aggregator with latest data from all monitors."""
        for symbol, data in symbol_data.items():
            # Liquidation
            liq = data['liquidation']
            self.aggregator.update_liquidation_signal(
                symbol, liq['dominance'], liq['long'], liq['short']
            )

            # Order book
            ob = data['orderbook']
            self.aggregator.update_orderbook_signal(
                symbol, ob['imbalance'], ob['signal']
            )

            # Whale
            whale = data['whale']
            self.aggregator.update_whale_signal(
                symbol, whale['dominance'], whale['buyers'], whale['sellers']
            )

            # Open interest
            oi = data['openinterest']
            self.aggregator.update_oi_signal(
                symbol, oi['signal'], oi['change'], oi['price']
            )

            # Taker volume
            self.aggregator.update_taker_volume_signal(symbol, oi['taker_ratio'])

            # Top trader ratio
            self.aggregator.update_top_trader_signal(symbol, oi['long_short_ratio'])

    async def _calculate_tp_sl(self, symbol: str, signal: str, symbol_data: Dict) -> Dict:
        """Calculate TP/SL levels for a confirmed signal."""
        tp_sl = self.monitors['tp_sl']

        price = symbol_data.get('openinterest', {}).get('price', 0)
        if price == 0:
            return {}

        # Calculate ATR
        atr = await tp_sl.calculate_atr(symbol)

        # Determine side
        side = 'LONG' if signal == 'STRONG_LONG' else 'SHORT'

        # Calculate SL and TP
        sl_price, sl_percent = tp_sl.calculate_stop_loss(symbol, price, side)
        tp_price, tp_percent, tp_source = await tp_sl.calculate_take_profit(symbol, price, side)

        # Calculate R:R ratio
        if side == 'LONG':
            profit = tp_price - price
            loss = price - sl_price
        else:
            profit = price - tp_price
            loss = sl_price - price

        rr_ratio = profit / loss if loss > 0 else 0

        # Calculate trailing stop levels
        trail_trigger, trail_stop = tp_sl.get_trail_levels(symbol, price, side, price)
        trail_distance = atr * config.TRAIL_DISTANCE_ATR

        # If trailing not yet activated, show PENDING stop level (entry ± ATR distance)
        # so user can see where the stop would be set once trailing activates
        if trail_stop is None and atr > 0:
            trail_stop = price - trail_distance if side == 'LONG' else price + trail_distance

        return {
            'entry_zone': f"{price*0.999:,.0f} - {price*1.001:,.0f}",
            'stop_loss': sl_price,
            'sl_percent': sl_percent,
            'take_profit': tp_price,
            'tp_percent': tp_percent,
            'tp_source': tp_source,
            'trail_start': price + (atr * config.TRAIL_TRIGGER_ATR) if side == 'LONG'
                           else price - (atr * config.TRAIL_TRIGGER_ATR),
            'trail_stop': trail_stop,
            'rr_ratio': f"1:{rr_ratio:.1f}" if rr_ratio > 0 else "N/A",
            'atr': atr
        }

    async def _prepare_display_data(self, symbol_data: Dict, signals: Dict) -> Dict:
        """Prepare data for terminal display."""
        display_data = {}

        for symbol in config.SYMBOLS:
            data = symbol_data.get(symbol, {})
            signal_info = signals.get(symbol, {})

            tp_sl = self.monitors['tp_sl']

            # Get regime info from aggregator cache
            regime_cache = self.aggregator.regime_detector._cache.get(symbol)
            regime_info = {}
            if regime_cache:
                regime_info = {
                    'regime': regime_cache.regime,
                    'adx': regime_cache.adx,
                    'regime_confidence': regime_cache.confidence,
                    'regime_skipped': False
                }

            # Get hold duration info if this symbol has an active signal
            hold_duration_info = {}
            for sig_id, sig_info in self.active_signals.items():
                if sig_info['symbol'] == symbol:
                    hold_duration_info = await self._build_hold_duration_display(sig_info)
                    break

            display_data[symbol] = {
                'price': data.get('openinterest', {}).get('price', 0),
                'change': 0,
                'atr': tp_sl.atr_cache.get(symbol, 0),
                'liq_long': data.get('liquidation', {}).get('long', 0),
                'liq_short': data.get('liquidation', {}).get('short', 0),
                'liq_signal': data.get('liquidation', {}).get('dominance', 'NEUTRAL'),
                'ob_imbalance': data.get('orderbook', {}).get('imbalance', 0),
                'ob_bar': data.get('orderbook', {}).get('bar', ''),
                'ob_signal': data.get('orderbook', {}).get('signal', 'NEUTRAL'),
                'whale_buyers': data.get('whale', {}).get('buyers', 0),
                'whale_sellers': data.get('whale', {}).get('sellers', 0),
                'whale_signal': data.get('whale', {}).get('dominance', 'NEUTRAL'),
                'oi_change': data.get('openinterest', {}).get('change', 0),
                'price_trend': self._get_price_trend(data),
                'oi_signal': data.get('openinterest', {}).get('signal', 'NEUTRAL'),
                'taker_buy_ratio': data.get('openinterest', {}).get('taker_ratio', 0.5),
                'taker_signal': self._get_taker_signal(data),
                'top_trader_ratio': data.get('openinterest', {}).get('long_short_ratio', 0.5),
                'top_trader_signal': self._get_top_trader_signal(data),
                'signal': signal_info.get('signal', 'NEUTRAL'),
                'score_100': signal_info.get('score_100', 50),
                'tp_sl': signal_info.get('tp_sl', {}),
                'is_confirmed': signal_info.get('is_confirmed', False),
                'confirmation_progress': signal_info.get('confirmation_progress', 0),
                'confirmation_total': signal_info.get('confirmation_total', config.CONFIRMATION_MINUTES),
                'hold_duration': hold_duration_info,
                'current_price': data.get('openinterest', {}).get('price', 0),
                **regime_info
            }

        return display_data

    async def _build_hold_duration_display(self, sig_info: Dict) -> Dict:
        """Build hold duration display info from active signal."""
        now = datetime.now()
        deadline = sig_info['hold_deadline']
        total_seconds = sig_info['hold_hours'] * 3600
        elapsed_seconds = (now - sig_info.get('confirmed_at', now)).total_seconds()
        remaining_seconds = max(0, (deadline - now).total_seconds())

        # Format remaining time
        rem_h = int(remaining_seconds // 3600)
        rem_m = int((remaining_seconds % 3600) // 60)
        remaining_str = f"{rem_h}j {rem_m:02d}m"

        # Calculate progress
        progress_pct = min(100, (elapsed_seconds / total_seconds) * 100) if total_seconds > 0 else 0

        # Check extension eligibility
        trail_active = self.trailing_manager.is_trail_active(sig_info['symbol'])
        current_score = await self.aggregator.get_raw_score(sig_info['symbol'])
        eligible, reason = self.hold_calculator.check_extension_eligibility(
            current_score=current_score,
            trail_active=trail_active,
            atr_value=sig_info['atr_value']
        )

        hold_result = sig_info.get('hold_result', {})

        return {
            'formula_str': hold_result.get('formula_str', f"{sig_info['hold_hours']:.1f}h × 1.00 × 1.00 × 1.00"),
            'formatted_duration': self.hold_calculator.format_duration(sig_info['hold_hours']),
            'deadline_str': deadline.strftime('%H:%M WIB'),
            'remaining_str': remaining_str,
            'total_seconds': total_seconds,
            'elapsed_seconds': elapsed_seconds,
            'atr_factor': hold_result.get('atr_factor', 1.0),
            'score_factor': hold_result.get('score_factor', 1.0),
            'volume_factor': hold_result.get('volume_factor', 1.0),
            'extend_ok': eligible,
            'extend_reason': reason,
            'is_extended': sig_info.get('extended', False),
            'extension_hours': sig_info.get('extension_hours', 0)
        }

    def _get_price_trend(self, data: Dict) -> str:
        """Get price trend arrow based on OI signal."""
        signal = data.get('openinterest', {}).get('signal', 'NEUTRAL')
        if signal == 'LONGS_ENTERING':
            return '↑'
        elif signal == 'SHORTS_ENTERING':
            return '↓'
        return '→'

    def _get_taker_signal(self, data: Dict) -> str:
        """Get taker signal based on ratio."""
        ratio = data.get('openinterest', {}).get('taker_ratio', 0.5)
        if ratio > 1:
            return 'BUY_PRESSURE'
        elif ratio < 1:
            return 'SELL_PRESSURE'
        return 'NEUTRAL'

    def _get_top_trader_signal(self, data: Dict) -> str:
        """Get top trader signal based on ratio."""
        ratio = data.get('openinterest', {}).get('long_short_ratio', 0.5)
        if ratio > 0.6:
            return 'LONGS_ENTERING'
        elif ratio < 0.4:
            return 'SHORTS_ENTERING'
        return 'NEUTRAL'

    _shutting_down = False

    async def shutdown(self):
        """Graceful shutdown of all components."""
        if self._shutting_down:
            return
        self._shutting_down = True

        logger.info("Shutting down...")
        self.running = False

        try:
            # Stop all monitors — cancel tasks first so they can clean up aiohttp sessions
            for name, monitor in self.monitors.items():
                try:
                    await monitor.stop()
                except Exception as e:
                    logger.warning(f"Error stopping {name} monitor: {e}")

            # Give cancelled tasks a moment to run __aexit__ on aiohttp sessions
            # This prevents "Unclosed client session" warnings on shutdown
            await asyncio.sleep(1.0)

            # Close shared client connection
            try:
                await self.client_manager.close()
            except Exception as e:
                logger.warning(f"Error closing client: {e}")

            # Close database
            try:
                await self.database.close()
            except Exception as e:
                logger.warning(f"Error closing database: {e}")

            # Close Telegram bot session
            try:
                await self.telegram_notifier.close()
            except Exception as e:
                logger.warning(f"Error closing Telegram: {e}")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

        logger.info("Shutdown complete.")
        sys.exit(0)


async def main():
    """Application entry point."""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(config.LOG_FILE),
            logging.StreamHandler()
        ]
    )

    app = CryptoSignalApp()

    try:
        await app.initialize()
        await app.start()

        # Keep event loop running
        while app.running:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
