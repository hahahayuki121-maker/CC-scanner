"""
CC Market Scanner v7.2
修正：分母保護、30分鐘重複過濾、開盤黃金半小時訊號擊發
"""

import requests
import pandas as pd
import ta
import yfinance as yf
import os
from datetime import datetime
import pytz

# ── Token ─────────────────────────────────────────────────────────────────────
TG_TOKEN      = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID", "")
ALPACA_KEY    = os.environ.get("ALPACA_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")
ALPACA_BASE   = "https://data.alpaca.markets/v2"

# ── 監控名單 (保留你的原始識別標籤) ───────────────────────────────────────────
TICKERS = {
    "🇺🇸": ["NVDA","AVGO","ANET","VRT","VST","TSLA","AMD","AMZN","AAPL","META","MSFT","GOOGL","PLTR","CRDO","ALAB","NVDL","AMDL"],
    "🛡️": ["PANW","FTNT","CRWD"],
    "⚛️": ["SMR","OKLO","NNE"],
    "🚀": ["COIN","MSTR","MARA","CLSK","HOOD","SOFI","APLD","IONQ","RGTI","NVTS","AAOI","RCAT","ONDS","TQQQ"],
    "🇨🇳": ["BABA","PDD","FUTU"],
    "🇹🇼": ["2330.TW","00631L.TW"],
    "₿": [("BTC-USD","BTC/USDT"),("ETH-BTC","ETH/BTC")],
}

# 全域冷卻緩存
SENT_CACHE = {}

# ── 時間判斷 ──────────────────────────────────────────────────────────────────
def _now_ny(): return datetime.now(pytz.timezone("America/New_York"))
def _now_tw(): return datetime.now(pytz.timezone("Asia/Taipei"))

def us_market_status():
    ny = _now_ny()
    m = ny.hour * 60 + ny.minute
    if ny.weekday() >= 5: return "CLOSED"
    if 240 <= m < 570: return "PRE"
    if 570 <= m < 960: return "OPEN"
    return "CLOSED"

def get_send_mode():
    tw = _now_tw()
    m = tw.hour * 60 + tw.minute
    us = us_market_status()
    # 20:30-21:30 彙整
    if 1230 <= m < 1290: return "DIGEST_PRE"
    # 21:30-22:00 黃金半小時 (美股開盤衝刺)
    if us == "OPEN" and 1290 <= m < 1320: return "GOLDEN_30"
    # 開盤中其餘時間
    if us == "OPEN": return "URGENT_ONLY"
    return "SILENT"

# ── 數據獲取 (Alpaca IEX) ─────────────────────────────────────────────────────
def get_alpaca_5m(symbol):
    try:
        url = f"{ALPACA_BASE}/stocks/{symbol}/bars"
        headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
        params = {"timeframe": "5Min", "limit": 50, "adjustment": "raw", "feed": "iex"}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        bars = r.json().get("bars", [])
        if not bars: return pd.DataFrame()
        df = pd.DataFrame(bars).rename(columns={"o":"Open","h":"High","l":"Low","c":"Close","v":"Volume"})
        df["t"] = pd.to_datetime(df["t"])
        return df.set_index("t")
    except: return pd.DataFrame()

# ── 核心策略 (保留原始邏輯與 L(v) 等級判斷) ──────────────────────────────────
def scan_logic(sym, tag, df5, mode):
    if len(df5) < 20: return None
    df = df5.copy()
    df["VMA"] = ta.trend.SMAIndicator(df["Volume"], window=15).sma_indicator()
    curr = df.iloc[-1]
    
    # 🛡️ 分母保護：若 VMA 太小視為無量
    vma_val = curr["VMA"] if not pd.isna(curr["VMA"]) else 0
    if vma_val < 50: return None 
    vr = curr["Volume"] / (vma_val + 1)

    # ⛈️ 暴跌預兆 (全時段監控)
    support_5 = df["Low"].tail(6).iloc[:-1].min()
    if curr["Close"] < support_5 and vr > 2.5:
        return {"type": "⛈️", "msg": f"{tag} ⛈️ *[暴跌預兆]* `{sym}`\n💰 現價: `{curr['Close']:.2f}`\n🚨 支撐潰散，量比 `{vr:.1f}x`"}

    # 🔮 暴漲/突破 (彙整與黃金半小時才擊發)
    if mode in ["DIGEST_PRE", "GOLDEN_30"]:
        prev_hi = df["High"].tail(7).iloc[:-1].max()
        if vr > 3.0 and curr["Close"] > prev_hi:
            return {"type": "🔮", "msg": f"{tag} 🔮 *[暴漲預兆]* `{sym}`\n💰 現價: `{curr['Close']:.2f}`\n🚀 突破前高，量比 `{vr:.1f}x`"}

    return None

# ── 主程式 ────────────────────────────────────────────────────────────────────
def main():
    mode = get_send_mode()
    if mode == "SILENT": return
    
    intraday_sigs = []
    for tag, syms in TICKERS.items():
        if tag in ["🇹🇼", "₿"]: continue
        for sym in syms:
            df = get_alpaca_5m(sym)
            if df.empty: continue
            
            res = scan_logic(sym, tag, df, mode)
            if res:
                # 30分鐘冷卻檢查
                cache_key = f"{sym}_{res['type']}"
                now = datetime.now()
                if cache_key in SENT_CACHE and (now - SENT_CACHE[cache_key]).total_seconds() < 1800:
                    continue
                SENT_CACHE[cache_key] = now
                
                if mode == "DIGEST_PRE":
                    intraday_sigs.append(res)
                else: # GOLDEN_30 或 URGENT_ONLY 直接發送
                    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", 
                                 data={"chat_id": TG_CHAT_ID, "text": res["msg"], "parse_mode": "Markdown"})

    if mode == "DIGEST_PRE" and intraday_sigs:
        report = "📋 *CC Scanner 開盤前彙整*\n" + "\n".join([s["msg"].split("\n")[0] for s in intraday_sigs[:10]])
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data={"chat_id": TG_CHAT_ID, "text": report, "parse_mode": "Markdown"})

if __name__ == "__main__":
    main()
