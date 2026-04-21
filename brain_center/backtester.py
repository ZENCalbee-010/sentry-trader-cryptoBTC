"""
SENTRY_TRADER — Backtester
============================
Simulate กลยุทธ์บนข้อมูลย้อนหลัง เพื่อประเมินก่อนรัน Live

Focus Metrics (ตามที่ตกลงไว้):
  1. Max Drawdown    — ต้องไม่เกิน 15-20%
  2. Profit Factor   — ต้องมากกว่า 1.5
  3. Win Rate + Total Trades — ดูคู่กันเสมอ

Additional (แสดงทั้งหมด):
  - Total P&L, Avg P&L per trade
  - Sharpe Ratio (annualized)
  - Best/Worst trade
  - Avg holding time

Backtest Mode: Simple (Indicator Only) — ไม่ใช้ AI เพื่อดู baseline
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

import config
from brain_center.indicators import (
    calc_rsi,
    calc_ema,
    calc_atr,
    calc_volume_ma,
    is_kill_switch_active,
    rsi_crossed_up_from_oversold,
)


# ============================================================
# Trade Record
# ============================================================

@dataclass
class BacktestTrade:
    entry_bar:   int
    entry_time:  datetime
    entry_price: float
    sl_price:    float
    tp_price:    float
    exit_bar:    Optional[int]   = None
    exit_time:   Optional[datetime] = None
    exit_price:  Optional[float] = None
    exit_reason: str             = ""  # "TP" | "SL" | "TIMEOUT"
    pnl_percent: float           = 0.0  # % กำไร/ขาดทุน
    pnl_usdt:    float           = 0.0
    holding_bars: int            = 0


# ============================================================
# Backtester
# ============================================================

class Backtester:
    """
    Simple Event-Driven Backtester (Bar-by-Bar Simulation)
    
    ใช้งาน:
        bt = Backtester()
        await bt.load_data("BTCUSDT", days=365)
        results = bt.run()
        bt.print_report(results)
    """

    def __init__(
        self,
        initial_balance: float = config.INITIAL_PAPER_BALANCE,
        risk_per_trade:  float = config.RISK_PER_TRADE,
        rr_ratio:        float = config.RR_RATIO,
        atr_sl_mult:     float = config.ATR_MULTIPLIER_SL,
        commission_rate: float = 0.0004,  # 0.04% per side (Binance Futures Taker)
    ):
        self.initial_balance = initial_balance
        self.risk_per_trade  = risk_per_trade
        self.rr_ratio        = rr_ratio
        self.atr_sl_mult     = atr_sl_mult
        self.commission_rate = commission_rate

        self.df_15m: Optional[pd.DataFrame] = None
        self.df_4h:  Optional[pd.DataFrame] = None

    async def load_data(self, symbol: str = config.PRIMARY_SYMBOL, days: int = 365):
        """โหลดข้อมูลย้อนหลังจาก Binance"""
        from data_engine.binance_client import BinanceClient

        client = BinanceClient()
        logger.info(f"📥 โหลดข้อมูล Backtest | {symbol} | {days} วัน")

        bars_15m = days * 24 * 4      # 4 แท่งต่อชั่วโมง
        bars_4h  = days * 6           # 6 แท่งต่อวัน

        # Binance max 1500 per request — ส่งหลาย request ถ้าต้องการมากกว่านั้น
        candles_15m = await self._fetch_full(client, symbol, "15m", min(bars_15m, 1500))
        candles_4h  = await self._fetch_full(client, symbol, "4h",  min(bars_4h, 500))

        self.df_15m = self._to_dataframe(candles_15m)
        self.df_4h  = self._to_dataframe(candles_4h)

        logger.info(f"  15m: {len(self.df_15m)} แท่ง ({self.df_15m.index[0].date()} → {self.df_15m.index[-1].date()})")
        logger.info(f"  4h:  {len(self.df_4h)} แท่ง ({self.df_4h.index[0].date()} → {self.df_4h.index[-1].date()})")

    async def _fetch_full(self, client, symbol, interval, limit):
        return await client.fetch_historical_klines(symbol, interval, limit=limit)

    def _to_dataframe(self, candles: list) -> pd.DataFrame:
        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        df.sort_index(inplace=True)
        return df[["open", "high", "low", "close", "volume"]]

    # ----------------------------------------------------------
    # Run Backtest
    # ----------------------------------------------------------

    def run(self) -> dict:
        """
        รัน Backtest แบบ Bar-by-Bar Simulation
        
        Returns:
            dict ของ Metrics ทั้งหมด
        """
        if self.df_15m is None:
            raise RuntimeError("ต้องเรียก load_data() ก่อน")

        df = self.df_15m.copy()

        # คำนวณ Indicators
        rsi_all    = calc_rsi(df)
        ema200_15m = calc_ema(df, period=50)   # ใช้ EMA50 บน 15m สำหรับ backtest (แทน 4H ที่ align ยาก)
        atr_all    = calc_atr(df)
        vol_ma_all = calc_volume_ma(df)

        # Align EMA 4H → 15m
        ema200_4h_aligned = self._align_4h_ema(df)

        # ============================================================
        # Simulate Bar by Bar
        # ============================================================
        trades: list[BacktestTrade] = []
        balance = self.initial_balance
        equity_curve = [balance]
        open_trade: Optional[BacktestTrade] = None

        warmup = max(config.EMA_PERIOD + 10, 60)  # ข้ามช่วงที่ Indicator ยังไม่พร้อม

        for i in range(warmup, len(df)):
            row = df.iloc[i]

            # --- จัดการ Open Trade ---
            if open_trade is not None:
                low  = row["low"]
                high = row["high"]

                if low <= open_trade.sl_price:
                    # SL hit
                    exit_price = open_trade.sl_price
                    pnl_pct    = (exit_price / open_trade.entry_price - 1) * 100
                    pnl_usdt   = balance * self.risk_per_trade * (pnl_pct / abs((open_trade.sl_price / open_trade.entry_price - 1) * 100))
                    fee        = balance * self.commission_rate * 2
                    pnl_usdt  -= fee
                    balance   += pnl_usdt
                    open_trade.exit_bar  = i
                    open_trade.exit_time = df.index[i]
                    open_trade.exit_price = exit_price
                    open_trade.exit_reason = "SL"
                    open_trade.pnl_percent = round(pnl_pct, 4)
                    open_trade.pnl_usdt    = round(pnl_usdt, 4)
                    open_trade.holding_bars = i - open_trade.entry_bar
                    trades.append(open_trade)
                    open_trade = None
                    equity_curve.append(balance)
                    continue

                elif high >= open_trade.tp_price:
                    # TP hit
                    exit_price = open_trade.tp_price
                    pnl_pct    = (exit_price / open_trade.entry_price - 1) * 100
                    risk_pct   = abs((open_trade.sl_price / open_trade.entry_price - 1) * 100)
                    pnl_usdt   = balance * self.risk_per_trade * (pnl_pct / risk_pct)
                    fee        = balance * self.commission_rate * 2
                    pnl_usdt  -= fee
                    balance   += pnl_usdt
                    open_trade.exit_bar   = i
                    open_trade.exit_time  = df.index[i]
                    open_trade.exit_price = exit_price
                    open_trade.exit_reason = "TP"
                    open_trade.pnl_percent = round(pnl_pct, 4)
                    open_trade.pnl_usdt    = round(pnl_usdt, 4)
                    open_trade.holding_bars = i - open_trade.entry_bar
                    trades.append(open_trade)
                    open_trade = None
                    equity_curve.append(balance)
                    continue

                equity_curve.append(balance)
                continue

            # --- ตรวจสัญญาณ (ถ้าไม่มี Open Trade) ---
            if balance <= 0:
                break

            # ดึงข้อมูลล่าสุด (ถึง i)
            rsi_window = rsi_all.iloc[:i+1]
            atr_val    = atr_all.iloc[i]
            vol_ratio  = df["volume"].iloc[i] / vol_ma_all.iloc[i] if vol_ma_all.iloc[i] > 0 else 0

            # Kill Switch
            if is_kill_switch_active(atr_all.iloc[:i+1]):
                equity_curve.append(balance)
                continue

            # Trend Filter (4H EMA 200)
            ema200_val = ema200_4h_aligned.iloc[i]
            if pd.isna(ema200_val) or row["close"] <= ema200_val:
                equity_curve.append(balance)
                continue

            # Triple Confirmation (RSI Cross + Volume) — ไม่มี AI ใน Backtest
            if not rsi_crossed_up_from_oversold(rsi_window):
                equity_curve.append(balance)
                continue
            if vol_ratio <= 1.0:
                equity_curve.append(balance)
                continue

            # ✅ สัญญาณ → คำนวณ Entry
            if pd.isna(atr_val) or atr_val <= 0:
                equity_curve.append(balance)
                continue

            entry = row["close"]
            sl    = entry - self.atr_sl_mult * atr_val
            tp    = entry + self.atr_sl_mult * atr_val * self.rr_ratio

            open_trade = BacktestTrade(
                entry_bar   = i,
                entry_time  = df.index[i],
                entry_price = entry,
                sl_price    = sl,
                tp_price    = tp,
            )
            equity_curve.append(balance)

        # ปิด Trade ที่ยังค้างอยู่ตอนหมด Data
        if open_trade is not None:
            last = df.iloc[-1]
            pnl_pct  = (last["close"] / open_trade.entry_price - 1) * 100
            risk_pct = abs((open_trade.sl_price / open_trade.entry_price - 1) * 100)
            pnl_usdt = balance * self.risk_per_trade * (pnl_pct / risk_pct) if risk_pct > 0 else 0
            balance += pnl_usdt
            open_trade.exit_reason  = "TIMEOUT"
            open_trade.exit_price   = last["close"]
            open_trade.pnl_usdt     = round(pnl_usdt, 4)
            open_trade.pnl_percent  = round(pnl_pct, 4)
            open_trade.holding_bars = len(df) - open_trade.entry_bar
            trades.append(open_trade)

        return self._calculate_metrics(trades, equity_curve)

    def _align_4h_ema(self, df_15m: pd.DataFrame) -> pd.Series:
        """Align EMA 200 จาก 4H → index ของ 15m ด้วย forward fill"""
        if self.df_4h is None or self.df_4h.empty:
            return pd.Series(index=df_15m.index, dtype=float)
        ema_4h = calc_ema(self.df_4h, period=config.EMA_PERIOD)
        return ema_4h.reindex(df_15m.index, method="ffill")

    # ----------------------------------------------------------
    # Metrics Calculation
    # ----------------------------------------------------------

    def _calculate_metrics(self, trades: list[BacktestTrade], equity_curve: list) -> dict:
        """คำนวณ Metrics ทั้งหมดจาก Trade History"""
        if not trades:
            return {"error": "ไม่มี Trade ใน Backtest"}

        equity = np.array(equity_curve)
        pnls   = [t.pnl_usdt for t in trades]
        wins   = [t for t in trades if t.pnl_usdt > 0]
        losses = [t for t in trades if t.pnl_usdt <= 0]

        # Profit Factor
        gross_profit = sum(t.pnl_usdt for t in wins)
        gross_loss   = abs(sum(t.pnl_usdt for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Max Drawdown
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak * 100
        max_drawdown = float(abs(drawdown.min()))

        # Sharpe Ratio (annualized, 15m bars → ~35,000 bars/year)
        returns = np.diff(equity) / equity[:-1]
        if returns.std() > 0:
            sharpe = float((returns.mean() / returns.std()) * np.sqrt(35040))
        else:
            sharpe = 0.0

        # Holding Time
        avg_holding = np.mean([t.holding_bars for t in trades]) * 15  # นาที

        # Exit Reasons breakdown
        tp_count  = sum(1 for t in trades if t.exit_reason == "TP")
        sl_count  = sum(1 for t in trades if t.exit_reason == "SL")
        to_count  = sum(1 for t in trades if t.exit_reason == "TIMEOUT")

        total_return = (equity[-1] / self.initial_balance - 1) * 100

        return {
            # ⭐ แกนหลัก 3 ตัว
            "max_drawdown_pct":  round(max_drawdown, 2),
            "profit_factor":     round(profit_factor, 3),
            "win_rate":          round(len(wins) / len(trades) * 100, 2),
            "total_trades":      len(trades),

            # เพิ่มเติม
            "total_return_pct":  round(total_return, 2),
            "final_balance":     round(equity[-1], 2),
            "gross_profit_usdt": round(gross_profit, 2),
            "gross_loss_usdt":   round(gross_loss, 2),
            "avg_pnl_usdt":      round(np.mean(pnls), 4),
            "best_trade_usdt":   round(max(pnls), 4),
            "worst_trade_usdt":  round(min(pnls), 4),
            "sharpe_ratio":      round(sharpe, 3),
            "avg_holding_min":   round(avg_holding, 1),

            # Breakdown
            "tp_count":  tp_count,
            "sl_count":  sl_count,
            "to_count":  to_count,
            "wins":      len(wins),
            "losses":    len(losses),

            # Raw data (สำหรับ plot)
            "_equity_curve": equity.tolist(),
            "_trades":       trades,
        }

    # ----------------------------------------------------------
    # Report Printer
    # ----------------------------------------------------------

    def print_report(self, results: dict):
        """พิมพ์รายงาน Backtest แบบสวยงาม"""
        if "error" in results:
            logger.error(f"Backtest Error: {results['error']}")
            return

        sep = "=" * 55
        logger.info(sep)
        logger.info("  📈 SENTRY_TRADER — Backtest Report")
        logger.info(sep)

        # ⭐ 3 Metrics หลัก
        dd    = results["max_drawdown_pct"]
        pf    = results["profit_factor"]
        wr    = results["win_rate"]
        total = results["total_trades"]

        dd_status = "✅" if dd < 15 else ("⚠️" if dd < 20 else "❌")
        pf_status = "✅" if pf > 1.5 else ("⚠️" if pf > 1.2 else "❌")
        wr_status = "✅" if wr > 50 else "⚠️"
        to_status = "✅" if total > 20 else "⚠️"

        logger.info(f"\n  ⭐ Focus Metrics:")
        logger.info(f"  {dd_status} Max Drawdown  : {dd:.2f}% (เป้า < 15%)")
        logger.info(f"  {pf_status} Profit Factor : {pf:.3f}   (เป้า > 1.5)")
        logger.info(f"  {wr_status}{to_status} Win Rate      : {wr:.1f}% | Trades: {total}")

        logger.info(f"\n  📊 Returns:")
        logger.info(f"     Total Return  : {results['total_return_pct']:+.2f}%")
        logger.info(f"     Final Balance : {results['final_balance']:.2f} USDT")
        logger.info(f"     Gross Profit  : +{results['gross_profit_usdt']:.2f} USDT")
        logger.info(f"     Gross Loss    : -{results['gross_loss_usdt']:.2f} USDT")
        logger.info(f"     Avg P&L/Trade : {results['avg_pnl_usdt']:+.4f} USDT")
        logger.info(f"     Best Trade    : +{results['best_trade_usdt']:.4f} USDT")
        logger.info(f"     Worst Trade   : {results['worst_trade_usdt']:.4f} USDT")

        logger.info(f"\n  📐 Risk/Performance:")
        logger.info(f"     Sharpe Ratio  : {results['sharpe_ratio']:.3f}")
        logger.info(f"     Avg Hold Time : {results['avg_holding_min']:.0f} นาที")

        logger.info(f"\n  🎯 Exit Breakdown:")
        logger.info(f"     TP Hit  : {results['tp_count']} ไม้ ({results['tp_count']/total*100:.1f}%)")
        logger.info(f"     SL Hit  : {results['sl_count']} ไม้ ({results['sl_count']/total*100:.1f}%)")
        logger.info(f"     Timeout : {results['to_count']} ไม้")

        logger.info(f"\n{sep}")

        # คำแนะนำ
        if dd > 20 or pf < 1.2:
            logger.warning("  ⚠️ ผลการทดสอบไม่ผ่านเกณฑ์ — ปรับจูน Parameter ก่อน Live!")
        elif dd < 15 and pf > 1.5 and total > 20:
            logger.success("  🎉 ผ่านเกณฑ์ทั้งหมด — พร้อม Paper Trading!")
        else:
            logger.warning("  🔶 ผ่านบางเกณฑ์ — ทบทวนก่อนตัดสินใจ")

        logger.info(sep)


# ============================================================
# CLI Runner
# ============================================================
async def run_backtest(symbol: str = "BTCUSDT", days: int = 180):
    bt = Backtester()
    await bt.load_data(symbol, days=days)
    results = bt.run()
    bt.print_report(results)
    return results


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_backtest())
