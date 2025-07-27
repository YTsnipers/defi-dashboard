# main.py - Render 雲端版本 (改進版 - 防止推播任務靜默停止)
#!/usr/bin/env python3

import datetime
import json
import os
import logging
import requests
import asyncio
import threading
import time
import signal
import sys
from datetime import timedelta
from flask import Flask, request, jsonify, render_template_string
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# === 設定日誌 ===
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# === 配置常數 ===
BOT_TOKEN = os.getenv("BOT_TOKEN")  # 從環境變數讀取
SUB_FILE = "subscribers.json"
PORT = int(os.getenv("PORT", 10000))  # Render 預設端口
WEBHOOK_PATH = "/webhook"
REQUEST_TIMEOUT = 10

# === Hyperliquid 設定 ===
HYPERLIQUID_API_URL = "https://api.hyperliquid.xyz/info"
HYPERLIQUID_ASSETS = ["BTC", "ETH", "HYPE", "BNB", "SOL", "AAVE", "SUI", "ENA", "DOGE", "PENDLE"]

# === PENDLE API URLs ===
MAGPIE_API_URL = "https://dev.api.magpiexyz.io/poolsnapshot/get?chainId=42161&domain=www.pendle.magpiexyz.io"
TARGET_POOL_ID = 6

PENDLE_URLS = {
    "mPendle": "https://api-v2.pendle.finance/core/v2/42161/markets/0x4e77520688601ceb5d4bbd217763640a689956cd/data",
    "fGHO": "https://api-v2.pendle.finance/core/v2/1/markets/0xc64d59eb11c869012c686349d24e1d7c91c86ee2/data",
    "USDS-SPK": "https://api-v2.pendle.finance/core/v2/1/markets/0xff43e751f2f07bbf84da1fc1fa12ce116bf447e5/data",
    "X33": "https://api-v2.pendle.finance/core/v2/146/markets/0x6d3ecf7a9fc726387bb6a91fffb4f90d1f38139c/data",
    "ClisBNB": "https://api-v2.pendle.finance/core/v2/56/markets/0xbd577ddabb5a1672d3c786726b87a175de652b96/data",
    "fxSAVE": "https://api-v2.pendle.finance/core/v2/1/markets/0x9bc2fb257e00468fe921635fe5a73271f385d0eb/data",
    "RLP": "https://api-v2.pendle.finance/core/v2/1/markets/0x55f06992e4c3ed17df830da37644885c0c34edda/data"
}

MERKL_API_URL = "https://api.merkl.xyz/v4/opportunities?sort=apr&items=10&page=0&tags=puffer&excludeSubCampaigns=true&order=desc"
MERKL_IDENTIFIERS = {
    "0xf00032d0F95e8f43E750C51d0188DCa33cC5a8eA": "CARROT-USDC LP",
    "0xb1dd1A6f9A9f09867C7A128d99E4C1f9510d8466": "PufETH YT ",
    "0xacb27f846a11b0727772d980e55fca65292f5253": "Staking CARROT"
}

# === 全域變數 ===
app = Flask(__name__)
telegram_app = None
app_loop = None
subscribers = set()
auto_push_enabled = True
push_interval = 300  # 5 分鐘

# === 任務監控變數 ===
last_push_time = 0
push_task_running = False
task_restart_count = 0

