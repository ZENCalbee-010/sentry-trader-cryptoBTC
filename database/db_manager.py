"""
SENTRY_TRADER — Database Manager
==================================
หน้าที่: CRUD operations สำหรับ ทุก Table
ใช้ SQLAlchemy Async เพื่อไม่ block Event Loop

Usage:
    db = DatabaseManager()
    await db.init()
    
    await db.log_signal(signal_data)
    await db.save_trade(trade_data)
    await db.close_trade(trade_id, exit_price, pnl)
"""

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, update, func
from loguru import logger

import config
from database.models import Base, Trade, Signal, PortfolioSnapshot, SystemEvent


class DatabaseManager:

    def __init__(self, db_url: Optional[str] = None):
        self._url = db_url or config.DATABASE_URL
        self._engine = None
        self._session_factory = None

    async def init(self):
        """สร้าง Database และ Tables ถ้ายังไม่มี"""
        logger.info(f"เชื่อมต่อ Database: {self._url}")

        self._engine = create_async_engine(
            self._url,
            echo=False,     # ตั้งเป็น True เพื่อ debug SQL
            pool_pre_ping=True,
        )

        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )

        # สร้างตารางทั้งหมด (ถ้ายังไม่มี)
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.success("✅ Database พร้อมใช้งาน")
        await self.log_event("INFO", "DB_INIT", "Database initialized successfully")

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def get_session(self):
        """Provide a transactional scope around a series of operations."""
        async with self._session_factory() as session:
            yield session

    # ----------------------------------------------------------
    # Signal Operations
    # ----------------------------------------------------------

    async def log_signal(
        self,
        symbol: str,
        timeframe: str,
        signal_type: str,
        triggered: bool,
        indicator_values: dict,
        trade_id: Optional[int] = None,
        skip_reason: Optional[str] = None,
    ) -> int:
        """บันทึก Signal ทุกอัน (ทั้ง triggered และไม่ triggered)"""
        async with self._session_factory() as session:
            signal = Signal(
                symbol=symbol,
                timeframe=timeframe,
                signal_type=signal_type,
                triggered=triggered,
                trade_id=trade_id,
                skip_reason=skip_reason,
                **indicator_values,
            )
            session.add(signal)
            await session.commit()
            await session.refresh(signal)
            return signal.id

    # ----------------------------------------------------------
    # Trade Operations
    # ----------------------------------------------------------

    async def open_trade(self, trade_data: dict) -> int:
        """บันทึก Trade ที่เพิ่งเปิด"""
        async with self._session_factory() as session:
            trade = Trade(**trade_data, status="OPEN")
            session.add(trade)
            await session.commit()
            await session.refresh(trade)
            logger.info(f"💾 บันทึก Trade #{trade.id} | {trade.symbol} {trade.side}")
            return trade.id

    async def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        pnl_usdt: float,
        pnl_percent: float,
        fee_usdt: float = 0.0,
    ):
        """อัปเดต Trade ที่ปิดแล้ว"""
        async with self._session_factory() as session:
            await session.execute(
                update(Trade)
                .where(Trade.id == trade_id)
                .values(
                    status="CLOSED",
                    exit_price=exit_price,
                    pnl_usdt=pnl_usdt,
                    pnl_percent=pnl_percent,
                    fee_usdt=fee_usdt,
                    closed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            logger.info(f"💾 ปิด Trade #{trade_id} | PnL={pnl_usdt:+.2f} USDT")

    async def activate_trailing_stop(self, trade_id: int):
        """Mark ว่า Trailing Stop activated แล้ว"""
        async with self._session_factory() as session:
            await session.execute(
                update(Trade)
                .where(Trade.id == trade_id)
                .values(trailing_activated=True)
            )
            await session.commit()

    async def get_open_trades(self) -> list[Trade]:
        """ดึง Trade ที่ยังเปิดอยู่ทั้งหมด"""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Trade).where(Trade.status == "OPEN")
            )
            return list(result.scalars().all())

    # ----------------------------------------------------------
    # Portfolio Snapshot
    # ----------------------------------------------------------

    async def save_portfolio_snapshot(self, snapshot_data: dict):
        """บันทึก Daily Portfolio Snapshot"""
        async with self._session_factory() as session:
            snapshot = PortfolioSnapshot(**snapshot_data)
            session.add(snapshot)
            await session.commit()

    async def get_stats(self) -> dict:
        """สรุปสถิติการเทรดทั้งหมด"""
        async with self._session_factory() as session:
            result = await session.execute(
                select(
                    func.count(Trade.id).label("total"),
                    func.sum(Trade.pnl_usdt).label("total_pnl"),
                    func.avg(Trade.pnl_usdt).label("avg_pnl"),
                ).where(Trade.status == "CLOSED")
            )
            row = result.one()

            wins = await session.execute(
                select(func.count(Trade.id)).where(
                    Trade.status == "CLOSED",
                    Trade.pnl_usdt > 0
                )
            )
            win_count = wins.scalar() or 0
            total = row.total or 0

            return {
                "total_trades": total,
                "win_trades":   win_count,
                "loss_trades":  total - win_count,
                "win_rate":     (win_count / total * 100) if total > 0 else 0.0,
                "total_pnl":    round(row.total_pnl or 0, 2),
                "avg_pnl":      round(row.avg_pnl or 0, 2),
            }

    # ----------------------------------------------------------
    # System Events
    # ----------------------------------------------------------

    async def log_event(
        self,
        level: str,
        event_type: str,
        message: str,
        details: Optional[dict] = None,
    ):
        """บันทึก System Event"""
        async with self._session_factory() as session:
            event = SystemEvent(
                level=level,
                event_type=event_type,
                message=message,
                details=json.dumps(details) if details else None,
            )
            session.add(event)
            await session.commit()

    async def close(self):
        """ปิด Database connection"""
        if self._engine:
            await self._engine.dispose()
            logger.info("Database connection ปิดแล้ว")
