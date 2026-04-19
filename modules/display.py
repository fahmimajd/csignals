"""
Terminal display module for showing real-time crypto signal data.
Uses Rich library for formatted terminal output.
"""
import os
from datetime import datetime
from typing import Dict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn

import config


class TerminalDisplay:
    """Terminal display for crypto signal monitoring."""

    def __init__(self):
        self.console = Console()

    def update_display(self, symbol_data: Dict):
        """
        Update terminal display with latest data for all symbols.

        Args:
            symbol_data: Dictionary mapping symbol -> data dict
        """
        os.system('cls' if os.name == 'nt' else 'clear')

        current_time = datetime.now().strftime("%H:%M:%S WIB")
        header_panel = Panel(
            f"[bold cyan]CRYPTO SIGNAL MONITOR[/] - {current_time}",
            style="bold white on blue"
        )
        self.console.print(header_panel)

        # Create table for all symbols
        symbols_table = Table(title="[bold]Symbols Overview[/]", show_header=True)
        symbols_table.add_column("Symbol", style="cyan bold")
        symbols_table.add_column("Price", style="green")
        symbols_table.add_column("Signal", style="yellow")
        symbols_table.add_column("Score", justify="center")
        symbols_table.add_column("Status", style="dim")

        for symbol, data in symbol_data.items():
            signal = data.get('signal', 'NEUTRAL')
            score_100 = data.get('score_100', 50)

            # Color based on signal
            signal_color = {
                'STRONG_LONG': '[green]',
                'WEAK_LONG': '[yellow]',
                'NEUTRAL': '[white]',
                'WEAK_SHORT': '[yellow]',
                'STRONG_SHORT': '[red]'
            }.get(signal, '[white]')

            status = ""
            if data.get('is_confirmed'):
                status = "✓ CONFIRMED"
            elif data.get('confirmation_progress', 0) > 0:
                status = f"Confirming {data.get('confirmation_progress', 0)}/{data.get('confirmation_total', 3)}m"

            symbols_table.add_row(
                symbol,
                f"{data.get('price', 0):,.2f}",
                f"{signal_color}{signal}[/]",
                f"{score_100}/100",
                status
            )

        self.console.print(symbols_table)

        # Create detailed metrics table
        for symbol, data in symbol_data.items():
            self._print_symbol_details(symbol, data)

    def _print_symbol_details(self, symbol: str, data: Dict):
        """Print detailed metrics for a single symbol."""
        metrics_table = Table(
            title=f"[bold]{symbol} Metrics[/]",
            show_header=False,
            box=None
        )
        metrics_table.add_column("Metric", style="dim")
        metrics_table.add_column("Value", style="white")
        metrics_table.add_column("Signal", style="bold")

        # Liquidation
        liq_signal = data.get('liq_signal', 'NEUTRAL')
        metrics_table.add_row(
            "LIQUIDATIONS",
            f"L: {data.get('liq_long', 0):,.0f} | S: {data.get('liq_short', 0):,.0f}",
            self._checkmark(liq_signal)
        )

        # Order book
        ob_signal = data.get('ob_signal', 'NEUTRAL')
        metrics_table.add_row(
            "ORDER BOOK",
            f"[{data.get('ob_bar', '')}] {data.get('ob_imbalance', 0):+.2f}",
            self._checkmark(ob_signal)
        )

        # Whale
        whale_signal = data.get('whale_signal', 'NEUTRAL')
        metrics_table.add_row(
            "WHALE TRADES",
            f"Buyers: {data.get('whale_buyers', 0)} | Sellers: {data.get('whale_sellers', 0)}",
            self._checkmark(whale_signal)
        )

        # Open interest
        oi_signal = data.get('oi_signal', 'NEUTRAL')
        metrics_table.add_row(
            "OPEN INTEREST",
            f"{data.get('oi_change', 0):+.1f}% {data.get('price_trend', '→')}",
            self._checkmark(oi_signal)
        )

        # Taker volume
        taker_signal = data.get('taker_signal', 'NEUTRAL')
        taker_ratio = data.get('taker_buy_ratio', 0.5)
        metrics_table.add_row(
            "TAKER VOLUME",
            f"Buy: {taker_ratio*100:.0f}% | Sell: {(1-taker_ratio)*100:.0f}%",
            self._checkmark(taker_signal)
        )

        # Top trader
        top_signal = data.get('top_trader_signal', 'NEUTRAL')
        top_ratio = data.get('top_trader_ratio', 0.5)
        metrics_table.add_row(
            "TOP TRADER RATIO",
            f"Long: {top_ratio*100:.0f}% | Short: {(1-top_ratio)*100:.0f}%",
            self._checkmark(top_signal)
        )

        # Volatility Regime
        regime = data.get('regime', 'RANGING')
        adx = data.get('adx', 0.0)
        regime_conf = data.get('regime_confidence', 0.0)
        regime_skipped = data.get('regime_skipped', False)
        
        if regime == "TRENDING":
            regime_color = "[green]"
            regime_icon = "✅"
        elif regime == "RANGING":
            regime_color = "[yellow]"
            regime_icon = "⚠️"
        else:  # CHOPPY
            regime_color = "[red]"
            regime_icon = "🚫 (SINYAL DIBLOK)"
        
        conf_pct = int(regime_conf * 100)
        metrics_table.add_row(
            "VOLATILITY REGIME",
            f"{regime_color}{regime} (ADX: {adx:.1f}, conf: {conf_pct}%)[/]",
            f"{regime_icon}" if regime != "CHOPPY" else ""
        )

        # Monte Carlo (if available in tp_sl data)
        tp_sl = data.get('tp_sl', {})
        mc_confidence = tp_sl.get('mc_confidence')
        if mc_confidence:
            mc_prob_tp = tp_sl.get('mc_prob_tp', 0) or 0
            mc_prob_sl = tp_sl.get('mc_prob_sl', 0) or 0
            mc_prob_expire = tp_sl.get('mc_prob_expire', 0) or 0
            
            # Create progress bar visualization
            total = mc_prob_tp + mc_prob_sl + mc_prob_expire
            if total > 0:
                tp_bar_len = int(10 * mc_prob_tp / 100)
                sl_bar_len = int(10 * mc_prob_sl / 100)
                exp_bar_len = 10 - tp_bar_len - sl_bar_len
                bar = "█" * tp_bar_len + "▓" * sl_bar_len + "░" * exp_bar_len
            else:
                bar = "░░░░░░░░░░"
            
            if mc_confidence == "HIGH":
                mc_color = "[green]"
                mc_icon = "✅"
            elif mc_confidence == "MEDIUM":
                mc_color = "[yellow]"
                mc_icon = "⚠️"
            elif mc_confidence == "LOW":
                mc_color = "[orange]"
                mc_icon = "⚪"
            else:  # SKIP
                mc_color = "[red]"
                mc_icon = "🚫"
            
            metrics_table.add_row(
                "MONTE CARLO",
                f"{mc_color}TP:{mc_prob_tp:.1f}% SL:{mc_prob_sl:.1f}% Exp:{mc_prob_expire:.1f}[/]",
                f"{bar} {mc_icon}"
            )
            metrics_table.add_row(
                "Confidence",
                f"{mc_color}{mc_confidence}[/]",
                "⭐" if mc_confidence == "HIGH" else ""
            )

        self.console.print(metrics_table)

        # Print TP/SL if available
        tp_sl = data.get('tp_sl', {})
        if tp_sl:
            tp_sl_table = Table(show_header=False, box=None)
            tp_sl_table.add_column("Level", style="dim")
            tp_sl_table.add_column("Value", style="white")
            tp_sl_table.add_column("Details", style="dim")

            tp_sl_table.add_row("Entry Zone", tp_sl.get('entry_zone', 'N/A'), f"ATR: {tp_sl.get('atr', 0):.2f}")
            tp_sl_table.add_row("Stop Loss", f"{tp_sl.get('stop_loss', 0):,.2f}", f"({tp_sl.get('sl_percent', 0):.2f}%)")
            tp_sl_table.add_row("Take Profit", f"{tp_sl.get('take_profit', 0):,.2f}", f"({tp_sl.get('tp_percent', 0):.2f}%)")
            tp_sl_table.add_row("R:R Ratio", tp_sl.get('rr_ratio', 'N/A'), f"Source: {tp_sl.get('tp_source', 'N/A')}")

            self.console.print(Panel(tp_sl_table, title="[bold green]Trade Levels[/]"))

        # Print Hold Duration if available
        hold_info = data.get('hold_duration', {})
        if hold_info:
            self._print_hold_duration(symbol, hold_info, data.get('signal'), data.get('current_price'))

        self.console.print()  # Spacing

    def _print_hold_duration(self, symbol: str, hold_info: Dict, signal: str, current_price: float):
        """Print hold duration section for a symbol."""
        hold_table = Table(show_header=False, box=None, title="[bold]HOLD DURATION[/]")

        # Formula calculation display
        formula = hold_info.get('formula_str', 'N/A')
        dur_formatted = hold_info.get('formatted_duration', 'N/A')
        deadline = hold_info.get('deadline_str', 'N/A')
        remaining = hold_info.get('remaining_str', 'N/A')

        # Calculate progress
        total_seconds = hold_info.get('total_seconds', 0)
        elapsed_seconds = hold_info.get('elapsed_seconds', 0)
        if total_seconds > 0:
            progress_pct = min(100, (elapsed_seconds / total_seconds) * 100)
            bar_len = 10
            filled = int(bar_len * progress_pct / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
        else:
            bar = "░░░░░░░░░░"
            progress_pct = 0

        # Faktor breakdown
        atr_factor = hold_info.get('atr_factor', 0)
        score_factor = hold_info.get('score_factor', 0)
        vol_factor = hold_info.get('volume_factor', 0)

        # Extend status
        extend_ok = hold_info.get('extend_ok', False)
        extend_reason = hold_info.get('extend_reason', 'Menunggu...')
        is_extended = hold_info.get('is_extended', False)

        # Build hold duration display
        hold_table.add_column("Label", style="dim")
        hold_table.add_column("Value", style="white")

        hold_table.add_row("Kalkulasi", f"{formula} = {dur_formatted}")
        hold_table.add_row("Faktor", f"ATR {atr_factor:.2f} | Score {score_factor:.2f} | Vol {vol_factor:.2f}")
        hold_table.add_row("Deadline", f"{deadline} (sisa: {remaining})")
        hold_table.add_row("Progress", f"[{bar}]  {progress_pct:.0f}%")

        if is_extended:
            ext_hours = hold_info.get('extension_hours', 0)
            hold_table.add_row("Status", f"[yellow]EXTENDED (+{ext_hours:.1f}h)[/]")
        elif extend_ok:
            hold_table.add_row("Extend OK?", f"[green]✓ Syarat terpenuhi[/]")
        else:
            hold_table.add_row("Extend OK?", f"[yellow]{extend_reason}[/]")

        self.console.print(Panel(hold_table, title=f"[bold cyan]⏱ HOLD DURATION[/]"))

    def _checkmark(self, signal: str) -> str:
        """Return checkmark based on signal."""
        if signal in ('BUY_PRESSURE', 'BULLISH', 'BUYER_DOMINANCE', 'LONGS_ENTERING'):
            return "✅"
        elif signal in ('SELL_PRESSURE', 'BEARISH', 'SELLER_DOMINANCE', 'SHORTS_ENTERING'):
            return "❌"
        return "➖"
