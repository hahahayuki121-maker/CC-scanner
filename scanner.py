"""
CC Market Scanner v8.0
數據源：Alpaca API（美股即時）/ yfinance（台股、加密、日線）
標籤：🇺🇸權值 | 🛡️資安 | ⚛️核能 | 🚀妖股 | 🇨🇳中概 | 🇹🇼台股 | ₿加密
策略：⛈️暴跌預兆 | 🔮暴漲預兆 | ⚡WASHOUT | 📈波段PULLBACK | 🏦SMC訂單塊 | ₿半木夏背離
等級：S級/A級（B級已移除）

v8.0 更新：
  1. 盤前信號改為三次批次匯總（-30min / -15min / 開盤當下）
  2. 新增 SMC（Smart Money Concept）策略：Order Block + FVG + BOS/CHoCH
  3. 加密改為半木夏三背離（BTC 15m，MACD 12/26/9，三峰/谷背離）
  4. Minervini 趨勢模板前置過濾（美股）
  5. 持倉整合提示（NVDA/CRCL/NVTS/定投股）
"""

import requests
import pandas as pd
import numpy as np
import ta
import yfinance as yf
import os
from datetime import datetime, date
import pytz

# ── Token ─────────────────────────────────────────────────────────────────────
TG_TOKEN      = os.environ.get("TG_TOKEN",      "")
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID",    "")
ALPACA_KEY    = os.environ.get("ALPACA_KEY",    "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")
ALPACA_BASE   = "https://data.alpaca.markets/v2"

# ── 監控名單 ──────────────────────────────────────────────────────────────────
TICKERS = {
    "🇺🇸": ["NVDA","AVGO","ANET","VRT","VST","TSLA","AMD","AMZN","AAPL","META","MSFT","GOOGL","PLTR","CRDO","ALAB","QQQ"],
    "🛡️": ["PANW","FTNT","CRWD"],
    "⚛️": ["SMR","OKLO","NNE"],
    "🚀": ["CRCL","COIN","MSTR","MARA","CLSK","HOOD","SOFI","APLD","IONQ","RGTI","NVTS","AAOI","RCAT","ONDS","AXTI","AEHR","ACMR","KTOS","SERV"],
    "🇹🇼": ["2330.TW","00631L.TW"],
    "₿":   [("BTC-USD","BTC/USDT")],
}

# SMC 單獨跑，覆蓋所有美股標的
SMC_TICKERS = (
    TICKERS["🇺🇸"] + TICKERS["🛡️"] + TICKERS["⚛️"] +
    TICKERS["🚀"] 
)

# ── 持倉提示 ──────────────────────────────────────────────────────────────────
PORTFOLIO_HINTS = {
    "NVDA": "💼 持238股 → PULLBACK/OB可賣Covered Call，行權價現價+5%，2~4週到期",
    "CRCL": "💼 持110股 → 高波動，S級信號才動，停損設前低",
    "NVTS": "💼 持200股 → 妖股，量確認再進，留意假突破",
    "AVGO": "💼 定投第4月 → 強信號可額外加碼1股",
    "VRT":  "💼 定投第4月 → 強信號可額外加碼1股",
    "ANET": "💼 定投第4月 → 強信號可額外加碼1股",
}

# ── 假日清單 ──────────────────────────────────────────────────────────────────
US_HOLIDAYS = {
    "2025-01-01","2025-01-20","2025-02-17","2025-04-18",
    "2025-05-26","2025-06-19","2025-07-04","2025-09-01",
    "2025-11-27","2025-12-25",
    "2026-01-01","2026-01-19","2026-02-16","2026-04-03",
    "2026-04-04","2026-05-25","2026-06-19","2026-07-03",
    "2026-09-07","2026-11-26","2026-12-25",
}
TW_HOLIDAYS = {
    "2026-01-01","2026-01-27","2026-01-28","2026-01-29","2026-01-30",
    "2026-02-28","2026-04-04","2026-04-05","2026-05-01",
    "2026-06-19","2026-09-26","2026-10-09","2026-10-10",
}

# ── 時間工具 ──────────────────────────────────────────────────────────────────
def _now_ny(): return datetime.now(pytz.timezone("America/New_York"))
def _now_tw(): return datetime.now(pytz.timezone("Asia/Taipei"))

def us_market_status():
    ny = _now_ny()
    d  = ny.strftime("%Y-%m-%d")
    if ny.weekday() >= 5 or d in US_HOLIDAYS: return "CLOSED"
    m = ny.hour * 60 + ny.minute
    if 240 <= m < 570:  return "PRE"
    if 570 <= m < 930:  return "OPEN"
    if 930 <= m < 1200: return "POST"
    return "CLOSED"

def is_tw_open():
    tw = _now_tw(); d = tw.strftime("%Y-%m-%d")
    if tw.weekday() >= 5 or d in TW_HOLIDAYS: return False
    m = tw.hour * 60 + tw.minute
    return 540 <= m < 810

