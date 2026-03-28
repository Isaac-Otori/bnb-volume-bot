import asyncio
import aiohttp
import logging
from datetime import datetime, timedelta
from telegram import Bot
from telegram.constants import ParseMode

# ================= CONFIGURE THESE =================
TELEGRAM_BOT_TOKEN = "8749443547:AAEXvMnpfO_sc1_GQxb2-xljA5Zz1NT5EZ4"
TELEGRAM_CHAT_ID = "7195135480"
# ===================================================

CHECK_INTERVAL = 60  # Check every 60 seconds
VOLUME_SPIKE_THRESHOLD = 3.0  # Alert when volume is 3x normal
MIN_VOLUME_USD = 50000  # Ignore coins under $50k volume

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class VolumeAlertBot:
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.session = None
        self.volume_history = {}
        self.alerted_tokens = set()
        
    async def start(self):
        self.session = aiohttp.ClientSession()
        logger.info("🚀 Bot Started! Monitoring BNB Chain...")
        
        # Send test message
        await self.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="✅ Bot is live! Monitoring for volume spikes..."
        )
        
        while True:
            try:
                await self.check_volume()
                await asyncio.sleep(CHECK_INTERVAL)
            except Exception as e:
                logger.error(f"Error: {e}")
                await asyncio.sleep(10)
    
    async def check_volume(self):
        """Check BNB Chain for volume spikes"""
        # Using DexScreener API (free, no key needed)
        url = "https://api.dexscreener.com/latest/dexes/pancakeswapv2"
        
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return
                    
                data = await response.json()
                pairs = data.get('pairs', [])
                
                for pair in pairs:
                    await self.analyze_pair(pair)
                    
        except Exception as e:
            logger.error(f"API Error: {e}")
    
    async def analyze_pair(self, pair):
        """Analyze individual trading pair"""
        # Skip if not BNB Chain
        if pair.get('chainId') != 'bsc':
            return
            
        pair_address = pair['pairAddress']
        token_symbol = pair['baseToken']['symbol']
        token_name = pair['baseToken']['name']
        
        # Get metrics
        try:
            volume_24h = float(pair.get('volumeUsd24h', 0))
            liquidity = float(pair.get('liquidityUsd', 0))
            price = float(pair.get('priceUsd', 0))
            
            # Skip low volume coins
            if volume_24h < MIN_VOLUME_USD:
                return
                
        except:
            return
        
        # Track history
        if pair_address not in self.volume_history:
            self.volume_history[pair_address] = []
        
        history = self.volume_history[pair_address]
        history.append(volume_24h)
        
        # Keep last 10 readings (10 minutes)
        if len(history) > 10:
            history.pop(0)
        
        # Need 3 readings minimum
        if len(history) < 3:
            return
        
        # Calculate average (excluding current)
        avg_volume = sum(history[:-1]) / len(history[:-1])
        
        # Check for spike
        if avg_volume > 0:
            ratio = volume_24h / avg_volume
            
            if ratio >= VOLUME_SPIKE_THRESHOLD:
                # Prevent spam (alert once per hour per token)
                last_alert_key = f"{pair_address}_{datetime.now().hour}"
                if last_alert_key not in self.alerted_tokens:
                    await self.send_alert(
                        token_symbol, token_name, volume_24h, 
                        ratio, price, liquidity, pair_address
                    )
                    self.alerted_tokens.add(last_alert_key)
                    
                    # Cleanup old alerts (keep last 100)
                    if len(self.alerted_tokens) > 100:
                        self.alerted_tokens.pop()
    
    async def send_alert(self, symbol, name, volume, ratio, price, liquidity, address):
        """Send Telegram alert"""
        message = f"""
🚨 *VOLUME SPIKE ALERT*

🔹 *{symbol}* ({name})
💰 Price: ${price:.6f}
📊 Volume: ${volume:,.0f} ({ratio:.1f}x normal)
💧 Liquidity: ${liquidity:,.0f}

🔗 [View Chart](https://dexscreener.com/bsc/{address})
🥞 [Buy Now](https://pancakeswap.finance/swap?outputCurrency={address})

⏰ {datetime.now().strftime('%H:%M:%S')}
⚠️ *DYOR - Not financial advice*
"""
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False
            )
            logger.info(f"Alert sent: {symbol}")
        except Exception as e:
            logger.error(f"Failed to send: {e}")

if __name__ == "__main__":
    bot = VolumeAlertBot()
    asyncio.run(bot.start())