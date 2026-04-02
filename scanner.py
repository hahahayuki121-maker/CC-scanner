"""
CC Market Scanner v6.2
新增：ORB 強勢突破策略 (專抓 AAOI, PL 這種開盤暴漲股)
優化：量能過濾與期權標籤白話文
"""

import yfinance as yf
import ta
import requests
import pandas as pd
import os
from datetime import datetime
import pytz

# ── 配置區 ────────────────────────────────────────────────────────────────────
TG_TOKEN   = os.environ.get("TG_TOKEN",   "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

TICKERS = {
    "🇺🇸 權值": ["NVDA", "AVGO", "ANET", "VRT", "VST", "TSLA", "AMD", "AMZN", "AAPL", "META", "MSFT", "GOOGL"],
    "🛡️ 資安": ["PANW", "FTNT", "CRWD"],
    "⚛️ 核能": ["SMR", "OKLO", "NNE"],
    "🚀 妖股": ["COIN", "MSTR", "MARA", "CLSK", "HOOD", "SOFI", "APLD", "IONQ", "RGTI", "NVTS", "PLTR", "ONDS", "PATH", "AAOI", "PL", "RCAT","AXTI","TQQQ","LUNR"],
    "🇨🇳 中概": ["BABA", "PDD", "FUTU"],
    "🇹🇼 台股": ["2330.TW", "00631L.TW"],
    "₿ 加密": ["BTC-USD", "ETH-BTC"],
}

# ── 工具函式 ──────────────────────────────────────────────────────────────────
def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: return print(msg)
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                     data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def tw_time(): return datetime.now(pytz.timezone("Asia/Taipei")).strftime("%H:%M:%S")

def grade(score, total):
    pct = score / total
    if pct >= 0.85: return "🏆 S級"
    if pct >= 0.70: return "🥇 A級"
    return "🥈 B級"

def is_us_open():
    ny = datetime.now(pytz.timezone("America/New_York"))
    m = ny.hour * 60 + ny.minute
    return ny.weekday() < 5 and 570 <= m < 960

# ── 數據與指標 ────────────────────────────────────────────────────────────────
def get_data(s, interval, period):
    try:
        df = yf.download(s, interval=interval, period=period, progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        return df.dropna()
    except: return pd.DataFrame()

def rsi(df): return ta.momentum.RSIIndicator(df["Close"]).rsi()
def sma(df, col, n): return ta.trend.SMAIndicator(df[col], window=n).sma_indicator()

# ══════════════════════════════════════════════════════════════════════════════
# ⚡ 核心策略診斷邏輯
# ══════════════════════════════════════════════════════════════════════════════

def scan_logic(sym, tag, df5, df1d):
    df = df5.copy()
    if len(df) < 15: return None
    
    # 指標計算
    df["RSI"] = rsi(df); df["MA5"] = sma(df, "Close", 5); df["MA20"] = sma(df, "Close", 20); df["VMA"] = sma(df, "Volume", 10)
    curr = df.iloc[-1]; prev = df.iloc[-2]
    day_open = df.iloc[0]["Open"]; day_low = df["Low"].min()
    vr = curr["Volume"] / (df["VMA"].iloc[-1] + 1e-6)
    
    # --- [策略 A: ORB 強勢突破] --- 專抓 AAOI, PL
    # 取前三根 5m K線 (15分鐘) 最高點
    orb_hi = df.iloc[0:3]["High"].max()
    if curr["Close"] > orb_hi and prev["Close"] <= orb_hi and vr > 1.2:
        return (f"{tag} 🔥 *[強勢突破]* `{sym}` 🚀\n"
                f"💰 現價: `{curr['Close']:.2f}` · 突破開盤高: `{orb_hi:.2f}`\n"
                f"📊 動能: 🔥 帶量突圍 (量比 {vr:.1f}x)\n"
                f"🎫 操作：**Buy Call** 或 現股追入 (5%倉)\n"
                f"⏰ {tw_time()}")

    # --- [策略 B: WASHOUT 殺低反彈] ---
    drop = (day_open - day_low) / day_open * 100
    c1 = drop > 0.8; c2 = curr["Close"] >= day_open * 0.998; c3 = curr["MA5"] > curr["MA20"]; c4 = vr > 0.6
    
    if (sum([c1, c2, c3, c4]) >= 3) and c1 and c2 and vr >= 0.5:
        # 量能診斷
        if vr < 0.8: vol_status = "⚠️ 縮量 (買氣普通)"
        elif vr < 1.8: vol_status = "✅ 溫和放量 (有人買)"
        else: vol_status = "🔥 爆量攻擊 (動能極強)"
        
        opt_advice = "🎫 操作：**Sell Put** (收租)" if vr < 1.2 else "🎫 操作：現股進場 或 **Buy Call**"
        return (f"{tag} ⚡ *[WASHOUT]* `{sym}` {grade(sum([c1,c2,c3,c4]), 4)}\n"
                f"💰 現價: `{curr['Close']:.2f}` · 跌幅: `{drop:.1f}%`回升\n"
                f"📊 動能: {vol_status}\n"
                f"{opt_advice}\n⏰ {tw_time()}")

    # --- [策略 C: 超買警戒] --- 針對 NVDA 大倉位
    if curr["RSI"] > 80 and curr["Close"] < df["MA5"].iloc[-1]:
        return (f"{tag} ⚠️ *[超買警戒]* `{sym}`\n"
                f"💰 現價: `{curr['Close']:.2f}` · RSI: `{curr['RSI']:.0f}`\n"
                f"🎫 操作：**Sell Call (Covered)** 收租避險\n⏰ {tw_time()}")

    return None

# ── 主程式 ────────────────────────────────────────────────────────────────────
def main():
    if not is_us_open(): return
    for tag, syms in TICKERS.items():
        if tag in ["🇹🇼 台股", "₿ 加密"]: continue
        for sym in syms:
            df5 = get_data(sym, "5m", "2d")
            if df5.empty: continue
            msg = scan_logic(sym, tag, df5, pd.DataFrame())
            if msg: send_tg(msg)

if __name__ == "__main__": main()
