import logging
import os
import sqlite3
import time

import requests
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from db import create_or_update_user
from db import get_user
from db import init_db
from db import set_user_pro
from migrate_pro_users import migrate

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PAYMENT_LINK = os.getenv("PAYMENT_LINK", "https://t.me/Crypdaman")
CRYP_PRO_LINK = os.getenv("CRYP_PRO_LINK", "https://t.me/+HCrmHvpLg_kzMGY0")
CRYP_PRO_CHANNEL_ID = int(os.getenv("CRYP_PRO_CHANNEL_ID", "-1003800067003"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "7057199314"))

client = OpenAI(api_key=OPENAI_API_KEY)

PRO_USERS = []
PRICE_ALERTS = []
pro_users = set()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRO_USERS_FILE = os.path.join(BASE_DIR, "pro_users.txt")
ALERTS_FILE = os.path.join(BASE_DIR, "alerts.txt")
DB_FILE = "cryp_data.db"
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlists.txt")
WATCHLISTS = {}
LAST_BREAKING_ALERTS = {}
MARKET_CACHE = {}
MARKET_CACHE_TIME = 0

def load_watchlists():
    global WATCHLISTS
    WATCHLISTS = {}

    if not os.path.exists(WATCHLIST_FILE):
        return

    with open(WATCHLIST_FILE, "r") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue

            parts = line.split(":")
            if len(parts) != 2:
                continue

            user_id = int(parts[0])
            coins = parts[1].split(",") if parts[1] else []
            WATCHLISTS[user_id] = set(
                coin.strip().upper() for coin in coins if coin.strip()
            )
            
def add_to_watchlist(user_id, coin):
    coin = coin.upper()

    if user_id not in WATCHLISTS:
        WATCHLISTS[user_id] = set()

    WATCHLISTS[user_id].add(coin)
    save_watchlists()
    
def remove_from_watchlist(user_id, coin):
    coin = coin.upper()

    if user_id in WATCHLISTS and coin in WATCHLISTS[user_id]:
        WATCHLISTS[user_id].remove(coin)

        if not WATCHLISTS[user_id]:
            del WATCHLISTS[user_id]

        save_watchlists()
        return True

    return False

def get_watchlist(user_id):
    return sorted(WATCHLISTS.get(user_id, set()))                
            
def save_watchlists():
    with open(WATCHLIST_FILE, "w") as file:
        for user_id, coins in WATCHLISTS.items():
            coin_list = ",".join(sorted(coins))
            file.write(f"{user_id}:{coin_list}\n")
            
def get_watchlist_with_prices(user_id):
    try:
        coins = get_watchlist(user_id)

        if not coins:
            return None

        coin_map = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "SOL": "solana",
            "XRP": "ripple",
            "DOGE": "dogecoin",
            "ADA": "cardano",
            "BNB": "binancecoin"
        }

        ids = ",".join(coin_map[c] for c in coins if c in coin_map)

        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": ids,
            "vs_currencies": "usd",
            "include_24hr_change": "true"
        }

        response = requests.get(url, params=params)
        data = response.json()

        lines = ["👀 *Your Watchlist:*\n"]

        for coin in coins:
            if coin not in coin_map:
                continue

            coin_id = coin_map[coin]
            price = data[coin_id]["usd"]
            change = data[coin_id]["usd_24h_change"]

            lines.append(f"🪙 {coin}: ${price:,.2f} ({change:+.2f}%)")

        return "\n".join(lines)

    except Exception as e:
        print("Error fetching watchlist prices:", e)
        return "⚠️ Failed to fetch watchlist data."            
            
                        

def load_pro_users():
    global pro_users
    pro_users = set()

    print("READING PRO USERS FROM:", PRO_USERS_FILE)

    try:
        with open(PRO_USERS_FILE, "r") as file:
            for line in file:
                line = line.strip()
                if line:
                    pro_users.add(int(line))
    except FileNotFoundError:
        pro_users = set()

    print("LOADED PRO USERS:", pro_users)


def save_pro_users():
    with open(PRO_USERS_FILE, "w") as file:
        for user_id in pro_users:
            file.write(f"{user_id}\n")

    print("SAVED PRO USERS:", pro_users)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
def load_price_alerts():
    global PRICE_ALERTS
    PRICE_ALERTS = []

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT user_id, coin, condition, target, premium
        FROM alerts
    """)

    rows = cursor.fetchall()
    conn.close()

    for row in rows:
        user_id, coin, condition, target, premium = row

        PRICE_ALERTS.append({
            "user_id": user_id,
            "coin": coin,
            "condition": condition,
            "target": float(target),
            "premium": bool(premium)
        })


def save_price_alerts():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM alerts")

    for alert in PRICE_ALERTS:
        cursor.execute("""
            INSERT INTO alerts (user_id, coin, condition, target, premium)
            VALUES (?, ?, ?, ?, ?)
        """, (
            alert["user_id"],
            alert["coin"],
            alert.get("condition", "above"),
            float(alert["target"]),
            1 if alert.get("premium", False) else 0
        ))

    conn.commit()
    conn.close()
            
def get_coin_data(symbol):
    url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
    data = requests.get(url).json()

    price = float(data["lastPrice"])
    change = float(data["priceChangePercent"])

    return price, change            

def main_menu_keyboard(user_id):
    user = get_user(user_id)
    is_pro = bool(user["is_pro"]) if user else False

    if is_pro:
        keyboard = [
            [InlineKeyboardButton("📊 Market Snapshot", callback_data="market_snapshot")],
            [InlineKeyboardButton("📰 Daily Briefing", callback_data="daily_briefing")],
            [InlineKeyboardButton("🗞️ News", callback_data="news_menu")],
            [InlineKeyboardButton("📈 Market Update", callback_data="market_update")],
            [InlineKeyboardButton("➕ Create Alert", callback_data="set_alert")],
            [InlineKeyboardButton("📂 View Alerts", callback_data="view_alerts")],
            [InlineKeyboardButton("💎 Pro Active", callback_data="pro_status")],
            [InlineKeyboardButton("💬 Support", callback_data="support")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
    else:

        keyboard = [
            [InlineKeyboardButton("📊 Market Snapshot", callback_data="market_snapshot")],
            [InlineKeyboardButton("📰 Daily Briefing", callback_data="daily_briefing")],
            [InlineKeyboardButton("🗞️ News", callback_data="news_menu")],
            [InlineKeyboardButton("📈 Market Update", callback_data="market_update")],
            [InlineKeyboardButton("➕ Create Alert", callback_data="set_alert")],
            [InlineKeyboardButton("📂 View Alerts", callback_data="view_alerts")],
            [InlineKeyboardButton("🚀 Upgrade to Pro", callback_data="upgrade")],
            [InlineKeyboardButton("💬 Support", callback_data="support")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        

    return InlineKeyboardMarkup(keyboard)

def back_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("⬅ Back to Menu", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def upgrade_keyboard():
    keyboard = [
        [InlineKeyboardButton("💳 Pay Now", url=PAYMENT_LINK)],
        [InlineKeyboardButton("✅ I've Paid", callback_data="i_paid")],
        [InlineKeyboardButton("⬅ Back to Menu", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_coin_analysis(symbol, is_pro=False):
    try:
        coin_map = {
            "btc": "bitcoin",
            "eth": "ethereum",
            "sol": "solana",
            "xrp": "ripple",
            "doge": "dogecoin",
            "ada": "cardano",
            "bnb": "binancecoin"
        }

        symbol = symbol.lower().strip()

        if symbol not in coin_map:
            return "❌ Coin not supported yet. Try: BTC, ETH, SOL, XRP, DOGE, ADA, BNB"

        coin_id = coin_map[symbol]

        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        response = requests.get(url)
        data = response.json()

        name = data["name"]
        price = data["market_data"]["current_price"]["usd"]
        change_24h = data["market_data"]["price_change_percentage_24h"]
        market_cap_rank = data.get("market_cap_rank", "N/A")

        if change_24h is None:
            trend = "➖ Neutral"
            outlook = "Price action is currently unclear."
            pro_insight = "The market is not giving a strong directional clue right now, so patience matters."
        elif change_24h > 2:
            trend = "📈 Bullish"
            outlook = "Momentum looks strong right now."
            pro_insight = "Buyers are showing strength here, and continuation is more likely if volume stays healthy."
        elif change_24h < -2:
            trend = "📉 Bearish"
            outlook = "Market pressure is still to the downside."
            pro_insight = "Sellers are still in control here, so catching bottoms too early can be risky."
        else:
            trend = "➖ Neutral"
            outlook = "Price is moving sideways for now."
            pro_insight = "Sideways movement usually means the market is waiting for a stronger catalyst before choosing direction."

        analysis = f"""
