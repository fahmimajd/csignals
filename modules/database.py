import asyncio
import asyncpg
from typing import Dict, List, Optional, Any
import config
from datetime import datetime
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

class Database:
    """
    PostgreSQL database handler for storing crypto signals and performance data.
    """
    
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
        
    async def initialize(self):
        """Initialize database connection pool and create tables"""
        try:
            # Use Unix socket for local connections (no password needed)
            if config.DB_HOST in ['localhost', '127.0.0.1']:
                # Connect via Unix socket
                self.pool = await asyncpg.create_pool(
                    host='/var/run/postgresql',
                    port=config.DB_PORT,
                    user=config.DB_USER,
                    password=config.DB_PASSWORD if config.DB_PASSWORD else None,
                    database=config.DB_NAME,
                    min_size=5,
                    max_size=20
                )
            else:
                # TCP connection for remote hosts
                self.pool = await asyncpg.create_pool(
                    host=config.DB_HOST,
                    port=config.DB_PORT,
                    user=config.DB_USER,
                    password=config.DB_PASSWORD,
                    database=config.DB_NAME,
                    min_size=5,
                    max_size=20
                )
            
            await self._create_tables()
            logger.info("Database initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise
    
    async def _create_tables(self):
        """Create database tables if they don't exist"""
        async with self.pool.acquire() as conn:
            # Signals table to store all trading signals
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS signals (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    signal_type VARCHAR(20) NOT NULL,  -- STRONG_LONG, STRONG_SHORT, etc.
                    score INTEGER NOT NULL,
                    entry_price DECIMAL(20, 8) NOT NULL,
                    stop_loss DECIMAL(20, 8) NOT NULL,
                    take_profit DECIMAL(20, 8) NOT NULL,
                    atr_value DECIMAL(20, 8) NOT NULL,
                    rr_ratio DECIMAL(10, 4) NOT NULL,
                    tp_source VARCHAR(50),  -- Source of TP calculation (Liq. Zone, ATR, etc.)
                    trail_start DECIMAL(20, 8),
                    trail_stop DECIMAL(20, 8),
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    confirmed_at TIMESTAMP,
                    exit_price DECIMAL(20, 8),
                    exit_time TIMESTAMP,
                    pnl_percent DECIMAL(10, 4),
                    status VARCHAR(20) DEFAULT 'ACTIVE',  -- ACTIVE, CLOSED_WIN, CLOSED_LOSS, CANCELLED, EXPIRED
                    
                    -- Hold duration columns
                    hold_hours DECIMAL(6,2),
                    hold_deadline TIMESTAMPTZ,
                    atr_factor DECIMAL(5,3),
                    score_factor DECIMAL(5,3),
                    volume_factor DECIMAL(5,3),
                    volume_score SMALLINT,
                    extended BOOLEAN DEFAULT FALSE,
                    extension_hours DECIMAL(6,2),
                    expired_price DECIMAL(20,8),
                    expired_pnl_pct DECIMAL(8,4),
                    
                    -- PATCH: Dedup key for preventing duplicate signals
                    dedup_key VARCHAR(200)
                )
            ''')
            
            # PATCH: Add dedup_key unique constraint if not exists
            try:
                await conn.execute('''
                    ALTER TABLE signals ADD COLUMN IF NOT EXISTS dedup_key VARCHAR(200)
                ''')
            except Exception:
                pass  # Column may already exist
            
            # Create indexes for better query performance
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_signals_symbol_timestamp 
                ON signals(symbol, timestamp DESC)
            ''')
            
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_signals_status 
                ON signals(status)
            ''')
            
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_signals_signal_type 
                ON signals(signal_type)
            ''')
            
            # PATCH: Index for active signals per symbol (conflict check)
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_signals_active_symbol
                ON signals(symbol, status) WHERE status = 'ACTIVE'
            ''')
            
            # PATCH: Index for dedup_key
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_signals_dedup
                ON signals(dedup_key) WHERE dedup_key IS NOT NULL
            ''')
            
            # Signal statistics table for quick aggregations
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS signal_stats (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    date DATE NOT NULL,
                    total_signals INTEGER DEFAULT 0,
                    winning_signals INTEGER DEFAULT 0,
                    losing_signals INTEGER DEFAULT 0,
                    winrate DECIMAL(5, 2) DEFAULT 0.00,
                    avg_pnl DECIMAL(10, 4) DEFAULT 0.00,
                    UNIQUE(symbol, date)
                )
            ''')
            
            logger.info("Database tables created/verified")

            # Migrate: add hold duration columns to existing tables
            await self.add_hold_duration_columns()
    
    async def save_signal(self, symbol: str, signal_data: Dict[str, Any]) -> int:
        """
        Save a new signal to the database.
        
        Args:
            symbol: Trading pair symbol (e.g., BTCUSDT)
            signal_data: Dictionary containing signal information
            
        Returns:
            ID of the inserted signal
        """
        try:
            async with self.pool.acquire() as conn:
                # Insert signal record
                query = '''
                    INSERT INTO signals (
                        symbol, signal_type, score, entry_price, stop_loss, 
                        take_profit, atr_value, rr_ratio, tp_source, 
                        trail_start, trail_stop, confirmed_at, status
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
                    ) RETURNING id
                '''
                
                signal_id = await conn.fetchval(
                    query,
                    symbol,
                    signal_data.get('signal_type'),
                    signal_data.get('score'),
                    signal_data.get('entry_price'),
                    signal_data.get('stop_loss'),
                    signal_data.get('take_profit'),
                    signal_data.get('atr_value'),
                    signal_data.get('rr_ratio'),
                    signal_data.get('tp_source'),
                    signal_data.get('trail_start'),
                    signal_data.get('trail_stop'),
                    signal_data.get('confirmed_at', datetime.now()),
                    'ACTIVE'
                )
                
                logger.info(f"Saved signal {signal_id} for {symbol}")
                return signal_id
                
        except Exception as e:
            logger.error(f"Error saving signal for {symbol}: {e}")
            raise
    
    async def update_signal_exit(self, signal_id: int, exit_price: Decimal, 
                               pnl_percent: Decimal, status: str):
        """
        Update a signal with exit information.
        
        Args:
            signal_id: ID of the signal to update
            exit_price: Price at which the signal exited
            pnl_percent: Profit/loss percentage
            status: Final status (CLOSED_WIN, CLOSED_LOSS, etc.)
        """
        try:
            async with self.pool.acquire() as conn:
                query = '''
                    UPDATE signals 
                    SET exit_price = $1, 
                        exit_time = CURRENT_TIMESTAMP,
                        pnl_percent = $2,
                        status = $3
                    WHERE id = $4
                '''
                
                await conn.execute(query, exit_price, pnl_percent, status, signal_id)
                logger.info(f"Updated signal {signal_id} with exit info")
                
        except Exception as e:
            logger.error(f"Error updating signal {signal_id}: {e}")
            raise
    
    async def get_recent_signals(self, symbol: Optional[str] = None, 
                               limit: int = 50) -> List[Dict]:
        """
        Get recent signals from the database.
        
        Args:
            symbol: Optional symbol filter
            limit: Maximum number of signals to return
            
        Returns:
            List of signal dictionaries
        """
        try:
            async with self.pool.acquire() as conn:
                if symbol:
                    query = '''
                        SELECT * FROM signals 
                        WHERE symbol = $1 
                        ORDER BY timestamp DESC 
                        LIMIT $2
                    '''
                    rows = await conn.fetch(query, symbol, limit)
                else:
                    query = '''
                        SELECT * FROM signals 
                        ORDER BY timestamp DESC 
                        LIMIT $1
                    '''
                    rows = await conn.fetch(query, limit)
                
                # Convert to list of dictionaries
                signals = []
                for row in rows:
                    signal = dict(row)
                    # Convert Decimal to float for easier handling
                    for key, value in signal.items():
                        if isinstance(value, Decimal):
                            signal[key] = float(value)
                    signals.append(signal)
                
                return signals
                
        except Exception as e:
            logger.error(f"Error getting recent signals: {e}")
            return []
    
    async def get_signal_stats(self, symbol: Optional[str] = None, 
                             days: int = 30) -> List[Dict]:
        """
        Get signal statistics for performance analysis.
        
        Args:
            symbol: Optional symbol filter
            days: Number of days to look back
            
        Returns:
            List of statistics dictionaries
        """
        try:
            async with self.pool.acquire() as conn:
                if symbol:
                    query = '''
                        SELECT * FROM signal_stats 
                        WHERE symbol = $1 
                        AND date >= CURRENT_DATE - INTERVAL '%s days'
                        ORDER BY date DESC
                    ''' % days
                    rows = await conn.fetch(query, symbol)
                else:
                    query = '''
                        SELECT * FROM signal_stats 
                        WHERE date >= CURRENT_DATE - INTERVAL '%s days'
                        ORDER BY symbol, date DESC
                    ''' % days
                    rows = await conn.fetch(query)
                
                # Convert to list of dictionaries
                stats = []
                for row in rows:
                    stat = dict(row)
                    # Convert Decimal to float
                    for key, value in stat.items():
                        if isinstance(value, Decimal):
                            stat[key] = float(value)
                    stats.append(stat)
                
                return stats
                
        except Exception as e:
            logger.error(f"Error getting signal stats: {e}")
            return []
    
    async def update_daily_stats(self, symbol: str, date: datetime):
        """
        Update daily statistics for a symbol.
        
        Args:
            symbol: Trading pair symbol
            date: Date for which to update stats
        """
        try:
            async with self.pool.acquire() as conn:
                # Calculate stats from signals table
                query = '''
                    SELECT 
                        COUNT(*) as total_signals,
                        COUNT(CASE WHEN status = 'CLOSED_WIN' THEN 1 END) as winning_signals,
                        COUNT(CASE WHEN status = 'CLOSED_LOSS' THEN 1 END) as losing_signals,
                        AVG(CASE WHEN pnl_percent IS NOT NULL THEN pnl_percent END) as avg_pnl
                    FROM signals 
                    WHERE symbol = $1 
                    AND DATE(timestamp) = $2
                '''
                
                row = await conn.fetchrow(query, symbol, date.date())
                
                total = row['total_signals'] or 0
                winning = row['winning_signals'] or 0
                losing = row['losing_signals'] or 0
                avg_pnl = row['avg_pnl'] or 0.0
                winrate = (winning / total * 100) if total > 0 else 0.0
                
                # Upsert into signal_stats table
                upsert_query = '''
                    INSERT INTO signal_stats (
                        symbol, date, total_signals, winning_signals, 
                        losing_signals, winrate, avg_pnl
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7
                    )
                    ON CONFLICT (symbol, date) 
                    DO UPDATE SET
                        total_signals = EXCLUDED.total_signals,
                        winning_signals = EXCLUDED.winning_signals,
                        losing_signals = EXCLUDED.losing_signals,
                        winrate = EXCLUDED.winrate,
                        avg_pnl = EXCLUDED.avg_pnl
                '''
                
                await conn.execute(
                    upsert_query,
                    symbol,
                    date.date(),
                    total,
                    winning,
                    losing,
                    winrate,
                    avg_pnl
                )
                
                logger.debug(f"Updated daily stats for {symbol} on {date.date()}")
                
        except Exception as e:
            logger.error(f"Error updating daily stats for {symbol}: {e}")
    
    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("Database connection pool closed")

    # ==================== HOLD DURATION FUNCTIONS ====================

    async def save_hold_duration(
        self,
        signal_id: int,
        hold_hours: float,
        hold_deadline: datetime,
        atr_factor: float,
        score_factor: float,
        volume_factor: float,
        volume_score: int
    ):
        """Save hold duration calculation after signal is locked."""
        try:
            async with self.pool.acquire() as conn:
                query = '''
                    UPDATE signals
                    SET hold_hours = $1,
                        hold_deadline = $2,
                        atr_factor = $3,
                        score_factor = $4,
                        volume_factor = $5,
                        volume_score = $6
                    WHERE id = $7
                '''
                await conn.execute(
                    query,
                    hold_hours,
                    hold_deadline,
                    atr_factor,
                    score_factor,
                    volume_factor,
                    volume_score,
                    signal_id
                )
                logger.info(f"Saved hold duration for signal {signal_id}: {hold_hours}h")
        except Exception as e:
            logger.error(f"Error saving hold duration for signal {signal_id}: {e}")
            raise

    async def extend_signal(self, signal_id: int, new_deadline: datetime, extension_hours: float):
        """Extend signal deadline and mark as extended."""
        try:
            async with self.pool.acquire() as conn:
                query = '''
                    UPDATE signals
                    SET hold_deadline = $1,
                        extended = TRUE,
                        extension_hours = $2
                    WHERE id = $3
                '''
                await conn.execute(query, new_deadline, extension_hours, signal_id)
                logger.info(f"Extended signal {signal_id} by {extension_hours}h")
        except Exception as e:
            logger.error(f"Error extending signal {signal_id}: {e}")
            raise

    async def expire_signal(self, signal_id: int, expired_price: float, expired_pnl_pct: float):
        """Mark signal as expired with price and PnL at expiration."""
        try:
            async with self.pool.acquire() as conn:
                query = '''
                    UPDATE signals
                    SET status = 'EXPIRED',
                        expired_price = $1,
                        expired_pnl_pct = $2,
                        exit_price = $1,
                        exit_time = CURRENT_TIMESTAMP
                    WHERE id = $3
                '''
                await conn.execute(query, expired_price, expired_pnl_pct, signal_id)
                logger.info(f"Expired signal {signal_id} at price {expired_price}")
        except Exception as e:
            logger.error(f"Error expiring signal {signal_id}: {e}")
            raise

    async def get_signals_near_deadline(self, minutes: int = 5) -> List[Dict]:
        """Get signals with deadline within N minutes (for warning display)."""
        try:
            async with self.pool.acquire() as conn:
                query = '''
                    SELECT * FROM signals
                    WHERE status = 'ACTIVE'
                    AND hold_deadline IS NOT NULL
                    AND hold_deadline <= NOW() + INTERVAL '%s minutes'
                    AND hold_deadline > NOW()
                    ORDER BY hold_deadline ASC
                ''' % minutes
                rows = await conn.fetch(query)

                signals = []
                for row in rows:
                    signal = dict(row)
                    for key, value in signal.items():
                        if isinstance(value, Decimal):
                            signal[key] = float(value)
                    signals.append(signal)
                return signals
        except Exception as e:
            logger.error(f"Error getting signals near deadline: {e}")
            return []

    async def get_active_signals(self) -> List[Dict]:
        """Get all active signals from database."""
        try:
            async with self.pool.acquire() as conn:
                query = '''
                    SELECT * FROM signals
                    WHERE status = 'ACTIVE'
                    ORDER BY timestamp DESC
                '''
                rows = await conn.fetch(query)

                signals = []
                for row in rows:
                    signal = dict(row)
                    for key, value in signal.items():
                        if isinstance(value, Decimal):
                            signal[key] = float(value)
                    signals.append(signal)
                return signals
        except Exception as e:
            logger.error(f"Error getting active signals: {e}")
            return []

    async def get_hold_duration_stats(self, symbol: str = None) -> Dict:
        """Get hold duration statistics for evaluation."""
        try:
            async with self.pool.acquire() as conn:
                if symbol:
                    query = '''
                        SELECT
                            symbol,
                            ROUND(AVG(hold_hours), 2) as avg_hold_hours,
                            ROUND(AVG(EXTRACT(EPOCH FROM
                                (COALESCE(exit_time, hold_deadline) - confirmed_at))
                                / 3600), 2) as avg_actual_hours,
                            COUNT(*) FILTER (WHERE extended = TRUE) as extended_count,
                            COUNT(*) FILTER (WHERE status = 'EXPIRED') as expired_count,
                            ROUND(AVG(expired_pnl_pct)
                                FILTER (WHERE status = 'EXPIRED'), 2) as avg_expired_pnl,
                            COUNT(*) as total
                        FROM signals
                        WHERE symbol = $1 AND hold_hours IS NOT NULL
                        GROUP BY symbol
                    '''
                    rows = await conn.fetch(query, symbol)
                else:
                    query = '''
                        SELECT
                            symbol,
                            ROUND(AVG(hold_hours), 2) as avg_hold_hours,
                            ROUND(AVG(EXTRACT(EPOCH FROM
                                (COALESCE(exit_time, hold_deadline) - confirmed_at))
                                / 3600), 2) as avg_actual_hours,
                            COUNT(*) FILTER (WHERE extended = TRUE) as extended_count,
                            COUNT(*) FILTER (WHERE status = 'EXPIRED') as expired_count,
                            ROUND(AVG(expired_pnl_pct)
                                FILTER (WHERE status = 'EXPIRED'), 2) as avg_expired_pnl,
                            COUNT(*) as total
                        FROM signals
                        WHERE hold_hours IS NOT NULL
                        GROUP BY symbol
                    '''
                    rows = await conn.fetch(query)

                stats = []
                for row in rows:
                    stat = dict(row)
                    for key, value in stat.items():
                        if isinstance(value, Decimal):
                            stat[key] = float(value)
                    stats.append(stat)
                return stats
        except Exception as e:
            logger.error(f"Error getting hold duration stats: {e}")
            return []

    async def add_hold_duration_columns(self):
        """Add hold duration columns to existing table (migration helper)."""
        columns = [
            ('hold_hours', 'DECIMAL(6,2)'),
            ('hold_deadline', 'TIMESTAMPTZ'),
            ('atr_factor', 'DECIMAL(5,3)'),
            ('score_factor', 'DECIMAL(5,3)'),
            ('volume_factor', 'DECIMAL(5,3)'),
            ('volume_score', 'SMALLINT'),
            ('extended', 'BOOLEAN DEFAULT FALSE'),
            ('extension_hours', 'DECIMAL(6,2)'),
            ('expired_price', 'DECIMAL(20,8)'),
            ('expired_pnl_pct', 'DECIMAL(8,4)'),
        ]
        async with self.pool.acquire() as conn:
            for col_name, col_type in columns:
                try:
                    await conn.execute(f'''
                        ALTER TABLE signals
                        ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                    ''')
                    logger.info(f"Added column {col_name} if not exists")
                except Exception as e:
                    logger.warning(f"Column {col_name} may already exist: {e}")
