"""
CC Market Scanner v6.3
新增：PANW, FTNT (資安), SMR, OKLO (核能), NVTS (半導體), BABA, PDD (中概)
功能：精確期權策略標籤 (Buy Call / Sell Put / Covered Call)
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
    "🚀 妖股": ["COIN", "MSTR", "MARA", "CLSK", "HOOD", "SOFI", "APLD", "IONQ", "RGTI", "NVTS", "PLTR", "ONDS", "PATH","RCAT","AXTI","TQQQ"],
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
def L(v): return "✅" if v else "❌"

def grade(score, total):
    pct = score / total
    if pct >= 0.85: return "🏆 S級"
    if pct >= 0.70: return "🥇 A級"
    return "🥈 B級"

def is_us_open():
    ny = datetime.now(pytz.timezone("America/New_York"))
    m = ny.hour * 60 + ny.minute
    return ny.weekday() < 5 and 570 <= m < 960 # 9:30 - 16:00

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
# ⚡ 策略邏輯：含期權導航
# ══════════════════════════════════════════════════════════════════════════════

def scan_logic(sym, tag, df5, df1d):
    # 1. 日內 WASHOUT (做多/Buy Call)
    df = df5.copy()
    df["RSI"] = rsi(df); df["MA5"] = sma(df, "Close", 5); df["MA20"] = sma(df, "Close", 20); df["VMA"] = sma(df, "Volume", 10)
    curr = df.iloc[-1]; prev = df.iloc[-2]
    day_open = df.iloc[0]["Open"]; day_low = df["Low"].min()
    drop = (day_open - day_low) / day_open * 100
    vr = curr["Volume"] / (curr["VMA"] + 1e-6)
    
    c1 = drop > 1.3; c2 = curr["Close"] >= day_open; c3 = curr["MA5"] > curr["MA20"]; c4 = vr > 1.1
    score = sum([c1, c2, c3, c4])
    
    if score >= 3 and c1 and c2:
        g = grade(score, 4)
        opt = "🎫 操作：現股進場 或 **Buy Call** (快攻)" if vr > 1.8 else "🎫 操作：現股進場 或 **Sell Put** (收租)"
        return (f"{tag} ⚡ *[WASHOUT]* `{sym}` {g}\n"
                f"💰 現價: `{curr['Close']:.2f}` · 殺低: `{drop:.1f}%` · 量比: `{vr:.1f}x`\n"
                f"{opt}\n⏰ {tw_time()}")

    # 2. 超買警戒 (避險/Covered Call)
    bias = (curr["Close"] - df["MA20"].iloc[-1]) / df["MA20"].iloc[-1] * 100
    c_ob = curr["RSI"] > 78; c_break = curr["Close"] < df["MA5"].iloc[-1]
    if c_ob and c_break:
        return (f"{tag} ⚠️ *[超買警戒]* `{sym}`\n"
                f"💰 現價: `{curr['Close']:.2f}` · RSI: `{curr['RSI']:.0f}`\n"
                f"🎫 操作：**Sell Call (Covered)** 避險收租\n⏰ {tw_time()}")

    # 3. 波段 PULLBACK (定投加碼/Sell Put)
    if not df1d.empty:
        d = df1d.copy()
        d["RSI"] = rsi(d); d["MA60"] = sma(d, "Close", 60)
        dc = d.iloc[-1]; dp = d.iloc[-2]
        if dc["Close"] > d["MA60"].iloc[-1] and dp["RSI"] < 50 and dc["RSI"] > dp["RSI"]:
            return (f"{tag} 📈 *[波段回測]* `{sym}`\n"
                    f"💰 現價: `{dc['Close']:.2f}` · 季線支撐中\n"
                    f"🎫 操作：**Sell Put** 獲取打折買股權\n⏰ {tw_time()}")
    return None

# ── 主程式 ────────────────────────────────────────────────────────────────────
def main():
    if not is_us_open(): 
        print("非美股交易時段"); return

    for tag, syms in TICKERS.items():
        if tag in ["🇹🇼 台股", "₿ 加密"]: continue
        for sym in syms:
            df5 = get_data(sym, "5m", "2d")
            if df5.empty: continue
            df1d = get_data(sym, "1d", "100d")
            msg = scan_logic(sym, tag, df5, df1d)
            if msg: send_tg(msg)

if __name__ == "__main__": main()
