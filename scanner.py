“””
CC Market Scanner — 最終版 v4
標籤：🇺🇸日內 / 🇺🇸波段 / 🇹🇼日內 / 🇹🇼波段 / ₿加密
成功率：PULLBACK > ORB > 加密PULLBACK > WASHOUT > 加密ORB
“””

import yfinance as yf
import ta
import requests
import pandas as pd
import os
from datetime import datetime
import pytz

# ── Token ─────────────────────────────────────────────────────────────────────

TG_TOKEN   = os.environ.get(“TG_TOKEN”,   “”)
TG_CHAT_ID = os.environ.get(“TG_CHAT_ID”, “”)

# 本地測試取消 # 填入：

# TG_TOKEN   = “你的token”

# TG_CHAT_ID = “你的chatid”

# ── 監控名單 ──────────────────────────────────────────────────────────────────

US_TICKERS   = [
“NVDA”,“TSLA”,“AMD”,“AAPL”,“META”,
“PLTR”,“SOFI”,“COIN”,
“F”,“BAC”,“T”,“SNAP”,“PATH”,“DOCU”,
“XLE”,“GLD”,“TLT”,“AXTI”,
]
TW_TICKERS   = [“2330.TW”, “00631L.TW”]   # 台積電 + 台灣50正2
CRYPTO_PAIRS = [(“BTC-USD”,“BTC/USDT”), (“ETH-BTC”,“ETH/BTC”)]  # (yf symbol, display name)

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_tg(msg):
if not TG_TOKEN or not TG_CHAT_ID:
print(f”[TG未設定] {msg[:80]}”)
return False
try:
r = requests.post(
f”https://api.telegram.org/bot{TG_TOKEN}/sendMessage”,
data={“chat_id”: TG_CHAT_ID, “text”: msg, “parse_mode”: “Markdown”},
timeout=10
)
return r.json().get(“ok”, False)
except Exception as e:
print(f”TG 失敗: {e}”)
return False

def tw_time():
return datetime.now(pytz.timezone(“Asia/Taipei”)).strftime(”%H:%M:%S”)

def L(v): return “✅” if v else “❌”   # 燈號

# ── 時間判斷 ──────────────────────────────────────────────────────────────────

def us_mins():
ny = datetime.now(pytz.timezone(“America/New_York”))
return -1 if ny.weekday() >= 5 else ny.hour * 60 + ny.minute

def tw_mins():
tw = datetime.now(pytz.timezone(“Asia/Taipei”))
return -1 if tw.weekday() >= 5 else tw.hour * 60 + tw.minute

def is_us_open():   return 570 <= us_mins() < 930
def is_us_swing():  return 900 <= us_mins() < 930
def is_tw_open():   return 540 <= tw_mins() < 810
def is_tw_swing():  return 780 <= tw_mins() < 810

# 加密貨幣 24/7，不需要時間判斷

# ── 數據獲取 ──────────────────────────────────────────────────────────────────

def _clean(df):
if isinstance(df.columns, pd.MultiIndex):
df.columns = df.columns.get_level_values(0)
return df.dropna()

def get_5m(s):  return _clean(yf.download(s, interval=“5m”,  period=“2d”,   progress=False, auto_adjust=True))
def get_15m(s): return _clean(yf.download(s, interval=“15m”, period=“5d”,   progress=False, auto_adjust=True))
def get_1d(s):  return _clean(yf.download(s, interval=“1d”,  period=“100d”, progress=False, auto_adjust=True))
def get_1h(s):  return _clean(yf.download(s, interval=“1h”,  period=“60d”,  progress=False, auto_adjust=True))

# ── 指標計算 ──────────────────────────────────────────────────────────────────

def add_rsi(df, col=“Close”, n=14):
df = df.copy()
df[“RSI”] = ta.momentum.RSIIndicator(df[col], window=n).rsi()
return df

def add_sma(df, col, n, out):
df = df.copy()
df[out] = ta.trend.SMAIndicator(df[col], window=n).sma_indicator()
return df

def add_macd(df, col=“Close”):
df = df.copy()
m = ta.trend.MACD(df[col])
df[“MACD”]      = m.macd()
df[“MACD_sig”]  = m.macd_signal()
df[“MACD_hist”] = m.macd_diff()
return df

def add_bb(df, col=“Close”, n=20):
df = df.copy()
b = ta.volatility.BollingerBands(df[col], window=n)
df[“BB_upper”] = b.bollinger_hband()
df[“BB_lower”] = b.bollinger_lband()
df[“BB_mid”]   = b.bollinger_mavg()
return df

# ══════════════════════════════════════════════════════════════════════════════

# 策略A：WASHOUT 殺低反彈（5m，5/6 燈）

