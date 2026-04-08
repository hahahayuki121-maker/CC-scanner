"""
CC Market Scanner v8.0 — FINAL INTEGRATION
=============================================================
整合來源：v7.3(修漏洞) + v7.5(Polygon) + v7.6(醫師格式+冷卻)
新增：
  - Polygon.io SIP 全市場量能（取代 IEX，量比不再失真）
  - ALAB / QQQ / ONDS 加回監控清單
  - 21:30 TWN 盤前統整（彙整一則，非逐筆）
  - 通知格式統一（圖片版：emoji+行距+狀態標籤）
  - 冷卻改檔案儲存（跨 GitHub Actions 執行有效）
  - 延遲偵測（Polygon 資料 > 15 分鐘自動 fallback）
=============================================================
"""

import requests
import pandas as pd
import ta
import yfinance as yf
import os
import json
import time
from datetime import datetime
import pytz

# ── 避開整點請求高峰 ─────────────────────────────────────────────────────────
time.sleep(30)

# ── 密鑰 ─────────────────────────────────────────────────────────────────────
TG_TOKEN      = os.environ.get("TG_TOKEN",      "")
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID",    "")
POLYGON_KEY   = os.environ.get("POLYGON_KEY",   "")   # ← 已購買月費，SIP全量
ALPACA_KEY    = os.environ.get("ALPACA_KEY",    "")   # fallback
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")
ALPACA_BASE   = "https://data.alpaca.markets/v2"

# ── 監控名單 ──────────────────────────────────────────────────────────────────
TICKERS = {
    "🇺🇸": ["NVDA","AVGO","ANET","VRT","VST","TSLA","AMD","AMZN",
             "AAPL","META","MSFT","GOOGL","PLTR","CRDO","ALAB","QQQ"],
    "🛡️": ["PANW","FTNT","CRWD"],
    "⚛️": ["SMR","OKLO","NNE"],
    "🚀": ["COIN","MSTR","MARA","CLSK","HOOD","SOFI",
            "APLD","IONQ","RGTI","NVTS","AAOI","RCAT","ONDS"],
    "🇨🇳": ["BABA","PDD","FUTU"],
    "🇹🇼": ["2330.TW","00631L.TW"],
    "₿":   [("BTC-USD","BTC/USDT"),("ETH-USD","ETH/USDT"),("ETH-BTC","ETH/BTC")],
}
VOLATILE_TAGS = {"🚀", "⚛️"}

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

# ── 冷卻（檔案儲存，跨 Actions 執行有效）────────────────────────────────────
CACHE_FILE = "/tmp/cc_scanner_cache.json"

def load_cache():
    try:
        with open(CACHE_FILE) as f: return json.load(f)
    except: return {}

def save_cache(c):
    try:
        with open(CACHE_FILE, "w") as f: json.dump(c, f)
    except: pass

def is_cooled(cache, key, mins=30):
    if key not in cache: return True
    elapsed = (datetime.utcnow() - datetime.fromisoformat(cache[key])).total_seconds()
    return elapsed > mins * 60

def mark_sent(cache, key):
    cache[key] = datetime.utcnow().isoformat()

# ── 時間 ─────────────────────────────────────────────────────────────────────
def _ny(): return datetime.now(pytz.timezone("America/New_York"))
def _tw(): return datetime.now(pytz.timezone("Asia/Taipei"))
def tw_time(): return _tw().strftime("%H:%M:%S")

def us_status():
    ny = _ny(); d = ny.strftime("%Y-%m-%d")
    if ny.weekday() >= 5 or d in US_HOLIDAYS: return "CLOSED"
    m = ny.hour * 60 + ny.minute
    if 240 <= m < 570:  return "PRE"
    if 570 <= m < 930:  return "OPEN"
    if 930 <= m < 1200: return "POST"
    return "CLOSED"

def is_tw_open():
    tw = _tw(); d = tw.strftime("%Y-%m-%d")
    if tw.weekday() >= 5 or d in TW_HOLIDAYS: return False
    m = tw.hour * 60 + tw.minute
    return 540 <= m < 810

def is_tw_swing():
    tw = _tw(); d = tw.strftime("%Y-%m-%d")
    if tw.weekday() >= 5 or d in TW_HOLIDAYS: return False
    return 780 <= tw.hour * 60 + tw.minute < 810

