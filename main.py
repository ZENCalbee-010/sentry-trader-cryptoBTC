"""
SENTRY_TRADER — Main Entry Point
==================================
จุดเริ่มต้นของบอท — รวมทุก Module เข้าด้วยกัน

Flow:
  1. Config validation
  2. Database init
  3. Load historical data → DataStore
  4. เปิด WebSocket stream
  5. ทุกครั้งที่แท่งเทียนปิด → Brain Center คำนวณ → ถ้ามีสัญญาณ → Risk Manager → Executor

Run:
  python main.py
"""

import asyncio
import signal as os_signal
from loguru import logger

import config
from data_engine.binance_client import BinanceClient
from data_engine.data_store import DataStore
from database.db_manager import DatabaseManager
from brain_center.signal_engine import SignalEngine
from risk_manager.position_sizer import PositionSizer
from risk_manager.risk_guard import RiskGuard
from risk_manager.stop_loss import StopLossManager
from executor.order_executor import OrderExecutor
from executor.portfolio_tracker import PortfolioTracker
from monitoring.telegram_bot import TelegramNotifier
from database.models import Trade
from sqlalchemy import select


# ============================================================
# Logging Setup
# ============================================================
logger.remove()
logger.add(
    lambda msg: print(msg, end=""),
    level=config.LOG_LEVEL,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    colorize=True,
)
logger.add(
    config.LOG_FILE,
    level="DEBUG",
    rotation=config.LOG_ROTATION,
    retention=config.LOG_RETENTION,
    encoding="utf-8",
)