# ══════════════════════════════════════════════════════════════════════════════

def strategy_washout(sym, df_5m, label):
if len(df_5m) < 4: return
df   = add_rsi(df_5m)
curr = df.iloc[-1]; prev = df.iloc[-2]; prev2 = df.iloc[-3]

```
day_open = df.iloc[0]["Open"]
day_low  = df["Low"].min()
yest     = df[df.index.date < df.index[-1].date()]
yest_low = yest["Low"].min() if not yest.empty else day_low * 0.97
drop     = (day_open - day_low) / day_open * 100

c1 = drop > 1.5
c2 = curr["Close"] >= day_open
c3 = prev["Close"] < day_open
c4 = curr["RSI"] > prev["RSI"] > prev2["RSI"]
c5 = curr["RSI"] < 70
c6 = curr["Close"] > yest_low
score = sum([c1,c2,c3,c4,c5,c6])

if score >= 5 and c1 and c2:
    warn = f"\n⚠️ RSI `{curr['RSI']:.0f}` 偏高，等5m回測再進" if curr["RSI"] > 65 else ""
    send_tg(
        f"⚡ *[WASHOUT 殺低反彈]* `{sym}` · {label}\n"
        f"💰 現價: `{curr['Close']:.2f}` · 殺低: `{drop:.1f}%`\n"
        f"燈號: {L(c1)}殺低 {L(c2)}站回 {L(c3)}剛翻 {L(c4)}RSI勾 {L(c5)}非追高 {L(c6)}未破昨低\n"
        f"📊 RSI: `{curr['RSI']:.0f}` · 條件: `{score}/6`{warn}\n"
        f"👉 TradingView 5m 確認進場\n⏰ {tw_time()} TWN"
    )
```

# ══════════════════════════════════════════════════════════════════════════════

# 策略B：ORB 開盤區間突破（5m + 15m，4/4 全中）

# ══════════════════════════════════════════════════════════════════════════════

def strategy_orb(sym, df_5m, df_15m, label):
if len(df_5m) < 6 or len(df_15m) < 3: return
df5    = add_rsi(add_sma(df_5m, “Volume”, 10, “V_MA10”))
curr5  = df5.iloc[-1]; prev5 = df5.iloc[-2]
curr15 = df_15m.iloc[-1]

```
hi15 = df5.iloc[0:3]["High"].max()
lo15 = df5.iloc[0:3]["Low"].min()
vr   = curr5["Volume"] / (curr5["V_MA10"] + 1)
rsi  = curr5["RSI"]

# 多頭突破
c1=curr5["Close"]>hi15; c2=prev5["Close"]<=hi15; c3=vr>=2.0; c4=curr15["Close"]>hi15
if all([c1,c2,c3,c4]):
    send_tg(
        f"🚀 *[ORB 多頭突破]* `{sym}` · {label}\n"
        f"💰 現價: `{curr5['Close']:.2f}` · 突破: `{hi15:.2f}`\n"
        f"燈號: {L(c1)}突破 {L(c2)}剛發動 {L(c3)}量2x {L(c4)}15m確認\n"
        f"📊 量比: `{vr:.1f}x` · RSI: `{rsi:.0f}`\n"
        f"👉 TradingView 2m 看進場\n⏰ {tw_time()} TWN"
    )
    return

# 空頭跌破 → Sell Call 參考
c1s=curr5["Close"]<lo15; c2s=prev5["Close"]>=lo15; c4s=curr15["Close"]<lo15
if all([c1s,c2s,c3,c4s]):
    send_tg(
        f"🔻 *[ORB 空頭跌破]* Sell Call 參考: `{sym}` · {label}\n"
        f"💰 現價: `{curr5['Close']:.2f}` · 跌破: `{lo15:.2f}`\n"
        f"燈號: {L(c1s)}跌破 {L(c2s)}剛發動 {L(c3)}量2x {L(c4s)}15m確認\n"
        f"📊 量比: `{vr:.1f}x` · RSI: `{rsi:.0f}`\n"
        f"⏰ {tw_time()} TWN"
    )
```

# ══════════════════════════════════════════════════════════════════════════════

# 策略C：PULLBACK 波段縮量回測（1d + 5m，5/5 全中）★成功率最高

# ══════════════════════════════════════════════════════════════════════════════

def strategy_pullback(sym, df_1d, df_5m, label):
if len(df_1d) < 65 or len(df_5m) < 3: return
d = add_rsi(add_sma(add_sma(df_1d, “Close”, 60, “MA60”), “Volume”, 5, “V_Avg5”))
f = add_rsi(df_5m)