def is_us_swing():
    ny = _ny(); d = ny.strftime("%Y-%m-%d")
    if ny.weekday() >= 5 or d in US_HOLIDAYS: return False
    return 900 <= ny.hour * 60 + ny.minute < 930

def get_mode():
    """
    DIGEST_PRE  = 台灣 21:28~21:33（美股開盤前彙整）
    DIGEST_TW   = 台灣 08:00~09:00（台股開盤前彙整）
    DIGEST_30   = 開盤中每 30 分鐘彙整（整點 / 半點）
    SILENT      = 休市
    注意：開盤中 S 級訊號無論何時都會即時補發，不等彙整。
    """
    tw = _tw(); m = tw.hour * 60 + tw.minute
    st = us_status()
    if 1288 <= m <= 1293: return "DIGEST_PRE"
    if 480 <= m < 540:    return "DIGEST_TW"
    if st in ("PRE", "OPEN") or is_tw_open(): return "DIGEST_30"
    return "SILENT"

def is_digest_30_window():
    """每 30 分鐘彙整：GitHub Actions 在整點/半點跑時觸發彙整。
    判斷：台灣時間分鐘數落在 28~33 或 58~63（即整點前後 5 分鐘）。
    注意 sleep(30) 已讓實際執行落在 :30 秒，所以用 minute % 30 <= 4 判斷。
    """
    m = _tw().minute
    return m % 30 <= 4

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

def L(v): return "✅" if v else "❌"

def grade(score, total):
    p = score / total
    if p >= 0.85: return "🏆 S級"
    if p >= 0.70: return "🥇 A級"
    return None

# ── 通知格式（對齊圖片版）────────────────────────────────────────────────────
def fmt_msg(tag, emoji, signal, sym, g,
            price, chg, vr, rsi, smc,
            status_label, advice, detail=""):
    """
    格式範例（對應圖片）：
    🚀 🔮 [暴漲預兆] AAOI 🏆 S級
    💰 現價: 15.82 · 📈: +6.4%
    📊 量比: 3.2x · RSI: 68 · SMC: BOS多
    🎫 [確診發動]: 結構已確認轉向且帶量突破OB區間，建議分批進場，SAR翻轉即停損。
    ⏰ 22:45:10 TWN
    """
    trend = "📈" if chg >= 0 else "📉"
    return (
        f"{tag} {emoji} *[{signal}]* `{sym}` {g}\n"
        f"💰 現價: `{price:.2f}` · {trend}: `{chg:+.1f}%`\n"
        f"📊 量比: `{vr:.1f}x` · RSI: `{rsi:.0f}` · SMC: `{smc}`\n"
        f"{detail}"
        f"🎫 *[{status_label}]*: {advice}\n"
        f"⏰ {tw_time()} TWN"
    )

# ══════════════════════════════════════════════════════════════════════════════
# 數據獲取：Polygon SIP → Alpaca IEX → yfinance
# ══════════════════════════════════════════════════════════════════════════════

