# SENTRY_TRADER — Crypto Futures Trading Bot

ระบบ Algorithmic Trading Bot สำหรับ Binance Futures โดยใช้สถาปัตยกรรม Microservices
ที่รองรับ Triple Confirmation Entry, AI Random Forest, และ Dynamic Risk Management

---

## User Review Required

> [!IMPORTANT]
> **Binance API Keys**: ต้องมี API Key จาก Binance (Testnet ก่อน) สำหรับ Phase 1-4
> **Python Version**: ใช้ Python 3.11+ (รองรับ asyncio และ websockets ได้ดี)
> **เงินทุน**: Phase 4 (Paper Trading) ใช้ Testnet ไม่มีความเสี่ยง, Phase 5 ถึงใช้เงินจริง

> [!WARNING]  
> **Futures Trading Risk**: บอทที่รันบน Live ต้องผ่าน Backtesting ≥1 เดือนก่อน
> Phase 5 จะ implement เพิ่มเติมจาก Phase 4 เท่านั้น ไม่ข้ามขั้น

---

## Proposed Changes

### Phase 1 — Infrastructure & Data Engine

#### [NEW] `requirements.txt`
Dependencies หลักของโปรเจค

#### [NEW] `config.py`
ไฟล์ตั้งค่ากลาง: API Keys (จาก .env), Symbol target, Timeframes, Risk parameters

#### [NEW] `.env.example`
Template สำหรับ API keys (ไม่ commit .env จริง)

#### [NEW] `data_engine/__init__.py`
#### [NEW] `data_engine/binance_client.py`
- ต่อ Binance REST API (ดึง historical OHLCV)
- ต่อ Binance WebSocket (Real-time price streaming)
- จัดการ reconnect อัตโนมัติเมื่อ connection drop

#### [NEW] `data_engine/data_store.py`
- SQLite / pandas DataFrame สำหรับเก็บ candle data
- Rolling buffer สำหรับ real-time calculation

#### [NEW] `database/__init__.py`
#### [NEW] `database/models.py`
- Schema: trades, signals, errors, portfolio_snapshots

---

### Phase 2 — AI & Technical Indicators (Brain Center)

#### [NEW] `brain_center/__init__.py`
#### [NEW] `brain_center/indicators.py`
- `calc_rsi(series, period=14)` — RSI calculation
- `calc_ema(series, period=200)` — EMA Trend Filter
- `calc_atr(high, low, close, period=14)` — ATR Volatility Filter
- `calc_volume_ma(volume, period=20)` — Volume MA

#### [NEW] `brain_center/signal_engine.py`
- **Triple Confirmation Logic:**
  1. RSI cross above 30 (Oversold)
  2. Volume > 20-bar MA
  3. AI Confidence > 85%
- EMA 200 Trend Filter (4H): เทรดตามทิศทางเท่านั้น
- ATR Volatility Kill Switch: ATR > 2x normal → หยุดทำงาน

#### [NEW] `brain_center/ai_model.py`
- Random Forest Classifier
- Features: RSI, EMA Slope, ATR, Volume Ratio, Price Action patterns
- เทรนด้วย 1-2 ปีย้อนหลัง
- `train()`, `predict(features)` → confidence score

#### [NEW] `brain_center/backtester.py`
- Backtest engine สำหรับ Phase 2 validation

---

### Phase 3 — Risk Management System

#### [NEW] `risk_manager/__init__.py`
#### [NEW] `risk_manager/position_sizer.py`
- **Fixed Fractional**: ลงทุน 1% ของพอร์ตต่อ 1 ไม้
- คำนวณ Lot Size จาก SL distance

#### [NEW] `risk_manager/stop_loss.py`
- SL = แนวรับล่าสุด หรือ Entry - (2 × ATR)
- TP = Entry + (SL distance × 2) → R:R = 1:2
- Trailing Stop: เมื่อ profit = 1R → เลื่อน SL มา Break Even

#### [NEW] `risk_manager/risk_guard.py`
- Daily Loss Limit: หาก drawdown > 5% ของพอร์ตวันนี้ → หยุดทำงาน
- Max Open Positions: จำกัดจำนวนไม้พร้อมกัน

---

### Phase 4 — Paper Trading (Testnet)

#### [NEW] `executor/__init__.py`
#### [NEW] `executor/order_executor.py`
- ส่ง Order ไปที่ Binance Testnet
- จัดการ Market/Limit orders
- Handle errors (insufficient margin, etc.)

#### [NEW] `executor/portfolio_tracker.py`
- Track open positions, P&L, portfolio value

#### [NEW] `main.py`
- Entry point รวมทุก module
- Async event loop หลัก

---

### Phase 5 — Live Deployment

#### [NEW] `monitoring/__init__.py`
#### [NEW] `monitoring/telegram_bot.py`
- แจ้งเตือนเมื่อเปิด/ปิด Order
- ส่ง Daily P&L Summary
- Emergency Pause command ผ่าน Telegram

#### [NEW] `monitoring/dashboard.py`
- Web dashboard (Flask/FastAPI) แสดงสถิติ

#### [NEW] `Dockerfile`
#### [NEW] `docker-compose.yml`
- Production deployment

---

## Roadmap

| Phase | เป้าหมาย | ระยะเวลาประมาณ |
|:------|:---------|:--------------|
| **Phase 1** | Infrastructure + Binance WebSocket | 1-2 วัน |
| **Phase 2** | Indicators + AI Model + Backtest | 3-5 วัน |
| **Phase 3** | Risk System (SL/TP/Trailing) | 2-3 วัน |
| **Phase 4** | Paper Trading บน Testnet 30 วัน | 1 เดือน |
| **Phase 5** | Live Deployment + Telegram Alert | 2-3 วัน |

---

## Open Questions

> [!IMPORTANT]
> **Symbol เป้าหมาย**: จะเทรด `BTCUSDT` อย่างเดียวก่อน หรือให้รองรับหลาย Symbol?

> [!IMPORTANT]  
> **Timeframe หลัก**: สัญญาณ Entry จาก Timeframe ไหน? (แนะนำ 15m หรือ 1H, ดู Trend จาก 4H)

> [!IMPORTANT]
> **พอร์ตเริ่มต้น (Testnet)**: ตั้ง Initial Balance เท่าไหร่สำหรับ Paper Trading?

> [!NOTE]
> **News Sentiment**: Phase 1 จะ stub ส่วน Sentiment ไว้ก่อน, implement จริงใน Phase 2/3

---

## Verification Plan

### Phase 1 Verification
```bash
# ทดสอบการดึงข้อมูลจาก Binance
python -m data_engine.binance_client --test

# ตรวจสอบ WebSocket stream 60 วินาที
python -m data_engine.binance_client --symbol BTCUSDT --stream
```

### Phase 2 Verification
```bash
# รัน Backtest ดู Win Rate, Sharpe Ratio, Max Drawdown
python -m brain_center.backtester --symbol BTCUSDT --days 365
```

### Phase 4 Verification
- รัน Paper Trading บน Testnet 30 วัน
- เป้าหมาย: Win Rate > 50%, Profit Factor > 1.5, Max Drawdown < 15%
