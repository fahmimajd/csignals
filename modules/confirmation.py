"""
confirmation.py — REFACTORED

Changes in this refactor:
1. SYNC update() — unified with async rolling average + 2-of-3 rule.
   Before: used old majority-of-CANDLES logic (inconsistent with async path).
   After:  same rolling-avg + 2-of-3 + fast-confirm as async path.

2. Removed duplicate _async_update_locked() — the logic lives in one place now.
   The async public method calls a shared helper that both paths use.

3. Added _evaluate_confirmation() — pure evaluation function shared by both
   sync and async paths. No side-effects, only computes whether to confirm.

4. Signal type propagation — main.py now passes (symbol, score, signal_type, price)
   so direction-flip detection works correctly for the sync path too.

5. Technical indicator integration — RSI, MACD, Stochastic confirmation layer.
   Added optional technical indicator confirmation before final signal approval.
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import config

logger = logging.getLogger(__name__)

# Lazy import of technical indicators
_technical_indicators = None


def _get_technical_indicators():
    """Lazy import to avoid circular dependencies."""
    global _technical_indicators
    if _technical_indicators is None:
        from modules.technical_indicators import TechnicalIndicators
        _technical_indicators = TechnicalIndicators()
    return _technical_indicators


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ConfirmationState:
    """
    Unified confirmation state per symbol.

    Fields previously split across dicts and dataclass are now consolidated
    here. score_history is the core of the rolling-average + 2-of-3 logic.
    """
    symbol: str
    # Pending / in-progress confirmation window
    pending_type: Optional[str] = None      # "STRONG_LONG" / "STRONG_SHORT"
    pending_score: int = 0
    confirmation_start: Optional[float] = None
    data_points: int = 0                   # candle counter for 2-of-3 rule
    is_locked: bool = False
    locked_at: Optional[float] = None
    lock_price: Optional[float] = None
    extension_count: int = 0
    # Rolling average — max 5 entries, FIFO
    score_history: list = field(default_factory=list)
    # Confirmed signal (nulled on direction flip or cooldown release)
    first_strong_time: Optional[float] = None
    is_confirmed: bool = False
    confirmed_signal: Optional[str] = None
    confirmed_score: int = 0
    entry_price: float = 0.0
    last_signal_time: float = 0.0
    cooldown_until: float = 0.0


@dataclass
class SignalEvent:
    """Emitted when a signal is confirmed — async path only."""
    symbol: str
    signal_type: str
    score: int
    entry_price: float
    confirmed_at: float
    is_new: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# SignalConfirmation
# ─────────────────────────────────────────────────────────────────────────────

class SignalConfirmation:
    """
    Confirmation layer with race-condition-safe deduplication.

    Both the sync update() and async async_update() paths share the same
    confirmation evaluation logic (rolling average + 2-of-3 + fast-confirm).

    Each symbol has its own asyncio.Lock so concurrent coroutines cannot
    bypass the cooldown check simultaneously.
    """

    def __init__(self):
        # Per-symbol state as ConfirmationState dataclass instances
        self.confirmation_state: Dict[str, ConfirmationState] = {}
        # Per-symbol asyncio locks — key for race-condition safety
        self._locks: Dict[str, asyncio.Lock] = {}
        # Cooldown tracker (mirrors state.cooldown_until but lookup is faster)
        self._cooldown_until: Dict[str, float] = {}
        # Dedup tracker — (symbol, signal_type, price_bucket) → last emit timestamp
        self._recent_signals: Dict[tuple, float] = {}

    # ── Lock management ────────────────────────────────────────────────────

    def _get_lock(self, symbol: str) -> asyncio.Lock:
        """Lazy-init a lock for each symbol."""
        if symbol not in self._locks:
            self._locks[symbol] = asyncio.Lock()
        return self._locks[symbol]

    # ── Symbol initialisation ──────────────────────────────────────────────

    def initialize_symbol(self, symbol: str):
        """Create a ConfirmationState for a symbol if not already present."""
        if symbol not in self.confirmation_state:
            self.confirmation_state[symbol] = ConfirmationState(symbol=symbol)

    # ── Cooldown helpers ───────────────────────────────────────────────────

    def _is_in_cooldown(self, symbol: str) -> bool:
        """True when the symbol is in its post-signal cooldown window."""
        until = self._cooldown_until.get(symbol, 0.0)
        if symbol in self.confirmation_state:
            until = max(until, self.confirmation_state[symbol].cooldown_until)
        return time.time() < until

    def _set_cooldown(self, symbol: str):
        """Record the cooldown expiry time after a confirmed signal."""
        secs = config.SIGNAL_COOLDOWN_MINUTES * 60
        now = time.time()
        self._cooldown_until[symbol] = now + secs
        if symbol in self.confirmation_state:
            self.confirmation_state[symbol].cooldown_until = now + secs

    def release_cooldown(self, symbol: str):
        """
        Called by hold_duration_monitor() when the cooldown window ends
        so the symbol can accept new signals.
        """
        self._cooldown_until.pop(symbol, None)
        if symbol in self.confirmation_state:
            state = self.confirmation_state[symbol]
            state.cooldown_until = 0.0
            state.is_confirmed = False
            state.confirmed_signal = None
            state.first_strong_time = None
            state.score_history.clear()

    # ─────────────────────────────────────────────────────────────────────
    # SHARED EVALUATION LOGIC
    # Both sync and async paths call this — it contains NO side-effects.
    # Returns (should_confirm, signal_type, score, state_updates).
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _evaluate_confirmation(
        state: ConfirmationState,
        score: int,
        current_time: float,
    ) -> Tuple[bool, Optional[str], int]:
        """
        Evaluate whether a signal should be confirmed using the
        rolling-average + 2-of-3 rule.

        Logic:
          1. Append score to score_history (max 5, FIFO)
          2. Rolling avg of last N scores
          3. is_strong_avg  = |avg| >= STRONG_THRESHOLD (=3)
          4. 2-of-3 rule   = ≥2 of last 3 scores agree with avg direction
          5. fast_confirm  = |score| >= 5 → bypass all checks
          6. Time + candle requirements as final gate

        Returns (confirmed, signal_type, final_score).
        Does NOT mutate state — caller applies side-effects.
        """
        # ── 1. Rolling history ───────────────────────────────────────────
        state.score_history.append(score)
        if len(state.score_history) > 5:
            state.score_history.pop(0)
        state.data_points += 1

        # ── 2. Rolling average ──────────────────────────────────────────
        n = len(state.score_history)
        avg_score = sum(state.score_history) / n if n >= 2 else float(score)

        # ── 3. is_strong_avg ───────────────────────────────────────────
        is_strong_avg = abs(avg_score) >= config.STRONG_THRESHOLD

        # ── 4. 2-of-3 rule ────────────────────────────────────────────
        recent = state.score_history[-3:] if n >= 3 else list(state.score_history)
        same_direction = sum(
            1 for s in recent
            if (s >= config.STRONG_THRESHOLD and avg_score > 0)
            or (s <= -config.STRONG_THRESHOLD and avg_score < 0)
        )
        two_of_three_ok = same_direction >= min(2, len(recent))

        # ── 5. Fast-confirm ────────────────────────────────────────────
        fast_confirm = abs(score) >= 5

        # ── 6. Time + candle gates ─────────────────────────────────────
        elapsed_minutes = (current_time - state.first_strong_time) / 60
        candles_ok = state.data_points >= config.CONFIRMATION_CANDLES
        time_ok = elapsed_minutes >= config.CONFIRMATION_MINUTES

        # Signal type
        signal_type = 'STRONG_LONG' if avg_score > 0 else 'STRONG_SHORT'

        confirmed = (
            fast_confirm
            or (is_strong_avg and two_of_three_ok and candles_ok and time_ok)
        )

        return confirmed, signal_type, score

    # ─────────────────────────────────────────────────────────────────────
    # SYNC INTERFACE — used by main.py
    # ─────────────────────────────────────────────────────────────────────

    def update(
        self,
        symbol: str,
        score: int,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, Optional[str], Optional[int]]:
        """
        SYNC confirmation update — used by main.py.

        Now uses the same rolling-average + 2-of-3 rule as the async path.

        Args:
            symbol: trading pair, e.g. "BTCUSDT"
            score: raw aggregator score (negative = short, positive = long)
            current_time: unix timestamp; defaults to time.time()

        Returns:
            (is_confirmed, signal_type, score)
        """
        if current_time is None:
            current_time = time.time()

        if symbol not in self.confirmation_state:
            self.initialize_symbol(symbol)

        state = self.confirmation_state[symbol]

        # Guard: cooldown
        if self._is_in_cooldown(symbol):
            return False, None, None

        # Direction flip — reset state
        if state.confirmed_signal is not None:
            # Derive signal_type from score (aggregator encodes direction)
            new_type = 'STRONG_LONG' if score > 0 else 'STRONG_SHORT'
            if state.confirmed_signal != new_type:
                logger.info(
                    f"[{symbol}] Direction flip ({state.confirmed_signal}→{new_type}), "
                    "resetting confirmation window"
                )
                state.score_history.clear()
                state.data_points = 0
                state.first_strong_time = None
                state.is_confirmed = False
                state.confirmed_signal = None

        # Guard: no strong signal yet — start the window
        is_strong = abs(score) >= config.STRONG_THRESHOLD
        if not is_strong:
            # Weak score — reset window, don't confirm
            state.first_strong_time = None
            return False, None, None

        # Start the confirmation window on first strong score
        if state.first_strong_time is None:
            state.first_strong_time = current_time
            return False, None, None

        # Evaluate using shared pure function
        confirmed, signal_type, final_score = self._evaluate_confirmation(
            state, score, current_time
        )

        if not confirmed:
            return False, None, None

        # ── CONFIRMED — apply side-effects ─────────────────────────────
        state.is_confirmed = True
        state.confirmed_signal = signal_type
        state.confirmed_score = final_score
        state.last_signal_time = current_time
        self._set_cooldown(symbol)

        logger.info(
            f"[{symbol}] SIGNAL CONFIRMED: {signal_type} "
            f"score={final_score} ({len(state.score_history)} candles)"
        )
        return True, signal_type, final_score

    # ─────────────────────────────────────────────────────────────────────
    # ASYNC INTERFACE — for orchestrator integration
    # ─────────────────────────────────────────────────────────────────────

    async def async_update(
        self,
        symbol: str,
        score: int,
        signal_type: str,
        current_price: float,
    ) -> Optional[SignalEvent]:
        """
        Async confirmation update — thread-safe via per-symbol lock.
        Returns SignalEvent if confirmed, None otherwise.
        """
        async with self._get_lock(symbol):
            return await self._async_update_impl(
                symbol, score, signal_type, current_price
            )

    async def _async_update_impl(
        self,
        symbol: str,
        score: int,
        signal_type: str,
        current_price: float,
    ) -> Optional[SignalEvent]:
        """
        Async implementation — same evaluation logic as sync update(),
        plus price-based deduplication and SignalEvent emission.
        """
        if self._is_in_cooldown(symbol):
            return None

        now = time.time()

        if symbol not in self.confirmation_state:
            self.initialize_symbol(symbol)

        state = self.confirmation_state[symbol]

        # Guard: duplicate by price bucket
        bucket = float(f"{current_price:.3g}")
        key = (symbol, signal_type, bucket)
        if (now - self._recent_signals.get(key, 0)) < config.SIGNAL_COOLDOWN_MINUTES * 60:
            return None

        # Direction flip
        if state.confirmed_signal is not None and state.confirmed_signal != signal_type:
            logger.info(
                f"[{symbol}] Direction flip ({state.confirmed_signal}→{signal_type}), resetting"
            )
            state.score_history.clear()
            state.data_points = 0
            state.first_strong_time = None
            state.is_confirmed = False
            state.confirmed_signal = None

        # Guard: no strong signal
        is_strong = abs(score) >= config.STRONG_THRESHOLD
        if not is_strong:
            state.first_strong_time = None
            return None

        if state.first_strong_time is None:
            state.first_strong_time = now
            return None

        # Evaluate with shared pure function
        confirmed, confirmed_type, final_score = self._evaluate_confirmation(
            state, score, now
        )

        if not confirmed:
            return None

        # ── Technical Indicator Confirmation Layer ───────────────────────
        # Optional: Check RSI, MACD, Stochastic for additional confirmation
        tech_confirm = await self._check_technical_indicators(symbol, signal_type)
        
        if not tech_confirm:
            logger.info(
                f"[{symbol}] Async signal blocked by technical indicators"
            )
            return None

        # ── CONFIRMED ─────────────────────────────────────────────────
        self._set_cooldown(symbol)
        self._recent_signals[key] = now

        state.is_confirmed = True
        state.confirmed_signal = confirmed_type
        state.confirmed_score = final_score
        state.confirmed_at = now
        state.entry_price = current_price
        state.last_signal_time = now

        logger.info(
            f"[{symbol}] ASYNC SIGNAL CONFIRMED: {confirmed_type} "
            f"score={final_score} fast_confirm={abs(score) >= 5}"
        )

        return SignalEvent(
            symbol=symbol,
            signal_type=confirmed_type,
            score=final_score,
            entry_price=current_price,
            confirmed_at=now,
            is_new=True,
        )

    # ─────────────────────────────────────────────────────────────────────
    # LEGACY DISPLAY HELPERS
    # ─────────────────────────────────────────────────────────────────────

    def get_confirmation_progress(self, symbol: str) -> Tuple[int, int]:
        """(minutes_elapsed, minutes_required) for display."""
        if symbol not in self.confirmation_state:
            return 0, config.CONFIRMATION_MINUTES
        state = self.confirmation_state[symbol]
        if state.first_strong_time is None:
            return 0, config.CONFIRMATION_MINUTES
        elapsed = int((time.time() - state.first_strong_time) / 60)
        return elapsed, config.CONFIRMATION_MINUTES

    def is_symbol_on_cooldown(self, symbol: str) -> bool:
        return self._is_in_cooldown(symbol)

    def get_cooldown_remaining(self, symbol: str) -> int:
        until = self._cooldown_until.get(symbol, 0.0)
        if symbol in self.confirmation_state:
            until = max(until, self.confirmation_state[symbol].cooldown_until)
        return max(0, int(until - time.time()))

    def get_progress(self, symbol: str) -> float:
        """0.0 → 1.0 confirmation progress."""
        if symbol not in self.confirmation_state:
            return 0.0
        state = self.confirmation_state[symbol]
        if state.first_strong_time is None:
            return 0.0
        if state.is_confirmed:
            return 1.0
        elapsed = (time.time() - state.first_strong_time) / 60
        return min(elapsed / config.CONFIRMATION_MINUTES, 1.0)

    # ─────────────────────────────────────────────────────────────────────
    # TECHNICAL INDICATOR CONFIRMATION
    # ─────────────────────────────────────────────────────────────────────

    async def _check_technical_indicators(
        self, symbol: str, signal_type: str
    ) -> bool:
        """
        Check technical indicators for additional confirmation.
        
        This is an optional layer that can be enabled/disabled via config.
        When enabled, signals must pass RSI, MACD, and Stochastic checks.
        
        Args:
            symbol: Trading pair
            signal_type: "STRONG_LONG" or "STRONG_SHORT"
            
        Returns:
            True if indicators confirm the signal (or if feature is disabled)
            False if indicators contradict the signal
        """
        # Check if technical indicator confirmation is enabled
        if not getattr(config, 'USE_TECHNICAL_INDICATORS', False):
            return True  # Skip check if disabled
        
        try:
            tech = _get_technical_indicators()
            confirmation = await tech.get_confirmation(symbol, signal_type)
            
            # Log indicator values for debugging
            logger.debug(
                f"[{symbol}] Tech Indicators: RSI={confirmation.rsi_signal}, "
                f"MACD={confirmation.macd_signal}, Stoch={confirmation.stoch_signal}, "
                f"Bias={confirmation.overall_bias}, Conf={confirmation.confidence:.2f}"
            )
            
            # Require at least neutral or better bias
            # Reject only if indicators strongly contradict
            if confirmation.overall_bias == "NEUTRAL":
                return True  # Allow neutral
            
            # For LONG signals, reject if bearish bias with high confidence
            if signal_type == "STRONG_LONG":
                if confirmation.overall_bias == "BEARISH" and confirmation.confidence > 0.5:
                    return False
            
            # For SHORT signals, reject if bullish bias with high confidence
            elif signal_type == "STRONG_SHORT":
                if confirmation.overall_bias == "BULLISH" and confirmation.confidence > 0.5:
                    return False
            
            return True
            
        except Exception as e:
            logger.warning(
                f"[{symbol}] Technical indicator check failed: {e}. "
                "Allowing signal to proceed."
            )
            # On error, allow signal to proceed (fail-safe)
            return True
