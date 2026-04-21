"""
SENTRY_TRADER — Risk Guard
============================
เป็น Defense Layer อีกชั้นก่อนเปิด Order ใหม่
ตรวจสอบระดับ Portfolio/Account เช่น Drawdown และจำนวนไม้ที่เปิดอยู่
"""

from typing import List, Tuple
from loguru import logger

import config
from database.models import Trade


class RiskGuard:
    """
    ใช้งาน:
        guard = RiskGuard()
        approved, reason = guard.check_opening_rules(
            open_trades=current_open_trades,
            daily_pnl=current_daily_pnl,
            total_balance=account_balance
        )
        if approved: ...
    """

    def __init__(
        self,
        max_open_positions: int = config.MAX_OPEN_POSITIONS,
        daily_loss_limit: float = config.DAILY_LOSS_LIMIT,
    ):
        self.max_open_positions = max_open_positions
        self.daily_loss_limit   = daily_loss_limit

    def check_opening_rules(
        self,
        open_trades: List[Trade],
        daily_pnl_usdt: float,
        total_balance: float,
    ) -> Tuple[bool, str]:
        """
        ตรวจสอบก่อนอนุญาตให้เปิดไม้ใหม่
        """
        # 1. ตรวจสอบจำนวนไม้ที่เปิดอยู่
        if len(open_trades) >= self.max_open_positions:
            logger.info(f"🛑 Risk Guard: จำนวนไม้ที่เปิดอยู่ถึงขีดจำกัดแล้ว ({len(open_trades)}/{self.max_open_positions})")
            return False, "Max Open Positions Reached"

        # 2. Daily Loss Limit (Drawdown วันนี้)
        # ถ้ายอด PnL วันนี้ติดลบเกินกำหนด (เช่น > 5% ของ Balance เริ่มต้น)
        # เพื่อความง่าย ใช้ PnL คิดเป็น % ของ Balance ปัจจุบัน หรือ Balance เมื่อเช้า (ตอนนี้ใช้ปัจจุบัน)
        
        if total_balance > 0:
            daily_loss_percent = (daily_pnl_usdt / total_balance)
            
            # daily_loss_percent จะเป็นค่าติดลบเมื่อขาดทุน
            if daily_loss_percent < 0 and abs(daily_loss_percent) >= self.daily_loss_limit:
                limit_pct = self.daily_loss_limit * 100
                logger.warning(
                    f"🛑 Risk Guard: วันนี้ขาดทุนทุละเป้าหยุด ({daily_loss_percent*100:.2f}% >= -{limit_pct}%) "
                    "บอทจะหยุดเปิดไม้ใหม่จนกว่าจะขึ้นวันใหม่"
                )
                return False, "Daily Loss Limit Exceeded"

        return True, "OK"


# ============================================================
# Quick Test
# ============================================================
if __name__ == "__main__":
    guard = RiskGuard(max_open_positions=1, daily_loss_limit=0.05)
    
    # มีไม้เปิดแล้ว
    ok, remark = guard.check_opening_rules([Trade(id=1)], 0, 100.0)
    print(f"Test 1 (Max Position): {ok} | {remark}")
    
    # ขาดทุนหนัก
    ok, remark = guard.check_opening_rules([], -6.0, 100.0)
    print(f"Test 2 (Loss Limit): {ok} | {remark}")
    
    # สบายๆ อนุญาตเทรด
    ok, remark = guard.check_opening_rules([], 2.0, 100.0)
    print(f"Test 3 (All Clear): {ok} | {remark}")
