"""
CC Market Scanner v7.5 - FINAL VERSION
=============================================================
1. 數據源鎖定：優先 Polygon.io (SIP全市場量能)，Fallback 至 yfinance。
2. 晨間巡房：台灣時間 21:15 自動發送全體標的盤前摘要。
3. 邏輯封存：完全保留 v7.1 之暴漲/暴跌/WASHOUT/PULLBACK 演算法。
=============================================================
"""

import requests
import pandas as pd
import ta
import yfinance as yf
import os
import time
from datetime import datetime
import pytz

# ── 密鑰與環境配置 ──────────────────────────────────────────────────────────
TG_TOKEN      = os.environ.get("TG_TOKEN",      "")
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID",    "")
POLYGON_KEY   = os.environ.get("POLYGON_KEY",   "")
ALPACA_KEY    = os.environ.get("ALPACA_KEY",    "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")

# 避開整點請求高峰
time.sleep(30)

TICKERS = {
    "🇺🇸": ["NVDA","AVGO","ANET","VRT","VST","TSLA","AMD","AMZN","AAPL","META","MSFT","GOOGL","PLTR","CRDO","ALAB","QQQ"],
    "🛡️": ["PANW","FTNT","CRWD"],
    "⚛️": ["SMR","OKLO","NNE"],
    "🚀": ["COIN","MSTR","MARA","CLSK","HOOD","SOFI","APLD","IONQ","RGTI","NVTS","AAOI","RCAT","ONDS"],
    "🇨🇳": ["BABA","PDD","FUTU"],
    "🇹🇼": ["2330.TW","00631L.TW"],
    "₿":   [("BTC-USD","BTC/USDT"),("ETH-BTC","ETH/BTC")],
}

