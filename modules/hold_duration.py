"""
Hold Duration Calculator Module

Menghitung berapa lama sinyal dianggap valid secara otomatis
berdasarkan kondisi market saat sinyal dikunci.

Formula:
  hold_hours = BASE_HOURS × atr_factor × score_factor × volume_factor
  hold_hours = clamp(hold_hours, MIN_HOLD_HOURS, MAX_HOLD_HOURS)
"""
from typing import Dict, Tuple
import config


class HoldDurationCalculator:
    """Calculator untuk dynamic hold duration berdasarkan kondisi market."""

    def __init__(self):
        self.base_hours = getattr(config, 'BASE_HOURS', 6)
        self.min_hold_hours = getattr(config, 'MIN_HOLD_HOURS', 2)
        self.max_hold_hours = getattr(config, 'MAX_HOLD_HOURS', 12)
        self.extension_ratio = getattr(config, 'EXTENSION_RATIO', 0.5)
        self.max_extensions = getattr(config, 'MAX_EXTENSIONS', 1)

    def calculate(
        self,
        atr_value: float,
        entry_price: float,
        raw_score: int,
        taker_buy_ratio: float = 0.5,
        oi_change_pct: float = 0.0,
        whale_trade_count: int = 0,
        symbol: str = None
    ) -> Dict:
        """
        Hitung hold duration berdasarkan 3 faktor.

        Args:
            atr_value: Nilai ATR saat ini
            entry_price: Harga entry sinyal
            raw_score: Score mentah (-6 sampai +6)
            taker_buy_ratio: Rasio taker buy (0.0-1.0)
            oi_change_pct: Persentase perubahan Open Interest
            whale_trade_count: Jumlah whale trade 10 menit terakhir
            symbol: Symbol (opsional, untuk logging)

        Returns:
            Dict dengan hold_hours, deadline, dan faktor-faktor breakdown
        """
        # ===== FAKTOR 1: ATR (Volatilitas) =====
        atr_factor = self._calc_atr_factor(atr_value, entry_price)

        # ===== FAKTOR 2: Score (Kekuatan Sinyal) =====
        score_factor = self._calc_score_factor(raw_score)

        # ===== FAKTOR 3: Volume & OI (Aktivitas Market) =====
        volume_score, volume_factor = self._calc_volume_factor(
            taker_buy_ratio, oi_change_pct, whale_trade_count
        )

        # ===== HITUNG HOLD HOURS =====
        hold_hours = (
            self.base_hours
            * atr_factor
            * score_factor
            * volume_factor
        )

        # Clamp ke min/max
        hold_hours = max(self.min_hold_hours, min(self.max_hold_hours, hold_hours))

        return {
            'hold_hours': round(hold_hours, 2),
            'hold_minutes': round(hold_hours * 60, 0),
            'atr_factor': round(atr_factor, 2),
            'score_factor': round(score_factor, 2),
            'volume_factor': round(volume_factor, 2),
            'volume_score': volume_score,
            'atr_pct': round((atr_value / entry_price) * 100, 3) if entry_price > 0 else 0,
            'raw_score': raw_score,
            'formula_str': (
                f"{self.base_hours}h × {atr_factor:.2f} × {score_factor:.2f} × {volume_factor:.2f}"
            )
        }

    def _calc_atr_factor(self, atr_value: float, entry_price: float) -> float:
        """Hitung atr_factor berdasarkan volatilitas."""
        if entry_price <= 0 or atr_value <= 0:
            return 1.0  # Default normal

        atr_pct = (atr_value / entry_price) * 100

        if atr_pct > 2.0:
            return 0.6   # Sangat volatile
        elif atr_pct > 1.5:
            return 0.8
        elif atr_pct > 1.0:
            return 1.0   # Normal
        elif atr_pct > 0.5:
            return 1.2
        else:
            return 1.4   # Sangat sepi

    def _calc_score_factor(self, raw_score: int) -> float:
        """Hitung score_factor berdasarkan kekuatan sinyal."""
        abs_score = abs(raw_score)

        if abs_score >= 6:
            return 1.3   # Semua komponen valid
        elif abs_score == 5:
            return 1.15
        elif abs_score >= 4:
            return 1.0   # Threshold minimum
        else:
            return 0.9   # Lemah, tapi tetap diizinkan

    def _calc_volume_factor(
        self,
        taker_buy_ratio: float,
        oi_change_pct: float,
        whale_trade_count: int
    ) -> Tuple[int, float]:
        """
        Hitung volume_score dan volume_factor.
        Returns: (volume_score, volume_factor)
        """
        # a. Taker buy/sell dominance
        dominant_ratio = max(taker_buy_ratio, 1 - taker_buy_ratio) * 100
        if dominant_ratio > 70:
            a_score = 1   # Sangat aktif
        elif dominant_ratio >= 55:
            a_score = 0   # Normal
        else:
            a_score = -1  # Lesu

        # b. OI change magnitude
        abs_oi = abs(oi_change_pct)
        if abs_oi > 3:
            b_score = 1   # OI bergerak besar
        elif abs_oi >= 1:
            b_score = 0   # Normal
        else:
            b_score = -1  # Lesu

        # c. Whale trade count
        if whale_trade_count > 5:
            c_score = 1   # Sangat aktif
        elif whale_trade_count >= 2:
            c_score = 0   # Normal
        else:
            c_score = -1  # Lesu

        # Total volume_score: -3 sampai +3
        volume_score = a_score + b_score + c_score

        # Map ke volume_factor
        if volume_score >= 2:
            volume_factor = 0.75
        elif volume_score == 1:
            volume_factor = 0.90
        elif volume_score == 0:
            volume_factor = 1.00
        elif volume_score == -1:
            volume_factor = 1.15
        else:  # volume_score <= -2
            volume_factor = 1.30

        return volume_score, volume_factor

    def check_extension_eligibility(
        self,
        current_score: int,
        trail_active: bool,
        trail_distance: float = 0.0,
        atr_value: float = 0.0
    ) -> Tuple[bool, str]:
        """
        Cek apakah sinyal eligible untuk diperpanjang.

        Returns:
            (eligible: bool, reason: str)
        """
        abs_score = abs(current_score)

        # Syarat 1: Score masih STRONG (>= 4)
        if abs_score < 4:
            return False, "Score lemah — tidak bisa diperpanjang"

        # Syarat 2: Trailing stop sudah aktif
        if not trail_active:
            # Hitung berapa lagi sampai trail aktif
            if atr_value > 0 and trail_distance > 0:
                trail_needed = trail_distance
                return False, f"Trail belum aktif ({trail_needed:.2f} lagi)"
            return False, "Trail belum aktif"

        return True, "Syarat terpenuhi — siap diperpanjang"

    def calculate_extension(self, current_hold_hours: float) -> float:
        """Hitung durasi perpanjangan."""
        return round(current_hold_hours * self.extension_ratio, 2)

    def format_duration(self, hours: float) -> str:
        """Format durasi ke string 'Xj Ym'."""
        h = int(hours)
        m = round((hours - h) * 60)
        if m >= 60:
            h += 1
            m = 0
        return f"{h}j {m:02d}m"
