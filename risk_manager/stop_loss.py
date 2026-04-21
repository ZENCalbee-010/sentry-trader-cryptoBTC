"""
SENTRY_TRADER — Dynamic Trailing Stop
=======================================
จัดการเลื่อน Stop Loss เมื่อราคาวิ่่งไปถูกทาง
ใช้กลยุทธ์ Dynamic Trailing Stop (Option B)

Logic (Long Only):
  1. เก็บค่า highest_price ตั้งแต่เปิดไม้
  2. เมื่อ Unrealized PnL ไปถึงเป้า 1R (1 × SL Distance)
     จะ activate Trailing Stop (เลื่อน SL มาที่ Break Even)
  3. หลังจากนั้น ทุกครั้งที่ราคาพุ่งทำจุดสูงสุดใหม่ 
     SL จะขยับตามไปที่: highest_price - (TRAILING_ATR_MULTIPLIER × ATR_ปัจจุบัน)
  4. SL จะถูก Lock ไว้ที่จุดสูงสุดเสมอ (ขยับขึ้นได้อย่างเดียว ห้ามถอยกลับ)
"""

from typing import Tuple
from loguru import logger

import config
from database.models import Trade


class StopLossManager:
    """
    ใช้งาน:
        sl_manager = StopLossManager()
        new_sl, activated = sl_manager.update_trailing_sl(
            trade=trade, 
            current_close=76000.0, 
            current_atr=200.0
        )
        if new_sl > trade.current_sl:
            # ส่งคำสั่งแก้ SL ไปที่ Exchange
    """

    def __init__(
        self,
        activation_rr: float = config.TRAILING_ACTIVATION_RR,
        atr_multiplier: float = config.TRAILING_ATR_MULTIPLIER,
    ):
        self.activation_rr = activation_rr
        self.atr_multiplier = atr_multiplier

    def update_trailing_sl(
        self, 
        trade: Trade, 
        current_close: float, 
        current_atr: float
    ) -> Tuple[float, bool]:
        """
        คำนวณและอัปเดต Trailing Stop Loss
        
        Args:
            trade: Object จาก Database Models
            current_close: ราคาปิดล่าสุด
            current_atr: ค่า ATR ของแท่งเทียนล่าสุด
            
        Returns:
            new_sl (float): ราคา Stop Loss อันใหม่ (ถ้าไม่ได้ขยับจะคืนค่าเดิม)
            new_activated (bool): สถานะ Trailing Stop หลังจากการอัปเดต
        """
        # อัปเดต Highest Price
        highest_price = trade.highest_price_since_entry or trade.entry_price
        if current_close > highest_price:
            highest_price = current_close

        current_sl = trade.current_sl or trade.sl_price
        activated = trade.trailing_activated

        # ระยะความเสี่ยงดั้งเดิมคือ Entry - Initial SL
        initial_risk = trade.entry_price - trade.sl_price

        # ยังไม่ถูกเปิดใช้งาน Trailing
        if not activated:
            # เช็คว่ากำไรถึงเป้า R หรือยัง (เช่น RR = 1)
            target_price = trade.entry_price + (initial_risk * self.activation_rr)
            
            if highest_price >= target_price:
                activated = True
                logger.info(f"🛡️ Trailing Stop ACTIVATED สำหรับ Trade #{trade.id} | Target={target_price:.2f}")

        # ถ้าเปิดใช้งานแล้ว ให้ลองขยับ SL
        if activated:
            # Trailing SL ใหม่ = จุดสูงสุด - (ATR ✕ ตัวคูณ)
            new_trailing_sl = highest_price - (self.atr_multiplier * current_atr)

            # --- กฎเหล็ก 2 ข้อ ---
            # 1. ต้องไม่ต่ำกว่า Break Even (Entry Price + ค่าธรรมเนียม ถ้าอยากเผื่อ)
            # ในที่นี้วางไว้เหนือ Entry นิดหน่อย หรือเท่ากับ Entry
            new_trailing_sl = max(new_trailing_sl, trade.entry_price)

            # 2. ต้องไม่ต่ำกว่า SL เดิม (Ratchet Effect ขยับได้อย่างเดียว)
            new_sl = max(current_sl, new_trailing_sl)
        else:
            new_sl = current_sl

        return new_sl, activated, highest_price

    def is_stop_loss_hit(self, trade: Trade, current_low: float) -> bool:
        """ตรวจสอบว่าโดนชน SL หรือไม่"""
        sl_price = trade.current_sl or trade.sl_price
        return current_low <= sl_price

    def is_take_profit_hit(self, trade: Trade, current_high: float) -> bool:
        """ตรวจสอบว่าโดนชน TP หรือไม่"""
        return current_high >= trade.tp_price


# ============================================================
# Quick Test
# ============================================================
if __name__ == "__main__":
    from database.models import Trade

    sl_mgr = StopLossManager(activation_rr=1.0, atr_multiplier=1.0)
    
    # จำลอง Trade
    t = Trade(
        id=1, symbol="BTCUSDT", side="LONG",
        entry_price=75000.0, sl_price=74500.0, tp_price=76000.0,
        trailing_activated=False, current_sl=74500.0, highest_price_since_entry=75000.0
    )
    
    print("=== ทดสอบ Dynamic Trailing Stop ===")
    print(f"Entry: {t.entry_price} | SL_Init: {t.sl_price} | TP: {t.tp_price}")
    
    # 1. ราคายังไม่ถึง 1R (1R = 75500)
    sl, act, hr = sl_mgr.update_trailing_sl(t, 75200.0, 250.0)
    t.current_sl, t.trailing_activated, t.highest_price_since_entry = sl, act, hr
    print(f"Close=75200 -> Act={act}, SL={sl}, High={hr}")
    
    # 2. ราคาพุ่งทะลุ 1R
    sl, act, hr = sl_mgr.update_trailing_sl(t, 75600.0, 300.0)
    t.current_sl, t.trailing_activated, t.highest_price_since_entry = sl, act, hr
    print(f"Close=75600 -> Act={act}, SL={sl}, High={hr} (ควรเลื่อนมา BE อย่างน้อย)")
    
    # 3. ราคาพุ่งต่อ (Trend)
    sl, act, hr = sl_mgr.update_trailing_sl(t, 75900.0, 200.0)
    t.current_sl, t.trailing_activated, t.highest_price_since_entry = sl, act, hr
    print(f"Close=75900 -> Act={act}, SL={sl}, High={hr} (ควรเลื่อนตาม Highest: 75900 - 200)")
    
    # 4. ราคาย่อตัว (SL ห้ามถอยกลับ)
    sl, act, hr = sl_mgr.update_trailing_sl(t, 75700.0, 250.0)
    t.current_sl, t.trailing_activated, t.highest_price_since_entry = sl, act, hr
    print(f"Close=75700 -> Act={act}, SL={sl}, High={hr} (SL ห้ามลดลง)")
