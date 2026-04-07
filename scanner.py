“””
CC Market Scanner v7.1
數據源：Alpaca API（美股即時）/ yfinance（台股、加密、日線）
標籤：🇺🇸權值 | 🛡️資安 | ⚛️核能 | 🚀妖股 | 🇨🇳中概 | 🇹🇼台股 | ₿加密
策略：⛈️暴跌預兆 | 🔮暴漲預兆 | 🔥強勢突破 | ⚡WASHOUT | 📈波段PULLBACK
等級：S級/A級（B級已移除）
“””

import requests
import pandas as pd
import ta
import yfinance as yf
import os
from datetime import datetime, date
import pytz

# ── Token ─────────────────────────────────────────────────────────────────────

TG_TOKEN      = os.environ.get(“TG_TOKEN”,      “”)
TG_CHAT_ID    = os.environ.get(“TG_CHAT_ID”,    “”)
ALPACA_KEY    = os.environ.get(“ALPACA_KEY”,    “”)
ALPACA_SECRET = os.environ.get(“ALPACA_SECRET”, “”)
ALPACA_BASE   = “https://data.alpaca.markets/v2”

# ── 監控名單 ──────────────────────────────────────────────────────────────────

TICKERS = {
“🇺🇸”: [“NVDA”,“AVGO”,“ANET”,“VRT”,“VST”,“TSLA”,“AMD”,“AMZN”,“AAPL”,“META”,“MSFT”,“GOOGL”,“PLTR”,“CRDO”,“ALAB”],
“🛡️”: [“PANW”,“FTNT”,“CRWD”],
“⚛️”: [“SMR”,“OKLO”,“NNE”],
“🚀”: [“COIN”,“MSTR”,“MARA”,“CLSK”,“HOOD”,“SOFI”,“APLD”,“IONQ”,“RGTI”,“NVTS”,“AAOI”,“RCAT”,"ONDS","TQQQ","NVDL","AMDL"],
# 移除：ONDS（流動性不足）、PATH（陷阱股）、PL（流動性差）
“🇨🇳”: [“BABA”,“PDD”,“FUTU”],
“🇹🇼”: [“2330.TW”,“00631L.TW”],
“₿”:   [(“BTC-USD”,“BTC/USDT”),(“ETH-BTC”,“ETH/BTC”)],
}

# ── 假日清單 ──────────────────────────────────────────────────────────────────

US_HOLIDAYS = {
“2025-01-01”,“2025-01-20”,“2025-02-17”,“2025-04-18”,
“2025-05-26”,“2025-06-19”,“2025-07-04”,“2025-09-01”,
“2025-11-27”,“2025-12-25”,
“2026-01-01”,“2026-01-19”,“2026-02-16”,“2026-04-03”,
“2026-04-04”,“2026-05-25”,“2026-06-19”,“2026-07-03”,
“2026-09-07”,“2026-11-26”,“2026-12-25”,
}
TW_HOLIDAYS = {
“2026-01-01”,“2026-01-27”,“2026-01-28”,“2026-01-29”,“2026-01-30”,
“2026-02-28”,“2026-04-04”,“2026-04-05”,“2026-05-01”,
“2026-06-19”,“2026-09-26”,“2026-10-09”,“2026-10-10”,
}

# ── 時間判斷 ──────────────────────────────────────────────────────────────────

def _now_ny():
return datetime.now(pytz.timezone(“America/New_York”))

def _now_tw():
return datetime.now(pytz.timezone(“Asia/Taipei”))

def us_market_status():
ny = _now_ny()
d  = ny.strftime(”%Y-%m-%d”)
if ny.weekday() >= 5 or d in US_HOLIDAYS:
return “CLOSED”
m = ny.hour * 60 + ny.minute
if 240 <= m < 570:  return “PRE”
if 570 <= m < 930:  return “OPEN”
if 930 <= m < 1200: return “POST”
return “CLOSED”

