import asyncio
import aiohttp
import logging
import os
import sys
from datetime import datetime, timedelta
from telegram import Bot
from telegram.constants import ParseMode

# Get credentials from Railway environment variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8749443547:AAEXvMnpfO_sc1_GQxb2-xljA5Zz1NT5EZ4")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "7195135480")

CHECK_INTERVAL = 60  # Can check more often with Binance
VOLUME_SPIKE_THRESHOLD = 2.5  # 2.5x average volume
MIN_VOLUME_BTC = 10  # Minimum 10 BTC volume

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class VolumeAlertBot:
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.session = None
        self.volume_history = {}  # Store volume history per symbol
        self.alerted_tokens = set()
        
    async def start(self):
        self.session = aiohttp.ClientSession()
        
        # Test Telegram connection
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text="🚀 Bot is LIVE!\nMonitoring Binance BNB pairs every minute..."
            )
            logger.info("✅ Telegram connected")
        except Exception as e:
            logger.error(f"❌ Telegram failed: {e}")
            return
        
        logger.info("Starting monitoring...")
        
        while True:
            try:
                await self.check_binance_volume()
                await asyncio.sleep(CHECK_INTERVAL)
            except Exception as e:
                logger.error(f"Loop error: {e}")
                await asyncio.sleep(30)
    
    async def fetch_binance_ticker(self):
        """Fetch 24hr ticker data from Binance"""
        url = "https://api.binance.com/api/v3/ticker/24hr"
        
        try:
            async with self.session.get(url, timeout=30) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.warning(f"Binance HTTP {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"Binance fetch error: {e}")
            return None
    
    async def check_binance_volume(self):
        """Check BNB pairs on Binance for volume spikes"""
        data = await self.fetch_binance_ticker()
        
        if not data:
            logger.error("No data from Binance")
            return
        
        # Filter for BNB pairs only (e.g., BTCBNB, ETHBNB, etc.)
        bnb_pairs = [t for t in data if t['symbol'].endswith('BNB')]
        
        logger.info(f"Checking {len(bnb_pairs)} BNB pairs...")
        
        for ticker in bnb_pairs:
            try:
                await self.analyze_ticker(ticker)
            except Exception as e:
                continue
    
    async def analyze_ticker(self, ticker):
        """Analyze individual ticker"""
        symbol = ticker['symbol']
        base_asset = symbol.replace('BNB', '')
        
        # Skip if not a real trading pair (BNB/BNB or too short)
        if len(base_asset) < 2:
            return
        
        try:
            volume = float(ticker['volume'])
            quote_volume = float(ticker['quoteVolume'])  # Volume in BNB
            price = float(ticker['lastPrice'])
            price_change = float(ticker['priceChangePercent'])
            
            # Skip low volume pairs (less than 10 BNB volume)
            if quote_volume < MIN_VOLUME_BTC:
                return
                
        except (ValueError, TypeError):
            return
        
        # Track volume history
        if symbol not in self.volume_history:
            self.volume_history[symbol] = []
        
        history = self.volume_history[symbol]
        history.append(quote_volume)
        
        # Keep last 10 readings
        if len(history) > 10:
            history.pop(0)
        
        # Need at least 3 data points
        if len(history) < 3:
            return
        
        # Calculate average volume (excluding current)
        avg_volume = sum(history[:-1]) / len(history[:-1])
        
        if avg_volume > 0:
            ratio = quote_volume / avg_volume
            
            # Check for volume spike
            if ratio >= VOLUME_SPIKE_THRESHOLD:
                alert_key = f"{symbol}_{datetime.now().hour}"
                if alert_key not in self.alerted_tokens:
                    await self.send_alert(
                        base_asset, symbol, quote_volume, ratio, 
                        price, price_change
                    )
                    self.alerted_tokens.add(alert_key)
                    
                    # Cleanup old alerts
                    if len(self.alerted_tokens) > 100:
                        self.alerted_tokens.pop()
    
    async def send_alert(self, asset, symbol, volume, ratio, price, price_change):
        """Send Telegram alert"""
        
        # Determine if bullish or bearish
        if price_change > 5:
            trend = "📈 STRONG UP"
        elif price_change > 0:
            trend = "📈 Up"
        elif price_change < -5:
            trend = "📉 STRONG DOWN"
        else:
            trend = "📉 Down"
        
        message = f"""
🚨 *BNB CHAIN VOLUME ALERT*

🔹 *{asset}/BNB*
💰 Price: {price:.8f} BNB
📊 24h Change: {price_change:+.2f}% {trend}
💧 Volume: {volume:.2f} BNB ({ratio:.1f}x average)

🔗 [Trade on Binance](https://www.binance.com/en/trade/{symbol})

⏰ {datetime.now().strftime('%H:%M:%S')}
⚠️ *DYOR - Not financial advice*
"""
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            logger.info(f"Alert sent: {asset}")
        except Exception as e:
            logger.error(f"Send failed: {e}")

if __name__ == "__main__":
    bot = VolumeAlertBot()
    asyncio.run(bot.start())