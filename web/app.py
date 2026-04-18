from flask import Flask, render_template, jsonify, request
import asyncio
import json
import threading
from datetime import datetime, timedelta
import logging
import sys
import os
from decimal import Decimal
from typing import Optional

# Add the parent directory to sys.path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.database import Database
import config

app = Flask(__name__)
app.config['SECRET_KEY'] = 'cyberpunk_signal_monitor_2026'

# CRITICAL FIX: Singleton database pool to prevent connection exhaustion
_db_instance: Optional[Database] = None
_db_lock = threading.Lock()

def get_database() -> Database:
    """Get or create singleton database instance."""
    global _db_instance
    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                _db_instance = Database()
    return _db_instance

# Initialize database
db = get_database()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---- Persistent event loop for asyncpg ----
_loop: asyncio.AbstractEventLoop = None
_loop_thread: threading.Thread = None


def _start_loop():
    """Run a persistent event loop in a background thread."""
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


def _ensure_loop():
    """Start the background event loop if not already running."""
    global _loop, _loop_thread
    if _loop is None or not _loop.is_running():
        _loop_thread = threading.Thread(target=_start_loop, daemon=True)
        _loop_thread.start()
        # Wait until the loop is actually running
        while _loop is None or not _loop.is_running():
            pass
        # Initialize the database pool inside the persistent loop
        future = asyncio.run_coroutine_threadsafe(db.initialize(), _loop)
        future.result(timeout=10)


def _run_async(coro):
    """Run an async coroutine on the persistent background event loop."""
    _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=15)


