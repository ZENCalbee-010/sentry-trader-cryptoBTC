"""
SENTRY_TRADER — Order Executor
================================
ผู้รับคำสั่งจาก Risk Manager ส่งต่อไปยัง Binance

รับผิดชอบ:
  1. การส่งคำสั่ง Market เข้าซื้อ 
  2. การตั้ง Native Stop Loss / Take Profit 
  3. การอัปเดต Trailing Stop (ลบของเก่า & ตั้งใหม่)
"""

from loguru import logger
from typing import Optional, Tuple

import config
from data_engine.binance_client import BinanceClient
from database.models import Trade
from database.db_manager import DatabaseManager


class OrderExecutor:
    """ส่งคำสั่งและจัดการออเดอร์บน Binance"""

    def __init__(self, client: BinanceClient, db: DatabaseManager):
        self.client = client
        self.db = db

    async def initialize_exchange_settings(self):
        """เซ็ตข้อมูลพื้นฐานตอนเปิดบอท (Idempotent)"""
        logger.info("=" * 50)
        logger.info("  ⚙️ ตั้งค่า Exchange Settings")
        
        for symbol in config.TRADING_SYMBOLS:
            await self.client.set_margin_type(symbol, "ISOLATED")
            await self.client.set_leverage(symbol, config.PAPER_LEVERAGE)
            
        logger.info("=" * 50)

    async def execute_long_entry(
        self, symbol: str, quantity: float, sl_price: float, tp_price: float
    ) -> Optional[Tuple[str, float]]:
        """
        ยิงฝั่ง Long:
        1. เปิด Market BUY
        2. ตั้ง OCO-like SL/TP (Reduce Only, STOP_MARKET / TAKE_PROFIT_MARKET)
        
        Returns:
            Tuple [binance_order_id, actual_entry_price]
        """
        logger.info(f"🚀 Executor เตรียมยิง LONG {symbol} | Qty={quantity} | SL={sl_price} | TP={tp_price}")

        # 1. ยิง Market Buy
        entry_res = await self.client.create_order(
            symbol=symbol, side="BUY", order_type="MARKET", quantity=quantity
        )

        if "error" in entry_res:
            logger.error(f"❌ เทรดล้มเหลว (Entry): {entry_res['error']}")
            return None

        binance_order_id = str(entry_res.get("orderId", ""))
        
        # ราคาจริงที่เข้าได้ (จาก response ถ้ามี หรือคร่าวๆ)
        # ปกติ Market order จะไม่มี price ใน response ทันที ต้องเช็ค fills
        # ใน Testnet/Simple อนุโลมว่า entry = close ก่อน (Portfolio Tracker จะ fetch CUMULATIVE QUOTE มาปรับ)
        actual_entry = float(entry_res.get("avgPrice") or entry_res.get("price") or 0.0)
        logger.success(f"✅ Market Buy สำเร็จ! OrderID: {binance_order_id}")

        return binance_order_id, actual_entry

    async def place_native_stops(
        self, trade: Trade, session
    ) -> bool:
        """
        สร้าง SL / TP ล็อกพอร์ต (Reduce Only) แล้วบันทึกลง DB
        """
        symbol = trade.symbol
        quantity = trade.quantity

        # 1. Stop Loss (STOP_MARKET)
        sl_res = await self.client.create_order(
            symbol=symbol, side="SELL", order_type="STOP_MARKET",
            quantity=quantity, stop_price=trade.sl_price, close_position=True
        )
        if "error" not in sl_res:
            trade.binance_sl_order_id = str(sl_res.get("orderId", ""))
            logger.info(f"🛡️ ตั้ง Native SL สำเร็จ [ID: {trade.binance_sl_order_id}] ราคา {trade.sl_price}")
        else:
            logger.error(f"❌ ตั้ง SL ล้มเหลว: {sl_res['error']}")

        # 2. Take Profit (TAKE_PROFIT_MARKET)
        tp_res = await self.client.create_order(
            symbol=symbol, side="SELL", order_type="TAKE_PROFIT_MARKET",
            quantity=quantity, stop_price=trade.tp_price, close_position=True
        )
        if "error" not in tp_res:
            trade.binance_tp_order_id = str(tp_res.get("orderId", ""))
            logger.info(f"🎯 ตั้ง Native TP สำเร็จ [ID: {trade.binance_tp_order_id}] ราคา {trade.tp_price}")
        else:
            logger.error(f"❌ ตั้ง TP ล้มเหลว: {tp_res['error']}")

        await session.commit()
        return True

    async def update_trailing_stop(self, trade: Trade, new_sl_price: float, session) -> bool:
        """
        เมื่อราคาวิ่ง ต้องขยับ SL ให้สูงขึ้น:
        1. Cancel SL เดิม
        2. ตั้ง STOP_MARKET อันใหม่
        """
        logger.info(f"🔄 ขยับ Trailing Stop สำหรับ {trade.symbol} จาก {trade.current_sl} -> {new_sl_price}")
        
        # 1. Cancel ของเดิม
        if trade.binance_sl_order_id:
            await self.client.cancel_order(trade.symbol, int(trade.binance_sl_order_id))

        # 2. ตั้งของใหม่
        sl_res = await self.client.create_order(
            symbol=trade.symbol, side="SELL", order_type="STOP_MARKET",
            quantity=trade.quantity, stop_price=new_sl_price, close_position=True
        )

        if "error" not in sl_res:
            trade.binance_sl_order_id = str(sl_res.get("orderId", ""))
            trade.current_sl = new_sl_price
            await session.commit()
            logger.success(f"✅ Trailing Stop เลื่อนสำเร็จ [New ID: {trade.binance_sl_order_id}]")
            return True
        else:
            logger.error(f"❌ เลื่อน Trailing Stop ล้มเหลว: {sl_res['error']}")
            return False