```
d_c=d.iloc[-1]; d_p=d.iloc[-2]; f_c=f.iloc[-1]; f_p=f.iloc[-2]
if pd.isna(d_c["MA60"]) or pd.isna(d_c["RSI"]): return

bias = (d_c["Close"] - d_c["MA60"]) / d_c["MA60"] * 100
vr   = d_c["Volume"] / (d_c["V_Avg5"] + 1)

c1 = d_c["Close"] > d_c["MA60"]
c2 = 42 <= d_p["RSI"] <= 55 and d_c["RSI"] > d_p["RSI"]
c3 = vr < 0.9
c4 = 0 <= bias < 4
c5 = f_c["RSI"] > f_p["RSI"]

if all([c1,c2,c3,c4,c5]):
    send_tg(
        f"📈 *[PULLBACK 縮量勾頭]* `{sym}` · {label} ★高勝率\n"
        f"💰 現價: `{d_c['Close']:.2f}` · 距季線: `{bias:.1f}%`\n"
        f"燈號: {L(c1)}60MA上 {L(c2)}RSI勾頭 {L(c3)}縮量 {L(c4)}乖離<4% {L(c5)}5m確認\n"
        f"📊 日RSI: `{d_c['RSI']:.0f}` · 量能: `{vr:.2f}x`\n"
        f"💡 Sell Put 佈局 · 守 60MA\n⏰ {tw_time()} TWN"
    )
```

# ══════════════════════════════════════════════════════════════════════════════

# 加密貨幣策略：1小時線，PULLBACK + ORB + MACD翻零軸

# ══════════════════════════════════════════════════════════════════════════════

def strategy_crypto(yf_sym, display_name, df_1h):
if len(df_1h) < 60: return

```
df = add_rsi(add_macd(add_bb(
    add_sma(add_sma(df_1h, "Close", 50, "EMA50"), "Volume", 20, "V_MA20")
)))

curr = df.iloc[-1]; prev = df.iloc[-2]; prev2 = df.iloc[-3]
if pd.isna(curr["RSI"]) or pd.isna(curr["MACD"]): return

price   = curr["Close"]
ema50   = curr["EMA50"]
vr      = curr["Volume"] / (curr["V_MA20"] + 1)
rsi     = curr["RSI"]
ch1h    = (curr["Close"] - prev["Close"]) / prev["Close"] * 100

# ── 加密 PULLBACK（★成功率較高）──────────────────────────────────────────
# 條件：在EMA50之上 + RSI回調42-58 + MACD在零軸上方 + 縮量 + RSI勾頭
cp1 = price > ema50
cp2 = 42 <= prev["RSI"] <= 58 and rsi > prev["RSI"]   # RSI勾頭
cp3 = curr["MACD"] > 0                                 # MACD零軸上方
cp4 = vr < 0.85                                        # 縮量
cp5 = curr["MACD_hist"] > prev["MACD_hist"]            # MACD柱放大

if sum([cp1,cp2,cp3,cp4,cp5]) >= 4 and cp1 and cp2:
    bias = (price - ema50) / ema50 * 100
    send_tg(
        f"₿ *[加密 PULLBACK]* `{display_name}` · ₿加密 ★\n"
        f"💰 現價: `{price:.4f}` · 1h漲跌: `{ch1h:+.2f}%`\n"
        f"燈號: {L(cp1)}EMA50上 {L(cp2)}RSI勾頭 {L(cp3)}MACD零軸上 {L(cp4)}縮量 {L(cp5)}柱放大\n"
        f"📊 RSI: `{rsi:.0f}` · 距EMA50: `{bias:.1f}%`\n"
        f"💡 做多參考 · 止損 EMA50 以下\n⏰ {tw_time()} TWN"
    )

# ── 加密 ORB（1小時突破前4小時高點）──────────────────────────────────────
# 條件：突破前4根1h高點 + 量能2x + MACD翻正
range_high = df.iloc[-5:-1]["High"].max()
range_low  = df.iloc[-5:-1]["Low"].min()
macd_cross_up   = curr["MACD"] > curr["MACD_sig"] and prev["MACD"] <= prev["MACD_sig"]
macd_cross_down = curr["MACD"] < curr["MACD_sig"] and prev["MACD"] >= prev["MACD_sig"]

# 多頭：突破 + 量2x + MACD金叉
ob1=price>range_high; ob2=prev["Close"]<=range_high; ob3=vr>=2.0; ob4=macd_cross_up
if sum([ob1,ob2,ob3,ob4]) >= 3 and ob1 and ob2:
    send_tg(
        f"₿ *[加密 ORB 多頭]* `{display_name}` · ₿加密\n"
        f"💰 現價: `{price:.4f}` · 突破: `{range_high:.4f}`\n"
        f"燈號: {L(ob1)}突破4h高 {L(ob2)}剛發動 {L(ob3)}量2x {L(ob4)}MACD金叉\n"
        f"📊 RSI: `{rsi:.0f}` · 量比: `{vr:.1f}x`\n"
        f"⏰ {tw_time()} TWN"
    )

# 空頭：跌破 + 量2x + MACD死叉
os1=price<range_low; os2=prev["Close"]>=range_low; os4=macd_cross_down
if sum([os1,os2,ob3,os4]) >= 3 and os1 and os2:
    send_tg(
        f"₿ *[加密 ORB 空頭]* `{display_name}` · ₿加密\n"
        f"💰 現價: `{price:.4f}` · 跌破: `{range_low:.4f}`\n"
        f"燈號: {L(os1)}跌破4h低 {L(os2)}剛發動 {L(ob3)}量2x {L(os4)}MACD死叉\n"
        f"📊 RSI: `{rsi:.0f}` · 量比: `{vr:.1f}x`\n"
        f"⏰ {tw_time()} TWN"
    )

# ── 加密 MACD翻零軸（獨立信號）────────────────────────────────────────────
# MACD從負翻正（強勢信號）
if curr["MACD"] > 0 and prev["MACD"] <= 0 and price > ema50:
    send_tg(
        f"₿ *[加密 MACD翻零軸]* `{display_name}` · ₿加密\n"
        f"💰 現價: `{price:.4f}`\n"
        f"📊 MACD: `{prev['MACD']:.4f}` → `{curr['MACD']:.4f}` 翻正\n"
        f"📈 RSI: `{rsi:.0f}` · 在EMA50之上\n"
        f"💡 中線做多信號\n⏰ {tw_time()} TWN"
    )
```