def is_tw_open():
tw = _now_tw()
d  = tw.strftime(”%Y-%m-%d”)
if tw.weekday() >= 5 or d in TW_HOLIDAYS: return False
m = tw.hour * 60 + tw.minute
return 540 <= m < 810

def is_tw_swing():
tw = _now_tw()
d  = tw.strftime(”%Y-%m-%d”)
if tw.weekday() >= 5 or d in TW_HOLIDAYS: return False
m = tw.hour * 60 + tw.minute
return 780 <= m < 810

def is_us_swing():
ny = _now_ny()
d  = ny.strftime(”%Y-%m-%d”)
if ny.weekday() >= 5 or d in US_HOLIDAYS: return False
m = ny.hour * 60 + ny.minute
return 900 <= m < 930

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_tg(msg):
if not TG_TOKEN or not TG_CHAT_ID:
print(msg); return False
try:
r = requests.post(
f”https://api.telegram.org/bot{TG_TOKEN}/sendMessage”,
data={“chat_id”: TG_CHAT_ID, “text”: msg, “parse_mode”: “Markdown”},
timeout=10
)
return r.json().get(“ok”, False)
except: return False

def tw_time():
return _now_tw().strftime(”%H:%M:%S”)

def L(v): return “✅” if v else “❌”

def grade(score, total):
pct = score / total
if pct >= 0.85: return “🏆 S級”
if pct >= 0.70: return “🥇 A級”
return None  # B級不發送

# ── Alpaca 即時數據（美股）────────────────────────────────────────────────────

def get_alpaca_bars(symbol, timeframe=“5Min”, limit=80):
“””
Alpaca免費帳戶可取得即時美股數據
timeframe: 1Min / 5Min / 15Min / 1Hour / 1Day
“””
if not ALPACA_KEY or not ALPACA_SECRET:
return pd.DataFrame()
try:
url = f”{ALPACA_BASE}/stocks/{symbol}/bars”
params = {
“timeframe”: timeframe,
“limit”: limit,
“adjustment”: “raw”,
“feed”: “iex”,       # IEX feed：免費即時
}
headers = {
“APCA-API-KEY-ID”:     ALPACA_KEY,
“APCA-API-SECRET-KEY”: ALPACA_SECRET,
}
r = requests.get(url, params=params, headers=headers, timeout=10)
if r.status_code != 200: return pd.DataFrame()
bars = r.json().get(“bars”, [])
if not bars: return pd.DataFrame()
df = pd.DataFrame(bars)
df[“t”] = pd.to_datetime(df[“t”])
df = df.set_index(“t”)
df = df.rename(columns={“o”:“Open”,“h”:“High”,“l”:“Low”,“c”:“Close”,“v”:“Volume”})
return df[[“Open”,“High”,“Low”,“Close”,“Volume”]].dropna()
except Exception as e:
print(f”  Alpaca error {symbol}: {e}”)
return pd.DataFrame()

def get_alpaca_5m(sym):  return get_alpaca_bars(sym, “5Min”,  80)
def get_alpaca_15m(sym): return get_alpaca_bars(sym, “15Min”, 40)

# ── yfinance（台股、加密、日線）──────────────────────────────────────────────

def _clean(df):
if isinstance(df.columns, pd.MultiIndex):
df.columns = df.columns.get_level_values(0)
return df.dropna()

def get_yf_5m(s):  return _clean(yf.download(s, interval=“5m”,  period=“2d”,   progress=False, auto_adjust=True))
def get_yf_15m(s): return _clean(yf.download(s, interval=“15m”, period=“5d”,   progress=False, auto_adjust=True))
def get_yf_1d(s):  return _clean(yf.download(s, interval=“1d”,  period=“100d”, progress=False, auto_adjust=True))
def get_yf_1h(s):  return _clean(yf.download(s, interval=“1h”,  period=“60d”,  progress=False, auto_adjust=True))

# ── 指標 ──────────────────────────────────────────────────────────────────────

def add_rsi(df, n=14):
df = df.copy()
df[“RSI”] = ta.momentum.RSIIndicator(df[“Close”], window=n).rsi()
return df

