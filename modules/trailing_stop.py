import time
from typing import Dict, Optional
import config

class TrailingStopManager:
    def __init__(self):
        self.active_trailing: Dict[str, Dict] = {}
        
    def update_trailing_stop(self, symbol: str, side: str, entry_price: float, current_price: float, atr: float) -> Optional[float]:
        """Update trailing stop and return new stop level if updated"""
        key = f"{symbol}_{side}"
        
        if key not in self.active_trailing:
            self.active_trailing[key] = {
                'entry': entry_price,
                'side': side,
                'trailing_activated': False,
                'best_price': entry_price if side == 'LONG' else entry_price,
                'current_stop': None
            }
            
        data = self.active_trailing[key]
        
        # Update best price
        if side == 'LONG':
            if current_price > data['best_price']:
                data['best_price'] = current_price
        else:  # SHORT
            if current_price < data['best_price']:
                data['best_price'] = current_price
                
        # Check if trailing should be activated
        if not data['trailing_activated']:
            profit_move = abs(current_price - entry_price)
            trigger_distance = atr * config.TRAIL_TRIGGER_ATR
            if profit_move >= trigger_distance:
                data['trailing_activated'] = True
                
        # Calculate trailing stop if activated
        if data['trailing_activated']:
            trail_distance = atr * config.TRAIL_DISTANCE_ATR
            if side == 'LONG':
                new_stop = data['best_price'] - trail_distance
            else:
                new_stop = data['best_price'] + trail_distance
                
            # Only update if stop is tighter (higher for LONG, lower for SHORT)
            if data['current_stop'] is None:
                data['current_stop'] = new_stop
            else:
                if side == 'LONG' and new_stop > data['current_stop']:
                    data['current_stop'] = new_stop
                elif side == 'SHORT' and new_stop < data['current_stop']:
                    data['current_stop'] = new_stop
                    
            return data['current_stop']
            
        return None
        
    def get_trailing_stop(self, symbol: str, side: str) -> Optional[float]:
        """Get current trailing stop level"""
        key = f"{symbol}_{side}"
        return self.active_trailing.get(key, {}).get('current_stop')
        
    def is_trail_active(self, symbol: str, side: str = None) -> bool:
        """Check if trailing stop is active for a symbol (optionally for a specific side)."""
        if side:
            key = f"{symbol}_{side}"
            data = self.active_trailing.get(key)
            return data is not None and data.get('trailing_activated', False)
        # If no side specified, check both LONG and SHORT
        for s in ('LONG', 'SHORT'):
            key = f"{symbol}_{s}"
            data = self.active_trailing.get(key)
            if data is not None and data.get('trailing_activated', False):
                return True
        return False

    def reset(self, symbol: str, side: str):
        """Reset trailing stop for a symbol/side"""
        key = f"{symbol}_{side}"
        if key in self.active_trailing:
            del self.active_trailing[key]
