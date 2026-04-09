"""
CC Market Scanner v8.2
數據源：Alpaca API（美股即時）/ yfinance（台股、加密、日線）
標籤：🇺🇸權值 | 🛡️資安 | ⚛️核能 | 🚀妖股 | 🇹🇼台股 | ₿加密 | 🔍VCP候選
策略：⛈️暴跌預兆 | 🔮暴漲預兆 | ⚡WASHOUT | 📈波段PULLBACK | 🏦SMC | ₿半木夏 | 🎯VCP Pro

v8.2 變更：
  - 移除 Finviz Elite API（改為手動清單）
  - 新增 VCP_WATCHLIST 手動候選清單（LUNR/ETON/MCS/REPX/TALK）
  - 整合 VCP Pro 掃描：波動收縮 + 成交量乾枯 + 支撐墊高
  - 保留 v8.1 全部漏洞修復（F1~F6）
"""

import requests
import pandas as pd
import numpy as np
import ta
import yfinance as yf
import os
import time
from datetime import datetime
import pytz

# ── Token ─────────────────────────────────────────────────────────────────────
TG_TOKEN      = os.environ.get("TG_TOKEN",      "")
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID",    "")
ALPACA_KEY    = os.environ.get("ALPACA_KEY",    "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")
ALPACA_BASE   = "https://data.alpaca.markets/v2"

# ── 監控名單 ──────────────────────────────────────────────────────────────────
TICKERS_FIXED = {
    "🇺🇸": ["NVDA","AVGO","ANET","VRT","VST","TSLA","AMD","AMZN","AAPL","META","MSFT","GOOGL","PLTR","CRDO","ALAB","QQQ"],
    "🛡️": ["PANW","FTNT","CRWD"],
    "⚛️": ["OKLO","NNE"],
    "🚀": ["COIN","MSTR","MARA","CLSK","HOOD","SOFI","APLD","IONQ","RGTI","NVTS","AAOI","RCAT","ONDS","AXTI","AEHR","ACMR","KTOS","SERV"],
    "🇹🇼": ["2330.TW","00631L.TW"],
    "₿":   [("BTC-USD","BTC/USDT")],
}

# ── VCP 手動候選清單（從 Finviz 手動篩選，定期更新）─────────────────────────
# 更新方式：直接修改此清單，或在 GitHub 設定環境變數覆蓋
VCP_WATCHLIST = [
    "LUNR",   # 月球探索/航太
    "ETON",   # 罕見疾病製藥
    "MCS",    # 影城連鎖
    "REPX",   # 石油天然氣
    "TALK",   # AI語音/通訊
]
# 支援從環境變數擴充：EXTRA_VCP=TSLA,AAPL
_extra = os.environ.get("EXTRA_VCP", "")
if _extra:
    VCP_WATCHLIST += [s.strip() for s in _extra.split(",") if s.strip()]

# ── 持倉提示 ──────────────────────────────────────────────────────────────────
PORTFOLIO_HINTS = {
    "NVDA": "💼 持238股 → PULLBACK/OB可賣Covered Call，行權價現價+5%，2~4週到期",
    "CRCL": "💼 持110股 → 高波動，S級信號才動，停損設前低",
    "NVTS": "💼 持200股 → 妖股，量確認再進，留意假突破",
    "AVGO": "💼 定投第4月 → 強信號可額外加碼1股",
    "VRT":  "💼 定投第4月 → 強信號可額外加碼1股",
    "ANET": "💼 定投第4月 → 強信號可額外加碼1股",
}

_crash_warned = set()   # 暴跌預兆狀態（F4 情境感知）

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
    ny = _now_ny(); d = ny.strftime("%Y-%m-%d")
    if ny.weekday() >= 5 or d in US_HOLIDAYS: return "CLOSED"
    m = ny.hour * 60 + ny.minute
    if 240 <= m < 570:  return "PRE"
    if 570 <= m < 930:  return "OPEN"
    if 930 <= m < 1200: return "POST"
    return "CLOSED"

def is_tw_open():
    tw = _now_tw(); d = tw.strftime("%Y-%m-%d")
    if tw.weekday() >= 5 or d in TW_HOLIDAYS: return False
    return 540 <= tw.hour * 60 + tw.minute < 810

def is_tw_swing():
    tw = _now_tw(); d = tw.strftime("%Y-%m-%d")
    if tw.weekday() >= 5 or d in TW_HOLIDAYS: return False
    return 780 <= tw.hour * 60 + tw.minute < 810

def is_us_swing():
    ny = _now_ny(); d = ny.strftime("%Y-%m-%d")
    if ny.weekday() >= 5 or d in US_HOLIDAYS: return False
    return 900 <= ny.hour * 60 + ny.minute < 930

def pre_market_window():
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
def L(v):     return "✅" if v else "❌"

