"""
SENTRY_TRADER — Telegram Bot Integration
==========================================
แจ้งเตือนแบบ Hybrid:
  - ทันที: เมื่อเปิดไม้ใหม่, ปิดไม้ตาย (Hit SL/TP), เกิด Error สำคัญ
  - สรุป: Daily Summary ทุกๆ เช้า (ใช้งานโดย import เข้าไปเรียกใน main หรือตั้ง Job loop)
"""

import aiohttp
from loguru import logger

import config

class TelegramNotifier:
    def __init__(self):
        self.bot_token = config.TELEGRAM_BOT_TOKEN
        self.chat_id   = config.TELEGRAM_CHAT_ID
        self.is_enabled = bool(self.bot_token and self.chat_id)

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """ส่งข้อความเข้า Telegram"""
        if not self.is_enabled:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, timeout=5) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        logger.error(f"Telegram API Error: {await resp.text()}")
                        return False
            except Exception as e:
                logger.error(f"Telegram Exception: {e}")
                return False

    async def notify_new_trade(self, trade):
        """แจ้งเตือนเมื่อเปิดไม้ใหม่"""
        emoji = "🟢" if trade.side == "LONG" else "🔴"
        text = (
            f"🚀 <b>SENTRY_TRADER | NEW POSITION</b>\n\n"
            f"{emoji} <b>{trade.symbol} | {trade.side}</b>\n"
            f"🔹 <b>Price:</b> {trade.entry_price:,.2f}\n"
            f"🔹 <b>Size:</b> {trade.quantity} BTC\n"
            f"🔹 <b>Stop Loss:</b> {trade.sl_price:,.2f}\n"
            f"🔹 <b>Take Profit:</b> {trade.tp_price:,.2f}\n"
            f"🤖 <b>AI Confidence:</b> {trade.ai_confidence:.1%}\n\n"
            f"<i>Order ID: #{trade.id}</i>"
        )
        return await self.send_message(text)

    async def notify_trade_closed(self, trade, exit_price: float, pnl_usdt: float, reason: str):
        """แจ้งเตือนเมื่อไม้โดนปิด"""
        if pnl_usdt > 0:
            emoji, title = "✅", "PROFIT HIT"
        else:
            emoji, title = "🛑", "STOP LOSS HIT"
            
        text = (
            f"{emoji} <b>SENTRY_TRADER | {title}</b>\n\n"
            f"<b>{trade.symbol} | {trade.side}</b>\n"
            f"🔹 <b>Exit Reason:</b> {reason}\n"
            f"🔹 <b>Entry:</b> {trade.entry_price:,.2f}\n"
            f"🔹 <b>Exit:</b> {exit_price:,.2f}\n"
            f"💰 <b>PnL:</b> {pnl_usdt:+.4f} USDT\n\n"
            f"<i>Order ID: #{trade.id}</i>"
        )
        return await self.send_message(text)

    async def notify_trailing_update(self, trade):
        """แจ้งเตือนเมื่อเลื่อน SL (Trailing)"""
        text = (
            f"🛡️ <b>SENTRY_TRADER | TRAILING STOP</b>\n\n"
            f"<b>{trade.symbol} | {trade.side}</b>\n"
            f"📈 <b>Price Peaked at:</b> {trade.highest_price_since_entry:,.2f}\n"
            f"🔒 <b>Moved Stop Loss to:</b> {trade.current_sl:,.2f}\n\n"
            f"<i>Secured better position!</i>"
        )
        return await self.send_message(text)

    async def send_daily_summary(self, total_trades: int, daily_pnl: float, balance: float):
        """สรุปยอดรายวัน"""
        emoji = "🔥" if daily_pnl > 0 else "☔"
        text = (
            f"📊 <b>SENTRY_TRADER | DAILY SUMMARY</b>\n\n"
            f"<b>Total Trades Today:</b> {total_trades}\n"
            f"<b>Daily PnL:</b> {daily_pnl:+.4f} USDT {emoji}\n"
            f"<b>Current Balance:</b> {balance:,.2f} USDT\n"
        )
        return await self.send_message(text)
