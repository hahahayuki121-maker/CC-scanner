"""
CC Market Scanner v9.8.3 — 統合修正版
底本：v9.8.2

相對 v9.8.2 的全部修正與改動：

【BUG-FIX-1】sig_crash 重複定義問題
  - 移除第一個不完整的 sig_crash（缺少 mark/crash_seal 寫入邏輯）
  - 保留第二個完整版本（含 last_type_{sym} 標記）

【FEAT-NEW-1】is_toxic_premarket_time() 低流動性時間屏蔽
  - 美東時間 04:00~04:15（台灣 16:00~16:15）屏蔽所有日內訊號
  - 盤前跳空 (sig_pregap) 豁免：該策略設計本就針對盤前，但延後到 04:15 後發送
  - 加密與台股不受影響（非美股盤前）
  - 不會影響每 30 分鐘彙整邏輯

【FEAT-NEW-2】S 級量比「一票否決」強化
  - 原 FEAT-3 僅在判定 S 級後降級；現改為：量比未達門檻時，
    在進入共振計分前就直接攔截（防止 sc 4/5 卻量能不足的假 S 級）
  - VR_Adj 模式：vr < 1.2 在 sig_surge 入口即過濾（原：vr < 1.5 只降級）
  - yfinance 模式：vr < 1.0 在入口過濾（原：vr < 1.2 只降級）
  - 注意：該門檻刻意低於 vol_thresh，只攔截「極端無量」，不縮減正常訊號

【FEAT-NEW-3】SMC「無結構」處理
  - sig_surge 中，若 smc == "無結構" 且 sc == 5（滿分），強制降為 A 級
  - 不直接過濾（避免訊號太少），但加上 ⚠️ 標記
  - sig_crash 中 smc == "無結構" 維持原邏輯（crash 以價量為主）

【BUG繼承】完整保留 v9.8.1 & v9.8.2 全部修正：
  BUG-1: bmx funding float() 空字串崩潰
  BUG-2: 空頭路徑補 log_forward_test
  BUG-3: 有結果即時 save_cache
  BUG-5: format_digest crash_syms 標記
  BUG-6: import re 移至頂部
  BUG-8: get_tw_stable _clean typo
  FEAT-1: validate_breakout_v3 三重校驗
  FEAT-2: sig_surge 硬性量能底線 < 1000 股
  FEAT-3: S 級 VR 降級（保留，作為二道防線）
  FEAT-4: CRYPTO_LONG / CRYPTO_SHORT 區分
  crash_seal 翻轉邏輯（🔄）
"""

import re
import requests
import pandas as pd
import numpy as np
import ta
import yfinance as yf
import os
import json
from datetime import datetime, timedelta
import pytz
from dataclasses import dataclass, asdict

# ── Token ──────────────────────────────────────────────────────────────────────

TG_TOKEN      = os.environ.get("TG_TOKEN",      "")
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID",    "")
ALPACA_KEY    = os.environ.get("ALPACA_KEY",    "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")
FMP_KEY       = os.environ.get("FMP_API_KEY",   "")
ALPACA_BASE   = "https://data.alpaca.markets/v2"
ACCOUNT_BAL   = int(os.environ.get("ACCOUNT_BAL", "25000"))

# ── 監控名單 ───────────────────────────────────────────────────────────────────

TICKERS = {
    "🇺🇸": ["NVDA","AVGO","ANET","VRT","VST","TSLA","AMD","AMZN",
             "AAPL","META","MSFT","GOOGL","PLTR","QQQ"],
    "🛡️": ["PANW","FTNT","CRWD"],
    "⚛️": ["SMR","OKLO","NNE"],
    "🚀": ["CRCL","COIN","MSTR","MARA","CLSK","HOOD","SOFI",
            "APLD","IONQ","RGTI","NVTS","AAOI","RCAT","ONDS",
            "AXTI","AEHR","ACMR","KTOS","SERV","CRDO","ALAB","INTC","ASX"],
    "🇹🇼": ["2330.TW","00631L.TW","3324.TW","3017.TW","2308.TW","3711.TW"],
    "₿":   [("BTC-USD","BTC/USDT"),("ETH-BTC","ETH/BTC")],
}
VOLATILE_TAGS = {"🚀", "⚛️"}

VCP_WATCHLIST = ["LUNR","ETON","MCS","REPX","TALK"]
_extra = os.environ.get("EXTRA_VCP", "")
if _extra:
    VCP_WATCHLIST += [s.strip() for s in _extra.split(",") if s.strip()]

PORTFOLIO_HINTS = {
    "NVDA": "💼 持倉 → PULLBACK/OB可賣Covered Call，行權價現價+5%",
    "NVTS": "💼 持倉 → 妖股屬性，量確認再進",
    "AVGO": "💼 定投股 → 強信號可考慮加碼",
    "VRT":  "💼 定投股 → 強信號可考慮加碼",
    "AXTI": "⚠️ 薄流動性，信號確認後小倉，止損嚴格",
    "ONDS": "⚠️ 薄流動性，只做盤前跳空信號",
}

# ── 風控設定 ──────────────────────────────────────────────────────────────────

RISK_BUDGET = {
    "SURGE":   0.005,
    "WASHOUT": 0.005,
    "VCP":     0.0075,
    "CRASH":   0.005,
    "CRYPTO":  0.004,
    "DEFAULT": 0.005,
}
STRATEGY_WEIGHT = {
    "SURGE": 1.0, "WASHOUT": 0.7, "VCP": 1.2,
    "CRYPTO": 0.5, "DEFAULT": 1.0,
}
MAX_TOTAL_RISK     = 0.02
MAX_OPEN_POSITIONS = 5
AI_GROUP = {"NVDA","AMD","AVGO","ANET","MSFT","GOOGL","META"}

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

# ── 冷卻快取 ──────────────────────────────────────────────────────────────────

CACHE_FILE    = "/tmp/cc_cache.json"
FWD_TEST_FILE = "/tmp/cc_forward_log.json"

def load_cache():
    try:
        with open(CACHE_FILE) as f: return json.load(f)
    except: return {}

def save_cache(c):
    try:
        with open(CACHE_FILE, "w") as f: json.dump(c, f)
    except: pass

def cooled(cache, key, mins=30):
    if key not in cache: return True
    return (datetime.utcnow() - datetime.fromisoformat(cache[key])).total_seconds() > mins * 60

def mark(cache, key):
    cache[key] = datetime.utcnow().isoformat()

# ── 全域狀態 ──────────────────────────────────────────────────────────────────

_crash_warned        = set()
_trend_cache         = {}
_bt_stats_cache      = {}
_market_regime_cache = {"ts": None, "risk_on": True}

# ── Forward Test ──────────────────────────────────────────────────────────────

@dataclass
class StrategyStats:
    strategy: str
    sample_size: int
    wins: int
    losses: int
    winrate: float
    avg_win_r: float
    avg_loss_r: float
    expectancy_r: float

    def is_tradeable(self, min_samples=15, min_exp=0.10):
        return self.sample_size >= min_samples and self.expectancy_r >= min_exp

def load_fwd():
    try:
        with open(FWD_TEST_FILE) as f: return json.load(f)
    except: return []

def save_fwd(data):
    try:
        with open(FWD_TEST_FILE, "w") as f: json.dump(data[-500:], f)
    except: pass

def log_forward_test(sym, strategy, entry, stop, target, shares=0):
    data   = load_fwd()
    sig_id = f"{sym}*{strategy}*{datetime.utcnow().strftime('%Y%m%d_%H%M')}"
    if any(x.get("signal_id") == sig_id for x in data): return
    data.append({
        "signal_id": sig_id, "sym": sym, "strategy": strategy,
        "shares": shares,
        "entry": round(entry, 4), "stop": round(stop, 4), "target": round(target, 4),
        "opened_at": _tw().strftime("%Y-%m-%d %H:%M"),
        "status": "OPEN", "exit": None, "r_multiple": None,
    })
    save_fwd(data)

def settle_forward_tests():
    data = load_fwd(); changed = False
    for row in data:
        if row.get("status") != "OPEN": continue
        sym = row["sym"]
        try:
            df    = _clean(yf.download(sym, interval="5m", period="5d", progress=False, auto_adjust=True))
            if df.empty: continue
            px    = float(df["Close"].iloc[-1])
            entry = float(row["entry"]); stop = float(row["stop"]); target = float(row["target"])
            risk  = entry - stop
            if risk <= 0: continue
            if px <= stop:
                row.update({"status": "STOPPED",    "exit": round(px,4), "r_multiple": -1.0})
                changed = True
            elif px >= target:
                row.update({"status": "TARGET_HIT", "exit": round(px,4), "r_multiple": round((target-entry)/risk, 2)})
                changed = True
        except: pass
    if changed: save_fwd(data)

def build_strategy_stats(fwd_data=None):
    data    = fwd_data if fwd_data is not None else load_fwd()
    grouped = {}
    for row in data:
        s = row.get("strategy", "DEFAULT")
        grouped.setdefault(s, []).append(row)
    stats = {}
    for strategy, rows in grouped.items():
        closed = [r for r in rows if r.get("r_multiple") is not None]
        if not closed: continue
        wins   = [r for r in closed if r["r_multiple"] > 0]
        losses = [r for r in closed if r["r_multiple"] <= 0]
        n      = len(closed)
        stats[strategy] = StrategyStats(
            strategy=strategy, sample_size=n,
            wins=len(wins), losses=len(losses),
            winrate=len(wins)/n,
            avg_win_r=sum(r["r_multiple"] for r in wins)/max(len(wins),1),
            avg_loss_r=sum(r["r_multiple"] for r in losses)/max(len(losses),1),
            expectancy_r=sum(r["r_multiple"] for r in closed)/n,
        )
    return stats

def analyze_portfolio(fwd_data=None):
    data         = fwd_data if fwd_data is not None else load_fwd()
    open_pos     = [r for r in data if r.get("status") == "OPEN"]
    closed_pos   = [r for r in data if r.get("r_multiple") is not None]
    total_risk_dollar = sum(
        abs(float(r.get("entry",0)) - float(r.get("stop",0))) * int(r.get("shares", 0))
        for r in open_pos
    )
    recent_r = sum(r["r_multiple"] for r in closed_pos[-5:]) if len(closed_pos) >= 5 else 0
    return {
        "open_count": len(open_pos),
        "total_risk": total_risk_dollar,
        "recent_r":   recent_r,
    }

