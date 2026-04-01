import asyncio
import aiohttp
import logging
import os
import random
import time
from datetime import datetime, timedelta
from telegram import Bot
from telegram.constants import ParseMode

# Get credentials
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8749443547:AAEXvMnpfO_sc1_GQxb2-xljA5Zz1NT5EZ4")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "7195135480")

# Settings - slower to avoid rate limits
CHECK_INTERVAL = 300  # 5 minutes between checks
VOLUME_SPIKE_THRESHOLD = 3.0
MIN_VOLUME_USD = 50000

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Rotate user agents to look less like a bot
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 14_7_1 like Mac OS X) AppleWebKit/605.1.15'
]

class VolumeAlertBot:
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.session = None
        self.volume_history = {}
        self.alerted_tokens = set()
        
    async def start(self):
        # Random delay on startup so all Railway users don't hit at same time
        await asyncio.sleep(random.randint(10, 60))
        
        self.session = aiohttp.ClientSession()
        
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text="🚀 Bot LIVE!\nMonitoring BSC USD pairs (checking every 5 min)..."
            )
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return
        
        while True:
            try:
                await self.check_bsc_pairs()
                # Add random jitter to check interval
                sleep_time = CHECK_INTERVAL + random.randint(0, 120)
                logger.info(f"Sleeping {sleep_time} seconds...")
                await asyncio.sleep(sleep_time)
            except Exception as e:
                logger.error(f"Main error: {e}")
                await asyncio.sleep(60)
    
    async def fetch_with_retry(self, url, max_retries=5):
        """Fetch with multiple retries and different user agents"""
        for attempt in range(max_retries):
            headers = {
                'User-Agent': random.choice(USER_AGENTS),
                'Accept': 'application/json',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://dexscreener.com/',
            }
            
            try:
                # Exponential backoff: wait longer between retries
                if attempt > 0:
                    wait_time = (2 ** attempt) + random.randint(1, 5)
                    logger.info(f"Retry {attempt}, waiting {wait_time}s...")
                    await asyncio.sleep(wait_time)
                
                async with self.session.get(url, headers=headers, timeout=30) as resp:
                    if resp.status == 200:
                        content_type = resp.headers.get('content-type', '')
                        if 'json' in content_type:
                            return await resp.json()
                        else:
                            text = await resp.text()
                            if text.startswith('{'):
                                # Might be JSON anyway
                                import json
                                return json.loads(text)
                            logger.warning(f"Got HTML instead of JSON")
                            continue
                    elif resp.status == 429:  # Rate limited
                        logger.warning("Rate limited, backing off...")
                        await asyncio.sleep(60)
                    else:
                        logger.warning(f"HTTP {resp.status}")
                        
            except Exception as e:
                logger.error(f"Attempt {attempt+1} error: {e}")
                continue
        
        return None
    
    async def check_bsc_pairs(self):
        """Check BSC pairs with USD stables"""
        # Try multiple endpoints
        urls = [
            "https://api.dexscreener.com/latest/dexes/pancakeswapv2",
            "https://api.dexscreener.com/latest/dexes/biswap",
            "https://api.dexscreener.com/latest/dexes/apeswap"
        ]
        
        all_pairs = []
        
        for url in urls:
            data = await self.fetch_with_retry(url)
            if data and 'pairs' in data:
                all_pairs.extend(data['pairs'])
                # Wait between API calls
                await asyncio.sleep(2)
        
        if not all_pairs:
            logger.error("No data from any DEX")
            return
        
        logger.info(f"Got {len(all_pairs)} total pairs")
        
        # Filter for BSC chain with USD pairs
        usd_pairs = []
        for pair in all_pairs:
            if pair.get('chainId') != 'bsc':
                continue
            
            quote_token = pair.get('quoteToken', {}).get('symbol', '')
            if quote_token in ['USDT', 'USDC', 'BUSD', 'DAI']:
                try:
                    vol = float(pair.get('volumeUsd24h', 0))
                    if vol >= MIN_VOLUME_USD:
                        usd_pairs.append(pair)
                except:
                    continue
        
        logger.info(f"Analyzing {len(usd_pairs)} USD pairs...")
        
        for pair in usd_pairs:
            try:
                await self.analyze_pair(pair)
            except Exception as e:
                continue
    
    async def analyze_pair(self, pair):
        """Analyze pair for volume spikes"""
        pair_address = pair['pairAddress']
        token_symbol = pair['baseToken']['symbol']
        token_name = pair['baseToken'].get('name', '')[:30]  # Truncate long names
        
        try:
            volume_24h = float(pair.get('volumeUsd24h', 0))
            liquidity = float(pair.get('liquidityUsd', 0))
            price = float(pair.get('priceUsd', 0))
            price_change = float(pair.get('priceChange24h', 0))
        except:
            return
        
        # Track history
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
                    await self.send_alert(
                        token_symbol, token_name, volume_24h, ratio,
                        price, liquidity, price_change, pair_address
                    )
                    self.alerted_tokens.add(alert_key)
                    
                    if len(self.alerted_tokens) > 100:
                        self.alerted_tokens.pop()
    
    async def send_alert(self, symbol, name, volume, ratio, price, liquidity, price_change, address):
        """Send alert to Telegram"""
        
        # Quality rating
        liq_ratio = liquidity / volume if volume > 0 else 0
        if liq_ratio < 0.1:
            quality, emoji = "⚠️ RISKY", "🚨"
        elif liq_ratio < 0.3:
            quality, emoji = "⚡ MODERATE", "⚡"
        else:
            quality, emoji = "✅ GOOD", "🎯"
        
        # Price trend
        if price_change > 10:
            trend = "🚀 PUMPING"
        elif price_change > 0:
            trend = "📈 Up"
        elif price_change < -10:
            trend = "📉 DUMPING"
        else:
            trend = "📉 Down"
        
        message = f"""
{emoji} *BSC VOLUME SPIKE*

🔹 *{symbol}*
{name and f'({name})' or ''}
💰 Price: ${price:.6f} ({price_change:+.1f}%) {trend}
📊 Volume: ${volume:,.0f} ({ratio:.1f}x avg)
💧 Liquidity: ${liquidity:,.0f}
📈 Quality: {quality}

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
            logger.info(f"✅ Alert: {symbol}")
        except Exception as e:
            logger.error(f"Send failed: {e}")

if __name__ == "__main__":
    bot = VolumeAlertBot()
    asyncio.run(bot.start())