def add_sma(df, col, n, out):
df = df.copy()
df[out] = ta.trend.SMAIndicator(df[col], window=n).sma_indicator()
return df

def add_macd(df):
df = df.copy()
m = ta.trend.MACD(df[“Close”])
df[“MACD”]      = m.macd()
df[“MACD_sig”]  = m.macd_signal()
df[“MACD_hist”] = m.macd_diff()
return df

# ══════════════════════════════════════════════════════════════════════════════

# ⛈️ 暴跌預兆：高位爆量滯漲 / 支撐潰散

# ══════════════════════════════════════════════════════════════════════════════

def signal_crash_warning(sym, tag, df5):
if len(df5) < 20: return None
df = add_rsi(add_sma(df5, “Volume”, 15, “VMA”))
curr = df.iloc[-1]; prev = df.iloc[-2]
if pd.isna(curr[“VMA”]): return None

```
vr          = curr["Volume"] / (curr["VMA"] + 1)
recent_hi   = df["High"].tail(20).max()
support_5   = df["Low"].tail(5).min()
is_high_pos = curr["Close"] > recent_hi * 0.94   # 處於近期高位

# Bug修正：support 用前5根低點，不包含當根
c1 = is_high_pos and vr > 3.5 and curr["Close"] < curr["Open"]  # 高位爆量收黑
c2 = curr["Close"] < df["Low"].tail(6).iloc[:-1].min()           # 跌破5日支撐 ★修正

score = sum([c1, c2])
if score == 0: return None
g = grade(score, 2)
if not g: return None

return {
    "score": score + 10,  # 風險警告優先級最高
    "msg": (
        f"{tag} ⛈️ *[暴跌預兆]* `{sym}` {g}\n"
        f"💰 現價: `{curr['Close']:.2f}`\n"
        f"燈號: {L(c1)}高位爆量收黑 {L(c2)}跌破5日支撐\n"
        f"📊 量比: `{vr:.1f}x` · RSI: `{curr['RSI']:.0f}`\n"
        f"🚨 建議立刻減倉，切勿留過夜\n⏰ {tw_time()} TWN"
    )
}
```

# ══════════════════════════════════════════════════════════════════════════════

# 🔮 暴漲預兆：縮量蓄勢突破前高（Bug修正版）

# ══════════════════════════════════════════════════════════════════════════════

def signal_breakout_pre(sym, tag, df5, df15):
if len(df5) < 20 or len(df15) < 5: return None
df = add_rsi(add_sma(df5, “Volume”, 15, “VMA”))
curr = df.iloc[-1]; prev = df.iloc[-2]
if pd.isna(curr[“VMA”]): return None

```
vr = curr["Volume"] / (curr["VMA"] + 1)
# Bug修正：用前5根高點最大值（排除當根）
prev_hi = df["High"].tail(7).iloc[:-1].max()
c15     = df15.iloc[-1]

c1 = vr > 2.5                               # 量能放大
c2 = curr["Close"] > prev_hi                # 突破前高 ★修正
c3 = prev["Close"] <= prev_hi               # 剛突破（非已久）
c4 = curr["RSI"] > 55 and curr["RSI"] < 78  # 動能強但非超買
c5 = c15["Close"] > prev_hi                 # 15m確認

score = sum([c1,c2,c3,c4,c5])
g = grade(score, 5)
if not g or not (c2 and c3): return None

return {
    "score": score,
    "msg": (
        f"{tag} 🔮 *[暴漲預兆]* `{sym}` {g}\n"
        f"💰 現價: `{curr['Close']:.2f}` · 突破: `{prev_hi:.2f}`\n"
        f"燈號: {L(c1)}量2.5x {L(c2)}突破前高 {L(c3)}剛發動 {L(c4)}RSI動能 {L(c5)}15m確認\n"
        f"📊 量比: `{vr:.1f}x` · RSI: `{curr['RSI']:.0f}` · 條件: `{score}/5`\n"
        f"💡 現股建倉或 Buy Call\n⏰ {tw_time()} TWN"
    )
}
```