def grade(score, total):
    pct = score / total
    if pct >= 0.85: return "🏆 S級"
    if pct >= 0.70: return "🥇 A級"
    return None

# ── 掃描名單組合（固定 + VCP 手動清單）───────────────────────────────────────
def build_scan_universe():
    """
    合併掃描名單：
      固定名單（TICKERS_FIXED）→ 維持原有標籤
      VCP_WATCHLIST → 標記為 🔍
    回傳 [(sym, tag), ...]
    """
    fixed_map = {}
    for tag, lst in TICKERS_FIXED.items():
        if tag == "₿": continue
        for sym in lst:
            fixed_map[sym] = tag

    universe = list(fixed_map.items())
    for sym in VCP_WATCHLIST:
        if sym not in fixed_map:
            universe.append((sym, "🔍"))

    return universe

# ── Minervini 趨勢模板（F2：新股保護）────────────────────────────────────────
_trend_cache = {}

def passes_trend_template(sym, df=None):
    if sym in _trend_cache: return _trend_cache[sym]
    try:
        if df is None:
            df = _clean(yf.download(sym, interval="1d", period="200d",
                                    progress=False, auto_adjust=True))
        n = len(df)
        if n < 50:
            _trend_cache[sym] = True; return True

        c     = df["Close"]
        price = float(c.iloc[-1])
        ma50  = float(c.rolling(50).mean().iloc[-1])

        if n < 152:
            ma50_prev = float(c.rolling(50).mean().iloc[-6])
            result = price > ma50 and ma50 > ma50_prev
            _trend_cache[sym] = result; return result

        ma150 = float(c.rolling(150).mean().iloc[-1])
        if n < 252:
            result = price > ma50 > ma150
            _trend_cache[sym] = result; return result

        ma200     = float(c.rolling(200).mean().iloc[-1])
        ma200_old = float(c.rolling(200).mean().iloc[-22])
        yr_low    = float(c.tail(252).min())
        yr_high   = float(c.tail(252).max())
        checks = [
            price > ma50, ma50 > ma150, ma150 > ma200,
            ma200 > ma200_old,
            price >= yr_low  * 1.25,
            price >= yr_high * 0.75,
        ]
        result = sum(checks) >= 5
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
        df = df.set_index("t").rename(
            columns={"o":"Open","h":"High","l":"Low","c":"Close","v":"Volume"})
        return df[["Open","High","Low","Close","Volume"]].dropna()
    except Exception as e:
        print(f"  Alpaca {symbol}: {e}"); return pd.DataFrame()

