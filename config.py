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
STRONG_THRESHOLD = 3       # (dari 4 — turun agar lebih mudah tercapai dari ±8)
WHALE_THRESHOLD = 200_000  # USD  (dari 500_000 — tiered di whale.py)
LIQUIDATION_THRESHOLD = 100_000  # USD
OB_IMBALANCE_THRESHOLD = 0.15  # (dari 0.30 — graduated di aggregator)
WHALE_WINDOW_MINUTES = 10
LIQUIDATION_WINDOW_MINUTES = 5

# ATR and TP/SL
ATR_PERIOD = 14
ATR_TIMEFRAME = "1h"
SL_MULTIPLIER = 1.0        # Tighter SL: 1x ATR (dari 1.5x — kurangi loss per trade)
TP_MULTIPLIER = 2.5        # Slightly reduced TP for higher hit rate (dari 3.0x)
TRAIL_TRIGGER_ATR = 1.0
TRAIL_DISTANCE_ATR = 0.75
MIN_RR_RATIO = 1.2  # (dari 1.5 — lebih banyak sinyal lolos)
MAX_SL_PERCENT = 0.10  # Maximum SL distance (10% of entry price)

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Confirmation Layer
CONFIRMATION_MINUTES = 1   # PERUBAHAN 2: tetap 1 menit
CONFIRMATION_CANDLES = 3   # Kembali ke 3 — mengurangi false positives
SIGNAL_COOLDOWN_MINUTES = 30  # Lebih lama (dari 15) — hindari whipsaw

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
