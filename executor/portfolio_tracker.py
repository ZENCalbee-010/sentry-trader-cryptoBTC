"""
SENTRY_TRADER — Portfolio Tracker (State Reconciliation)
========================================================
ดึงข้อมูล Position จาก Binance เทียบกับ SQLite เสมอเพื่อตรวจสอบ:
1. การชน SL/TP ไปแล้ว (ขณะบอทปิดหรือเน็ตหลุด)
2. อัปเดต PnL ประจำวัน
"""

from loguru import logger

import config
from data_engine.binance_client import BinanceClient
from database.db_manager import DatabaseManager
from database.models import Trade


class PortfolioTracker:
    def __init__(self, client: BinanceClient, db: DatabaseManager):
        self.client = client
        self.db = db

    async def sync_open_positions(self):
        """
        Self-Healing Mechanism 
        ตรวจจับว่า DB ของเราบอกว่าถือของอยู่ แต่ Binance บอกว่าไม่ได้ถือแล้ว 
        (แปลว่าชน SL หรือ TP ไปแล้วตั้งแต่ตอนออฟไลน์)
        """
        logger.info("=" * 50)
        logger.info("  🔄 State Reconciliation: ตรวจสอบความตรงกันของระบบ")

        for symbol in config.TRADING_SYMBOLS:
            # 1. ฐานข้อมูลบอกว่าเรามีของไหม?
            async with self.db.get_session() as session:
                from sqlalchemy import select
                stmt = select(Trade).where(
                    (Trade.symbol == symbol) & (Trade.status == "OPEN")
                )
                result = await session.execute(stmt)
                db_trades = result.scalars().all()

                # 2. ของจริงจาก Binance มีไหม?
                binance_positions = await self.client.fetch_open_positions(symbol)
                
                has_binance_pos = len(binance_positions) > 0
                has_db_pos = len(db_trades) > 0

                if has_db_pos and not has_binance_pos:
                    # ของจริงหายไปแล้ว!
                    logger.warning(
                        f"🚨 {symbol}: DB สถานะ OPEN แต่ Binance ไม่มีของ! → สันนิษฐานว่าชน SL/TP ระหว่างออฟไลน์"
                    )
                    for trade in db_trades:
                        trade.status = "CLOSED"
                        trade.exit_reason = "HIT_SL_OR_TP_OFFLINE"
                        # ไม่รู้ผลกำไรเป๊ะๆ อาจต้องยิงอีก API เพื่อหาประวัติ PnL (สำหรับตอนนี้ตีเป็นดึงค่าล่าสุดแทน)
                        logger.warning(f"ปิดรอบไม้ #{trade.id} บน DB ให้ตรงกับความเป็นจริง")
                    
                    await session.commit()
                
                elif has_binance_pos and not has_db_pos:
                    logger.warning(
                        f"🚨 {symbol}: Binance มือดีเปิดของไว้ แต่ DB ไม่รับรู้! "
                        "→ อาจมีมือดีใช้แอปเทรดเอง (Bot จะปล่อยผ่านและไม่ไปยุ่ง)"
                    )
                
                elif has_binance_pos and has_db_pos:
                    # ถือทั้งคู่ -> อัปเดต PnL เบื้องต้นได้
                    pos = binance_positions[0]
                    unrealized_pnl = float(pos.get("unRealizedProfit", 0))
                    logger.info(f"✅ {symbol} Sync ตรงกัน | ถืออยู่ {pos['positionAmt']} | Unrealized PnL: {unrealized_pnl:.2f} USDT")
                
                else:
                    logger.info(f"✅ {symbol} Sync ตรงกัน | ปอดว่าง ไม่มี Position")
        
        logger.info("=" * 50)