def _serialize(obj):
    """Convert Decimal/datetime objects for JSON serialization."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    return obj


# ==================== PAGE ROUTES ====================

@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('index.html')


# ==================== SIGNAL APIs ====================

@app.route('/api/signals')
def get_signals():
    """Get recent signals with optional filters"""
    try:
        symbol = request.args.get('symbol')
        status = request.args.get('status')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        signals = _run_async(db.get_recent_signals(symbol=symbol, limit=limit))

        # Filter by status if provided
        if status:
            signals = [s for s in signals if s.get('status') == status]

        return jsonify({
            'success': True,
            'data': _serialize(signals),
            'count': len(signals),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error fetching signals: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/signals/active')
def get_active_signals():
    """Get all active signals with current prices"""
    try:
        signals = _run_async(db.get_active_signals())
        
        # Fetch current prices from Binance for active symbols
        active_symbols = list(set(s['symbol'] for s in signals))
        current_prices = {}
        
        if active_symbols:
            try:
                import requests
                # Use Binance Futures API to get current prices
                binance_url = "https://fapi.binance.com/fapi/v1/ticker/price"
                response = requests.get(binance_url, timeout=5)
                if response.status_code == 200:
                    all_prices = response.json()
                    # Map symbol -> price
                    current_prices = {p['symbol']: float(p['price']) for p in all_prices 
                                     if p['symbol'] in active_symbols}
            except Exception as price_err:
                logger.warning(f"Could not fetch current prices: {price_err}")
        
        # Add current_price to each signal
        for signal in signals:
            symbol = signal['symbol']
            signal['current_price'] = current_prices.get(symbol, None)
            
            # Calculate unrealized PnL if current price available
            if signal['current_price'] and signal.get('entry_price'):
                entry = float(signal['entry_price'])
                current = float(signal['current_price'])
                signal_type = signal.get('signal_type', '').upper()
                
                if 'LONG' in signal_type:
                    pnl_pct = ((current - entry) / entry) * 100
                elif 'SHORT' in signal_type:
                    pnl_pct = ((entry - current) / entry) * 100
                else:
                    pnl_pct = 0
                
                signal['unrealized_pnl'] = round(pnl_pct, 2)
            else:
                signal['unrealized_pnl'] = None
        
        return jsonify({
            'success': True,
            'data': _serialize(signals),
            'count': len(signals),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error fetching active signals: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/signals/near-deadline')
def get_signals_near_deadline():
    """Get signals near their hold deadline"""
    try:
        minutes = request.args.get('minutes', 30, type=int)
        signals = _run_async(db.get_signals_near_deadline(minutes=minutes))
        return jsonify({
            'success': True,
            'data': _serialize(signals),
            'count': len(signals),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error fetching near-deadline signals: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/signals/<int:signal_id>')
def get_signal_detail(signal_id):
    """Get detail of a specific signal"""
    try:
        signals = _run_async(db.get_recent_signals(limit=1000))
        signal = next((s for s in signals if s.get('id') == signal_id), None)
        if not signal:
            return jsonify({'success': False, 'error': 'Signal not found'}), 404
        return jsonify({
            'success': True,
            'data': _serialize(signal),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error fetching signal {signal_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== STATS APIs ====================

@app.route('/api/stats/<symbol>')
def get_stats(symbol):
    """Get statistics for a symbol"""
    try:
        days = request.args.get('days', 30, type=int)
        stats = _run_async(db.get_signal_stats(symbol=symbol, days=days))
        return jsonify({
            'success': True,
            'data': _serialize(stats),
            'symbol': symbol,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error fetching stats for {symbol}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/stats/summary')
def get_stats_summary():
    """Get overall summary statistics"""
    try:
        stats = _run_async(db.get_signal_stats(days=30))
        hold_stats = _run_async(db.get_hold_duration_stats())

        # Aggregate
        total_signals = sum(s.get('total_signals', 0) for s in stats) if stats else 0
        total_wins = sum(s.get('winning_signals', 0) for s in stats) if stats else 0
        total_losses = sum(s.get('losing_signals', 0) for s in stats) if stats else 0
        winrate = (total_wins / total_signals * 100) if total_signals > 0 else 0
        avg_pnl_list = [s.get('avg_pnl', 0) for s in stats if s.get('avg_pnl')]
        avg_pnl = sum(avg_pnl_list) / len(avg_pnl_list) if avg_pnl_list else 0

        return jsonify({
            'success': True,
            'data': {
                'total_signals': total_signals,
                'total_wins': total_wins,
                'total_losses': total_losses,
                'winrate': round(winrate, 2),
                'avg_pnl': round(avg_pnl, 4),
                'by_symbol': _serialize(stats),
                'hold_duration': _serialize(hold_stats)
            },
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error fetching stats summary: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hold-duration/stats')
def get_hold_duration_stats():
    """Get hold duration statistics"""
    try:
        symbol = request.args.get('symbol')
        stats = _run_async(db.get_hold_duration_stats(symbol=symbol))
        return jsonify({
            'success': True,
            'data': _serialize(stats),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error fetching hold duration stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== SYMBOLS API ====================

@app.route('/api/symbols')
def get_symbols():
    """Get monitored symbols"""
    symbols = config.SYMBOLS if isinstance(config.SYMBOLS, list) else ['BTCUSDT', 'ETHUSDT', 'ADAUSDT', 'SOLUSDT', 'DOTUSDT', 'LINKUSDT']
    return jsonify({
        'success': True,
        'data': symbols,
        'timestamp': datetime.now().isoformat()
    })


# ==================== PERFORMANCE CHART DATA ====================

@app.route('/api/performance/daily')
def get_daily_performance():
    """Get daily performance data for charts"""
    try:
        days = request.args.get('days', 30, type=int)
        stats = _run_async(db.get_signal_stats(days=days))

        # Group by date
        by_date = {}
        for s in (stats or []):
            date_str = str(s.get('date', ''))
            if date_str not in by_date:
                by_date[date_str] = {
                    'date': date_str,
                    'total_signals': 0,
                    'winning_signals': 0,
                    'losing_signals': 0,
                    'avg_pnl': 0,
                    'pnl_values': []
                }
            by_date[date_str]['total_signals'] += s.get('total_signals', 0)
            by_date[date_str]['winning_signals'] += s.get('winning_signals', 0)
            by_date[date_str]['losing_signals'] += s.get('losing_signals', 0)
            if s.get('avg_pnl'):
                by_date[date_str]['pnl_values'].append(s['avg_pnl'])

        # Calculate averages
        for d in by_date.values():
            d['avg_pnl'] = sum(d['pnl_values']) / len(d['pnl_values']) if d['pnl_values'] else 0
            del d['pnl_values']
            winrate = (d['winning_signals'] / d['total_signals'] * 100) if d['total_signals'] > 0 else 0
            d['winrate'] = round(winrate, 2)

        sorted_data = sorted(by_date.values(), key=lambda x: x['date'])

        return jsonify({
            'success': True,
            'data': sorted_data,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error fetching daily performance: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== MAIN ====================

if __name__ == '__main__':
    # Trigger lazy init of persistent loop + db pool
    _ensure_loop()
    logger.info("Database initialized for web UI")
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
