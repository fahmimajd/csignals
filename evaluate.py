import asyncio
import argparse
import sys
from datetime import datetime, timedelta
import config
from modules.database import Database


async def list_signals(db: Database, symbol: str = None, limit: int = 20):
    """List recent signals"""
    signals = await db.get_recent_signals(symbol=symbol, limit=limit)
    
    if not signals:
        print("No signals found.")
        return
    
    print(f"\n{'='*100}")
    print(f"{'ID':<6} {'SYMBOL':<12} {'TYPE':<15} {'SCORE':<8} {'ENTRY':<15} {'SL':<15} {'TP':<15} {'STATUS':<12}")
    print(f"{'='*100}")
    
    for s in signals:
        print(f"{s['id']:<6} {s['symbol']:<12} {s['signal_type']:<15} {s['score']:<8} "
              f"{s['entry_price']:<15.2f} {s['stop_loss']:<15.2f} {s['take_profit']:<15.2f} {s['status']:<12}")
    
    print(f"{'='*100}\n")


async def show_stats(db: Database, symbol: str = None, days: int = 30):
    """Show signal statistics"""
    signals = await db.get_recent_signals(symbol=symbol, limit=1000)
    
    if not signals:
        print("No signals found.")
        return
    
    # Filter by days
    cutoff = datetime.now() - timedelta(days=days)
    signals = [s for s in signals if s['timestamp'] >= cutoff]
    
    # Calculate stats
    total = len(signals)
    closed = [s for s in signals if s['status'] in ['CLOSED_WIN', 'CLOSED_LOSS']]
    wins = [s for s in signals if s['status'] == 'CLOSED_WIN']
    losses = [s for s in signals if s['status'] == 'CLOSED_LOSS']
    active = [s for s in signals if s['status'] == 'ACTIVE']
    
    winrate = (len(wins) / len(closed) * 100) if closed else 0
    avg_pnl = sum(s['pnl_percent'] for s in closed) / len(closed) if closed else 0
    
    # By symbol
    symbols = {}
    for s in signals:
        sym = s['symbol']
        if sym not in symbols:
            symbols[sym] = {'total': 0, 'wins': 0, 'losses': 0, 'active': 0}
        symbols[sym]['total'] += 1
        if s['status'] == 'CLOSED_WIN':
            symbols[sym]['wins'] += 1
        elif s['status'] == 'CLOSED_LOSS':
            symbols[sym]['losses'] += 1
        elif s['status'] == 'ACTIVE':
            symbols[sym]['active'] += 1
    
    print(f"\n{'='*60}")
    print(f" SIGNAL PERFORMANCE STATISTICS (Last {days} days)")
    print(f"{'='*60}")
    print(f"Total Signals    : {total}")
    print(f"Active           : {len(active)}")
    print(f"Closed           : {len(closed)}")
    print(f"  - Wins         : {len(wins)}")
    print(f"  - Losses       : {len(losses)}")
    print(f"Win Rate         : {winrate:.2f}%")
    print(f"Avg PnL          : {avg_pnl:.2f}%")
    print(f"{'='*60}")
    print(f"\nPer Symbol:")
    print(f"{'SYMBOL':<15} {'TOTAL':<10} {'WINS':<10} {'LOSSES':<10} {'ACTIVE':<10}")
    print(f"{'-'*55}")
    for sym, data in sorted(symbols.items()):
        sym_winrate = (data['wins'] / (data['wins'] + data['losses']) * 100) if (data['wins'] + data['losses']) > 0 else 0
        print(f"{sym:<15} {data['total']:<10} {data['wins']:<10} {data['losses']:<10} {data['active']:<10} (Winrate: {sym_winrate:.1f}%)")
    print(f"{'='*60}\n")


