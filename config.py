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
TOP_N_BY_VOLUME = 20  # If SYMBOLS="ALL", take top N by 24h volume

# Thresholds
# PERUBAHAN 2: disesuaikan — STRONG_THRESHOLD turun, graduated di aggregator
STRONG_THRESHOLD = 3       # (dari 4 — turun agar lebih mudah tercapai dari ±8)
WHALE_THRESHOLD = 200_000  # USD  (dari 500_000 — tiered di whale.py)
LIQUIDATION_THRESHOLD = 100_000  # USD
OB_IMBALANCE_THRESHOLD = 0.15  # (dari 0.30 — graduated di aggregator)
WHALE_WINDOW_MINUTES = 10
LIQUIDATION_WINDOW_MINUTES = 5

# ATR and TP/SL
ATR_PERIOD = 14
ATR_TIMEFRAME = "1h"
SL_MULTIPLIER = 1.5
TP_MULTIPLIER = 3.0
TRAIL_TRIGGER_ATR = 1.0
TRAIL_DISTANCE_ATR = 0.75
MIN_RR_RATIO = 1.2  # (dari 1.5 — lebih banyak sinyal lolos)

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Confirmation Layer
CONFIRMATION_MINUTES = 1   # PERUBAHAN 2: tetap 1 menit
CONFIRMATION_CANDLES = 2   # PERUBAHAN 2: turun dari 3 → cukup 2 candles
SIGNAL_COOLDOWN_MINUTES = 15

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
