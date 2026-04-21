"""
SENTRY_TRADER — Data Store
============================
หน้าที่: เก็บแท่งเทียน (OHLCV) ใน Memory Buffer (pandas DataFrame)
สำหรับให้ Brain Center ดึงไปคำนวณ Indicators ได้ทันที

Features:
  - Rolling Buffer per (Symbol, Timeframe): เก็บแค่ N แท่งล่าสุด
  - Thread-safe ด้วย asyncio.Lock
  - รองรับ Multi-symbol ตั้งแต่ต้น
"""

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from loguru import logger

import config


class CandleBuffer:
    """
    Rolling Buffer เก็บแท่งเทียนล่าสุดสำหรับ 1 คู่ (symbol, timeframe)
    
    ใช้ deque เพื่อให้การ append และ popleft เร็ว O(1)
    """

    def __init__(self, symbol: str, timeframe: str, maxsize: int = config.CANDLE_BUFFER_SIZE):
        self.symbol = symbol
        self.timeframe = timeframe
        self.maxsize = maxsize
        self._buffer: deque[dict] = deque(maxlen=maxsize)
        self._lock = asyncio.Lock()

    async def append(self, candle: dict):
        """เพิ่มแท่งเทียนใหม่ (แทนที่แท่งเก่าสุดถ้าเต็ม)"""
        async with self._lock:
            # ป้องกัน duplicate: ถ้า open_time ซ้ำกับแท่งล่าสุด → อัปเดตแทน
            if self._buffer and self._buffer[-1]["open_time"] == candle["open_time"]:
                self._buffer[-1] = candle
                logger.debug(f"อัปเดตแท่ง (duplicate): {candle['symbol']} {candle['timeframe']}")
            else:
                self._buffer.append(candle)

    async def get_dataframe(self) -> pd.DataFrame:
        """แปลง Buffer → pandas DataFrame พร้อมใช้งาน"""
        async with self._lock:
            if not self._buffer:
                return pd.DataFrame()

            df = pd.DataFrame(list(self._buffer))
            df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df.set_index("datetime", inplace=True)
            df.sort_index(inplace=True)

            return df

    @property
    def size(self) -> int:
        return len(self._buffer)

    @property
    def is_ready(self) -> bool:
        """Buffer มีข้อมูลพอสำหรับคำนวณ EMA 200 หรือยัง"""
        min_required = config.EMA_PERIOD + 10  # +10 margin
        return self.size >= min_required

    def latest_close(self) -> Optional[float]:
        """ราคาปิดล่าสุด"""
        if self._buffer:
            return self._buffer[-1]["close"]
        return None


