import asyncio
import aiohttp
import logging
import os
import sys
from datetime import datetime, timedelta
from telegram import Bot
from telegram.constants import ParseMode

# Get from Railway environment variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# If not found (running locally), use these fallback values
if not TELEGRAM_BOT_TOKEN:
    TELEGRAM_BOT_TOKEN = "8749443547:AAEXvMnpfO_sc1_GQxb2-xljA5Zz1NT5EZ4"  # Replace when testing locally
    
if not TELEGRAM_CHAT_ID:
    TELEGRAM_CHAT_ID = "7195135480"  # Replace when testing locally

# Validate credentials
if TELEGRAM_BOT_TOKEN == "8749443547:AAEXvMnpfO_sc1_GQxb2-xljA5Zz1NT5EZ4" or TELEGRAM_CHAT_ID == "7195135480":
    if not os.environ.get("RAILWAY_ENVIRONMENT"):
        print("⚠️  WARNING: Using placeholder credentials. Set your real token and chat ID!")
        sys.exit(1)

CHECK_INTERVAL = 120  # 2 minutes to avoid rate limits
VOLUME_SPIKE_THRESHOLD = 3.0
MIN_VOLUME_USD = 50000

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class VolumeAlertBot:
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.session = None
        self.volume_history = {}
        self.alerted_tokens = set()
        self.whale_alerted = set()
        
    async def start(self):
        self.session = aiohttp.ClientSession()
        
        # Test Telegram connection first
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text="🚀 Bot is now LIVE on Railway!\nMonitoring BNB Chain every 2 minutes..."
            )
            logger.info("✅ Telegram connection successful")
        except Exception as e:
            logger.error(f"❌ Telegram failed: {e}")
            logger.error("Check your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
            return
        
        logger.info("Starting monitoring loop...")
        
        while True:
            try:
                await self.check_volume()
                logger.info("Check complete, sleeping 2 minutes...")
                await asyncio.sleep(CHECK_INTERVAL)
            except Exception as e:
                logger.error(f"Error in loop: {e}")
                await asyncio.sleep(60)
    
    async def fetch_dex_data(self):
        """Fetch with retry logic"""
        url = "https://api.dexscreener.com/latest/dexes/pancakeswapv2"
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; Bot/1.0)'}
        
        for attempt in range(3):
            try:
                async with self.session.get(url, headers=headers, timeout=30) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    logger.warning(f"HTTP {resp.status}, retrying...")
                    await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Attempt {attempt+1} failed: {e}")
                await asyncio.sleep(5)
        return None
    
    async def check_volume(self):
        data = await self.fetch_dex_data()
        if not data:
            logger.error("Could not fetch data")
            return
        
        pairs = data.get('pairs', [])
        logger.info(f"Analyzing {len(pairs)} pairs...")
        
        for pair in pairs:
            try:
                await self.analyze_pair(pair)
            except Exception as e:
                continue
    
    async def analyze_pair(self, pair):
        if pair.get('chainId') != 'bsc':
            return
            
        pair_address = pair.get('pairAddress')
        token_symbol = pair['baseToken'].get('symbol', 'Unknown')
        token_name = pair['baseToken'].get('name', 'Unknown')
        
        try:
            volume_24h = float(pair.get('volumeUsd24h', 0))
            liquidity = float(pair.get('liquidityUsd', 0))
            price = float(pair.get('priceUsd', 0))
            
            if volume_24h < MIN_VOLUME_USD:
                return
        except:
            return
        
        # Whale check
        if liquidity > 100000 and volume_24h > liquidity * 0.5:
            await self.check_whale_alert(token_symbol, volume_24h, liquidity, pair_address)
        
        # Volume spike check
        if pair_address not in self.volume_history:
            self.volume_history[pair_address] = []
        
        history = self.volume_history[pair_address]
        history.append(volume_24h)
        
        if len(history) > 10:
            history.pop(0)
        
        if len(history) < 3:
            return
        
        avg_volume = sum(history[:-1]) / len(history[:-1])
        if avg_volume > 0:
            ratio = volume_24h / avg_volume
            if ratio >= VOLUME_SPIKE_THRESHOLD:
                alert_key = f"{pair_address}_{datetime.now().hour}"
                if alert_key not in self.alerted_tokens:
                    await self.send_alert(token_symbol, token_name, volume_24h, ratio, price, liquidity, pair_address)
                    self.alerted_tokens.add(alert_key)
    
    async def check_whale_alert(self, symbol, volume, liquidity, address):
        alert_key = f"whale_{address}_{datetime.now().hour}"
        if alert_key in self.whale_alerted:
            return
        
        self.whale_alerted.add(alert_key)
        
        msg = f"""🐋 *WHALE ACTIVITY*

🔹 *{symbol}*
💰 Volume: ${volume:,.0f}
🌊 Liquidity: ${liquidity:,.0f}
📊 {(volume/liquidity)*100:.1f}% ratio

🔗 [Chart](https://dexscreener.com/bsc/{address})
⏰ {datetime.now().strftime('%H:%M:%S')}"""
        
        try:
            await self.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
        except:
            pass
    
    async def send_alert(self, symbol, name, volume, ratio, price, liquidity, address):
        liq_ratio = liquidity / volume if volume > 0 else 0
        
        if liq_ratio < 0.1:
            quality, emoji = "⚠️ RISKY", "🚨"
        elif liq_ratio < 0.3:
            quality, emoji = "⚡ MODERATE", "⚡"
        else:
            quality, emoji = "✅ GOOD", "🎯"
        
        msg = f"""{emoji} *VOLUME SPIKE*

🔹 *{symbol}* ({name})
💰 ${price:.6f} | Vol: ${volume:,.0f} ({ratio:.1f}x)
💧 Liq: ${liquidity:,.0f}
📈 {quality}

🔗 [Chart](https://dexscreener.com/bsc/{address})
🥞 [Buy](https://pancakeswap.finance/swap?outputCurrency={address})
📋 [Contract](https://bscscan.com/address/{address})

⏰ {datetime.now().strftime('%H:%M:%S')}"""
        
        try:
            await self.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"Alert: {symbol}")
        except Exception as e:
            logger.error(f"Send failed: {e}")

if __name__ == "__main__":
    bot = VolumeAlertBot()
    asyncio.run(bot.start())