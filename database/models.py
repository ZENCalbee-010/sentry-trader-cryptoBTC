"""
SENTRY_TRADER — Database Models (SQLAlchemy)
=============================================
Schema สำหรับเก็บ Log การเทรด, สัญญาณ, และ Portfolio

Tables:
  - trades          : บันทึกทุก Order ที่เปิด/ปิด
  - signals         : Log สัญญาณทุกอัน (ทั้งที่ trigger และไม่ trigger)
  - portfolio_snapshots : บันทึก Balance ทุกวัน
  - system_events   : Log event สำคัญ (start, stop, errors)
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, Float, String, Boolean,
    DateTime, Text, Enum, Index
)
from sqlalchemy.orm import DeclarativeBase
import enum


class Base(DeclarativeBase):
    pass


# ============================================================
# Enums
# ============================================================

class TradeStatus(str, enum.Enum):
    OPEN     = "OPEN"
    CLOSED   = "CLOSED"
    CANCELED = "CANCELED"

class TradeSide(str, enum.Enum):
    LONG  = "LONG"
    SHORT = "SHORT"

class SignalType(str, enum.Enum):
    BUY   = "BUY"
    SELL  = "SELL"
    HOLD  = "HOLD"

class EventLevel(str, enum.Enum):
    INFO    = "INFO"
    WARNING = "WARNING"
    ERROR   = "ERROR"
    CRITICAL = "CRITICAL"


# ============================================================
# Tables
# ============================================================

class Trade(Base):
    """
    บันทึกทุก Order ที่ส่งออกไป
    
    Columns สำคัญ:
      - entry_price, sl_price, tp_price  : ราคาเข้า, SL, TP
      - exit_price, pnl                  : ราคาออก, กำไร/ขาดทุน
      - ai_confidence                    : ความมั่นใจของ AI ณ ตอนที่เข้า
    """
    __tablename__ = "trades"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    symbol        = Column(String(20), nullable=False, index=True)    # เช่น "BTCUSDT"
    side          = Column(String(10), nullable=False)                # "LONG" / "SHORT"
    status        = Column(String(10), nullable=False, default="OPEN")

    # Order Info
    binance_order_id = Column(String(50), nullable=True)
    quantity         = Column(Float, nullable=False)                  # จำนวนที่ซื้อ (BTC)
    entry_price      = Column(Float, nullable=False)
    sl_price         = Column(Float, nullable=False)                  # Stop Loss
    tp_price         = Column(Float, nullable=False)                  # Take Profit
    exit_price       = Column(Float, nullable=True)

    # P&L
    pnl_usdt         = Column(Float, nullable=True)                   # กำไร/ขาดทุน (USDT)
    pnl_percent      = Column(Float, nullable=True)                   # %
    fee_usdt         = Column(Float, nullable=True, default=0.0)

    # Risk Info
    risk_amount_usdt   = Column(Float, nullable=True)                 # เงินที่ยอมเสี่ยง
    account_balance    = Column(Float, nullable=True)                 # Balance ณ ตอนเข้า
    leverage           = Column(Integer, nullable=True, default=1)

    # Signal Info (จาก Brain Center)
    rsi_at_entry       = Column(Float, nullable=True)
    ema_trend          = Column(String(10), nullable=True)            # "BULLISH"/"BEARISH"
    atr_at_entry       = Column(Float, nullable=True)
    volume_ratio       = Column(Float, nullable=True)                 # volume / volume_ma
    ai_confidence      = Column(Float, nullable=True)                 # 0.0 - 1.0
    trailing_activated        = Column(Boolean, default=False)
    # Dynamic Trailing Stop State
    current_sl                = Column(Float, nullable=True)   # SL ปัจจุบัน (อัปเดตได้)
    highest_price_since_entry = Column(Float, nullable=True)   # Highest Close หลังเข้า Order
    
    # Binance Order IDs (Phase 4)
    binance_order_id          = Column(String, nullable=True)
    binance_sl_order_id       = Column(String, nullable=True)
    binance_tp_order_id       = Column(String, nullable=True)

    # Timestamps
    opened_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    closed_at  = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))

    # Indexes สำหรับ Query เร็ว
    __table_args__ = (
        Index("ix_trades_symbol_status", "symbol", "status"),
        Index("ix_trades_opened_at", "opened_at"),
    )

    def __repr__(self):
        return (
            f"<Trade id={self.id} {self.symbol} {self.side} "
            f"entry={self.entry_price} status={self.status}>"
        )


class Signal(Base):
    """
    Log สัญญาณที่ Brain Center คำนวณออกมา (ทุกอัน)
    ใช้สำหรับ Weekly Review และปรับจูน Parameter
    """
    __tablename__ = "signals"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    symbol      = Column(String(20), nullable=False, index=True)
    timeframe   = Column(String(5), nullable=False)
    signal_type = Column(String(10), nullable=False)  # "BUY" / "SELL" / "HOLD"
    triggered   = Column(Boolean, default=False)      # True = Bot เปิด Order จริง

    # Indicator Values ณ ตอนสัญญาณ
    close_price    = Column(Float, nullable=True)
    rsi            = Column(Float, nullable=True)
    ema_200_4h     = Column(Float, nullable=True)
    atr            = Column(Float, nullable=True)
    volume_ratio   = Column(Float, nullable=True)
    ai_confidence  = Column(Float, nullable=True)

    # เหตุผลที่ไม่ trigger (ถ้า triggered=False)
    skip_reason    = Column(String(100), nullable=True)

    # Trade ที่เกิดขึ้น (ถ้า triggered)
    trade_id       = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    def __repr__(self):
        return f"<Signal {self.symbol} {self.signal_type} triggered={self.triggered}>"


class PortfolioSnapshot(Base):
    """
    บันทึก Balance และสถิติการเทรดทุกวัน
    ใช้ plot Equity Curve และ drawdown analysis
    """
    __tablename__ = "portfolio_snapshots"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date   = Column(DateTime(timezone=True), nullable=False, index=True)

    # Balance
    total_balance   = Column(Float, nullable=False)
    available_balance = Column(Float, nullable=False)
    unrealized_pnl  = Column(Float, default=0.0)

    # Daily Stats
    daily_pnl       = Column(Float, default=0.0)
    daily_trades    = Column(Integer, default=0)
    daily_wins      = Column(Integer, default=0)
    daily_losses    = Column(Integer, default=0)

    # Running Stats
    total_trades    = Column(Integer, default=0)
    total_wins      = Column(Integer, default=0)
    win_rate        = Column(Float, default=0.0)        # 0.0 - 1.0
    max_drawdown    = Column(Float, default=0.0)        # จาก Peak

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SystemEvent(Base):
    """Log event สำคัญของระบบ"""
    __tablename__ = "system_events"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    level       = Column(String(10), nullable=False)      # INFO / WARNING / ERROR
    event_type  = Column(String(50), nullable=False)      # เช่น "BOT_START", "KILL_SWITCH"
    message     = Column(Text, nullable=True)
    details     = Column(Text, nullable=True)             # JSON string สำหรับข้อมูลเพิ่มเติม

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    def __repr__(self):
        return f"<SystemEvent {self.level} {self.event_type}>"