class DataStore:
    """
    Central Data Store — จัดการ CandleBuffer ทุก (Symbol, Timeframe)
    
    ใช้งาน:
        store = DataStore()
        await store.initialize()
        
        # เพิ่มแท่ง
        await store.update(candle)
        
        # ดึง DataFrame
        df = await store.get_df("BTCUSDT", "15m")
    """

    def __init__(self):
        # key: (symbol, timeframe) → CandleBuffer
        self._buffers: dict[tuple[str, str], CandleBuffer] = {}
        self._initialized = False

        # สร้าง Buffer สำหรับทุก Symbol + Timeframe ที่ Config กำหนด
        for symbol in config.TRADING_SYMBOLS:
            for tf in [config.ENTRY_TIMEFRAME, config.TREND_TIMEFRAME]:
                key = (symbol.upper(), tf)
                self._buffers[key] = CandleBuffer(symbol.upper(), tf)

        logger.info(f"DataStore สร้าง Buffer: {list(self._buffers.keys())}")

    async def initialize_from_history(self, client):
        """
        โหลดข้อมูลย้อนหลังเข้า Buffer จาก REST API
        เรียกตอน Startup เพื่อให้ Indicator คำนวณได้ทันที
        
        Args:
            client: BinanceClient instance
        """
        logger.info("📥 กำลังโหลดข้อมูลย้อนหลัง...")

        tasks = []
        for (symbol, tf), buffer in self._buffers.items():
            tasks.append(
                self._load_history_for_buffer(client, symbol, tf, buffer)
            )

        await asyncio.gather(*tasks)
        self._initialized = True
        logger.success("✅ โหลดข้อมูลย้อนหลังเสร็จสิ้น — พร้อมรับสัญญาณ Real-time")

    async def _load_history_for_buffer(
        self, client, symbol: str, tf: str, buffer: CandleBuffer
    ):
        """โหลดข้อมูลย้อนหลังสำหรับ 1 buffer"""
        candles = await client.fetch_historical_klines(
            symbol=symbol,
            interval=tf,
            limit=config.CANDLE_BUFFER_SIZE,
        )

        for candle in candles:
            await buffer.append(candle)

        logger.info(f"  {symbol} {tf}: โหลด {buffer.size} แท่ง | Ready: {buffer.is_ready}")

    async def update(self, candle: dict):
        """
        รับแท่งเทียนใหม่จาก WebSocket → ใส่ใน Buffer ที่ถูกต้อง
        
        เรียกจาก BinanceClient callback
        """
        symbol = candle["symbol"].upper()
        tf = candle["timeframe"]
        key = (symbol, tf)

        if key not in self._buffers:
            # เพิ่ม Symbol ใหม่ที่ไม่ได้อยู่ใน Config (Dynamic expansion)
            logger.warning(f"Buffer ไม่มีสำหรับ {key} — สร้างใหม่")
            self._buffers[key] = CandleBuffer(symbol, tf)

        await self._buffers[key].append(candle)

        logger.debug(
            f"📊 DataStore อัปเดต | {symbol} {tf} | "
            f"Size={self._buffers[key].size} | "
            f"Close={candle['close']:.2f}"
        )

    async def get_df(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """
        ดึง DataFrame สำหรับ (symbol, timeframe)
        
        Returns:
            pandas DataFrame พร้อม columns: open, high, low, close, volume
            Index เป็น DatetimeIndex (UTC)
        """
        key = (symbol.upper(), timeframe)
        buffer = self._buffers.get(key)

        if buffer is None:
            logger.warning(f"ไม่มี Buffer สำหรับ {key}")
            return pd.DataFrame()

        return await buffer.get_dataframe()

    def is_ready(self, symbol: str, timeframe: str) -> bool:
        """Buffer มีข้อมูลพอสำหรับคำนวณ Indicator หรือยัง"""
        key = (symbol.upper(), timeframe)
        buffer = self._buffers.get(key)
        return buffer.is_ready if buffer else False

    def latest_price(self, symbol: str) -> Optional[float]:
        """ราคาปิดล่าสุด (ใช้ Entry Timeframe)"""
        key = (symbol.upper(), config.ENTRY_TIMEFRAME)
        buffer = self._buffers.get(key)
        return buffer.latest_close() if buffer else None

    def status_report(self) -> dict:
        """รายงานสถานะ Buffer ทั้งหมด"""
        return {
            f"{sym}_{tf}": {
                "size": buf.size,
                "ready": buf.is_ready,
                "latest_close": buf.latest_close(),
            }
            for (sym, tf), buf in self._buffers.items()
        }


if __name__ == "__main__":
    """ทดสอบ DataStore โดยตรง"""
    import asyncio

    async def test():
        from data_engine.binance_client import BinanceClient

        client = BinanceClient()
        store = DataStore()

        await store.initialize_from_history(client)

        # ตรวจสอบผล
        report = store.status_report()
        print("\n=== DataStore Status ===")
        for key, info in report.items():
            print(f"  {key}: size={info['size']} | ready={info['ready']} | last_close={info['latest_close']}")

        # ดึง DataFrame
        df = await store.get_df("BTCUSDT", "15m")
        print(f"\nBTCUSDT 15m DataFrame shape: {df.shape}")
        print(df.tail(3)[["open", "high", "low", "close", "volume"]])

    asyncio.run(test())
