"""
SENTRY_TRADER — Position Sizer
================================
คำนวณขนาดไม้ที่ถูกต้องด้วย Fixed Fractional พร้อม Defense Layer 2 ชั้น

Layer 1 — Min Lot Guard:
  ถ้า qty ที่คำนวณได้ < MIN_LOT → ปัดขึ้นเป็น MIN_LOT อัตโนมัติ

Layer 2 — Margin Circuit Breaker:
  ถ้า Margin ที่ใช้ > 30% ของพอร์ต → Skip Trade ทันที
  (ป้องกัน Liquidation cascades บน พอร์ตเล็ก)

ตัวอย่าง (BTCUSDT, ATR=210, Balance=100 USDT, Leverage=5x):
  risk_amount    = 100 × 1% = 1 USDT
  raw_qty        = 1 / 210 = 0.00476 BTC
  adjusted_qty   = max(0.00476, 0.001) = 0.00476 BTC → ปัดลงหาก lot step
  margin_needed  = 0.00476 × 75,600 / 5 = 71.9 USDT (71.9% → SKIP!)
"""

import math
from dataclasses import dataclass
from typing import Optional

from loguru import logger

import config


# ============================================================
# Result
# ============================================================

@dataclass
class SizingResult:
    """ผลลัพธ์การคำนวณขนาดไม้"""
    approved:        bool           # True = ผ่านการอนุมัติทั้งหมด

    quantity:        float = 0.0   # BTC จำนวนที่จะซื้อ
    entry_price:     float = 0.0
    sl_price:        float = 0.0
    tp_price:        float = 0.0

    risk_amount_usdt:   float = 0.0   # USDT ที่ยอมเสี่ยง
    margin_required:    float = 0.0   # USDT margin ที่ถูก Lock
    margin_ratio:       float = 0.0   # % ของพอร์ตที่เป็น Margin
    sl_distance:        float = 0.0   # Entry - SL (USDT)
    tp_distance:        float = 0.0   # TP - Entry (USDT)

    used_min_lot:    bool = False   # True = ถูกปัดขึ้น Min Lot
    skip_reason:     Optional[str] = None

    def __str__(self):
        if not self.approved:
            return f"❌ SKIP | {self.skip_reason}"
        lot_tag = " [MIN LOT]" if self.used_min_lot else ""
        return (
            f"✅ Sizing OK{lot_tag} | "
            f"Qty={self.quantity:.3f} BTC | "
            f"Margin={self.margin_required:.2f} USDT ({self.margin_ratio:.1%}) | "
            f"Risk={self.risk_amount_usdt:.4f} USDT | "
            f"SL={self.sl_price:.2f} | TP={self.tp_price:.2f}"
        )


# ============================================================
# Position Sizer
# ============================================================