def _clean(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

def get_polygon_bars(symbol, multiplier, timespan, limit=100):
    """
    Polygon.io SIP feed — 100% 真實市場成交量，量比不再失真。
    付費帳戶可取得即時數據。
    """
    if not POLYGON_KEY: return pd.DataFrame()
    try:
        url = (f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range"
               f"/{multiplier}/{timespan}/2026-01-01/2026-12-31")
        params = {"adjusted": "true", "sort": "desc", "limit": limit,
                  "apiKey": POLYGON_KEY}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200: return pd.DataFrame()
        results = r.json().get("results", [])
        if not results: return pd.DataFrame()
        df = pd.DataFrame(results)
        df["t"] = (pd.to_datetime(df["t"], unit="ms")
                   .dt.tz_localize("UTC")
                   .dt.tz_convert("America/New_York"))
        df = df.set_index("t").sort_index()
        df = df.rename(columns={"o":"Open","h":"High","l":"Low","c":"Close","v":"Volume"})
        df = df[["Open","High","Low","Close","Volume"]].dropna()

        # 延遲偵測：最新一根超過 15 分鐘視為過期
        now_ny = _ny()
        last_t = df.index[-1]
        if (now_ny - last_t).total_seconds() > 900:
            print(f"  ⚠️ {symbol} Polygon延遲{(now_ny-last_t).total_seconds()/60:.0f}分，跳過")
            return pd.DataFrame()
        return df
    except Exception as e:
        print(f"  Polygon {symbol}: {e}")
        return pd.DataFrame()

def get_alpaca_bars(symbol, tf="5Min", limit=80):
    """Alpaca IEX — 第二層 fallback（量能約真實市場的 1/8）"""
    if not ALPACA_KEY or not ALPACA_SECRET: return pd.DataFrame()
    try:
        r = requests.get(
            f"{ALPACA_BASE}/stocks/{symbol}/bars",
            headers={"APCA-API-KEY-ID": ALPACA_KEY,
                     "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={"timeframe": tf, "limit": limit, "feed": "iex"},
            timeout=10
        )
        bars = r.json().get("bars", [])
        if not bars: return pd.DataFrame()
        df = pd.DataFrame(bars)
        df["t"] = pd.to_datetime(df["t"])
        df = (df.set_index("t")
                .rename(columns={"o":"Open","h":"High","l":"Low","c":"Close","v":"Volume"}))
        # 延遲偵測
        try:
            last_t = df.index[-1].tz_convert("America/New_York")
            if (_ny() - last_t).total_seconds() > 900:
                print(f"  ⚠️ {symbol} Alpaca延遲")
                return pd.DataFrame()
        except: pass
        return df[["Open","High","Low","Close","Volume"]].dropna()
    except Exception as e:
        print(f"  Alpaca {symbol}: {e}"); return pd.DataFrame()

def get_consistent(sym):
    """
    FIX-A: 強制同源，消滅陰陽價。
    優先順序：Polygon(SIP) > Alpaca(IEX) > yfinance
    回傳: (df5, df15, source)
    """
    # Polygon SIP
    df5 = get_polygon_bars(sym, 5, "minute", 80)
    if not df5.empty:
        df15 = get_polygon_bars(sym, 15, "minute", 40)
        if not df15.empty: return df5, df15, "polygon"

    # Alpaca IEX fallback
    df5 = get_alpaca_bars(sym, "5Min", 80)
    if not df5.empty:
        df15 = get_alpaca_bars(sym, "15Min", 40)
        if not df15.empty: return df5, df15, "alpaca"

    # yfinance fallback
    df5  = _clean(yf.download(sym, interval="5m",  period="2d", progress=False, auto_adjust=True))
    df15 = _clean(yf.download(sym, interval="15m", period="5d", progress=False, auto_adjust=True))
    return df5, df15, "yfinance"

def get_yf(sym, interval, period):
    return _clean(yf.download(sym, interval=interval, period=period,
                               progress=False, auto_adjust=True))

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
    df = df.copy(); m = ta.trend.MACD(df["Close"])
    df["MACD"] = m.macd(); df["MACD_hist"] = m.macd_diff()
    return df

# ── SMC 簡版（15m 結構辨識）─────────────────────────────────────────────────
def detect_smc(df15):
    if len(df15) < 15: return "無結構"
    c = df15.iloc[-1]; p = df15.iloc[-2]
    hi = df15["High"].tail(10).max()
    lo = df15["Low"].tail(10).min()
    if c["Close"] > hi and p["Close"] <= hi: return "BOS多🔥"
    if c["Close"] < lo and p["Close"] >= lo: return "BOS空🔴"
    if c["Close"] > df15["High"].tail(5).iloc[:-1].max(): return "CHoCH轉多"
    if c["Close"] < df15["Low"].tail(5).iloc[:-1].min():  return "CHoCH轉空"
    return "盤整"

# ── FIX-B: 當日開盤（嚴格校驗）──────────────────────────────────────────────
def get_day_open(df):
    ny_today = _ny().date()
    try:    idx = df.index.tz_convert("America/New_York")
    except: idx = df.index
    mask = [t.date() == ny_today for t in idx]
    today = df[mask]
    return today.iloc[0]["Open"] if not today.empty else None

# ── FIX-C: 動態止損 ───────────────────────────────────────────────────────────
def dynamic_stop(df, day_low, tag):
    if any(t in tag for t in VOLATILE_TAGS) and len(df) >= 14:
        atr = ta.volatility.AverageTrueRange(
            df["High"], df["Low"], df["Close"], window=14
        ).average_true_range().iloc[-1]
        return day_low - atr * 1.5, f"ATR×1.5(`{atr:.2f}`)"
    return day_low * 0.995, "低點×0.995"

# ══════════════════════════════════════════════════════════════════════════════
# ⛈️ 暴跌預兆
# ══════════════════════════════════════════════════════════════════════════════
def signal_crash(sym, tag, df5, df15, cache):
    if len(df5) < 20: return None
    ck = f"crash_{sym}"
    if not is_cooled(cache, ck, 15): return None

    df = add_rsi(add_sma(df5, "Volume", 15, "VMA"))
    c  = df.iloc[-1]; p = df.iloc[-2]
    if pd.isna(c["VMA"]) or c["VMA"] < 50: return None

    vr      = c["Volume"] / (c["VMA"] + 1)
    support = df["Low"].tail(6).iloc[:-1].min()
    smc     = detect_smc(df15)

    c1 = c["Close"] < support                        # 跌破支撐
    c2 = vr > 2.5                                    # 放量（Polygon 真實量，門檻恢復 2.5x）
    c3 = c["Close"] < c["Open"]                      # 收黑
    c4 = c["RSI"] < p["RSI"] and c["RSI"] < 65      # FIX-D RSI 背離

    score = sum([c1, c2, c3, c4])
    g = grade(score, 4)
    if not g or not c1 or not c2: return None

    mark_sent(cache, ck)
    chg = (c["Close"] - df.iloc[0]["Open"]) / df.iloc[0]["Open"] * 100
    return {
        "score": score + 10, "type": "⛈️",
        "msg": fmt_msg(
            tag, "⛈️", "暴跌預兆", sym, g,
            c["Close"], chg, vr, c["RSI"], smc,
            "敗象已現",
            "高位爆量結構轉空，立刻減倉，切勿留過夜",
            f"燈號: {L(c1)}破支撐 {L(c2)}放量 {L(c3)}收黑 {L(c4)}RSI背離\n"
        )
    }

# ══════════════════════════════════════════════════════════════════════════════
# 🔮 暴漲預兆
# ══════════════════════════════════════════════════════════════════════════════
def signal_surge(sym, tag, df5, df15, source, cache):
    if len(df5) < 20 or len(df15) < 5: return None
    ck = f"surge_{sym}"
    if not is_cooled(cache, ck, 30): return None

    df = add_rsi(add_sma(df5, "Volume", 15, "VMA"))
    c  = df.iloc[-1]; p = df.iloc[-2]
    if pd.isna(c["VMA"]) or c["VMA"] < 50: return None

    vr      = c["Volume"] / (c["VMA"] + 1)
    prev_hi = df["High"].tail(7).iloc[:-1].max()
    smc     = detect_smc(df15)
    c15     = df15.iloc[-1]

    # Polygon 量比門檻 2.5x；Alpaca IEX 補償 /8
    vol_thresh = 2.5 if source == "polygon" else (2.5 / 8.0)

    c1 = vr > vol_thresh
    c2 = c["Close"] > prev_hi
    c3 = p["Close"] <= prev_hi
    c4 = 55 < c["RSI"] < 78
    c5 = c15["Close"] > prev_hi

    if not (c1 and c2 and c3): return None
    score = sum([c1, c2, c3, c4, c5])
    g = grade(score, 5)
    if not g: return None

    mark_sent(cache, ck)
    chg = (c["Close"] - df.iloc[0]["Open"]) / df.iloc[0]["Open"] * 100
    return {
        "score": score, "type": "🔮",
        "msg": fmt_msg(
            tag, "🔮", "暴漲預兆", sym, g,
            c["Close"], chg, vr, c["RSI"], smc,
            "確診發動",
            "結構已確認轉向且帶量突破OB區間，建議分批進場，SAR翻轉即停損",
            f"燈號: {L(c1)}量能 {L(c2)}突破前高 {L(c3)}剛發動 {L(c4)}RSI動能 {L(c5)}15m確認\n"
        )
    }

# ══════════════════════════════════════════════════════════════════════════════
# ⚡ WASHOUT
# ══════════════════════════════════════════════════════════════════════════════
def signal_washout(sym, tag, df5, df15, status, cache):
    if len(df5) < 6 or len(df15) < 3: return None
    ck = f"washout_{sym}"
    if not is_cooled(cache, ck, 30): return None

    df = add_rsi(add_sma(add_sma(add_sma(
        df5, "Volume", 10, "V10"), "Close", 5, "MA5"), "Close", 20, "MA20"))
    c = df.iloc[-1]; p = df.iloc[-2]; p2 = df.iloc[-3]
    if pd.isna(c["MA5"]): return None

    # FIX-B: 嚴格校驗當日開盤
    day_open = get_day_open(df)
    if day_open is None: return None

    day_low  = df["Low"].min()
    yest     = df[df.index.date < df.index[-1].date()]
    yest_low = yest["Low"].min() if not yest.empty else day_low * 0.97
    drop     = (day_open - day_low) / day_open * 100
    rebound  = (c["Close"] - day_low) / (day_open - day_low + 0.001)
    vr       = c["Volume"] / (c["V10"] + 1)
    smc      = detect_smc(df15)

    min_drop = 1.5 if "🚀" not in tag else 2.0
    c1 = drop > min_drop
    c2 = c["Close"] >= day_open * 0.998
    c3 = p["Close"] < day_open
    # FIX-4: RSI 連升加斜率門檻
    c4 = (c["RSI"] > p["RSI"] > p2["RSI"]) and (c["RSI"] - p2["RSI"] > 3)
    c5 = c["RSI"] < 72
    c6 = c["Close"] > yest_low
    c7 = rebound > 0.5
    c8 = c["MA5"] > c["MA20"]

    if not (c1 and c2 and vr > 0.3): return None
    score = sum([c1, c2, c3, c4, c5, c6, c7, c8])
    g = grade(score, 8)
    if not g: return None

    # FIX-C: 動態止損
    stop, stop_m = dynamic_stop(df, day_low, tag)
    target = c["Close"] * 1.02
    rr     = (target - c["Close"]) / (c["Close"] - stop + 0.001)
    if rr < 1.5: return None

    mark_sent(cache, ck)
    chg    = (c["Close"] - day_open) / day_open * 100
    prefix = "盤前洗盤" if status == "PRE" else "洗盤結束"
    warn   = " ⚠️ RSI偏高等回測5MA" if c["RSI"] > 65 else ""
    return {
        "score": score, "type": "⚡",
        "msg": fmt_msg(
            tag, "⚡", "WASHOUT", sym, g,
            c["Close"], chg, vr, c["RSI"], smc,
            prefix,
            f"大幅殺低帶量站回，守今日低點進場，止損{stop:.2f}({stop_m})",
            f"燈號: {L(c1)}殺低 {L(c2)}站回 {L(c3)}剛翻 {L(c4)}RSI勾 "
            f"{L(c5)}非追高 {L(c6)}守昨低 {L(c7)}彈力 {L(c8)}MA翻多\n"
            f"條件:`{score}/8` 反彈:`{rebound*100:.0f}%` 風報:`{rr:.1f}x`{warn}\n"
        )
    }

# ══════════════════════════════════════════════════════════════════════════════
# 📈 波段 PULLBACK
# ══════════════════════════════════════════════════════════════════════════════
def signal_pullback(sym, tag, df1d, df5, cache):
    if len(df1d) < 65 or len(df5) < 3: return None
    ck = f"pullback_{sym}"
    if not is_cooled(cache, ck, 60): return None

    d  = add_macd(add_rsi(add_sma(add_sma(df1d, "Close", 60, "MA60"), "Volume", 5, "V5")))
    f  = add_rsi(df5)
    dc = d.iloc[-1]; dp = d.iloc[-2]; fc = f.iloc[-1]; fp = f.iloc[-2]
    if pd.isna(dc["MA60"]) or pd.isna(dc["RSI"]): return None

    bias = (dc["Close"] - dc["MA60"]) / dc["MA60"] * 100
    vr   = dc["Volume"] / (dc["V5"] + 1)
    c1 = dc["Close"] > dc["MA60"]
    c2 = 42 <= dp["RSI"] <= 58 and dc["RSI"] > dp["RSI"]
    c3 = vr < 0.85; c4 = 0 <= bias < 5
    c5 = fc["RSI"] > fp["RSI"]; c6 = dc["MACD_hist"] > dp["MACD_hist"]

    score = sum([c1, c2, c3, c4, c5, c6])
    g = grade(score, 6)
    if not g or not (c1 and c2): return None

    mark_sent(cache, ck)
    sp = dc["Close"] * 0.95
    return {
        "score": score, "type": "📈",
        "msg": fmt_msg(
            tag, "📈", "波段PULLBACK", sym, g,
            dc["Close"], 0, vr, dc["RSI"], "日線",
            "候補進場",
            f"縮量回測季線RSI勾頭，Sell Put參考{sp:.1f}(-5%)，守季線{dc['MA60']:.2f}",
            f"燈號: {L(c1)}季線上 {L(c2)}RSI勾頭 {L(c3)}縮量 "
            f"{L(c4)}貼季線 {L(c5)}5m確認 {L(c6)}MACD\n"
            f"條件:`{score}/6` 距季線:`{bias:.1f}%` 量能:`{vr:.2f}x`\n"
        )
    }

# ══════════════════════════════════════════════════════════════════════════════
# ₿ 加密（5m 暴跌預警 + 1h PULLBACK / MACD 翻零軸）
# ══════════════════════════════════════════════════════════════════════════════
def signal_crypto(yf_sym, disp, df5m, df1h, cache):
    results = []

    # — 5m 暴跌預警 —
    if len(df5m) >= 20:
        ck = f"crypto_crash_{disp}"
        if is_cooled(cache, ck, 15):
            df5 = add_rsi(add_sma(df5m, "Volume", 15, "VMA"))
            c5  = df5.iloc[-1]
            if not pd.isna(c5["VMA"]) and c5["VMA"] > 1:
                vr5 = c5["Volume"] / (c5["VMA"] + 1)
                sup = df5["Low"].tail(6).iloc[:-1].min()
                cc1 = c5["Close"] < sup
                cc2 = vr5 > 2.5
                cc3 = c5["Close"] < c5["Open"]
                cc4 = c5["RSI"] < 45
                if sum([cc1, cc2, cc3, cc4]) >= 3 and cc1 and cc2:
                    mark_sent(cache, ck)
                    results.append({
                        "score": 12, "type": "⛈️",
                        "msg": (
                            f"₿ ⛈️ *[加密暴跌預警]* `{disp}` 🏆 S級\n"
                            f"💰 現價: `{c5['Close']:.4f}`\n"
                            f"燈號: {L(cc1)}破支撐 {L(cc2)}放量 {L(cc3)}收黑 {L(cc4)}RSI弱\n"
                            f"📊 量比: `{vr5:.1f}x`\n"
                            f"🚨 建議立刻止損\n⏰ {tw_time()} TWN"
                        )
                    })

    if len(df1h) < 50: return results

    # — 1h 指標 —
    df1 = add_macd(add_rsi(df1h.copy()))
    df1["EMA20"] = ta.trend.EMAIndicator(df1["Close"], window=20).ema_indicator()
    df1["EMA50"] = ta.trend.EMAIndicator(df1["Close"], window=50).ema_indicator()
    df1["VMA20"] = ta.trend.SMAIndicator(df1["Volume"], window=20).sma_indicator()
    c1h = df1.iloc[-1]; p1h = df1.iloc[-2]
    if pd.isna(c1h["EMA50"]) or pd.isna(c1h["RSI"]): return results

    price = c1h["Close"]
    vr1   = c1h["Volume"] / (c1h["VMA20"] + 1)

    # — MACD 翻零軸（冷卻 60min）—
    ck2 = f"crypto_macd_{disp}"
    if (c1h["MACD"] > 0 and p1h["MACD"] <= 0
            and price > c1h["EMA50"] and c1h["RSI"] < 72
            and is_cooled(cache, ck2, 60)):
        mark_sent(cache, ck2)
        results.append({
            "score": 10, "type": "🔥",
            "msg": (
                f"₿ 🔥 *[加密MACD翻零軸]* `{disp}` 🏆 S級\n"
                f"💰 現價: `{price:.4f}`\n"
                f"📊 MACD: `{p1h['MACD']:.4f}`→`{c1h['MACD']:.4f}` 翻正\n"
                f"✅ RSI: `{c1h['RSI']:.0f}` · EMA50上方\n"
                f"💡 中線做多強信號 · 止損EMA50\n⏰ {tw_time()} TWN"
            )
        })

    # — 1h PULLBACK（條件收緊，冷卻 30min）—
    ck3 = f"crypto_pull_{disp}"
    if is_cooled(cache, ck3, 30):
        cp1 = price > c1h["EMA50"]; cp2 = price > c1h["EMA20"]
        cp3 = 45 <= p1h["RSI"] <= 60 and c1h["RSI"] > p1h["RSI"]
        cp4 = c1h["RSI"] < 70; cp5 = c1h["MACD"] > 0
        cp6 = c1h["MACD_hist"] > p1h["MACD_hist"]; cp7 = vr1 < 0.9
        sc  = sum([cp1, cp2, cp3, cp4, cp5, cp6, cp7])
        g   = grade(sc, 7)
        if g and cp1 and cp2 and cp3 and cp4:
            mark_sent(cache, ck3)
            bias = (price - c1h["EMA20"]) / c1h["EMA20"] * 100
            results.append({
                "score": sc, "type": "📈",
                "msg": (
                    f"₿ 📈 *[加密PULLBACK]* `{disp}` {g}\n"
                    f"💰 現價: `{price:.4f}` · 距EMA20: `{bias:.1f}%`\n"
                    f"燈號: {L(cp1)}EMA50 {L(cp2)}EMA20 {L(cp3)}RSI勾 "
                    f"{L(cp4)}未超買 {L(cp5)}MACD>0 {L(cp6)}柱放大 {L(cp7)}縮量\n"
                    f"📊 RSI: `{c1h['RSI']:.0f}` · 條件: `{sc}/7`\n"
                    f"💡 做多 · 止損EMA50\n⏰ {tw_time()} TWN"
                )
            })

    return results

# ══════════════════════════════════════════════════════════════════════════════
# 📋 21:30 盤前統整（彙整一則訊息）
# ══════════════════════════════════════════════════════════════════════════════
def format_digest(sigs, label):
    tw_now = _tw()
    intra  = sorted([s for s in sigs if s["type"] in ("⚡","🔮","⛈️")],
                    key=lambda x: -x["score"])
    swing  = sorted([s for s in sigs if s["type"] == "📈"],
                    key=lambda x: -x["score"])
    crypto = sorted([s for s in sigs if s.get("type") in ("🔥",) or
                     ("BTC" in s["msg"] or "ETH" in s["msg"])],
                    key=lambda x: -x["score"])

    lines = [
        f"📋 *CC Scanner · {label}*",
        f"⏰ {tw_now.strftime('%m/%d %H:%M')} TWN · 美股: {us_status()}",
        f"數據源: Polygon{'✓' if POLYGON_KEY else '✗'} SIP",
        ""
    ]

    if intra:
        lines.append(f"⚡ *日內候選 ({len(intra)}個)*")
        for s in intra[:8]:
            # 取第一行作摘要
            first = s["msg"].split("\n")[0].replace("*","").replace("`","")
            lines.append(f"• {first}")
        lines.append("")

    if swing:
        lines.append(f"📈 *波段候選 ({len(swing)}個)*")
        for s in swing[:5]:
            first = s["msg"].split("\n")[0].replace("*","").replace("`","")
            lines.append(f"• {first}")
        lines.append("")

    if crypto:
        lines.append(f"₿ *加密信號 ({len(crypto)}個)*")
        for s in crypto[:3]:
            first = s["msg"].split("\n")[0].replace("*","").replace("`","")
            lines.append(f"• {first}")
        lines.append("")

    if not intra and not swing and not crypto:
        lines.append("本次無 S/A 級信號，繼續等待")

    lines += ["━━━━━━━━━━━━━━━", "開盤後僅發 ⛈️ 緊急警示"]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════════════════════════════
def main():
    mode   = get_mode()
    status = us_status()
    tw_now = _tw()
    cache  = load_cache()

    print(f"\n{'='*55}")
    print(f"CC Scanner v8.0 · {tw_now.strftime('%Y-%m-%d %H:%M')} TWN")
    print(f"美股:{status} | 模式:{mode}")
    print(f"Polygon:{'✓' if POLYGON_KEY else '✗'} | Alpaca:{'✓' if ALPACA_KEY else '✗'}")
    print(f"{'='*55}")

    if mode == "SILENT":
        print("靜默模式，跳過"); return

    all_sigs = []

    # ── 美股 ──────────────────────────────────────────────────────────────────
    if status in ("PRE", "OPEN") or mode == "DIGEST_PRE":
        for tag in ["🇺🇸", "🛡️", "⚛️", "🚀", "🇨🇳"]:
            for sym in TICKERS.get(tag, []):
                try:
                    df5, df15, src = get_consistent(sym)
                    if df5.empty: continue
                    for fn in [
                        lambda s,t,d5,d15: signal_crash(s, t, d5, d15, cache),
                        lambda s,t,d5,d15: signal_surge(s, t, d5, d15, src, cache),
                        lambda s,t,d5,d15: signal_washout(s, t, d5, d15, status, cache),
                    ]:
                        r = fn(sym, tag, df5, df15)
                        if r: all_sigs.append(r)
                except Exception as e:
                    print(f"  {sym}: {e}")

    # ── 美股波段 ──────────────────────────────────────────────────────────────
    if is_us_swing() or mode == "DIGEST_PRE":
        for tag in ["🇺🇸", "🛡️", "🚀"]:
            for sym in TICKERS.get(tag, []):
                try:
                    df1d = get_yf(sym, "1d", "100d")
                    df5, _, _ = get_consistent(sym)
                    if not df1d.empty and not df5.empty:
                        r = signal_pullback(sym, tag, df1d, df5, cache)
                        if r: all_sigs.append(r)
                except Exception as e:
                    print(f"  {sym}: {e}")

    # ── 台股 ──────────────────────────────────────────────────────────────────
    if is_tw_open() or mode == "DIGEST_TW":
        for sym in TICKERS["🇹🇼"]:
            try:
                df5  = get_yf(sym, "5m",  "2d")
                df15 = get_yf(sym, "15m", "5d")
                if df5.empty: continue
                r = signal_surge(sym, "🇹🇼", df5, df15, "yfinance", cache)
                if r: all_sigs.append(r)
                r = signal_washout(sym, "🇹🇼", df5, df15, "OPEN", cache)
                if r: all_sigs.append(r)
            except Exception as e:
                print(f"  {sym}: {e}")

    if is_tw_swing() or mode == "DIGEST_TW":
        for sym in TICKERS["🇹🇼"]:
            try:
                df1d = get_yf(sym, "1d", "100d")
                df5  = get_yf(sym, "5m",  "2d")
                if not df1d.empty and not df5.empty:
                    r = signal_pullback(sym, "🇹🇼", df1d, df5, cache)
                    if r: all_sigs.append(r)
            except Exception as e:
                print(f"  {sym}: {e}")

    # ── 加密 ──────────────────────────────────────────────────────────────────
    for yf_sym, disp in TICKERS["₿"]:
        try:
            df5m = get_yf(yf_sym, "5m",  "2d")
            df1h = get_yf(yf_sym, "1h",  "60d")
            results = signal_crypto(yf_sym, disp, df5m, df1h, cache)
            if results: all_sigs.extend(results)
        except Exception as e:
            print(f"  {disp}: {e}")

    save_cache(cache)
    all_sigs.sort(key=lambda x: x["score"], reverse=True)
    print(f"掃描完成: {len(all_sigs)}個信號 · 模式:{mode}")

    # ── 發送策略 ──────────────────────────────────────────────────────────────
    if mode in ("DIGEST_PRE", "DIGEST_TW"):
        # 盤前彙整：一則摘要 + S 級補發（最多 3 則）
        label = "美股開盤前彙整 🇺🇸" if mode == "DIGEST_PRE" else "台股開盤前彙整 🇹🇼"
        send_tg(format_digest(all_sigs, label))
        for s in [x for x in all_sigs if "🏆" in x["msg"]][:3]:
            send_tg(s["msg"])

    elif mode == "DIGEST_30":
        # 開盤中：S 級即時發送（每次掃描都跑）
        s_sigs = [s for s in all_sigs if "🏆" in s["msg"]]
        for s in s_sigs:
            send_tg(s["msg"])
            print(f"  → S級即時: {s['msg'].split(chr(10))[0]}")

        # 每 30 分鐘彙整一則（整點/半點視窗）
        if is_digest_30_window():
            tw_now = _tw()
            label  = f"開盤彙整 {tw_now.strftime('%H:%M')} 🇺🇸"
            digest = format_digest(all_sigs, label)
            send_tg(digest)
            print(f"  → 30min彙整發送")
        else:
            mins_left = 30 - (_tw().minute % 30)
            print(f"  → 開盤中：S級已發 {len(s_sigs)} 則，下次彙整約 {mins_left} 分鐘後")

    print("掃描結束\n")

if __name__ == "__main__":
    main()