🔍 *{name} Analysis*

💵 Price: ${price:,.4f}
📊 24h Change: {change_24h:+.2f}%
🏆 Market Cap Rank: #{market_cap_rank}
📈 Trend: {trend}

🧠 Outlook:
{outlook}
"""

        if is_pro:
            analysis += f"""

💎 *Pro Insight:*
{pro_insight}
"""

        return analysis

    except Exception as e:
        print("Error fetching coin analysis:", e)
        return "⚠️ Failed to fetch coin data. Try again later."
    
def get_daily_briefing(is_pro=False):
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "bitcoin,ethereum,solana",
            "vs_currencies": "usd",
            "include_24hr_change": "true"
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        print("Daily briefing raw data:", data)

        if not all(coin in data for coin in ["bitcoin", "ethereum", "solana"]):
            print("Missing coin data in response")
            return "⚠️ Failed to fetch daily briefing. Try again later."

        btc_price = data["bitcoin"].get("usd")
        btc_change = data["bitcoin"].get("usd_24h_change")

        eth_price = data["ethereum"].get("usd")
        eth_change = data["ethereum"].get("usd_24h_change")

        sol_price = data["solana"].get("usd")
        sol_change = data["solana"].get("usd_24h_change")

        if None in [btc_price, btc_change, eth_price, eth_change, sol_price, sol_change]:
            print("Missing price/change values:", data)
            return "⚠️ Failed to fetch daily briefing. Try again later."

        avg_change = (btc_change + eth_change + sol_change) / 3

        if avg_change > 2:
            market_mood = "📈 Bullish"
            takeaway = "Momentum is strong across the market today."
            pro_insight = "Buyers are in control right now, and strong coins may continue leading if momentum holds."
        elif avg_change < -2:
            market_mood = "📉 Bearish"
            takeaway = "The market is under pressure today."
            pro_insight = "Sellers are dominating for now, so caution is important until price action stabilises."
        else:
            market_mood = "➖ Neutral"
            takeaway = "The market is moving sideways with mixed signals."
            pro_insight = "This usually means the market is waiting for direction, so patience is often better than forcing trades."

        briefing = f"""
📰 *Daily Briefing*

🪙 BTC: ${btc_price:,.2f} ({btc_change:+.2f}%)
🪙 ETH: ${eth_price:,.2f} ({eth_change:+.2f}%)
🪙 SOL: ${sol_price:,.2f} ({sol_change:+.2f}%)

📊 Overall Mood: {market_mood}

🧠 Today's Take:
{takeaway}
"""

        if is_pro:
            briefing += f"""

💎 *Pro Insight:*
{pro_insight}
"""

        return briefing.strip()

    except Exception as e:
        print("Error fetching daily briefing:", e)
        return "⚠️ Failed to fetch daily briefing. Try again later."    

def get_market_snapshot():
    try:
        data = get_cached_market_data()

        if not data:
            return "⚠️ Failed to fetch market data. Try again later."

        btc_price = data["bitcoin"]["usd"]
        btc_change = data["bitcoin"]["usd_24h_change"]

        eth_price = data["ethereum"]["usd"]
        eth_change = data["ethereum"]["usd_24h_change"]

        sol_price = data["solana"]["usd"]
        sol_change = data["solana"]["usd_24h_change"]

        def trend(change):
            if change > 2:
                return "📈 Bullish"
            elif change < -2:
                return "📉 Bearish"
            else:
                return "➖ Neutral"

        snapshot = f"""
📊 *Market Snapshot*

🪙 BTC: ${btc_price:,.2f} ({btc_change:+.2f}%)
🪙 ETH: ${eth_price:,.2f} ({eth_change:+.2f}%)
🪙 SOL: ${sol_price:,.2f} ({sol_change:+.2f}%)

