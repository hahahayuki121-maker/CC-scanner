"""
CC Market Scanner v6.8 終極整合版
- 監控時段：台灣時間 16:00 (美東 04:00) 起全天候監控
- 防偽機制：排除休市日數據回補、過濾 15 分鐘前的舊 K 線
- 智能量能：盤前 > 2.5x 才發、妖股 > 1.2x 才發、權值股 > 0.8x 即發 (溫和放量)
- 期權策略：內建 Buy Call / Sell Put / Covered Call 建議
"""

import yfinance as yf
import ta
import requests
import pandas as pd
import os
from datetime import datetime
import pytz

# ── 1. 配置區 ──────────────────────────────────────────────────────────────────
TG_TOKEN   = os.environ.get("TG_TOKEN",   "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

TICKERS = {
    "🇺🇸 權值": ["NVDA", "AVGO", "ANET", "VRT", "VST", "TSLA", "AMD", "AMZN", "AAPL", "META", "MSFT", "GOOGL"],
    "🛡️ 資安": ["PANW", "FTNT", "CRWD"],
    "⚛️ 核能": ["SMR", "OKLO", "NNE"],
    "🚀 妖股": ["COIN", "MSTR", "MARA", "CLSK", "HOOD", "SOFI", "APLD", "IONQ", "RGTI", "NVTS", "PLTR", "ONDS", "PATH", "AAOI", "PL", "RCAT", "AXTI", "TQQQ", "LUNR"],
    "🇨🇳 中概": ["BABA", "PDD", "FUTU"],
    "🇹🇼 台股": ["2330.TW", "00631L.TW"],
    "₿ 加密": ["BTC-USD", "ETH-BTC"],
}

# ── 2. 工具函式 ────────────────────────────────────────────────────────────────
def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: return print(msg)
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                     data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def tw_time(): return datetime.now(pytz.timezone("Asia/Taipei")).strftime("%H:%M:%S")

def get_market_status():
    ny = datetime.now(pytz.timezone("America/New_York"))
    m = ny.hour * 60 + ny.minute
    if ny.weekday() >= 5: return "CLOSED"
    if 240 <= m < 570: return "PRE"      # 04:00 - 09:30
    if 570 <= m < 960: return "REGULAR"  # 09:30 - 16:00
    return "CLOSED"

# ── 3. 數據獲取與校驗 ────────────────────────────────────────────────────────────
def get_data(s, interval, period):
    try:
        # 開啟 prepost=True 抓取盤前
        df = yf.download(s, interval=interval, period=period, progress=False, auto_adjust=True, prepost=True)
        if df.empty: return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        
        # 🛡️ 數據時效性檢查 (排除休市與舊訊號)
        last_ts = df.index[-1].astimezone(pytz.timezone("America/New_York"))
        now_ny = datetime.now(pytz.timezone("America/New_York"))
        if (now_ny - last_ts).total_seconds() > 900: # 15分鐘保險
            return pd.DataFrame()
            
        return df.dropna()
    except: return pd.DataFrame()

def rsi(df): return ta.momentum.RSIIndicator(df["Close"]).rsi()
def sma(df, col, n): return ta.trend.SMAIndicator(df[col], window=n).sma_indicator()

# ── 4. 核心診斷邏輯 ──────────────────────────────────────────────────────────────
def scan_logic(sym, tag, df5):
    df = df5.copy()
    if len(df) < 15: return None
    status = get_market_status()
    
    # 指標計算
    df["RSI"] = rsi(df); df["MA5"] = sma(df, "Close", 5); df["MA20"] = sma(df, "Close", 20); df["VMA"] = sma(df, "Volume", 10)
    curr = df.iloc[-1]; prev = df.iloc[-2]
    day_open = df.iloc[0]["Open"]; day_low = df["Low"].min()
    vr = curr["Volume"] / (df["VMA"].iloc[-1] + 1e-6)
    
    # --- 🏥 智能量能門檻 (辨證施治) ---
    if status == "PRE":
        min_vr = 2.5  # 盤前：只看爆量
    elif "🚀" in tag:
        min_vr = 1.2  # 妖股：需要顯著放量
    else:
        min_vr = 0.8  # 權值/資安：溫和放量即可

    if vr < min_vr: return None # 量能不足直接消音

    # --- [策略 A: 強勢突破] --- 專抓 AAOI, PL
    orb_hi = df.iloc[0:3]["High"].max()
    if curr["Close"] > orb_hi and prev["Close"] <= orb_hi and vr > 1.2:
        prefix = "🌅 [盤前強勢]" if status == "PRE" else "🔥 [強勢突破]"
        return (f"{tag} {prefix} `{sym}` 🚀\n"
                f"💰 現價: `{curr['Close']:.2f}` · 突破開盤高\n"
                f"📊 動能: {vr:.1f}x (強勁突破)\n"
                f"🎫 操作: **Buy Call** 或 現股追入\n⏰ {tw_time()}")

    # --- [策略 B: WASHOUT 殺低反彈] ---
    drop = (day_open - day_low) / day_open * 100
    c1 = drop > 0.8; c2 = curr["Close"] >= day_open * 0.998; c3 = curr["MA5"] > curr["MA20"]
    
    if (sum([c1, c2, c3]) >= 2) and c1 and c2:
        prefix = "🌅 [盤前洗盤]" if status == "PRE" else "⚡ [WASHOUT]"
        vol_txt = "🔥 爆量攻擊" if vr > 2.0 else "✅ 溫和放量"
        opt = "🎫 操作: **Sell Put** (收租)" if vr < 1.5 else "🎫 操作: 現股 或 **Buy Call**"
        return (f"{tag} {prefix} `{sym}`\n"
                f"💰 現價: `{curr['Close']:.2f}` · 跌幅: `{drop:.1f}%`回升\n"
                f"📊 動能: {vol_txt} ({vr:.1f}x)\n"
                f"{opt}\n⏰ {tw_time()}")

    # --- [策略 C: 超買警戒] --- 針對 NVDA 大倉位
    if curr["RSI"] > 80 and curr["Close"] < df["MA5"].iloc[-1]:
        return (f"{tag} ⚠️ *[超買警戒]* `{sym}`\n"
                f"💰 現價: `{curr['Close']:.2f}` · RSI: `{curr['RSI']:.0f}`\n"
                f"🎫 操作: **Sell Call (Covered)** 收租避險\n⏰ {tw_time()}")

    return None

# ── 5. 主程式 ──────────────────────────────────────────────────────────────────
def main():
    status = get_market_status()
    if status == "CLOSED":
        print(f"[{tw_time()}] 休市中或非監控時段。")
        return
        
    for tag, syms in TICKERS.items():
        if tag in ["🇹🇼 台股", "₿ 加密"]: continue
        for sym in syms:
            df5 = get_data(sym, "5m", "2d")
            if df5.empty: continue 
            msg = scan_logic(sym, tag, df5)
            if msg: send_tg(msg)

if __name__ == "__main__": main()