# ============================================================
# Main Bot Class
# ============================================================
class SentryTrader:
    """
    SENTRY_TRADER — Main Controller

    ทำหน้าที่ Orchestrate ทุก Module:
      - BinanceClient   : ดึงข้อมูล
      - DataStore       : เก็บแท่งเทียน
      - DatabaseManager : บันทึก Log
      - SignalEngine    : Triple Confirmation Brain Center
      [Phase 3 จะเพิ่ม Risk Manager]
      [Phase 4 จะเพิ่ม Executor]
    """

    def __init__(self):
        self.client        = BinanceClient()
        self.store         = DataStore()
        self.db            = DatabaseManager()
        self.signal_engine = SignalEngine()
        self.position_sizer = PositionSizer()
        self.risk_guard    = RiskGuard()
        self.stop_loss_mgr = StopLossManager()
        self.executor      = OrderExecutor(self.client, self.db)
        self.tracker       = PortfolioTracker(self.client, self.db)
        self.notifier      = TelegramNotifier()
        self._running      = False

    async def startup(self):
        """เริ่มต้นระบบ"""
        logger.info("=" * 60)
        logger.info("  🚀 SENTRY_TRADER กำลังเริ่มต้น...")
        logger.info(f"  Mode    : {config.TRADING_MODE.upper()}")
        logger.info(f"  Symbols : {config.TRADING_SYMBOLS}")
        logger.info(f"  Entry TF: {config.ENTRY_TIMEFRAME}  |  Trend TF: {config.TREND_TIMEFRAME}")
        logger.info(f"  Balance : {config.INITIAL_PAPER_BALANCE} USDT (Testnet)")
        logger.info("=" * 60)

        # 1. Validate Config
        if not config.validate_config():
            logger.error("❌ Config ไม่ถูกต้อง — กรุณาตรวจสอบ .env")
            return False

        # 2. Init Database
        await self.db.init()
        await self.db.log_event("INFO", "BOT_START", "SENTRY_TRADER started", {
            "mode": config.TRADING_MODE,
            "symbols": config.TRADING_SYMBOLS,
        })

        # 3. Load Historical Data
        await self.store.initialize_from_history(self.client)

        # รายงานสถานะหลังโหลด
        status = self.store.status_report()
        for key, info in status.items():
            ready_icon = "✅" if info["ready"] else "⚠️"
            logger.info(f"  {ready_icon} {key}: {info['size']} แท่ง | Close={info['latest_close']}")

        # 4. Load AI Model (Inference-only) — ถ้าไม่มีไฟล์ จะรัน Indicator-Only mode
        self.signal_engine.load_model()

        # 5. Initialize Exchange (ISOLATED + Auto-Leverage)
        await self.executor.initialize_exchange_settings()

        # 6. State Reconciliation (Self-Healing)
        await self.tracker.sync_open_positions()

        return True

    async def on_candle_closed(self, candle: dict):
        """
        Callback ที่เรียกทุกครั้งที่แท่งเทียนปิด (จาก WebSocket)
        Phase 2: Signal Engine คำนวณสัญญาณ Triple Confirmation
        """
        # อัปเดต DataStore ก่อนเสมอ (ทั้ง 15m และ 4h)
        await self.store.update(candle)

        symbol    = candle["symbol"]
        timeframe = candle["timeframe"]

        # วิเคราะห์สัญญาณเฉพาะ Entry Timeframe (15m) เท่านั้น
        if timeframe != config.ENTRY_TIMEFRAME:
            return

        # Signal Engine — Triple Confirmation
        result = await self.signal_engine.process(self.store, symbol=symbol)

        # 1. 🛑 เช็ค & อัปเดต Trailing Stop ก่อนตัดสินใจเปิดไม้ใหม่
        await self._manage_open_trades(symbol, result)

        # Log ทุก Signal (ทั้ง triggered และ skip) ลง Database เพื่อ Weekly Review
        await self.db.log_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type=result.signal_type,
            triggered=result.triggered,
            indicator_values={
                "close_price":   result.close_price,
                "rsi":           result.rsi,
                "ema_200_4h":    result.ema_200_4h,
                "atr":           result.atr,
                "volume_ratio":  result.volume_ratio,
                "ai_confidence": result.ai_confidence,
            },
            skip_reason=result.skip_reason,
        )

        if result.triggered:
            await self._process_entry(symbol, result)
        else:
            logger.debug(str(result))

    async def _manage_open_trades(self, symbol: str, result):
        """บริหารจัดการไม้ที่เปิดอยู่ (Trailing Stop)"""
        async with self.db.get_session() as session:
            stmt = select(Trade).where((Trade.symbol == symbol) & (Trade.status == "OPEN"))
            db_trades = (await session.execute(stmt)).scalars().all()

            for trade in db_trades:
                new_sl, activated, hp = self.stop_loss_mgr.update_trailing_sl(
                    trade=trade, 
                    current_close=result.close_price, 
                    current_atr=result.atr
                )
                
                # ถ้าจุด Stop Loss ถูกคำนวณใหม่ให้สูงขึ้น
                if new_sl > (trade.current_sl or trade.sl_price):
                    # ส่งคำสั่งไป Binance
                    success = await self.executor.update_trailing_stop(trade, new_sl, session)
                    if success:
                        trade.current_sl = new_sl
                        trade.trailing_activated = activated
                        trade.highest_price_since_entry = hp
                        await session.commit()
                        await self.notifier.notify_trailing_update(trade)

    async def _process_entry(self, symbol: str, result):
        """ขั้นตอนการประเมิน Risk และเปิด Order จริง"""
        async with self.db.get_session() as session:
            open_trades = (await session.execute(select(Trade).where(Trade.status == "OPEN"))).scalars().all()

            # 1. Risk Guard
            # หมายเหตุ: daily_pnl ชั่วคราวใส่ 0 ก่อน (ค่อยทำระบบดึง Daily PnL ทีหลัง)
            balances = await self.client.get_account_balance()
            total_balance = balances.get("USDT", 0.0)

            ok, reason = self.risk_guard.check_opening_rules(
                open_trades=open_trades, daily_pnl_usdt=0.0, total_balance=total_balance
            )
            if not ok:
                logger.warning(f"⏩ ข้ามสัญญาณ: {reason}")
                return

            # 2. Position Sizing
            size_res = self.position_sizer.calculate(
                balance=total_balance, entry_price=result.close_price, atr=result.atr
            )
            
            if not size_res.approved:
                logger.warning(f"⏩ ข้ามสัญญาณ: Sizing ไม่ผ่าน -> {size_res.skip_reason}")
                return

            # 3. YING ORDER TAI! (Execute)
            order_info = await self.executor.execute_long_entry(
                symbol=symbol, quantity=size_res.quantity, 
                sl_price=size_res.sl_price, tp_price=size_res.tp_price
            )

            if order_info:
                binance_order_id, actual_entry = order_info
                
                # บันทึกลง DB
                new_trade = Trade(
                    symbol=symbol, side="LONG", status="OPEN",
                    entry_price=actual_entry, quantity=size_res.quantity,
                    sl_price=size_res.sl_price, tp_price=size_res.tp_price,
                    current_sl=size_res.sl_price, highest_price_since_entry=actual_entry,
                    binance_order_id=binance_order_id,
                    rsi_at_entry=result.rsi, atr_at_entry=result.atr,
                    volume_ratio=result.volume_ratio, ai_confidence=result.ai_confidence
                )
                session.add(new_trade)
                await session.flush() # เพื่อให้ได้ new_trade.id ก่อนเข้าไปตั้ง SL

                # 4. วาง Native Stop Loss & Take Profit
                await self.executor.place_native_stops(new_trade, session)
                await session.commit()
                logger.success(f"🎉 เก็บข้อมูล Trade #{new_trade.id} ลงระบบเสร็จสิ้น")
                
                # 5. แจ้งหนุ่ม Telegram
                await self.notifier.notify_new_trade(new_trade)


    async def run(self):
        """Main loop — เปิด WebSocket และรออย่างไม่มีกำหนด"""
        ready = await self.startup()
        if not ready:
            return

        self._running = True
        logger.success("✅ SENTRY_TRADER พร้อมทำงาน — กำลังเฝ้าดูตลาด...")

        try:
            await self.client.start_stream(
                symbols=config.TRADING_SYMBOLS,
                callback=self.on_candle_closed,
                intervals=[config.ENTRY_TIMEFRAME, config.TREND_TIMEFRAME],
            )
        except asyncio.CancelledError:
            logger.info("รับสัญญาณหยุด — กำลัง Shutdown...")
        finally:
            await self.shutdown()

    async def shutdown(self):
        """ปิดระบบอย่างปลอดภัย"""
        logger.info("=" * 60)
        logger.info("  ⏹️ กำลัง Shutdown SENTRY_TRADER...")

        self._running = False
        await self.client.stop_stream()

        await self.db.log_event("INFO", "BOT_STOP", "SENTRY_TRADER stopped gracefully")
        await self.db.close()

        logger.info("  ✅ Shutdown เสร็จสิ้น")
        logger.info("=" * 60)


# ============================================================
# Entry Point
# ============================================================
async def main():
    bot = SentryTrader()

    # Handle Ctrl+C อย่าง Graceful
    loop = asyncio.get_event_loop()

    def handle_shutdown(signum, frame):
        logger.warning("รับสัญญาณ SIGINT/SIGTERM — กำลังหยุด...")
        loop.call_soon_threadsafe(loop.stop)

    try:
        import signal as _sig
        _sig.signal(_sig.SIGINT, handle_shutdown)
        _sig.signal(_sig.SIGTERM, handle_shutdown)
    except Exception:
        pass  # Windows อาจไม่รองรับ SIGTERM

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