# === 儀表板 HTML ===
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DeFi Yield Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8f9fa; min-height: 100vh; color: #495057; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .header { text-align: center; margin-bottom: 40px; color: #343a40; }
        .header h1 { font-size: 3rem; font-weight: 700; margin-bottom: 10px; color: #212529; }
        .header p { font-size: 1.2rem; color: #6c757d; }
        .status-banner { background: linear-gradient(135deg, #28a745, #20c997); color: white; text-align: center; padding: 15px; border-radius: 10px; margin-bottom: 30px; }
        .pools-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 18px; margin-bottom: 40px; }
        .pool-card { background: white; border-radius: 12px; padding: 18px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); border: 1px solid #e9ecef; transition: all 0.3s ease; }
        .pool-card:hover { transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,0,0,0.12); }
        .pool-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #e9ecef; }
        .pool-name { font-size: 1.4rem; font-weight: 600; color: #212529; }
        .pool-type { background: #6c757d; color: white; padding: 6px 12px; border-radius: 16px; font-size: 0.8rem; font-weight: 500; }
        .yield-info { display: grid; gap: 10px; }
        .yield-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #f8f9fa; }
        .yield-row:last-child { border-bottom: none; }
        .yield-label { font-size: 0.95rem; color: #6c757d; font-weight: 500; }
        .yield-value { font-size: 1.1rem; font-weight: 600; color: #495057; }
        .yield-value.underlying-higher { color: #28a745; }
        .yield-value.underlying-lower { color: #dc3545; }
        .section-title { color: #212529; font-size: 2rem; font-weight: 600; margin: 50px 0 30px 0; text-align: center; }
        .hyperliquid-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; }
        .funding-card { background: white; border-radius: 12px; padding: 15px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); border: 1px solid #e9ecef; transition: all 0.3s ease; }
        .funding-card:hover { transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,0,0,0.12); }
        .funding-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .asset-name { font-size: 1.1rem; font-weight: 600; color: #212529; }
        .funding-rate { font-size: 1.2rem; font-weight: 700; color: #495057; }
        .footer { margin-top: 60px; text-align: center; color: #6c757d; padding: 20px; }
        .refresh-btn { position: fixed; bottom: 30px; right: 30px; background: #495057; color: white; border: none; width: 60px; height: 60px; border-radius: 50%; font-size: 1.5rem; cursor: pointer; box-shadow: 0 4px 16px rgba(0,0,0,0.15); transition: all 0.3s ease; z-index: 1000; }
        .refresh-btn:hover { background: #343a40; transform: scale(1.05); box-shadow: 0 6px 20px rgba(0,0,0,0.2); }
        @media (max-width: 768px) { .container { padding: 15px; } .header h1 { font-size: 2rem; } .pools-grid { grid-template-columns: 1fr; gap: 15px; } .hyperliquid-grid { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; } .pool-card, .funding-card { padding: 15px; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>DeFi Yield Dashboard</h1>
            <p>Real-time tracking of PENDLE yields and Hyperliquid funding rates</p>
        </div>
        
        <div class="status-banner">
            🚀 Running on Render | Bot: {{ 'Online' if bot_running else 'Offline' }} | Subscribers: {{ subscriber_count }} | Last updated: {{ last_update }} | Task: {{ 'Active' if task_running else 'Inactive' }}
        </div>
        
        <div class="pools-grid">
            {% for pool in pendle_data %}
            <div class="pool-card">
                <div class="pool-header">
                    <div class="pool-name">{{ pool.name }}</div>
                    <div class="pool-type">{{ pool.type }}</div>
                </div>
                <div class="yield-info">
                    {% if pool.staking_apy %}
                    <div class="yield-row">
                        <span class="yield-label">Staking APY</span>
                        <span class="yield-value">{{ pool.staking_apy }}</span>
                    </div>
                    {% endif %}
                    <div class="yield-row">
                        <span class="yield-label">Implied APY</span>
                        <span class="yield-value">{{ pool.implied_apy }}</span>
                    </div>
                    <div class="yield-row">
                        <span class="yield-label">Underlying APY</span>
                        <span class="yield-value {{ pool.underlying_class }}">{{ pool.underlying_apy }}</span>
                    </div>
                </div>
            </div>
            {% endfor %}
            
            <div class="pool-card">
                <div class="pool-header">
                    <div class="pool-name">$carrot</div>
                    <div class="pool-type">Puffer Eco</div>
                </div>
                <div class="yield-info">
                    {% for merkl_item in merkl_data %}
                    <div class="yield-row">
                        <span class="yield-label">{{ merkl_item.name }}</span>
                        <span class="yield-value">{{ merkl_item.apr }}</span>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
        
        <div class="section-title">Hyperliquid Funding Rates APR</div>
        
        <div class="hyperliquid-grid">
            {% for funding in hyperliquid_data %}
            <div class="funding-card">
                <div class="funding-header">
                    <div class="asset-name">{{ funding.asset }}</div>
                    <div class="funding-rate">{{ funding.rate }}</div>
                </div>
            </div>
            {% endfor %}
        </div>
        
        <div class="footer">
            <p>&copy; 2025 DeFi Yield Dashboard | Powered by Render</p>
            <p style="margin-top: 10px; font-size: 0.9rem;">Telegram: /start (subscribe) | /check (view) | /stop (unsubscribe)</p>
        </div>
    </div>
    
    <button class="refresh-btn" onclick="location.reload()">⟳</button>
</body>
</html>"""

# === 取得 Render URL ===
def get_app_url():
    """取得 Render 應用 URL"""
    render_external_url = os.getenv("RENDER_EXTERNAL_URL")
    if render_external_url:
        return render_external_url
    
    service_name = os.getenv("RENDER_SERVICE_NAME", "defi-dashboard")
    return f"https://{service_name}.onrender.com"

# === 輔助函數 ===
def load_subscribers():
    """載入訂閱者清單"""
    try:
        if os.path.exists(SUB_FILE):
            with open(SUB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data) if isinstance(data, list) else set()
        return set()
    except Exception as e:
        logger.error(f"Failed to load subscribers: {e}")
        return set()

def save_subscribers(subs):
    """儲存訂閱者清單"""
    try:
        with open(SUB_FILE, "w", encoding="utf-8") as f:
            json.dump(list(subs), f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(subs)} subscribers")
    except Exception as e:
        logger.error(f"Failed to save subscribers: {e}")

def fetch_api_data(url, description=""):
    """通用 API 資料擷取函數"""
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"{description} API request failed: {e}")
        return None

def get_funding_rates(asset_names):
    """取得 Hyperliquid 資金費率"""
    try:
        payload = {"type": "metaAndAssetCtxs"}
        headers = {"Content-Type": "application/json"}
        resp = requests.post(HYPERLIQUID_API_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        meta, asset_contexts = resp.json()
        
        asset_map = {asset["name"].upper(): idx for idx, asset in enumerate(meta["universe"])}
        rates = {}
        
        for name in asset_names:
            idx = asset_map.get(name.upper())
            if idx is not None:
                ctx = asset_contexts[idx]
                rate = float(ctx.get("funding", 0))
                rates[name] = rate
            else:
                logger.warning(f"Asset {name} not found in Hyperliquid")
                
        return rates
    except Exception as e:
        logger.error(f"Failed to get Hyperliquid funding rates: {e}")
        return {}

def calculate_apr(hourly_rate):
    """計算年化報酬率"""
    return hourly_rate * 24 * 365

# === 數據處理函數 ===
def get_dashboard_data():
    """獲取儀表板數據"""
    try:
        # 獲取 PENDLE 數據
        pendle_data = []
        
        # 獲取 Magpie 數據
        magpie_data = fetch_api_data(MAGPIE_API_URL, "Magpie")
        staking_apy = None
        if magpie_data and "data" in magpie_data:
            pools = magpie_data["data"]["snapshot"]["pools"]
            target_pool = next((p for p in pools if p.get("poolId") == TARGET_POOL_ID), None)
            if target_pool and "aprInfo" in target_pool:
                apr = target_pool["aprInfo"]["value"]
                staking_apy = f"{apr*100:.2f}%"

        # 處理每個 PENDLE 池
        pool_types = {
            "mPendle": "Pendle YT",
            "fGHO": "Pendle YT", 
            "USDS-SPK": "Pendle YT",
            "X33": "Pendle YT",
            "ClisBNB": "Pendle YT",
            "fxSAVE": "Pendle YT",
            "RLP": "Pendle YT"
        }
        
        for name, url in PENDLE_URLS.items():
            pool_info = {"name": name, "type": pool_types.get(name, "Pool")}
            
            if name == "mPendle" and staking_apy:
                pool_info["staking_apy"] = staking_apy
                
            pendle_data_api = fetch_api_data(url, f"Pendle {name}")
            if pendle_data_api:
                implied_apy = pendle_data_api.get("impliedApy")
                underlying_apy = pendle_data_api.get("underlyingApy")
                
                if implied_apy is not None:
                    pool_info["implied_apy"] = f"{implied_apy*100:.2f}%"
                else:
                    pool_info["implied_apy"] = "N/A"
                    
                if underlying_apy is not None:
                    pool_info["underlying_apy"] = f"{underlying_apy*100:.2f}%"
                    # 比較 Underlying 和 Implied APY
                    if implied_apy is not None:
                        if underlying_apy > implied_apy:
                            pool_info["underlying_class"] = "underlying-higher"
                        else:
                            pool_info["underlying_class"] = "underlying-lower"
                    else:
                        pool_info["underlying_class"] = ""
                else:
                    pool_info["underlying_apy"] = "N/A"
                    pool_info["underlying_class"] = ""
            else:
                pool_info["implied_apy"] = "API Error"
                pool_info["underlying_apy"] = "API Error"
                pool_info["underlying_class"] = ""
                
            pendle_data.append(pool_info)

        # 獲取 Merkl 數據
        merkl_data = []
        merkl_api_data = fetch_api_data(MERKL_API_URL, "Merkl")
        if merkl_api_data and isinstance(merkl_api_data, list):
            merkl_result = {item["identifier"]: item["apr"] for item in merkl_api_data}
            for identifier, display_name in MERKL_IDENTIFIERS.items():
                apr = merkl_result.get(identifier)
                merkl_data.append({
                    "name": display_name,
                    "apr": f"{apr:.2f}%" if apr is not None else "N/A"
                })
        else:
            for display_name in MERKL_IDENTIFIERS.values():
                merkl_data.append({
                    "name": display_name,
                    "apr": "API Error"
                })

        # 獲取 Hyperliquid 數據
        hyperliquid_data = []
        rates = get_funding_rates(HYPERLIQUID_ASSETS)
        if rates:
            for asset, rate in rates.items():
                apr = calculate_apr(rate) * 100
                hyperliquid_data.append({
                    "asset": asset,
                    "rate": f"{apr:.2f}%"
                })
        else:
            for asset in HYPERLIQUID_ASSETS:
                hyperliquid_data.append({
                    "asset": asset,
                    "rate": "API Error"
                })

        return {
            "pendle_data": pendle_data,
            "merkl_data": merkl_data,
            "hyperliquid_data": hyperliquid_data,
            "last_update": datetime.datetime.now().strftime('%H:%M:%S'),
            "bot_running": telegram_app is not None,
            "subscriber_count": len(subscribers),
            "task_running": push_task_running
        }
        
    except Exception as e:
        logger.error(f"Failed to get dashboard data: {e}")
        return None

# === Telegram 相關函數 ===
def get_combined_message():
    """產生整合訊息（Telegram 用）"""
    timestamp = (datetime.datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
    
    lines = [
        f"{timestamp} (UTC+8) | ",
        "_" * 33,
        ""
    ]
    
    # PENDLE 收益率
    pendle_msg = get_pendle_message()
    lines.append(pendle_msg)
    
    lines.append("_" * 33)
    lines.append("")
    
    # Hyperliquid 資金費率
    hyperliquid_msg = get_hyperliquid_message()
    lines.append(hyperliquid_msg)
    
    lines.append("_" * 33)
    
    return "\n".join(lines)

def get_pendle_message():
    """產生 PENDLE 收益率訊息（Telegram 用）"""
    lines = []

    # 獲取 Magpie 數據
    magpie_data = fetch_api_data(MAGPIE_API_URL, "Magpie")
    staking_apy = None
    if magpie_data and "data" in magpie_data:
        pools = magpie_data["data"]["snapshot"]["pools"]
        target_pool = next((p for p in pools if p.get("poolId") == TARGET_POOL_ID), None)
        if target_pool and "aprInfo" in target_pool:
            apr = target_pool["aprInfo"]["value"]
            staking_apy = f"{apr*100:.2f}%"

    # 獲取 Pendle 數據
    for name, url in PENDLE_URLS.items():
        pendle_data = fetch_api_data(url, f"Pendle {name}")
        
        lines.append(f"{name}:")
        
        # 如果是 mPendle，加入 Staking APY
        if name == "mPendle" and staking_apy:
            lines.append(f"• Staking APY: {staking_apy}")
            
        if pendle_data:
            implied_apy = pendle_data.get("impliedApy")
            underlying_apy = pendle_data.get("underlyingApy")
            
            if implied_apy is not None:
                lines.append(f"• Implied APY: {implied_apy*100:.2f}%")
            else:
                lines.append(f"• Implied APY: N/A")
                
            if underlying_apy is not None:
                lines.append(f"• Underlying APY: {underlying_apy*100:.2f}%")
            else:
                lines.append(f"• Underlying APY: N/A")
        else:
            lines.append(f"• API Error")
        
        lines.append("")

    # 獲取 Merkl 數據
    merkl_data = fetch_api_data(MERKL_API_URL, "Merkl")
    if merkl_data and isinstance(merkl_data, list):
        merkl_result = {item["identifier"]: item["apr"] for item in merkl_data}
        lines.append("$carrot APR:")
        
        for identifier, display_name in MERKL_IDENTIFIERS.items():
            apr = merkl_result.get(identifier)
            if apr is not None:
                lines.append(f"• {display_name}: {apr:.2f}%")
            else:
                lines.append(f"• {display_name}: N/A")
    else:
        lines.append("$carrot APR:")
        lines.append("• API Error")

    return "\n".join(lines)

def get_hyperliquid_message():
    """產生 Hyperliquid 資金費率訊息（Telegram 用）"""
    lines = ["Hyperliquid funding rate APR:"]
    
    try:
        rates = get_funding_rates(HYPERLIQUID_ASSETS)
        if not rates:
            lines.append("• API Error")
            return "\n".join(lines)
            
        for asset, rate in rates.items():
            apr = calculate_apr(rate) * 100
            lines.append(f"• {asset} = {apr:.2f}%")
            
    except Exception as e:
        lines.append(f"• Data Error: {e}")
        
    return "\n".join(lines)

# === Telegram 指令處理 ===
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global subscribers
    try:
        chat_id = update.effective_chat.id
        if chat_id not in subscribers:
            subscribers.add(chat_id)
            save_subscribers(subscribers)
        
        app_url = get_app_url()
        
        await update.message.reply_text(
            "Welcome to yield & funding rate updates!\n"
            f"Auto push: Every {push_interval//60} minutes\n"
            "Use /check to view immediately\n"
            "Use /stop to unsubscribe\n"
            f"Dashboard: {app_url}"
        )
    except Exception as e:
        logger.error(f"handle_start error: {e}")

async def handle_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global subscribers
    try:
        chat_id = update.effective_chat.id
        if chat_id in subscribers:
            subscribers.remove(chat_id)
            save_subscribers(subscribers)
        await update.message.reply_text("Successfully unsubscribed")
    except Exception as e:
        logger.error(f"handle_stop error: {e}")

async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        status_message = await update.message.reply_text("Fetching latest data...")
        message = get_combined_message()
        await status_message.edit_text(message)
    except Exception as e:
        logger.error(f"handle_check error: {e}")

async def send_to_all_subscribers(message):
    """發送訊息給所有訂閱者（改進版）"""
    global subscribers
    if not subscribers:
        logger.info("No subscribers, skipping push")
        return
    
    failed_chats = []
    success_count = 0
    
    logger.info(f"Starting push to {len(subscribers)} subscribers")
    
    for chat_id in subscribers.copy():
        try:
            await telegram_app.bot.send_message(chat_id=chat_id, text=message)
            success_count += 1
            await asyncio.sleep(0.1)  # 防止過快發送
        except Exception as e:
            logger.warning(f"Push failed chat_id={chat_id}: {e}")
            failed_chats.append(chat_id)
    
    # 移除失效的 chat_id
    if failed_chats:
        for chat_id in failed_chats:
            subscribers.discard(chat_id)
        save_subscribers(subscribers)
        logger.info(f"Removed {len(failed_chats)} failed chat IDs")
    
    logger.info(f"Auto push completed: {success_count} sent, {len(failed_chats)} failed")

# === 改進版自動推播任務 ===
async def auto_push_task():
    """改進版自動推播任務 - 防止靜默停止"""
    global auto_push_enabled, push_interval, last_push_time, push_task_running
    
    logger.info("🚀 Auto push task started with enhanced error handling")
    push_task_running = True
    
    consecutive_errors = 0
    max_consecutive_errors = 3
    
    while True:
        try:
            current_time = time.time()
            
            if auto_push_enabled and subscribers:
                try:
                    logger.info(f"📡 Starting auto push to {len(subscribers)} subscribers...")
                    message = get_combined_message()
                    
                    if not message or len(message.strip()) == 0:
                        logger.error("❌ Generated message is empty!")
                        raise Exception("Empty message generated")
                    
                    await send_to_all_subscribers(message)
                    last_push_time = current_time
                    consecutive_errors = 0  # 重置錯誤計數
                    logger.info(f"✅ Auto push completed successfully")
                    
                except Exception as push_error:
                    consecutive_errors += 1
                    logger.error(f"❌ Push error (attempt {consecutive_errors}): {push_error}")
                    logger.error(f"Push error details: {str(push_error)}")
                    
                    if consecutive_errors >= max_consecutive_errors:
                        logger.error(f"🔥 Too many consecutive errors ({consecutive_errors}), will retry with longer delay")
                        await asyncio.sleep(60)  # 等待更久再重試
                        consecutive_errors = 0
                        
            else:
                logger.info(f"⏭️ Skipping push (enabled: {auto_push_enabled}, subscribers: {len(subscribers)})")
            
            # 安全的睡眠
            try:
                logger.info(f"💤 Sleeping for {push_interval} seconds until next push...")
                await asyncio.sleep(push_interval)
            except Exception as sleep_error:
                logger.error(f"❌ Sleep error: {sleep_error}")
                # 如果 sleep 失败，使用备用等待时间
                await asyncio.sleep(300)  # 5 分钟备用
                
        except Exception as task_error:
            logger.error(f"🔥 Critical auto push task error: {task_error}")
            logger.error(f"Task error details: {str(task_error)}")
            
            # 即使出现严重错误，也要继续运行
            try:
                await asyncio.sleep(60)  # 等待1分钟后重试
            except:
                pass  # 确保不会在 sleep 时也崩溃

# === 任务监控 ===
async def task_monitor():
    """监控推播任务是否还活着"""
    global last_push_time, push_task_running, task_restart_count
    
    logger.info("🔍 Task monitor started")
    
    while True:
        try:
            current_time = time.time()
            time_since_last_push = current_time - last_push_time
            
            # 如果超过推播间隔的2倍时间没有推播，认为任务可能死亡
            if last_push_time > 0 and time_since_last_push > (push_interval * 2):
                logger.warning(f"⚠️ Push task seems inactive. Last push: {time_since_last_push:.0f}s ago")
                
                if time_since_last_push > (push_interval * 3):
                    logger.error(f"🚨 Push task appears dead! Attempting restart...")
                    push_task_running = False
                    task_restart_count += 1
                    
                    # 尝试重启推播任务
                    if app_loop:
                        app_loop.create_task(auto_push_task())
                        logger.info(f"🔄 Push task restarted (attempt #{task_restart_count})")
            
            await asyncio.sleep(120)  # 每2分钟检查一次
            
        except Exception as monitor_error:
            logger.error(f"❌ Task monitor error: {monitor_error}")
            await asyncio.sleep(60)

# === Flask 路由 ===
@app.route('/')
def dashboard():
    """主儀表板頁面"""
    try:
        data = get_dashboard_data()
        if data:
            return render_template_string(DASHBOARD_HTML, **data)
        else:
            return render_template_string(DASHBOARD_HTML, 
                pendle_data=[], 
                merkl_data=[], 
                hyperliquid_data=[], 
                last_update="Error",
                bot_running=False,
                subscriber_count=0,
                task_running=False
            )
    except Exception as e:
        logger.error(f"Dashboard page error: {e}")
        return f"Error: {e}", 500

@app.route('/health')
def health_check():
    """健康檢查端點 - 防止 Render 休眠"""
    current_time = time.time()
    time_since_last_push = current_time - last_push_time if last_push_time > 0 else 0
    
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.datetime.now().isoformat(),
        "bot_running": telegram_app is not None,
        "subscribers": len(subscribers),
        "push_task_running": push_task_running,
        "last_push_ago": f"{time_since_last_push:.0f}s" if last_push_time > 0 else "never",
        "task_restarts": task_restart_count
    })

@app.route('/api/yields')
def api_yields():
    """API 端點返回 JSON 數據"""
    try:
        data = get_dashboard_data()
        if data:
            return jsonify(data)
        else:
            return jsonify({"error": "Failed to fetch data"}), 500
    except Exception as e:
        logger.error(f"API endpoint error: {e}")
        return jsonify({"error": str(e)}), 500

# === Webhook 處理 ===
@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    """處理 Telegram webhook"""
    try:
        data = request.get_json(force=True)
        if telegram_app and app_loop:
            update = Update.de_json(data, telegram_app.bot)
            asyncio.run_coroutine_threadsafe(
                telegram_app.process_update(update),
                app_loop
            )
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

# === 設定 Telegram Webhook ===
def setup_webhook():
    """設定 Telegram webhook for Render"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set, cannot setup webhook")
        return False
        
    app_url = get_app_url()
    webhook_url = f"{app_url}{WEBHOOK_PATH}"
    
    logger.info(f"Setting Telegram Webhook: {webhook_url}")
    
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            json={"url": webhook_url},
            timeout=10
        )
        result = response.json()
        if result.get("ok"):
            logger.info("✅ Webhook setup successful")
            return True
        else:
            logger.error(f"❌ Webhook setup failed: {result}")
            return False
    except Exception as e:
        logger.error(f"❌ Webhook setup error: {e}")
        return False

# === 初始化 Telegram 應用程式 ===
async def setup_telegram():
    """設定 Telegram 應用程式"""
    global telegram_app, app_loop
    
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not set, cannot initialize Telegram app")
        return False
    
    try:
        logger.info("🤖 Initializing Telegram application...")
        telegram_app = ApplicationBuilder().token(BOT_TOKEN).build()
        app_loop = asyncio.get_running_loop()
        
        # 註冊指令處理器
        telegram_app.add_handler(CommandHandler("start", handle_start))
        telegram_app.add_handler(CommandHandler("stop", handle_stop))
        telegram_app.add_handler(CommandHandler("check", handle_check))
        
        await telegram_app.initialize()
        await telegram_app.start()
        
        logger.info("✅ Telegram application initialized successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Telegram app initialization failed: {e}")
        return False

# === 主程序 ===
def run_async_loop():
    """在背景執行 asyncio loop"""
    global app_loop, last_push_time
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app_loop = loop
    
    try:
        # 設定 Telegram 應用程式
        success = loop.run_until_complete(setup_telegram())
        
        if success:
            # 初始化推播时间
            last_push_time = time.time()
            
            # 啟動自動推播任務
            loop.create_task(auto_push_task())
            logger.info("✅ Auto push task scheduled")
            
            # 啟動任務監控
            loop.create_task(task_monitor())
            logger.info("✅ Task monitor scheduled")
            
        else:
            logger.error("❌ Telegram setup failed, web service only")
        
        # 保持 loop 運行
        loop.run_forever()
    except Exception as e:
        logger.error(f"❌ Asyncio loop error: {e}")
    finally:
        loop.close()

def main():
    """主程序 - Render 雲端版"""
    global subscribers
    
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable not set")
        print("Error: BOT_TOKEN environment variable not set")
        return
    
    print("🚀 Starting Complete DeFi Dashboard + Telegram Bot (Enhanced Version)")
    print("Features: Dashboard + Auto Push to Telegram + Enhanced Error Handling")
    print("Tracking assets: BTC, ETH, HYPE, BNB, SOL, AAVE, SUI, ENA, DOGE, PENDLE")
    
    # 載入訂閱者
    subscribers = load_subscribers()
    print(f"📋 Loaded {len(subscribers)} subscribers")
    
    # 在背景啟動 asyncio loop (Telegram bot)
    async_thread = threading.Thread(target=run_async_loop, daemon=True)
    async_thread.start()
    print("🔄 Telegram background tasks started")
    
    # 等待 Telegram 應用程式初始化
    time.sleep(3)
    
    # 設定 webhook
    app_url = get_app_url()
    if setup_webhook():
        print(f"✅ Telegram webhook setup successful: {app_url}{WEBHOOK_PATH}")
    else:
        print("❌ Telegram webhook setup failed")
    
    # 啟動 Flask 應用程式
    print(f"🌐 Dashboard URL: {app_url}")
    print(f"❤️ Health check: {app_url}/health")
    print("")
    print("✨ Enhanced Features:")
    print("   ✓ Real-time yield dashboard")
    print("   ✓ Curve Finance-style UI")
    print("   ✓ Underlying APY color coding")
    print("   ✓ Telegram bot with auto push")
    print("   ✓ /start /check /stop commands")
    print(f"   ✓ Auto push every {push_interval//60} minutes")
    print("   ✓ Health check endpoint for monitoring")
    print("   ✓ Enhanced error handling & task monitoring")
    print("   ✓ Automatic task restart on failure")
    
    try:
        app.run(host="0.0.0.0", port=PORT, debug=False)
    except KeyboardInterrupt:
        print("\n👋 Dashboard stopped")

if __name__ == "__main__":
    main()
