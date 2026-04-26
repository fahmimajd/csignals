import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional
import telegram
from telegram.error import TelegramError
import config


class TelegramNotifier:
    def __init__(self):
        self.bot = None
        self.chat_id = None
        self.last_alert: Dict[str, datetime] = {}
        self.cooldown = timedelta(minutes=config.SIGNAL_COOLDOWN_MINUTES)

    async def initialize(self):
        if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
            self.bot = telegram.Bot(token=config.TELEGRAM_BOT_TOKEN)
            self.chat_id = config.TELEGRAM_CHAT_ID
            return True
        else:
            print("Telegram credentials not configured. Notifications disabled.")
            return False

    async def close(self):
        """Close the Telegram bot session cleanly to prevent unclosed aiohttp warnings."""
        if self.bot:
            try:
                await self.bot.session.close()
            except Exception:
                pass
            self.bot = None

    async def send_signal_alert(
        self,
        symbol: str,
        signal: str,
        score_100: int,
        raw_score: int,
        details: Dict,
        tp_sl_info: Dict = None,
        hold_duration: Dict = None
    ):
        """Send signal alert to Telegram (with hold duration)"""
        if not self.bot or not self.chat_id:
            return False

        # Check cooldown — use a shorter cooldown than confirmation layer
        # Confirmation already blocks same symbol for SIGNAL_COOLDOWN_MINUTES,
        # but we add a per-Telegram safety net of 2 minutes to prevent duplicates
        telegram_cooldown = timedelta(minutes=2)
        now = datetime.now()
        if symbol in self.last_alert:
            time_since = now - self.last_alert[symbol]
            if time_since < telegram_cooldown:
                return False

        try:
            message = self._format_message_alert(
                symbol, signal, score_100, raw_score, details, tp_sl_info, hold_duration
            )
            await self.bot.send_message(chat_id=self.chat_id, text=message, parse_mode='HTML')
            self.last_alert[symbol] = now
            return True
        except TelegramError as e:
            print(f"Telegram send error: {e}")
            return False

    async def send_signal_extended(
        self,
        symbol: str,
        signal: str,
        score_100: int,
        raw_score: int,
        extension_hours: float,
        old_deadline: datetime,
        new_deadline: datetime,
        trail_price: float,
        current_price: float,
        entry_price: float
    ):
        """Send notification when signal is extended"""
        if not self.bot or not self.chat_id:
            return False

        try:
            message = self._format_message_extended(
                symbol, signal, score_100, raw_score,
                extension_hours, old_deadline, new_deadline,
                trail_price, current_price, entry_price
            )
            await self.bot.send_message(chat_id=self.chat_id, text=message, parse_mode='HTML')
            return True
        except TelegramError as e:
            print(f"Telegram send error: {e}")
            return False

    async def send_signal_expired(
        self,
        symbol: str,
        signal: str,
        score_100: int,
        raw_score: int,
        duration_hours: float,
        entry_price: float,
        expired_price: float,
        expired_pnl_pct: float,
        extended: bool,
        reason: str
    ):
        """Send notification when signal is expired"""
        if not self.bot or not self.chat_id:
            return False

        try:
            message = self._format_message_expired(
                symbol, signal, score_100, raw_score,
                duration_hours, entry_price, expired_price,
                expired_pnl_pct, extended, reason
            )
            await self.bot.send_message(chat_id=self.chat_id, text=message, parse_mode='HTML')
            return True
        except TelegramError as e:
            print(f"Telegram send error: {e}")
            return False

    def _format_message_alert(
        self,
        symbol: str,
        signal: str,
        score_100: int,
        raw_score: int,
        details: Dict,
        tp_sl_info: Dict = None,
        hold_duration: Dict = None
    ) -> str:
        """Format signal alert message with hold duration"""
        emoji_map = {
            'STRONG_LONG': '🟢',
            'WEAK_LONG': '🟡',
            'NEUTRAL': '⚪',
            'WEAK_SHORT': '🟡',
            'STRONG_SHORT': '🔴'
        }

        emoji = emoji_map.get(signal, '⚪')

        lines = []
        lines.append(f"<b>🚨 SIGNAL ALERT</b>")
        lines.append(f"Pair    : {symbol}")
        lines.append(f"Signal  : {signal} {emoji}")
        lines.append(f"Score   : {score_100}/100 ({raw_score:+d}/6)")
        lines.append(f"Locked  : {datetime.now().strftime('%H:%M:%S WIB')}")
        lines.append("")
        lines.append("<b>📊 BREAKDOWN:</b>")

        # Signal breakdown
        for sig_type, info in details.items():
            detail = info  # info IS the detail dict (no nested 'details' key)
            # score_change not provided by aggregator — derive from signal direction
            # using the detail data to determine bullish/bearish
            if sig_type == 'liquidation':
                dominance = detail.get('dominance', 'NEUTRAL')
                if dominance == 'BULLISH':
                    score_change = 1
                    lines.append(f"✅ Short liquidation dominance")
                else:
                    score_change = -1
                    lines.append(f"❌ Long liquidation dominance")
            elif sig_type == 'orderbook':
                signal_val = detail.get('signal', 'NEUTRAL')
                imbalance = detail.get('imbalance', 0)
                if signal_val == 'BUY_PRESSURE':
                    score_change = 1
                    lines.append(f"✅ Order book: buy pressure {imbalance:.2f}")
                elif signal_val == 'SELL_PRESSURE':
                    score_change = -1
                    lines.append(f"❌ Order book: sell pressure {imbalance:.2f}")
                else:
                    score_change = 0
                    lines.append(f"➖ Order book: neutral {imbalance:.2f}")
            elif sig_type == 'whale':
                dominance = detail.get('dominance', 'NEUTRAL')
                buyers = detail.get('buyers', 0)
                sellers = detail.get('sellers', 0)
                if dominance == 'BUYER_DOMINANCE':
                    score_change = 1
                    lines.append(f"✅ Whale buyers: {buyers} trades")
                elif dominance == 'SELLER_DOMINANCE':
                    score_change = -1
                    lines.append(f"❌ Whale sellers: {sellers} trades")
                else:
                    score_change = 0
                    lines.append(f"➖ Whale neutral: {buyers}B/{sellers}S")
            elif sig_type == 'open_interest':
                signal_val = detail.get('signal', 'NEUTRAL')
                if signal_val == 'LONGS_ENTERING':
                    score_change = 1
                    lines.append(f"✅ OI increasing + price up")
                elif signal_val == 'SHORTS_ENTERING':
                    score_change = -1
                    lines.append(f"❌ OI increasing + price down")
                else:
                    score_change = 0
                    lines.append(f"➖ OI neutral")
            elif sig_type == 'taker_volume':
                ratio = detail.get('ratio', 0.5)
                if ratio > 1:
                    score_change = 1
                    lines.append(f"✅ Taker buy dominant")
                elif ratio < 1:
                    score_change = -1
                    lines.append(f"❌ Taker sell dominant")
                else:
                    score_change = 0
                    lines.append(f"➖ Taker volume neutral")
            elif sig_type == 'top_trader':
                ratio = detail.get('ratio', 0.5)
                if ratio > 0.6:
                    score_change = 1
                    lines.append(f"✅ Top trader ratio long")
                elif ratio < 0.4:
                    score_change = -1
                    lines.append(f"❌ Top trader ratio short")
                else:
                    score_change = 0
                    lines.append(f"➖ Top trader ratio neutral")

        # TP/SL info if available
        if tp_sl_info:
            lines.append("")
            lines.append("<b>💰 TRADE LEVELS:</b>")
            entry_zone = tp_sl_info.get('entry_zone', 'N/A')
            stop_loss = tp_sl_info.get('stop_loss', 0) or 0
            sl_percent = tp_sl_info.get('sl_percent', 0) or 0
            take_profit = tp_sl_info.get('take_profit', 0) or 0
            tp_percent = tp_sl_info.get('tp_percent', 0) or 0
            trail_start = tp_sl_info.get('trail_start', 0) or 0
            trail_stop = tp_sl_info.get('trail_stop', 0) or 0
            trail_active = tp_sl_info.get('trail_stop_active', False)
            rr_ratio = tp_sl_info.get('rr_ratio', 'N/A')
            atr = tp_sl_info.get('atr', 0) or 0

            # Format prices with proper thousand separators and decimal precision
            # Use locale-aware formatting or manual formatting for consistency
            lines.append(f"Entry Zone  : {entry_zone}")
            lines.append(f"Stop Loss   : ${stop_loss:,.4f} ({sl_percent:.2f}%)")
            lines.append(f"TP Target   : ${take_profit:,.4f} ({tp_percent:.2f}%)")
            lines.append(f"Trail Start : ${trail_start:,.4f}")
            ts_label = "(Active)" if trail_active else "(Pending)"
            lines.append(f"Trail Stop  : ${trail_stop:,.4f} {ts_label}")
            lines.append(f"R:R Ratio   : {rr_ratio}")
            lines.append(f"ATR (14,1H) : {atr:,.4f}")
            
            # Monte Carlo info if available
            mc_confidence = tp_sl_info.get('mc_confidence')
            if mc_confidence:
                mc_prob_tp = tp_sl_info.get('mc_prob_tp', 0) or 0
                mc_prob_sl = tp_sl_info.get('mc_prob_sl', 0) or 0
                mc_prob_expire = tp_sl_info.get('mc_prob_expire', 0) or 0
                
                lines.append("")
                lines.append("<b>🎲 MONTE CARLO (1.000 simulasi):</b>")
                lines.append(f"Prob TP     : {mc_prob_tp:.1f}%")
                lines.append(f"Prob SL     : {mc_prob_sl:.1f}%")
                lines.append(f"Prob Expire : {mc_prob_expire:.1f}%")
                
                # Add star emoji for HIGH confidence
                if mc_confidence == "HIGH":
                    lines.append(f"Confidence  : {mc_confidence} ⭐")
                else:
                    lines.append(f"Confidence  : {mc_confidence}")

        # Hold duration info if available
        if hold_duration:
            lines.append("")
            lines.append("<b>⏱ HOLD DURATION:</b>")
            dur_formatted = hold_duration.get('formatted_duration', 'N/A')
            deadline = hold_duration.get('deadline_str', 'N/A')
            formula = hold_duration.get('formula_str', 'N/A')
            atr_factor = hold_duration.get('atr_factor', 0)
            score_factor = hold_duration.get('score_factor', 0)
            vol_factor = hold_duration.get('volume_factor', 0)
            lines.append(f"Durasi      : {dur_formatted}")
            lines.append(f"Deadline    : {deadline}")
            lines.append(f"Faktor      : ATR×{atr_factor:.2f} | Score×{score_factor:.2f} | Vol×{vol_factor:.2f}")

        # Market regime info
        regime_info = details.get('regime_info', {})
        if regime_info and not regime_info.get('skipped', False):
            lines.append("")
            lines.append("<b>📊 MARKET REGIME:</b>")
            regime = regime_info.get('regime', 'RANGING')
            adx = regime_info.get('adx', 0.0)
            conf_pct = int(regime_info.get('regime_confidence', 0.0) * 100)
            lines.append(f"Market Regime : {regime} (ADX {adx:.1f})")
            lines.append(f"Confidence    : {conf_pct}%")

        lines.append("")
        lines.append(f"🕐 {datetime.now().strftime('%H:%M:%S WIB')}")

        return "\n".join(lines)

    def _format_message_extended(
        self,
        symbol: str,
        signal: str,
        score_100: int,
        raw_score: int,
        extension_hours: float,
        old_deadline: datetime,
        new_deadline: datetime,
        trail_price: float,
        current_price: float,
        entry_price: float
    ) -> str:
        """Format signal extended notification"""
        emoji_map = {
            'STRONG_LONG': '🟢',
            'WEAK_LONG': '🟡',
            'STRONG_SHORT': '🔴',
            'WEAK_SHORT': '🟡'
        }
        emoji = emoji_map.get(signal, '⚪')

        ext_h = int(extension_hours)
        ext_m = round((extension_hours - ext_h) * 60)
        ext_str = f"+{ext_h}j {ext_m:02d}m"

        pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0

        lines = []
        lines.append(f"<b>⏳ SIGNAL EXTENDED</b>")
        lines.append(f"Pair      : {symbol}")
        lines.append(f"Signal    : {signal} {emoji}")
        lines.append(f"Score     : {score_100}/100 ({raw_score:+d}/6)")
        lines.append("")
        lines.append("<b>Syarat terpenuhi:</b>")
        lines.append(f"✅ Score masih STRONG ({score_100})")
        lines.append(f"✅ Trailing stop aktif di ${trail_price:,.2f}")
        lines.append("")
        lines.append(f"Perpanjangan : {ext_str}")
        lines.append(f"Deadline lama: {old_deadline.strftime('%H:%M WIB')}")
        lines.append(f"Deadline baru: {new_deadline.strftime('%H:%M WIB')}")
        lines.append("")
        lines.append(f"Posisi saat ini : ${current_price:,.2f} ({pnl_pct:+.2f}% dari entry)")
        lines.append(f"Trailing stop   : ${trail_price:,.2f}")
        lines.append("")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        return "\n".join(lines)

    def _format_message_expired(
        self,
        symbol: str,
        signal: str,
        score_100: int,
        raw_score: int,
        duration_hours: float,
        entry_price: float,
        expired_price: float,
        expired_pnl_pct: float,
        extended: bool,
        reason: str
    ) -> str:
        """Format signal expired notification"""
        emoji_map = {
            'STRONG_LONG': '🟢',
            'WEAK_LONG': '🟡',
            'STRONG_SHORT': '🔴',
            'WEAK_SHORT': '🟡'
        }
        emoji = emoji_map.get(signal, '⚪')

        dur_h = int(duration_hours)
        dur_m = round((duration_hours - dur_h) * 60)
        dur_str = f"{dur_h}j {dur_m:02d}m"

        extended_str = "Ya" if extended else "Tidak (syarat score tidak terpenuhi)"

        lines = []
        lines.append(f"<b>⌛ SIGNAL EXPIRED</b>")
        lines.append(f"Pair    : {symbol}")
        lines.append(f"Signal  : {signal} {emoji}")
        lines.append(f"Score   : {score_100}/100 ({raw_score:+d}/6)")
        lines.append("")
        lines.append(f"Durasi aktual  : {dur_str}")
        lines.append(f"Entry          : ${entry_price:,.2f}")
        lines.append(f"Harga expired  : ${expired_price:,.2f}")
        lines.append(f"Unrealized PnL : {expired_pnl_pct:+.2f}%")
        lines.append("")
        lines.append("<b>Alasan expired:</b>")
        lines.append(f"  {reason}")
        lines.append("")
        lines.append(f"Extended : {extended_str}")
        lines.append("")
        lines.append("<b>Evaluasi:</b> Sinyal ini diklasifikasikan EXPIRED")
        lines.append("bukan WIN/LOSS karena tidak resolved.")
        lines.append("Data tetap disimpan untuk analisis.")
        lines.append("")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        return "\n".join(lines)
