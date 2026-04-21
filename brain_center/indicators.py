"""
SENTRY_TRADER — Technical Indicators
======================================
ฟังก์ชันคำนวณ Indicator ทั้งหมดที่ Brain Center ใช้
Input: pandas DataFrame (OHLCV จาก DataStore)
Output: pandas Series

Indicators:
  - RSI(14)         → Entry Signal
  - EMA(200) on 4H  → Trend Filter
  - EMA(50) on 15m  → Slope calculation
  - ATR(14)         → Volatility / SL distance
  - Volume MA(20)   → Volume confirmation
  - RSI Momentum    → ความเร็วของ RSI
  - Body Ratio      → ความแข็งแกร่งของแท่งเทียน
"""

import numpy as np
import pandas as pd
import ta

import config


# ============================================================
# Individual Indicators
# ============================================================

def calc_rsi(df: pd.DataFrame, period: int = config.RSI_PERIOD) -> pd.Series:
    """
    RSI (Relative Strength Index)
    ค่า < 30 = Oversold, ค่า > 70 = Overbought
    """
    return ta.momentum.RSIIndicator(
        close=df["close"], window=period
    ).rsi()


def calc_ema(df: pd.DataFrame, period: int = config.EMA_PERIOD) -> pd.Series:
    """EMA (Exponential Moving Average)"""
    return ta.trend.EMAIndicator(
        close=df["close"], window=period
    ).ema_indicator()


def calc_atr(df: pd.DataFrame, period: int = config.ATR_PERIOD) -> pd.Series:
    """
    ATR (Average True Range) — วัดความผันผวน
    ใช้: SL = Entry - (2 × ATR), Kill Switch (ATR > 2x avg)
    """
    return ta.volatility.AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=period,
    ).average_true_range()


def calc_volume_ma(df: pd.DataFrame, period: int = config.VOLUME_MA_PERIOD) -> pd.Series:
    """Volume Moving Average — ใช้ confirm แรงซื้อ"""
    return df["volume"].rolling(window=period).mean()


def calc_ema_slope(ema_series: pd.Series, period: int = 5) -> pd.Series:
    """
    ความชัน EMA — % change ใน N แท่ง
    ค่าบวก = เทรนด์ขาขึ้น, ค่าลบ = เทรนด์ขาลง
    """
    return ema_series.pct_change(periods=period) * 100


def calc_rsi_momentum(rsi_series: pd.Series, period: int = 3) -> pd.Series:
    """
    RSI Momentum — ความเร็วที่ RSI เปลี่ยน
    RSI ขึ้นเร็ว = momentum แรง
    """
    return rsi_series.diff(periods=period)


def calc_body_ratio(df: pd.DataFrame) -> pd.Series:
    """
    Body Ratio — สัดส่วน Body ต่อ Total Range ของแท่งเทียน
    ค่าใกล้ 1 = แท่งเทียนแข็งแกร่ง (Marubozu-like)
    ค่าใกล้ 0 = Doji หรือ Spinning Top (ลังเล)
    """
    body = (df["close"] - df["open"]).abs()
    total_range = df["high"] - df["low"]
    # หลีกเลี่ยง division by zero
    return body / total_range.replace(0, np.nan)


# ============================================================
# ATR Kill Switch Check
# ============================================================

def is_kill_switch_active(
    atr_series: pd.Series,
    multiplier: float = config.ATR_KILL_SWITCH_MULTIPLIER,
    avg_period: int = 50,
) -> bool:
    """
    Kill Switch ตรวจสอบว่าตลาดผันผวนเกินปกติหรือไม่

    Logic: ถ้า ATR ปัจจุบัน > avg_ATR(50) × multiplier → True (หยุดเปิดออเดอร์ใหม่)
    
    Args:
        atr_series: Series ของ ATR values
        multiplier: ค่า default 2.0 (จาก config)
        avg_period: ช่วงเวลาสำหรับคำนวณค่าเฉลี่ย ATR
    
    Returns:
        True = Kill Switch ON (หยุดเปิดไม้ใหม่)
        False = ปกติ
    """
    if len(atr_series) < avg_period:
        return False  # ข้อมูลไม่พอ → ปลอดภัยก่อน

    current_atr = atr_series.iloc[-1]
    avg_atr = atr_series.iloc[-avg_period:].mean()

    activated = current_atr > avg_atr * multiplier

    if activated:
        import loguru
        loguru.logger.warning(
            f"⚠️ Kill Switch ACTIVE | ATR={current_atr:.2f} > {multiplier}x avg={avg_atr:.2f}"
        )

    return activated


# ============================================================
# RSI Cross Detection
# ============================================================

