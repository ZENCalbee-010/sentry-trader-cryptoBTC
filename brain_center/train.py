"""
SENTRY_TRADER — Manual Training Script
========================================
รันบน PC เท่านั้น! ไม่ต้องนำขึ้น Hardware

Workflow:
  1. python train.py                  → เทรนด้วยข้อมูล 1 ปีย้อนหลัง
  2. ตรวจสอบ CV F1 Score และ Feature Importance
  3. ถ้าพอใจ → copy brain_center/models/random_forest.joblib → Hardware

Tips:
  - เทรนใหม่เมื่อ: Win Rate ใน Live ลดลง > 10%, หรือทุก 1-3 เดือน
  - ทดสอบ confim ด้วย Backtester ก่อนโยน Model ขึ้น Bot จริง
"""

import asyncio
import sys
from loguru import logger
from pathlib import Path

# ============================================================
# Logging
# ============================================================
logger.remove()
logger.add(
    lambda msg: print(msg, end=""),
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    colorize=True,
)

import config
from data_engine.binance_client import BinanceClient
from brain_center.indicators import (
    calc_rsi, calc_ema, calc_atr, calc_volume_ma,
    build_feature_dataframe, create_labels_from_df,
)
from brain_center.ai_model import AIModel, FEATURE_COLUMNS, create_labels


async def fetch_training_data(symbol: str, days: int = config.TRAIN_DATA_DAYS):
    """ดึงข้อมูล Historical สำหรับ Training"""
    client = BinanceClient()

    logger.info(f"📥 ดึงข้อมูล Training | {symbol} | {days} วัน")

    # ดึง 15m bars (max 1500 per request)
    limit_15m = min(days * 24 * 4, 1500)
    limit_4h  = min(days * 6, 500)

    candles_15m = await client.fetch_historical_klines(symbol, "15m", limit=limit_15m)
    candles_4h  = await client.fetch_historical_klines(symbol, "4h",  limit=limit_4h)

    import pandas as pd

    def to_df(candles):
        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        return df[["open", "high", "low", "close", "volume"]].sort_index()

    df_15m = to_df(candles_15m)
    df_4h  = to_df(candles_4h)

    logger.info(f"  15m: {len(df_15m)} แท่ง | 4h: {len(df_4h)} แท่ง")
    return df_15m, df_4h


async def main():
    import pandas as pd

    symbol = config.PRIMARY_SYMBOL
    logger.info("=" * 60)
    logger.info("  🧠 SENTRY_TRADER — Manual Training")
    logger.info(f"  Symbol: {symbol} | Features: {len(FEATURE_COLUMNS)}")
    logger.info("=" * 60)

    # 1. ดึงข้อมูล
    df_15m, df_4h = await fetch_training_data(symbol)

    # 2. สร้าง Features
    logger.info("\n📐 สร้าง Feature Matrix...")
    X = build_feature_dataframe(df_15m, df_4h)
    logger.info(f"  Feature shape: {X.shape}")

    # 3. สร้าง Labels
    logger.info("\n🏷️  สร้าง Labels (TP/SL lookahead)...")
    atr_series = calc_atr(df_15m)
    y_all = create_labels(df_15m, atr_series)

    # Align X กับ y
    common_idx = X.index.intersection(y_all.dropna().index)
    X = X.loc[common_idx]
    y = y_all.loc[common_idx].astype(int)

    logger.info(f"  Training samples : {len(X)}")
    logger.info(f"  Win rate in data : {y.mean():.1%}")
    logger.info(f"  Class balance    : Win={y.sum()} | Loss={len(y)-y.sum()}")

    if len(X) < 100:
        logger.error("❌ ข้อมูลน้อยเกินไป (< 100 samples) — ลองเพิ่ม days")
        return

    # 4. Train Model
    logger.info("\n🏋️  เริ่ม Training...")
    model = AIModel()
    metrics = model.train(X, y, cv_folds=5)

    # 5. แสดงผล
    logger.info(f"\n📊 Training Results:")
    logger.info(f"   Samples    : {metrics['train_samples']}")
    logger.info(f"   CV F1 Mean : {metrics['cv_f1_mean']:.3f} ± {metrics['cv_f1_std']:.3f}")
    logger.info(f"   Accuracy   : {metrics['train_accuracy']:.3f}")

    # 6. ถามก่อน Save
    logger.info(f"\n💾 บันทึก Model ไปที่: {config.MODELS_DIR / 'random_forest.joblib'}")
    answer = input("บันทึก? (y/n): ").strip().lower()

    if answer == "y":
        model.save()
        logger.success("✅ บันทึกสำเร็จ!")
        logger.info("  → copy ไฟล์ .joblib ไปใส่ใน Hardware เพื่อใช้ Inference")
    else:
        logger.warning("ยกเลิกการบันทึก")

    # 7. รัน Quick Backtest เพื่อยืนยัน
    logger.info("\n🔍 รัน Quick Backtest เพื่อยืนยัน Strategy...")
    from brain_center.backtester import Backtester
    bt = Backtester()
    bt.df_15m = df_15m
    bt.df_4h  = df_4h
    results = bt.run()
    bt.print_report(results)


if __name__ == "__main__":
    asyncio.run(main())
