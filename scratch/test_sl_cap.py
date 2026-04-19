import sys
import os
import asyncio
from unittest.mock import MagicMock

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, project_root)

import config
from modules.tp_sl_calculator import TPSLCalculator

async def test_sl_cap():
    calc = TPSLCalculator()
    
    # Mock ATR to be very large (e.g., 50% of price)
    entry_price = 100.0
    calc.atr_cache['TESTUSDT'] = 50.0  # ATR is 50
    config.SL_MULTIPLIER = 1.0
    config.MAX_SL_PERCENT = 0.10  # 10%
    
    print(f"Entry: {entry_price}, ATR: {calc.atr_cache['TESTUSDT']}, Max SL %: {config.MAX_SL_PERCENT}")
    
    # Test LONG
    sl_price, sl_pct = calc.calculate_stop_loss('TESTUSDT', entry_price, 'LONG')
    print(f"LONG SL Price: {sl_price}, SL Pct: {sl_pct}%")
    
    # Expected: SL should be at 90.0 (10% below 100), not 50.0
    assert abs(sl_price - 90.0) < 0.0001
    assert abs(sl_pct - (-10.0)) < 0.0001
    
    # Test SHORT
    sl_price, sl_pct = calc.calculate_stop_loss('TESTUSDT', entry_price, 'SHORT')
    print(f"SHORT SL Price: {sl_price}, SL Pct: {sl_pct}%")
    
    # Expected: SL should be at 110.0 (10% above 100), not 150.0
    assert abs(sl_price - 110.0) < 0.0001
    assert abs(sl_pct - 10.0) < 0.0001
    
    print("\nSUCCESS: SL Cap is working correctly!")

if __name__ == "__main__":
    asyncio.run(test_sl_cap())
