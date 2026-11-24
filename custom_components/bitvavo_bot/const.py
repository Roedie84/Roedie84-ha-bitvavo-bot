"""Constanten voor Bitvavo Bot."""

DOMAIN = "bitvavo_bot"
VERSION = "0.6.0"

API_BASE = "https://api.bitvavo.com/v2"
CANDLE_INTERVAL = "1h"
CANDLE_MS = 3_600_000
CANDLE_LIMIT = 1440

CONF_MARKET_COUNT = "market_count"
CONF_HISTORY_DAYS = "history_days"
CONF_MARKETS = "markets"

DEFAULT_MARKET_COUNT = 10
DEFAULT_HISTORY_DAYS = 1460
MIN_HISTORY_DAYS = 1460  # preregistratie v2 toetst op 4 jaar

# Geen stablecoins/fiat-achtigen in het universum: daar valt niets te verdienen.
EXCLUDED_BASES = frozenset(
    {
        "USDC", "USDT", "EURC", "DAI", "TUSD", "USDP", "PYUSD", "FDUSD",
        "EUROP", "EURCV", "USDCV", "USDS", "USDE", "PAXG", "EURT", "EURS",
    }
)

DATA_DIR = "bitvavo_bot"
SIGNAL_UPDATE = f"{DOMAIN}_update"

SERVICE_UPDATE_DATA = "update_data"
SERVICE_RUN_LAB = "run_lab"

STATUS_IDLE = "gereed"
STATUS_DOWNLOADING = "downloaden"
STATUS_ERROR = "fout"

VERDICT_NONE = "nog niet gedraaid"
VERDICT_RUNNING = "bezig"
VERDICT_NO_EDGE = "geen edge"
VERDICT_CANDIDATE = "kandidaat"
