"""
aggregator.py — REFACTORED

Changes in this refactor:
1. Removed dead fields from AggregatorState: oi_signal, oi_change.
   These were set but never read by compute_score().

2. Added price_change_pct tracking inside the aggregator itself.
   Previously it was a field on AggregatorState that was never populated.
   Now update_oi_signal() computes it inline from the previous price,
   eliminating the need for OI module to know about it.

3. MAX_COMPONENTS tightened to 6 (matches the 6 scoring components).
   The whale tier expansion is already bounded by the component count
   so the max stays ±6 — removing the confusing "was 6, now 8" comment.

4. All field assignments in update_*_signal() methods are explicit,
   no dict-spread that could silently accept unknown keys.

5. update_state() now validates keys against AggregatorState fields
   before assignment — catches typos in caller code.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional, List

import config
from modules.volatility_regime import VolatilityRegimeDetector

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ComponentResult:
    name: str
    value: float
    passed: bool
    score_delta: int  # +1/-1/+2/-2


@dataclass
class AggregatorState:
    """
    Per-symbol snapshot state — all fields are refreshed every compute cycle.

    Only fields that are actually read by compute_score() are kept.
    Dead fields (oi_signal, oi_change) from prior patches are removed.
    """
    symbol: str
    # Component 1: Liquidation
    liq_long_usd: float = 0.0
    liq_short_usd: float = 0.0
    # Component 2: Order book (graduated: ±1 at OB_WEAK_THRESH, ±2 at OB_STRONG_THRESH)
    ob_imbalance: float = 0.0
    # Component 3: Whale (tiered: ±1 for 1-2 whales, ±2 for 3+)
    whale_buyers: int = 0
    whale_sellers: int = 0
    # Component 4: Open Interest (direction from OI change %)
    oi_change_pct: float = 0.0    # set by update_oi_signal()
    price_change_pct: float = 0.0  # computed inline by update_oi_signal()
    last_price: float = 0.0
    # Component 5: Taker volume ratio
    taker_buy_ratio: float = 0.5
    # Component 6: Top trader long/short ratio
    top_trader_ratio: float = 0.5
    last_updated: float = field(default_factory=time.time)


# ─────────────────────────────────────────────────────────────────────────────
# Thresholds (kept as class constants so they're co-located with scoring logic)
# ─────────────────────────────────────────────────────────────────────────────

class SignalAggregator:
    """
    Computes a fresh signal score from 6 independent components every cycle.

    Key design: stateless scoring — compute_score() always returns a result
    based solely on the current snapshot state, never accumulating scores
    across cycles.
    """

    MAX_COMPONENTS = 6
    STRONG_THRESHOLD = 3          # |score| >= 3 → STRONG signal
    OB_STRONG_THRESHOLD = 0.30    # |ob| >= 0.30 → ±2 pts
    OB_WEAK_THRESHOLD   = 0.15    # |ob| >= 0.15 → ±1 pt
    TAKER_BULL_THRESH   = 0.52     # buy% > 52% → +1
    TAKER_BEAR_THRESH   = 0.48     # buy% < 48% → -1
    TRADER_LONG_THRESH  = 0.52    # ratio > 0.52 → +1
    TRADER_SHORT_THRESH  = 0.48    # ratio < 0.48 → -1
    OI_SIGNAL_THRESHOLD = 1.0     # |oi_change_pct| > 1% → ±1

    def __init__(self):
        self._states: Dict[str, AggregatorState] = {}
        # Inline price-change tracking (prev price per symbol)
        self._prev_price: Dict[str, float] = {}
        # Display details
        self.signal_details: Dict[str, Dict] = {}
        # Volatility regime detector
        self.regime_detector = VolatilityRegimeDetector()

    # ─────────────────────────────────────────────────────────────────────
    # LEGACY INTERFACE — each update method stores one component's snapshot
    # ─────────────────────────────────────────────────────────────────────

    def update_liquidation_signal(
        self, symbol: str, dominance: str, long_total: float, short_total: float
    ):
        state = self._get_state(symbol)
        state.liq_long_usd = long_total
        state.liq_short_usd = short_total
        state.last_updated = time.time()
        self._store_detail(symbol, 'liquidation', {
            'dominance': dominance,
            'long_total': long_total,
            'short_total': short_total
        })

    def update_orderbook_signal(self, symbol: str, imbalance: float, signal: str):
        state = self._get_state(symbol)
        state.ob_imbalance = imbalance
        state.last_updated = time.time()
        self._store_detail(symbol, 'orderbook', {
            'imbalance': imbalance,
            'signal': signal
        })

    def update_whale_signal(
        self, symbol: str, dominance: str, buyers: int, sellers: int
    ):
        state = self._get_state(symbol)
        state.whale_buyers = buyers
        state.whale_sellers = sellers
        state.last_updated = time.time()
        self._store_detail(symbol, 'whale', {
            'dominance': dominance,
            'buyers': buyers,
            'sellers': sellers
        })

    def update_oi_signal(
        self, symbol: str, oi_signal: str, oi_change_pct: float, current_price: float
    ):
        """
        Update OI + price change state for a symbol.

        oi_signal is kept for display compatibility but is NOT used in scoring.
        price_change_pct is computed inline here (previous price tracked in
        _prev_price dict) so callers don't need to compute it.
        """
        state = self._get_state(symbol)

        # Compute price change % inline
        prev = self._prev_price.get(symbol, 0.0)
        if prev > 0 and current_price > 0:
            price_change_pct = ((current_price - prev) / prev) * 100
        else:
            price_change_pct = 0.0

        state.oi_change_pct = oi_change_pct
        state.price_change_pct = price_change_pct
        state.last_price = current_price
        state.last_updated = time.time()

        self._prev_price[symbol] = current_price

        self._store_detail(symbol, 'open_interest', {
            'signal': oi_signal,
            'oi_change': oi_change_pct,
            'price_change_pct': price_change_pct,
            'price': current_price
        })

    def update_taker_volume_signal(self, symbol: str, ratio: float):
        state = self._get_state(symbol)
        state.taker_buy_ratio = ratio
        state.last_updated = time.time()
        self._store_detail(symbol, 'taker_volume', {'ratio': ratio})

    def update_top_trader_signal(self, symbol: str, ratio: float):
        state = self._get_state(symbol)
        state.top_trader_ratio = ratio
        state.last_updated = time.time()
        self._store_detail(symbol, 'top_trader', {'ratio': ratio})

    # ─────────────────────────────────────────────────────────────────────
    # NEW INTERFACE — bulk update + fresh score computation
    # ─────────────────────────────────────────────────────────────────────

    def update_state(self, symbol: str, **kwargs):
        """
        Bulk-update snapshot fields for a symbol.

        Validates keys against AggregatorState fields — typos in caller code
        will raise AttributeError rather than silently doing nothing.
        """
        state = self._get_state(symbol)
        for key, val in kwargs.items():
            if hasattr(state, key):
                setattr(state, key, val)
            else:
                raise AttributeError(
                    f"AggregatorState has no field '{key}' — check for typo"
                )
        state.last_updated = time.time()

    async def compute_score(self, symbol: str) -> Tuple[int, List[ComponentResult], Dict]:
        """
        Compute a FRESH score from the current snapshot state.

        Returns (score, components, regime_info) where:
          - score is in [-6, +6]
          - components is list of ComponentResult
          - regime_info contains regime classification data

        This method is stateless with respect to score — result depends only
        on the current snapshot, never on previous calls.

        Gate: Check volatility regime first. If CHOPPY, skip scoring entirely.
        """
        if symbol not in self._states:
            return 0, [], {"regime": "RANGING", "skipped": False}

        # ── Gate: Check volatility regime first ──────────────────────────────
        regime_result = await self.regime_detector.detect(symbol)
        regime = regime_result["regime"]
        adx = regime_result["adx"]
        atr_percentile = regime_result["atr_percentile"]
        bbw_percentile = regime_result["bbw_percentile"]
        regime_confidence = regime_result["confidence"]

        # If CHOPPY, block signal immediately
        if regime == "CHOPPY":
            logger.info(f"[{symbol}] CHOPPY market detected - signal blocked")
            return 0, [], {
                "regime": regime,
                "adx": adx,
                "atr_percentile": atr_percentile,
                "bbw_percentile": bbw_percentile,
                "regime_confidence": regime_confidence,
                "skipped": True
            }

        s = self._states[symbol]
        components: List[ComponentResult] = []

        # ── Component 1: Liquidation dominance ──────────────────────────────
        liq_total = s.liq_long_usd + s.liq_short_usd
        if liq_total > 0:
            short_dom = s.liq_short_usd > s.liq_long_usd
            components.append(ComponentResult(
                name="liquidation_dominance",
                value=round(liq_total, 2),
                passed=True,
                score_delta=1 if short_dom else -1
            ))
        else:
            components.append(ComponentResult(
                name="liquidation_dominance",
                value=0.0, passed=False, score_delta=0
            ))

        # ── Component 2: Order book imbalance (graduated) ─────────────────
        ob = s.ob_imbalance
        if ob > self.OB_STRONG_THRESHOLD:
            ob_delta = 2
        elif ob > self.OB_WEAK_THRESHOLD:
            ob_delta = 1
        elif ob < -self.OB_STRONG_THRESHOLD:
            ob_delta = -2
        elif ob < -self.OB_WEAK_THRESHOLD:
            ob_delta = -1
        else:
            ob_delta = 0

        components.append(ComponentResult(
            name="ob_imbalance",
            value=round(ob, 4),
            passed=ob_delta != 0,
            score_delta=ob_delta
        ))

        # ── Component 3: Whale dominance (tiered) ──────────────────────────
        whale_total = s.whale_buyers + s.whale_sellers
        if whale_total == 0:
            whale_delta = 0
        elif s.whale_buyers > s.whale_sellers:
            dominant = s.whale_buyers
            whale_delta = 2 if dominant >= 3 else 1
        elif s.whale_sellers > s.whale_buyers:
            dominant = s.whale_sellers
            whale_delta = -2 if dominant >= 3 else -1
        else:
            whale_delta = 0

        components.append(ComponentResult(
            name="whale_dominance",
            value=float(s.whale_buyers - s.whale_sellers),
            passed=whale_delta != 0,
            score_delta=whale_delta
        ))

        # ── Component 4: Open Interest direction ───────────────────────────
        # Score is pure OI direction (±1); price direction is only for
        # confirmation display, not scoring.
        oi_pct = s.oi_change_pct
        if oi_pct > self.OI_SIGNAL_THRESHOLD:
            oi_base = 1
        elif oi_pct < -self.OI_SIGNAL_THRESHOLD:
            oi_base = -1
        else:
            oi_base = 0

        components.append(ComponentResult(
            name="oi_direction",
            value=round(oi_pct, 4),
            passed=oi_base != 0,
            score_delta=oi_base
        ))

        # ── Component 5: Taker buy/sell volume ────────────────────────────
        buy_pct = s.taker_buy_ratio / (s.taker_buy_ratio + 1) if s.taker_buy_ratio > 0 else 0.5
        if buy_pct > self.TAKER_BULL_THRESH:
            taker_delta = 1
        elif buy_pct < self.TAKER_BEAR_THRESH:
            taker_delta = -1
        else:
            taker_delta = 0

        components.append(ComponentResult(
            name="taker_volume",
            value=round(s.taker_buy_ratio, 4),
            passed=taker_delta != 0,
            score_delta=taker_delta
        ))

        # ── Component 6: Top trader long/short ratio ───────────────────────
        if s.top_trader_ratio > self.TRADER_LONG_THRESH:
            trader_delta = 1
        elif s.top_trader_ratio < self.TRADER_SHORT_THRESH:
            trader_delta = -1
        else:
            trader_delta = 0

        components.append(ComponentResult(
            name="top_trader_ratio",
            value=round(s.top_trader_ratio, 2),
            passed=trader_delta != 0,
            score_delta=trader_delta
        ))

        # ── Total score ───────────────────────────────────────────────────
        raw_score = sum(c.score_delta for c in components)
        raw_score = max(-self.MAX_COMPONENTS, min(self.MAX_COMPONENTS, raw_score))

        # Apply regime multiplier to confidence (not score itself)
        # TRENDING: no change, RANGING: reduce confidence by 20%
        if regime == "TRENDING":
            confidence_multiplier = 1.0
        elif regime == "RANGING":
            confidence_multiplier = 0.8
        else:
            confidence_multiplier = 1.0

        return raw_score, components, {
            "regime": regime,
            "adx": adx,
            "atr_percentile": atr_percentile,
            "bbw_percentile": bbw_percentile,
            "regime_confidence": regime_confidence,
            "confidence_multiplier": confidence_multiplier,
            "skipped": False
        }

    # ─────────────────────────────────────────────────────────────────────
    # LEGACY GETTERS — used by main.py
    # ─────────────────────────────────────────────────────────────────────

    async def get_signal(self, symbol: str) -> Tuple[str, int, int, Dict, str]:
        """
        Returns (signal, score_100, raw_score, details, emoji).

        signal: 'STRONG_LONG' | 'WEAK_LONG' | 'NEUTRAL' | 'WEAK_SHORT' | 'STRONG_SHORT'
        score_100: 0–100 (linear scale)
        raw_score: -6 to +6
        """
        raw_score, _, regime_info = await self.compute_score(symbol)
        score_100 = self.raw_to_100(raw_score)

        if score_100 >= 75:
            signal, emoji = 'STRONG_LONG', '🟢'
        elif score_100 >= 51:
            signal, emoji = 'WEAK_LONG', '🟡'
        elif score_100 <= 25:
            signal, emoji = 'STRONG_SHORT', '🔴'
        elif score_100 <= 49:
            signal, emoji = 'WEAK_SHORT', '🟡'
        else:
            signal, emoji = 'NEUTRAL', '⚪'

        # Store regime info in details for Telegram and display
        details = self.signal_details.get(symbol, {}).copy()
        details['regime_info'] = regime_info

        return signal, score_100, raw_score, details, emoji

    def get_signal_type(self, score: int) -> str:
        abs_s = abs(score)
        if abs_s >= self.STRONG_THRESHOLD:
            return "STRONG_LONG" if score > 0 else "STRONG_SHORT"
        elif abs_s >= 2:
            return "WEAK_LONG" if score > 0 else "WEAK_SHORT"
        return "NEUTRAL"

    @staticmethod
    def raw_to_100(raw_score: int) -> int:
        """Convert raw score (-6 to +6) → 0–100 scale."""
        normalized = (raw_score + 6) / 12
        return int(normalized * 100)

    async def get_raw_score(self, symbol: str) -> int:
        score, _, _ = await self.compute_score(symbol)
        return score

    def is_strong(self, score: int) -> bool:
        return abs(score) >= self.STRONG_THRESHOLD

    def get_state(self, symbol: str) -> Optional[AggregatorState]:
        return self._states.get(symbol)

    def clear_state(self, symbol: str):
        """Reset state for a symbol — called after signal is locked."""
        if symbol in self._states:
            self._states[symbol] = AggregatorState(symbol=symbol)

    def reset_scores(self):
        """
        Clear stale states that haven't been updated in > 5 minutes.
        Active symbols are NOT reset — only abandoned ones.
        """
        now = time.time()
        stale = 300  # 5 minutes
        for symbol in list(self._states.keys()):
            if now - self._states[symbol].last_updated > stale:
                self._states[symbol] = AggregatorState(symbol=symbol)
                self.signal_details.pop(symbol, None)

    # ─────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────

    def _get_state(self, symbol: str) -> AggregatorState:
        if symbol not in self._states:
            self._states[symbol] = AggregatorState(symbol=symbol)
        return self._states[symbol]

    def _store_detail(self, symbol: str, signal_type: str, details: Dict):
        if symbol not in self.signal_details:
            self.signal_details[symbol] = {}
        self.signal_details[symbol][signal_type] = details
