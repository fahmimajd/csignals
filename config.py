import os
from dotenv import load_dotenv

load_dotenv()

# Binance API
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "False").lower() == "true"

# Symbols to monitor
# Options:
# - "ALL" - Monitor all USDT futures
# - List of symbols: ["BTCUSDT", "ETHUSDT", ...]
# - Pattern: "USDT" will match all symbols ending with USDT
SYMBOLS = "ALL"
SYMBOL_PATTERN = "USDT"  # Filter symbols containing this string
TOP_N_BY_VOLUME = 15  # If SYMBOLS="ALL", take top N by 24h volume (focused on liquid pairs)

# Thresholds
# PERUBAHAN 2: disesuaikan — STRONG_THRESHOLD turun, graduated di aggregator
STRONG_THRESHOLD = 4       # INCREASED from 3 to reduce false positives
WHALE_THRESHOLD = 200_000  # USD  (dari 500_000 — tiered di whale.py)
LIQUIDATION_THRESHOLD = 100_000  # USD
OB_IMBALANCE_THRESHOLD = 0.15  # (dari 0.30 — graduated di aggregator)
WHALE_WINDOW_MINUTES = 10
LIQUIDATION_WINDOW_MINUTES = 5

# ATR and TP/SL
ATR_PERIOD = 14
ATR_TIMEFRAME = "1h"
SL_MULTIPLIER = 1.2        # IMPROVED: Slightly wider SL (from 1.0) to avoid noise
TP_MULTIPLIER = 3.0        # IMPROVED: Higher TP for better R:R (from 2.5)
TRAIL_TRIGGER_ATR = 1.0
TRAIL_DISTANCE_ATR = 0.75
MIN_RR_RATIO = 1.5         # IMPROVED: Higher minimum R:R (from 1.2)
MAX_SL_PERCENT = 0.08      # IMPROVED: Tighter max SL (from 10%)

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Confirmation Layer
CONFIRMATION_MINUTES = 1   # Tetap 1 menit
CONFIRMATION_CANDLES = 4   # INCREASED from 3 to reduce false positives
SIGNAL_COOLDOWN_MINUTES = 45  # INCREASED from 30 for better signal quality

# Hold Duration Parameters
BASE_HOURS = 6           # Titik tengah durasi hold (jam)
MIN_HOLD_HOURS = 2       # Minimum durasi hold (jam)
MAX_HOLD_HOURS = 12      # Maksimum durasi hold (jam)
EXTENSION_RATIO = 0.5    # Perpanjangan = 50% dari durasi awal
MAX_EXTENSIONS = 1       # Maksimal 1x perpanjangan per sinyal

# Database
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "crypto_signals")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Display
UPDATE_INTERVAL = 10  # seconds
LOG_FILE = "logs/signals.log"

# Monte Carlo Filter
MC_N_SIMULATIONS = 1000  # Number of simulation runs
MC_MIN_PROB_TP = 40.0    # Minimum TP probability (%) to not skip signal
MC_HIGH_CONF = 65.0      # TP probability (%) for HIGH confidence

# Technical Indicators (RSI, MACD, Stochastic)
USE_TECHNICAL_INDICATORS = True   # Set to True to enable RSI/MACD/Stochastic confirmation
TECH_RSI_PERIOD = 14
TECH_MACD_FAST = 12
TECH_MACD_SLOW = 26
TECH_MACD_SIGNAL = 9
TECH_STOCH_K = 14
TECH_STOCH_D = 3