📊 Market Mood:
BTC: {trend(btc_change)}
ETH: {trend(eth_change)}
SOL: {trend(sol_change)}
"""

        return snapshot

    except Exception as e:
        print("Error fetching market data:", e)
        return "⚠️ Failed to fetch market data. Try again later."
    
import feedparser

def get_crypto_news(user_id):
    try:
        user = get_user(user_id)
        is_pro = bool(user["is_pro"]) if user else False

        url = "https://cointelegraph.com/rss"
        response = requests.get(url, timeout=5)
        feed = feedparser.parse(response.content)

        if not feed.entries:
            return "⚠️ No market news available right now."

        news_text = "🌍 *MARKET NEWS*\n"
        news_text += "━━━━━━━━━━━━━━\n\n"

        headlines = []

        for entry in feed.entries:
            headlines.append(entry.title)

            if len(headlines) == 3:
                break

        if len(headlines) == 0:
            return "⚠️ No market news found right now."

        summaries = []
        if is_pro:
            summaries = get_ai_summary_block(headlines)

        sentiment = "🟡 Neutral"
        if is_pro:
            sentiment = get_market_sentiment(headlines)

        top_story = headlines[0]
        top_summary = summaries[0] if is_pro and len(summaries) > 0 else ""

        if is_pro:
            news_text += f"🔥 *Top Story*\n{top_story}\n\n"
            if top_summary:
                news_text += format_signal_line(top_summary) + "\n\n"
            news_text += "━━━━━━━━━━━━━━\n\n"

        start_index = 1 if is_pro else 0

        for i, title in enumerate(headlines[start_index:], start=1):
            news_text += f"*{i}.* {title}\n"

            summary_index = i if is_pro else i - 1

            if is_pro and summary_index < len(summaries):
                news_text += format_signal_line(summaries[summary_index]) + "\n"

            news_text += "\n"

        news_text += "━━━━━━━━━━━━━━\n"

        if is_pro:
            news_text += f"📊 *Market Sentiment:* {sentiment}\n\n"
            news_text += "💎 *Cryp Pro Active*\n"
            news_text += "📡 Premium insights enabled"
        else:
            news_text += "⚡ *Live market headlines*\n"
            news_text += "🔓 Upgrade to *Cryp Pro* for deeper market intel"

        return news_text

    except Exception as e:
        print("Error fetching market news:", e)
        return "⚠️ Failed to fetch market news."
    
def get_btc_news(user_id):
    try:
        user = get_user(user_id)
        is_pro = bool(user["is_pro"]) if user else False

        url = "https://cointelegraph.com/rss"
        response = requests.get(url, timeout=5)
        feed = feedparser.parse(response.content)

        if not feed.entries:
            return "⚠️ No BTC news available right now."

        news_text = "₿ *BTC NEWS*\n"
        news_text += "━━━━━━━━━━━━━━\n\n"

        headlines = []

        btc_keywords = [
            "bitcoin",
            " btc ",
            "satoshi",
            "ordinals",
            "lightning network",
            "spot bitcoin etf",
            "bitcoin etf",
            "microstrategy",
            "michael saylor",
            "halving",
            "miners",
            "mining difficulty"
        ]

        for entry in feed.entries:
            title = entry.title
            summary = getattr(entry, "summary", "")

            combined_text = f" {title.lower()} {summary.lower()} "

            if any(keyword in combined_text for keyword in btc_keywords):
                headlines.append(title)

            if len(headlines) == 3:
                break

        if len(headlines) == 0:
            return "⚠️ No BTC news found right now."

        summaries = []
        if is_pro:
            summaries = get_ai_summary_block(headlines)

        sentiment = "🟡 Neutral"
        if is_pro:
            sentiment = get_market_sentiment(headlines)

        top_story = headlines[0]
        top_summary = summaries[0] if is_pro and len(summaries) > 0 else ""

        if is_pro:
            news_text += f"🔥 *Top Story*\n{top_story}\n\n"
            if top_summary:
                news_text += format_signal_line(top_summary) + "\n\n"
            news_text += "━━━━━━━━━━━━━━\n\n"

        start_index = 1 if is_pro else 0

        for i, title in enumerate(headlines[start_index:], start=1):
            news_text += f"*{i}.* {title}\n"

            summary_index = i if is_pro else i - 1

            if is_pro and summary_index < len(summaries):
                news_text += format_signal_line(summaries[summary_index]) + "\n"

            news_text += "\n"

        news_text += "━━━━━━━━━━━━━━\n"

        if is_pro:
            news_text += f"📊 *Market Sentiment:* {sentiment}\n\n"
            news_text += "💎 *Cryp Pro Active*\n"
            news_text += "📡 Premium insights enabled"
        else:
            news_text += "⚡ *Live BTC headlines*\n"
            news_text += "🔓 Upgrade to *Cryp Pro* for deeper market intel"

        return news_text

    except Exception as e:
        print("Error fetching BTC news:", e)
        return "⚠️ Failed to fetch BTC news."
    
def get_eth_news(user_id):
    try:
        user = get_user(user_id)
        is_pro = bool(user["is_pro"]) if user else False

        url = "https://cointelegraph.com/rss"
        response = requests.get(url, timeout=5)
        feed = feedparser.parse(response.content)

        if not feed.entries:
            return "⚠️ No ETH news available right now."

        news_text = "Ξ *ETH NEWS*\n"
        news_text += "━━━━━━━━━━━━━━\n\n"

        headlines = []

        eth_keywords = [
            "ethereum",
            " ether ",
            " eth ",
            "vitalik",
            "staking",
            "staked ether",
            "gas fees",
            "erc-20",
            "layer 2",
            "rollup",
            "rollups",
            "arbitrum",
            "optimism",
            "base",
            "lido",
            "eigenlayer",
            "pectra"
        ]

        for entry in feed.entries:
            title = entry.title
            summary = getattr(entry, "summary", "")

            combined_text = f" {title.lower()} {summary.lower()} "

            if any(keyword in combined_text for keyword in eth_keywords):
                headlines.append(title)

            if len(headlines) == 3:
                break

        if len(headlines) == 0:
            return "⚠️ No ETH news found right now."

        summaries = []
        if is_pro:
            summaries = get_ai_summary_block(headlines)

        sentiment = "🟡 Neutral"
        if is_pro:
            sentiment = get_market_sentiment(headlines)

        top_story = headlines[0]
        top_summary = summaries[0] if is_pro and len(summaries) > 0 else ""

        if is_pro:
            news_text += f"🔥 *Top Story*\n{top_story}\n\n"
            if top_summary:
                news_text += format_signal_line(top_summary) + "\n\n"
            news_text += "━━━━━━━━━━━━━━\n\n"

        start_index = 1 if is_pro else 0

        for i, title in enumerate(headlines[start_index:], start=1):
            news_text += f"*{i}.* {title}\n"

            summary_index = i if is_pro else i - 1

            if is_pro and summary_index < len(summaries):
                news_text += format_signal_line(summaries[summary_index]) + "\n"

            news_text += "\n"

        news_text += "━━━━━━━━━━━━━━\n"

        if is_pro:
            news_text += f"📊 *Market Sentiment:* {sentiment}\n\n"
            news_text += "💎 *Cryp Pro Active*\n"
            news_text += "📡 Premium insights enabled"
        else:
            news_text += "⚡ *Live ETH headlines*\n"
            news_text += "🔓 Upgrade to *Cryp Pro* for deeper market intel"

        return news_text

    except Exception as e:
        print("Error fetching ETH news:", e)
        return "⚠️ Failed to fetch ETH news." 
    
def get_altcoin_news(user_id):
    try:
        user = get_user(user_id)
        is_pro = bool(user["is_pro"]) if user else False

        url = "https://cointelegraph.com/rss"
        response = requests.get(url, timeout=5)
        feed = feedparser.parse(response.content)

        if not feed.entries:
            return "⚠️ No altcoin news available right now."

        news_text = "🚀 *ALTCOIN NEWS*\n"
        news_text += "━━━━━━━━━━━━━━\n\n"

        headlines = []

        for entry in feed.entries:
            title = entry.title
            summary = getattr(entry, "summary", "")

            title_lower = title.lower()
            combined_text = f" {title_lower} {summary.lower()} "
            padded_title = f" {title_lower} "

            is_btc = (
                "bitcoin" in combined_text
                or " btc " in combined_text
                or "satoshi" in combined_text
                or "lightning network" in combined_text
                or "bitcoin etf" in combined_text
                or "microstrategy" in combined_text
                or "michael saylor" in combined_text
            )

            is_eth = (
                "ethereum" in combined_text
                or " ether " in combined_text
                or " eth " in combined_text
                or "vitalik" in combined_text
                or "staking" in combined_text
                or "erc-20" in combined_text
                or "layer 2" in combined_text
                or "rollup" in combined_text
                or "arbitrum" in combined_text
                or "optimism" in combined_text
                or "base" in combined_text
                or "lido" in combined_text
                or "eigenlayer" in combined_text
                or "pectra" in combined_text
            )

            if not is_btc and not is_eth:
                headlines.append(title)

            if len(headlines) == 3:
                break

        if len(headlines) == 0:
            return "⚠️ No altcoin news found right now."

        summaries = []
        if is_pro:
            summaries = get_ai_summary_block(headlines)

        sentiment = "🟡 Neutral"
        if is_pro:
            sentiment = get_market_sentiment(headlines)

        top_story = headlines[0]
        top_summary = summaries[0] if is_pro and len(summaries) > 0 else ""

        if is_pro:
            news_text += f"🔥 *Top Story*\n{top_story}\n\n"
            if top_summary:
                news_text += format_signal_line(top_summary) + "\n\n"
            news_text += "━━━━━━━━━━━━━━\n\n"

        start_index = 1 if is_pro else 0

        for i, title in enumerate(headlines[start_index:], start=1):
            news_text += f"*{i}.* {title}\n"

            summary_index = i if is_pro else i - 1

            if is_pro and summary_index < len(summaries):
                news_text += format_signal_line(summaries[summary_index]) + "\n"

            news_text += "\n"

        news_text += "━━━━━━━━━━━━━━\n"

        if is_pro:
            news_text += f"📊 *Market Sentiment:* {sentiment}\n\n"
            news_text += "💎 *Cryp Pro Active*\n"
            news_text += "📡 Premium insights enabled"
        else:
            news_text += "⚡ *Live altcoin headlines*\n"
            news_text += "🔓 Upgrade to *Cryp Pro* for deeper market intel"

        return news_text

    except Exception as e:
        print("Error fetching altcoin news:", e)
        return "⚠️ Failed to fetch altcoin news."     
    
def get_ai_summary_block(headlines):
    try:
        joined_headlines = "\n".join(headlines)

        response = client.responses.create(
            model="gpt-5-mini",
            input=f"""
You are writing for a premium crypto Telegram bot called Cryp Pro.

For each headline below, write exactly one short signal line.

Headlines:
{joined_headlines}

Rules:
- Return one line per headline
- Keep each line under 16 words
- Start each line with exactly one of:
  Opportunity:
  Risk:
  Market Impact:
- Be clear, concise, and professional
- No hype
- No financial advice
- Match the same order as the headlines
- Do not repeat the headline
"""
        )

        lines = []
        for line in response.output_text.strip().split("\n"):
            cleaned = line.strip()
            if cleaned:
                lines.append(cleaned)

        return lines

    except Exception as e:
        print("AI block error:", e)
        return [] 
    
def get_ai_market_summary():
    try:
        url = "https://cointelegraph.com/rss"
        response = requests.get(url, timeout=5)
        feed = feedparser.parse(response.content)

        if not feed.entries:
            return "⚠️ No news available for AI summary right now."

        headlines = [entry.title for entry in feed.entries[:5]]
        joined_headlines = "\n".join(headlines)

        response = client.responses.create(
            model="gpt-5-mini",
            input=f"""
You are writing for a premium crypto Telegram bot called Cryp Pro.