def is_tw_swing():
    tw = _now_tw(); d = tw.strftime("%Y-%m-%d")
    if tw.weekday() >= 5 or d in TW_HOLIDAYS: return False
    m = tw.hour * 60 + tw.minute
    return 780 <= m < 810

def is_us_swing():
    ny = _now_ny(); d = ny.strftime("%Y-%m-%d")
    if ny.weekday() >= 5 or d in US_HOLIDAYS: return False
    m = ny.hour * 60 + ny.minute
    return 900 <= m < 930

def pre_market_window():
    """
    PRE_30  = ET 09:00~09:05（開盤前30分鐘匯總）
    PRE_15  = ET 09:15~09:20（開盤前15分鐘匯總）
    OPEN_NOW= ET 09:30~09:35（開盤當下匯總）
    """
    ny = _now_ny(); d = ny.strftime("%Y-%m-%d")
    if ny.weekday() >= 5 or d in US_HOLIDAYS: return None
    m = ny.hour * 60 + ny.minute
    if 540 <= m < 545:  return "PRE_30"
    if 555 <= m < 560:  return "PRE_15"
    if 570 <= m < 575:  return "OPEN_NOW"
    return None

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        print(msg); return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        return r.json().get("ok", False)
    except: return False

def tw_time(): return _now_tw().strftime("%H:%M:%S")
def L(v): return "✅" if v else "❌"

def grade(score, total):
    pct = score / total
    if pct >= 0.85: return "🏆 S級"
    if pct >= 0.70: return "🥇 A級"
    return None

# ── Minervini 趨勢模板快取 ────────────────────────────────────────────────────
_trend_cache = {}

def passes_trend_template(sym):
    if sym in _trend_cache: return _trend_cache[sym]
    try:
        df = _clean(yf.download(sym, interval="1d", period="200d",
                                progress=False, auto_adjust=True))
        if len(df) < 55:
            _trend_cache[sym] = True; return True
        c     = df["Close"]
        price = float(c.iloc[-1])
        ma50  = float(c.rolling(50).mean().iloc[-1])
        ma150 = float(c.rolling(150).mean().iloc[-1]) if len(df) >= 150 else None
        ma200 = float(c.rolling(200).mean().iloc[-1]) if len(df) >= 200 else None
        checks = [price > ma50]
        if ma150: checks.append(ma50 > ma150)
        if ma200 and ma150:
            checks += [
                ma150 > ma200,
                ma200 > float(c.rolling(200).mean().iloc[-22]),
                price >= float(c.tail(252).min()) * 1.25,
                price >= float(c.tail(252).max()) * 0.75,
            ]
        result = sum(checks) >= max(1, len(checks) * 0.7)
        _trend_cache[sym] = result; return result
    except:
        _trend_cache[sym] = True; return True

# ── 數據抓取 ──────────────────────────────────────────────────────────────────
def get_alpaca_bars(symbol, timeframe="5Min", limit=80):
    if not ALPACA_KEY or not ALPACA_SECRET: return pd.DataFrame()
    try:
        url     = f"{ALPACA_BASE}/stocks/{symbol}/bars"
        params  = {"timeframe": timeframe, "limit": limit,
                   "adjustment": "raw", "feed": "iex"}
        headers = {"APCA-API-KEY-ID": ALPACA_KEY,
                   "APCA-API-SECRET-KEY": ALPACA_SECRET}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200: return pd.DataFrame()
        bars = r.json().get("bars", [])
        if not bars: return pd.DataFrame()
        df = pd.DataFrame(bars)
        df["t"] = pd.to_datetime(df["t"])
        df = df.set_index("t")
        df = df.rename(columns={"o":"Open","h":"High","l":"Low","c":"Close","v":"Volume"})
        return df[["Open","High","Low","Close","Volume"]].dropna()
    except Exception as e:
        print(f"  Alpaca error {symbol}: {e}"); return pd.DataFrame()

def get_alpaca_5m(sym):  return get_alpaca_bars(sym, "5Min",  80)
def get_alpaca_15m(sym): return get_alpaca_bars(sym, "15Min", 40)

