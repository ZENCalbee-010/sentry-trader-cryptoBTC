"""
SENTRY_TRADER — Central Configuration
=====================================
ไฟล์นี้เป็นแหล่งความจริงเดียว (Single Source of Truth) ของ Parameter ทั้งหมด
แก้ไขที่นี่ที่เดียว มีผลกับทุก Module
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# โหลด .env ก่อนอ่านค่า
load_dotenv()

# ============================================================
# PROJECT PATHS
# ============================================================
BASE_DIR = Path(__file__).parent
DATABASE_DIR = BASE_DIR / "database"
LOGS_DIR = BASE_DIR / "logs"
MODELS_DIR = BASE_DIR / "brain_center" / "models"

# สร้าง directories ที่จำเป็น
for directory in [DATABASE_DIR, LOGS_DIR, MODELS_DIR]:
    directory.mkdir(parents=True, exist_ok=True)


# ============================================================
# BINANCE API SETTINGS
# ============================================================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# Trading Mode: "testnet" หรือ "live"
TRADING_MODE = os.getenv("TRADING_MODE", "testnet").lower()

# Binance Endpoints
BINANCE_ENDPOINTS = {
    "testnet": {
        "rest": "https://testnet.binancefuture.com",
        "websocket": "wss://stream.binancefuture.com/ws",
    },
    "live": {
        "rest": "https://fapi.binance.com",
        "websocket": "wss://fstream.binance.com/ws",
    },
}

ACTIVE_REST_URL = BINANCE_ENDPOINTS[TRADING_MODE]["rest"]
ACTIVE_WS_URL = BINANCE_ENDPOINTS[TRADING_MODE]["websocket"]


# ============================================================
# TRADING SYMBOLS (Multi-symbol Ready)
# ============================================================
# โฟกัสที่ BTCUSDT ก่อน — เพิ่ม Symbol ได้โดยแค่ append list นี้
TRADING_SYMBOLS = ["BTCUSDT"]

# Symbol หลักที่เทรดก่อน
PRIMARY_SYMBOL = "BTCUSDT"


# ============================================================
# TIMEFRAME SETTINGS
# ============================================================
# Entry Signal Timeframe (15 นาที — ได้รอบเยอะ)
ENTRY_TIMEFRAME = "15m"
ENTRY_TIMEFRAME_MS = 15 * 60 * 1000  # milliseconds

# Trend Filter Timeframe (4 ชั่วโมง — ดูทิศทางใหญ่)
TREND_TIMEFRAME = "4h"
TREND_TIMEFRAME_MS = 4 * 60 * 60 * 1000

# จำนวนแท่งเทียนที่เก็บใน Memory Buffer (per symbol, per timeframe)
CANDLE_BUFFER_SIZE = 500  # เพียงพอสำหรับ EMA 200 + indicators


# ============================================================
# TECHNICAL INDICATOR PARAMETERS
# ============================================================
# EMA — Trend Filter (Trend Timeframe: 4H)
EMA_PERIOD = 200

# RSI — Entry Signal (Entry Timeframe: 15m)
RSI_PERIOD = 14
RSI_OVERSOLD = 30       # ตัดขึ้นจาก zone นี้ = สัญญาณ Buy
RSI_OVERBOUGHT = 70     # ตัดลงจาก zone นี้ = สัญญาณ Sell Short

# ATR — Volatility Filter & Stop Loss Calculation
ATR_PERIOD = 14
ATR_MULTIPLIER_SL = 2.0          # SL = Entry - (2 × ATR)
ATR_KILL_SWITCH_MULTIPLIER = 2.0 # ถ้า ATR > 2x ค่าเฉลี่ย → หยุดบอท

# Volume — Confirmation Filter
VOLUME_MA_PERIOD = 20   # ปริมาณต้องสูงกว่าค่าเฉลี่ย 20 แท่ง


# ============================================================
# AI MODEL PARAMETERS
# ============================================================
# Random Forest
RF_N_ESTIMATORS = 200
RF_MAX_DEPTH = 10
RF_MIN_CONFIDENCE = 0.85  # ต้องมั่นใจ > 85% ถึงเข้าออเดอร์

# Training data
TRAIN_DATA_DAYS = 730  # ใช้ข้อมูล 2 ปีย้อนหลัง


# ============================================================
# RISK MANAGEMENT PARAMETERS
# ============================================================
# Fixed Fractional Position Sizing
RISK_PER_TRADE = 0.01   # ลงเสี่ยงแค่ 1% ของพอร์ตต่อ 1 ไม้

# Risk:Reward Ratio
RR_RATIO = 2.0           # TP = SL_distance × 2.0

# Trailing Stop: Dynamic (Option B) — ติดตามราคาต่อด้วย ATR
TRAILING_ACTIVATION_RR = 1.0       # เปิด Trailing เมื่อกำไร = 1R
TRAILING_ATR_MULTIPLIER = 1.0      # SL ใหม่ = highest_price - 1×ATR

# Position Sizing — Binance BTCUSDT Constraints
MIN_LOT       = 0.001   # Minimum lot size (BTC)
LOT_STEP      = 0.001   # Lot step size
MAX_MARGIN_RATIO = 0.30 # Skip trade ถ้า Margin > 30% ของพอร์ต (ป้องกัน Liquidation)

# Daily Loss Limit: หยุดทำงานวันนี้ถ้า drawdown เกิน X%
DAILY_LOSS_LIMIT = 0.05  # 5% ของพอร์ต

# Max simultaneous open positions
MAX_OPEN_POSITIONS = 1   # เริ่มต้นระวัง: เปิดทีละ 1 ไม้เท่านั้น

# Order Execution
ORDER_TYPE = "MARKET"    # Market Order เสมอ (ไม่เสี่ยงตกรถ)


# ============================================================
# PAPER TRADING / TESTNET SETTINGS
# ============================================================
INITIAL_PAPER_BALANCE = 100.0  # USDT
PAPER_LEVERAGE = 5             # Leverage เริ่มต้น (ระวัง! เพิ่มขึ้นได้ภายหลัง)


# ============================================================
# DATABASE
# ============================================================
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite+aiosqlite:///{DATABASE_DIR}/sentry_trader.db"
)


# ============================================================
# LOGGING
# ============================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = LOGS_DIR / "sentry_trader.log"
LOG_ROTATION = "1 day"
LOG_RETENTION = "30 days"


# ============================================================
# TELEGRAM (Phase 5)
# TELEGRAM NOTIFICATIONS
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")    # เว้นว่างไว้ถ้าไม่ต้องการแจ้งเตือน
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# ============================================================
# TRADING CONFIGURATION
# ============================================================
def validate_config() -> bool:
    """ตรวจสอบว่า Config พร้อมใช้งาน"""
    errors = []

    if not BINANCE_API_KEY:
        errors.append("❌ BINANCE_API_KEY ไม่ได้ตั้งค่าใน .env")
    if not BINANCE_API_SECRET:
        errors.append("❌ BINANCE_API_SECRET ไม่ได้ตั้งค่าใน .env")
    if TRADING_MODE not in ("testnet", "live"):
        errors.append(f"❌ TRADING_MODE ไม่ถูกต้อง: {TRADING_MODE}")

    if errors:
        for err in errors:
            print(err)
        return False

    print(f"✅ Config ถูกต้อง | Mode: {TRADING_MODE.upper()} | Symbols: {TRADING_SYMBOLS}")
    return True


if __name__ == "__main__":
    validate_config()
