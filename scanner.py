"""
CC Market Scanner - 最終版 v4 (修正純文字版)
標籤：🇺🇸日內 / 🇺🇸波段 / 🇹🇼日內 / 🇹🇼波段 / ₿加密
"""

import yfinance as yf
import ta  # 改用穩定版 ta 庫，確保 GitHub Actions 不會報錯
import requests
import pandas as pd
import os
from datetime import datetime
import pytz

# -- Token ---------------------------------------------------------------------
TG_TOKEN   = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# -- 監控名單 ------------------------------------------------------------------
US_TICKERS = ["NVDA","TSLA","AMD","AAPL","META","PLTR","SOFI","COIN","F","BAC","T","SNAP","PATH","DOCU","XLE","GLD","TLT","AXTI"]

# -- 功能函數 ------------------------------------------------------------------
def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        return r.json().get("ok", False)
    except: return False

def tw_time():
    return datetime.now(pytz.timezone("Asia/Taipei")).strftime("%H:%M:%S")

def L(v): return "✅" if v else "❌"

def us_mins():
    ny = datetime.now(pytz.timezone("America/New_York"))
    return -1 if ny.weekday() >= 5 else ny.hour * 60 + ny.minute

def is_us_open():   return 570 <= us_mins() < 930
def is_us_swing():  return 900 <= us_mins() < 930

def _clean(df):
    if df is None or df.empty: return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

# -- 策略邏輯 ------------------------------------------------------------------
def strategy_washout(sym, df_5m, label):
    if len(df_5m) < 15: return
    df = df_5m.copy()
    # 修正：使用 ta 庫計算 RSI
    df["RSI"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
    curr = df.iloc[-1]; prev = df.iloc[-2]; prev2 = df.iloc[-3]
    
    day_open = df.iloc[0]["Open"]
    day_low  = df["Low"].min()
    drop = (day_open - day_low) / day_open * 100

    c1 = drop > 1.5
    c2 = curr["Close"] >= day_open
    c3 = prev["Close"] < day_open
    c4 = curr["RSI"] > prev["RSI"] > prev2["RSI"]
    c5 = curr["RSI"] < 70
    c6 = True 
    
    score = sum([c1,c2,c3,c4,c5,c6])
    if score >= 5 and c1 and c2:
        send_tg(f"⚡ *[WASHOUT 殺低反轉]* `{sym}` · {label}\n💰 現價: `{curr['Close']:.2f}`\n燈號: {L(c1)}殺低 {L(c2)}站回 {L(c3)}剛翻 {L(c4)}RSI勾 {L(c5)}安全\n⏰ {tw_time()}")

def strategy_orb(sym, df_5m, df_15m, label):
    if len(df_5m) < 15: return
    df5 = df_5m.copy()
    df5["V_MA10"] = df5["Volume"].rolling(window=10).mean()
    hi15 = df5.iloc[0:3]["High"].max()
    curr = df5.iloc[-1]; prev = df5.iloc[-2]
    vr = curr["Volume"] / (curr["V_MA10"] + 1)
    
    if curr["Close"] > hi15 and prev["Close"] <= hi15 and vr >= 2.0:
        send_tg(f"🚀 *[ORB 多頭突破]* `{sym}` · {label}\n💰 現價: `{curr['Close']:.2f}` · 量比: `{vr:.1f}x`\n⏰ {tw_time()}")

def strategy_pullback(sym, df_1d, df_5m, label):
    if len(df_1d) < 65: return
    d = df_1d.copy()
    d["MA60"] = d["Close"].rolling(window=60).mean()
    d["RSI"] = ta.momentum.RSIIndicator(d["Close"], window=14).rsi()
    d_c = d.iloc[-1]; d_p = d.iloc[-2]
    bias = (d_c["Close"] - d_c["MA60"]) / d_c["MA60"] * 100
    
    if d_c["Close"] > d_c["MA60"] and 42 <= d_p["RSI"] <= 55 and d_c["RSI"] > d_p["RSI"] and 0 <= bias < 4:
        send_tg(f"📈 *[PULLBACK 縮量]* `{sym}` · {label} ★\n💰 現價: `{d_c['Close']:.2f}` · 距季線: `{bias:.1f}%`\n⏰ {tw_time()}")

# -- 主程式 --------------------------------------------------------------------
def main():
    print(f"CC Scanner 啟動 - {tw_time()}")
    
    if is_us_open():
        for sym in US_TICKERS:
            try:
                df5 = _clean(yf.download(sym, interval="5m", period="2d", progress=False))
                strategy_washout(sym, df5, "US")
                df15 = _clean(yf.download(sym, interval="15m", period="5d", progress=False))
                strategy_orb(sym, df5, df15, "US")
            except: continue
            
    if is_us_swing():
        for sym in US_TICKERS:
            try:
                df1d = _clean(yf.download(sym, period="100d", progress=False))
                df5 = _clean(yf.download(sym, interval="5m", period="2d", progress=False))
                strategy_pullback(sym, df1d, df5, "US")
            except: continue

    print("掃描結束")

if __name__ == "__main__":
    main()
