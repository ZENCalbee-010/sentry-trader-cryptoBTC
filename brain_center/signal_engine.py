"""
SENTRY_TRADER — Signal Engine
================================
หัวใจของ Brain Center — ตัดสินใจว่าจะเข้าหรือไม่เข้าออเดอร์

Triple Confirmation System (Long Only):
  1. RSI ตัดขึ้นจาก Oversold (< 30) ใน 15m
  2. Volume > Volume MA(20) ใน 15m
  3. AI Confidence > 85%

Filters:
  - EMA 200 Filter: Close ต้องอยู่เหนือ EMA200 บน 4H (เทรดตามน้ำ)
  - ATR Kill Switch: ถ้า ATR > 2x avg → หยุดเปิดไม้ใหม่ (แต่ไม่ปิดไม้เก่า)
"""

from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

import config
from brain_center.indicators import (
    calc_rsi,
    calc_ema,
    calc_atr,
    calc_volume_ma,
    build_feature_row,
    is_kill_switch_active,
    rsi_crossed_up_from_oversold,
)
from brain_center.ai_model import AIModel
from data_engine.data_store import DataStore


# ============================================================
# Signal Result
# ============================================================

@dataclass
class SignalResult:
    """ผลลัพธ์จาก Signal Engine"""
    
    symbol:        str
    timeframe:     str
    signal_type:   str           # "BUY" | "HOLD"
    triggered:     bool          # True = ส่งไปให้ Risk Manager
    
    # Indicator Values
    close_price:   float = 0.0
    rsi:           float = 0.0
    ema_200_4h:    float = 0.0
    atr:           float = 0.0
    volume_ratio:  float = 0.0
    ai_confidence: float = 0.0
    
    # Debug
    skip_reason:   Optional[str] = None
    checks:        dict = field(default_factory=dict)  # ผลแต่ละ check

    def __str__(self):
        if self.triggered:
            return (
                f"✅ BUY Signal | {self.symbol} "
                f"| RSI={self.rsi:.1f} | Vol={self.volume_ratio:.2f}x "
                f"| AI={self.ai_confidence:.1%} | Close={self.close_price:.2f}"
            )
        return f"⏸️ HOLD | {self.symbol} | {self.skip_reason}"


# ============================================================
# Signal Engine
# ============================================================