# ══════════════════════════════════════════════════════════════════════════════

# ⚡ WASHOUT：殺低反彈（門檻提高到1.5%，避免雜訊）

# ══════════════════════════════════════════════════════════════════════════════

def signal_washout(sym, tag, df5, df15, status):
if len(df5) < 6 or len(df15) < 3: return None
df = add_rsi(add_sma(add_sma(df5, “Volume”, 10, “VMA10”), “Close”, 5, “MA5”))
df = add_sma(df, “Close”, 20, “MA20”)
curr = df.iloc[-1]; prev = df.iloc[-2]; prev2 = df.iloc[-3]
if pd.isna(curr[“MA5”]) or pd.isna(curr[“MA20”]): return None

```
c15      = df15.iloc[-1]
day_open = df.iloc[0]["Open"]
day_low  = df["Low"].min()
yest     = df[df.index.date < df.index[-1].date()]
yest_low = yest["Low"].min() if not yest.empty else day_low * 0.97
drop     = (day_open - day_low) / day_open * 100
rebound  = (curr["Close"] - day_low) / (day_open - day_low + 0.001)
vr       = curr["Volume"] / (curr["VMA10"] + 1)

# 妖股門檻更高，避免假訊號
min_drop = 1.5 if "🚀" not in tag else 2.0

c1 = drop > min_drop                                  # 殺低夠深
c2 = curr["Close"] >= day_open * 0.998                # 站回開盤
c3 = prev["Close"] < day_open                         # 剛站回
c4 = curr["RSI"] > prev["RSI"] > prev2["RSI"]         # RSI連升
c5 = curr["RSI"] < 72                                 # 非追高
c6 = curr["Close"] > yest_low                         # 守昨低
c7 = rebound > 0.5                                    # 反彈力道
c8 = curr["MA5"] > curr["MA20"]                       # MA翻多

score = sum([c1,c2,c3,c4,c5,c6,c7,c8])
g = grade(score, 8)
if not g or not c1 or not c2: return None

prefix = "🌅 [盤前洗盤]" if status == "PRE" else "⚡ [WASHOUT]"
warn   = f"\n⚠️ RSI `{curr['RSI']:.0f}` 偏高，等回測5MA再加碼" if curr["RSI"] > 65 else ""
target = curr["Close"] * 1.02
stop   = yest_low

return {
    "score": score,
    "msg": (
        f"{tag} {prefix} `{sym}` {g}\n"
        f"💰 現價: `{curr['Close']:.2f}` · 殺低: `{drop:.1f}%` · 反彈: `{rebound*100:.0f}%`\n"
        f"燈號: {L(c1)}殺低 {L(c2)}站回 {L(c3)}剛翻 {L(c4)}RSI勾 "
        f"{L(c5)}非追高 {L(c6)}守昨低 {L(c7)}彈力 {L(c8)}MA翻多\n"
        f"📊 RSI: `{curr['RSI']:.0f}` · 量比: `{vr:.1f}x` · 條件: `{score}/8`{warn}\n"
        f"🎯 目標: `{target:.2f}` (+2%) · 止損: `{stop:.2f}`\n"
        f"👉 TradingView 5m 確認\n⏰ {tw_time()} TWN"
    )
}
```

# ══════════════════════════════════════════════════════════════════════════════

# 📈 波段PULLBACK：縮量回測季線（最高勝率）

# ══════════════════════════════════════════════════════════════════════════════

def signal_pullback(sym, tag, df1d, df5):
if len(df1d) < 65 or len(df5) < 3: return None
d = add_macd(add_rsi(add_sma(add_sma(df1d, “Close”, 60, “MA60”), “Volume”, 5, “V5”)))
f = add_rsi(df5)

