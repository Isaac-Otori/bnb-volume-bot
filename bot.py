import asyncio
import aiohttp
import logging
from datetime import datetime, timedelta
from telegram import Bot
from telegram.constants import ParseMode
import random

# ================= CONFIGURE THESE =================
TELEGRAM_BOT_TOKEN = "8749443547:AAEXvMnpfO_sc1_GQxb2-xljA5Zz1NT5EZ4"
TELEGRAM_CHAT_ID = "7195135480"
# ===================================================

# Anti-scam protection
SCAM_PATTERNS = [
    'honeypot', 'rug', 'scam', 'elon', 'musk', 'trump', 'pumpit', 
    'moonshot', '1000x', 'guarantee', 'no loss', 'safe', 'baby', 'floki'
]

CHECK_INTERVAL = 120  # INCREASED to 2 minutes to avoid rate limits
VOLUME_SPIKE_THRESHOLD = 3.0
MIN_VOLUME_USD = 50000
WHALE_THRESHOLD = 0.5  # Volume > 50% of liquidity

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
        self.whale_alerted = set()
        self.last_api_data = None  # Cache API data
        
    async def is_safe_token(self, pair_address: str, token_symbol: str, token_name: str = "") -> bool:
        """Check if token is likely a scam"""
        combined_text = f"{token_symbol} {token_name}".lower()
        
        if any(pattern in combined_text for pattern in SCAM_PATTERNS):
            logger.info(f"Filtered {token_symbol} - scam keyword")
            return False
            
        if len(token_name) > 40:
            return False
            
        return True
        
    async def start(self):
        self.session = aiohttp.ClientSession()
        logger.info("🚀 Bot Started! Monitoring BNB Chain...")
        
        await self.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="✅ Bot is live! Anti-scam protection enabled. Checking every 2 minutes."
        )
        
        while True:
            try:
                await self.check_volume()
                await asyncio.sleep(CHECK_INTERVAL)
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                await asyncio.sleep(60)  # Wait longer on error
    
    async def fetch_dex_data(self):
        """Fetch data with retry logic"""
        url = "https://api.dexscreener.com/latest/dexes/pancakeswapv2"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        for attempt in range(3):  # Try 3 times
            try:
                async with self.session.get(url, headers=headers, timeout=30) as response:
                    if response.status == 200:
                        content_type = response.headers.get('content-type', '')
                        if 'application/json' in content_type:
                            return await response.json()
                        else:
                            logger.warning(f"Unexpected content type: {content_type}")
                            await asyncio.sleep(5)
                    else:
                        logger.warning(f"HTTP {response.status}, waiting...")
                        await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(5)
        
        return None
    
    async def check_volume(self):
        """Check BNB Chain for volume spikes and whales in one call"""
        data = await self.fetch_dex_data()
        
        if not data:
            logger.error("Failed to fetch data after 3 attempts")
            return
            
        self.last_api_data = data  # Cache for potential reuse
        pairs = data.get('pairs', [])
        
        logger.info(f"Checking {len(pairs)} pairs...")
        
        for pair in pairs:
            try:
                await self.analyze_pair(pair)
            except Exception as e:
                logger.error(f"Error analyzing pair: {e}")
                continue
    
    async def analyze_pair(self, pair):
        """Analyze individual trading pair"""
        if pair.get('chainId') != 'bsc':
            return
            
        pair_address = pair['pairAddress']
        token_symbol = pair['baseToken']['symbol']
        token_name = pair['baseToken']['name']
        
        try:
            volume_24h = float(pair.get('volumeUsd24h', 0))
            liquidity = float(pair.get('liquidityUsd', 0))
            price = float(pair.get('priceUsd', 0))
            
            if volume_24h < MIN_VOLUME_USD:
                return
                
        except (ValueError, TypeError):
            return
        
        # Check for whale activity first (volume > 50% of liquidity)
        if liquidity > 100000 and volume_24h > (liquidity * WHALE_THRESHOLD):
            await self.check_whale_alert(token_symbol, volume_24h, liquidity, pair_address)
        
        # Track history for volume spike detection
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
            
            if ratio >= VOLUME_SPIKE_THRESHOLD and await self.is_safe_token(pair_address, token_symbol, token_name):
                last_alert_key = f"{pair_address}_{datetime.now().hour}"
                if last_alert_key not in self.alerted_tokens:
                    await self.send_alert(
                        token_symbol, token_name, volume_24h, 
                        ratio, price, liquidity, pair_address
                    )
                    self.alerted_tokens.add(last_alert_key)
                    
                    if len(self.alerted_tokens) > 100:
                        self.alerted_tokens.pop()
    
    async def check_whale_alert(self, symbol: str, volume: float, liquidity: float, address: str):
        """Send whale movement alert (with cooldown)"""
        alert_key = f"whale_{address}_{datetime.now().hour}"
        
        if alert_key in self.whale_alerted:
            return
            
        self.whale_alerted.add(alert_key)
        
        # Cleanup old alerts
        if len(self.whale_alerted) > 50:
            self.whale_alerted.pop()
        
        message = f"""
🐋 *WHALE ACTIVITY*

🔹 *{symbol}*
💰 Volume: ${volume:,.0f}
🌊 Liquidity: ${liquidity:,.0f}
📊 {(volume/liquidity)*100:.1f}% of liquidity traded!

Big money is moving!

🔗 [Chart](https://dexscreener.com/bsc/{address})
⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
            logger.info(f"Whale alert sent: {symbol}")
        except Exception as e:
            logger.error(f"Whale alert failed: {e}")
    
    async def send_alert(self, symbol, name, volume, ratio, price, liquidity, address):
        """Send Telegram alert with entry analysis"""
        liquidity_ratio = liquidity / volume if volume > 0 else 0
        
        if liquidity_ratio < 0.1:
            entry_quality = "⚠️ RISKY - Low liquidity"
            emoji = "🚨"
        elif liquidity_ratio < 0.3:
            entry_quality = "⚡ MODERATE - Check chart"
            emoji = "⚡"
        else:
            entry_quality = "✅ GOOD - Healthy"
            emoji = "🎯"
        
        message = f"""
{emoji} *VOLUME SPIKE ALERT*

🔹 *{symbol}* ({name})
💰 Price: ${price:.6f}
📊 Volume: ${volume:,.0f} ({ratio:.1f}x avg)
💧 Liquidity: ${liquidity:,.0f}
📈 Quality: {entry_quality}

🔗 [Chart](https://dexscreener.com/bsc/{address})
🥞 [Buy](https://pancakeswap.finance/swap?outputCurrency={address})
📋 [Contract](https://bscscan.com/address/{address})

⏰ {datetime.now().strftime('%H:%M:%S')}
⚠️ *DYOR*
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