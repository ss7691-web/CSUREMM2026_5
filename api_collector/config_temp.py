import os

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
WS       = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"

key_id           = ""
private_key_path = ""

SCAN_INTERVAL = 300
SECTOR_STAGGER = 5
EMPTY_REMOVAL_THRESHOLD = 3
SECTORS = []
WATCHLIST_TICKERS = []
WATCHLIST_SERIES  = []
WATCHLIST_POLL_INTERVAL = 60

REST_MIN_INTERVAL = 0.1
REST_MAX_RETRIES  = 5
REST_BASE_BACKOFF = 0.5

WS_BACKOFF_CEILING = 60

DB_PATH = "kalshi.duckdb"
PARQUET_DIR = "data/lake"

LOG_PATH = "logs/kalshi.log"
LOG_LEVEL = "INFO"
LOG_MAX_BYTES = 10_000_000
LOG_BACKUPS = 5

def validate():
    errors = []
    if not key_id:
        errors.append("key_id is empty")
    if not private_key_path or not os.path.exists(private_key_path):
        errors.append(f"private_key_path not found: {private_key_path!r}")
    if not isinstance(SECTORS, list):
        errors.append("SECTORS must be a list")
    if errors:
        raise ValueError("config invalid:\n  - " + "\n  - ".join(errors))
    return True