```
d_c=d.iloc[-1]; d_p=d.iloc[-2]; f_c=f.iloc[-1]; f_p=f.iloc[-2]
if pd.isna(d_c["MA60"]) or pd.isna(d_c["RSI"]): return None

bias60 = (d_c["Close"] - d_c["MA60"]) / d_c["MA60"] * 100
vr     = d_c["Volume"] / (d_c["V5"] + 1)

c1 = d_c["Close"] > d_c["MA60"]
c2 = 42 <= d_p["RSI"] <= 58 and d_c["RSI"] > d_p["RSI"]
c3 = vr < 0.85
c4 = 0 <= bias60 < 5
c5 = f_c["RSI"] > f_p["RSI"]
c6 = d_c["MACD_hist"] > d_p["MACD_hist"]

score = sum([c1,c2,c3,c4,c5,c6])
g = grade(score, 6)
if not g or not c1 or not c2: return None

sp = d_c["Close"] * 0.95
return {
    "score": score,
    "msg": (
        f"{tag} 📈 *[波段PULLBACK]* `{sym}` {g}\n"
        f"💰 現價: `{d_c['Close']:.2f}` · 距季線: `{bias60:.1f}%`\n"
        f"燈號: {L(c1)}季線上 {L(c2)}RSI勾頭 {L(c3)}縮量 "
        f"{L(c4)}貼季線 {L(c5)}5m確認 {L(c6)}MACD放大\n"
        f"📊 日RSI: `{d_c['RSI']:.0f}` · 量能: `{vr:.2f}x` · 條件: `{score}/6`\n"
        f"💡 Sell Put 行權價: `{sp:.1f}` (-5%) · 守季線\n⏰ {tw_time()} TWN"
    )
}
```

# ══════════════════════════════════════════════════════════════════════════════

# ₿ 加密（yfinance 1h，Binance數據延遲小）

# ══════════════════════════════════════════════════════════════════════════════

def signal_crypto(yf_sym, disp, df1h):
if len(df1h) < 60: return None
df = add_macd(add_rsi(df1h.copy()))
df[“EMA50”]  = ta.trend.EMAIndicator(df[“Close”], window=50).ema_indicator()
df[“V_MA20”] = ta.trend.SMAIndicator(df[“Volume”], window=20).sma_indicator()

```
curr=df.iloc[-1]; prev=df.iloc[-2]
if pd.isna(curr["RSI"]) or pd.isna(curr["MACD"]): return None

price = curr["Close"]
ema50 = curr["EMA50"]
vr    = curr["Volume"] / (curr["V_MA20"] + 1)
r     = curr["RSI"]

results = []

# MACD翻零軸（最強信號，直接S級）
if curr["MACD"] > 0 and prev["MACD"] <= 0 and price > ema50:
    results.append({"score": 10, "msg": (
        f"₿ 🔥 *[加密 MACD翻零軸]* `{disp}` 🏆 S級\n"
        f"💰 現價: `{price:.4f}`\n"
        f"📊 MACD: `{prev['MACD']:.4f}` → `{curr['MACD']:.4f}` 翻正\n"
        f"💡 中線做多強信號\n⏰ {tw_time()} TWN"
    )})

# PULLBACK
cp1=price>ema50; cp2=42<=prev["RSI"]<=58 and r>prev["RSI"]
cp3=curr["MACD"]>0; cp4=vr<0.85; cp5=curr["MACD_hist"]>prev["MACD_hist"]
sc = sum([cp1,cp2,cp3,cp4,cp5])
g  = grade(sc, 5)
if g and cp1 and cp2:
    bias = (price-ema50)/ema50*100
    results.append({"score": sc, "msg": (
        f"₿ 📈 *[加密 PULLBACK]* `{disp}` {g}\n"
        f"💰 現價: `{price:.4f}` · 距EMA50: `{bias:.1f}%`\n"
        f"燈號: {L(cp1)}EMA50上 {L(cp2)}RSI勾頭 {L(cp3)}MACD>0 {L(cp4)}縮量 {L(cp5)}柱放大\n"
        f"📊 RSI: `{r:.0f}` · 條件: `{sc}/5`\n"
        f"💡 做多 · 止損EMA50\n⏰ {tw_time()} TWN"
    )})

return results if results else None
```