async def analyze_rr(db: Database, symbol: str = None):
    """Analyze risk-reward ratio performance"""
    signals = await db.get_recent_signals(symbol=symbol, limit=100)
    closed = [s for s in signals if s['status'] in ['CLOSED_WIN', 'CLOSED_LOSS']]
    
    if not closed:
        print("No closed signals to analyze.")
        return
    
    print(f"\n{'='*60}")
    print(f" RISK-REWARD ANALYSIS")
    print(f"{'='*60}")
    
    rr_ranges = {
        '1.0-1.5': [s for s in closed if 1.0 <= s['rr_ratio'] < 1.5],
        '1.5-2.0': [s for s in closed if 1.5 <= s['rr_ratio'] < 2.0],
        '2.0-2.5': [s for s in closed if 2.0 <= s['rr_ratio'] < 2.5],
        '2.5-3.0': [s for s in closed if 2.5 <= s['rr_ratio'] < 3.0],
        '3.0+': [s for s in closed if s['rr_ratio'] >= 3.0],
    }
    
    for rr_range, signals_list in rr_ranges.items():
        if signals_list:
            wins = len([s for s in signals_list if s['status'] == 'CLOSED_WIN'])
            losses = len([s for s in signals_list if s['status'] == 'CLOSED_LOSS'])
            total = len(signals_list)
            winrate = (wins / total * 100) if total > 0 else 0
            print(f"R:R {rr_range:<10}: {total:<5} signals, Winrate: {winrate:.1f}% (W:{wins}, L:{losses})")
    
    print(f"{'='*60}\n")


async def analyze_hold_duration(db: Database, symbol: str = None):
    """Analyze hold duration performance"""
    stats = await db.get_hold_duration_stats(symbol=symbol)

    if not stats:
        print("No hold duration data found.")
        return

    print(f"\n{'='*60}")
    print(f" HOLD DURATION ANALYSIS")
    print(f"{'='*60}")

    for stat in stats:
        sym = stat.get('symbol', 'ALL')
        avg_hold = stat.get('avg_hold_hours', 0)
        avg_actual = stat.get('avg_actual_hours', 0)
        extended_count = stat.get('extended_count', 0)
        expired_count = stat.get('expired_count', 0)
        avg_expired_pnl = stat.get('avg_expired_pnl', 0)
        total = stat.get('total', 0)

        extended_pct = (extended_count / total * 100) if total > 0 else 0
        expired_pct = (expired_count / total * 100) if total > 0 else 0

        print(f"\n[{sym}]")
        print(f"  Total signals     : {total}")
        print(f"  Avg hold          : {avg_hold:.2f} jam")
        print(f"  Avg actual        : {avg_actual:.2f} jam")
        print(f"  Extended          : {extended_count} ({extended_pct:.1f}%)")
        print(f"  Expired           : {expired_count} ({expired_pct:.1f}%)")
        print(f"  Avg expired PnL   : {avg_expired_pnl:+.2f}%")

    print(f"\n{'='*60}\n")


async def main():
    parser = argparse.ArgumentParser(description='Crypto Signal Evaluator CLI')
    parser.add_argument('--list', action='store_true', help='List recent signals')
    parser.add_argument('--stats', action='store_true', help='Show signal statistics')
    parser.add_argument('--rr', action='store_true', help='Analyze risk-reward performance')
    parser.add_argument('--hold', action='store_true', help='Analyze hold duration performance')
    parser.add_argument('--symbol', type=str, default=None, help='Filter by symbol')
    parser.add_argument('--days', type=int, default=30, help='Number of days to analyze (default: 30)')
    parser.add_argument('--limit', type=int, default=20, help='Number of signals to list (default: 20)')

    args = parser.parse_args()

    # Initialize database
    db = Database()
    await db.initialize()

    try:
        if args.list:
            await list_signals(db, symbol=args.symbol, limit=args.limit)
        elif args.rr:
            await analyze_rr(db, symbol=args.symbol)
        elif args.hold:
            await analyze_hold_duration(db, symbol=args.symbol)
        elif args.stats:
            await show_stats(db, symbol=args.symbol, days=args.days)
        else:
            parser.print_help()
    finally:
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())