def rsi_crossed_up_from_oversold(rsi_series: pd.Series, threshold: float = config.RSI_OVERSOLD) -> bool:
    """
    ตรวจสอบว่า RSI เพิ่งตัดขึ้นจากโซน Oversold หรือไม่
    (แท่งก่อนหน้า < threshold AND แท่งปัจจุบัน >= threshold)
    
    นี่คือ Entry Signal แรกของ Triple Confirmation
    """
    if len(rsi_series) < 2:
        return False

    prev_rsi = rsi_series.iloc[-2]
    curr_rsi = rsi_series.iloc[-1]

    return prev_rsi < threshold and curr_rsi >= threshold


# ============================================================
# Feature Builder (สำหรับ AI Model)
# ============================================================

def build_feature_row(
    df_entry: pd.DataFrame,
    df_trend: pd.DataFrame,
) -> dict | None:
    """
    สร้าง Feature Dict สำหรับ AI Model จากแท่งล่าสุด

    Features (ทั้งหมดเป็น Continuous Numerical):
      1. rsi              — RSI 14 ของ 15m
      2. ema_slope        — ความชัน EMA50 (% change 5 แท่ง)
      3. atr_ratio        — ATR / Close * 100 (normalized volatility)
      4. volume_ratio     — Volume / Volume_MA20
      5. price_to_ema200  — % ห่างจาก EMA200 บน 4H
      6. rsi_momentum     — RSI diff 3 แท่ง
      7. body_ratio       — Body / Total Range
    
    Returns:
        dict of {feature_name: float} หรือ None ถ้าข้อมูลไม่พอ
    """
    min_required = max(config.EMA_PERIOD + 10, 60)
    if len(df_entry) < min_required or len(df_trend) < config.EMA_PERIOD + 10:
        return None

    # คำนวณทุก Indicator บน Entry TF (15m)
    rsi     = calc_rsi(df_entry)
    ema50   = calc_ema(df_entry, period=50)
    atr     = calc_atr(df_entry)
    vol_ma  = calc_volume_ma(df_entry)

    # คำนวณบน Trend TF (4H)
    ema200_4h = calc_ema(df_trend, period=config.EMA_PERIOD)

    # ตรวจสอบว่ามีค่าครบ
    if any(
        s.isna().iloc[-1] for s in [rsi, ema50, atr, vol_ma, ema200_4h]
    ):
        return None

    # Derived Features
    ema_slope_val   = calc_ema_slope(ema50).iloc[-1]
    atr_ratio_val   = atr.iloc[-1] / df_entry["close"].iloc[-1] * 100
    volume_ratio_val = df_entry["volume"].iloc[-1] / vol_ma.iloc[-1]
    rsi_momentum_val = calc_rsi_momentum(rsi).iloc[-1]
    body_ratio_val  = calc_body_ratio(df_entry).iloc[-1]
    price_to_ema200 = (
        (df_entry["close"].iloc[-1] - ema200_4h.iloc[-1]) / ema200_4h.iloc[-1] * 100
    )

    return {
        "rsi":            round(rsi.iloc[-1], 4),
        "ema_slope":      round(ema_slope_val, 6),
        "atr_ratio":      round(atr_ratio_val, 6),
        "volume_ratio":   round(volume_ratio_val, 6),
        "price_to_ema200": round(price_to_ema200, 6),
        "rsi_momentum":   round(rsi_momentum_val, 4),
        "body_ratio":     round(body_ratio_val if not np.isnan(body_ratio_val) else 0.5, 4),
    }


def build_feature_dataframe(
    df_entry: pd.DataFrame,
    df_trend: pd.DataFrame,
) -> pd.DataFrame:
    """
    สร้าง Feature DataFrame ทั้งหมด (ใช้สำหรับ Training AI Model)
    
    Returns:
        DataFrame shape (N, 7) พร้อม feature columns
    """
    rsi       = calc_rsi(df_entry)
    ema50     = calc_ema(df_entry, period=50)
    atr       = calc_atr(df_entry)
    vol_ma    = calc_volume_ma(df_entry)
    ema200_4h = calc_ema(df_trend, period=config.EMA_PERIOD)

    # Align 4H EMA → 15m index (forward fill)
    if df_trend.index.dtype != df_entry.index.dtype:
        ema200_aligned = ema200_4h.reindex(df_entry.index, method="ffill")
    else:
        ema200_aligned = ema200_4h.reindex(df_entry.index, method="ffill")

    features = pd.DataFrame(index=df_entry.index)
    features["rsi"]             = rsi
    features["ema_slope"]       = calc_ema_slope(ema50)
    features["atr_ratio"]       = atr / df_entry["close"] * 100
    features["volume_ratio"]    = df_entry["volume"] / vol_ma
    features["price_to_ema200"] = (df_entry["close"] - ema200_aligned) / ema200_aligned * 100
    features["rsi_momentum"]    = calc_rsi_momentum(rsi)
    features["body_ratio"]      = calc_body_ratio(df_entry)

    return features.dropna()