# ══════════════════════════════════════════════════════════════════════════════

# 發送模式判斷

# ══════════════════════════════════════════════════════════════════════════════

def get_send_mode():
“””
三種模式：
DIGEST_PRE   → 開盤前1小時（台灣 20:30-21:30）：發彙整報告
URGENT_ONLY  → 開盤中（台灣 21:30-04:00）：只發⛈️暴跌預兆（S級緊急）
SILENT       → 其他時間：只掃描不發送
“””
tw = datetime.now(pytz.timezone(“Asia/Taipei”))
m  = tw.hour * 60 + tw.minute
us = us_market_status()

```
# 開盤前1小時：台灣時間 20:30-21:30（美股 21:30 開盤）
if 1230 <= m < 1290:   # 20:30-21:30
    return "DIGEST_PRE"

# 美股開盤中
if us in ("PRE", "OPEN"):
    return "URGENT_ONLY"

# 台股開盤前1小時：台灣時間 08:00-09:00
if is_tw_open() or (540 - 60 <= m < 540):
    return "DIGEST_TW" if (480 <= m < 540) else "URGENT_ONLY"

return "SILENT"
```

def format_digest(intraday, swing, crypto, label=“開盤前彙整”):
“”“把所有信號壓縮成一則彙整訊息”””
tw_now = datetime.now(pytz.timezone(“Asia/Taipei”))
lines  = [f”📋 *CC Scanner · {label}*”,
f”⏰ {tw_now.strftime(’%m/%d %H:%M’)} TWN”,
“”]

```
if intraday:
    lines.append(f"⚡ *日內候選（{len(intraday)}個，依評分排序）*")
    for s in intraday[:8]:   # 最多顯示8個
        # 從 msg 提取第一行摘要
        first = s["msg"].split("\n")[0].replace("*","").replace("`","")
        score_line = next((l for l in s["msg"].split("\n") if "條件" in l or "S級" in l or "A級" in l), "")
        lines.append(f"• {first}")
    lines.append("")

if swing:
    lines.append(f"📈 *波段候選（{len(swing)}個）*")
    for s in swing[:5]:
        first = s["msg"].split("\n")[0].replace("*","").replace("`","")
        lines.append(f"• {first}")
    lines.append("")

if crypto:
    lines.append(f"₿ *加密信號（{len(crypto)}個）*")
    for s in crypto[:3]:
        first = s["msg"].split("\n")[0].replace("*","").replace("`","")
        lines.append(f"• {first}")
    lines.append("")

if not intraday and not swing and not crypto:
    lines.append("本次無 S/A 級信號")

lines.append("━━━━━━━━━━━━━━━━━━━━━")
lines.append("開盤後僅發 ⛈️ 緊急警示")
return "\n".join(lines)
```

# ══════════════════════════════════════════════════════════════════════════════

# 主程式

# ══════════════════════════════════════════════════════════════════════════════

def main():
mode   = get_send_mode()
status = us_market_status()
tw_now = datetime.now(pytz.timezone(“Asia/Taipei”))
print(f”\n{’=’*50}”)
print(f”CC Scanner v7.1 · {tw_now.strftime(’%Y-%m-%d %H:%M’)} TWN”)
print(f”美股:{status} | 模式:{mode}”)
print(f”{’=’*50}”)

```
# SILENT 模式：直接跳過，不掃描
if mode == "SILENT":
    print("靜默模式，跳過掃描")
    return

intraday_sigs = []
swing_sigs    = []
crypto_sigs   = []