# ── 數據獲取模組 (Polygon 優先，獲取 100% 真實成交量) ─────────────────────────
def get_polygon_bars(symbol, multiplier=5, timespan="minute", limit=100):
    if not POLYGON_KEY: return pd.DataFrame()
    try:
        # 使用 2026 年當前日期範圍
        url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/2026-01-01/2026-12-31"
        params = {"adjusted": "true", "sort": "desc", "limit": limit, "apiKey": POLYGON_KEY}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200: return pd.DataFrame()
        results = r.json().get("results", [])
        if not results: return pd.DataFrame()
        
        df = pd.DataFrame(results)
        df['t'] = pd.to_datetime(df['t'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('America/New_York')
        df = df.set_index('t').sort_index()
        df = df.rename(columns={"o":"Open","h":"High","l":"Low","c":"Close","v":"Volume"})
        return df[["Open","High","Low","Close","Volume"]].dropna()
    except: return pd.DataFrame()

def get_live_data(sym):
    df5 = get_polygon_bars(sym, 5, "minute", 80)
    if not df5.empty:
        df15 = get_polygon_bars(sym, 15, "minute", 40)
        return df5, df15, "polygon"
    # Fallback to yfinance
    df5 = _clean(yf.download(sym, interval="5m", period="2d", progress=False, auto_adjust=True))
    df15 = _clean(yf.download(sym, interval="15m", period="5d", progress=False, auto_adjust=True))
    return df5, df15, "yfinance"

def _clean(df):
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    return df.dropna()

# ── 策略邏輯區 (完全保留 v7.1 演算法) ──────────────────────────────────────────
def add_rsi(df, n=14):
    df = df.copy(); df["RSI"] = ta.momentum.RSIIndicator(df["Close"], window=n).rsi()
    return df

def add_sma(df, col, n, out):
    df = df.copy(); df[out] = ta.trend.SMAIndicator(df[col], window=n).sma_indicator()
    return df

def add_macd(df):
    df = df.copy(); m = ta.trend.MACD(df["Close"])
    df["MACD"], df["MACD_sig"], df["MACD_hist"] = m.macd(), m.macd_signal(), m.macd_diff()
    return df

def grade(score, total):
    pct = score / total
    if pct >= 0.85: return "🏆 S級"
    if pct >= 0.70: return "🥇 A級"
    return None

def L(v): return "✅" if v else "❌"
def tw_time(): return datetime.now(pytz.timezone("Asia/Taipei")).strftime("%H:%M:%S")

# ⛈️ 暴跌預兆
def signal_crash_warning(sym, tag, df5):
    if len(df5) < 20: return None
    df = add_rsi(add_sma(df5, "Volume", 15, "VMA"))
    curr = df.iloc[-1]; vr = curr["Volume"] / (curr["VMA"] + 1)
    recent_hi = df["High"].tail(20).max()
    is_high_pos = curr["Close"] > recent_hi * 0.94
    c1 = is_high_pos and vr > 3.5 and curr["Close"] < curr["Open"]
    c2 = curr["Close"] < df["Low"].tail(6).iloc[:-1].min()
    score = sum([c1, c2])
    if score == 0: return None
    g = grade(score, 2)
    if not g: return None
    return {"score": score + 10, "msg": (f"{tag} ⛈️ *[暴跌預兆]* `{sym}` {g}\n💰 現價: `{curr['Close']:.2f}`\n"
                                         f"燈號: {L(c1)}高位爆量收黑 {L(c2)}跌破5日支撐\n📊 量比: `{vr:.1f}x` · RSI: `{curr['RSI']:.0f}`\n"
                                         f"🚨 建議立刻減倉，切勿留過夜\n⏰ {tw_time()} TWN")}

# 🔮 暴漲預兆
def signal_breakout_pre(sym, tag, df5, df15):
    if len(df5) < 20 or len(df15) < 5: return None
    df = add_rsi(add_sma(df5, "Volume", 15, "VMA"))
    curr = df.iloc[-1]; prev = df.iloc[-2]; vr = curr["Volume"] / (curr["VMA"] + 1)
    prev_hi = df["High"].tail(7).iloc[:-1].max()
    c15 = df15.iloc[-1]
    c1, c2, c3, c4, c5 = vr > 2.5, curr["Close"] > prev_hi, prev["Close"] <= prev_hi, (55 < curr["RSI"] < 78), c15["Close"] > prev_hi
    score = sum([c1,c2,c3,c4,c5])
    g = grade(score, 5)
    if not g or not (c2 and c3): return None
    return {"score": score, "msg": (f"{tag} 🔮 *[暴漲預兆]* `{sym}` {g}\n💰 現價: `{curr['Close']:.2f}` · 突破: `{prev_hi:.2f}`\n"
                                    f"燈號: {L(c1)}量2.5x {L(c2)}突破前高 {L(c3)}剛發動 {L(c4)}RSI動能 {L(c5)}15m確認\n"
                                    f"📊 量比: `{vr:.1f}x` · RSI: `{curr['RSI']:.0f}`\n⏰ {tw_time()} TWN")}

# ⚡ WASHOUT
def signal_washout(sym, tag, df5, df15, status):
    if len(df5) < 6 or len(df15) < 3: return None
    df = add_rsi(add_sma(add_sma(df5, "Volume", 10, "VMA10"), "Close", 5, "MA5"))
    df = add_sma(df, "Close", 20, "MA20")
    curr, prev, prev2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    ny_today = datetime.now(pytz.timezone("America/New_York")).date()
    today_bars = df[df.index.date == ny_today]
    if today_bars.empty: return None
    day_open, day_low = today_bars.iloc[0]["Open"], df["Low"].min()
    yest = df[df.index.date < ny_today]; yest_low = yest["Low"].min() if not yest.empty else day_low * 0.97
    drop, rebound, vr = (day_open - day_low) / day_open * 100, (curr["Close"] - day_low) / (day_open - day_low + 0.001), curr["Volume"] / (df["VMA10"].iloc[-1] + 1)
    min_drop = 1.5 if "🚀" not in tag else 2.0
    c1, c2, c3, c4, c5, c6, c7, c8 = drop > min_drop, curr["Close"] >= day_open * 0.998, prev["Close"] < day_open, (curr["RSI"] > prev["RSI"] > prev2["RSI"]), curr["RSI"] < 72, curr["Close"] > yest_low, rebound > 0.5, curr["MA5"] > curr["MA20"]
    score = sum([c1,c2,c3,c4,c5,c6,c7,c8]); g = grade(score, 8)
    if not g or not c1 or not c2: return None
    prefix = "🌅 [盤前洗盤]" if status == "PRE" else "⚡ [WASHOUT]"
    return {"score": score, "msg": (f"{tag} {prefix} `{sym}` {g}\n💰 現價: `{curr['Close']:.2f}` · 殺低: `{drop:.1f}%`\n"
                                    f"燈號: {L(c1)}殺低 {L(c2)}站回 {L(c3)}剛翻 {L(c4)}RSI勾 {L(c5)}非追高 {L(c6)}守昨低 {L(c7)}彈力 {L(c8)}MA翻多\n"
                                    f"📊 量比: `{vr:.1f}x` · 條件: `{score}/8`\n⏰ {tw_time()} TWN")}

# 📈 波段 PULLBACK
def signal_pullback(sym, tag, df1d, df5):
    if len(df1d) < 65 or len(df5) < 3: return None
    d = add_macd(add_rsi(add_sma(add_sma(df1d, "Close", 60, "MA60"), "Volume", 5, "V5")))
    f_c, f_p = add_rsi(df5).iloc[-1], add_rsi(df5).iloc[-2]
    d_c, d_p = d.iloc[-1], d.iloc[-2]
    bias60, vr = (d_c["Close"] - d_c["MA60"]) / d_c["MA60"] * 100, d_c["Volume"] / (d_c["V5"] + 1)
    c1, c2, c3, c4, c5, c6 = d_c["Close"] > d_c["MA60"], (42 <= d_p["RSI"] <= 58 and d_c["RSI"] > d_p["RSI"]), vr < 0.85, 0 <= bias60 < 5, f_c["RSI"] > f_p["RSI"], d_c["MACD_hist"] > d_p["MACD_hist"]
    score = sum([c1,c2,c3,c4,c5,c6]); g = grade(score, 6)
    if not g or not c1 or not c2: return None
    return {"score": score, "msg": (f"{tag} 📈 *[波段PULLBACK]* `{sym}` {g}\n💰 現價: `{d_c['Close']:.2f}` · 距季線: `{bias60:.1f}%`\n"
                                    f"📊 日RSI: `{d_c['RSI']:.0f}` · 條件: `{score}/6`\n⏰ {tw_time()} TWN")}

# ── 晨間巡房摘要 ──────────────────────────────────────────────────────────────
def send_premarket_summary():
    report = "🌅 [CC Scanner 晨間巡房摘要]\n━━━━━━━━━━━━━\n"
    for tag, syms in TICKERS.items():
        if tag in ["🇹🇼", "₿"]: continue
        report += f"\n【{tag} 板塊】\n"
        for sym in syms:
            df, _, _ = get_live_data(sym)
            if df.empty: continue
            ny_today = datetime.now(pytz.timezone("America/New_York")).date()
            today_bars = df[df.index.date == ny_today]
            if today_bars.empty: continue
            day_open, curr_price = today_bars.iloc[0]["Open"], df.iloc[-1]["Close"]
            chg = (curr_price - day_open) / day_open * 100
            icon = "🔥" if chg > 1.5 else ("❄️" if chg < -1.5 else "⚪")
            report += f"{icon} `{sym}`: {chg:+.1f}%\n"
    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data={"chat_id": TG_CHAT_ID, "text": report, "parse_mode": "Markdown"})

# ── 主程式 ────────────────────────────────────────────────────────────────────
def main():
    ny_now = datetime.now(pytz.timezone("America/New_York"))
    m = ny_now.hour * 60 + ny_now.minute
    if 555 <= m < 560: send_premarket_summary(); return
    
    ny = datetime.now(pytz.timezone("America/New_York"))
    d = ny.strftime("%Y-%m-%d")
    status = "CLOSED"
    if not (ny.weekday() >= 5 or d in ["2026-04-03"]): # 簡易假日判斷
        if 240 <= m < 570: status = "PRE"
        elif 570 <= m < 930: status = "OPEN"
    
    if status == "CLOSED": return
    
    intraday_sigs = []
    for tag, syms in TICKERS.items():
        if tag in ["🇹🇼", "₿"]: continue
        for sym in syms:
            try:
                df5, df15, _ = get_live_data(sym)
                if df5.empty: continue
                for fn in [lambda s,t,d5,d15: signal_crash_warning(s, t, d5), signal_breakout_pre, lambda s,t,d5,d15: signal_washout(s, t, d5, d15, status)]:
                    r = fn(sym, tag, df5, df15)
                    if r: intraday_sigs.append(r)
            except: continue
    
    intraday_sigs.sort(key=lambda x: x["score"], reverse=True)
    for s in intraday_sigs: requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data={"chat_id": TG_CHAT_ID, "text": s["msg"], "parse_mode": "Markdown"})

if __name__ == "__main__": main()