# ══════════════════════════════════════════════════════════════════════════════

# 主程式

# ══════════════════════════════════════════════════════════════════════════════

def main():
tw_now = datetime.now(pytz.timezone(“Asia/Taipei”))
print(f”\n{’=’*50}”)
print(f”CC Scanner {tw_now.strftime(’%Y-%m-%d %H:%M’)} TWN”)
print(f”美股:{‘開盤’ if is_us_open() else ‘休市’} | 台股:{‘開盤’ if is_tw_open() else ‘休市’} | 加密:24/7”)
print(f”{’=’*50}”)

```
# ── 🇺🇸 美股日內（A + B）─────────────────────────────────────────────────
if is_us_open():
    print("[🇺🇸日內] 策略A+B...")
    for sym in US_TICKERS:
        try:
            df5 = get_5m(sym); df15 = get_15m(sym)
            if df5.empty: continue
            strategy_washout(sym, df5, "🇺🇸日內")
            strategy_orb(sym, df5, df15, "🇺🇸日內")
        except Exception as e:
            print(f"  {sym}: {e}")

# ── 🇺🇸 美股波段（C，收盤前30分）────────────────────────────────────────
if is_us_swing():
    print("[🇺🇸波段] 策略C...")
    for sym in US_TICKERS:
        try:
            df1d = get_1d(sym); df5 = get_5m(sym)
            if not df1d.empty and not df5.empty:
                strategy_pullback(sym, df1d, df5, "🇺🇸波段")
        except Exception as e:
            print(f"  {sym}: {e}")

# ── 🇹🇼 台股日內（A + B）─────────────────────────────────────────────────
if is_tw_open():
    print("[🇹🇼日內] 策略A+B...")
    for sym in TW_TICKERS:
        try:
            df5 = get_5m(sym); df15 = get_15m(sym)
            if df5.empty: continue
            strategy_washout(sym, df5, "🇹🇼日內")
            strategy_orb(sym, df5, df15, "🇹🇼日內")
        except Exception as e:
            print(f"  {sym}: {e}")

# ── 🇹🇼 台股波段（C，收盤前30分）────────────────────────────────────────
if is_tw_swing():
    print("[🇹🇼波段] 策略C...")
    for sym in TW_TICKERS:
        try:
            df1d = get_1d(sym); df5 = get_5m(sym)
            if not df1d.empty and not df5.empty:
                strategy_pullback(sym, df1d, df5, "🇹🇼波段")
        except Exception as e:
            print(f"  {sym}: {e}")

# ── ₿ 加密貨幣（24/7，每次都跑）─────────────────────────────────────────
print("[₿加密] 掃描...")
for yf_sym, disp in CRYPTO_PAIRS:
    try:
        df1h = get_1h(yf_sym)
        if not df1h.empty:
            strategy_crypto(yf_sym, disp, df1h)
    except Exception as e:
        print(f"  {disp}: {e}")

# ── 全部休市時 ────────────────────────────────────────────────────────────
if not is_us_open() and not is_tw_open():
    print("股市全部休市，僅掃加密貨幣")

print("掃描結束\n")
```

if **name** == “**main**”:
main()