class PositionSizer:
    """
    Fixed Fractional Position Sizer พร้อม Binance Constraints

    ใช้งาน:
        sizer = PositionSizer()
        result = sizer.calculate(
            balance=100.0,
            entry_price=75600.0,
            atr=210.0,
        )
        if result.approved:
            # ส่ง order ได้
    """

    def __init__(
        self,
        risk_per_trade:   float = config.RISK_PER_TRADE,
        rr_ratio:         float = config.RR_RATIO,
        atr_sl_mult:      float = config.ATR_MULTIPLIER_SL,
        min_lot:          float = config.MIN_LOT,
        lot_step:         float = config.LOT_STEP,
        max_margin_ratio: float = config.MAX_MARGIN_RATIO,
        leverage:         int   = config.PAPER_LEVERAGE,
    ):
        self.risk_per_trade   = risk_per_trade
        self.rr_ratio         = rr_ratio
        self.atr_sl_mult      = atr_sl_mult
        self.min_lot          = min_lot
        self.lot_step         = lot_step
        self.max_margin_ratio = max_margin_ratio
        self.leverage         = leverage

    def calculate(
        self,
        balance:     float,
        entry_price: float,
        atr:         float,
    ) -> SizingResult:
        """
        คำนวณขนาดไม้ที่เหมาะสม

        Args:
            balance:     Account Balance (USDT)
            entry_price: ราคาที่จะเข้า (ราคาตลาดปัจจุบัน หรือ Close ล่าสุด)
            atr:        ATR ณ แท่งที่สัญญาณออก

        Returns:
            SizingResult พร้อม approved=True/False
        """
        if balance <= 0:
            return SizingResult(approved=False, skip_reason="Balance = 0 หรือ ติดลบ")
        if atr <= 0 or entry_price <= 0:
            return SizingResult(approved=False, skip_reason=f"ATR หรือ Entry Price ไม่ถูกต้อง (ATR={atr}, Entry={entry_price})")

        # ─── คำนวณ SL / TP ────────────────────────────────
        sl_distance = self.atr_sl_mult * atr          # = 2 × ATR
        tp_distance = sl_distance * self.rr_ratio     # = 4 × ATR (R:R = 1:2)
        sl_price    = entry_price - sl_distance
        tp_price    = entry_price + tp_distance

        # ─── Step 1: Fixed Fractional Sizing ─────────────
        risk_amount = balance * self.risk_per_trade   # เช่น 100 × 1% = 1 USDT
        raw_qty     = risk_amount / sl_distance        # qty ตาม Formula

        # ─── Step 2: Snap to Lot Step ────────────────────
        # ปัดลงหา lot step ที่ใกล้ที่สุด (เช่น 0.00476 → 0.004)
        snapped_qty = math.floor(raw_qty / self.lot_step) * self.lot_step
        snapped_qty = round(snapped_qty, 3)           # หลีกเลี่ยง floating point error

        # ─── Layer 1: Min Lot Guard ───────────────────────
        used_min_lot = False
        if snapped_qty < self.min_lot:
            logger.info(
                f"📐 Min Lot Guard: qty={snapped_qty:.4f} < MIN={self.min_lot} "
                f"→ ปัดขึ้นเป็น {self.min_lot} BTC"
            )
            snapped_qty  = self.min_lot
            used_min_lot = True

        # ─── Layer 2: Margin Circuit Breaker ─────────────
        notional        = snapped_qty * entry_price
        margin_required = notional / self.leverage
        margin_ratio    = margin_required / balance

        if margin_ratio > self.max_margin_ratio:
            reason = (
                f"Margin {margin_required:.2f} USDT = {margin_ratio:.1%} ของพอร์ต "
                f"เกิน {self.max_margin_ratio:.0%} → SKIP (ป้องกัน Liquidation)"
            )
            logger.warning(f"🚫 {reason}")
            return SizingResult(
                approved=False,
                quantity=snapped_qty,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_price=tp_price,
                risk_amount_usdt=round(risk_amount, 4),
                margin_required=round(margin_required, 4),
                margin_ratio=round(margin_ratio, 4),
                sl_distance=round(sl_distance, 4),
                tp_distance=round(tp_distance, 4),
                used_min_lot=used_min_lot,
                skip_reason=reason,
            )

        # ─── ผ่านทั้งคู่ → Approved! ──────────────────────
        result = SizingResult(
            approved=True,
            quantity=snapped_qty,
            entry_price=entry_price,
            sl_price=round(sl_price, 2),
            tp_price=round(tp_price, 2),
            risk_amount_usdt=round(risk_amount, 4),
            margin_required=round(margin_required, 4),
            margin_ratio=round(margin_ratio, 4),
            sl_distance=round(sl_distance, 4),
            tp_distance=round(tp_distance, 4),
            used_min_lot=used_min_lot,
        )

        logger.info(str(result))
        return result

    def estimate_required_balance(self, entry_price: float, atr: float) -> float:
        """
        คำนวณ Balance ขั้นต่ำที่ต้องการเพื่อให้ Margin ≤ 30%

        ใช้สำหรับ Weekly Review: "พอร์ตต้องใหญ่แค่ไหนถึงจะเทรด BTC ได้สมบูรณ์"
        """
        sl_distance     = self.atr_sl_mult * atr
        raw_qty         = 1.0 / sl_distance          # qty เมื่อ risk = 1 USDT
        snapped_qty     = max(raw_qty, self.min_lot)
        notional        = snapped_qty * entry_price
        margin_required = notional / self.leverage

        # Balance ที่ต้องการ = Margin / MAX_MARGIN_RATIO / RISK_PER_TRADE
        # = margin ÷ 0.30 ÷ 0.01
        min_balance = (margin_required / self.max_margin_ratio) / self.risk_per_trade
        return round(min_balance, 2)


# ============================================================
# Quick Test
# ============================================================
if __name__ == "__main__":
    sizer = PositionSizer()

    print("\n=== Test 1: พอร์ต 100 USDT + BTC ATR สูง (ควร SKIP) ===")
    r1 = sizer.calculate(balance=100.0, entry_price=75600.0, atr=210.0)
    print(r1)

    print("\n=== Test 2: พอร์ต 5000 USDT + BTC ATR ปกติ (ควรผ่าน) ===")
    r2 = sizer.calculate(balance=5000.0, entry_price=75600.0, atr=210.0)
    print(r2)

    print("\n=== Test 3: พอร์ต 100 USDT + SOL (ราคาต่ำกว่ามาก) ===")
    r3 = sizer.calculate(balance=100.0, entry_price=140.0, atr=3.5)
    print(r3)

    min_bal = sizer.estimate_required_balance(entry_price=75600.0, atr=210.0)
    print(f"\n💡 Balance ขั้นต่ำสำหรับ BTCUSDT (ATR=210): {min_bal:,.0f} USDT")