class SignalEngine:
    """
    ประมวลผลสัญญาณแต่ละแท่งเทียนที่ปิดสำเร็จ
    
    ใช้งาน:
        engine = SignalEngine()
        engine.load_model()
        
        # เรียกทุกครั้งที่แท่ง 15m ปิด
        result = await engine.process(store, symbol="BTCUSDT")
        if result.triggered:
            # ส่งต่อให้ Risk Manager
    """

    def __init__(self):
        self.ai_model = AIModel()
        self._kill_switch_active = False

    def load_model(self) -> bool:
        """โหลด AI Model (ถ้ามี) — ถ้าไม่มีจะรัน Indicator-Only mode"""
        loaded = self.ai_model.load()
        if not loaded:
            logger.warning("🔶 AI Model ไม่มี → Bot จะใช้เฉพาะ Indicator (ไม่ต้องการ AI Confidence)")
        return loaded

    # ----------------------------------------------------------
    # Main Process (เรียกทุกครั้งที่ 15m แท่งปิด)
    # ----------------------------------------------------------

    async def process(
        self,
        store: DataStore,
        symbol: str = config.PRIMARY_SYMBOL,
    ) -> SignalResult:
        """
        วิเคราะห์สัญญาณจาก DataStore ล่าสุด
        
        Args:
            store:  DataStore ที่มีข้อมูล 15m + 4H ล่าสุด
            symbol: Symbol ที่ต้องการตรวจ
        
        Returns:
            SignalResult
        """
        result_base = {"symbol": symbol, "timeframe": config.ENTRY_TIMEFRAME}

        # 1. ตรวจว่า Buffer พร้อมหรือยัง
        if not store.is_ready(symbol, config.ENTRY_TIMEFRAME):
            return SignalResult(
                **result_base,
                signal_type="HOLD", triggered=False,
                skip_reason="Buffer ยังไม่พร้อม (กำลังโหลดข้อมูล)"
            )
        if not store.is_ready(symbol, config.TREND_TIMEFRAME):
            return SignalResult(
                **result_base,
                signal_type="HOLD", triggered=False,
                skip_reason="Trend buffer ยังไม่พร้อม"
            )

        # 2. ดึง DataFrame
        df_15m = await store.get_df(symbol, config.ENTRY_TIMEFRAME)
        df_4h  = await store.get_df(symbol, config.TREND_TIMEFRAME)

        if df_15m.empty or df_4h.empty:
            return SignalResult(
                **result_base,
                signal_type="HOLD", triggered=False,
                skip_reason="DataFrame ว่างเปล่า"
            )

        # 3. คำนวณ Indicators
        rsi_series    = calc_rsi(df_15m)
        ema200_4h     = calc_ema(df_4h, period=config.EMA_PERIOD)
        atr_series    = calc_atr(df_15m)
        vol_ma_series = calc_volume_ma(df_15m)

        # ค่าล่าสุด
        close       = df_15m["close"].iloc[-1]
        curr_rsi    = rsi_series.iloc[-1]
        curr_atr    = atr_series.iloc[-1]
        curr_vol    = df_15m["volume"].iloc[-1]
        curr_vol_ma = vol_ma_series.iloc[-1]
        curr_ema200 = ema200_4h.iloc[-1]

        vol_ratio = curr_vol / curr_vol_ma if curr_vol_ma > 0 else 0.0

        # Base result
        base_info = dict(
            close_price  = round(close, 4),
            rsi          = round(curr_rsi, 2),
            ema_200_4h   = round(curr_ema200, 4),
            atr          = round(curr_atr, 4),
            volume_ratio = round(vol_ratio, 4),
        )

        # ============================================================
        # Filter 1: ATR Kill Switch (ตลาดผันผวนเกิน → ไม่เปิดใหม่)
        # ============================================================
        kill_active = is_kill_switch_active(atr_series)
        self._kill_switch_active = kill_active

        if kill_active:
            return SignalResult(
                **result_base, **base_info,
                signal_type="HOLD", triggered=False,
                skip_reason="Kill Switch ON — ATR สูงเกิน 2x ค่าเฉลี่ย",
                checks={"kill_switch": True},
            )

        # ============================================================
        # Filter 2: Trend Filter — ราคาต้องอยู่เหนือ EMA 200 (4H)
        # Long Only: ห้ามเทรด Counter-Trend
        # ============================================================
        trend_ok = close > curr_ema200
        if not trend_ok:
            return SignalResult(
                **result_base, **base_info,
                signal_type="HOLD", triggered=False,
                skip_reason=f"Trend Filter ไม่ผ่าน | Close={close:.2f} < EMA200={curr_ema200:.2f}",
                checks={"kill_switch": False, "trend": False},
            )

        # ============================================================
        # Triple Confirmation
        # ============================================================
        checks = {"kill_switch": False, "trend": True}

        # Confirmation 1: RSI ตัดขึ้นจาก Oversold
        rsi_signal = rsi_crossed_up_from_oversold(rsi_series, threshold=config.RSI_OVERSOLD)
        checks["rsi_cross"] = rsi_signal

        if not rsi_signal:
            return SignalResult(
                **result_base, **base_info,
                signal_type="HOLD", triggered=False,
                skip_reason=f"RSI ยังไม่ตัดขึ้นจาก {config.RSI_OVERSOLD} (RSI={curr_rsi:.1f})",
                checks=checks,
            )

        # Confirmation 2: Volume สูงกว่าค่าเฉลี่ย
        volume_ok = vol_ratio > 1.0
        checks["volume"] = volume_ok

        if not volume_ok:
            return SignalResult(
                **result_base, **base_info,
                signal_type="HOLD", triggered=False,
                skip_reason=f"Volume ไม่พอ | ratio={vol_ratio:.2f}x (ต้องการ > 1.0x)",
                checks=checks,
            )

        # Confirmation 3: AI Confidence > 85% (ถ้ามี Model)
        features = build_feature_row(df_15m, df_4h)
        if features is None:
            ai_confidence = 1.0 if not self.ai_model.is_loaded else 0.0
        else:
            ai_confidence = self.ai_model.predict_confidence(features)

        checks["ai_confidence"] = ai_confidence

        min_confidence = config.RF_MIN_CONFIDENCE if self.ai_model.is_loaded else 0.0
        if ai_confidence < min_confidence:
            return SignalResult(
                **result_base, **base_info,
                ai_confidence=round(ai_confidence, 4),
                signal_type="HOLD", triggered=False,
                skip_reason=f"AI Confidence ต่ำเกิน | {ai_confidence:.1%} < {min_confidence:.0%}",
                checks=checks,
            )

        # ============================================================
        # 🚀 ผ่านทุก Check → BUY Signal!
        # ============================================================
        logger.success(
            f"🚀 BUY Signal! | {symbol} | "
            f"RSI={curr_rsi:.1f} | Vol={vol_ratio:.2f}x | "
            f"AI={ai_confidence:.1%} | Close={close:.2f}"
        )

        return SignalResult(
            **result_base, **base_info,
            ai_confidence=round(ai_confidence, 4),
            signal_type="BUY",
            triggered=True,
            checks={**checks, "all_passed": True},
        )

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch_active