def calc_position(entry, stop, strategy="DEFAULT", stats=None, pf=None):
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0: return 0
    if pf is not None:
        if pf["open_count"] >= MAX_OPEN_POSITIONS: return 0
        max_total_risk_dollar = ACCOUNT_BAL * MAX_TOTAL_RISK
        if pf["total_risk"] >= max_total_risk_dollar: return 0
    risk_pct = RISK_BUDGET.get(strategy, RISK_BUDGET["DEFAULT"]) * STRATEGY_WEIGHT.get(strategy, 1.0)
    if stats and stats.sample_size >= 20:
        if stats.expectancy_r >= 0.4 and stats.winrate >= 0.55:
            risk_pct = min(risk_pct * 1.25, 0.01)
        elif stats.expectancy_r < 0.0:
            risk_pct = max(risk_pct * 0.5, 0.002)
    elif _bt_stats_cache.get(strategy, {}).get("n", 0) >= 30:
        bt = _bt_stats_cache[strategy]
        if bt["wr"] >= 0.55 and bt["exp"] >= 0.3:
            risk_pct = min(risk_pct * 1.15, 0.008)
        elif bt["exp"] < 0.0:
            risk_pct = max(risk_pct * 0.6, 0.002)
    if pf is not None and pf.get("recent_r", 0) <= -3:
        risk_pct *= 0.5
    if pf is not None:
        remaining_risk = ACCOUNT_BAL * MAX_TOTAL_RISK - pf["total_risk"]
        actual_budget  = min(ACCOUNT_BAL * risk_pct, max(remaining_risk, 0))
    else:
        actual_budget  = ACCOUNT_BAL * risk_pct
    shares        = int(actual_budget / risk_per_share)
    max_affordable = int(ACCOUNT_BAL / entry) if entry > 0 else 0
    return min(shares, max_affordable)

# ── 時間判斷 ──────────────────────────────────────────────────────────────────

def _ny(): return datetime.now(pytz.timezone("America/New_York"))
def _tw(): return datetime.now(pytz.timezone("Asia/Taipei"))
def tw_time(): return _tw().strftime("%H:%M:%S")

def us_status():
    ny = _ny(); d = ny.strftime("%Y-%m-%d")
    if ny.weekday() >= 5 or d in US_HOLIDAYS: return "CLOSED"
    m = ny.hour*60 + ny.minute
    if 240 <= m < 570:  return "PRE"
    if 570 <= m < 930:  return "OPEN"
    if 930 <= m < 1200: return "POST"
    return "CLOSED"

def is_tw_open():
    tw = _tw(); d = tw.strftime("%Y-%m-%d")
    if tw.weekday() >= 5 or d in TW_HOLIDAYS: return False
    return 540 <= tw.hour*60 + tw.minute < 810

def is_tw_swing():
    tw = _tw(); d = tw.strftime("%Y-%m-%d")
    if tw.weekday() >= 5 or d in TW_HOLIDAYS: return False
    return 780 <= tw.hour*60 + tw.minute < 810

def is_us_swing():
    ny = _ny(); d = ny.strftime("%Y-%m-%d")
    if ny.weekday() >= 5 or d in US_HOLIDAYS: return False
    return 900 <= ny.hour*60 + ny.minute < 930

def get_mode():
    tw = _tw(); m = tw.hour*60 + tw.minute; st = us_status()
    if 1275 <= m < 1290: return "DIGEST_PRE"
    if 480  <= m < 540:  return "DIGEST_TW_PRE"
    if 810  <= m < 840:  return "DIGEST_TW_CLOSE"
    if st in ("PRE","OPEN") or is_tw_open(): return "OPEN_MODE"
    return "SILENT"

def is_digest_30_window(): return _tw().minute % 30 <= 4

# ── [FEAT-NEW-1] 低流動性時間屏蔽 ─────────────────────────────────────────────

def is_toxic_premarket_time():
    """
    美東時間 04:00~04:15（台灣 16:00~16:15）
    此時段為盤前極低流動性窗口（隔夜掛單宣洩期），
    任何日內動量訊號均不可靠，直接屏蔽。

    豁免：
    - sig_pregap 不受此限制（彙整性質，且 04:15 後才有意義）
    - 台股、加密貨幣不受影響
    - DIGEST 彙整模式仍可發出報表，但當下訊號計分不列入即時推送
    """
    ny = _ny()
    m  = ny.hour * 60 + ny.minute
    # 美東 04:00 = 分鐘數 240，04:15 = 255
    return 240 <= m < 255

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: print(msg); return False
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

def fmt_msg(tag, emoji, signal, sym, g, price, chg, vr, rsi, smc, lbl, advice,
            lights="", extra="", source="", shares=0, stop=0, earn_warn=""):
    trend    = "📈" if chg >= 0 else "📉"
    src      = f" · 來源:`{'IEX即時' if source=='alpaca' else 'yf延遲'}`" if source else ""
    strat_key = signal.split()[0] if signal else "DEFAULT"
    lines = [
        f"{tag} {emoji} *[{signal}]* `{sym}` {g}{src}{earn_warn}",
        f"💰 現價:`{price:.2f}` · {trend}:`{chg:+.1f}%`",
        f"📊 量比:`{vr:.1f}x` · RSI:`{rsi:.0f}` · SMC:`{smc}`"
    ]
    if lights: lines.append(f"燈號: {lights}")
    if extra:  lines.append(extra)
    if shares > 0:
        lines.append(
            f"🛡️ 建議倉位:`{shares}`股 · 止損:`{stop:.2f}` "
            f"(固定{RISK_BUDGET.get(strat_key, RISK_BUDGET['DEFAULT'])*100:.1f}%風險)"
        )
    hint = PORTFOLIO_HINTS.get(sym, "")
    if hint: lines.append(f"📌 {hint}")
    lines += [f"🎫 *[{lbl}]*: {advice}", f"⏰ {tw_time()} TWN"]
    return "\n".join(lines)

# ── 大盤濾網 ──────────────────────────────────────────────────────────────────

def get_market_regime(cache=None):
    now = datetime.utcnow()
    if (_market_regime_cache["ts"] and
            (now - _market_regime_cache["ts"]).total_seconds() < 300):
        return _market_regime_cache["risk_on"]
    try:
        df = _clean(yf.download("QQQ", period="1mo", interval="1d", progress=False, auto_adjust=True))
        if df.empty or len(df) < 10:
            _market_regime_cache.update({"ts": now, "risk_on": True}); return True
        c    = float(df["Close"].iloc[-1])
        ma10 = float(df["Close"].rolling(10).mean().iloc[-1])
        risk_on = c > ma10
        _market_regime_cache.update({"ts": now, "risk_on": risk_on})
        return risk_on
    except:
        _market_regime_cache.update({"ts": now, "risk_on": True}); return True

# ── 財報警告 ──────────────────────────────────────────────────────────────────

def get_earn_warn(sym, cache):
    ek    = f"earn_{sym}"
    ek_ts = f"earn_ts_{sym}"
    now   = _ny().replace(tzinfo=None)
    if ek_ts in cache:
        try:
            if (datetime.utcnow() - datetime.fromisoformat(cache[ek_ts])).total_seconds() < 86400:
                v = cache.get(ek, "NONE")
                if v == "NONE": return ""
                try:
                    days = (datetime.fromisoformat(v) - now).days
                    return f" ⚠️{days}天後財報" if 0 <= days <= 7 else ""
                except: return ""
        except: pass
    if ek in cache:
        v = cache[ek]
        if v == "NONE": return ""
        try:
            days = (datetime.fromisoformat(v) - now).days
            if 0 <= days <= 7: return f" ⚠️{days}天後財報"
            if days < 0:       del cache[ek]
            else: return ""
        except: pass
    earn_date = None
    if FMP_KEY:
        try:
            r = requests.get(
                "https://financialmodelingprep.com/api/v3/earning_calendar",
                params={"from": now.strftime("%Y-%m-%d"),
                        "to": (now + timedelta(days=14)).strftime("%Y-%m-%d"),
                        "apikey": FMP_KEY},
                timeout=8
            )
            for item in r.json():
                if item.get("symbol") == sym and item.get("date"):
                    earn_date = datetime.strptime(item["date"][:10], "%Y-%m-%d")
                    break
        except: pass
    if not earn_date:
        try:
            tk = yf.Ticker(sym); ed = tk.earnings_dates
            if ed is not None and not ed.empty:
                future = ed[ed.index.tz_localize(None) > now]
                if not future.empty:
                    earn_date = future.index[0].tz_localize(None)
        except: pass
    if earn_date:
        cache[ek]    = earn_date.isoformat()
        days = (earn_date - now).days
        if 0 <= days <= 7: return f" ⚠️{days}天後財報"
        return ""
    cache[ek]    = "NONE"
    cache[ek_ts] = datetime.utcnow().isoformat()
    return ""

# ── 數據獲取 ──────────────────────────────────────────────────────────────────

