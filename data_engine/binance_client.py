"""
SENTRY_TRADER — Binance Client
================================
รองรับทั้ง REST API (ดึงข้อมูลย้อนหลัง) และ WebSocket (Real-time Stream)
ออกแบบให้รองรับ Multi-symbol ตั้งแต่ต้น

Architecture:
  BinanceClient
    ├── fetch_historical_klines()  — REST: ดึง OHLCV ย้อนหลัง
    ├── get_account_balance()      — REST: ดู Balance ใน Testnet/Live
    ├── start_stream()             — WebSocket: เปิด real-time stream
    └── stop_stream()             — หยุด WebSocket อย่างปลอดภัย
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import aiohttp
import websockets
from loguru import logger

import config


class BinanceClient:
    """
    Binance Futures Client — รองรับ Testnet และ Live
    
    ใช้งาน:
        client = BinanceClient()
        
        # ดึง historical data
        df = await client.fetch_historical_klines("BTCUSDT", "15m", limit=500)
        
        # เปิด real-time stream
        await client.start_stream(["BTCUSDT"], callback=my_handler)
    """

    def __init__(self):
        self.rest_url = config.ACTIVE_REST_URL
        self.ws_url = config.ACTIVE_WS_URL
        self.api_key = config.BINANCE_API_KEY
        self.api_secret = config.BINANCE_API_SECRET
        self.mode = config.TRADING_MODE

        self._ws_connection: Optional[websockets.WebSocketClientProtocol] = None
        self._stream_task: Optional[asyncio.Task] = None
        self._running = False

        logger.info(f"BinanceClient เริ่มต้น | Mode: {self.mode.upper()} | URL: {self.rest_url}")

    # ----------------------------------------------------------
    # REST API — Historical Data
    # ----------------------------------------------------------

    async def fetch_historical_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> list[dict]:
        """
        ดึงข้อมูล OHLCV ย้อนหลังจาก REST API
        
        Args:
            symbol:     เช่น "BTCUSDT"
            interval:   เช่น "15m", "4h", "1d"
            limit:      จำนวนแท่งเทียน (สูงสุด 1500)
            start_time: Timestamp milliseconds (optional)
            end_time:   Timestamp milliseconds (optional)
        
        Returns:
            List of candle dicts: {open_time, open, high, low, close, volume, ...}
        """
        endpoint = f"{self.rest_url}/fapi/v1/klines"
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1500),  # Binance max limit
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        logger.debug(f"ดึง Historical Klines | {symbol} | {interval} | limit={limit}")

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(endpoint, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"Binance REST Error {resp.status}: {error_text}")
                        return []

                    raw_data = await resp.json()
                    candles = self._parse_klines(raw_data)
                    logger.info(f"✅ ดึง Klines สำเร็จ | {symbol} {interval} | {len(candles)} แท่ง")
                    return candles

            except aiohttp.ClientError as e:
                logger.error(f"Connection Error ดึง Klines: {e}")
                return []
            except asyncio.TimeoutError:
                logger.error("Timeout ดึง Klines จาก Binance")
                return []

    def _parse_klines(self, raw: list) -> list[dict]:
        """แปลง Raw Binance Response → Dict ที่อ่านง่าย"""
        candles = []
        for k in raw:
            candles.append({
                "open_time":       k[0],                          # Timestamp (ms)
                "open":            float(k[1]),
                "high":            float(k[2]),
                "low":             float(k[3]),
                "close":           float(k[4]),
                "volume":          float(k[5]),
                "close_time":      k[6],
                "quote_volume":    float(k[7]),
                "trades":          int(k[8]),
                "taker_buy_vol":   float(k[9]),
                "datetime":        datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
            })
        return candles

    async def _signed_request(self, method: str, endpoint: str, params: dict = None) -> dict | list | None:
        """Helper function สำหรับยิง API ที่เข้ารหัส HMAC SHA256"""
        if not self.api_key or not self.api_secret:
            logger.warning("ไม่มี API Key/Secret — ไม่สามารถทำ Signed Request ได้")
            return None

        if params is None:
            params = {}

        params["timestamp"] = int(time.time() * 1000)
        
        import hmac
        import hashlib
        query = "&".join(f"{k}={v}" for k, v in params.items())
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature

        headers = {"X-MBX-APIKEY": self.api_key}

        async with aiohttp.ClientSession() as session:
            try:
                request_func = getattr(session, method.lower())
                async with request_func(
                    endpoint, headers=headers, params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status not in (200, 201):
                        error_text = await resp.text()
                        logger.error(f"Binance API Error [{resp.status}]: {error_text}")
                        return {"error": error_text, "status": resp.status}

                    return await resp.json()

            except Exception as e:
                logger.error(f"Error {method} {endpoint}: {e}")
                return None

    async def get_account_balance(self) -> dict[str, float]:
        """
        ดู Balance ใน Futures Account
        Returns: {"USDT": 100.0, ...}
        """
        endpoint = f"{self.rest_url}/fapi/v2/balance"
        data = await self._signed_request("GET", endpoint)
        
        if not isinstance(data, list):
            return {}

        balances = {
            item["asset"]: float(item["balance"])
            for item in data
            if float(item["balance"]) > 0
        }
        logger.info(f"💰 Balance: {balances}")
        return balances

    # ----------------------------------------------------------
    # Trading Actions (Phase 4)
    # ----------------------------------------------------------

    async def set_leverage(self, symbol: str, leverage: int):
        """ตั้งค่า Leverage"""
        endpoint = f"{self.rest_url}/fapi/v1/leverage"
        params = {"symbol": symbol, "leverage": leverage}
        res = await self._signed_request("POST", endpoint, params)
        if isinstance(res, dict) and "leverage" in res:
            logger.success(f"⚙️ ตั้งค่า Leverage สำเร็จ | {symbol} -> {res['leverage']}x")
        return res

    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED"):
        """ตั้งค่า Margin Type (ISOLATED / CROSSED)"""
        endpoint = f"{self.rest_url}/fapi/v1/marginType"
        params = {"symbol": symbol, "marginType": margin_type}
        res = await self._signed_request("POST", endpoint, params)
        
        if isinstance(res, dict) and res.get("status") in (400, 401, 200) and "error" in res:
            # Code -4046 = No need to change margin type (Already isolated)
            if "-4046" in res["error"]:
                logger.info(f"⚙️ Margin Type ปัจจุบันเป็น {margin_type} อยู่แล้ว")
                return True
            return False

        logger.success(f"⚙️ ตั้งค่า Margin Type สำเร็จ | {symbol} -> {margin_type}")
        return True

    async def create_order(
        self, symbol: str, side: str, order_type: str, 
        quantity: float, price: float = None, stop_price: float = None,
        close_position: bool = False, reduce_only: bool = False
    ) -> dict:
        """ส่งคำสั่งซื้อขาย / ตั้ง SL, TP"""
        endpoint = f"{self.rest_url}/fapi/v1/order"
        params = {
            "symbol": symbol,
            "side": side.upper(),       # BUY, SELL
            "type": order_type.upper(), # MARKET, STOP_MARKET, TAKE_PROFIT_MARKET
        }
        
        # ถ้าระบุว่าปิดสถานะ (ป้องกันเกิด Order กำพร้า)
        if close_position:
            params["closePosition"] = "true"
        elif reduce_only:
            params["reduceOnly"] = "true"
        else:
            params["quantity"] = str(quantity)

        if price:
            params["price"] = str(price)
        if stop_price:
            params["stopPrice"] = str(stop_price)

        logger.info(f"📤 ส่ง Order: {side} {order_type} | Qty={quantity} | Stop={stop_price}")
        return await self._signed_request("POST", endpoint, params)

    async def cancel_order(self, symbol: str, order_id: int):
        """ยกเลิก Order (สำหรับขยับ Trailing Stop)"""
        endpoint = f"{self.rest_url}/fapi/v1/order"
        params = {"symbol": symbol, "orderId": order_id}
        logger.info(f"🗑️ กำลังยกเลิก Order ID: {order_id}")
        return await self._signed_request("DELETE", endpoint, params)

    async def fetch_open_positions(self, symbol: str) -> list[dict]:
        """ดึงรายการ Position ที่เปิดอยู่จริง (ใช้สำหรับ State Reconciliation)"""
        endpoint = f"{self.rest_url}/fapi/v2/positionRisk"
        params = {"symbol": symbol}
        data = await self._signed_request("GET", endpoint, params)
        
        if isinstance(data, list):
            # return เฉพาะไม้ที่มี positionAmount != 0
            open_pos = [p for p in data if float(p.get("positionAmt", 0)) != 0]
            return open_pos
        return []

    async def start_stream(
        self,
        symbols: list[str],
        callback: Callable[[dict], None],
        intervals: Optional[list[str]] = None,
    ):
        """
        เปิด WebSocket stream สำหรับหลาย Symbol พร้อมกัน
        
        Args:
            symbols:   เช่น ["BTCUSDT", "ETHUSDT"]
            callback:  ฟังก์ชันที่รับข้อมูลแต่ละแท่งเทียนที่ปิดสำเร็จ
            intervals: เช่น ["15m", "4h"] — default ใช้จาก config
        
        Callback signature:
            def my_handler(candle: dict):
                # candle = {symbol, timeframe, open, high, low, close, volume, ...}
        """
        if intervals is None:
            intervals = [config.ENTRY_TIMEFRAME, config.TREND_TIMEFRAME]

        # สร้าง stream names: btcusdt@kline_15m, btcusdt@kline_4h, ...
        streams = []
        for symbol in symbols:
            for interval in intervals:
                streams.append(f"{symbol.lower()}@kline_{interval}")

        stream_path = "/".join(streams)
        ws_endpoint = f"{self.ws_url}/stream?streams={stream_path}"

        logger.info(f"🔌 เชื่อมต่อ WebSocket | Streams: {streams}")
        self._running = True

        while self._running:
            try:
                async with websockets.connect(
                    ws_endpoint,
                    ping_interval=20,    # ส่ง ping ทุก 20 วิ เพื่อ Keep alive
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws_connection = ws
                    logger.success(f"✅ WebSocket เชื่อมต่อสำเร็จ!")

                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_ws_message(message, callback)

            except websockets.exceptions.ConnectionClosed as e:
                if self._running:
                    logger.warning(f"WebSocket ปิดโดยไม่คาดคิด: {e} — กำลัง Reconnect ใน 5 วิ...")
                    await asyncio.sleep(5)
            except Exception as e:
                if self._running:
                    logger.error(f"WebSocket Error: {e} — Reconnect ใน 10 วิ...")
                    await asyncio.sleep(10)

        logger.info("WebSocket หยุดทำงานเรียบร้อย")

    async def _handle_ws_message(self, raw_message: str, callback: Callable):
        """Parse WebSocket message และเรียก callback เมื่อแท่งเทียนปิดสำเร็จ"""
        try:
            msg = json.loads(raw_message)

            # Combined stream format: {"stream": "btcusdt@kline_15m", "data": {...}}
            if "data" in msg:
                data = msg["data"]
            else:
                data = msg

            if data.get("e") != "kline":
                return

            kline = data["k"]

            # ✅ เฉพาะแท่งเทียนที่ "ปิดแล้ว" เพื่อป้องกัน False Signal
            if not kline.get("x", False):
                return

            candle = {
                "symbol":     kline["s"],
                "timeframe":  kline["i"],
                "open_time":  kline["t"],
                "open":       float(kline["o"]),
                "high":       float(kline["h"]),
                "low":        float(kline["l"]),
                "close":      float(kline["c"]),
                "volume":     float(kline["v"]),
                "close_time": kline["T"],
                "trades":     kline["n"],
                "datetime":   datetime.fromtimestamp(kline["t"] / 1000, tz=timezone.utc),
            }

            logger.debug(
                f"🕯️ แท่งปิด | {candle['symbol']} {candle['timeframe']} | "
                f"C={candle['close']:.2f} V={candle['volume']:.0f}"
            )

            # ส่งให้ callback (Signal Engine จะ handle ต่อ)
            if asyncio.iscoroutinefunction(callback):
                await callback(candle)
            else:
                callback(candle)

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Parse WebSocket message error: {e} | raw={raw_message[:100]}")

    async def stop_stream(self):
        """หยุด WebSocket อย่างปลอดภัย"""
        logger.info("กำลังหยุด WebSocket Stream...")
        self._running = False

        if self._ws_connection:
            try:
                await self._ws_connection.close()
            except Exception:
                pass
            self._ws_connection = None

        logger.info("WebSocket หยุดแล้ว")


# ----------------------------------------------------------
# Quick Test (รันไฟล์นี้โดยตรง)
# ----------------------------------------------------------
async def _test():
    """ทดสอบการเชื่อมต่อ Binance"""
    client = BinanceClient()

    print("\n=== ทดสอบ REST API — Historical Klines ===")
    candles = await client.fetch_historical_klines("BTCUSDT", "15m", limit=5)
    if candles:
        latest = candles[-1]
        print(f"แท่งล่าสุด: {latest['datetime']} | O={latest['open']} H={latest['high']} L={latest['low']} C={latest['close']}")

    print("\n=== ทดสอบ WebSocket — Real-time Stream (30 วิ) ===")

    count = 0
    async def on_candle(candle: dict):
        nonlocal count
        count += 1
        print(f"[{count}] แท่งปิด: {candle['symbol']} {candle['timeframe']} | Close={candle['close']}")

    # เปิด stream 30 วิแล้วหยุด
    stream_task = asyncio.create_task(
        client.start_stream([config.PRIMARY_SYMBOL], callback=on_candle)
    )
    await asyncio.sleep(30)
    await client.stop_stream()
    stream_task.cancel()
    print(f"\n✅ ได้รับ {count} แท่งเทียน ใน 30 วินาที")


if __name__ == "__main__":
    from loguru import logger
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level="DEBUG")
    asyncio.run(_test())