def _clean(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

def get_yf_5m(s):  return _clean(yf.download(s, interval="5m",  period="2d",   progress=False, auto_adjust=True))
def get_yf_15m(s): return _clean(yf.download(s, interval="15m", period="10d",  progress=False, auto_adjust=True))
def get_yf_1d(s):  return _clean(yf.download(s, interval="1d",  period="200d", progress=False, auto_adjust=True))

def get_df5(sym):
    df = get_alpaca_bars(sym, "5Min", 80)
    return df if not df.empty else get_yf_5m(sym)

def get_df15_intraday(sym):
    df = get_alpaca_bars(sym, "15Min", 40)
    return df if not df.empty else get_yf_15m(sym)

def get_df15_full(sym):
    # F5：SMC 專用，強制 yfinance 全市場量能
    return get_yf_15m(sym)

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
    df["MACD"] = m.macd()
    df["MACD_sig"]  = m.macd_signal()
    df["MACD_hist"] = m.macd_diff()
    return df

def add_atr(df, n=14):
    df = df.copy()
    df["ATR"] = ta.volatility.AverageTrueRange(
        df["High"], df["Low"], df["Close"], window=n).average_true_range()
    return df

# ══════════════════════════════════════════════════════════════════════════════
# 🎯 VCP Pro 掃描
# 條件：波動收縮 + 成交量乾枯 + 支撐墊高
# 數據：日線 6 個月（yfinance）
# 觸發：每日收盤後波段掃描時段 / 盤前匯總
# ══════════════════════════════════════════════════════════════════════════════
def scan_vcp_pro_all(ticker_list):
    """
    批次掃描 VCP_WATCHLIST，回傳 Telegram 訊息列表
    整合三個條件：
      1. 波動收縮（Volatility Contraction）：近10日高點標準差 < 前期
      2. 成交量乾枯（Volume Dry-up）：近5日均量 < 50日均量的70%
      3. 支撐墊高（Higher Low）：近5日最低 > 前10~15日最低
    另附：50MA 乖離率、樞軸點、距離支點%、狀態判斷
    """
    results = []
    for symbol in ticker_list:
        try:
            df = yf.download(symbol, period="6mo", progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.dropna()
            if len(df) < 50: continue

            # ── 基礎指標 ──────────────────────────────────────────────────────
            current_price = float(df["Close"].iloc[-1])
            df["MA50"]    = df["Close"].rolling(50).mean()
            ma50          = float(df["MA50"].iloc[-1])
            bias_50       = (current_price - ma50) / ma50 * 100

            vol_50avg     = float(df["Volume"].rolling(50).mean().iloc[-1])
            vol_5avg      = float(df["Volume"].iloc[-5:].mean())

            # ── 三核心條件 ────────────────────────────────────────────────────
            # 1. 成交量乾枯：近5日均量 < 50日均量 × 70%
            vol_dry    = vol_5avg < vol_50avg * 0.7

            # 2. 波動收縮：近10日高點標準差 < 前10~30日高點標準差
            std_recent = float(df["High"].iloc[-10:].std())
            std_prior  = float(df["High"].iloc[-30:-10].std())
            is_tight   = std_recent < std_prior and std_prior > 0

            # 3. 支撐墊高：近5日最低 > 前10~15日最低
            low_recent = float(df["Low"].iloc[-5:].min())
            low_prior  = float(df["Low"].iloc[-15:-5].min())
            higher_low = low_recent > low_prior

            # ── 入場支點與風險 ────────────────────────────────────────────────
            pivot         = float(df["High"].iloc[-10:].max())
            entry_price   = pivot + 0.05
            dist_to_pivot = (entry_price - current_price) / current_price * 100

            # ── 狀態判斷 ──────────────────────────────────────────────────────
            conditions_met = sum([vol_dry, is_tight, higher_low])
            if conditions_met == 3 and 0 < dist_to_pivot < 3 and bias_50 < 25:
                status = "🔥 準備突破"
            elif bias_50 > 30:
                status = "⚠️ 偏離過遠"
            elif conditions_met >= 2:
                status = "👀 觀察中"
            else:
                status = "⏳ 尚未成熟"

            # ── 只發 S/A 級：三條件全中 或 兩條件+接近支點 ──────────────────
            should_alert = (
                conditions_met == 3 or
                (conditions_met == 2 and 0 < dist_to_pivot < 5)
            )
            if not should_alert:
                continue

            # ── 止損計算（樞軸點 -7%，Minervini 標準）────────────────────────
            stop_loss = pivot * 0.93
            risk      = entry_price - stop_loss
            target    = entry_price + risk * 3   # 1:3 風報比

            hint = PORTFOLIO_HINTS.get(symbol, "")
            results.append({
                "score": conditions_met * 3 + (3 if status == "🔥 準備突破" else 0),
                "msg": (
                    f"🔍 🎯 *[VCP Pro]* `{symbol}` "
                    f"{'🏆 S級' if conditions_met == 3 else '🥇 A級'}\n"
                    f"💰 現價: `{current_price:.2f}` · 50MA乖離: `{bias_50:.1f}%`\n"
                    f"燈號: {L(vol_dry)}量縮乾枯 {L(is_tight)}波動收縮 {L(higher_low)}支撐墊高\n"
                    f"📊 量比: `{vol_5avg/vol_50avg:.2f}x` · 近期/前期波動: "
                    f"`{std_recent:.2f}`/`{std_prior:.2f}`\n"
                    f"🎯 樞軸: `{pivot:.2f}` · 建議買入: `{entry_price:.2f}` "
                    f"(距{dist_to_pivot:.1f}%)\n"
                    f"🛑 止損: `{stop_loss:.2f}` · 目標: `{target:.2f}` (1:3)\n"
                    f"狀態: {status}\n"
                    + (f"📌 {hint}\n" if hint else "")
                    + f"⏰ {tw_time()} TWN"
                )
            })

        except Exception as e:
            print(f"  VCP {symbol}: {e}")

    return results


def run_vcp_scan():
    """
    VCP 掃描入口，整合進盤前匯總與波段時段
    """
    results = scan_vcp_pro_all(VCP_WATCHLIST)
    if not results:
        return []
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ══════════════════════════════════════════════════════════════════════════════
# 🏦 SMC（F1 OB失效偵測 + F5 yfinance全市場量能）
# ══════════════════════════════════════════════════════════════════════════════
def signal_smc(sym, tag, df15, df1d):
    if len(df15) < 30 or len(df1d) < 20: return None

    results    = []
    curr_price = float(df15["Close"].iloc[-1])

    hi10 = df1d["High"].tail(10).values
    lo10 = df1d["Low"].tail(10).values
    bull_structure = (hi10[-3:].max() > hi10[:5].max() and
                      lo10[-3:].min() > lo10[:5].min())
    bear_structure = (hi10[-3:].max() < hi10[:5].max() and
                      lo10[-3:].min() < lo10[:5].min())

    def find_ob(df, mode="bull"):
        for i in range(3, min(25, len(df) - 2)):
            idx   = -i
            bar   = df.iloc[idx]
            after = df.iloc[idx + 1:]
            next3 = df.iloc[idx + 1: idx + 4]
            if len(next3) < 3: continue
            if mode == "bull":
                is_ob    = bar["Close"] < bar["Open"]
                strong   = (next3["Close"].iloc[-1] > bar["High"] and
                            (next3["Close"].iloc[-1] - bar["Low"]) / (bar["Low"] + 1e-9) > 0.005)
                if not (is_ob and strong): continue
                ob_h, ob_l = float(bar["High"]), float(bar["Low"])
                # F1：失效偵測
                if (after["Close"] < ob_l).any(): continue
                if ob_l <= curr_price <= ob_h * 1.005:
                    return {"high": ob_h, "low": ob_l, "age": i}
            else:
                is_ob    = bar["Close"] > bar["Open"]
                strong   = (next3["Close"].iloc[-1] < bar["Low"] and
                            (bar["High"] - next3["Close"].iloc[-1]) / (bar["High"] + 1e-9) > 0.005)
                if not (is_ob and strong): continue
                ob_h, ob_l = float(bar["High"]), float(bar["Low"])
                if (after["Close"] > ob_h).any(): continue
                if ob_l * 0.995 <= curr_price <= ob_h:
                    return {"high": ob_h, "low": ob_l, "age": i}
        return None

    def find_fvg(df, mode="bull"):
        for i in range(2, min(20, len(df) - 1)):
            b1 = df.iloc[-i - 1]; b3 = df.iloc[-i + 1]
            if mode == "bull":
                if float(b1["High"]) < float(b3["Low"]):
                    top, bot = float(b3["Low"]), float(b1["High"])
                    if bot <= curr_price <= top:
                        return {"top": top, "bot": bot, "age": i}
            else:
                if float(b1["Low"]) > float(b3["High"]):
                    top, bot = float(b1["Low"]), float(b3["High"])
                    if bot <= curr_price <= top:
                        return {"top": top, "bot": bot, "age": i}
        return None

    bull_ob  = find_ob(df15, "bull"); bear_ob  = find_ob(df15, "bear")
    bull_fvg = find_fvg(df15, "bull"); bear_fvg = find_fvg(df15, "bear")

    sw_high = float(df15["High"].tail(15).iloc[:-2].max())
    sw_low  = float(df15["Low"].tail(15).iloc[:-2].min())
    bos_b   = curr_price > sw_high; bos_r = curr_price < sw_low
    choch_b = bos_b and bear_structure; choch_r = bos_r and bull_structure

    hint = PORTFOLIO_HINTS.get(sym, "")
    crash_note = (f"⚠️ {sym} 本日有暴跌預兆，謹慎操作\n"
                  if sym in _crash_warned else
                  f"📌 {hint}\n" if hint else "")

    if bull_structure and (bull_ob or bull_fvg) and (bos_b or choch_b):
        bs = sum([bull_structure, bool(bull_ob), bool(bull_fvg), bos_b, choch_b])
        g  = grade(bs, 5)
        if g:
            stop   = (bull_ob["low"] if bull_ob else bull_fvg["bot"]) * 0.995
            target = curr_price + (curr_price - stop) * 2
            ctag   = " *CHoCH反轉*" if choch_b else " BOS順勢"
            results.append({"score": bs, "msg": (
                f"{tag} 🏦 *[SMC 多頭]{ctag}* `{sym}` {g}\n"
                f"💰 現價:`{curr_price:.2f}`\n"
                f"燈號:{L(bull_structure)}日線牛市 {L(bool(bull_ob))}OB有效 "
                f"{L(bool(bull_fvg))}FVG {L(bos_b)}BOS {L(choch_b)}CHoCH\n"
                + (f"📦 OB:`{bull_ob['low']:.2f}~{bull_ob['high']:.2f}`\n" if bull_ob else "")
                + (f"🕳️ FVG:`{bull_fvg['bot']:.2f}~{bull_fvg['top']:.2f}`\n" if bull_fvg else "")
                + f"🎯 進場:`{curr_price:.2f}` 止損:`{stop:.2f}` 目標:`{target:.2f}` (1:2)\n"
                + crash_note + f"⏰ {tw_time()} TWN"
            )})

    if bear_structure and (bear_ob or bear_fvg) and (bos_r or choch_r):
        bs = sum([bear_structure, bool(bear_ob), bool(bear_fvg), bos_r, choch_r])
        g  = grade(bs, 5)
        if g:
            stop   = (bear_ob["high"] if bear_ob else bear_fvg["top"]) * 1.005
            target = curr_price - (stop - curr_price) * 2
            ctag   = " *CHoCH反轉*" if choch_r else " BOS順勢"
            results.append({"score": bs, "msg": (
                f"{tag} 🏦 *[SMC 空頭]{ctag}* `{sym}` {g}\n"
                f"💰 現價:`{curr_price:.2f}`\n"
                f"燈號:{L(bear_structure)}日線熊市 {L(bool(bear_ob))}OB有效 "
                f"{L(bool(bear_fvg))}FVG {L(bos_r)}BOS {L(choch_r)}CHoCH\n"
                + (f"📦 OB:`{bear_ob['low']:.2f}~{bear_ob['high']:.2f}`\n" if bear_ob else "")
                + (f"🕳️ FVG:`{bear_fvg['bot']:.2f}~{bear_fvg['top']:.2f}`\n" if bear_fvg else "")
                + f"🎯 進場:`{curr_price:.2f}` 止損:`{stop:.2f}` 目標:`{target:.2f}` (1:2)\n"
                + crash_note + f"⏰ {tw_time()} TWN"
            )})

    return results if results else None


# ══════════════════════════════════════════════════════════════════════════════
# ⛈️ 暴跌預兆（F4 記錄狀態）
# ══════════════════════════════════════════════════════════════════════════════
def signal_crash_warning(sym, tag, df5):
    if len(df5) < 20: return None
    df = add_rsi(add_sma(df5, "Volume", 15, "VMA"))
    curr = df.iloc[-1]
    if pd.isna(curr["VMA"]): return None
    vr      = curr["Volume"] / (curr["VMA"] + 1)
    prev_hi = df["High"].tail(20).max()
    c1 = (curr["Close"] > prev_hi * 0.94 and
          vr > 3.5 and curr["Close"] < curr["Open"])
    c2 = curr["Close"] < df["Low"].tail(6).iloc[:-1].min()
    score = sum([c1, c2])
    if score == 0: return None
    g = grade(score, 2)
    if not g: return None
    _crash_warned.add(sym)   # F4
    hint = PORTFOLIO_HINTS.get(sym, "")
    return {
        "score": score + 10,
        "msg": (
            f"{tag} ⛈️ *[暴跌預兆]* `{sym}` {g}\n"
            f"💰 現價:`{curr['Close']:.2f}`\n"
            f"燈號:{L(c1)}高位爆量收黑 {L(c2)}跌破5日支撐\n"
            f"📊 量比:`{vr:.1f}x` · RSI:`{curr['RSI']:.0f}`\n"
            f"🚨 建議立刻減倉，切勿留過夜\n"
            + (f"🚨 {sym} 持倉：暫停 CC/加碼，優先評估停損\n" if hint else "")
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
    c1 = vr > 2.5; c2 = curr["Close"] > prev_hi
    c3 = prev["Close"] <= prev_hi; c4 = 55 < curr["RSI"] < 78
    c5 = c15["Close"] > prev_hi
    score = sum([c1, c2, c3, c4, c5])
    g = grade(score, 5)
    if not g or not (c2 and c3): return None
    hint = PORTFOLIO_HINTS.get(sym, "") if sym not in _crash_warned else ""
    return {
        "score": score,
        "msg": (
            f"{tag} 🔮 *[暴漲預兆]* `{sym}` {g}\n"
            f"💰 現價:`{curr['Close']:.2f}` · 突破:`{prev_hi:.2f}`\n"
            f"燈號:{L(c1)}量2.5x {L(c2)}突破前高 {L(c3)}剛發動 {L(c4)}RSI動能 {L(c5)}15m確認\n"
            f"📊 量比:`{vr:.1f}x` · RSI:`{curr['RSI']:.0f}` · 條件:`{score}/5`\n"
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
    day_open = float(df.iloc[0]["Open"]); day_low = float(df["Low"].min())
    yest     = df[df.index.date < df.index[-1].date()]
    yest_low = float(yest["Low"].min()) if not yest.empty else day_low * 0.97
    drop     = (day_open - day_low) / day_open * 100
    rebound  = (float(curr["Close"]) - day_low) / (day_open - day_low + 0.001)
    vr       = curr["Volume"] / (curr["VMA10"] + 1)
    min_drop = 1.5 if "🚀" not in tag else 2.0
    c1=drop>min_drop; c2=curr["Close"]>=day_open*0.998; c3=prev["Close"]<day_open
    c4=curr["RSI"]>prev["RSI"]>prev2["RSI"]; c5=curr["RSI"]<72
    c6=curr["Close"]>yest_low; c7=rebound>0.5; c8=curr["MA5"]>curr["MA20"]
    score = sum([c1,c2,c3,c4,c5,c6,c7,c8])
    g = grade(score, 8)
    if not g or not c1 or not c2: return None
    prefix = "🌅 [盤前洗盤]" if status == "PRE" else "⚡ [WASHOUT]"
    warn   = f"\n⚠️ RSI`{curr['RSI']:.0f}` 偏高，等回測5MA" if curr["RSI"] > 65 else ""
    hint   = PORTFOLIO_HINTS.get(sym, "") if sym not in _crash_warned else ""
    return {
        "score": score,
        "msg": (
            f"{tag} {prefix} `{sym}` {g}\n"
            f"💰 現價:`{curr['Close']:.2f}` · 殺低:`{drop:.1f}%` · 反彈:`{rebound*100:.0f}%`\n"
            f"燈號:{L(c1)}殺低 {L(c2)}站回 {L(c3)}剛翻 {L(c4)}RSI勾 "
            f"{L(c5)}非追高 {L(c6)}守昨低 {L(c7)}彈力 {L(c8)}MA翻多\n"
            f"📊 RSI:`{curr['RSI']:.0f}` 量比:`{vr:.1f}x` 條件:`{score}/8`{warn}\n"
            f"🎯 目標:`{curr['Close']*1.02:.2f}` · 止損:`{yest_low:.2f}`\n"
            + (f"📌 {hint}\n" if hint else "")
            + f"⏰ {tw_time()} TWN"
        )
    }


# ══════════════════════════════════════════════════════════════════════════════
# 📈 波段PULLBACK（F4 情境感知）
# ══════════════════════════════════════════════════════════════════════════════
def signal_pullback(sym, tag, df1d, df5):
    if len(df1d) < 65 or len(df5) < 3: return None
    d = add_macd(add_rsi(add_sma(add_sma(df1d,"Close",60,"MA60"),"Volume",5,"V5")))
    f = add_rsi(df5)
    d_c=d.iloc[-1]; d_p=d.iloc[-2]; f_c=f.iloc[-1]; f_p=f.iloc[-2]
    if pd.isna(d_c["MA60"]) or pd.isna(d_c["RSI"]): return None
    bias60=(d_c["Close"]-d_c["MA60"])/d_c["MA60"]*100
    vr=d_c["Volume"]/(d_c["V5"]+1)
    c1=d_c["Close"]>d_c["MA60"]; c2=42<=d_p["RSI"]<=58 and d_c["RSI"]>d_p["RSI"]
    c3=vr<0.85; c4=0<=bias60<5; c5=f_c["RSI"]>f_p["RSI"]; c6=d_c["MACD_hist"]>d_p["MACD_hist"]
    score=sum([c1,c2,c3,c4,c5,c6])
    g=grade(score,6)
    if not g or not c1 or not c2: return None
    if sym in _crash_warned:
        action = f"⚠️ {sym} 本日有暴跌預兆，PULLBACK 可能是誘多，暫緩操作"
    elif sym == "NVDA":
        action = f"💼 持238股 → 可賣Covered Call，行權價約`{d_c['Close']*1.05:.0f}`，2~4週到期"
    else:
        action = PORTFOLIO_HINTS.get(sym, "")
    sp = d_c["Close"] * 0.95
    return {
        "score": score,
        "msg": (
            f"{tag} 📈 *[波段PULLBACK]* `{sym}` {g}\n"
            f"💰 現價:`{d_c['Close']:.2f}` · 距季線:`{bias60:.1f}%`\n"
            f"燈號:{L(c1)}季線上 {L(c2)}RSI勾 {L(c3)}縮量 "
            f"{L(c4)}貼季線 {L(c5)}5m確認 {L(c6)}MACD放大\n"
            f"📊 日RSI:`{d_c['RSI']:.0f}` 量能:`{vr:.2f}x` 條件:`{score}/6`\n"
            f"💡 Sell Put行權價:`{sp:.1f}` · 守季線\n"
            + (f"📌 {action}\n" if action else "")
            + f"⏰ {tw_time()} TWN"
        )
    }


# ══════════════════════════════════════════════════════════════════════════════
# ₿ 半木夏三背離（F3 跨度>20根 + F6 止損1.5ATR）
# ══════════════════════════════════════════════════════════════════════════════
def signal_banmuxa(yf_sym, disp, df15):
    if len(df15) < 120: return None
    df    = add_atr(add_macd(df15.copy()))
    hist  = df["MACD_hist"].values
    high  = df["High"].values; low = df["Low"].values
    curr  = df.iloc[-1]
    atr   = float(curr["ATR"]) if not pd.isna(curr["ATR"]) else 0
    price = float(curr["Close"])

    def find_extremes(arr, window=5, mode="peak"):
        out = []
        for i in range(window, len(arr)-window):
            seg = arr[i-window:i+window+1]
            if mode=="peak"   and arr[i]==max(seg) and arr[i]>0: out.append(i)
            if mode=="trough" and arr[i]==min(seg) and arr[i]<0: out.append(i)
        return out

    peaks=find_extremes(hist,"peak"); troughs=find_extremes(hist,"trough")
    results=[]

    if len(peaks)>=3:
        p1,p2,p3=peaks[-3],peaks[-2],peaks[-1]
        if ((p3-p1)>20 and                          # F3 跨度門檻
            hist[p1]<hist[p2]<hist[p3] and          # MACD峰遞升
            high[p1]>high[p2]>high[p3] and          # 價格高遞降
            hist[-1]>0 and
            abs(hist[-1])<abs(hist[p3])*0.7 and
            hist[-1]<hist[-2]):                      # 正柱縮小
            stop=float(high[p3])+atr*1.5            # F6 1.5ATR
            risk=max(stop-price, atr*0.5)
            target=price-risk*2
            results.append({"score":9,"msg":(
                f"₿ 🔻 *[半木夏 空頭三背離]* `{disp}` 🏆 S級\n"
                f"💰 現價:`{price:.2f}`\n"
                f"📊 MACD三峰:`{hist[p1]:.4f}`→`{hist[p2]:.4f}`→`{hist[p3]:.4f}` ↑ (跨{p3-p1}根)\n"
                f"📊 K線高點:`{high[p1]:.2f}`→`{high[p2]:.2f}`→`{high[p3]:.2f}` ↓\n"
                f"⚡ 正柱縮小（實心→虛心）進場信號\n"
                f"🎯 做空:`{price:.2f}` 止損:`{stop:.2f}` (×1.5ATR={atr:.0f})\n"
                f"💰 目標:`{target:.2f}` (1:2)\n⏰ {tw_time()} TWN"
            )})

    if len(troughs)>=3:
        t1,t2,t3=troughs[-3],troughs[-2],troughs[-1]
        if ((t3-t1)>20 and
            hist[t1]>hist[t2]>hist[t3] and
            low[t1]<low[t2]<low[t3] and
            hist[-1]<0 and
            abs(hist[-1])<abs(hist[t3])*0.7 and
            hist[-1]>hist[-2]):
            stop=float(low[t3])-atr*1.5
            risk=max(price-stop, atr*0.5)
            target=price+risk*2
            results.append({"score":9,"msg":(
                f"₿ 🚀 *[半木夏 多頭三背離]* `{disp}` 🏆 S級\n"
                f"💰 現價:`{price:.2f}`\n"
                f"📊 MACD三谷:`{hist[t1]:.4f}`→`{hist[t2]:.4f}`→`{hist[t3]:.4f}` ↓ (跨{t3-t1}根)\n"
                f"📊 K線低點:`{low[t1]:.2f}`→`{low[t2]:.2f}`→`{low[t3]:.2f}` ↑\n"
                f"⚡ 負柱縮小（實心→虛心）進場信號\n"
                f"🎯 做多:`{price:.2f}` 止損:`{stop:.2f}` (×1.5ATR={atr:.0f})\n"
                f"💰 目標:`{target:.2f}` (1:2)\n⏰ {tw_time()} TWN"
            )})

    return results if results else None


# ══════════════════════════════════════════════════════════════════════════════
# 盤前匯總
# ══════════════════════════════════════════════════════════════════════════════
def run_pre_market_scan(universe):
    window = pre_market_window()
    if not window: return
    label = {
        "PRE_30":   "⏰ 開盤前30分鐘 盤前匯總",
        "PRE_15":   "⏰ 開盤前15分鐘 盤前匯總",
        "OPEN_NOW": "🔔 開盤當下 信號匯總",
    }[window]

    all_sigs = []

    # 一般信號
    for sym, tag in universe:
        if tag in ("🇹🇼", "₿"): continue
        try:
            if not passes_trend_template(sym): continue
            df5  = get_df5(sym); df15 = get_df15_intraday(sym)
            df1d = get_yf_1d(sym)
            if df5.empty: continue
            for fn in [
                lambda s,t,d5,d15: signal_crash_warning(s,t,d5),
                signal_breakout_pre,
                lambda s,t,d5,d15: signal_washout(s,t,d5,d15,"PRE"),
            ]:
                r = fn(sym, tag, df5, df15)
                if r: all_sigs.append(r)
            df15f = get_df15_full(sym)
            if not df15f.empty and not df1d.empty:
                smc = signal_smc(sym, tag, df15f, df1d)
                if smc: all_sigs.extend(smc)
            time.sleep(0.3)
        except Exception as e:
            print(f"  PRE {sym}: {e}")

    # VCP Pro 掃描（盤前也跑）
    vcp_sigs = run_vcp_scan()
    all_sigs.extend(vcp_sigs)

    all_sigs.sort(key=lambda x: x["score"], reverse=True)
    vcp_count = sum(1 for s, t in universe if t == "🔍")

    if not all_sigs:
        send_tg(f"━━━ {label} ━━━\n📭 無 S/A 級信號"); return

    send_tg(
        f"━━━ {label} · {len(all_sigs)}個信號 ━━━\n"
        f"🗓️ {_now_tw().strftime('%Y-%m-%d %H:%M')} TWN\n"
        f"監控: 固定名單 + VCP手動 {vcp_count}支"
    )
    for s in all_sigs:
        send_tg(s["msg"])


# ══════════════════════════════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════════════════════════════
def main():
    global _crash_warned
    _crash_warned = set()

    status = us_market_status()
    tw_now = _now_tw()
    print(f"\n{'='*55}")
    print(f"CC Scanner v8.2 · {tw_now.strftime('%Y-%m-%d %H:%M')} TWN")
    print(f"美股:{status} | 台股:{'開盤' if is_tw_open() else '休市'} | 加密:24/7")
    print(f"VCP候選:{VCP_WATCHLIST}")
    print(f"{'='*55}")

    universe = build_scan_universe()

    # ── 盤前匯總 ─────────────────────────────────────────────────────────────
    if pre_market_window():
        run_pre_market_scan(universe)
        if pre_market_window() in ("PRE_30", "PRE_15"):
            print("盤前匯總完成\n"); return

    intraday_sigs=[]; swing_sigs=[]; smc_sigs=[]; crypto_sigs=[]; vcp_sigs=[]

    # ── 美股即時 ─────────────────────────────────────────────────────────────
    if status == "OPEN":
        for sym, tag in universe:
            if tag in ("🇹🇼","₿"): continue
            try:
                if not passes_trend_template(sym): continue
                df5=get_df5(sym); df15=get_df15_intraday(sym)
                if df5.empty: continue
                for fn in [
                    lambda s,t,d5,d15: signal_crash_warning(s,t,d5),
                    signal_breakout_pre,
                    lambda s,t,d5,d15: signal_washout(s,t,d5,d15,status),
                ]:
                    r=fn(sym,tag,df5,df15)
                    if r: intraday_sigs.append(r)
            except Exception as e:
                print(f"  {sym}: {e}")

    # ── SMC（F5 yfinance 全市場量能）────────────────────────────────────────
    if status=="OPEN" or is_us_swing():
        for sym, tag in universe:
            if tag in ("🇹🇼","₿"): continue
            try:
                if not passes_trend_template(sym): continue
                df15f=get_df15_full(sym); df1d=get_yf_1d(sym)
                if df15f.empty or df1d.empty: continue
                res=signal_smc(sym,tag,df15f,df1d)
                if res: smc_sigs.extend(res)
                time.sleep(0.3)
            except Exception as e:
                print(f"  SMC {sym}: {e}")

    # ── 美股波段 + VCP Pro ───────────────────────────────────────────────────
    if is_us_swing():
        for sym, tag in universe:
            if tag in ("🇹🇼","₿","🇨🇳"): continue
            try:
                if not passes_trend_template(sym): continue
                df1d=get_yf_1d(sym); df5=get_df5(sym)
                if not df1d.empty and not df5.empty:
                    r=signal_pullback(sym,tag,df1d,df5)
                    if r: swing_sigs.append(r)
            except Exception as e:
                print(f"  SWING {sym}: {e}")

        # VCP Pro 在波段時段執行
        vcp_sigs = run_vcp_scan()

    # ── 台股 ─────────────────────────────────────────────────────────────────
    if is_tw_open():
        for sym in TICKERS_FIXED["🇹🇼"]:
            try:
                df5=get_yf_5m(sym); df15=get_yf_15m(sym)
                if df5.empty: continue
                for fn in [signal_breakout_pre,
                           lambda s,t,d5,d15: signal_washout(s,t,d5,d15,"OPEN")]:
                    r=fn(sym,"🇹🇼",df5,df15)
                    if r: intraday_sigs.append(r)
            except Exception as e:
                print(f"  TW {sym}: {e}")

    if is_tw_swing():
        for sym in TICKERS_FIXED["🇹🇼"]:
            try:
                df1d=get_yf_1d(sym); df5=get_yf_5m(sym)
                if not df1d.empty and not df5.empty:
                    r=signal_pullback(sym,"🇹🇼",df1d,df5)
                    if r: swing_sigs.append(r)
            except Exception as e:
                print(f"  TW SWING {sym}: {e}")

    # ── 加密：半木夏（24/7）──────────────────────────────────────────────────
    for yf_sym, disp in TICKERS_FIXED["₿"]:
        try:
            df15=get_yf_15m(yf_sym)
            if df15.empty: continue
            res=signal_banmuxa(yf_sym,disp,df15)
            if res: crypto_sigs.extend(res)
        except Exception as e:
            print(f"  ₿ {disp}: {e}")

    # ── 排序 + 發送 ──────────────────────────────────────────────────────────
    for lst in [intraday_sigs, swing_sigs, smc_sigs, crypto_sigs, vcp_sigs]:
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

    if vcp_sigs:
        send_tg(f"━━━ 🎯 VCP Pro · {len(vcp_sigs)}個 ━━━")
        for s in vcp_sigs: send_tg(s["msg"])

    if crypto_sigs:
        send_tg(f"━━━ ₿ 半木夏三背離 · {len(crypto_sigs)}個 ━━━")
        for s in crypto_sigs: send_tg(s["msg"])

    if not any([intraday_sigs, smc_sigs, swing_sigs, vcp_sigs, crypto_sigs]):
        print("本次無 S/A 級信號")

    print("掃描結束\n")


if __name__ == "__main__":
    main()
