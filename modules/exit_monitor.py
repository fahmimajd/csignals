"""
Exit Monitor Module

Monitor sinyal ACTIVE dan update statusnya ke DB ketika:
- Harga hit Take Profit  → CLOSED_WIN
- Harga hit Stop Loss    → CLOSED_LOSS
- Melewati hold_deadline → EXPIRED (atau diperpanjang)

PERBAIKAN vs versi lama:
1. Query DB langsung, tidak bergantung pada self.active_signals yang hilang saat restart
2. Pakai kolom yang benar: exit_price, pnl_percent (bukan close_price, pnl_pct)
3. Release cooldown setelah signal di-close
4. Handle duplikat: hanya proses 1 sinyal terbaru per symbol
5. Exception tidak mematikan loop
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable, Awaitable

import config

logger = logging.getLogger(__name__)


class ExitMonitor:
    """
    Monitor sinyal ACTIVE dari database dan close ketika:
    - Harga hit TP → CLOSED_WIN
    - Harga hit SL → CLOSED_LOSS
    - Deadline lewat → EXPIRED atau EXTENDED
    """

    def __init__(
        self,
        database,
        price_fetcher: Callable[[str], Awaitable[float]],
        confirmation,
        aggregator,
        hold_calculator,
        trailing_manager,
        telegram_notifier
    ):
        """
        Args:
            database: Database instance (punya pool asyncpg)
            price_fetcher: async function (symbol) -> float
            confirmation: SignalConfirmation instance
            aggregator: SignalAggregator instance
            hold_calculator: HoldDurationCalculator instance
            trailing_manager: TrailingStopManager instance
            telegram_notifier: TelegramNotifier instance
        """
        self.db = database
        self.price_fetcher = price_fetcher
        self.confirmation = confirmation
        self.aggregator = aggregator
        self.hold_calculator = hold_calculator
        self.trailing_manager = trailing_manager
        self.telegram = telegram_notifier
        self._running = False
        self._check_interval = 60  # cek setiap 60 detik
        self._max_retries = 3  # Maximum retries for price fetch

    async def _fetch_price_with_retry(self, symbol: str, max_retries: int = None) -> Optional[float]:
        """
        Fetch price with exponential backoff retry logic.
        
        Args:
            symbol: Trading pair symbol
            max_retries: Override default max retries
            
        Returns:
            Current price or None if all retries failed
        """
        if max_retries is None:
            max_retries = self._max_retries
            
        for attempt in range(max_retries):
            try:
                price = await self.price_fetcher(symbol)
                if price and price > 0:
                    return price
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(
                        f"[EXIT] Price fetch failed after {max_retries} attempts for {symbol}: {e}"
                    )
                    return None
                # Exponential backoff: 1s, 2s, 4s...
                wait_time = 2 ** attempt
                logger.warning(
                    f"[EXIT] Price fetch attempt {attempt + 1}/{max_retries} failed for {symbol}, "
                    f"retrying in {wait_time}s: {e}"
                )
                await asyncio.sleep(wait_time)
        
        return None

    async def run(self):
        """
        Entry point — di-gather di main.py via asyncio.create_task().
        Loop tidak pernah berhenti kecuali _running = False atau CancelledError.
        """
        self._running = True
        logger.info("[EXIT] Monitor dimulai")

        while self._running:
            try:
                await self._check_all_signals()
            except asyncio.CancelledError:
                logger.info("[EXIT] Monitor di-cancel — shutdown")
                break
            except Exception as e:
                logger.error(f"[EXIT] Error dalam check cycle: {e}", exc_info=True)

            await asyncio.sleep(self._check_interval)

        logger.info("[EXIT] Monitor berhenti")

    def stop(self):
        """Stop the monitor loop."""
        self._running = False

    async def _check_all_signals(self):
        """
        Satu iterasi pengecekan semua sinyal ACTIVE dari DB.
        Ambil hanya 1 sinyal terbaru per symbol untuk menghindari duplikat.
        """
        # Ambil semua ACTIVE signals dari DB
        all_active = await self.db.get_active_signals()
        if not all_active:
            return

        # Dedup: ambil hanya 1 sinyal terbaru per symbol
        latest_per_symbol = {}
        for s in all_active:
            sym = s['symbol']
            if sym not in latest_per_symbol or s['id'] > latest_per_symbol[sym]['id']:
                latest_per_symbol[sym] = s

        # Untuk sinyal duplikat yang bukan terbaru → EXPIRE langsung
        latest_ids = {s['id'] for s in latest_per_symbol.values()}
        for s in all_active:
            if s['id'] not in latest_ids:
                try:
                    # Hitung PnL berdasarkan harga terkini
                    try:
                        current_price = await self.price_fetcher(s['symbol'])
                    except Exception:
                        current_price = float(s['entry_price'])

                    entry = float(s['entry_price'])
                    sig_type = s.get('signal_type', 'STRONG_LONG')
                    pnl = ((current_price - entry) / entry * 100
                           if 'LONG' in sig_type
                           else (entry - current_price) / entry * 100)

                    await self.db.update_signal_exit(
                        s['id'], current_price, pnl, 'EXPIRED'
                    )
                except Exception as e:
                    logger.warning(f"[EXIT] Gagal expire duplikat id={s['id']}: {e}")

        signals_to_check = list(latest_per_symbol.values())
        logger.info(
            f"[EXIT] Mengecek {len(signals_to_check)} sinyal ACTIVE "
            f"(total {len(all_active)}, dedup {len(all_active) - len(signals_to_check)})"
        )

        # Cek setiap sinyal secara concurrent
        tasks = [self._check_signal(s) for s in signals_to_check]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for signal, result in zip(signals_to_check, results):
            if isinstance(result, Exception):
                logger.error(
                    f"[EXIT] Error saat cek {signal['symbol']} "
                    f"id={signal['id']}: {result}"
                )

    async def _check_signal(self, signal: dict):
        """Cek satu sinyal: harga vs TP/SL vs deadline."""
        symbol = signal['symbol']
        signal_id = signal['id']
        signal_type = signal.get('signal_type', '')
        entry = float(signal['entry_price'])
        stop_loss = float(signal.get('stop_loss', 0))
        take_profit = float(signal.get('take_profit', 0))
        deadline = signal.get('hold_deadline')
        is_long = 'LONG' in signal_type

        # ── Ambil harga terkini dengan retry logic ──
        current_price = await self._fetch_price_with_retry(symbol)
        if current_price is None or current_price <= 0:
            logger.debug(f"[EXIT] Price not yet available for {symbol}, skipping this cycle")
            return

        # ── TRACK: Update running highest/lowest price for retroactive TP/SL check ──
        await self.db.update_signal_price_range(signal_id, current_price, current_price)

        # ── Cek Take Profit dengan Trailing Stop ──
        # First check if trailing stop is active and has updated the stop level
        trailing_stop = None
        if self.trailing_manager:
            trailing_stop = self.trailing_manager.update_trailing_stop(
                symbol=symbol,
                side='LONG' if is_long else 'SHORT',
                entry_price=entry,
                current_price=current_price,
                atr=float(signal.get('atr', 0) or 0)
            )
            
            # If trailing stop returned a new level, use it instead of original SL
            if trailing_stop is not None:
                stop_loss = trailing_stop
                logger.debug(f"[EXIT] {symbol} Trailing stop updated to {trailing_stop:.4f}")
        
        # ── Cek Take Profit ──
        if is_long and current_price >= take_profit:
            pnl = (current_price - entry) / entry * 100
            await self._close_signal(signal_id, 'CLOSED_WIN', take_profit, pnl, symbol)
            logger.info(f"[EXIT] ✅ {symbol} HIT_TP id={signal_id}: +{pnl:.2f}%")
            return

        if not is_long and current_price <= take_profit:
            pnl = (entry - current_price) / entry * 100
            await self._close_signal(signal_id, 'CLOSED_WIN', take_profit, pnl, symbol)
            logger.info(f"[EXIT] ✅ {symbol} HIT_TP id={signal_id}: +{pnl:.2f}%")
            return

        # ── Cek Stop Loss (dengan trailing stop jika aktif) ──
        if is_long and stop_loss > 0 and current_price <= stop_loss:
            pnl = (current_price - entry) / entry * 100
            exit_type = 'CLOSED_LOSS_TRAILING' if trailing_stop else 'CLOSED_LOSS'
            await self._close_signal(signal_id, exit_type, stop_loss, pnl, symbol)
            logger.info(f"[EXIT] ❌ {symbol} HIT_SL id={signal_id}: {pnl:.2f}% {'(trailing)' if trailing_stop else ''}")
            return

        if not is_long and stop_loss > 0 and current_price >= stop_loss:
            pnl = (entry - current_price) / entry * 100
            exit_type = 'CLOSED_LOSS_TRAILING' if trailing_stop else 'CLOSED_LOSS'
            await self._close_signal(signal_id, exit_type, stop_loss, pnl, symbol)
            logger.info(f"[EXIT] ❌ {symbol} HIT_SL id={signal_id}: {pnl:.2f}% {'(trailing)' if trailing_stop else ''}")
            return

        # ── Cek Deadline ──
        if deadline is None:
            # Set default deadline jika belum ada
            await self._set_default_deadline(signal_id)
            return

        # Handle timezone-aware comparison
        # FIX: Deadline dari DB tanpa timezone = WIB (UTC+7), bukan UTC!
        WIB = timezone(timedelta(hours=7))
        now_utc = datetime.now(timezone.utc)
        if deadline.tzinfo is None:
            # Deadline disimpan sebagai naive datetime dalam WIB
            deadline_utc = deadline.replace(tzinfo=WIB).astimezone(timezone.utc)
        else:
            deadline_utc = deadline.astimezone(timezone.utc)

        # ── Smart Exit: close di profit jika mendekati deadline ──
        time_remaining = (deadline_utc - now_utc).total_seconds()
        if 0 < time_remaining < 1800:  # < 30 menit sebelum deadline
            pnl = ((current_price - entry) / entry * 100
                   if is_long
                   else (entry - current_price) / entry * 100)
            if pnl > 0.5:  # Minimal 0.5% profit
                await self._close_signal(signal_id, 'CLOSED_WIN', current_price, pnl, symbol)
                logger.info(
                    f"[EXIT] ⏰ {symbol} Smart exit near deadline: "
                    f"+{pnl:.2f}% (sisa {time_remaining/60:.0f}m)"
                )
                return

        if now_utc >= deadline_utc:
            await self._handle_deadline(signal, current_price)

    async def _close_signal(
        self, signal_id: int, status: str,
        close_price: float, pnl_pct: float, symbol: str
    ):
        """
        Update status sinyal ke DB dan release cooldown.
        Pakai kolom yang benar: exit_price, pnl_percent, status.
        """
        try:
            await self.db.update_signal_exit(signal_id, close_price, pnl_pct, status)
            logger.info(f"[EXIT] DB updated: id={signal_id} → {status}")

            # Release cooldown agar symbol bisa menerima sinyal baru
            self.confirmation.release_cooldown(symbol)

            # Send telegram notification
            await self._send_close_notification(signal_id, status, close_price, pnl_pct, symbol)

        except Exception as e:
            logger.error(f"[EXIT] Error closing signal {signal_id}: {e}")

    async def _send_close_notification(
        self, signal_id: int, status: str,
        close_price: float, pnl_pct: float, symbol: str
    ):
        """Send telegram notification for closed signals."""
        try:
            if not self.telegram or not self.telegram.bot:
                return

            emoji = "✅" if "WIN" in status else "❌" if "LOSS" in status else "⌛"
            status_text = {
                'CLOSED_WIN': 'HIT TAKE PROFIT',
                'CLOSED_LOSS': 'HIT STOP LOSS',
                'EXPIRED': 'EXPIRED'
            }.get(status, status)

            message = (
                f"{emoji} <b>Signal Closed</b>\n\n"
                f"Symbol: <b>{symbol}</b>\n"
                f"Status: {status_text}\n"
                f"Exit Price: {close_price:,.4f}\n"
                f"PnL: <b>{pnl_pct:+.2f}%</b>\n"
                f"Signal ID: {signal_id}"
            )

            await self.telegram.bot.send_message(
                chat_id=self.telegram.chat_id,
                text=message,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.warning(f"[EXIT] Telegram notification failed: {e}")

    async def _handle_deadline(self, signal: dict, current_price: float):
        """
        Tangani sinyal yang melewati deadline — extend atau expire.

        FIX: Retroactive TP/SL check — jika highest/lowest price pernah menyentuh
        TP atau SL selama periode hold, закрываем di level tersebut sebagai WIN/LOSS,
        bukan sebagai EXPIRED dengan current_price.
        """
        signal_id = signal['id']
        symbol = signal['symbol']
        extended = signal.get('extended', False)
        entry = float(signal['entry_price'])
        signal_type = signal.get('signal_type', '')
        stop_loss = float(signal.get('stop_loss', 0))
        take_profit = float(signal.get('take_profit', 0))
        is_long = 'LONG' in signal_type

        # ── Retroactive TP/SL check: pernahkah harga menyentuh TP atau SL? ──
        # highest_price/lowest_price di-track setiap 60 detik oleh _check_signal
        highest = float(signal.get('highest_price', 0) or 0)
        lowest = float(signal.get('lowest_price', 0) or 0)

        tp_hit = False
        sl_hit = False

        if is_long:
            # LONG: TP = take_profit (atas), SL = stop_loss (bawah)
            if highest > 0 and highest >= take_profit:
                tp_hit = True
            if sl_hit > 0 and lowest > 0 and lowest <= stop_loss:
                sl_hit = True
        else:
            # SHORT: TP = take_profit (bawah), SL = stop_loss (atas)
            if lowest > 0 and lowest <= take_profit:
                tp_hit = True
            if stop_loss > 0 and highest > 0 and highest >= stop_loss:
                sl_hit = True

        if tp_hit:
            # TP pernah disentuh — close WIN di level TP
            pnl = (take_profit - entry) / entry * 100 if is_long else (entry - take_profit) / entry * 100
            await self._close_signal(signal_id, 'CLOSED_WIN', take_profit, pnl, symbol)
            logger.info(
                f"[EXIT] ✅ {symbol} RETROACTIVE HIT_TP id={signal_id}: "
                f"high={highest:.4f} tp={take_profit:.4f} → +{pnl:.2f}%"
            )
            return

        if sl_hit:
            # SL pernah disentuh — close LOSS di level SL
            pnl = (stop_loss - entry) / entry * 100 if is_long else (entry - stop_loss) / entry * 100
            await self._close_signal(signal_id, 'CLOSED_LOSS', stop_loss, pnl, symbol)
            logger.info(
                f"[EXIT] ❌ {symbol} RETROACTIVE HIT_SL id={signal_id}: "
                f"low={lowest:.4f} sl={stop_loss:.4f} → {pnl:.2f}%"
            )
            return

        # ── Tidak ada TP/SL yang disentuh — cek apakah bisa extend ──
        can_extend = not extended and await self._check_extension_eligible(signal, current_price)

        if can_extend:
            hold_hours = float(signal.get('hold_hours', 6))
            extension_hours = self.hold_calculator.calculate_extension(hold_hours)
            new_deadline = datetime.now(timezone.utc) + timedelta(hours=extension_hours)
            # Convert to naive for DB compatibility
            new_deadline_naive = new_deadline.replace(tzinfo=None)

            await self.db.extend_signal(signal_id, new_deadline_naive, extension_hours)
            logger.info(
                f"[EXIT] ⏳ {symbol} EXTENDED +{extension_hours:.1f}h "
                f"(deadline baru: {new_deadline_naive})"
            )

            # Send extended notification
            try:
                old_deadline = signal.get('hold_deadline', datetime.now())
                score_100 = self.aggregator.raw_to_100(signal.get('score', 0))
                raw_score = signal.get('score', 0)

                await self.telegram.send_signal_extended(
                    symbol=symbol,
                    signal=signal_type,
                    score_100=score_100,
                    raw_score=raw_score,
                    extension_hours=extension_hours,
                    old_deadline=old_deadline,
                    new_deadline=new_deadline_naive,
                    trail_price=float(signal.get('trail_stop', 0)),
                    current_price=current_price,
                    entry_price=entry
                )
            except Exception as e:
                logger.warning(f"[EXIT] Telegram extended notification failed: {e}")
        else:
            # True EXPIRED — TP/SL tidak pernah disentuh dan tidak bisa extend
            pnl = ((current_price - entry) / entry * 100
                   if is_long
                   else (entry - current_price) / entry * 100)

            await self._close_signal(signal_id, 'EXPIRED', current_price, pnl, symbol)
            logger.info(
                f"[EXIT] ⌛ {symbol} TRUE EXPIRED id={signal_id} "
                f"(high={highest:.4f} low={lowest:.4f}) PnL: {pnl:.2f}%"
            )

    async def _check_extension_eligible(self, signal: dict, current_price: float) -> bool:
        """Cek syarat perpanjangan: score masih STRONG + trailing aktif."""
        symbol = signal['symbol']

        # Syarat 1: Trailing stop sudah aktif
        trail_active = self.trailing_manager.is_trail_active(symbol)
        if not trail_active:
            logger.debug(f"[EXIT] {symbol}: trail belum aktif, tidak extend")
            return False

        # Syarat 2: Score masih STRONG (>= 4)
        current_score = self.aggregator.get_raw_score(symbol)
        abs_score = abs(current_score)
        if abs_score < 4:
            logger.debug(f"[EXIT] {symbol}: score lemah ({current_score}), tidak extend")
            return False

        return True

    async def _set_default_deadline(self, signal_id: int):
        """Set deadline retroaktif +6 jam untuk sinyal yang NULL."""
        try:
            async with self.db.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE signals
                    SET hold_deadline = COALESCE(confirmed_at, timestamp) + INTERVAL '6 hours'
                    WHERE id = $1 AND hold_deadline IS NULL
                    """,
                    signal_id
                )
            logger.info(f"[EXIT] Set default deadline for signal {signal_id}")
        except Exception as e:
            logger.error(f"[EXIT] Error setting default deadline: {e}")
