"""
CC Market Scanner v7.0 終極醫規版
新增：⛈️ [暴跌預兆]：偵測高位放量滯漲、支撐潰散，防範跳空悶殺
整合：🔮 [暴漲預兆]、🔥 [強勢突破]、⚡ [WASHOUT]
優化：針對妖股與權值股自動切換防禦門檻
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
    "🚀 妖股": ["COIN", "MSTR", "MARA", "CLSK", "HOOD", "SOFI", "APLD", "IONQ", "RGTI", "NVTS", "PLTR", "ONDS", "PATH", "AAOI", "PL", "RCAT", "AXTI", "TQQQ" "LUNR"],
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

def get_market_status():
    ny = datetime.now(pytz.timezone("America/New_York"))
    m = ny.hour * 60 + ny.minute
    if ny.weekday() >= 5: return "CLOSED"
    if 240 <= m < 570: return "PRE"
    if 570 <= m < 960: return "REGULAR"
    return "CLOSED"

def get_data(s, interval, period):
    try:
        df = yf.download(s, interval=interval, period=period, progress=False, auto_adjust=True, prepost=True)
        if df.empty: return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        last_ts = df.index[-1].astimezone(pytz.timezone("America/New_York"))
        now_ny = datetime.now(pytz.timezone("America/New_York"))
        if (now_ny - last_ts).total_seconds() > 900: return pd.DataFrame()
        return df.dropna()
    except: return pd.DataFrame()

def rsi(df): return ta.momentum.RSIIndicator(df["Close"]).rsi()
def sma(df, col, n): return ta.trend.SMAIndicator(df[col], window=n).sma_indicator()

# ══════════════════════════════════════════════════════════════════════════════
# ⚡ 診斷邏輯 (全功能版)
# ══════════════════════════════════════════════════════════════════════════════

def scan_logic(sym, tag, df5):
    df = df5.copy()
    if len(df) < 25: return None
    status = get_market_status()
    
    # 1. 基礎指標計算
    df["RSI"] = rsi(df); df["MA5"] = sma(df, "Close", 5); df["MA20"] = sma(df, "Close", 20); df["VMA"] = sma(df, "Volume", 15)
    curr = df.iloc[-1]; prev = df.iloc[-2]
    day_open = df.iloc[0]["Open"]; day_low = df["Low"].min()
    vr = curr["Volume"] / (df["VMA"].iloc[-1] + 1e-6)
    
    # 🛡️ 動態過濾：盤前要 2.5x 量，妖股要 1.2x，權值要 0.6x
    min_vr = 2.5 if status == "PRE" else (1.2 if tag == "🚀 妖股" else 0.6)
    if vr < min_vr: return None

    # --- [策略 E: 暴跌預警 (防悶殺)] --- 針對 AXTI 事件
    # 邏輯：高位爆量滯漲 或 跌破短期支撐
    recent_hi = df["High"].tail(20).max()
    support_min = df["Low"].tail(5).min()
    if curr["Close"] > recent_hi * 0.96: # 處於高位
        if (vr > 3.5 and curr["Close"] < curr["Open"]) or (curr["Close"] < support_min):
            return (f"{tag} ⛈️ *[暴跌預兆]* `{sym}`\n"
                    f"💰 現價: `{curr['Close']:.2f}` · 警告: 支撐潰散\n"
                    f"📊 動能: ⚠️ 異常放量滯漲 (量比 {vr:.1f}x)\n"
                    f"🎫 操作: **立刻減倉** 或 平倉觀望，切勿留過夜！\n⏰ {tw_time()}")

    # --- [策略 D: 暴漲預兆 (起漲偵測)] ---
    range_hi = df["High"].tail(6).max()
    if vr > 3.0 and curr["Close"] > range_hi and (curr["Close"]-day_open)/day_open < 0.10:
        return (f"{tag} 🔮 *[暴漲預兆]* `{sym}`\n"
                f"💰 現價: `{curr['Close']:.2f}` · 蓄勢待發\n"
                f"📊 動能: 🔥 買盤偷跑 (量比 {vr:.1f}x)\n"
                f"🎫 操作: **現股建立底倉**，博當天噴發\n⏰ {tw_time()}")

    # --- [策略 A: 強勢突破] ---
    orb_hi = df.iloc[0:3]["High"].max()
    if curr["Close"] > orb_hi and prev["Close"] <= orb_hi and vr > 1.2:
        prefix = "🌅 [盤前強勢]" if status == "PRE" else "🔥 [強勢突破]"
        return (f"{tag} {prefix} `{sym}` 🚀\n"
                f"💰 現價: `{curr['Close']:.2f}` · 破開盤高\n"
                f"📊 動能: {vr:.1f}x (攻擊量)\n"
                f"🎫 操作: **Buy Call** 或 現股追入\n⏰ {tw_time()}")

    # --- [策略 B: WASHOUT 殺低反彈] ---
    drop = (day_open - day_low) / day_open * 100
    c1 = drop > 0.8; c2 = curr["Close"] >= day_open * 0.998; c3 = curr["MA5"] > curr["MA20"]
    if (sum([c1, c2, c3]) >= 2) and c1 and c2:
        prefix = "🌅 [盤前洗盤]" if status == "PRE" else "⚡ [WASHOUT]"
        return (f"{tag} {prefix} `{sym}` {grade(sum([c1,c2,c3]), 3)}\n"
                f"💰 現價: `{curr['Close']:.2f}` · 跌幅: `{drop:.1f}%`回升\n"
                f"📊 動能: {vr:.1f}x (低位護盤)\n⏰ {tw_time()}")

    return None

# ── 主程式邏輯 (維持不變) ──
def main():
    status = get_market_status()
    if status == "CLOSED": return
    for tag, syms in TICKERS.items():
        if tag in ["🇹🇼 台股", "₿ 加密"]: continue
        for sym in syms:
            df5 = get_data(sym, "5m", "2d")
            if df5.empty: continue 
            msg = scan_logic(sym, tag, df5)
            if msg: send_tg(msg)

if __name__ == "__main__": main()
