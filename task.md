# SENTRY_TRADER — Task Tracker

## Phase 1: Infrastructure & Data Engine

- [x] สร้าง Folder Structure ทั้งหมด
- [x] เขียน `requirements.txt`
- [x] เขียน `.env.example`
- [x] เขียน `config.py` (Central config + multi-symbol ready)
- [x] เขียน `data_engine/binance_client.py` (REST + WebSocket)
- [x] เขียน `data_engine/data_store.py` (Candle buffer + DataFrame)
- [x] เขียน `database/models.py` (SQLAlchemy schema)
- [x] เขียน `database/db_manager.py` (CRUD operations)
- [x] เขียน `main.py` (Entry point + async loop)
- [x] ทดสอบ Historical OHLCV fetch — ได้ 500 แท่ง BTCUSDT 15m + 4h
- [x] ทดสอบ Database init — OK

## Phase 2: AI & Indicators (ถัดไป)
- [ ] brain_center/indicators.py
- [ ] brain_center/signal_engine.py
- [ ] brain_center/ai_model.py
- [ ] brain_center/backtester.py

## Phase 3: Risk Management (ถัดไป)
- [ ] risk_manager/position_sizer.py
- [ ] risk_manager/stop_loss.py
- [ ] risk_manager/risk_guard.py

## Phase 4: Paper Trading
- [ ] executor/order_executor.py (Testnet)
- [ ] executor/portfolio_tracker.py

## Phase 5: Live Deployment
- [ ] monitoring/telegram_bot.py
- [ ] Dockerfile + docker-compose.yml
