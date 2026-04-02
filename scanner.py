"""
CC Market Scanner - v5.8 日內/波段辨識版
標籤：🇹🇼(台股) | 🇺🇸(龍頭) | 🚀(妖股) | ₿(加密)
策略：日內(當沖轉折) | 波段(趨勢起漲)
"""
import yfinance as yf
import pandas_ta as ta
import requests
import pandas as pd
import os
from datetime import datetime
import pytz

TG_TOKEN   = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

TICKERS = {
    "🇺🇸": ["NVDA", "TSLA", "AMD", "AMZN", "AAPL", "META", "MSFT", "GOOGL", "VRT", "ANET", "VST", "AVGO", "PLTR"],
    "🚀": ["COIN", "MSTR", "MARA", "CLSK", "SOFI", "HOOD", "CRDO", "CRCL", "AAOI", "ASX", "PL", "BKSY", "ONDS", "RCAT", "APLD", "AEHR", "AXTI", "IONQ", "RGTI", "EH", "WOLF", "PATH"],
    "🇹🇼": ["2330.TW", "00631L.TW"],
    "₿ ": ["BTC-USD", "ETH-BTC"]
}

def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                     data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def tw_time(): return datetime.now(pytz.timezone("Asia/Taipei")).strftime("%H:%M:%S")

def analyze_logic(tag, sym, df_5m, df_1d):
    # 5分K 數據 (日內)
    df_5m["MA5"] = ta.sma(df_5m["Close"], length=5)
    df_5m["MA20"] = ta.sma(df_5m["Close"], length=20)
    curr_5m = df_5m.iloc[-1]
    day_open = df_5m.iloc[0]["Open"]
    
    # 日線數據 (波段)
    df_1d["D_MA20"] = ta.sma(df_1d["Close"], length=20)
    curr_1d = df_1d.iloc[-1]
    
    # --- 判斷型態 ---
    # 波段定義：現價站在日線 20MA 之上，且日線趨勢向上
    is_swing = curr_5m["Close"] > curr_1d["D_MA20"]
    mode_tag = "📈 [波段持有]" if is_swing else "⚡ [日內短打]"
    
    # --- 策略 A：WASHOUT (多) ---
    day_low = df_5m["Low"].min()
    drop = (day_open - day_low) / day_open * 100
    
    if drop > 1.2 and curr_5m["Close"] >= day_open and curr_5m["MA5"] > curr_5m["MA20"]:
        msg = f"{tag} {mode_tag} *{sym}*\n💰 現價: `{curr_5m['Close']:.2f}`\n💡 說明: 殺低後站回，"
        msg += "趨勢偏多建議續抱" if is_swing else "上方有壓建議見好就收"
        msg += f"\n⏰ {tw_time()}"
        send_tg(msg)

    # --- 策略 B：OVERBOUGHT (空) ---
    bias = (curr_5m["Close"] - curr_5m["MA20"]) / curr_5m["MA20"] * 100
    if ta.rsi(df_5m["Close"]).iloc[-1] > 75 and bias > 3.5 and curr_5m["Close"] < curr_5m["MA5"]:
        send_tg(f"{tag} ⚠️ [轉折警戒] *{sym}*\n💰 現價: `{curr_5m['Close']:.2f}`\n💡 乖離過大且破5MA，建議先入袋為安\n⏰ {tw_time()}")

def main():
    for tag, sym_list in TICKERS.items():
        for sym in sym_list:
            try:
                d5 = yf.download(sym, interval="5m", period="2d", progress=False)
                d1 = yf.download(sym, interval="1d", period="1mo", progress=False)
                # 清洗 MultiIndex
                for df in [d5, d1]:
                    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
                if not d5.empty and not d1.empty: analyze_logic(tag, sym, d5.dropna(), d1.dropna())
            except: pass

if __name__ == "__main__":
    main()
