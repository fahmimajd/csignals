"""
Monte Carlo Filter for Crypto Signal System
Simulates 1000 price scenarios to evaluate TP/SL probability
"""
import asyncio
import aiohttp
import numpy as np
from typing import Dict, Optional
import time
import config
from concurrent.futures import ThreadPoolExecutor

class MonteCarloFilter:
    def __init__(self):
        self.cache = {}
        self.cache_timeout = 300  # 5 minutes
        self.executor = ThreadPoolExecutor(max_workers=4)
        
    def _calculate_realized_vol(self, closes: list) -> float:
        """Calculate realized volatility per hour from close prices"""
        if len(closes) < 2:
            return 0.02  # Default 2% hourly vol
        
        log_returns = np.diff(np.log(closes))
        # Hourly volatility (data is 1h candles)
        vol = float(np.std(log_returns, ddof=1))
        # Crypto typically has higher volatility, ensure minimum realistic value
        return max(vol, 0.01)  # Minimum 1% hourly vol for crypto
    
    def _calculate_drift(self, closes: list, hours: int = 6) -> float:
        """
        Calculate drift from recent N hours.
        Positive = uptrend, negative = downtrend
        Returns drift per hour
        """
        if len(closes) < hours:
            hours = min(len(closes), 6)
        
        if hours < 2:
            return 0.0
        
        recent = closes[-hours:]
        total_return = np.log(recent[-1] / recent[0])
        drift_per_hour = total_return / hours
        return float(drift_per_hour)
    
    def _run_simulation(
        self,
        entry: float,
        tp: float,
        sl: float,
        hold_hours: float,
        mu: float,
        sigma: float,
        n_sim: int,
        dt: float
    ) -> Dict:
        """
        Run Monte Carlo simulation in a separate thread.
        Uses Geometric Brownian Motion with jump diffusion.
        """
        n_steps = max(1, int(hold_hours / dt))
        hit_tp = 0
        hit_sl = 0
        expired = 0
        
        # Calculate distance to TP and SL in percentage terms
        tp_distance = (tp - entry) / entry
        sl_distance = (sl - entry) / entry
        
        # Pre-calculate constants
        sqrt_dt = np.sqrt(dt)
        drift_term = (mu - 0.5 * sigma**2) * dt
        vol_term = sigma * sqrt_dt
        
        for _ in range(n_sim):
            price = entry
            cumulative_return = 0.0
            outcome = "expired"
            
            for step in range(n_steps):
                # Standard GBM component
                z = np.random.standard_normal()
                
                # Jump component: 5% chance per step
                jump = 0.0
                if np.random.random() < 0.05:
                    jump_size = np.random.choice([1.5, 2.0, 3.0])
                    jump_direction = np.random.choice([-1, 1])
                    jump = jump_direction * jump_size * vol_term
                
                # Calculate return for this step
                step_return = drift_term + vol_term * z + jump
                cumulative_return += step_return
                
                # Price update using cumulative return
                price = entry * np.exp(cumulative_return)
                
                # Check barriers
                if price >= tp:
                    outcome = "tp"
                    break
                elif price <= sl:
                    outcome = "sl"
                    break
            
            if outcome == "tp":
                hit_tp += 1
            elif outcome == "sl":
                hit_sl += 1
            else:
                expired += 1
        
        total = n_sim
        prob_tp = round(hit_tp / total * 100, 1)
        prob_sl = round(hit_sl / total * 100, 1)
        prob_expire = round(expired / total * 100, 1)
        
        return {
            "prob_tp": prob_tp,
            "prob_sl": prob_sl,
            "prob_expire": prob_expire,
            "n_sim": n_sim,
            "mu_annual": round(mu * 8760, 2),
            "sigma_annual": round(sigma * np.sqrt(8760), 2)
        }
    
    async def _fetch_klines(self, symbol: str) -> Optional[list]:
        """Fetch candle data from Binance API"""
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {
            'symbol': symbol.upper(),
            'interval': '1h',
            'limit': 24
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        if len(data) >= 10:
                            closes = [float(k[4]) for k in data]
                            return closes
                    return None
        except Exception as e:
            print(f"MC fetch error for {symbol}: {e}")
            return None
    
    def _get_confidence(self, prob_tp: float) -> str:
        """Determine confidence level based on TP probability"""
        if prob_tp >= config.MC_HIGH_CONF:
            return "HIGH"
        elif prob_tp >= 50.0:
            return "MEDIUM"
        elif prob_tp >= config.MC_MIN_PROB_TP:
            return "LOW"
        else:
            return "SKIP"
    
    async def evaluate(
        self,
        symbol: str,
        entry_price: float,
        take_profit: float,
        stop_loss: float,
        hold_hours: float,
        signal_type: str
    ) -> dict:
        """
        Fetch data, calculate parameters, run simulation.
        Returns complete dict including confidence level.
        """
        current_time = time.time()
        
        # Check cache
        cache_key = f"{symbol}_{entry_price}_{take_profit}_{stop_loss}"
        if cache_key in self.cache:
            cached_time, cached_result = self.cache[cache_key]
            if current_time - cached_time < self.cache_timeout:
                return cached_result
        
        # Validate inputs
        if not all([entry_price, take_profit, stop_loss, hold_hours]):
            # Fallback: medium confidence, don't skip
            result = {
                "prob_tp": 50.0,
                "prob_sl": 30.0,
                "prob_expire": 20.0,
                "confidence": "MEDIUM",
                "n_sim": config.MC_N_SIMULATIONS,
                "skipped": False,
                "error": "Invalid input parameters"
            }
            self.cache[cache_key] = (current_time, result)
            return result
        
        # Fetch historical data
        closes = await self._fetch_klines(symbol)
        
        if closes is None or len(closes) < 10:
            # Fallback: use default volatility, don't skip signal
            sigma = 0.02  # 2% hourly vol default
            mu = 0.0      # No drift assumption
        else:
            sigma = self._calculate_realized_vol(closes)
            mu = self._calculate_drift(closes, hours=6)
        
        # Adjust parameters based on signal direction
        if signal_type == "STRONG_SHORT":
            mu = -mu  # Invert drift for short signals
        
        # Run simulation in executor to avoid blocking event loop
        loop = asyncio.get_event_loop()
        
        try:
            mc_result = await loop.run_in_executor(
                self.executor,
                self._run_simulation,
                entry_price,
                take_profit,
                stop_loss,
                hold_hours,
                mu,
                sigma,
                config.MC_N_SIMULATIONS,
                1/6  # dt = 10 minutes (6 steps per hour)
            )
        except Exception as e:
            print(f"MC simulation error for {symbol}: {e}")
            # Fallback on error
            mc_result = {
                "prob_tp": 50.0,
                "prob_sl": 30.0,
                "prob_expire": 20.0,
                "n_sim": config.MC_N_SIMULATIONS
            }
        
        # Determine confidence
        confidence = self._get_confidence(mc_result["prob_tp"])
        skipped = (confidence == "SKIP")
        
        result = {
            "prob_tp": mc_result["prob_tp"],
            "prob_sl": mc_result["prob_sl"],
            "prob_expire": mc_result["prob_expire"],
            "confidence": confidence,
            "n_sim": mc_result["n_sim"],
            "mu_annual": mc_result.get("mu_annual", 0),
            "sigma_annual": mc_result.get("sigma_annual", 0),
            "skipped": skipped
        }
        
        # Cache result
        self.cache[cache_key] = (current_time, result)
        return result
    
    async def close(self):
        """Clean up executor"""
        self.executor.shutdown(wait=False)