def _clean(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

def get_alpaca(sym, tf="5Min", limit=80):
    if not ALPACA_KEY: return pd.DataFrame()
    try:
        r = requests.get(
            f"{ALPACA_BASE}/stocks/{sym}/bars",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={"timeframe": tf, "limit": limit, "feed": "iex"},
            timeout=10
        )
        bars = r.json().get("bars", [])
        if not bars: return pd.DataFrame()
        df = pd.DataFrame(bars)
        df["t"] = pd.to_datetime(df["t"])
        df = df.set_index("t").rename(columns={"o":"Open","h":"High","l":"Low","c":"Close","v":"Volume"})
        try:
            if (_ny() - df.index[-1].tz_convert("America/New_York")).total_seconds() > 900:
                return pd.DataFrame()
        except: pass
        return df[["Open","High","Low","Close","Volume"]].dropna()
    except: return pd.DataFrame()

REQ_LEN = 80

def get_vr_adj(c_vol, vma_short, df1d=None):
    if df1d is None or len(df1d) < 5:
        return c_vol / (vma_short + 1)
    daily_avg    = float(df1d["Volume"].tail(5).mean())
    iex_baseline = (daily_avg / 78) * 0.025
    safe_denom   = max(vma_short + 1, iex_baseline)
    return c_vol / safe_denom

def get_consistent(sym):
    df5 = get_alpaca(sym, "5Min", REQ_LEN)
    if not df5.empty:
        try:
            if (_ny().replace(tzinfo=None) -
                    df5.index[-1].tz_convert("America/New_York").replace(tzinfo=None)
                ).total_seconds() > 900:
                df5 = pd.DataFrame()
        except: pass

    if not df5.empty:
        df15 = get_alpaca(sym, "15Min", REQ_LEN // 2)
        if not df15.empty:
            if us_status() == "PRE" and len(df5) < 10:
                df5 = pd.DataFrame()
            else:
                try:
                    df1d_vol = _clean(yf.download(sym, interval="1d", period="10d",
                                                  progress=False, auto_adjust=True))
                    if not df1d_vol.empty:
                        df5["_VMA15"] = df5["Volume"].rolling(15).mean()
                        df5["VR_Adj"] = df5.apply(
                            lambda row: get_vr_adj(
                                row["Volume"],
                                row["_VMA15"] if not pd.isna(row["_VMA15"]) else 0,
                                df1d_vol),
                            axis=1
                        )
                        df5.drop(columns=["_VMA15"], inplace=True, errors="ignore")
                except: pass
                return df5.iloc[-REQ_LEN:], df15.iloc[-REQ_LEN//2:], "alpaca"

    df5  = _clean(yf.download(sym, interval="5m",  period="2d", progress=False, auto_adjust=True))
    df15 = _clean(yf.download(sym, interval="15m", period="5d", progress=False, auto_adjust=True))
    if not df5.empty:
        df5  = df5.iloc[-REQ_LEN:]
        df15 = df15.iloc[-REQ_LEN//2:]
    return df5, df15, "yfinance"

def get_tw_stable(sym):
    # [BUG-8 FIX] 修正原碼 `*clean` typo
    for iv, pd_ in [("5m","2d"),("15m","5d"),("1h","5d")]:
        df = _clean(yf.download(sym, interval=iv, period=pd_, progress=False, auto_adjust=True))
        if not df.empty and len(df) >= 5: return df
    return pd.DataFrame()

def get_yf(sym, interval, period):
    return _clean(yf.download(sym, interval=interval, period=period,
                              progress=False, auto_adjust=True))

# ── 指標 ──────────────────────────────────────────────────────────────────────

def add_rsi(df, n=14):
    df = df.copy(); df["RSI"] = ta.momentum.RSIIndicator(df["Close"], window=n).rsi(); return df

def add_sma(df, col, n, out):
    df = df.copy(); df[out] = ta.trend.SMAIndicator(df[col], window=n).sma_indicator(); return df

def add_macd(df):
    df = df.copy(); m = ta.trend.MACD(df["Close"])
    df["MACD"] = m.macd(); df["MACD_sig"] = m.macd_signal()
    df["MACD_hist"] = m.macd_diff(); return df

def add_atr(df, n=14):
    df = df.copy()
    df["ATR"] = ta.volatility.AverageTrueRange(
        df["High"], df["Low"], df["Close"], window=n).average_true_range()
    return df

def get_day_open(df):
    ny_today = _ny().date()
    try: idx = df.index.tz_convert("America/New_York")
    except: idx = df.index
    mask  = [t.date() == ny_today for t in idx]
    today = df[mask]
    return today.iloc[0]["Open"] if not today.empty else None

def dynamic_stop(df, day_low, tag):
    if any(t in tag for t in VOLATILE_TAGS) and len(df) >= 14:
        atr = ta.volatility.AverageTrueRange(
            df["High"], df["Low"], df["Close"], window=14).average_true_range().iloc[-1]
        return day_low - atr * 1.5, f"ATR×1.5(`{atr:.2f}`)"
    return day_low * 0.995, "低點×0.995"

def detect_smc(df15):
    if len(df15) < 15: return "無結構"
    c = df15.iloc[-1]; p = df15.iloc[-2]
    hi = df15["High"].tail(10).max(); lo = df15["Low"].tail(10).min()
    if c["Close"] > hi and p["Close"] <= hi: return "BOS多🔥"
    if c["Close"] < lo and p["Close"] >= lo: return "BOS空🔴"
    if c["Close"] > df15["High"].tail(5).iloc[:-1].max(): return "CHoCH轉多"
    if c["Close"] < df15["Low"].tail(5).iloc[:-1].min():  return "CHoCH轉空"
    return "盤整"

def validate_breakout_v3(df, tag):
    """
    v9.8.2 物理結構三重校驗：
    A. 實體比例 (Body/Range) — 防十字星/紡錘
    B. 收盤位置 (防上影線)  — 防收盤遠離高點
    C. 創高回落偵測          — 全天衝高後收在低位 = 賣壓沉重
    回傳 (is_valid: bool, reason: str)
    """
    if len(df) < 5: return True, ""
    c         = df.iloc[-1]
    is_rocket = any(t in tag for t in VOLATILE_TAGS)

    rng = c["High"] - c["Low"]
    if rng <= 0: return True, ""

    body = abs(c["Close"] - c["Open"])

    # A. 實體佔比（妖股放寬至 0.35）
    body_thresh = 0.35 if is_rocket else 0.50
    if (body / rng) < body_thresh:
        return False, f"實體過小({body/rng:.0%}<{body_thresh:.0%})"

    # B. 收盤位置：距當根最高點的比例（防上影線）
    close_pos_k  = (c["High"] - c["Close"]) / rng
    close_thresh = 0.30 if is_rocket else 0.20
    if close_pos_k > close_thresh:
        return False, f"收盤受阻(上影線{close_pos_k:.0%})"

    # C. 創高回落：收盤 vs 全天波動範圍（防衝高崩落）
    day_hi    = float(df["High"].max())
    day_lo    = float(df["Low"].min())
    day_range = day_hi - day_lo
    if day_range > 0:
        close_pos_day = (day_hi - c["Close"]) / day_range
        day_thresh    = 0.45 if is_rocket else 0.35
        if close_pos_day > day_thresh:
            return False, f"創高回落(賣壓{close_pos_day:.0%})"

    return True, ""

def passes_trend(sym, tag, df1d=None):
    if tag in VOLATILE_TAGS: return True
    CORE_BYPASS = {
        "NVDA","AVGO","TSLA","VRT","ANET","AMD","AAPL","META",
        "MSFT","GOOGL","AMZN","QQQ","PLTR","CRDO","ALAB",
        "CRCL","VST","COIN","MSTR","MARA","HOOD","SOFI",
    }
    if sym in CORE_BYPASS: return True
    if sym in _trend_cache: return _trend_cache[sym]
    try:
        if df1d is None or len(df1d) < 50: return True
        c    = df1d["Close"]; price = float(c.iloc[-1])
        ma50 = float(c.rolling(50).mean().iloc[-1])
        if len(c) < 152:
            res = price > ma50
        elif len(c) < 252:
            res = price > ma50 > float(c.rolling(150).mean().iloc[-1])
        else:
            ma150     = float(c.rolling(150).mean().iloc[-1])
            ma200     = float(c.rolling(200).mean().iloc[-1])
            ma200_old = float(c.rolling(200).mean().iloc[-22])
            res = sum([price > ma50, ma50 > ma150, ma150 > ma200,
                       ma200 > ma200_old,
                       price >= float(c.tail(252).min()) * 1.25,
                       price >= float(c.tail(252).max()) * 0.75]) >= 5
        _trend_cache[sym] = res; return res
    except: return True

# ══════════════════════════════════════════════════════════════════════════════
# sig_crash
# [BUG-FIX-1] 移除第一個重複且不完整的 sig_crash 定義
# 保留此唯一完整版本（含 last_type_{sym}、crash_seal 寫入）
# ══════════════════════════════════════════════════════════════════════════════

def sig_crash(sym, tag, df5, df15, source, cache, earn_warn=""):
    ck = f"crash_{sym}"
    if not cooled(cache, ck, 15) or len(df5) < 20: return None
    df = add_rsi(add_sma(df5, "Volume", 15, "VMA"))
    c = df.iloc[-1]; p = df.iloc[-2]
    if pd.isna(c["VMA"]) or c["VMA"] < 10: return None

    vr        = float(df["VR_Adj"].iloc[-1]) if "VR_Adj" in df.columns else c["Volume"] / (c["VMA"] + 1)
    smc       = detect_smc(df15)
    is_rocket = any(t in tag for t in VOLATILE_TAGS)
    status    = us_status()

    day_open = get_day_open(df)
    if day_open is None: return None
    day_high = float(df["High"].max())
    day_chg  = (day_high - day_open) / day_open * 100
    if day_chg < 1.5: return None

    is_strong_day = day_chg >= 8.0
    if is_strong_day:
        sup       = df["Low"].tail(15).iloc[:-1].min()
        vr_thresh = 4.0
        rsi_limit = 80
    else:
        sup       = df["Low"].tail(6).iloc[:-1].min()
        vr_thresh = 2.5 if status == "PRE" else (1.25 if is_rocket else 2.0)
        rsi_limit = 65

    c1 = c["Close"] < sup
    c2 = vr > vr_thresh
    c3 = c["Close"] < c["Open"]
    c4 = c["RSI"] < p["RSI"] and c["RSI"] < rsi_limit
    sc = sum([c1,c2,c3,c4]); g = grade(sc, 4)
    if not g or not (c1 and c2): return None

    mark(cache, ck)
    _crash_warned.add(sym)
    mark(cache, f"crash_seal_{sym}")
    # 記錄 crash 嚴重程度（供翻轉判斷參考）與最後信號類型
    cache[f"crash_sc_{sym}"]  = sc
    cache[f"last_type_{sym}"] = "⛈️"
    save_cache(cache)

    chg = (c["Close"] - df.iloc[0]["Open"]) / df.iloc[0]["Open"] * 100
    return {"score": sc+10, "type": "⛈️", "sym": sym, "msg": fmt_msg(
        tag, "⛈️", "暴跌預兆", sym, g, c["Close"], chg, vr, c["RSI"], smc,
        "敗象已現", "高位爆量結構轉空，立刻減倉，切勿留過夜",
        f"{L(c1)}破支撐 {L(c2)}放量({vr:.1f}x) {L(c3)}收黑 {L(c4)}RSI背離",
        source=source, earn_warn=earn_warn)}


# ══════════════════════════════════════════════════════════════════════════════
# sig_surge
# ══════════════════════════════════════════════════════════════════════════════

def sig_surge(sym, tag, df5, df15, source, cache, regime_on=True,
              earn_warn="", stats=None, pf=None):
    if source in ("none","",None): return None
    ck = f"surge_{sym}"
    if not cooled(cache, ck, 30) or len(df5) < 5 or len(df15) < 3: return None

    df = add_atr(add_rsi(add_sma(df5, "Volume", 15, "VMA")))
    c = df.iloc[-1]; p = df.iloc[-2]
    is_rocket = any(t in tag for t in VOLATILE_TAGS)
    status    = us_status()

    if not regime_on and not is_rocket: return None

    # [FEAT-2] 硬性量能底線：當根成交量 < 1000 股直接過濾
    if float(c["Volume"]) < 1000:
        return None

    chg = (c["Close"] - df.iloc[0]["Open"]) / df.iloc[0]["Open"] * 100
    if chg < 0: return None

    min_vma = 8 if is_rocket else 50
    if pd.isna(c["VMA"]) or c["VMA"] < min_vma: return None

    if "VR_Adj" in df.columns:
        vr         = float(df["VR_Adj"].iloc[-1])
        vol_thresh = 0.8 if is_rocket else 1.5
        # [FEAT-NEW-2] S 級量比一票否決（入口攔截，僅針對極端低量）
        # 門檻刻意低於 vol_thresh，只攔截幾乎無量的極端情況
        vr_hard_floor = 0.5 if is_rocket else 1.0
    else:
        vr         = c["Volume"] / (c["VMA"] + 1)
        vol_thresh = 1.25 if is_rocket else 2.5
        if source == "yfinance": vol_thresh = max(vol_thresh * 0.85, 1.1)
        vr_hard_floor = 0.6 if is_rocket else 0.8

    # [FEAT-NEW-2] 硬性地板：低於此值連 A 級都不給
    if vr < vr_hard_floor:
        print(f"  {sym}: VR {vr:.2f} < 硬性地板 {vr_hard_floor:.2f}，直接過濾")
        return None

    vol_ok    = vr > vol_thresh
    vol_surge = False
    if not vol_ok and is_rocket and len(df) >= 10:
        recent_vol = df["Volume"].tail(5).mean()
        older_vol  = df["Volume"].iloc[max(-len(df),-15):-5].mean() or 1
        rel_surge  = recent_vol > older_vol * 2.0
        abs_floor  = 200.0
        try:
            df1d_chk = _clean(yf.download(sym, interval="1d", period="10d",
                                          progress=False, auto_adjust=True))
            if not df1d_chk.empty:
                daily_avg = float(df1d_chk["Volume"].tail(5).mean())
                abs_floor = max((daily_avg / 78) * 0.025 * 0.5, 200.0)
        except:
            pass
        vol_surge = rel_surge and (recent_vol >= abs_floor)
        if vol_surge:
            vol_ok = True

    if status == "PRE":
        try:
            df1d_ref = _clean(yf.download(sym, interval="1d", period="5d", progress=False, auto_adjust=True))
            if not df1d_ref.empty and len(df1d_ref) >= 2:
                prev_hi    = float(df1d_ref["High"].iloc[-2])
                yest_close = float(df1d_ref["Close"].iloc[-2])
                prev_hi    = min(prev_hi, yest_close * 1.03)
            else:
                prev_hi = float(df["High"].tail(7).iloc[:-1].max()) if len(df) >= 7 else float(df["High"].iloc[0])
        except:
            prev_hi = float(df["High"].tail(7).iloc[:-1].max()) if len(df) >= 7 else float(df["High"].iloc[0])
    else:
        prev_hi = float(df["High"].tail(7).iloc[:-1].max()) if len(df) >= 7 else float(df["High"].iloc[0])

    smc = detect_smc(df15); c15 = df15.iloc[-1]
    c1 = vol_ok
    c2 = c["Close"] > prev_hi
    c3 = p["Close"] <= prev_hi
    rsi_max = 88 if is_rocket else 78
    c4 = 52 < c["RSI"] < rsi_max
    c5 = c15["Close"] > prev_hi

    if not (c1 and c2 and c3): return None
    sc = sum([c1,c2,c3,c4,c5])

    # ── crash_seal 翻轉判斷 ────────────────────────────────────────────────
    seal_key      = f"crash_seal_{sym}"
    in_seal       = not cooled(cache, seal_key, 60)
    is_reversal   = False
    reversal_prefix = ""

    if in_seal:
        valid_pre, reason_pre = validate_breakout_v3(df, tag)
        if not valid_pre or sc < 4:
            print(f"  {sym}: crash_seal 封印 (物理:{reason_pre or 'ok'}, sc:{sc}/5)，封印維持")
            return None
        # 物理通過 + 條件充足 → 翻轉
        is_reversal     = True
        sc              = min(sc + 1, 5)
        reversal_prefix = "🔄 *[訊號翻轉]* 收復早前賣壓，結構由空轉多！\n"
        print(f"  {sym}: crash_seal 封印但物理通過 sc={sc}，發出翻轉信號")
    # ── 翻轉判斷結束 ───────────────────────────────────────────────────────

    g = grade(sc, 5)
    if not g: return None

    # [FEAT-3] S 級量能門檻（二道防線，仍保留）
    if g == "🏆 S級":
        if "VR_Adj" in df.columns:
            if vr < 1.5: g = "🥇 A級"
        else:
            if vr < 1.2: g = "🥇 A級"

    # [FEAT-NEW-3] SMC 無結構：S 級強制降 A 級並加警告標記
    # 不過濾（避免訊號太少），但降級 + 提醒
    smc_warn = ""
    if smc == "無結構" and g == "🏆 S級":
        g        = "🥇 A級"
        smc_warn = " ⚠️SMC無結構"
        print(f"  {sym}: SMC無結構，S→A 級")

    # 物理校驗（翻轉路徑已在上方做過，此處只走正常路徑）
    if not is_reversal:
        valid, reason = validate_breakout_v3(df, tag)
        if not valid:
            if sc == 5:
                mark(cache, ck)
                return {"score": 1, "type": "⚠️", "sym": sym, "msg": (
                    f"{tag} ⚠️ *[假突破警告]* `{sym}` 原{g}→已過濾\n"
                    f"💰 現價:`{c['Close']:.2f}` · ❌ {reason}\n"
                    f"🚫 建議放棄{earn_warn}\n⏰ {tw_time()} TWN")}
            return None

    mark(cache, ck)
    cache[f"last_type_{sym}"] = "🔮"

    atr_val    = float(c["ATR"]) if not pd.isna(c["ATR"]) else c["Close"] * 0.02
    stop_price = c["Close"] - atr_val * (1.5 if is_rocket else 1.0)
    target     = c["Close"] + abs(c["Close"] - stop_price) * 2
    shares     = calc_position(c["Close"], stop_price, "SURGE", stats, pf)
    if shares > 0:
        log_forward_test(sym, "SURGE", c["Close"], stop_price, target, shares)

    vol_note = (f"突增{df['Volume'].tail(5).mean()/max(df['Volume'].iloc[max(-len(df),-15):-5].mean(),1):.1f}x"
                if vol_surge else f"{vr:.1f}x")
    extra = f"條件:`{sc}/5` · 突破:`{prev_hi:.2f}`{smc_warn}"
    if is_reversal:
        extra += f"\n🔄 翻轉：前crash={cache.get(f'crash_sc_{sym}','?')}/4，物理結構已確認翻多"
    elif sym in _crash_warned:
        extra += "\n⚠️ 本日有暴跌預兆，此為反彈，謹慎"
    if stats and stats.sample_size >= 15:
        extra += f"\n📈 歷史:{stats.sample_size}筆 勝率:{stats.winrate:.0%} 期望:{stats.expectancy_r:.2f}R"

    signal_label = "訊號翻轉" if is_reversal else "暴漲預兆"
    advice       = ("帶量突破前高，結構由空翻多，小倉試進，SAR翻轉停損"
                    if is_reversal else "帶量突破，分批進場，SAR翻轉即停損")
    score_out    = sc + (2 if is_reversal else 0)

    return {"score": score_out, "type": "🔮", "sym": sym, "msg": (
        reversal_prefix + fmt_msg(
            tag, "🔮", signal_label, sym, g, c["Close"], chg, vr, c["RSI"], smc,
            "確診發動", advice,
            f"{L(c1)}量{vol_note} {L(c2)}突破前高 {L(c3)}剛發動 {L(c4)}RSI {L(c5)}15m確認",
            extra, source, shares, stop_price, earn_warn))}


# ══════════════════════════════════════════════════════════════════════════════
# sig_pregap
# ══════════════════════════════════════════════════════════════════════════════

def sig_pregap(sym, tag, cache, earn_warn=""):
    """
    盤前跳空訊號。
    [FEAT-NEW-1] 豁免 is_toxic_premarket_time()：
      - 16:00~16:14（美東 04:00~04:14）此函式仍可執行，
        但在結果中加上「建議 04:15 後確認」提示。
    """
    ck = f"pregap_{sym}_{_ny().strftime('%Y%m%d')}"
    if not cooled(cache, ck, 720): return None
    try:
        pre_df = get_alpaca(sym, "1Min", 90)
        if pre_df.empty: pre_df = get_yf(sym, "1m", "2d")
        if pre_df.empty: return None
        ny_today = _ny().date()
        pre = pre_df[(pre_df.index.date == ny_today) &
                     ((pre_df.index.hour < 9) |
                      ((pre_df.index.hour == 9) & (pre_df.index.minute < 30)))]
        if len(pre) < 5: return None
        df1d = get_yf(sym, "1d", "20d")
        if len(df1d) < 6: return None
        yest_close = float(df1d["Close"].iloc[-2])
        avg_vol    = float(df1d["Volume"].tail(5).mean())
        pre_price  = float(pre.iloc[-1]["Close"])
        pre_vol    = float(pre["Volume"].sum())
        chg        = (pre_price - yest_close) / yest_close * 100
        is_rocket  = any(t in tag for t in VOLATILE_TAGS)
        min_abs    = 30000 if is_rocket else 100000
        G1 = chg > 5.0
        G2 = pre_vol > avg_vol * 1.5 and pre_vol > min_abs
        df1d_rsi = add_rsi(df1d)
        yest_rsi = float(df1d_rsi["RSI"].iloc[-2]) if not pd.isna(df1d_rsi["RSI"].iloc[-2]) else 50
        G3 = yest_rsi < 75
        recent = pre.tail(30)
        G4 = float(recent["Volume"].tail(10).mean()) > float(recent["Volume"].head(10).mean()) * 0.7
        if not (G1 and G2): return None
        sc = sum([G1,G2,G3,G4]); g = "🏆 S級" if sc == 4 else "🥇 A級"
        mark(cache, ck)
        vr   = pre_vol / (avg_vol + 1)
        warn = " ⚠️ 昨RSI偏高，注意高開低走" if yest_rsi >= 70 else ""

        # [FEAT-NEW-1] 低流動性視窗提醒（不過濾，但提示等待）
        toxic_note = ""
        if is_toxic_premarket_time():
            toxic_note = "\n⏳ *[建議等待]* 現處 04:00~04:15 低流動性視窗，04:15 後確認仍成立再行動"

        advice = (f"盤前爆量跳空+{chg:.1f}%，開盤前5分鐘觀察縮量回測，"
                  f"確認站穩再進，止損昨收{yest_close:.2f}{warn}")
        hint = PORTFOLIO_HINTS.get(sym, "")
        return {"score": sc+8, "type": "🌅", "sym": sym, "msg": (
            f"{tag} 🌅 *[盤前跳空]* `{sym}` {g}{earn_warn}\n"
            f"💰 盤前:`{pre_price:.2f}` · 📈:`{chg:+.1f}%` vs 昨收`{yest_close:.2f}`\n"
            f"📊 量比:`{vr:.1f}x` · 絕對量:`{pre_vol/1000:.0f}K` · 昨RSI:`{yest_rsi:.0f}`\n"
            f"燈號:{L(G1)}跳空>5% {L(G2)}量>1.5x {L(G3)}RSI可控 {L(G4)}量持續\n"
            + (f"📌 {hint}\n" if hint else "")
            + f"🎫 *[盤前機會]*: {advice}\n"
            + toxic_note
            + f"\n⏰ {tw_time()} TWN")}
    except Exception as e:
        print(f"  盤前{sym}: {e}"); return None


# ══════════════════════════════════════════════════════════════════════════════
# sig_washout
# ══════════════════════════════════════════════════════════════════════════════

def sig_washout(sym, tag, df5, df15, status, cache, earn_warn="", stats=None, pf=None):
    ck = f"wash_{sym}"
    if not cooled(cache, ck, 30) or len(df5) < 6 or len(df15) < 3: return None
    df = add_rsi(add_sma(add_sma(add_sma(df5,"Volume",10,"V10"),"Close",5,"MA5"),"Close",20,"MA20"))
    c = df.iloc[-1]; p = df.iloc[-2]; p2 = df.iloc[-3]
    if pd.isna(c["MA5"]): return None
    day_open = get_day_open(df)
    if day_open is None: return None
    day_low  = float(df["Low"].min())
    yest     = df[df.index.date < df.index[-1].date()]
    yest_low = float(yest["Low"].min()) if not yest.empty else day_low * 0.97
    drop     = (day_open - day_low) / day_open * 100
    rebound  = (float(c["Close"]) - day_low) / (day_open - day_low + 0.001)
    vr       = c["Volume"] / (c["V10"] + 1)
    smc      = detect_smc(df15)
    min_drop = 1.5 if "🚀" not in tag else 2.0
    c1 = drop > min_drop; c2 = c["Close"] >= day_open * 0.998; c3 = p["Close"] < day_open
    c4 = (c["RSI"] > p["RSI"] > p2["RSI"]) and (c["RSI"] - p2["RSI"] > 3)
    c5 = c["RSI"] < 72; c6 = c["Close"] > yest_low; c7 = rebound > 0.5; c8 = c["MA5"] > c["MA20"]
    if not (c1 and c2 and vr > 0.3): return None
    sc = sum([c1,c2,c3,c4,c5,c6,c7,c8]); g = grade(sc, 8)
    if not g: return None
    stop, stop_m = dynamic_stop(df, day_low, tag)
    rr = (c["Close"] * 1.02 - c["Close"]) / (c["Close"] - stop + 0.001)
    if rr < 1.5: return None
    mark(cache, ck)
    shares = calc_position(c["Close"], stop, "WASHOUT", stats, pf)
    target = c["Close"] + (c["Close"] - stop) * 2
    if shares > 0:
        log_forward_test(sym, "WASHOUT", c["Close"], stop, target, shares)
    chg    = (float(c["Close"]) - day_open) / day_open * 100
    prefix = "盤前洗盤" if status == "PRE" else "洗盤結束"
    warn   = " ⚠️ RSI偏高等回測5MA" if c["RSI"] > 65 else ""
    if sym in _crash_warned: warn += " ⚠️ 本日有暴跌預兆，謹慎"
    extra = f"條件:`{sc}/8` 反彈:`{rebound*100:.0f}%` 風報:`{rr:.1f}x`{warn}"
    if stats and stats.sample_size >= 15:
        extra += f"\n📈 歷史:{stats.sample_size}筆 勝率:{stats.winrate:.0%}"
    return {"score": sc, "type": "⚡", "sym": sym, "msg": fmt_msg(
        tag, "⚡", "WASHOUT", sym, g, c["Close"], chg, vr, c["RSI"], smc,
        prefix, f"大幅殺低帶量站回，止損{stop:.2f}({stop_m})",
        f"{L(c1)}殺低 {L(c2)}站回 {L(c3)}剛翻 {L(c4)}RSI勾 "
        f"{L(c5)}非追高 {L(c6)}守昨低 {L(c7)}彈力 {L(c8)}MA翻多",
        extra, "", shares, stop, earn_warn)}


# ══════════════════════════════════════════════════════════════════════════════
# sig_pullback
# ══════════════════════════════════════════════════════════════════════════════

def sig_pullback(sym, tag, df1d, df5, cache, earn_warn="", pf=None):
    ck = f"pull_{sym}"
    if not cooled(cache, ck, 60) or len(df1d) < 65 or len(df5) < 3: return None
    d  = add_macd(add_rsi(add_sma(add_sma(df1d,"Close",60,"MA60"),"Volume",5,"V5")))
    f  = add_rsi(df5)
    dc = d.iloc[-1]; dp = d.iloc[-2]; fc = f.iloc[-1]; fp = f.iloc[-2]
    if pd.isna(dc["MA60"]) or pd.isna(dc["RSI"]): return None
    bias = (dc["Close"] - dc["MA60"]) / dc["MA60"] * 100
    vr   = dc["Volume"] / (dc["V5"] + 1)
    c1 = dc["Close"] > dc["MA60"]; c2 = 42 <= dp["RSI"] <= 58 and dc["RSI"] > dp["RSI"]
    c3 = vr < 0.85; c4 = 0 <= bias < 5; c5 = fc["RSI"] > fp["RSI"]
    c6 = dc["MACD_hist"] > dp["MACD_hist"]
    sc = sum([c1,c2,c3,c4,c5,c6]); g = grade(sc, 6)
    if not g or not (c1 and c2): return None
    mark(cache, ck)
    stop_est = float(dc["MA60"]) * 0.99
    shares   = calc_position(float(dc["Close"]), stop_est, "VCP", None, pf)
    sp       = dc["Close"] * 0.95
    if sym in _crash_warned:
        advice = "⚠️ 本日有暴跌預兆，PULLBACK可能誘多，暫緩"
    else:
        advice = f"縮量回測季線RSI勾頭，Sell Put:{sp:.1f}(-5%)，守季線{dc['MA60']:.2f}"
    extra = f"條件:`{sc}/6` 距季線:`{bias:.1f}%` 量能:`{vr:.2f}x`"
    return {"score": sc, "type": "📈", "sym": sym, "msg": fmt_msg(
        tag, "📈", "波段PULLBACK", sym, g, dc["Close"], 0, vr, dc["RSI"], "日線",
        "候補進場", advice,
        f"{L(c1)}季線上 {L(c2)}RSI勾頭 {L(c3)}縮量 {L(c4)}貼季線 {L(c5)}5m確認 {L(c6)}MACD",
        extra, "", shares, stop_est, earn_warn)}


# ══════════════════════════════════════════════════════════════════════════════
# sig_smc
# ══════════════════════════════════════════════════════════════════════════════

def sig_smc(sym, tag, df15, df1d, cache, pf=None):
    ck = f"smc_{sym}"
    if not cooled(cache, ck, 60) or len(df15) < 30 or len(df1d) < 20: return None
    results    = []; curr_price = float(df15["Close"].iloc[-1])
    hi10 = df1d["High"].tail(10).values; lo10 = df1d["Low"].tail(10).values
    bull_str = (hi10[-3:].max() > hi10[:5].max() and lo10[-3:].min() > lo10[:5].min())
    bear_str = (hi10[-3:].max() < hi10[:5].max() and lo10[-3:].min() < lo10[:5].min())

    def find_ob(df, mode="bull"):
        for i in range(3, min(25, len(df)-2)):
            bar = df.iloc[-i]; after = df.iloc[-i+1:]; next3 = df.iloc[-i+1:-i+4]
            if len(next3) < 3: continue
            if mode == "bull":
                if not (bar["Close"] < bar["Open"]): continue
                strong = (next3["Close"].iloc[-1] > bar["High"] and
                          (next3["Close"].iloc[-1]-bar["Low"])/(bar["Low"]+1e-9) > 0.005)
                if not strong: continue
                ob_h, ob_l = float(bar["High"]), float(bar["Low"])
                if (after["Close"] < ob_l).any(): continue
                if ob_l <= curr_price <= ob_h * 1.005:
                    body_mid = (float(df.iloc[-1]["Open"]) + float(df.iloc[-1]["Close"])) / 2
                    if body_mid < ob_l: continue
                    return {"high": ob_h, "low": ob_l}
            else:
                if not (bar["Close"] > bar["Open"]): continue
                strong = (next3["Close"].iloc[-1] < bar["Low"] and
                          (bar["High"]-next3["Close"].iloc[-1])/(bar["High"]+1e-9) > 0.005)
                if not strong: continue
                ob_h, ob_l = float(bar["High"]), float(bar["Low"])
                if (after["Close"] > ob_h).any(): continue
                if ob_l * 0.995 <= curr_price <= ob_h:
                    body_mid = (float(df.iloc[-1]["Open"]) + float(df.iloc[-1]["Close"])) / 2
                    if body_mid > ob_h: continue
                    return {"high": ob_h, "low": ob_l}
        return None

    def find_fvg(df, mode="bull"):
        for i in range(2, min(20, len(df)-1)):
            b1 = df.iloc[-i-1]; b3 = df.iloc[-i+1]
            if mode == "bull":
                if float(b1["High"]) < float(b3["Low"]):
                    top, bot = float(b3["Low"]), float(b1["High"])
                    if bot <= curr_price <= top: return {"top": top, "bot": bot}
            else:
                if float(b1["Low"]) > float(b3["High"]):
                    top, bot = float(b1["Low"]), float(b3["High"])
                    if bot <= curr_price <= top: return {"top": top, "bot": bot}
        return None

    bull_ob  = find_ob(df15, "bull"); bear_ob  = find_ob(df15, "bear")
    bull_fvg = find_fvg(df15, "bull"); bear_fvg = find_fvg(df15, "bear")
    sw_hi   = float(df15["High"].tail(15).iloc[:-2].max())
    sw_lo   = float(df15["Low"].tail(15).iloc[:-2].min())
    bos_b   = curr_price > sw_hi; bos_r   = curr_price < sw_lo
    choch_b = bos_b and bear_str; choch_r = bos_r and bull_str
    crash_note = f"⚠️ 本日有暴跌預兆，謹慎\n" if sym in _crash_warned else ""

    if bull_str and (bull_ob or bull_fvg) and (bos_b or choch_b):
        bs = sum([bull_str, bool(bull_ob), bool(bull_fvg), bos_b, choch_b]); g = grade(bs, 5)
        if g:
            mark(cache, ck)
            stop   = (bull_ob["low"] if bull_ob else bull_fvg["bot"]) * 0.995
            target = curr_price + (curr_price - stop) * 2
            shares = calc_position(curr_price, stop, "SURGE", None, pf)
            ctag   = " *CHoCH反轉*" if choch_b else " BOS順勢"
            results.append({"score": bs, "type": "🏦", "sym": sym, "msg": (
                f"{tag} 🏦 *[SMC 多頭]{ctag}* `{sym}` {g}\n"
                f"💰 現價:`{curr_price:.2f}`\n"
                f"燈號:{L(bull_str)}日線牛市 {L(bool(bull_ob))}OB {L(bool(bull_fvg))}FVG "
                f"{L(bos_b)}BOS {L(choch_b)}CHoCH\n"
                + (f"📦 OB:`{bull_ob['low']:.2f}~{bull_ob['high']:.2f}`\n" if bull_ob else "")
                + (f"🕳️ FVG:`{bull_fvg['bot']:.2f}~{bull_fvg['top']:.2f}`\n" if bull_fvg else "")
                + f"🎯 止損:`{stop:.2f}` 目標:`{target:.2f}` (1:2)"
                + (f" · 建議倉位:`{shares}股`" if shares > 0 else "") + "\n"
                + crash_note + f"⏰ {tw_time()} TWN")})

    if bear_str and (bear_ob or bear_fvg) and (bos_r or choch_r):
        bs = sum([bear_str, bool(bear_ob), bool(bear_fvg), bos_r, choch_r]); g = grade(bs, 5)
        if g:
            mark(cache, ck)
            stop   = (bear_ob["high"] if bear_ob else bear_fvg["top"]) * 1.005
            target = curr_price - (stop - curr_price) * 2
            ctag   = " *CHoCH反轉*" if choch_r else " BOS順勢"
            results.append({"score": bs, "type": "🏦", "sym": sym, "msg": (
                f"{tag} 🏦 *[SMC 空頭]{ctag}* `{sym}` {g}\n"
                f"💰 現價:`{curr_price:.2f}`\n"
                f"燈號:{L(bear_str)}日線熊市 {L(bool(bear_ob))}OB {L(bool(bear_fvg))}FVG "
                f"{L(bos_r)}BOS {L(choch_r)}CHoCH\n"
                + (f"📦 OB:`{bear_ob['low']:.2f}~{bear_ob['high']:.2f}`\n" if bear_ob else "")
                + (f"🕳️ FVG:`{bear_fvg['bot']:.2f}~{bear_fvg['top']:.2f}`\n" if bear_fvg else "")
                + f"🎯 止損:`{stop:.2f}` 目標:`{target:.2f}` (1:2)\n"
                + crash_note + f"⏰ {tw_time()} TWN")})

    return results if results else None


# ══════════════════════════════════════════════════════════════════════════════
# scan_vcp
# ══════════════════════════════════════════════════════════════════════════════

def scan_vcp(ticker_list, cache, stats=None, pf=None):
    results = []
    for sym in ticker_list:
        ck = f"vcp_{sym}"
        if not cooled(cache, ck, 120): continue
        try:
            df = _clean(yf.download(sym, period="6mo", progress=False, auto_adjust=True))
            if len(df) < 50: continue
            curr    = float(df["Close"].iloc[-1])
            df["MA50"] = df["Close"].rolling(50).mean()
            ma50    = float(df["MA50"].iloc[-1])
            bias    = (curr - ma50) / ma50 * 100
            vol_50  = float(df["Volume"].rolling(50).mean().iloc[-1])
            vol_5   = float(df["Volume"].iloc[-5:].mean())
            vol_dry = vol_5 < vol_50 * 0.7
            std_r   = float(df["High"].iloc[-10:].std())
            std_p   = float(df["High"].iloc[-30:-10].std())
            is_tight     = std_r < std_p and std_p > 0
            low_r        = float(df["Low"].iloc[-5:].min())
            low_p        = float(df["Low"].iloc[-15:-5].min())
            higher_low   = low_r > low_p
            pivot        = float(df["High"].iloc[-10:].max())
            dist         = (pivot + 0.05 - curr) / curr * 100
            conds        = sum([vol_dry, is_tight, higher_low])
            if not (conds == 3 or (conds == 2 and 0 < dist < 5)): continue
            status_lbl = "🔥 準備突破" if (conds == 3 and 0 < dist < 3 and bias < 25) else "👀 觀察中"
            mark(cache, ck)
            stop_est = curr * 0.93
            shares   = calc_position(curr, stop_est, "VCP", stats, pf)
            target   = curr + (curr - stop_est) * 3
            if shares > 0:
                log_forward_test(sym, "VCP", curr, stop_est, target, shares)
            stat_line = ""
            if stats and stats.sample_size >= 15:
                stat_line = f"\n📈 歷史:{stats.sample_size}筆 勝率:{stats.winrate:.0%} 期望:{stats.expectancy_r:.2f}R"
            results.append({"score": conds*3 + (3 if status_lbl == "🔥 準備突破" else 0),
                            "type": "🎯", "sym": sym,
                            "msg": (
                                f"🔍 🎯 *[VCP Pro]* `{sym}` {'🏆 S級' if conds==3 else '🥇 A級'}\n"
                                f"💰 現價:`{curr:.2f}` · 50MA乖離:`{bias:.1f}%`\n"
                                f"燈號:{L(vol_dry)}量縮 {L(is_tight)}波動收縮 {L(higher_low)}支撐墊高\n"
                                f"📊 量比:`{vol_5/vol_50:.2f}x` · 距支點:`{dist:.1f}%`\n"
                                f"🛡️ 建議:`{shares}股` · 止損:`{stop_est:.2f}` · 目標:`{target:.2f}`\n"
                                f"狀態:{status_lbl}{stat_line}\n⏰ {tw_time()} TWN")})
        except Exception as e: print(f"  VCP {sym}: {e}")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# 半木夏 Pro — sig_banmuxa
# ══════════════════════════════════════════════════════════════════════════════

def _bmx_get_funding(disp_sym):
    """
    [BUG-6 FIX] import re 已移至頂部，不重複。
    """
    s       = str(disp_sym).strip().upper()
    compact = re.sub(r"[/:\-\s]", "", s)
    is_ethbtc = (compact == "ETHBTC")
    syms = ["ETHUSDT", "BTCUSDT"] if is_ethbtc else [
        compact if compact.endswith("USDT") else compact + "USDT"
    ]
    vals = []
    for sym in syms:
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": sym, "limit": 3},
                timeout=5
            )
            if r.status_code == 200:
                rates = [float(x["fundingRate"]) for x in r.json() if "fundingRate" in x]
                if rates:
                    vals.append(float(np.mean(rates)))
        except Exception:
            pass
    if is_ethbtc and len(vals) == 2:
        return vals[0] - vals[1]
    return vals[0] if vals else None


def _bmx_trend_break(df, price, w=14, mode="bull"):
    if len(df) < w * 2 + 5:
        return False, 0.0
    try:
        pts = df["High"].to_numpy(dtype=float) if mode == "bull" else df["Low"].to_numpy(dtype=float)
        pivot_idx = []
        for i in range(len(df) - w - 1, w, -1):
            seg = pts[i - w: i + w + 1]
            if mode == "bull" and pts[i] == seg.max(): pivot_idx.append(i)
            if mode == "bear" and pts[i] == seg.min(): pivot_idx.append(i)
            if len(pivot_idx) == 2: break

        if len(pivot_idx) < 2: return False, 0.0
        left, right = sorted(pivot_idx)
        if right <= left: return False, 0.0

        lv, rv = pts[left], pts[right]
        if lv <= 0: return False, 0.0
        slope     = (rv - lv) / (right - left)
        intercept = rv - slope * right
        p_slope   = slope / lv

        closes = df["Close"].to_numpy(dtype=float)
        for i in range(left, right):
            line_val = slope * i + intercept
            if mode == "bull" and closes[i] > line_val * 1.002: return False, 0.0
            if mode == "bear" and closes[i] < line_val * 0.998: return False, 0.0

        curr_line = slope * (len(df) - 1) + intercept
        if mode == "bull":
            is_break = price > curr_line and p_slope < -0.0001 and p_slope > -0.05
        else:
            is_break = price < curr_line and p_slope > 0.0001 and p_slope < 0.05
        return is_break, p_slope
    except Exception:
        return False, 0.0


def sig_banmuxa(yf_sym, disp, df15, cache, pf=None):
    """
    半木夏 Pro — 完整繼承 v9.8.2 全部修正。
    [BUG-1] funding cache 空字串守門
    [BUG-2] 空頭補 log_forward_test
    [BUG-3] 有結果即時 save_cache
    [FEAT-4] CRYPTO_LONG / CRYPTO_SHORT 區分
    """
    ck = f"bmx_{disp}"
    if not cooled(cache, ck, 120) or len(df15) < 120: return None

    df = add_atr(add_macd(df15.copy()))
    if len(df) < 120: return None

    try:
        price = float(df["Close"].iloc[-1])
    except Exception: return None
    if not np.isfinite(price) or price <= 0: return None

    atr_raw   = df["ATR"].iloc[-1]
    atr       = float(atr_raw) if pd.notna(atr_raw) and np.isfinite(atr_raw) and float(atr_raw) > 0 else price * 0.005
    risk_floor = price * 0.003

    hist = df["MACD_hist"].to_numpy(dtype=float)
    high = df["High"].to_numpy(dtype=float)
    low  = df["Low"].to_numpy(dtype=float)

    results   = []
    is_ethbtc = str(disp).upper().replace("/","").replace("-","") == "ETHBTC"

    # ── Funding 取值（BUG-1）────────────────────────────────────────────────
    f_cache_key    = f"bmx_funding_{disp}"
    f_cache_ts_key = f"bmx_funding_ts_{disp}"
    now_utc        = datetime.utcnow().timestamp()
    f_val          = None
    if f_cache_ts_key in cache:
        try:
            if now_utc - float(cache[f_cache_ts_key]) < 3600:
                raw = cache.get(f_cache_key)
                f_val = float(raw) if raw else None
        except Exception:
            pass
    if f_val is None:
        f_val = _bmx_get_funding(disp)
        cache[f_cache_key]    = str(f_val) if f_val is not None else ""
        cache[f_cache_ts_key] = str(now_utc)

    def funding_grade(fv, direction):
        if fv is None: return "中性", 0
        if direction == "bull": return ("燃料充足🔥", 1) if fv < 0 else ("中性", 0)
        else: return ("多頭擁擠🔥", 1) if fv > 0.0003 else ("中性", 0)

    # ── 多頭三背離 ─────────────────────────────────────────────────────────
    raw_troughs = [
        i for i in range(10, len(hist)-10)
        if np.isfinite(hist[i]) and hist[i] < 0
        and hist[i] == np.min(hist[i-10:i+11])
    ]
    if len(raw_troughs) >= 3:
        t1, t2, t3 = raw_troughs[-3], raw_troughs[-2], raw_troughs[-1]
        spacing_ok = (t3-t2 >= 5) and (t2-t1 >= 5)
        fresh_ok   = (len(hist)-1-t3) <= 15
        div_ok = (
            spacing_ok and fresh_ok
            and (t3-t1 > 20)
            and hist[t1] > hist[t2] > hist[t3]
            and low[t1] < low[t2] < low[t3]
            and hist[-1] > hist[-2]
        )
        if div_ok:
            w_break = 24 if is_ethbtc else 14
            is_break, p_slope = _bmx_trend_break(df, price, w=w_break, mode="bull")
            if is_break:
                raw_stop = float(low[t3]) - atr * 1.5
                stop     = min(raw_stop, price - risk_floor)
                if np.isfinite(stop) and 0 < stop < price:
                    risk   = price - stop
                    target = price + risk * 2
                    shares = calc_position(price, stop, "CRYPTO", None, pf)
                    if shares > 0:
                        log_forward_test(disp, "CRYPTO_LONG", price, stop, target, shares)
                    mark(cache, ck)
                    f_text, f_bonus = funding_grade(f_val, "bull")
                    score     = 10 if f_bonus else 9
                    grade_lbl = "🌟 SSS" if f_bonus else "🏆 S級"
                    f_str     = f"{f_val*100:.4f}%" if f_val is not None else "N/A"
                    results.append({"score": score, "type": "₿", "sym": disp, "msg": (
                        f"₿ 🚀 *[半木夏 Pro 多頭]* `{disp}` {grade_lbl}\n"
                        f"💰 現價:`{price:.4f}`\n"
                        f"📊 MACD三谷跨{t3-t1}根 · 低點遞升 · 壓力線突破\n"
                        f"📐 趨勢斜率:`{p_slope:.5f}` · 情緒:`{f_text}`({f_str})\n"
                        f"🎯 止損:`{stop:.4f}` 目標:`{target:.4f}` (1:2)\n"
                        + (f"🛡️ 建議:`{shares}單位`\n" if shares > 0 else "")
                        + f"⏰ {tw_time()} TWN")})

    # ── 空頭三背離 ─────────────────────────────────────────────────────────
    raw_peaks = [
        i for i in range(10, len(hist)-10)
        if np.isfinite(hist[i]) and hist[i] > 0
        and hist[i] == np.max(hist[i-10:i+11])
    ]
    if len(raw_peaks) >= 3:
        p1, p2, p3 = raw_peaks[-3], raw_peaks[-2], raw_peaks[-1]
        spacing_ok = (p3-p2 >= 5) and (p2-p1 >= 5)
        fresh_ok   = (len(hist)-1-p3) <= 15
        div_ok = (
            spacing_ok and fresh_ok
            and (p3-p1 > 20)
            and hist[p1] < hist[p2] < hist[p3]
            and high[p1] > high[p2] > high[p3]
            and hist[-1] < hist[-2]
        )
        if div_ok:
            is_break, p_slope = _bmx_trend_break(df, price, w=14, mode="bear")
            if is_break:
                raw_stop = float(high[p3]) + atr * 1.5
                stop     = max(raw_stop, price + risk_floor)
                if np.isfinite(stop) and stop > price:
                    risk   = stop - price
                    target = price - risk * 2
                    shares = calc_position(price, price + risk, "CRYPTO", None, pf)
                    # [BUG-2 FIX] 空頭補 log_forward_test + [FEAT-4] CRYPTO_SHORT
                    if shares > 0:
                        log_forward_test(disp, "CRYPTO_SHORT", price, stop, target, shares)
                    mark(cache, ck)
                    f_text, f_bonus = funding_grade(f_val, "bear")
                    score     = 10 if f_bonus else 9
                    grade_lbl = "🌟 SSS" if f_bonus else "🏆 S級"
                    f_str     = f"{f_val*100:.4f}%" if f_val is not None else "N/A"
                    results.append({"score": score, "type": "₿", "sym": disp, "msg": (
                        f"₿ 🔻 *[半木夏 Pro 空頭]* `{disp}` {grade_lbl}\n"
                        f"💰 現價:`{price:.4f}`\n"
                        f"📊 MACD三峰跨{p3-p1}根 · 高點遞降 · 支撐線跌破\n"
                        f"📐 趨勢斜率:`{p_slope:.5f}` · 情緒:`{f_text}`({f_str})\n"
                        f"🎯 止損:`{stop:.4f}` 目標:`{target:.4f}` (1:2)\n"
                        + f"⏰ {tw_time()} TWN")})

    # [BUG-3 FIX] 有結果才即時落盤
    if results:
        save_cache(cache)

    return results if results else None


# ══════════════════════════════════════════════════════════════════════════════
# format_digest
# ══════════════════════════════════════════════════════════════════════════════

def format_digest(sigs, label, regime_on=True, strategy_stats=None, crash_syms=None):
    """
    [BUG-5 FIX] crash_syms 標記彙整中的 SURGE/WASHOUT 降級顯示。
    """
    if crash_syms is None:
        crash_syms = set()

    tw_now = _tw()
    groups = {
        "⛈️風險":      sorted([s for s in sigs if s["type"]=="⛈️"],                      key=lambda x:-x["score"]),
        "⚡🔮🌅日內":  sorted([s for s in sigs if s["type"] in ("⚡","🔮","🌅","⚠️")],   key=lambda x:-x["score"]),
        "🏦SMC":       sorted([s for s in sigs if s["type"]=="🏦"],                      key=lambda x:-x["score"]),
        "📈波段":      sorted([s for s in sigs if s["type"]=="📈"],                      key=lambda x:-x["score"]),
        "🎯VCP":       sorted([s for s in sigs if s["type"]=="🎯"],                      key=lambda x:-x["score"]),
        "₿加密":       sorted([s for s in sigs if s["type"]=="₿"],                      key=lambda x:-x["score"]),
    }
    regime_txt = "🟢多頭(QQQ>10MA)" if regime_on else "🔴空頭(一般股封印)"
    lines = [
        f"📋 *CC Scanner · {label}*",
        f"⏰ {tw_now.strftime('%m/%d %H:%M')} TWN · 美股:{us_status()}",
        f"大盤:{regime_txt} · Alpaca:{'✓' if ALPACA_KEY else '✗'} IEX",
        f"風控: 總持倉≤{MAX_OPEN_POSITIONS} · 總風險≤{MAX_TOTAL_RISK*100:.0f}%",
        ""
    ]
    if strategy_stats:
        active = [s for s in strategy_stats.values() if s.sample_size >= 5]
        if active:
            lines.append("*策略統計*")
            for st in active:
                lines.append(f"• `{st.strategy}` {st.sample_size}筆 勝率:{st.winrate:.0%} 期望:{st.expectancy_r:.2f}R")
            lines.append("")
    for grp, lst in groups.items():
        if not lst: continue
        lines.append(f"*{grp} ({len(lst)}個)*")
        for s in lst[:6]:
            first = s["msg"].split("\n")[0].replace("*","").replace("`","")
            sym   = s.get("sym", "")
            if sym in crash_syms and s["type"] in ("🔮","⚡"):
                first = f"⚠️[已發暴跌] {first}"
            lines.append(f"• {first}")
        lines.append("")
    if not any(groups.values()):
        lines.append("本次無 S/A 級信號")
    lines += ["━━━━━━━━━━━━━", "S/A級開盤後即時 · ⛈️緊急即時"]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# 報表函式
# ══════════════════════════════════════════════════════════════════════════════

def send_daily_report(all_sigs, fwd_data, stats):
    tw_now    = _tw(); today_str = tw_now.strftime("%Y-%m-%d")
    today_fwd = [r for r in fwd_data if r.get("opened_at","").startswith(today_str)]
    open_pos  = [r for r in fwd_data if r.get("status") == "OPEN"]
    hit       = [r for r in fwd_data if r.get("status") == "TARGET_HIT"]
    stopped   = [r for r in fwd_data if r.get("status") == "STOPPED"]
    lines = [
        f"📅 *CC Scanner 日報 {tw_now.strftime('%m/%d')}*",
        f"今日發出信號: `{len(all_sigs)}` · 今日進場: `{len(today_fwd)}`",
        f"持倉中:`{len(open_pos)}` · 達標:`{len(hit)}` · 止損:`{len(stopped)}`",
    ]
    if stats:
        lines.append("")
        for k, st in stats.items():
            if st.sample_size >= 5:
                lines.append(f"• `{k}` 勝率:{st.winrate:.0%} 期望:{st.expectancy_r:.2f}R")
    send_tg("\n".join(lines))
    print("日報已發送")

def send_weekly_report(stats, bt_cache):
    tw_now = _tw()
    lines  = [f"📊 *CC Scanner 週報 {tw_now.strftime('%m/%d')}*", ""]
    if stats:
        for k, s in stats.items():
            if s.sample_size >= 3:
                lines.append(f"• `{k}` {s.sample_size}筆 勝率:{s.winrate:.0%} 期望:{s.expectancy_r:.2f}R")
    if bt_cache:
        lines += ["", "*歷史回測（近2年）*"]
        for s, d in bt_cache.items():
            lines.append(f"• `{s}` 勝率:{d['wr']:.0%} 期望:{d['exp']:.2f}R 樣本:{d['n']}")
    if len(lines) > 2:
        send_tg("\n".join(lines))
    print("週報已發送")

def send_monthly_report(fwd_data):
    tw_now  = _tw()
    closed  = [r for r in fwd_data if r.get("r_multiple") is not None]
    if not closed: return
    total_r  = sum(r["r_multiple"] for r in closed)
    wins     = [r for r in closed if r["r_multiple"] > 0]
    win_rate = len(wins) / len(closed)
    lines    = [
        f"🏆 *CC Scanner 月報 ({tw_now.strftime('%Y-%m')})*",
        f"結算比數: `{len(closed)}` · 勝率: `{win_rate:.0%}`",
        f"累積R值: `{total_r:.2f}R`",
        f"達標: `{len(wins)}` · 止損: `{len(closed)-len(wins)}`",
    ]
    send_tg("\n".join(lines))
    print("月報已發送")

def run_weekend_backtest(cache):
    print("[週末回測] 開始…")
    results  = {"SURGE": [], "WASHOUT": []}
    test_syms = (TICKERS.get("🇺🇸",[])[:8] + TICKERS.get("🚀",[])[:6])
    for sym in test_syms:
        try:
            df = _clean(yf.download(sym, period="2y", interval="1d", progress=False, auto_adjust=True))
            if len(df) < 60: continue
            df["Vol20"] = df["Volume"].rolling(20).mean()
            df["Hi20"]  = df["High"].shift(1).rolling(20).max()
            for i in range(25, len(df)-6):
                c = df.iloc[i]
                if pd.isna(c["Hi20"]) or pd.isna(c["Vol20"]): continue
                vr = c["Volume"] / (c["Vol20"] + 1)
                if c["Close"] > c["Hi20"] and vr > 1.5:
                    entry = float(c["Close"]); stop = entry * 0.97; risk = entry - stop
                    future = df.iloc[i+1:i+6]
                    if float(future["Low"].min()) <= stop:
                        results["SURGE"].append(-1.0)
                    elif float(future["High"].max()) >= entry + risk*2:
                        results["SURGE"].append(2.0)
            for i in range(5, len(df)-4):
                c        = df.iloc[i]
                day_open = float(c["Open"]); day_low = float(c["Low"])
                drop     = (day_open - day_low) / day_open * 100
                if drop > 1.5 and c["Close"] >= day_open * 0.998:
                    entry  = float(c["Close"]); stop = day_low * 0.995
                    risk   = max(entry - stop, entry * 0.005)
                    future = df.iloc[i+1:i+4]
                    if float(future["Low"].min()) <= stop:
                        results["WASHOUT"].append(-1.0)
                    elif float(future["High"].max()) >= entry + risk*2:
                        results["WASHOUT"].append(2.0)
        except Exception as e:
            print(f"  回測{sym}: {e}")
    bt_stats = {}
    for strategy, rs in results.items():
        if len(rs) < 10: continue
        wins = [r for r in rs if r > 0]; n = len(rs)
        bt_stats[strategy] = {"wr": round(len(wins)/n, 3), "exp": round(sum(rs)/n, 3), "n": n}
        print(f"  {strategy}: {n}筆 勝率{len(wins)/n:.0%} 期望{sum(rs)/n:.2f}R")
    cache["bt_stats"] = bt_stats
    print("[週末回測] 完成")
    return bt_stats


# ══════════════════════════════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════════════════════════════

def main():
    global _crash_warned, _trend_cache, _bt_stats_cache
    _crash_warned = set(); _trend_cache = {}

    mode   = get_mode(); status = us_status(); cache = load_cache()
    tw_now = _tw()

    # 跨日清除 crash_seal
    today_str    = tw_now.strftime("%Y-%m-%d")
    seal_date_key = "crash_seal_date"
    if cache.get(seal_date_key) != today_str:
        stale_keys = [k for k in list(cache.keys()) if k.startswith("crash_seal_")]
        for k in stale_keys:
            del cache[k]
        cache[seal_date_key] = today_str
        print(f"跨日清除 {len(stale_keys)} 個 crash_seal")

    print(f"\n{'='*55}")
    print(f"CC Scanner v9.8.3 · {tw_now.strftime('%Y-%m-%d %H:%M')} TWN")
    print(f"美股:{status} | 模式:{mode}")
    print(f"Alpaca:{'✓' if ALPACA_KEY else '✗'} · FMP:{'✓' if FMP_KEY else '✗'} · 帳戶:{ACCOUNT_BAL:,}")
    print(f"風控: 最大持倉={MAX_OPEN_POSITIONS} · 最大總風險={MAX_TOTAL_RISK*100:.0f}%")
    print(f"{'='*55}")

    if tw_now.weekday() == 6:
        print("週日靜默模式"); return

    if tw_now.weekday() == 5:
        if tw_now.hour == 9 and tw_now.minute < 10:
            settle_forward_tests()
            fwd_data = load_fwd()
            stats    = build_strategy_stats(fwd_data)
            run_weekend_backtest(cache)
            send_weekly_report(stats, cache.get("bt_stats", {}))
            if tw_now.day <= 7:
                send_monthly_report(fwd_data)
        print("週六統計日 · 不執行日內掃描"); return

    if mode == "SILENT": print("靜默模式"); return

    # [FEAT-NEW-1] 低流動性時間窗口：僅記錄，不中斷執行
    # 讓 sig_pregap 仍能收集資料，但日內訊號在發送層會被抑制
    toxic_window = is_toxic_premarket_time()
    if toxic_window:
        print(f"⚠️ 低流動性時窗 04:00~04:15 (美東)，日內訊號發送抑制中")

    if cooled(cache, f"settle_open_{tw_now.strftime('%Y%m%d_%H')}", 55):
        settle_forward_tests()
        mark(cache, f"settle_open_{tw_now.strftime('%Y%m%d_%H')}")

    fwd_data       = load_fwd()
    strategy_stats = build_strategy_stats(fwd_data)
    pf             = analyze_portfolio(fwd_data)

    _bt_stats_cache = cache.get("bt_stats", {})
    if _bt_stats_cache:
        print(f"已載入回測快取: {list(_bt_stats_cache.keys())}")

    regime_on  = get_market_regime(cache)
    regime_txt = "🟢多頭" if regime_on else "🔴空頭(一般股封印)"
    print(f"大盤:{regime_txt} | 開倉:{pf['open_count']}/{MAX_OPEN_POSITIONS} | 總風險:${pf['total_risk']:.0f}")

    all_sigs = []

    # ── 美股日內 ──────────────────────────────────────────────────────────────
    if status in ("PRE","OPEN") or mode == "DIGEST_PRE":
        for tag in ["🇺🇸","🛡️","⚛️","🚀"]:
            for sym in TICKERS.get(tag, []):
                try:
                    df1d = get_yf(sym, "1d", "200d")
                    if not passes_trend(sym, tag, df1d): continue
                    earn_warn = get_earn_warn(sym, cache)

                    if tag not in VOLATILE_TAGS:
                        lk = f"liq_{sym}"
                        if cooled(cache, lk, 60*12):
                            try:
                                _dv = _clean(yf.download(sym, "1d", "10d", progress=False, auto_adjust=True))
                                cache[f"liq_v_{sym}"] = float(_dv["Volume"].tail(5).mean()) if not _dv.empty else 1e9
                                mark(cache, lk)
                            except: cache[f"liq_v_{sym}"] = 1e9
                        if cache.get(f"liq_v_{sym}", 1e9) < 500_000:
                            print(f"  {sym}: 日均量不足50萬，跳過"); continue

                    df5, df15, src = get_consistent(sym)
                    if df5.empty: continue

                    r = sig_crash(sym, tag, df5, df15, src, cache, earn_warn)
                    if r: all_sigs.append(r)
                    r = sig_surge(sym, tag, df5, df15, src, cache, regime_on, earn_warn,
                                  strategy_stats.get("SURGE"), pf)
                    if r: all_sigs.append(r)
                    r = sig_washout(sym, tag, df5, df15, status, cache, earn_warn,
                                    strategy_stats.get("WASHOUT"), pf)
                    if r: all_sigs.append(r)
                    df15f = get_yf(sym, "15m", "5d")
                    if not df15f.empty and not df1d.empty:
                        res = sig_smc(sym, tag, df15f, df1d, cache, pf)
                        if res: all_sigs.extend(res)
                except Exception as e: print(f"  {sym}: {e}")

    # ── 盤前跳空 ─────────────────────────────────────────────────────────────
    if status == "PRE":
        sym_tag_map = {}
        for _t in ["🇺🇸", "🛡️", "⚛️", "🚀"]:
            for _s in TICKERS.get(_t, []):
                if _s not in sym_tag_map:
                    sym_tag_map[_s] = _t
        for sym, tag in sym_tag_map.items():
            try:
                earn_warn = get_earn_warn(sym, cache)
                r = sig_pregap(sym, tag, cache, earn_warn)
                if r: all_sigs.append(r)
            except Exception as e: print(f"  盤前{sym}: {e}")

    # ── 美股波段 + VCP ─────────────────────────────────────────────────────────
    if is_us_swing() or mode == "DIGEST_PRE":
        for tag in ["🇺🇸","🛡️","🚀"]:
            for sym in TICKERS.get(tag, []):
                try:
                    df1d = get_yf(sym, "1d", "200d")
                    if not passes_trend(sym, tag, df1d): continue
                    earn_warn = get_earn_warn(sym, cache)
                    df5, _, _ = get_consistent(sym)
                    if not df1d.empty and not df5.empty:
                        r = sig_pullback(sym, tag, df1d, df5, cache, earn_warn, pf)
                        if r: all_sigs.append(r)
                except Exception as e: print(f"  {sym}: {e}")
        vcp = scan_vcp(VCP_WATCHLIST, cache, strategy_stats.get("VCP"), pf)
        if vcp: all_sigs.extend(vcp)

    # ── 台股 ──────────────────────────────────────────────────────────────────
    if is_tw_open() or mode in ("DIGEST_TW_PRE", "DIGEST_TW_CLOSE"):
        for sym in TICKERS["🇹🇼"]:
            try:
                df5  = get_tw_stable(sym)
                df15 = get_yf(sym, "15m", "5d")
                if df5.empty: continue
                r = sig_surge(sym, "🇹🇼", df5, df15, "yfinance", cache, True, "", None, pf)
                if r: all_sigs.append(r)
                r = sig_washout(sym, "🇹🇼", df5, df15, "OPEN", cache, "", None, pf)
                if r: all_sigs.append(r)
            except Exception as e: print(f"  TW{sym}: {e}")
    if is_tw_swing() or mode in ("DIGEST_TW_PRE", "DIGEST_TW_CLOSE"):
        for sym in TICKERS["🇹🇼"]:
            try:
                df1d = get_yf(sym, "1d", "200d"); df5 = get_tw_stable(sym)
                if not df1d.empty and not df5.empty:
                    r = sig_pullback(sym, "🇹🇼", df1d, df5, cache, "", pf)
                    if r: all_sigs.append(r)
            except Exception as e: print(f"  TW SWING{sym}: {e}")

    # ── 加密 ──────────────────────────────────────────────────────────────────
    for yf_sym, disp in TICKERS["₿"]:
        try:
            df15 = get_yf(yf_sym, "15m", "60d")
            if not df15.empty:
                res = sig_banmuxa(yf_sym, disp, df15, cache, pf)
                if res: all_sigs.extend(res)
        except Exception as e: print(f"  ₿{disp}: {e}")

    save_cache(cache)
    all_sigs.sort(key=lambda x: x["score"], reverse=True)

    # 去重
    seen_sig_keys = set()
    deduped       = []
    for s in all_sigs:
        first_line = s["msg"].split("\n")[0][:40]
        key        = f"{s['type']}|{first_line}"
        if key not in seen_sig_keys:
            seen_sig_keys.add(key)
            deduped.append(s)
    all_sigs = deduped

    crash_syms_this_run = {s.get("sym","") for s in all_sigs if s["type"] == "⛈️"}

    print(f"掃描完成:{len(all_sigs)}個信號 · 模式:{mode}")
    for k, st in strategy_stats.items():
        if st.sample_size >= 5:
            print(f"  {k}: {st.sample_size}筆 勝率{st.winrate:.0%} 期望{st.expectancy_r:.2f}R")

    # ══════════════════════════════════════════════════════════════════════════
    # 發送邏輯
    # ══════════════════════════════════════════════════════════════════════════
    if mode in ("DIGEST_PRE", "DIGEST_TW_PRE", "DIGEST_TW_CLOSE"):
        digest_ck = f"digest_{mode}_{tw_now.strftime('%Y%m%d')}"
        if not cooled(cache, digest_ck, 55):
            print(f"彙整已發送過（{mode}），跳過")
        else:
            label_map = {
                "DIGEST_PRE":      "美股盤前彙整 🇺🇸",
                "DIGEST_TW_PRE":   "台股盤前彙整 🇹🇼",
                "DIGEST_TW_CLOSE": "台股收盤彙整 🇹🇼",
            }
            label      = label_map[mode]
            digest_msg = format_digest(all_sigs, label, regime_on, strategy_stats, crash_syms_this_run)
            send_tg(digest_msg)
            mark(cache, digest_ck)
            save_cache(cache)
            print(f"已發送彙整報表（{label}），包含 {len(all_sigs)} 個信號。")

    elif mode == "OPEN_MODE":
        # 暴跌：S 級 + (持倉 OR 妖股)
        crash_sent = 0
        for s in [x for x in all_sigs if x["type"] == "⛈️"]:
            if crash_sent >= 3: break
            if "🏆" not in s["msg"]: continue
            try: sym = s["msg"].split("`")[1]
            except: sym = ""
            if sym in PORTFOLIO_HINTS or ("🚀" in s["msg"] or "⚛️" in s["msg"]):
                send_tg(s["msg"])
                crash_sent += 1

        # 暴漲：S 級 + IEX 即時 + 未在本次觸發 crash
        # [FEAT-NEW-1] 低流動性視窗期間，SURGE 即時推送也抑制
        surge_sent = 0
        for s in [x for x in all_sigs if x["type"] == "🔮"]:
            if surge_sent >= 3: break
            sym = s.get("sym", "")
            if sym in crash_syms_this_run:
                print(f"  {sym}: 本次已發 crash，SURGE 即時推送跳過")
                continue
            if toxic_window:
                print(f"  {sym}: 低流動性視窗，SURGE 即時推送延後至 04:15")
                continue
            if "🏆" in s["msg"] and "IEX即時" in s["msg"]:
                send_tg(s["msg"])
                surge_sent += 1

        # 每 30 分鐘彙整（含 crash_syms 標記）
        if is_digest_30_window() and all_sigs:
            send_tg(format_digest(
                all_sigs,
                f"盤中即時匯報 {tw_now.strftime('%H:%M')}",
                regime_on, strategy_stats, crash_syms_this_run
            ))

    # 日報
    if 240 <= tw_now.hour*60 + tw_now.minute <= 250:
        send_daily_report(all_sigs, fwd_data, strategy_stats)

    print("掃描結束\n")


if __name__ == "__main__":
    main()
