"""
CC Market Scanner v6.2
標籤：🇺🇸龍頭 | 🚀妖股 | 🇹🇼台股 | ₿加密
分級：S級(🏆) > A級(🥇) > B級(🥈)
優化：加入流動性門檻(量比) 與 期權(Options) 操作建議
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

# 監控名單
TICKERS = {
    "🇺🇸": ["NVDA", "AVGO", "ANET", "VRT", "VST", "TSLA", "AMD", "AMZN", "AAPL", "META", "MSFT", "GOOGL"],
    "🚀": ["COIN", "MSTR", "MARA", "CLSK", "HOOD", "SOFI", "CRCL", "APLD", "IONQ", "RGTI", "AAOI", "CRDO", "ALAB", "PLTR", "ONDS", "AXTI", "PATH"],
    "🇹🇼": ["2330.TW", "00631L.TW"],
    "₿ ": ["BTC-USD", "ETH-BTC"],
}

# ── 工具函式 ──────────────────────────────────────────────────────────────────
def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: return False
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                     data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        return True
    except: return False

def tw_time(): return datetime.now(pytz.timezone("Asia/Taipei")).strftime("%H:%M:%S")
def L(v): return "✅" if v else "❌"

def grade(score, total):
    pct = score / total
    if pct >= 0.85: return "🏆 S級"
    if pct >= 0.70: return "🥇 A級"
    return "🥈 B級"

# 時間判斷
def us_mins():
    ny = datetime.now(pytz.timezone("America/New_York"))
    return -1 if ny.weekday() >= 5 else ny.hour * 60 + ny.minute

def tw_mins():
    tw = datetime.now(pytz.timezone("Asia/Taipei"))
    return -1 if tw.weekday() >= 5 else tw.hour * 60 + tw.minute

def is_us_open():  return 570 <= us_mins() < 930
def is_us_swing(): return 900 <= us_mins() < 930
def is_tw_open():  return 540 <= tw_mins() < 810
def is_tw_swing(): return 780 <= tw_mins() < 810

# ── 數據與指標 ────────────────────────────────────────────────────────────────
def _clean(df):
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    return df.dropna()

def get_data(s, interval, period):
    try:
        return _clean(yf.download(s, interval=interval, period=period, progress=False, auto_adjust=True))
    except: return pd.DataFrame()

def rsi(df): return ta.momentum.RSIIndicator(df["Close"]).rsi()
def sma(df, col, n): return ta.trend.SMAIndicator(df[col], window=n).sma_indicator()
def macd_diff(df): return ta.trend.MACD(df["Close"]).macd_diff()

# ══════════════════════════════════════════════════════════════════════════════
# ⚡ 日內：WASHOUT 殺低反彈
# ══════════════════════════════════════════════════════════════════════════════
def intraday_washout(sym, tag, df5):
    if len(df5) < 20: return None
    df = df5.copy()
    df["RSI"] = rsi(df); df["MA5"] = sma(df, "Close", 5); df["MA20"] = sma(df, "Close", 20); df["VMA"] = sma(df, "Volume", 10)
    
    curr = df.iloc[-1]; prev = df.iloc[-2]
    day_open = df.iloc[0]["Open"]; day_low = df["Low"].min()
    drop = (day_open - day_low) / day_open * 100
    vr = curr["Volume"] / (curr["VMA"] + 1e-6)
    
    c1 = drop > 1.3                   # 殺低洗盤
    c2 = curr["Close"] >= day_open    # 站回開盤
    c3 = curr["MA5"] > curr["MA20"]   # 5/20 交叉
    c4 = vr > 1.1                     # 帶量突破 (流動性關鍵!)
    c5 = 40 < curr["RSI"] < 68        # 動能起步
    
    score = sum([c1, c2, c3, c4, c5])
    if score < 4 or not c1 or not c2 or not c4: return None
    
    g = grade(score, 5); sp_ref = curr["Close"] * 0.95
    return { "score": score, "msg": (
        f"{tag} ⚡ *[日內 WASHOUT]* `{sym}` {g}\n"
        f"💰 現價: `{curr['Close']:.2f}` · 殺低: `{drop:.1f}%` · 量比: `{vr:.1f}x`\n"
        f"燈號: {L(c1)}殺低 {L(c2)}站回 {L(c3)}5/20交叉 {L(c4)}帶量\n"
        f"📊 RSI: `{curr['RSI']:.0f}` · 條件: `{score}/5`\n"
        f"🎫 期權: 可考慮 *Sell Put* 參考價 `{sp_ref:.1f}`\n⏰ {tw_time()}"
    )}

# ══════════════════════════════════════════════════════════════════════════════
# ⚠️ 日內：超買警戒 (Sell Call)
# ══════════════════════════════════════════════════════════════════════════════
def intraday_overbought(sym, tag, df5):
    df = df5.copy()
    df["RSI"] = rsi(df); df["MA20"] = sma(df, "Close", 20); df["MA5"] = sma(df, "Close", 5)
    curr = df.iloc[-1]; prev = df.iloc[-2]
    bias = (curr["Close"] - curr["MA20"]) / curr["MA20"] * 100
    
    c1 = curr["RSI"] > 78; c2 = bias > 4.5; c3 = curr["Close"] < curr["MA5"]; c4 = curr["RSI"] < prev["RSI"]
    
    if (c1 and c3) or (c1 and c2 and c4):
        return { "score": 8, "msg": (
            f"{tag} ⚠️ *[超買警戒 撤退/Sell Call]* `{sym}`\n"
            f"💰 現價: `{curr['Close']:.2f}` · 乖離: `{bias:.1f}%` · RSI: `{curr['RSI']:.0f}`\n"
            f"💡 建議先收割獲利，或佈局 *Sell Call* 收權利金\n⏰ {tw_time()}"
        )}
    return None

# ══════════════════════════════════════════════════════════════════════════════
# 📈 波段：PULLBACK 縮量回測
# ══════════════════════════════════════════════════════════════════════════════
def swing_pullback(sym, tag, df1d):
    if len(df1d) < 65: return None
    d = df1d.copy()
    d["RSI"] = rsi(d); d["MA20"] = sma(d, "Close", 20); d["MA60"] = sma(d, "Close", 60); d["VMA"] = sma(d, "Volume", 5); d["Hist"] = macd_diff(d)
    
    c = d.iloc[-1]; p = d.iloc[-2]; vr = c["Volume"] / (c["VMA"] + 1e-6); bias60 = (c["Close"] - c["MA60"]) / c["MA60"] * 100
    
    c1 = c["Close"] > c["MA60"]       # 季線支撐
    c2 = p["RSI"] < 58 and c["RSI"] > p["RSI"] # RSI低位勾頭
    c3 = vr < 0.9                     # 縮量 (洗盤完成)
    c4 = c["Hist"] > p["Hist"]        # 動能轉正
    
    score = sum([c1, c2, c3, c4])
    if score < 3 or not c1 or not c2: return None
    
    g = grade(score, 4)
    return { "score": score, "msg": (
        f"{tag} 📈 *[波段 PULLBACK]* `{sym}` {g}\n"
        f"💰 現價: `{c['Close']:.2f}` · 距季線: `{bias60:.1f}%` · 量比: `{vr:.1f}x`\n"
        f"📊 RSI: `{c['RSI']:.0f}` · 💡 適合持有或 *Sell Put* 佈局\n⏰ {tw_time()}"
    )}

# ── 主程式 ────────────────────────────────────────────────────────────────────
def main():
    signals = []
    
    # 美股與台股掃描
    for tag, syms in TICKERS.items():
        if tag == "₿ ": continue
        for sym in syms:
            # 判斷開盤與否以節省流量
            if (tag == "🇹🇼" and is_tw_open()) or (tag in ["🇺🇸", "🚀"] and is_us_open()):
                df5 = get_data(sym, "5m", "2d")
                if df5.empty: continue
                r_w = intraday_washout(sym, tag, df5)
                if r_w: signals.append(r_w)
                r_o = intraday_overbought(sym, tag, df5)
                if r_o: signals.append(r_o)
                
            if (tag == "🇹🇼" and is_tw_swing()) or (tag in ["🇺🇸", "🚀"] and is_us_swing()):
                df1d = get_data(sym, "1d", "100d")
                if df1d.empty: continue
                r_p = swing_pullback(sym, tag, df1d)
                if r_p: signals.append(r_p)

    # 加密貨幣 (24/7)
    for sym in TICKERS["₿ "]:
        df1h = get_data(sym, "1h", "10d")
        if df1h.empty: continue
        # 加密採用簡化波段邏輯
        r_p = swing_pullback(sym, "₿ ", df1h)
        if r_p: signals.append(r_p)

    # 排序並發送
    signals.sort(key=lambda x: x["score"], reverse=True)
    for s in signals: send_tg(s["msg"])

if __name__ == "__main__": main()