def _clean(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

def get_yf_5m(s):  return _clean(yf.download(s, interval="5m",  period="2d",   progress=False, auto_adjust=True))
def get_yf_15m(s): return _clean(yf.download(s, interval="15m", period="10d",  progress=False, auto_adjust=True))
def get_yf_1d(s):  return _clean(yf.download(s, interval="1d",  period="100d", progress=False, auto_adjust=True))

def get_df5(sym):
    df = get_alpaca_5m(sym)
    return df if not df.empty else get_yf_5m(sym)

def get_df15(sym):
    df = get_alpaca_15m(sym)
    return df if not df.empty else get_yf_15m(sym)

# ── 指標 ──────────────────────────────────────────────────────────────────────
def add_rsi(df, n=14):
    df = df.copy()
    df["RSI"] = ta.momentum.RSIIndicator(df["Close"], window=n).rsi()
    return df

def add_sma(df, col, n, out):
    df = df.copy()
    df[out] = ta.trend.SMAIndicator(df[col], window=n).sma_indicator()
    return df

def add_macd(df):
    df = df.copy()
    m = ta.trend.MACD(df["Close"], window_slow=26, window_fast=12, window_sign=9)
    df["MACD"] = m.macd(); df["MACD_sig"] = m.macd_signal(); df["MACD_hist"] = m.macd_diff()
    return df

def add_atr(df, n=14):
    df = df.copy()
    df["ATR"] = ta.volatility.AverageTrueRange(
        df["High"], df["Low"], df["Close"], window=n).average_true_range()
    return df

# ══════════════════════════════════════════════════════════════════════════════
# 🏦 SMC（Smart Money Concept）
# 偵測：Order Block + Fair Value Gap + BOS/CHoCH
# 時間框架：15m（日內）+ 1D（波段）
#
# Order Block（OB）：
#   多頭OB = 強勢上漲前的最後一根收黑K（機構買入區）
#   空頭OB = 強勢下跌前的最後一根收紅K（機構賣出區）
#
# Fair Value Gap（FVG）：
#   三根K線，第一根高點 < 第三根低點 → 多頭FVG（缺口）
#   三根K線，第一根低點 > 第三根高點 → 空頭FVG（缺口）
#
# BOS（Break of Structure）= 結構突破（順勢）
# CHoCH（Change of Character）= 性格改變（逆勢反轉）
#
# 進場邏輯：
#   價格回測OB或FVG區域 + BOS確認方向 → S/A級信號
# ══════════════════════════════════════════════════════════════════════════════
def signal_smc(sym, tag, df15, df1d):
    """
    SMC 多時間框架信號偵測
    df15: 15分鐘K線（日內OB/FVG）
    df1d: 日線（趨勢方向/波段OB）
    """
    if len(df15) < 30 or len(df1d) < 20: return None

    results = []

    # ── 1. 判斷日線結構方向（Higher High / Lower Low）─────────────────────
    d = df1d.copy()
    recent_highs = d["High"].tail(10).values
    recent_lows  = d["Low"].tail(10).values
    # 趨勢判斷：最近5根 vs 前5根
    bull_structure = (recent_highs[-3:].max() > recent_highs[:5].max() and
                      recent_lows[-3:].min()  > recent_lows[:5].min())
    bear_structure = (recent_highs[-3:].max() < recent_highs[:5].max() and
                      recent_lows[-3:].min()  < recent_lows[:5].min())

    # ── 2. 15分鐘 Order Block 識別 ────────────────────────────────────────
    f = df15.copy()
    curr_price = float(f["Close"].iloc[-1])

    # 多頭OB：找近20根K線中，後面跟著強勢上漲的最後一根收黑K
    bullish_ob = None
    bearish_ob = None

    for i in range(3, min(25, len(f)-2)):
        idx = -(i)
        bar     = f.iloc[idx]
        next3   = f.iloc[idx+1:idx+4]
        # 多頭OB條件：收黑K + 後面3根明顯上漲（漲幅>0.5%）
        if (bar["Close"] < bar["Open"] and
                next3["Close"].iloc[-1] > bar["High"] and
                (next3["Close"].iloc[-1] - bar["Low"]) / bar["Low"] > 0.005):
            ob_high = float(bar["High"])
            ob_low  = float(bar["Low"])
            # 目前價格在OB範圍內或剛回測
            if ob_low <= curr_price <= ob_high * 1.005:
                bullish_ob = {"high": ob_high, "low": ob_low, "age": i}
                break

        # 空頭OB條件：收紅K + 後面3根明顯下跌
        if (bar["Close"] > bar["Open"] and
                next3["Close"].iloc[-1] < bar["Low"] and
                (bar["High"] - next3["Close"].iloc[-1]) / bar["High"] > 0.005):
            ob_high = float(bar["High"])
            ob_low  = float(bar["Low"])
            if ob_low * 0.995 <= curr_price <= ob_high:
                bearish_ob = {"high": ob_high, "low": ob_low, "age": i}
                break

    # ── 3. Fair Value Gap（FVG）識別 ──────────────────────────────────────
    bullish_fvg = None
    bearish_fvg = None

    for i in range(2, min(20, len(f)-1)):
        idx  = -(i)
        bar1 = f.iloc[idx-1]
        bar3 = f.iloc[idx+1]
        # 多頭FVG：bar1高點 < bar3低點
        if float(bar1["High"]) < float(bar3["Low"]):
            fvg_top = float(bar3["Low"])
            fvg_bot = float(bar1["High"])
            if fvg_bot <= curr_price <= fvg_top:
                bullish_fvg = {"top": fvg_top, "bot": fvg_bot, "age": i}
                break
        # 空頭FVG：bar1低點 > bar3高點
        if float(bar1["Low"]) > float(bar3["High"]):
            fvg_top = float(bar1["Low"])
            fvg_bot = float(bar3["High"])
            if fvg_bot <= curr_price <= fvg_top:
                bearish_fvg = {"top": fvg_top, "bot": fvg_bot, "age": i}
                break

    # ── 4. BOS / CHoCH 確認 ───────────────────────────────────────────────
    # BOS多頭：突破近期高點結構
    swing_high = float(f["High"].tail(15).iloc[:-2].max())
    swing_low  = float(f["Low"].tail(15).iloc[:-2].min())
    bos_bull   = curr_price > swing_high
    bos_bear   = curr_price < swing_low

    # CHoCH：原本下跌趨勢中出現BOS多頭（反轉）
    choch_bull = bos_bull and bear_structure
    choch_bear = bos_bear and bull_structure

    # ── 5. 組合信號 ───────────────────────────────────────────────────────
    hint = PORTFOLIO_HINTS.get(sym, "")

    # 多頭信號：日線牛市結構 + (OB回測 或 FVG回測) + BOS確認
    bull_conditions = [
        bull_structure,
        bullish_ob is not None,
        bullish_fvg is not None,
        bos_bull,
        choch_bull,
    ]
    bull_score = sum(bull_conditions)
    # 至少要有：結構 + OB或FVG其中一個 + BOS
    bull_valid = (bull_structure and
                  (bullish_ob or bullish_fvg) and
                  (bos_bull or choch_bull))

    if bull_valid:
        g = grade(bull_score, 5)
        if g:
            ob_info  = f"OB區: `{bullish_ob['low']:.2f}~{bullish_ob['high']:.2f}`" if bullish_ob else ""
            fvg_info = f"FVG區: `{bullish_fvg['bot']:.2f}~{bullish_fvg['top']:.2f}`" if bullish_fvg else ""
            choch_tag = " *CHoCH反轉*" if choch_bull else " BOS順勢"
            stop = (bullish_ob["low"] if bullish_ob else bullish_fvg["bot"]) * 0.995
            target = curr_price + (curr_price - stop) * 2

            results.append({
                "score": bull_score,
                "msg": (
                    f"{tag} 🏦 *[SMC 多頭]{choch_tag}* `{sym}` {g}\n"
                    f"💰 現價: `{curr_price:.2f}`\n"
                    f"燈號: {L(bull_structure)}日線牛市結構 {L(bullish_ob is not None)}OB回測 "
                    f"{L(bullish_fvg is not None)}FVG回測 {L(bos_bull)}BOS突破 {L(choch_bull)}CHoCH\n"
                    + (f"📦 {ob_info}\n" if ob_info else "")
                    + (f"🕳️ {fvg_info}\n" if fvg_info else "")
                    + f"🎯 進場: `{curr_price:.2f}` · 止損: `{stop:.2f}` · 目標: `{target:.2f}` (1:2)\n"
                    + (f"📌 {hint}\n" if hint else "")
                    + f"⏰ {tw_time()} TWN"
                )
            })

    # 空頭信號
    bear_conditions = [
        bear_structure,
        bearish_ob is not None,
        bearish_fvg is not None,
        bos_bear,
        choch_bear,
    ]
    bear_score = sum(bear_conditions)
    bear_valid = (bear_structure and
                  (bearish_ob or bearish_fvg) and
                  (bos_bear or choch_bear))

    if bear_valid:
        g = grade(bear_score, 5)
        if g:
            ob_info  = f"OB區: `{bearish_ob['low']:.2f}~{bearish_ob['high']:.2f}`" if bearish_ob else ""
            fvg_info = f"FVG區: `{bearish_fvg['bot']:.2f}~{bearish_fvg['top']:.2f}`" if bearish_fvg else ""
            choch_tag = " *CHoCH反轉*" if choch_bear else " BOS順勢"
            stop   = (bearish_ob["high"] if bearish_ob else bearish_fvg["top"]) * 1.005
            target = curr_price - (stop - curr_price) * 2

            results.append({
                "score": bear_score,
                "msg": (
                    f"{tag} 🏦 *[SMC 空頭]{choch_tag}* `{sym}` {g}\n"
                    f"💰 現價: `{curr_price:.2f}`\n"
                    f"燈號: {L(bear_structure)}日線熊市結構 {L(bearish_ob is not None)}OB回測 "
                    f"{L(bearish_fvg is not None)}FVG回測 {L(bos_bear)}BOS跌破 {L(choch_bear)}CHoCH\n"
                    + (f"📦 {ob_info}\n" if ob_info else "")
                    + (f"🕳️ {fvg_info}\n" if fvg_info else "")
                    + f"🎯 進場: `{curr_price:.2f}` · 止損: `{stop:.2f}` · 目標: `{target:.2f}` (1:2)\n"
                    + (f"📌 {hint}\n" if hint else "")
                    + f"⏰ {tw_time()} TWN"
                )
            })

    return results if results else None

# ══════════════════════════════════════════════════════════════════════════════
# ⛈️ 暴跌預兆
# ══════════════════════════════════════════════════════════════════════════════
def signal_crash_warning(sym, tag, df5):
    if len(df5) < 20: return None
    df = add_rsi(add_sma(df5, "Volume", 15, "VMA"))
    curr = df.iloc[-1]
    if pd.isna(curr["VMA"]): return None
    vr      = curr["Volume"] / (curr["VMA"] + 1)
    prev_hi = df["High"].tail(20).max()
    c1 = curr["Close"] > prev_hi * 0.94 and vr > 3.5 and curr["Close"] < curr["Open"]
    c2 = curr["Close"] < df["Low"].tail(6).iloc[:-1].min()
    score = sum([c1, c2])
    if score == 0: return None
    g = grade(score, 2)
    if not g: return None
    hint = PORTFOLIO_HINTS.get(sym, "")
    return {
        "score": score + 10,
        "msg": (
            f"{tag} ⛈️ *[暴跌預兆]* `{sym}` {g}\n"
            f"💰 現價: `{curr['Close']:.2f}`\n"
            f"燈號: {L(c1)}高位爆量收黑 {L(c2)}跌破5日支撐\n"
            f"📊 量比: `{vr:.1f}x` · RSI: `{curr['RSI']:.0f}`\n"
            f"🚨 建議立刻減倉，切勿留過夜\n"
            + (f"📌 {hint}\n" if hint else "")
            + f"⏰ {tw_time()} TWN"
        )
    }

# ══════════════════════════════════════════════════════════════════════════════
# 🔮 暴漲預兆
# ══════════════════════════════════════════════════════════════════════════════
def signal_breakout_pre(sym, tag, df5, df15):
    if len(df5) < 20 or len(df15) < 5: return None
    df = add_rsi(add_sma(df5, "Volume", 15, "VMA"))
    curr = df.iloc[-1]; prev = df.iloc[-2]
    if pd.isna(curr["VMA"]): return None
    vr      = curr["Volume"] / (curr["VMA"] + 1)
    prev_hi = df["High"].tail(7).iloc[:-1].max()
    c15     = df15.iloc[-1]
    c1 = vr > 2.5
    c2 = curr["Close"] > prev_hi
    c3 = prev["Close"] <= prev_hi
    c4 = 55 < curr["RSI"] < 78
    c5 = c15["Close"] > prev_hi
    score = sum([c1, c2, c3, c4, c5])
    g = grade(score, 5)
    if not g or not (c2 and c3): return None
    hint = PORTFOLIO_HINTS.get(sym, "")
    return {
        "score": score,
        "msg": (
            f"{tag} 🔮 *[暴漲預兆]* `{sym}` {g}\n"
            f"💰 現價: `{curr['Close']:.2f}` · 突破: `{prev_hi:.2f}`\n"
            f"燈號: {L(c1)}量2.5x {L(c2)}突破前高 {L(c3)}剛發動 {L(c4)}RSI動能 {L(c5)}15m確認\n"
            f"📊 量比: `{vr:.1f}x` · RSI: `{curr['RSI']:.0f}` · 條件: `{score}/5`\n"
            f"💡 現股建倉或 Buy Call\n"
            + (f"📌 {hint}\n" if hint else "")
            + f"⏰ {tw_time()} TWN"
        )
    }

# ══════════════════════════════════════════════════════════════════════════════
# ⚡ WASHOUT
# ══════════════════════════════════════════════════════════════════════════════
def signal_washout(sym, tag, df5, df15, status):
    if len(df5) < 6 or len(df15) < 3: return None
    df = add_rsi(add_sma(add_sma(df5, "Volume", 10, "VMA10"), "Close", 5, "MA5"))
    df = add_sma(df, "Close", 20, "MA20")
    curr = df.iloc[-1]; prev = df.iloc[-2]; prev2 = df.iloc[-3]
    if pd.isna(curr["MA5"]) or pd.isna(curr["MA20"]): return None
    day_open = df.iloc[0]["Open"]
    day_low  = df["Low"].min()
    yest     = df[df.index.date < df.index[-1].date()]
    yest_low = yest["Low"].min() if not yest.empty else day_low * 0.97
    drop     = (day_open - day_low) / day_open * 100
    rebound  = (curr["Close"] - day_low) / (day_open - day_low + 0.001)
    vr       = curr["Volume"] / (curr["VMA10"] + 1)
    min_drop = 1.5 if "🚀" not in tag else 2.0
    c1 = drop > min_drop
    c2 = curr["Close"] >= day_open * 0.998
    c3 = prev["Close"] < day_open
    c4 = curr["RSI"] > prev["RSI"] > prev2["RSI"]
    c5 = curr["RSI"] < 72
    c6 = curr["Close"] > yest_low
    c7 = rebound > 0.5
    c8 = curr["MA5"] > curr["MA20"]
    score = sum([c1, c2, c3, c4, c5, c6, c7, c8])
    g = grade(score, 8)
    if not g or not c1 or not c2: return None
    prefix = "🌅 [盤前洗盤]" if status == "PRE" else "⚡ [WASHOUT]"
    warn   = f"\n⚠️ RSI `{curr['RSI']:.0f}` 偏高，等回測5MA再加碼" if curr["RSI"] > 65 else ""
    hint   = PORTFOLIO_HINTS.get(sym, "")
    return {
        "score": score,
        "msg": (
            f"{tag} {prefix} `{sym}` {g}\n"
            f"💰 現價: `{curr['Close']:.2f}` · 殺低: `{drop:.1f}%` · 反彈: `{rebound*100:.0f}%`\n"
            f"燈號: {L(c1)}殺低 {L(c2)}站回 {L(c3)}剛翻 {L(c4)}RSI勾 "
            f"{L(c5)}非追高 {L(c6)}守昨低 {L(c7)}彈力 {L(c8)}MA翻多\n"
            f"📊 RSI: `{curr['RSI']:.0f}` · 量比: `{vr:.1f}x` · 條件: `{score}/8`{warn}\n"
            f"🎯 目標: `{curr['Close']*1.02:.2f}` (+2%) · 止損: `{yest_low:.2f}`\n"
            + (f"📌 {hint}\n" if hint else "")
            + f"⏰ {tw_time()} TWN"
        )
    }

# ══════════════════════════════════════════════════════════════════════════════
# 📈 波段PULLBACK
# ══════════════════════════════════════════════════════════════════════════════
def signal_pullback(sym, tag, df1d, df5):
    if len(df1d) < 65 or len(df5) < 3: return None
    d = add_macd(add_rsi(add_sma(add_sma(df1d, "Close", 60, "MA60"), "Volume", 5, "V5")))
    f = add_rsi(df5)
    d_c = d.iloc[-1]; d_p = d.iloc[-2]; f_c = f.iloc[-1]; f_p = f.iloc[-2]
    if pd.isna(d_c["MA60"]) or pd.isna(d_c["RSI"]): return None
    bias60 = (d_c["Close"] - d_c["MA60"]) / d_c["MA60"] * 100
    vr     = d_c["Volume"] / (d_c["V5"] + 1)
    c1 = d_c["Close"] > d_c["MA60"]
    c2 = 42 <= d_p["RSI"] <= 58 and d_c["RSI"] > d_p["RSI"]
    c3 = vr < 0.85
    c4 = 0 <= bias60 < 5
    c5 = f_c["RSI"] > f_p["RSI"]
    c6 = d_c["MACD_hist"] > d_p["MACD_hist"]
    score = sum([c1, c2, c3, c4, c5, c6])
    g = grade(score, 6)
    if not g or not c1 or not c2: return None
    sp   = d_c["Close"] * 0.95
    hint = PORTFOLIO_HINTS.get(sym, "")
    if sym == "NVDA":
        hint = f"💼 持238股 → 可賣Covered Call，行權價約 `{d_c['Close']*1.05:.0f}`，2~4週到期"
    return {
        "score": score,
        "msg": (
            f"{tag} 📈 *[波段PULLBACK]* `{sym}` {g}\n"
            f"💰 現價: `{d_c['Close']:.2f}` · 距季線: `{bias60:.1f}%`\n"
            f"燈號: {L(c1)}季線上 {L(c2)}RSI勾頭 {L(c3)}縮量 "
            f"{L(c4)}貼季線 {L(c5)}5m確認 {L(c6)}MACD放大\n"
            f"📊 日RSI: `{d_c['RSI']:.0f}` · 量能: `{vr:.2f}x` · 條件: `{score}/6`\n"
            f"💡 Sell Put 行權價: `{sp:.1f}` (-5%) · 守季線\n"
            + (f"📌 {hint}\n" if hint else "")
            + f"⏰ {tw_time()} TWN"
        )
    }

# ══════════════════════════════════════════════════════════════════════════════
# ₿ 半木夏三背離（BTC 15m）
# MACD 12/26/9，尋找三次連續峰/谷背離
# 實心柱轉虛心柱時進場，止損 ± ATR，目標 1:2
# ══════════════════════════════════════════════════════════════════════════════
def signal_banmuxa(yf_sym, disp, df15):
    if len(df15) < 120: return None
    df   = add_atr(add_macd(df15.copy()))
    hist  = df["MACD_hist"].values
    high  = df["High"].values
    low   = df["Low"].values
    curr  = df.iloc[-1]
    atr   = float(curr["ATR"]) if not pd.isna(curr["ATR"]) else 0
    price = float(curr["Close"])

    def find_extremes(arr, window=5, mode="peak"):
        out = []
        for i in range(window, len(arr) - window):
            seg = arr[i-window:i+window+1]
            if mode == "peak"   and arr[i] == max(seg) and arr[i] > 0: out.append(i)
            if mode == "trough" and arr[i] == min(seg) and arr[i] < 0: out.append(i)
        return out

    peaks   = find_extremes(hist, mode="peak")
    troughs = find_extremes(hist, mode="trough")
    results = []

    # ── 空頭三背離 ────────────────────────────────────────────────────────
    if len(peaks) >= 3:
        p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
        macd_up    = hist[p1] < hist[p2] < hist[p3]   # MACD峰值遞升
        price_down = high[p1] > high[p2] > high[p3]   # 價格高點遞降
        # 虛心柱：正柱縮小超過30%且連續縮
        shrinking  = (hist[-1] > 0 and
                      abs(hist[-1]) < abs(hist[p3]) * 0.7 and
                      hist[-1] < hist[-2])
        if macd_up and price_down and shrinking:
            stop   = float(high[p3]) + atr
            risk   = max(stop - price, atr * 0.5)
            target = price - risk * 2
            results.append({"score": 9, "msg": (
                f"₿ 🔻 *[半木夏 空頭三背離]* `{disp}` 🏆 S級\n"
                f"💰 現價: `{price:.2f}`\n"
                f"📊 MACD三峰: `{hist[p1]:.4f}` → `{hist[p2]:.4f}` → `{hist[p3]:.4f}` ↑\n"
                f"📊 K線高點: `{high[p1]:.2f}` → `{high[p2]:.2f}` → `{high[p3]:.2f}` ↓\n"
                f"⚡ 正柱縮小（實心→虛心）· 進場信號\n"
                f"🎯 做空: `{price:.2f}` · 止損: `{stop:.2f}` (+ATR {atr:.0f})\n"
                f"💰 目標: `{target:.2f}` (1:2 風報比)\n"
                f"⏰ {tw_time()} TWN"
            )})

    # ── 多頭三背離 ────────────────────────────────────────────────────────
    if len(troughs) >= 3:
        t1, t2, t3 = troughs[-3], troughs[-2], troughs[-1]
        macd_down  = hist[t1] > hist[t2] > hist[t3]   # MACD谷值遞降（更負）
        price_up   = low[t1] < low[t2] < low[t3]      # 價格低點遞升
        shrinking  = (hist[-1] < 0 and
                      abs(hist[-1]) < abs(hist[t3]) * 0.7 and
                      hist[-1] > hist[-2])
        if macd_down and price_up and shrinking:
            stop   = float(low[t3]) - atr
            risk   = max(price - stop, atr * 0.5)
            target = price + risk * 2
            results.append({"score": 9, "msg": (
                f"₿ 🚀 *[半木夏 多頭三背離]* `{disp}` 🏆 S級\n"
                f"💰 現價: `{price:.2f}`\n"
                f"📊 MACD三谷: `{hist[t1]:.4f}` → `{hist[t2]:.4f}` → `{hist[t3]:.4f}` ↓\n"
                f"📊 K線低點: `{low[t1]:.2f}` → `{low[t2]:.2f}` → `{low[t3]:.2f}` ↑\n"
                f"⚡ 負柱縮小（實心→虛心）· 進場信號\n"
                f"🎯 做多: `{price:.2f}` · 止損: `{stop:.2f}` (-ATR {atr:.0f})\n"
                f"💰 目標: `{target:.2f}` (1:2 風報比)\n"
                f"⏰ {tw_time()} TWN"
            )})

    return results if results else None

# ══════════════════════════════════════════════════════════════════════════════
# 盤前匯總（PRE_30 / PRE_15 / OPEN_NOW）
# ══════════════════════════════════════════════════════════════════════════════
def run_pre_market_scan():
    window = pre_market_window()
    if not window: return
    label = {
        "PRE_30":   "⏰ 開盤前30分鐘 盤前匯總",
        "PRE_15":   "⏰ 開盤前15分鐘 盤前匯總",
        "OPEN_NOW": "🔔 開盤當下 信號匯總",
    }[window]

    all_sigs = []
    us_tags  = ["🇺🇸", "🛡️", "⚛️", "🚀", "🇨🇳"]

    for tag in us_tags:
        for sym in TICKERS.get(tag, []):
            try:
                if not passes_trend_template(sym): continue
                df5  = get_df5(sym)
                df15 = get_df15(sym)
                df1d = get_yf_1d(sym)
                if df5.empty: continue

                for fn in [
                    lambda s, t, d5, d15: signal_crash_warning(s, t, d5),
                    signal_breakout_pre,
                    lambda s, t, d5, d15: signal_washout(s, t, d5, d15, "PRE"),
                ]:
                    r = fn(sym, tag, df5, df15)
                    if r: all_sigs.append(r)

                # SMC 也加入盤前匯總
                if not df15.empty and not df1d.empty:
                    smc = signal_smc(sym, tag, df15, df1d)
                    if smc: all_sigs.extend(smc)

            except Exception as e:
                print(f"  PRE {sym}: {e}")

    all_sigs.sort(key=lambda x: x["score"], reverse=True)

    if not all_sigs:
        send_tg(f"━━━ {label} ━━━\n📭 無 S/A 級信號")
        return

    send_tg(
        f"━━━ {label} · {len(all_sigs)}個信號 ━━━\n"
        f"🗓️ {_now_tw().strftime('%Y-%m-%d %H:%M')} TWN\n"
        f"已通過 Minervini 趨勢模板過濾："
    )
    for s in all_sigs:
        send_tg(s["msg"])

# ══════════════════════════════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════════════════════════════
def main():
    status = us_market_status()
    tw_now = _now_tw()
    print(f"\n{'='*55}")
    print(f"CC Scanner v8.0 · {tw_now.strftime('%Y-%m-%d %H:%M')} TWN")
    print(f"美股:{status} | 台股:{'開盤' if is_tw_open() else '休市'} | 加密:24/7")
    print(f"{'='*55}")

    # ── 盤前匯總視窗 ─────────────────────────────────────────────────────────
    if pre_market_window():
        run_pre_market_scan()
        if pre_market_window() in ("PRE_30", "PRE_15"):
            print("盤前匯總完成\n"); return

    intraday_sigs = []
    swing_sigs    = []
    smc_sigs      = []
    crypto_sigs   = []

    # ── 美股即時（開盤中）────────────────────────────────────────────────────
    if status == "OPEN":
        us_tags = ["🇺🇸", "🛡️", "⚛️", "🚀", "🇨🇳"]
        for tag in us_tags:
            for sym in TICKERS.get(tag, []):
                try:
                    if not passes_trend_template(sym): continue
                    df5  = get_df5(sym)
                    df15 = get_df15(sym)
                    if df5.empty: continue
                    for fn in [
                        lambda s, t, d5, d15: signal_crash_warning(s, t, d5),
                        signal_breakout_pre,
                        lambda s, t, d5, d15: signal_washout(s, t, d5, d15, status),
                    ]:
                        r = fn(sym, tag, df5, df15)
                        if r: intraday_sigs.append(r)
                except Exception as e:
                    print(f"  {sym}: {e}")

    # ── SMC 掃描（開盤中 + 波段時段）────────────────────────────────────────
    if status == "OPEN" or is_us_swing():
        for sym in SMC_TICKERS:
            try:
                tag = next((t for t, lst in TICKERS.items()
                            if isinstance(lst[0], str) and sym in lst), "🇺🇸")
                if not passes_trend_template(sym): continue
                df15 = get_df15(sym)
                df1d = get_yf_1d(sym)
                if df15.empty or df1d.empty: continue
                res = signal_smc(sym, tag, df15, df1d)
                if res: smc_sigs.extend(res)
            except Exception as e:
                print(f"  SMC {sym}: {e}")

    # ── 美股波段（收盤前30分）────────────────────────────────────────────────
    if is_us_swing():
        for tag in ["🇺🇸", "🛡️", "🚀"]:
            for sym in TICKERS.get(tag, []):
                try:
                    if not passes_trend_template(sym): continue
                    df1d = get_yf_1d(sym)
                    df5  = get_df5(sym)
                    if not df1d.empty and not df5.empty:
                        r = signal_pullback(sym, tag, df1d, df5)
                        if r: swing_sigs.append(r)
                except Exception as e:
                    print(f"  SWING {sym}: {e}")

    # ── 台股 ─────────────────────────────────────────────────────────────────
    if is_tw_open():
        for sym in TICKERS["🇹🇼"]:
            try:
                df5  = get_yf_5m(sym); df15 = get_yf_15m(sym)
                if df5.empty: continue
                for fn in [signal_breakout_pre,
                           lambda s, t, d5, d15: signal_washout(s, t, d5, d15, "OPEN")]:
                    r = fn(sym, "🇹🇼", df5, df15)
                    if r: intraday_sigs.append(r)
            except Exception as e:
                print(f"  TW {sym}: {e}")

    if is_tw_swing():
        for sym in TICKERS["🇹🇼"]:
            try:
                df1d = get_yf_1d(sym); df5 = get_yf_5m(sym)
                if not df1d.empty and not df5.empty:
                    r = signal_pullback(sym, "🇹🇼", df1d, df5)
                    if r: swing_sigs.append(r)
            except Exception as e:
                print(f"  TW SWING {sym}: {e}")

    # ── 加密：半木夏（24/7）──────────────────────────────────────────────────
    for yf_sym, disp in TICKERS["₿"]:
        try:
            df15 = get_yf_15m(yf_sym)
            if df15.empty: continue
            res = signal_banmuxa(yf_sym, disp, df15)
            if res: crypto_sigs.extend(res)
        except Exception as e:
            print(f"  ₿ {disp}: {e}")

    # ── 排序 + 發送 ──────────────────────────────────────────────────────────
    for lst in [intraday_sigs, swing_sigs, smc_sigs, crypto_sigs]:
        lst.sort(key=lambda x: x["score"], reverse=True)

    if intraday_sigs:
        send_tg(f"━━━ ⚡ 日內信號 · {len(intraday_sigs)}個 ━━━")
        for s in intraday_sigs: send_tg(s["msg"])

    if smc_sigs:
        send_tg(f"━━━ 🏦 SMC信號 · {len(smc_sigs)}個 ━━━")
        for s in smc_sigs: send_tg(s["msg"])

    if swing_sigs:
        send_tg(f"━━━ 📈 波段信號 · {len(swing_sigs)}個 ━━━")
        for s in swing_sigs: send_tg(s["msg"])

    if crypto_sigs:
        send_tg(f"━━━ ₿ 半木夏三背離 · {len(crypto_sigs)}個 ━━━")
        for s in crypto_sigs: send_tg(s["msg"])

    if not any([intraday_sigs, smc_sigs, swing_sigs, crypto_sigs]):
        print("本次無 S/A 級信號")

    print("掃描結束\n")


if __name__ == "__main__":
    main()
