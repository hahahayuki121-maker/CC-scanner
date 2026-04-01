"""
CC Market Scanner — 最終版
策略A: WASHOUT  殺低反彈     (5m, 5/6 燈)
策略B: ORB      開盤區間突破  (5m + 15m確認, 4/4 全中)
策略C: PULLBACK 波段縮量回測  (1d + 5m確認, 5/5 全中)
"""

import yfinance as yf
import ta
import requests
import pandas as pd
import os
from datetime import datetime
import pytz

# ── Token（GitHub Actions 從 Secrets 讀取）───────────────────────────────────
TG_TOKEN   = os.environ.get("TG_TOKEN",   "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
# 本地測試時取消以下兩行的 # 並填入：
# TG_TOKEN   = "你的token"
# TG_CHAT_ID = "你的chatid"

# ── 監控名單 ──────────────────────────────────────────────────────────────────
US_TICKERS = [
    "NVDA", "TSLA", "AMD", "AAPL", "META",
    "PLTR", "SOFI", "COIN",
    "F", "BAC", "T", "SNAP", "PATH", "DOCU",
    "XLE", "GLD", "TLT", "AXTI",
]
TW_TICKERS = ["2330.TW", "2454.TW", "6235.TW"]

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        print(f"[TG未設定] {msg[:80]}")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        return r.json().get("ok", False)
    except Exception as e:
        print(f"TG 失敗: {e}")
        return False

def tw_time():
    return datetime.now(pytz.timezone("Asia/Taipei")).strftime("%H:%M:%S")

# ── 時間判斷 ───────────────────────────────────────────────────────────────────
def is_market_open(symbol):
    if ".TW" in symbol:
        tw = datetime.now(pytz.timezone("Asia/Taipei"))
        if tw.weekday() >= 5: return False
        m = tw.hour * 60 + tw.minute
        return 540 <= m < 810
    ny = datetime.now(pytz.timezone("America/New_York"))
    if ny.weekday() >= 5: return False
    m = ny.hour * 60 + ny.minute
    return 570 <= m < 930

def is_swing_scan_time(symbol):
    if ".TW" in symbol:
        tw = datetime.now(pytz.timezone("Asia/Taipei"))
        if tw.weekday() >= 5: return False
        m = tw.hour * 60 + tw.minute
        return 780 <= m < 810
    ny = datetime.now(pytz.timezone("America/New_York"))
    if ny.weekday() >= 5: return False
    m = ny.hour * 60 + ny.minute
    return 900 <= m < 930

# ── 數據獲取 ───────────────────────────────────────────────────────────────────
def _clean(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

def get_5m(sym):
    return _clean(yf.download(sym, interval="5m",  period="2d",   progress=False, auto_adjust=True))

def get_15m(sym):
    return _clean(yf.download(sym, interval="15m", period="5d",   progress=False, auto_adjust=True))

def get_1d(sym):
    return _clean(yf.download(sym, interval="1d",  period="100d", progress=False, auto_adjust=True))

# ── 指標計算（使用 ta 套件）──────────────────────────────────────────────────
def add_rsi(df, col="Close", length=14):
    df = df.copy()
    df["RSI"] = ta.momentum.RSIIndicator(df[col], window=length).rsi()
    return df

def add_sma(df, col, length, out_col):
    df = df.copy()
    df[out_col] = ta.trend.SMAIndicator(df[col], window=length).sma_indicator()
    return df

# ── 策略A：WASHOUT 殺低反彈 ──────────────────────────────────────────────────
def strategy_washout(symbol, df_5m):
    if len(df_5m) < 4: return

    df = add_rsi(df_5m)

    curr  = df.iloc[-1]
    prev  = df.iloc[-2]
    prev2 = df.iloc[-3]

    day_open = df.iloc[0]["Open"]
    day_low  = df["Low"].min()
    yest     = df[df.index.date < df.index[-1].date()]
    yest_low = yest["Low"].min() if not yest.empty else day_low * 0.97

    lights = [
        (day_open - day_low) / day_open > 0.015,      # C1 殺低 > 1.5%
        curr["Close"] >= day_open,                    # C2 站回開盤價
        prev["Close"] < day_open,                     # C3 剛站回
        curr["RSI"] > prev["RSI"] > prev2["RSI"],     # C4 RSI 連兩根上升
        curr["RSI"] < 70,                             # C5 非追高
        curr["Close"] > yest_low,                     # C6 未跌破昨低
    ]
    score = sum(lights)
    if score >= 5 and lights[0] and lights[1]:
        drop = (day_open - day_low) / day_open * 100
        send_tg(
            f"⚡ *[WASHOUT 殺低反彈]*: `{symbol}`\n"
            f"💰 現價: `{curr['Close']:.2f}`\n"
            f"📉 殺低: `{drop:.1f}%` · RSI: `{curr['RSI']:.0f}` 連升\n"
            f"✅ 條件: `{score}/6`\n"
            f"👉 TradingView 5m 確認進場點\n"
            f"⏰ {tw_time()} TWN"
        )

# ── 策略B：ORB 開盤區間突破 ───────────────────────────────────────────────────
def strategy_orb(symbol, df_5m, df_15m):
    if len(df_5m) < 6 or len(df_15m) < 3: return

    df5  = add_rsi(df_5m)
    df5  = add_sma(df5, "Volume", 10, "V_MA10")

    curr5  = df5.iloc[-1]
    prev5  = df5.iloc[-2]
    curr15 = df_15m.iloc[-1]

    hi15 = df5.iloc[0:3]["High"].max()
    lo15 = df5.iloc[0:3]["Low"].min()
    vr   = curr5["Volume"] / (curr5["V_MA10"] + 1)
    rsi  = curr5["RSI"]

    # 多頭突破
    if all([curr5["Close"] > hi15, prev5["Close"] <= hi15,
            vr >= 2.0, curr15["Close"] > hi15]):
        send_tg(
            f"🚀 *[ORB 多頭突破]*: `{symbol}`\n"
            f"💰 現價: `{curr5['Close']:.2f}` · 突破: `{hi15:.2f}`\n"
            f"📊 量比: `{vr:.1f}x` · RSI: `{rsi:.0f}`\n"
            f"✅ 5m + 15m 雙重確認\n"
            f"👉 TradingView 2m 看進場\n"
            f"⏰ {tw_time()} TWN"
        )
        return

    # 空頭跌破
    if all([curr5["Close"] < lo15, prev5["Close"] >= lo15,
            vr >= 2.0, curr15["Close"] < lo15]):
        send_tg(
            f"🔻 *[ORB 空頭跌破]* Sell Call 參考: `{symbol}`\n"
            f"💰 現價: `{curr5['Close']:.2f}` · 跌破: `{lo15:.2f}`\n"
            f"📊 量比: `{vr:.1f}x` · RSI: `{rsi:.0f}`\n"
            f"✅ 5m + 15m 雙重確認\n"
            f"⏰ {tw_time()} TWN"
        )

# ── 策略C：PULLBACK 波段縮量回測 ──────────────────────────────────────────────
def strategy_pullback(symbol, df_1d, df_5m):
    if len(df_1d) < 65 or len(df_5m) < 3: return

    d = add_rsi(df_1d)
    d = add_sma(d, "Close",  60, "MA60")
    d = add_sma(d, "Volume",  5, "V_Avg5")

    f = add_rsi(df_5m)

    d_c = d.iloc[-1]
    d_p = d.iloc[-2]
    f_c = f.iloc[-1]
    f_p = f.iloc[-2]

    if pd.isna(d_c["MA60"]) or pd.isna(d_c["RSI"]): return

    bias = (d_c["Close"] - d_c["MA60"]) / d_c["MA60"] * 100
    vr   = d_c["Volume"] / (d_c["V_Avg5"] + 1)

    lights = [
        d_c["Close"] > d_c["MA60"],                          # C1 在60MA之上
        42 <= d_p["RSI"] <= 55 and d_c["RSI"] > d_p["RSI"], # C2 日線RSI勾頭
        vr < 0.9,                                            # C3 日線縮量
        0 <= bias < 4,                                       # C4 乖離率 0–4%
        f_c["RSI"] > f_p["RSI"],                             # C5 5m RSI確認
    ]
    if all(lights):
        send_tg(
            f"📈 *[PULLBACK 波段縮量勾頭]*: `{symbol}`\n"
            f"💰 現價: `{d_c['Close']:.2f}`\n"
            f"📏 距季線: `{bias:.1f}%` · 日RSI: `{d_c['RSI']:.0f}`\n"
            f"📊 量能: `{vr:.2f}x` 均量\n"
            f"✅ 日線 + 5m 雙重確認\n"
            f"💡 Sell Put 佈局 · 守 60MA\n"
            f"⏰ {tw_time()} TWN"
        )

# ── 主程式 ─────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*45}")
    print(f"CC Scanner {datetime.now(pytz.timezone('Asia/Taipei')).strftime('%Y-%m-%d %H:%M')} TWN")
    print(f"{'='*45}")

    for sym in US_TICKERS + TW_TICKERS:
        if not is_market_open(sym):
            continue
        try:
            print(f"掃描 {sym}...")
            df_5m  = get_5m(sym)
            df_15m = get_15m(sym)
            if df_5m.empty: continue

            strategy_washout(sym, df_5m)
            strategy_orb(sym, df_5m, df_15m)

            if is_swing_scan_time(sym):
                df_1d = get_1d(sym)
                if not df_1d.empty:
                    strategy_pullback(sym, df_1d, df_5m)

        except Exception as e:
            print(f"  {sym}: {e}")

    print("掃描結束\n")

if __name__ == "__main__":
    main()