# ── 掃描美股 ─────────────────────────────────────────────────────────────
if status in ("PRE","OPEN") or mode in ("DIGEST_PRE",):
    us_tags = ["🇺🇸","🛡️","⚛️","🚀","🇨🇳"]
    for tag in us_tags:
        for sym in TICKERS.get(tag, []):
            try:
                df5  = get_alpaca_5m(sym)
                df15 = get_alpaca_15m(sym)
                if df5.empty:
                    df5  = get_yf_5m(sym)
                    df15 = get_yf_15m(sym)
                if df5.empty: continue

                r = signal_crash_warning(sym, tag, df5)
                if r: intraday_sigs.append(r)

                if mode != "URGENT_ONLY":
                    r2 = signal_breakout_pre(sym, tag, df5, df15)
                    if r2: intraday_sigs.append(r2)
                    r3 = signal_washout(sym, tag, df5, df15, status)
                    if r3: intraday_sigs.append(r3)
            except Exception as e:
                print(f"  {sym}: {e}")

# ── 掃描波段（只在彙整模式或收盤前）────────────────────────────────────
if mode in ("DIGEST_PRE","DIGEST_TW") or is_us_swing():
    for tag in ["🇺🇸","🛡️","🚀"]:
        for sym in TICKERS.get(tag, []):
            try:
                df1d = get_yf_1d(sym)
                df5  = get_alpaca_5m(sym)
                if df5.empty: df5 = get_yf_5m(sym)
                if not df1d.empty and not df5.empty:
                    r = signal_pullback(sym, tag, df1d, df5)
                    if r: swing_sigs.append(r)
            except Exception as e:
                print(f"  {sym}: {e}")

# ── 掃描台股 ─────────────────────────────────────────────────────────────
if is_tw_open() or mode == "DIGEST_TW":
    for sym in TICKERS["🇹🇼"]:
        try:
            df5  = get_yf_5m(sym)
            df15 = get_yf_15m(sym)
            if df5.empty: continue
            if mode != "URGENT_ONLY":
                r = signal_breakout_pre(sym, "🇹🇼", df5, df15)
                if r: intraday_sigs.append(r)
                r2 = signal_washout(sym, "🇹🇼", df5, df15, "OPEN")
                if r2: intraday_sigs.append(r2)
        except Exception as e:
            print(f"  {sym}: {e}")

# ── 掃描加密（彙整或有信號才發）────────────────────────────────────────
for yf_sym, disp in TICKERS["₿"]:
    try:
        df1h = get_yf_1h(yf_sym)
        if df1h.empty: continue
        results = signal_crypto(yf_sym, disp, df1h)
        if results: crypto_sigs.extend(results)
    except Exception as e:
        print(f"  {disp}: {e}")

# ── 排序 ─────────────────────────────────────────────────────────────────
intraday_sigs.sort(key=lambda x: x["score"], reverse=True)
swing_sigs.sort(key=lambda x:    x["score"], reverse=True)
crypto_sigs.sort(key=lambda x:   x["score"], reverse=True)

# ── 發送（依模式決定格式）────────────────────────────────────────────────
if mode in ("DIGEST_PRE", "DIGEST_TW"):
    # 彙整模式：一則訊息搞定
    label = "美股開盤前彙整" if mode == "DIGEST_PRE" else "台股開盤前彙整"
    msg   = format_digest(intraday_sigs, swing_sigs, crypto_sigs, label)
    send_tg(msg)
    # 彙整後，S級個別再發一次（讓你快速確認最重要的）
    top = [s for s in intraday_sigs if "🏆" in s["msg"]][:3]
    for s in top:
        send_tg(s["msg"])

elif mode == "URGENT_ONLY":
    # 開盤中：只發⛈️暴跌預兆（已在掃描中優先處理）
    crash = [s for s in intraday_sigs if "⛈️" in s["msg"]]
    if crash:
        send_tg(f"━━━ ⛈️ 緊急警示 · {len(crash)}個 ━━━")
        for s in crash:
            send_tg(s["msg"])
    else:
        print("開盤中：無緊急警示")

total = len(intraday_sigs) + len(swing_sigs) + len(crypto_sigs)
print(f"掃描完成：{total}個信號 · 模式:{mode}\n")
```

if **name** == “**main**”:
main()