Based on these crypto headlines, write a short premium market summary.

Headlines:
{joined_headlines}

Rules:
- Keep it concise
- Sound professional and premium
- No hype
- No financial advice
- Use exactly this format:

🧠 AI MARKET SUMMARY
Sentiment: <Bullish, Neutral, or Bearish>
Focus: <one short sentence>
Takeaway: <one short sentence>
"""
        )

        return response.output_text.strip()

    except Exception as e:
        print("Error generating AI market summary:", e)
        return "⚠️ AI market summary is temporarily unavailable." 
    
def get_market_sentiment(headlines):
    try:
        joined = "\n".join(headlines)

        response = client.responses.create(
            model="gpt-5-mini",
            input=f"""
You are analyzing crypto market sentiment.

Based on these headlines, return ONLY ONE word:

Bullish, Bearish, or Neutral

Headlines:
{joined}

Rules:
- Only return ONE word
- No explanation
"""
        )

        sentiment = response.output_text.strip().lower()

        if "bull" in sentiment:
            return "🟢 Bullish"
        elif "bear" in sentiment:
            return "🔴 Bearish"
        else:
            return "🟡 Neutral"

    except Exception as e:
        print("Sentiment error:", e)
        return "🟡 Neutral"  
    
def format_signal_line(text):
    cleaned = text.strip()

    if cleaned.startswith("Opportunity:"):
        return f"📈 *Opportunity:* {cleaned.replace('Opportunity:', '', 1).strip()}"
    elif cleaned.startswith("Risk:"):
        return f"⚠️ *Risk:* {cleaned.replace('Risk:', '', 1).strip()}"
    elif cleaned.startswith("Market Impact:"):
        return f"📊 *Market Impact:* {cleaned.replace('Market Impact:', '', 1).strip()}"
    else:
        return f"📊 *Market Impact:* {cleaned}" 
    
def get_ai_daily_briefing():
    try:
        url = "https://cointelegraph.com/rss"
        response = requests.get(url, timeout=5)
        feed = feedparser.parse(response.content)

        if not feed.entries:
            return "⚠️ No data available for briefing."

        headlines = [entry.title for entry in feed.entries[:5]]
        joined = "\n".join(headlines)

        response = client.responses.create(
            model="gpt-5-mini",
            input=f"""
You are generating a premium crypto daily briefing.

Based on the headlines below, generate a structured summary.

Headlines:
{joined}

Rules:
- Professional tone
- No hype
- No financial advice
- Keep everything concise

Format EXACTLY like this:

Sentiment: <Bullish / Neutral / Bearish>
Top Narrative: <1 short sentence>
Opportunity: <1 short sentence>
Risk: <1 short sentence>
"""
        )

        text = response.output_text.strip()

        # Format it nicely for Telegram
        lines = text.split("\n")

        sentiment = lines[0].replace("Sentiment:", "").strip()
        narrative = lines[1].replace("Top Narrative:", "").strip()
        opportunity = lines[2].replace("Opportunity:", "").strip()
        risk = lines[3].replace("Risk:", "").strip()

        # Emoji mapping
        if "bull" in sentiment.lower():
            sentiment = "🟢 Bullish"
        elif "bear" in sentiment.lower():
            sentiment = "🔴 Bearish"
        else:
            sentiment = "🟡 Neutral"

        briefing = "🧠 *DAILY BRIEFING*\n"
        briefing += "━━━━━━━━━━━━━━\n\n"

        briefing += f"📊 *Market Sentiment:* {sentiment}\n\n"
        briefing += f"🔥 *Top Narrative:*\n{narrative}\n\n"
        briefing += f"📈 *Opportunity:*\n{opportunity}\n\n"
        briefing += f"⚠️ *Risk:*\n{risk}\n\n"

        briefing += "━━━━━━━━━━━━━━\n"
        briefing += "💎 *Cryp Pro Intelligence*"

        return briefing

    except Exception as e:
        print("Daily briefing error:", e)
        return "⚠️ Failed to generate daily briefing."                         
    
def get_top_movers():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "percent_change_24h_desc",
            "per_page": 10,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h"
        }

        response = requests.get(url, params=params)
        data = response.json()

        if not data:
            return "⚠️ No top movers available right now."

        movers_text = "🚀 *Top Movers (24h)*\n\n"

        count = 0
        for coin in data:
            symbol = coin["symbol"].upper()
            change = coin.get("price_change_percentage_24h_in_currency")

            if change is None:
                continue

            movers_text += f"{count + 1}. {symbol} {change:+.2f}%\n"
            count += 1

            if count == 5:
                break

        if count == 0:
            return "⚠️ No top movers available right now."

        return movers_text

    except Exception as e:
        print("Error fetching top movers:", e)
        return "⚠️ Failed to fetch top movers." 
    
def get_breaking_alert():
    global LAST_BREAKING_ALERTS

    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "bitcoin,ethereum,solana",
            "vs_currencies": "usd",
            "include_24hr_change": "true"
        }

        response = requests.get(url, params=params)
        data = response.json()

        coin_map = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "SOL": "solana"
        }

        alerts = []

        for symbol, coin_id in coin_map.items():
            price = data[coin_id]["usd"]
            change = data[coin_id]["usd_24h_change"]

            if change >= 5:
                direction = "🚀 Breaking Up"
            elif change <= -5:
                direction = "🔻 Breaking Down"
            else:
                continue

            previous_direction = LAST_BREAKING_ALERTS.get(symbol)

            if previous_direction == direction:
                continue

            LAST_BREAKING_ALERTS[symbol] = direction

            alerts.append(
                f"{direction}\n🪙 {symbol}: ${price:,.2f} ({change:+.2f}%)"
            )

        if not alerts:
            return None

        return "\n\n".join(alerts)

    except Exception as e:
        print("Error fetching breaking alerts:", e)
        return None
    
def get_premium_insight():
    try:
        data = get_cached_market_data()

        if not data:
            return "⚠️ Failed to fetch premium insight."

        btc_change = data.get("bitcoin", {}).get("usd_24h_change")
        eth_change = data.get("ethereum", {}).get("usd_24h_change")
        sol_change = data.get("solana", {}).get("usd_24h_change")

        if btc_change is None or eth_change is None or sol_change is None:
            print("Premium insight data problem:", data)
            return "⚠️ Premium insight data is unavailable right now."

        avg_change = (btc_change + eth_change + sol_change) / 3

        if avg_change > 3:
            title = "📈 Strong Bullish Pressure"
            insight = (
                "Buyers are clearly in control right now. Momentum is strong across the majors, "
                "so continuation is more likely if this strength holds."
            )
        elif avg_change > 1:
            title = "🟢 Mild Bullish Bias"
            insight = (
                "The market is leaning bullish, but not aggressively. This usually supports steady moves "
                "rather than explosive breakouts."
            )
        elif avg_change < -3:
            title = "📉 Strong Bearish Pressure"
            insight = (
                "Sellers are dominating the market right now. Caution matters here, especially if price "
                "keeps failing to recover."
            )
        elif avg_change < -1:
            title = "🔴 Mild Bearish Bias"
            insight = (
                "The market is leaning weak, but not in full panic mode. This often leads to choppy downside "
                "unless buyers step in soon."
            )
        else:
            title = "➖ Neutral Market Structure"
            insight = (
                "The market is balanced for now and waiting for stronger direction. In conditions like this, "
                "patience is usually better than forcing trades."
            )

        return f"💎 *Premium Insight*\n\n{title}\n\n{insight}"

    except Exception as e:
        print("Error fetching premium insight:", e)
        return "⚠️ Failed to fetch premium insight."
    
def get_cached_market_data():
    global MARKET_CACHE, MARKET_CACHE_TIME

    try:
        current_time = time.time()

        # Reuse cached data for 60 seconds
        if MARKET_CACHE and (current_time - MARKET_CACHE_TIME < 60):
            return MARKET_CACHE

        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "bitcoin,ethereum,solana",
            "vs_currencies": "usd",
            "include_24hr_change": "true"
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()

        MARKET_CACHE = data
        MARKET_CACHE_TIME = current_time

        return data

    except Exception as e:
        print("Error fetching cached market data:", e)

        if MARKET_CACHE:
            return MARKET_CACHE

        return None               
    
async def send_pro_daily_update(context: ContextTypes.DEFAULT_TYPE):
    try:
        briefing = get_daily_briefing(is_pro=True)

        await context.bot.send_message(
            chat_id=CRYP_PRO_CHANNEL_ID,
            text=briefing,
            parse_mode="Markdown"
        )

        print("Sent Pro Daily Briefing")

    except Exception as e:
        print("Error sending pro update:", e)
        
async def send_premium_insight(context: ContextTypes.DEFAULT_TYPE):
    try:
        insight = get_premium_insight()

        await context.bot.send_message(
            chat_id=CRYP_PRO_CHANNEL_ID,
            text=insight,
            parse_mode="Markdown"
        )

        print("Sent Premium Insight")

    except Exception as e:
        print("Error sending premium insight:", e)        
        
async def send_breaking_alert(context: ContextTypes.DEFAULT_TYPE):
    try:
        alert_text = get_breaking_alert()

        if not alert_text:
            return

        await context.bot.send_message(
            chat_id=CRYP_PRO_CHANNEL_ID,
            text=f"🚨 *Breaking Alert*\n\n{alert_text}",
            parse_mode="Markdown"
        )

        print("Sent Breaking Alert")

    except Exception as e:
        print("Error sending breaking alert:", e)        
        
async def send_top_movers(context: ContextTypes.DEFAULT_TYPE):
    try:
        movers = get_top_movers()

        await context.bot.send_message(
            chat_id=CRYP_PRO_CHANNEL_ID,
            text=movers,
            parse_mode="Markdown"
        )

        print("Sent Top Movers")

    except Exception as e:
        print("Error sending top movers:", e)         
        
async def send_market_snapshot(context: ContextTypes.DEFAULT_TYPE):
    try:
        snapshot = get_market_snapshot()

        await context.bot.send_message(
            chat_id=CRYP_PRO_CHANNEL_ID,
            text="📊 *Market Update*\n" + snapshot,
            parse_mode="Markdown"
        )

        print("Sent Market Snapshot")

    except Exception as e:
        print("Error sending market snapshot:", e)
        
async def send_daily_briefing(context: ContextTypes.DEFAULT_TYPE):
    try:
        briefing = get_daily_briefing()
        await context.bot.send_message(
            chat_id=CRYP_PRO_CHANNEL_ID,
            text=briefing,
            parse_mode="Markdown"
        )
    except Exception as e:
        print("Error sending daily briefing:", e)                      

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    
    create_or_update_user(user_id, username=username)
    
    user = get_user(user_id)
    is_pro = bool(user["is_pro"]) if user else False
    
    print(f"START DEBUG -> user_id={user_id}, username={username}, db_is_pro={is_pro}")

    if user_id in pro_users:
        text = (
    "🚀 *Welcome to Cryp Pro*\n\n"
    "Your premium access is active.\n\n"
    "Pro plan includes:\n"
    "• Unlimited active alerts\n"
    "• Real-time market updates\n"
    "• Faster notifications\n"
    "• Premium features as they roll out\n\n"
    "Choose an option below 👇"
)
    else:
        text = (
    "📊 *Welcome to Cryp Free*\n\n"
    "Track the market with simple crypto alerts.\n\n"
    "Free plan includes:\n"
    "• Up to 2 active alerts\n"
    "• Easy alert management\n"
    "• Option to upgrade anytime\n\n"
    "Choose an option below 👇"
)

    await update.message.reply_text(
    text,
    reply_markup=main_menu_keyboard(user_id),
    parse_mode="Markdown"
)
    
async def remove_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pro_users

    user_id = update.effective_user.id

    if user_id in pro_users:
        pro_users.remove(user_id)
        save_pro_users()

    set_user_pro(
        telegram_user_id=user_id,
        is_pro=0,
        subscription_status="removed_for_testing"
    )

    await update.message.reply_text("❌ Your Pro access has been removed. You are now on Cryp Free.")
        
async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(update.to_dict())
    await update.message.reply_text("Check your terminal")
    
async def log_channel_post(update, context):
    chat = update.effective_chat
    message = update.channel_post

    print("CHANNEL POST DETECTED")
    print("Chat ID:", chat.id)
    print("Chat Title:", chat.title)
    print("Chat Type:", chat.type)

    if message:
        print("Message text:", message.text)
    
async def sendpro(update, context):
    try:
        user_id = update.effective_user.id

        if user_id != ADMIN_ID:
            await update.message.reply_text("You are not allowed to use this command.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /sendpro Your message here")
            return

        message_text = " ".join(context.args)

        await context.bot.send_message(
            chat_id=CRYP_PRO_CHANNEL_ID,
            text=message_text
        )

        await update.message.reply_text("Message sent to Cryp Pro successfully.")

    except Exception as e:
        print("SENDPRO ERROR:", e)
        await update.message.reply_text(f"Error: {e}")
        
async def premium_alert(update, context):
    try:
        user_id = update.effective_user.id

        if user_id != ADMIN_ID:
            await update.message.reply_text("You are not allowed to use this command.")
            return

        if len(context.args) != 2:
            await update.message.reply_text("Usage: /premiumalert BTC 70000")
            return

        coin = context.args[0].upper()
        target = float(context.args[1])

        alert = {
            "coin": coin,
            "target": target,
            "user_id": user_id,
            "premium": True
        }

        PRICE_ALERTS.append(alert)
        save_price_alerts()

        await update.message.reply_text(f"Premium alert set for {coin} at ${target}")

    except ValueError:
        await update.message.reply_text("Please enter a valid number. Example: /premiumalert BTC 70000")

    except Exception as e:
        print("PREMIUM ALERT ERROR:", e)
        await update.message.reply_text(f"Error: {e}")        
        
async def getchatid(update, context):
    chat = update.effective_chat
    await update.message.reply_text(
        f"Chat ID: {chat.id}\n"
        f"Chat type: {chat.type}\n"
        f"Chat title: {chat.title}"
    )                            

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        user_id = query.from_user.id
        username = query.from_user.username

        create_or_update_user(user_id, username=username)

        user = get_user(user_id)
        is_pro = bool(user["is_pro"]) if user else False

        print(f"BUTTON DEBUG -> user_id={user_id}, username={username}, db_is_pro={is_pro}")

        if query.data == "free_alerts":
            text = (
                "Free Alerts ✅\n\n"
                "Cryp Free currently covers:\n"
                "• BTC\n"
                "• ETH\n"
                "• SOL\n\n"
                "You’ll receive basic market alerts and daily updates here.\n\n"
                "Want faster alerts and more coins? Tap Upgrade to Pro."
            )
            await query.edit_message_text(
                text=text,
                reply_markup=main_menu_keyboard(user_id)
            )

        elif query.data == "market_snapshot":
            snapshot = get_market_snapshot()
            await query.message.reply_text(snapshot, parse_mode="Markdown")

        elif query.data == "daily_briefing":
            if not is_pro:
                await query.edit_message_text(
                    "🔒 Daily Briefing is a *Cryp Pro* feature.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💎 Upgrade to Pro", callback_data="upgrade_pro")],
                        [InlineKeyboardButton("⬅️ Back to News", callback_data="news_menu")]
                    ])
                )
                return

            briefing = get_ai_daily_briefing()

            await query.edit_message_text(
                text=briefing,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to News", callback_data="news_menu")]
                ])
            )

        elif query.data == "news_menu":
            if is_pro:
                keyboard = [
                    [InlineKeyboardButton("₿ BTC News", callback_data="btc_news")],
                    [InlineKeyboardButton("Ξ ETH News", callback_data="eth_news")],
                    [InlineKeyboardButton("🌍 Market News", callback_data="market_news")],
                    [InlineKeyboardButton("🚀 Altcoin News", callback_data="altcoin_news")],
                    [InlineKeyboardButton("🧠 AI Summary", callback_data="ai_summary")],
                    [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]
                ]
                title = "🗞️ *NEWS HUB PRO*\n\nChoose an option:"
            else:
                keyboard = [
                    [InlineKeyboardButton("₿ BTC News", callback_data="btc_news")],
                    [InlineKeyboardButton("Ξ ETH News", callback_data="eth_news")],
                    [InlineKeyboardButton("🌍 Market News", callback_data="market_news")],
                    [InlineKeyboardButton("🔒 Altcoin News (Pro)", callback_data="pro_feature")],
                    [InlineKeyboardButton("🔒 AI Summary (Pro)", callback_data="pro_feature")],
                    [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]
                ]
                title = "🗞️ *NEWS HUB*\n\nChoose an option:"

            await query.edit_message_text(
                title,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

        elif query.data == "btc_news":
            news = get_btc_news(user_id)
            await query.edit_message_text(
                text=news,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to News", callback_data="news_menu")]
                ]),
                parse_mode="Markdown"
            )

        elif query.data == "eth_news":
            news = get_eth_news(user_id)
            await query.edit_message_text(
                text=news,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to News", callback_data="news_menu")]
                ]),
                parse_mode="Markdown"
            )

        elif query.data == "market_news":
            news = get_crypto_news(user_id)
            await query.edit_message_text(
                text=news,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to News", callback_data="news_menu")]
                ]),
                parse_mode="Markdown"
            )

        elif query.data == "altcoin_news":
            news = get_altcoin_news(user_id)
            await query.edit_message_text(
                text=news,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to News", callback_data="news_menu")]
                ]),
                parse_mode="Markdown"
            )

        elif query.data == "ai_summary":
            if not is_pro:
                await query.answer("🔒 This is a Cryp Pro feature.", show_alert=True)
                return

            summary = get_ai_market_summary()
            await query.edit_message_text(
                text=summary,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to News", callback_data="news_menu")]
                ]),
                parse_mode="Markdown"
            )

        elif query.data == "pro_feature":
            await query.answer("🔒 This is a Cryp Pro feature.", show_alert=True)

        elif query.data == "latest_news":
            news = get_crypto_news(user_id)
            await query.edit_message_text(
                text=news,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to News", callback_data="news_menu")]
                ]),
                parse_mode="Markdown"
            )

        elif query.data == "help":
            text = (
                "📘 *How to Use Cryp*\n\n"
                "📊 Market Snapshot\n"
                "→ Tap to see current prices & trend\n\n"
                "📈 Market Update\n"
                "→ Quick market overview\n\n"
                "🚨 Create Alert\n"
                "→ Follow the guided steps\n\n"
                "👁 View Alerts\n"
                "→ See your active alerts\n\n"
                "⭐ Watchlist\n"
                "→ Add: `add btc`\n"
                "→ Remove: `remove btc`\n"
                "→ View: `watchlist`\n\n"
                "🧠 Coin Analysis\n"
                "→ Just type:\n"
                "`btc`, `eth`, `sol`\n\n"
                "💎 Pro Features\n"
                "→ Unlock advanced tools & insights\n"
            )

            await query.edit_message_text(
                text,
                parse_mode="Markdown",
                reply_markup=back_menu_keyboard()
            )

        elif query.data == "support":
            text = (
                "💬 *Cryp Support*\n\n"
                "Need help with alerts, payments, or Pro access?\n\n"
                "Our support team is here to help you 👇\n\n"
                "📩 Contact: @crypdaman\n\n"
                "We usually respond quickly 🚀"
            )

            await query.edit_message_text(
                text=text,
                reply_markup=back_menu_keyboard(),
                parse_mode="Markdown"
            )

        elif query.data == "view_alerts":
            user_alerts = [a for a in PRICE_ALERTS if a["user_id"] == user_id]

            if not user_alerts:
                await query.edit_message_text(
                    text="You have no alerts set.",
                    reply_markup=back_menu_keyboard()
                )
            else:
                keyboard = []
                text = "📈 Your Alerts:\n\n"

                for i, a in enumerate(user_alerts):
                    condition = a.get("condition", "above").upper()
                    text += f"{i + 1}. {a['coin']} {condition} ${a['target']}\n"
                    keyboard.append([
                        InlineKeyboardButton(
                            f"❌ Delete {a['coin']} ${a['target']}",
                            callback_data=f"delete_alert_{i}"
                        )
                    ])

                keyboard.append([
                    InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")
                ])

                await query.edit_message_text(
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

        elif query.data.startswith("delete_alert_"):
            user_alerts = [a for a in PRICE_ALERTS if a["user_id"] == user_id]
            index = int(query.data.split("_")[-1])

            if 0 <= index < len(user_alerts):
                alert_to_delete = user_alerts[index]
                PRICE_ALERTS.remove(alert_to_delete)
                save_price_alerts()

                await query.answer("Alert deleted.")

                remaining_alerts = [a for a in PRICE_ALERTS if a["user_id"] == user_id]

                if not remaining_alerts:
                    await query.edit_message_text(
                        text="You have no alerts set.",
                        reply_markup=back_menu_keyboard()
                    )
                else:
                    keyboard = []
                    text = "📈 Your Alerts:\n\n"

                    for i, a in enumerate(remaining_alerts):
                        text += f"{i + 1}. {a['coin']} → ${a['target']}\n"
                        keyboard.append([
                            InlineKeyboardButton(
                                f"❌ Delete {a['coin']} ${a['target']}",
                                callback_data=f"delete_alert_{i}"
                            )
                        ])

                    keyboard.append([
                        InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")
                    ])

                    await query.edit_message_text(
                        text=text,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
            else:
                await query.answer("Alert not found.", show_alert=True)

        elif query.data == "set_alert":
            keyboard = [
                [
                    InlineKeyboardButton("BTC", callback_data="alert_coin_btc"),
                    InlineKeyboardButton("ETH", callback_data="alert_coin_eth")
                ],
                [
                    InlineKeyboardButton("SOL", callback_data="alert_coin_sol"),
                    InlineKeyboardButton("XRP", callback_data="alert_coin_xrp")
                ],
                [
                    InlineKeyboardButton("DOGE", callback_data="alert_coin_doge"),
                    InlineKeyboardButton("ADA", callback_data="alert_coin_ada")
                ],
                [
                    InlineKeyboardButton("BNB", callback_data="alert_coin_bnb")
                ],
                [
                    InlineKeyboardButton("⬅ Back to Menu", callback_data="back_to_menu")
                ]
            ]

            await query.edit_message_text(
                "🪙 Choose a coin for your alert:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        elif query.data.startswith("alert_coin_"):
            coin = query.data.split("_")[-1]

            if is_pro:
                keyboard = [
                    [InlineKeyboardButton("📈 Above", callback_data=f"alert_cond_{coin}_above")],
                    [InlineKeyboardButton("📉 Below", callback_data=f"alert_cond_{coin}_below")],
                    [InlineKeyboardButton("⬅ Back", callback_data="set_alert")]
                ]

                await query.edit_message_text(
                    f"⚙️ {coin.upper()} — choose alert condition:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                context.user_data["alert_coin"] = coin
                context.user_data["alert_condition"] = "above"

                await query.edit_message_text(
                    f"💰 Enter the price for {coin.upper()}:\n\nExample: 70000",
                    reply_markup=back_menu_keyboard()
                )

        elif query.data.startswith("alert_cond_"):
            parts = query.data.split("_")
            coin = parts[2]
            condition = parts[3]

            context.user_data["alert_coin"] = coin
            context.user_data["alert_condition"] = condition

            await query.edit_message_text(
                f"💰 Enter the price for {coin.upper()} ({condition}):\n\nExample: 70000",
                reply_markup=back_menu_keyboard()
            )

        elif query.data == "market_update":
            if not is_pro:
                text = (
                    "🔒 *Pro Feature*\n\n"
                    "Live market updates are available on *Cryp Pro* only.\n\n"
                    "Upgrade to unlock:\n"
                    "• Real-time market updates\n"
                    "• Faster alerts\n"
                    "• Premium features as they roll out\n\n"
                    "🚀 Upgrade now to get the full experience."
                )

                keyboard = [
                    [InlineKeyboardButton("🚀 Upgrade to Pro", callback_data="upgrade")],
                    [InlineKeyboardButton("← Back to Menu", callback_data="back_to_menu")]
                ]

                await query.edit_message_text(
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
                return

            btc_price, btc_change = get_coin_data("BTCUSDT")
            eth_price, eth_change = get_coin_data("ETHUSDT")
            sol_price, sol_change = get_coin_data("SOLUSDT")
            xrp_price, xrp_change = get_coin_data("XRPUSDT")
            doge_price, doge_change = get_coin_data("DOGEUSDT")

            def trend_emoji(change):
                return "🟢" if change >= 0 else "🔴"

            text = (
                "📈 *Cryp Pro Market Update*\n\n"
                f"{trend_emoji(btc_change)} ₿ BTC: ${btc_price:,.2f} ({btc_change:+.2f}%)\n"
                f"{trend_emoji(eth_change)} ◆ ETH: ${eth_price:,.2f} ({eth_change:+.2f}%)\n"
                f"{trend_emoji(sol_change)} ◎ SOL: ${sol_price:,.2f} ({sol_change:+.2f}%)\n"
                f"{trend_emoji(xrp_change)} ✕ XRP: ${xrp_price:,.4f} ({xrp_change:+.2f}%)\n"
                f"{trend_emoji(doge_change)} 🐶 DOGE: ${doge_price:,.4f} ({doge_change:+.2f}%)\n\n"
                "Market is active 🚀"
            )

            await query.edit_message_text(
                text=text,
                reply_markup=back_menu_keyboard(),
                parse_mode="Markdown"
            )

        elif query.data == "upgrade":
            text = (
                "💎 *Cryp Pro*\n\n"
                "Stop missing moves. Start catching them.\n\n"
                "📊 *What you unlock:*\n"
                "• Unlimited price alerts\n"
                "• Advanced alerts (above / below)\n"
                "• Real-time market updates\n"
                "• Faster notifications\n"
                "• Premium signals (coming soon)\n\n"
                "⚡ Built for traders who want an edge.\n\n"
                "💰 *One-time upgrade – instant access*\n\n"
                "👇 Tap below to upgrade now"
            )
            await query.edit_message_text(
                text=text,
                reply_markup=upgrade_keyboard(),
                parse_mode="Markdown"
            )

        elif query.data == "pro_status":
            if is_pro:
                text = (
                    "💎 Cryp Pro Unlocked\n\n"
                    "Your premium access is active.\n"
                    "Enjoy all pro features 🚀"
                )
            else:
                text = (
                    "🔓 You are currently on Cryp Free.\n\n"
                    "Upgrade to unlock premium features 🚀"
                )

            await query.edit_message_text(
                text=text,
                reply_markup=back_menu_keyboard()
            )

        elif query.data == "upgrade_pro":
            text = (
                "Upgrade to Cryp Pro 🚀\n\n"
                "Cryp Pro gives you:\n"
                "• Faster alerts\n"
                "• More coins covered\n"
                "• Premium watchlist updates\n"
                "• Better trade breakdowns\n\n"
                "Price: R99/month\n\n"
                "Tap Pay Now to join Cryp Pro."
            )
            await query.edit_message_text(
                text=text,
                reply_markup=upgrade_keyboard()
            )

        elif query.data.startswith("approve_"):
            approved_user_id = int(query.data.split("_")[1])

            if approved_user_id not in pro_users:
                pro_users.add(approved_user_id)
                save_pro_users()

            create_or_update_user(approved_user_id)

            set_user_pro(
                telegram_user_id=approved_user_id,
                is_pro=1,
                subscription_status="manually_approved"
            )

            await context.bot.send_message(
                chat_id=approved_user_id,
                text=(
                    "🎉 You have been approved!\n\n"
                    "Welcome to Cryp Pro 🚀\n\n"
                    "Here is your private access link:\n"
                    f"{CRYP_PRO_LINK}"
                )
            )

            await query.edit_message_text(
                text="✅ User approved successfully"
            )

        elif query.data == "i_paid":
            paid_user = query.from_user
            paid_username = f"@{paid_user.username}" if paid_user.username else "No username"
            paid_user_id = paid_user.id

            admin_message = (
                "🚨 New Payment Request\n\n"
                f"User: {paid_username}\n"
                f"User ID: {paid_user_id}"
            )

            approve_button = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve User", callback_data=f"approve_{paid_user_id}")]
            ])

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_message,
                reply_markup=approve_button
            )

            await query.edit_message_text(
                text=(
                    "✅ Payment request sent!\n\n"
                    "Please wait while your payment is reviewed.\n"
                    "You will be approved shortly."
                ),
                reply_markup=back_menu_keyboard()
            )

        elif query.data == "back_to_menu":
            user_id = query.from_user.id
            user = get_user(user_id)
            is_pro = bool(user["is_pro"]) if user else False

            if is_pro:
                text = (
                    "🚀 Welcome to Cryp Pro\n\n"
                    "Get real-time crypto alerts, market updates, and premium signals.\n\n"
                    "👇 Choose an option below:"
                )
            else:
                text = (
                    "📉 Welcome to Cryp Free\n\n"
                    "Get free crypto alerts, market updates, and basic coin coverage.\n\n"
                    "👇 Choose an option below:"
                )

            await query.edit_message_text(
                text=text,
                reply_markup=main_menu_keyboard(user_id)
            )
    except Exception as e:
        print("Button handler error:", e)
        
async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pro_users

    if update.effective_user.id != ADMIN_ID:
        return

    try:
        user_id = int(context.args[0])

        if user_id not in pro_users:
            pro_users.add(user_id)
            save_pro_users()

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "Welcome to Cryp Pro 🚀\n\n"
                "Your payment has been confirmed.\n\n"
                "Here is your private access link:\n"
                "https://t.me/+HCrmHvpLg_kzMGY0"
            )
        )

        await update.message.reply_text("User approved ✅")

    except:
        await update.message.reply_text("Error. Use: /approve USER_ID")
        
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    try:
        user_id = update.effective_user.id
        username = update.effective_user.username

        create_or_update_user(user_id, username=username)

        user = get_user(user_id)
        is_pro = bool(user["is_pro"]) if user else False

        print(f"HANDLE DEBUG -> user_id={user_id}, username={username}, db_is_pro={is_pro}")

        message_parts = update.message.text.strip().lower().split()
        text = update.message.text.strip().lower()

        supported_coins = ["btc", "eth", "sol", "xrp", "doge", "ada", "bnb"]

        if text in supported_coins:
            analysis = get_coin_analysis(text, is_pro=is_pro)
            await update.message.reply_text(analysis, parse_mode="Markdown")
            return

        if text == "insight":
            if not is_pro:
                await update.message.reply_text("🔒 Premium insight is a Cryp Pro feature.")
                return

            insight = get_premium_insight()
            await update.message.reply_text(insight, parse_mode="Markdown")
            return

        if text == "watchlist":
            watchlist_text = get_watchlist_with_prices(user_id)

            if not watchlist_text:
                await update.message.reply_text(
                    "👀 Your watchlist is empty.\n\nUse:\nadd btc\nadd eth\nremove btc"
                )
            else:
                await update.message.reply_text(watchlist_text, parse_mode="Markdown")

            return

        if text == "news":
            news = get_crypto_news(user_id)
            await update.message.reply_text(news, parse_mode="Markdown")
            return

        if "alert_coin" in context.user_data and "alert_condition" in context.user_data:
            try:
                coin = context.user_data["alert_coin"]
                condition = context.user_data["alert_condition"]
                target_price = float(text)

                if not is_pro and condition == "below":
                    await update.message.reply_text("💎 Below alerts are for Cryp Pro users only.")
                    context.user_data.clear()
                    return

                if not is_pro:
                    user_alerts = [a for a in PRICE_ALERTS if a["user_id"] == user_id]
                    if len(user_alerts) >= 2:
                        await update.message.reply_text(
                            "⚠️ Free users can only have 2 active alerts.\nUpgrade to Cryp Pro for unlimited alerts."
                        )
                        context.user_data.clear()
                        return

                PRICE_ALERTS.append({
                    "user_id": user_id,
                    "coin": coin.upper(),
                    "condition": condition,
                    "target": target_price,
                    "premium": is_pro
                })

                save_price_alerts()

                await update.message.reply_text(
                    f"✅ Alert set: {coin.upper()} {condition} {target_price}"
                )

                context.user_data.clear()
                return

            except ValueError:
                await update.message.reply_text("❌ Please enter a valid number.\n\nExample: 70000")
                return

        if len(message_parts) == 2 and message_parts[0] == "add":
            coin = message_parts[1].lower()

            if coin not in supported_coins:
                await update.message.reply_text("❌ Coin not supported yet.")
                return

            add_to_watchlist(user_id, coin)
            await update.message.reply_text(f"✅ {coin.upper()} added to your watchlist.")
            return

        if len(message_parts) == 2 and message_parts[0] == "remove":
            coin = message_parts[1].lower()

            if remove_from_watchlist(user_id, coin):
                await update.message.reply_text(f"🗑 {coin.upper()} removed from your watchlist.")
            else:
                await update.message.reply_text(f"❌ {coin.upper()} is not in your watchlist.")
            return

        if len(message_parts) == 2:
            coin = message_parts[0].upper()
            condition = "above"
            target_price = float(message_parts[1])

        elif len(message_parts) == 3:
            coin = message_parts[0].upper()
            condition = message_parts[1].lower()
            target_price = float(message_parts[2])

            if condition not in ["above", "below"]:
                await update.message.reply_text(
                    "❌ Invalid alert type.\n\nUse `above` or `below`.",
                    parse_mode="Markdown"
                )
                return

            if not is_pro:
                await update.message.reply_text(
                    "🔒 Above/below alerts are a *Cryp Pro* feature.",
                    parse_mode="Markdown"
                )
                return

        else:
            await update.message.reply_text(
                "❌ Invalid format.\n\nUse:\n`BTC 70000`\n`BTC above 70000`\n`BTC below 65000`",
                parse_mode="Markdown"
            )
            return

        user_alerts = [alert for alert in PRICE_ALERTS if alert["user_id"] == user_id]

        if not is_pro and len(user_alerts) >= 2:
            await update.message.reply_text(
                "❌ Free users can only have 2 alerts.\nUpgrade to Pro for unlimited alerts."
            )
            return

        PRICE_ALERTS.append({
            "user_id": user_id,
            "coin": coin,
            "target": target_price,
            "condition": condition,
            "premium": is_pro
        })

        save_price_alerts()

        await update.message.reply_text(
            f"✅ *Alert Created*\n\n"
            f"Coin: {coin}\n"
            f"Condition: {condition.upper()}\n"
            f"Target Price: ${target_price}\n\n"
            f"📊 We'll notify you when the price is reached.",
            parse_mode="Markdown"
        )

        if not is_pro:
            await update.message.reply_text(
                "🚀 Want unlimited alerts? Upgrade to *Cryp Pro* anytime.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚀 Upgrade to Pro", callback_data="upgrade")]
                ]),
                parse_mode="Markdown"
            )

    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format. Use like this:\nBTC 70000"
        )
    except Exception as e:
        print("HANDLE MESSAGE ERROR:", e)
        await update.message.reply_text("⚠️ Something went wrong. Please try again.")

async def check_price_alerts(context: ContextTypes.DEFAULT_TYPE):
    print("=== CHECKING ALERTS ===")
    print("PRICE_ALERTS:", PRICE_ALERTS)

    for alert in PRICE_ALERTS[:]:
        try:
            print("Raw alert:", alert)

            coin = alert["coin"]
            user_id = alert["user_id"]
            target = float(alert["target"])
            condition = alert.get("condition", "above")

            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {
                "ids": "bitcoin,ethereum,solana",
                "vs_currencies": "usd"
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            print("CoinGecko response:", data)
            
            price_map = {
                "BTC": data["bitcoin"]["usd"],
                "ETH": data["ethereum"]["usd"],
                "SOL": data["solana"]["usd"]
            }
            
            price = price_map.get(coin)

            print(
                f"Checking {coin}: current price = {price}, "
                f"target = {target}, condition = {condition}"
            )

            triggered = False

            if condition == "above" and price >= target:
                triggered = True
                print("ABOVE alert triggered")

            elif condition == "below" and price <= target:
                triggered = True
                print("BELOW alert triggered")

            if triggered:
                if alert.get("premium"):
                    alert_message = (
                        f"🚨 CRYP PRO SIGNAL 🚨\n\n"
                        f"📈 Pair: {coin}/USDT\n"
                        f"🎯 Target: ${target}\n"
                        f"💵 Current: ${price}\n\n"
                        f"⚡ Status: BREAKOUT CONFIRMED\n"
                        f"🧠 Strategy: Watch for continuation\n\n"
                        f"🔒 Pro Exclusive Signal"
                    )
                else:
                    alert_message = f"🚨 {coin} hit ${target}!\nCurrent price: ${price}"

                await context.bot.send_message(
                    chat_id=user_id,
                    text=alert_message
                )

                if alert.get("premium"):
                    await context.bot.send_message(
                        chat_id=CRYP_PRO_CHANNEL_ID,
                        text=alert_message
                    )

                PRICE_ALERTS.remove(alert)
                save_price_alerts()
                print("Alert removed after sending")

        except Exception as e:
            print("check_price_alerts error:", e)
async def show_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_alerts = []

    for alert in PRICE_ALERTS:
        if alert["user_id"] == user_id:
            user_alerts.append(alert)

    if not user_alerts:
        await update.message.reply_text("You have no active alerts.")
        return

    message = "📋 Your active alerts:\n\n"

    for i, alert in enumerate(user_alerts, start=1):
        message += f"{i}. {alert['coin']} at ${alert['target']}\n"

    await update.message.reply_text(message)
async def delete_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_alerts = []

    for alert in PRICE_ALERTS:
        if alert["user_id"] == user_id:
            user_alerts.append(alert)

    if not user_alerts:
        await update.message.reply_text("You have no active alerts to delete.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage: /delete 2")
        return

    try:
        alert_number = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Please enter a valid alert number. Example: /delete 2")
        return

    if alert_number < 1 or alert_number > len(user_alerts):
        await update.message.reply_text("That alert number does not exist.")
        return

    alert_to_delete = user_alerts[alert_number - 1]
    PRICE_ALERTS.remove(alert_to_delete)
    save_price_alerts()

    await update.message.reply_text(
        f"✅ Deleted alert: {alert_to_delete['coin']} at ${alert_to_delete['target']}"
    )

def get_db_connection():
    return sqlite3.connect(DB_FILE)


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            coin TEXT NOT NULL,
            condition TEXT NOT NULL,
            target REAL NOT NULL,
            premium INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()    


def validate_environment():
    required_vars = {
        "BOT_TOKEN": BOT_TOKEN,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "ADMIN_ID": ADMIN_ID,
        "CRYP_PRO_LINK": CRYP_PRO_LINK,
        "PAYMENT_LINK": PAYMENT_LINK,
        "CRYP_PRO_CHANNEL_ID": CRYP_PRO_CHANNEL_ID,
    }

    missing = [name for name, value in required_vars.items() if value in (None, "", 0)]

    if missing:
        raise ValueError(
            "Missing required environment variables: " + ", ".join(missing)
        )


def main():
    validate_environment()
    init_db()
    migrate()
    load_pro_users()
    load_price_alerts()
    load_watchlists()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("alerts", show_alerts))
    app.add_handler(CommandHandler("delete", delete_alert))
    app.add_handler(CommandHandler("remove_me", remove_me))
    app.add_handler(CommandHandler("chatid", get_chat_id))
    app.add_handler(CommandHandler("sendpro", sendpro))
    app.add_handler(CommandHandler("premiumalert", premium_alert))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, log_channel_post))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_repeating(check_price_alerts, interval=30, first=5)
    app.job_queue.run_repeating(send_pro_daily_update, interval=3600, first=10)
    app.job_queue.run_repeating(send_market_snapshot, interval=3600, first=15)
    app.job_queue.run_repeating(send_top_movers, interval=14400, first=20)
    app.job_queue.run_repeating(send_breaking_alert, interval=900, first=25)
    app.job_queue.run_repeating(send_premium_insight, interval=14400, first=30)
    app.job_queue.run_repeating(send_daily_briefing, interval=86400, first=86400)

    print("Cryp bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
