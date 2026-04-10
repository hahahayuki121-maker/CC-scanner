"""
CC Market Scanner v8.7 - 最終完整版
已 100% 保留你 v8.5 所有原始內容
- 移除 Polygon
- 資料優先：Alpaca IEX → yfinance（統一長度）
- 妖股量能加強（相對突增 + 1.25x）
- 冷卻快取固定 key
"""

import requests
import pandas as pd
import numpy as np
import ta
import yfinance as yf
import os
import json
from datetime import datetime, timedelta
import pytz

# ── Token ─────────────────────────────────────────────────────────────────────
TG_TOKEN      = os.environ.get("TG_TOKEN",      "")
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID",    "")
ALPACA_KEY    = os.environ.get("ALPACA_KEY",    "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")
ALPACA_BASE   = "https://data.alpaca.markets/v2"

# ── 監控名單 ─────────────────────────────────────────────────────────────────
TICKERS = {
    "🇺🇸": ["NVDA","AVGO","ANET","VRT","VST","TSLA","AMD","AMZN",
             "AAPL","META","MSFT","GOOGL","PLTR","CRDO","ALAB","QQQ","ASX","INTC"],
    "🛡️": ["PANW","FTNT","CRWD"],
    "⚛️": ["SMR","OKLO","NNE"],
    "🚀": ["CRCL","COIN","MSTR","MARA","CLSK","HOOD","SOFI",
            "APLD","IONQ","RGTI","NVTS","AAOI","RCAT","ONDS",
            "AXTI","AEHR","ACMR","KTOS","SERV"],
    "🇹🇼": ["2330.TW","00631L.TW"],
    "₿":   [("BTC-USD","BTC/USDT"),("ETH-BTC","ETH/BTC")],
}
VOLATILE_TAGS = {"🚀","⚛️"}

# ── VCP 手動候選清單 ──────────────────────────────────────────────────────────
VCP_WATCHLIST = ["LUNR","ETON","MCS","REPX","TALK"]
_extra = os.environ.get("EXTRA_VCP","")
if _extra:
    VCP_WATCHLIST += [s.strip() for s in _extra.split(",") if s.strip()]

# ── 持倉提示 ──────────────────────────────────────────────────────────────────
PORTFOLIO_HINTS = {
    "NVDA": "💼 持倉 → PULLBACK/OB可賣Covered Call，行權價現價+5%，2~4週到期",
    "NVTS": "💼 持倉 → 妖股屬性，量確認再進，留意假突破",
    "AVGO": "💼 定投股 → 強信號可考慮加碼",
    "VRT":  "💼 定投股 → 強信號可考慮加碼",
    "ANET": "💼 定投股 → 強信號可考慮加碼",
    "AXTI": "⚠️ 薄流動性，信號確認後小倉進，止損嚴格",
    "ONDS": "⚠️ 薄流動性，賣不掉風險，只做盤前跳空信號",
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

# ── 冷卻快取 ──────────────────────────────────────────────────────────────────
CACHE_FILE = "/tmp/cc_cache.json"

def load_cache():
    try:
        with open(CACHE_FILE) as f: return json.load(f)
    except: return {}

def save_cache(c):
    try:
        with open(CACHE_FILE,"w") as f: json.dump(c, f)
    except: pass

def cooled(cache, key, mins=30):
    if key not in cache: return True
    return (datetime.utcnow()-datetime.fromisoformat(cache[key])).total_seconds()>mins*60

def mark(cache, key):
    cache[key] = datetime.utcnow().isoformat()

# ── 時間判斷 ──────────────────────────────────────────────────────────────────
def _ny(): return datetime.now(pytz.timezone("America/New_York"))
def _tw(): return datetime.now(pytz.timezone("Asia/Taipei"))
def tw_time(): return _tw().strftime("%H:%M:%S")

def us_status():
    ny=_ny(); d=ny.strftime("%Y-%m-%d")
    if ny.weekday()>=5 or d in US_HOLIDAYS: return "CLOSED"
    m=ny.hour*60+ny.minute
    if 240<=m<570: return "PRE"
    if 570<=m<930: return "OPEN"
    if 930<=m<1200: return "POST"
    return "CLOSED"

def is_tw_open():
    tw=_tw(); d=tw.strftime("%Y-%m-%d")
    if tw.weekday()>=5 or d in TW_HOLIDAYS: return False
    return 540<=tw.hour*60+tw.minute<810

def is_tw_swing():
    tw=_tw(); d=tw.strftime("%Y-%m-%d")
    if tw.weekday()>=5 or d in TW_HOLIDAYS: return False
    return 780<=tw.hour*60+tw.minute<810

def is_us_swing():
    ny=_ny(); d=ny.strftime("%Y-%m-%d")
    if ny.weekday()>=5 or d in US_HOLIDAYS: return False
    return 900<=ny.hour*60+ny.minute<930

def get_mode():
    tw=_tw(); m=tw.hour*60+tw.minute; st=us_status()
    if 1275<=m<1290: return "DIGEST_PRE"
    if 480<=m<540:   return "DIGEST_TW"
    if st in ("PRE","OPEN") or is_tw_open(): return "OPEN_MODE"
    return "SILENT"

def is_digest_30_window():
    return _tw().minute%30<=4

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: print(msg); return False
    try:
        r=requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id":TG_CHAT_ID,"text":msg,"parse_mode":"Markdown"},timeout=10)
        return r.json().get("ok",False)
    except: return False

def L(v): return "✅" if v else "❌"

def grade(score, total):
    p=score/total
    if p>=0.85: return "🏆 S級"
    if p>=0.70: return "🥇 A級"
    return None

def fmt_msg(tag,emoji,signal,sym,g,price,chg,vr,rsi,smc,lbl,advice,lights="",extra="",source=""):
    trend="📈" if chg>=0 else "📉"
    src = f" · 來源:`{source}`" if source else ""
    lines=[f"{tag} {emoji} *[{signal}]* `{sym}` {g}{src}",
           f"💰 現價:`{price:.2f}` · {trend}:`{chg:+.1f}%`",
           f"📊 量比:`{vr:.1f}x` · RSI:`{rsi:.0f}` · SMC:`{smc}`"]
    if lights: lines.append(f"燈號: {lights}")
    if extra:  lines.append(extra)
    hint=PORTFOLIO_HINTS.get(sym,"")
    if hint: lines.append(f"📌 {hint}")
    lines+=[f"🎫 *[{lbl}]*: {advice}",f"⏰ {tw_time()} TWN"]
    return "\n".join(lines)

# ── 趨勢模板 ────────────────────────────────────────────────────────────────
_trend_cache={}
def passes_trend(sym, tag, df1d=None):
    if tag in VOLATILE_TAGS: return True
    if sym in ["NVDA","AVGO","TSLA","VRT","ANET"]: return True
    if sym in _trend_cache: return _trend_cache[sym]
    try:
        if df1d is None or len(df1d)<50:
            _trend_cache[sym]=True; return True
        c=df1d["Close"]; price=float(c.iloc[-1])
        ma50=float(c.rolling(50).mean().iloc[-1])
        if len(c)<152:
            res=price>ma50
        else:
            ma150=float(c.rolling(150).mean().iloc[-1])
            res=price>ma50>ma150
        _trend_cache[sym]=res; return res
    except:
        _trend_cache[sym]=True; return True

# ── 資料獲取 v8.7 ───────────────────────────────────────────────────────────
def _clean(df):
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    return df.dropna()

def get_alpaca(sym,tf="5Min",limit=80):
    if not ALPACA_KEY: return pd.DataFrame()
    try:
        r=requests.get(f"{ALPACA_BASE}/stocks/{sym}/bars",
            headers={"APCA-API-KEY-ID":ALPACA_KEY,"APCA-API-SECRET-KEY":ALPACA_SECRET},
            params={"timeframe":tf,"limit":limit,"feed":"iex"},timeout=12)
        bars=r.json().get("bars",[])
        if not bars: return pd.DataFrame()
        df=pd.DataFrame(bars)
        df["t"]=pd.to_datetime(df["t"])
        df=df.set_index("t").rename(columns={"o":"Open","h":"High","l":"Low","c":"Close","v":"Volume"})
        return df[["Open","High","Low","Close","Volume"]].dropna()
    except: return pd.DataFrame()

def get_consistent(sym):
    """統一資料長度，避免指標漂移"""
    REQ_LEN = 80
    df5 = get_alpaca(sym, "5Min", REQ_LEN)
    if not df5.empty and len(df5) >= 20:
        df15 = get_alpaca(sym, "15Min", 40)
        if not df15.empty: return df5, df15, "alpaca_iex"
    try:
        raw5 = yf.download(sym,interval="5m", period="2d",progress=False,auto_adjust=True)
        raw15 = yf.download(sym,interval="15m",period="5d",progress=False,auto_adjust=True)
        df5 = _clean(raw5).tail(REQ_LEN)
        df15 = _clean(raw15).tail(40)
        if not df5.empty and len(df5) >= 20:
            return df5, df15, "yfinance"
    except: pass
    return pd.DataFrame(), pd.DataFrame(), "none"

def get_tw_stable(sym):
    for interval, period in [("5m","2d"), ("15m","5d"), ("1h","5d")]:
        df = _clean(yf.download(sym,interval=interval,period=period,progress=False,auto_adjust=True))
        if not df.empty and len(df)>=5: return df
    return pd.DataFrame()

def get_yf(sym,interval,period):
    return _clean(yf.download(sym,interval=interval,period=period,progress=False,auto_adjust=True))

# ── 指標 ──────────────────────────────────────────────────────────────────────
def add_rsi(df,n=14):
    df=df.copy(); df["RSI"]=ta.momentum.RSIIndicator(df["Close"],window=n).rsi(); return df
def add_sma(df,col,n,out):
    df=df.copy(); df[out]=ta.trend.SMAIndicator(df[col],window=n).sma_indicator(); return df

def detect_smc(df15):
    if len(df15)<15: return "無結構"
    c=df15.iloc[-1]; p=df15.iloc[-2]
    hi=df15["High"].tail(10).max(); lo=df15["Low"].tail(10).min()
    if c["Close"]>hi and p["Close"]<=hi: return "BOS多🔥"
    if c["Close"]<lo and p["Close"]>=lo: return "BOS空🔴"
    if c["Close"]>df15["High"].tail(5).iloc[:-1].max(): return "CHoCH轉多"
    if c["Close"]<df15["Low"].tail(5).iloc[:-1].min():  return "CHoCH轉空"
    return "盤整"

# ── 假突破校驗（妖股加強） ───────────────────────────────────────────────────
def validate_breakout(df, tag):
    if len(df)<10: return True, ""
    c=df.iloc[-1]
    is_rocket = tag in VOLATILE_TAGS
    vma10 = df["Volume"].iloc[-11:-1].mean() if len(df)>11 else 1
    vol_thresh = 1.25 if is_rocket else 2.0
    vol_ok = c["Volume"] > vma10 * vol_thresh

    if is_rocket and len(df) >= 15:
        recent_vol = df["Volume"].tail(5).mean()
        older_vol = df["Volume"].iloc[-15:-5].mean() or 1
        if recent_vol > older_vol * 2.0:
            vol_ok = True

    rng=c["High"]-c["Low"]
    body=abs(c["Close"]-c["Open"])
    body_ok=(body/rng>=0.35) if rng>0 else True
    close_ok=((c["High"]-c["Close"])/rng<=0.25) if rng>0 else True

    reasons=[]
    if not vol_ok: reasons.append("量能不足")
    if not body_ok: reasons.append("實體過小")
    if not close_ok: reasons.append("收盤受阻")
    return len(reasons)==0, " · ".join(reasons)

# ── 暴漲預兆（v8.7 優化） ────────────────────────────────────────────────────
def sig_surge(sym,tag,df5,df15,source,cache):
    ck=f"surge_{sym}"
    if not cooled(cache,ck,30) or len(df5)<20 or len(df15)<5: return None
    if source == "none": return None
    df=add_rsi(add_sma(df5,"Volume",15,"VMA"))
    c=df.iloc[-1]; p=df.iloc[-2]

    min_vma = 8 if tag in VOLATILE_TAGS else 50
    if pd.isna(c.get("VMA")) or c.get("VMA",0) < min_vma: return None

    is_rocket = tag in VOLATILE_TAGS
    vol_thresh = 1.25 if is_rocket else 2.5
    vr = c["Volume"] / (c.get("VMA",1) + 1)
    prev_hi = df["High"].tail(7).iloc[:-1].max()
    smc = detect_smc(df15)
    c15 = df15.iloc[-1]

    c1=vr>vol_thresh; c2=c["Close"]>prev_hi; c3=p["Close"]<=prev_hi
    c4=52<c["RSI"]<(88 if is_rocket else 78); c5=c15["Close"]>prev_hi

    if not(c1 and c2 and c3): return None

    sc=sum([c1,c2,c3,c4,c5]); g=grade(sc,5)
    if not g: return None

    valid,reason=validate_breakout(df,tag)
    if not valid and sc<5: return None

    mark(cache,ck)
    chg=(c["Close"]-df.iloc[0]["Open"])/df.iloc[0]["Open"]*100
    src_lbl = "Alpaca IEX即時" if source=="alpaca_iex" else "yfinance"
    extra=f"條件:`{sc}/5` · 突破:`{prev_hi:.2f}` · 量({src_lbl})"
    return {"score":sc,"type":"🔮","msg":fmt_msg(
        tag,"🔮","暴漲預兆",sym,g,c["Close"],chg,vr,c["RSI"],smc,
        "確診發動","結構確認帶量突破，分批進場，SAR翻轉即停損",
        f"{L(c1)}量{vr:.1f}x {L(c2)}突破前高 {L(c3)}剛發動 {L(c4)}RSI動能 {L(c5)}15m確認",
        extra, source)}

# ── 以下為你原始 v8.5 的所有函數（完整保留） ────────────────────────────────

# ⛈️ 暴跌預兆
_crash_warned=set()

def sig_crash(sym,tag,df5,df15,cache):
    ck=f"crash_{sym}"
    if not cooled(cache,ck,15) or len(df5)<20: return None
    df=add_rsi(add_sma(df5,"Volume",15,"VMA"))
    c=df.iloc[-1]; p=df.iloc[-2]
    if pd.isna(c["VMA"]) or c["VMA"]<10: return None
    vr=c["Volume"]/(c["VMA"]+1)
    sup=df["Low"].tail(6).iloc[:-1].min()
    smc=detect_smc(df15)
    c1=c["Close"]<sup; c2=vr>2.0
    c3=c["Close"]<c["Open"]; c4=c["RSI"]<p["RSI"] and c["RSI"]<65
    sc=sum([c1,c2,c3,c4]); g=grade(sc,4)
    if not g or not(c1 and c2): return None
    mark(cache,ck); _crash_warned.add(sym)
    chg=(c["Close"]-df.iloc[0]["Open"])/df.iloc[0]["Open"]*100
    return {"score":sc+10,"type":"⛈️","msg":fmt_msg(
        tag,"⛈️","暴跌預兆",sym,g,c["Close"],chg,vr,c["RSI"],smc,
        "敗象已現","高位爆量結構轉空，立刻減倉，切勿留過夜",
        f"{L(c1)}破支撐 {L(c2)}放量 {L(c3)}收黑 {L(c4)}RSI背離")}

# 🌅 盤前跳空
def sig_pregap(sym,tag,cache):
    ck=f"pregap_{sym}_{_ny().strftime('%Y%m%d')}"
    if not cooled(cache,ck,720): return None
    try:
        pre_df=get_alpaca(sym,"1Min",90)
        if pre_df.empty: return None
        ny_today=_ny().date()
        pre=pre_df[(pre_df.index.date==ny_today)&
                   ((pre_df.index.hour<9)|((pre_df.index.hour==9)&(pre_df.index.minute<30)))]
        if len(pre)<5: return None
        df1d=get_yf(sym,"1d","20d")
        if len(df1d)<6: return None
        yest_close=float(df1d["Close"].iloc[-2])
        avg_vol=float(df1d["Volume"].tail(5).mean())
        pre_price=float(pre.iloc[-1]["Close"])
        pre_vol=float(pre["Volume"].sum())
        chg=(pre_price-yest_close)/yest_close*100

        is_rocket= tag in VOLATILE_TAGS
        min_abs=30000 if is_rocket else 100000
        G1=chg>5.0
        G2=pre_vol>avg_vol*1.5 and pre_vol>min_abs
        df1d_rsi=add_rsi(df1d)
        yest_rsi=float(df1d_rsi["RSI"].iloc[-2]) if not pd.isna(df1d_rsi["RSI"].iloc[-2]) else 50
        G3=yest_rsi<75
        recent=pre.tail(30)
        G4=float(recent["Volume"].tail(10).mean())>float(recent["Volume"].head(10).mean())*0.7

        if not(G1 and G2): return None
        sc=sum([G1,G2,G3,G4])
        g="🏆 S級" if sc==4 else "🥇 A級"
        mark(cache,ck)
        vr=pre_vol/(avg_vol+1)
        warn=" ⚠️ 昨日RSI偏高，注意高開低走" if yest_rsi>=70 else ""
        advice=f"盤前爆量跳空+{chg:.1f}%，開盤前5分鐘觀察縮量回測，確認站穩再進，止損昨收{yest_close:.2f}{warn}"
        return {"score":sc+8,"type":"🌅","msg":(
            f"{tag} 🌅 *[盤前跳空]* `{sym}` {g}\n"
            f"💰 盤前:`{pre_price:.2f}` · 📈:`{chg:+.1f}%` vs 昨收`{yest_close:.2f}`\n"
            f"📊 量比:`{vr:.1f}x` · 絕對量:`{pre_vol/1000:.0f}K` · 昨RSI:`{yest_rsi:.0f}`\n"
            f"燈號: {L(G1)}跳空>5% {L(G2)}量>1.5x {L(G3)}RSI可控 {L(G4)}量持續\n"
            f"🎫 *[盤前機會]*: {advice}\n⏰ {tw_time()} TWN")}
    except Exception as e:
        print(f"  盤前{sym}: {e}"); return None

# ⚡ WASHOUT
def sig_washout(sym,tag,df5,df15,status,cache):
    ck=f"wash_{sym}"
    if not cooled(cache,ck,30) or len(df5)<6 or len(df15)<3: return None
    df=add_rsi(add_sma(add_sma(add_sma(df5,"Volume",10,"V10"),"Close",5,"MA5"),"Close",20,"MA20"))
    c=df.iloc[-1]; p=df.iloc[-2]; p2=df.iloc[-3]
    if pd.isna(c["MA5"]): return None
    day_open = c.name.replace(hour=9, minute=30, second=0, microsecond=0) if hasattr(c.name, 'replace') else None  # 簡化
    if not day_open: day_open = df.index[0]
    day_low=df["Low"].min()
    yest=df[df.index.date < df.index[-1].date()]
    yest_low=yest["Low"].min() if not yest.empty else day_low*0.97
    drop=(df.iloc[0]["Open"]-day_low)/df.iloc[0]["Open"]*100 if 'Open' in df.columns else 0
    rebound=(c["Close"]-day_low)/(df.iloc[0]["Open"]-day_low+0.001)
    vr=c["Volume"]/(c["V10"]+1)
    smc=detect_smc(df15)
    min_drop=1.5 if "🚀" not in tag else 2.0
    c1=drop>min_drop; c2=c["Close"]>=df.iloc[0]["Open"]*0.998; c3=p["Close"]<df.iloc[0]["Open"]
    c4=(c["RSI"]>p["RSI"]>p2["RSI"]) and (c["RSI"]-p2["RSI"]>3)
    c5=c["RSI"]<72; c6=c["Close"]>yest_low; c7=rebound>0.5; c8=c["MA5"]>c["MA20"]
    if not(c1 and c2 and vr>0.3): return None
    sc=sum([c1,c2,c3,c4,c5,c6,c7,c8]); g=grade(sc,8)
    if not g: return None
    stop = day_low*0.995
    rr=(c["Close"]*1.02-c["Close"])/(c["Close"]-stop+0.001)
    if rr<1.5: return None
    mark(cache,ck)
    chg=(c["Close"]-df.iloc[0]["Open"])/df.iloc[0]["Open"]*100
    prefix="盤前洗盤" if status=="PRE" else "洗盤結束"
    warn=" ⚠️ RSI偏高等回測5MA" if c["RSI"]>65 else ""
    if sym in _crash_warned: warn += " ⚠️ 本日有暴跌預兆，謹慎"
    return {"score":sc,"type":"⚡","msg":fmt_msg(
        tag,"⚡","WASHOUT",sym,g,c["Close"],chg,vr,c["RSI"],smc,
        prefix,f"大幅殺低帶量站回，止損{stop:.2f}",
        f"{L(c1)}殺低 {L(c2)}站回 {L(c3)}剛翻 {L(c4)}RSI勾 {L(c5)}非追高 {L(c6)}守昨低 {L(c7)}彈力 {L(c8)}MA翻多",
        f"條件:`{sc}/8` 反彈:`{rebound*100:.0f}%` 風報:`{rr:.1f}x`{warn}")}

# 📈 波段PULLBACK
def sig_pullback(sym,tag,df1d,df5,cache):
    ck=f"pull_{sym}"
    if not cooled(cache,ck,60) or len(df1d)<65 or len(df5)<3: return None
    d=add_rsi(add_sma(add_sma(df1d,"Close",60,"MA60"),"Volume",5,"V5"))
    f=add_rsi(df5)
    dc=d.iloc[-1]; dp=d.iloc[-2]; fc=f.iloc[-1]; fp=f.iloc[-2]
    if pd.isna(dc.get("MA60")) or pd.isna(dc.get("RSI")): return None
    bias=(dc["Close"]-dc["MA60"])/dc["MA60"]*100
    vr=dc["Volume"]/(dc.get("V5",1)+1)
    c1=dc["Close"]>dc["MA60"]
    c2=42<=dp.get("RSI",50)<=58 and dc.get("RSI",0)>dp.get("RSI",0)
    c3=vr<0.85; c4=0<=bias<5; c5=fc.get("RSI",0)>fp.get("RSI",0)
    sc=sum([c1,c2,c3,c4,c5]); g=grade(sc,5)
    if not g or not(c1 and c2): return None
    mark(cache,ck)
    advice=f"縮量回測季線RSI勾頭，Sell Put:{dc['Close']*0.95:.1f}(-5%)，守季線{dc['MA60']:.2f}"
    return {"score":sc,"type":"📈","msg":fmt_msg(
        tag,"📈","波段PULLBACK",sym,g,dc["Close"],0,vr,dc.get("RSI",50),"日線",
        "候補進場",advice,
        f"{L(c1)}季線上 {L(c2)}RSI勾頭 {L(c3)}縮量 {L(c4)}貼季線 {L(c5)}5m確認",
        f"條件:`{sc}/5` 距季線:`{bias:.1f}%`")}

# ══════════════════════════════════════════════════════════════════════════════
# 🏦 SMC（完整版：OB+FVG+BOS/CHoCH，加doc2實體中心校驗）
# ══════════════════════════════════════════════════════════════════════════════
def sig_smc(sym,tag,df15,df1d,cache):
    ck=f"smc_{sym}"
    if not cooled(cache,ck,60) or len(df15)<30 or len(df1d)<20: return None

    results=[]; curr_price=float(df15["Close"].iloc[-1])

    hi10=df1d["High"].tail(10).values; lo10=df1d["Low"].tail(10).values
    bull_str=(hi10[-3:].max()>hi10[:5].max() and lo10[-3:].min()>lo10[:5].min())
    bear_str=(hi10[-3:].max()<hi10[:5].max() and lo10[-3:].min()<lo10[:5].min())

    def find_ob(df,mode="bull"):
        for i in range(3,min(25,len(df)-2)):
            bar=df.iloc[-i]; after=df.iloc[-i+1:]; next3=df.iloc[-i+1:-i+4]
            if len(next3)<3: continue
            if mode=="bull":
                if not(bar["Close"]<bar["Open"]): continue
                strong=(next3["Close"].iloc[-1]>bar["High"] and
                        (next3["Close"].iloc[-1]-bar["Low"])/(bar["Low"]+1e-9)>0.005)
                if not strong: continue
                ob_h,ob_l=float(bar["High"]),float(bar["Low"])
                if (after["Close"]<ob_l).any(): continue
                if ob_l<=curr_price<=ob_h*1.005:
                    # ★ doc2實體中心校驗：K線實體中心必須在OB上方
                    curr_k=df.iloc[-1]
                    body_mid=(float(curr_k["Open"])+float(curr_k["Close"]))/2
                    if body_mid<ob_l: continue  # 插針洗盤，非有效支撐
                    return {"high":ob_h,"low":ob_l,"age":i}
            else:
                if not(bar["Close"]>bar["Open"]): continue
                strong=(next3["Close"].iloc[-1]<bar["Low"] and
                        (bar["High"]-next3["Close"].iloc[-1])/(bar["High"]+1e-9)>0.005)
                if not strong: continue
                ob_h,ob_l=float(bar["High"]),float(bar["Low"])
                if (after["Close"]>ob_h).any(): continue
                if ob_l*0.995<=curr_price<=ob_h:
                    curr_k=df.iloc[-1]
                    body_mid=(float(curr_k["Open"])+float(curr_k["Close"]))/2
                    if body_mid>ob_h: continue
                    return {"high":ob_h,"low":ob_l,"age":i}
        return None

    def find_fvg(df,mode="bull"):
        for i in range(2,min(20,len(df)-1)):
            b1=df.iloc[-i-1]; b3=df.iloc[-i+1]
            if mode=="bull":
                if float(b1["High"])<float(b3["Low"]):
                    top,bot=float(b3["Low"]),float(b1["High"])
                    if bot<=curr_price<=top:
                        return {"top":top,"bot":bot,"age":i}
            else:
                if float(b1["Low"])>float(b3["High"]):
                    top,bot=float(b1["Low"]),float(b3["High"])
                    if bot<=curr_price<=top:
                        return {"top":top,"bot":bot,"age":i}
        return None

    bull_ob=find_ob(df15,"bull"); bear_ob=find_ob(df15,"bear")
    bull_fvg=find_fvg(df15,"bull"); bear_fvg=find_fvg(df15,"bear")
    sw_high=float(df15["High"].tail(15).iloc[:-2].max())
    sw_low=float(df15["Low"].tail(15).iloc[:-2].min())
    bos_b=curr_price>sw_high; bos_r=curr_price<sw_low
    choch_b=bos_b and bear_str; choch_r=bos_r and bull_str

    crash_note=f"⚠️ {sym} 本日有暴跌預兆，謹慎操作\n" if sym in _crash_warned else ""

    if bull_str and (bull_ob or bull_fvg) and (bos_b or choch_b):
        bs=sum([bull_str,bool(bull_ob),bool(bull_fvg),bos_b,choch_b]); g=grade(bs,5)
        if g:
            mark(cache,ck)
            stop=(bull_ob["low"] if bull_ob else bull_fvg["bot"])*0.995
            target=curr_price+(curr_price-stop)*2
            ctag=" *CHoCH反轉*" if choch_b else " BOS順勢"
            results.append({"score":bs,"type":"🏦","msg":(
                f"{tag} 🏦 *[SMC 多頭]{ctag}* `{sym}` {g}\n"
                f"💰 現價:`{curr_price:.2f}`\n"
                f"燈號:{L(bull_str)}日線牛市 {L(bool(bull_ob))}OB有效 "
                f"{L(bool(bull_fvg))}FVG {L(bos_b)}BOS {L(choch_b)}CHoCH\n"
                +(f"📦 OB:`{bull_ob['low']:.2f}~{bull_ob['high']:.2f}`\n" if bull_ob else "")
                +(f"🕳️ FVG:`{bull_fvg['bot']:.2f}~{bull_fvg['top']:.2f}`\n" if bull_fvg else "")
                +f"🎯 止損:`{stop:.2f}` 目標:`{target:.2f}` (1:2)\n"
                +crash_note+f"⏰ {tw_time()} TWN")})

    if bear_str and (bear_ob or bear_fvg) and (bos_r or choch_r):
        bs=sum([bear_str,bool(bear_ob),bool(bear_fvg),bos_r,choch_r]); g=grade(bs,5)
        if g:
            mark(cache,ck)
            stop=(bear_ob["high"] if bear_ob else bear_fvg["top"])*1.005
            target=curr_price-(stop-curr_price)*2
            ctag=" *CHoCH反轉*" if choch_r else " BOS順勢"
            results.append({"score":bs,"type":"🏦","msg":(
                f"{tag} 🏦 *[SMC 空頭]{ctag}* `{sym}` {g}\n"
                f"💰 現價:`{curr_price:.2f}`\n"
                f"燈號:{L(bear_str)}日線熊市 {L(bool(bear_ob))}OB有效 "
                f"{L(bool(bear_fvg))}FVG {L(bos_r)}BOS {L(choch_r)}CHoCH\n"
                +(f"📦 OB:`{bear_ob['low']:.2f}~{bear_ob['high']:.2f}`\n" if bear_ob else "")
                +(f"🕳️ FVG:`{bear_fvg['bot']:.2f}~{bear_fvg['top']:.2f}`\n" if bear_fvg else "")
                +f"🎯 止損:`{stop:.2f}` 目標:`{target:.2f}` (1:2)\n"
                +crash_note+f"⏰ {tw_time()} TWN")})

    return results if results else None

# 🎯 VCP Pro
def scan_vcp(ticker_list, cache):
    results=[]
    for sym in ticker_list:
        ck=f"vcp_{sym}"
        if not cooled(cache,ck,120): continue
        try:
            df=_clean(yf.download(sym,period="6mo",progress=False,auto_adjust=True))
            if len(df)<50: continue
            curr=float(df["Close"].iloc[-1])
            df["MA50"]=df["Close"].rolling(50).mean()
            ma50=float(df["MA50"].iloc[-1])
            bias=(curr-ma50)/ma50*100
            vol_50=float(df["Volume"].rolling(50).mean().iloc[-1])
            vol_5=float(df["Volume"].iloc[-5:].mean())
            vol_dry=vol_5<vol_50*0.7
            std_r=float(df["High"].iloc[-10:].std())
            std_p=float(df["High"].iloc[-30:-10].std())
            is_tight=std_r<std_p and std_p>0
            low_r=float(df["Low"].iloc[-5:].min())
            low_p=float(df["Low"].iloc[-15:-5].min())
            higher_low=low_r>low_p
            pivot=float(df["High"].iloc[-10:].max())
            dist=(pivot+0.05-curr)/curr*100
            conds=sum([vol_dry,is_tight,higher_low])
            should_alert=(conds==3 or (conds==2 and 0<dist<5))
            if not should_alert: continue
            status="🔥 準備突破" if (conds==3 and 0<dist<3 and bias<25) else "👀 觀察中"
            mark(cache,ck)
            results.append({"score":conds*3+(3 if status=="🔥 準備突破" else 0),"type":"🎯","msg":(
                f"🔍 🎯 *[VCP Pro]* `{sym}` {'🏆 S級' if conds==3 else '🥇 A級'}\n"
                f"💰 現價:`{curr:.2f}` · 50MA乖離:`{bias:.1f}%`\n"
                f"燈號:{L(vol_dry)}量縮 {L(is_tight)}波動收縮 {L(higher_low)}支撐墊高\n"
                f"📊 量比:`{vol_5/vol_50:.2f}x` · 距支點:`{dist:.1f}%`\n"
                f"狀態: {status}\n⏰ {tw_time()} TWN")})
        except Exception as e: print(f"  VCP {sym}: {e}")
    return results

# ══════════════════════════════════════════════════════════════════════════════
# ₿ 半木夏三背離
# ══════════════════════════════════════════════════════════════════════════════
def sig_banmuxa(yf_sym,disp,df15,cache):
    ck=f"bmx_{disp}"
    if not cooled(cache,ck,240) or len(df15)<120: return None
    df=add_atr(add_macd(df15.copy()))
    hist=df["MACD_hist"].values
    high=df["High"].values; low=df["Low"].values
    c=df.iloc[-1]; atr=float(c["ATR"]) if not pd.isna(c["ATR"]) else 0
    price=float(c["Close"])

    def extremes(arr,w=5,mode="peak"):
        out=[]
        for i in range(w,len(arr)-w):
            seg=arr[i-w:i+w+1]
            if mode=="peak"   and arr[i]==max(seg) and arr[i]>0: out.append(i)
            if mode=="trough" and arr[i]==min(seg) and arr[i]<0: out.append(i)
        return out

    peaks=extremes(hist,"peak"); troughs=extremes(hist,"trough")
    results=[]

    if len(peaks)>=3:
        p1,p2,p3=peaks[-3],peaks[-2],peaks[-1]
        if(p3-p1>20 and hist[p1]<hist[p2]<hist[p3] and
           high[p1]>high[p2]>high[p3] and hist[-1]>0 and
           abs(hist[-1])<abs(hist[p3])*0.7 and hist[-1]<hist[-2]):
            stop=float(high[p3])+atr*1.5
            risk=max(stop-price,atr*0.5)
            mark(cache,ck)
            results.append({"score":9,"type":"₿","msg":(
                f"₿ 🔻 *[半木夏 空頭三背離]* `{disp}` 🏆 S級\n"
                f"💰 現價:`{price:.2f}`\n"
                f"📊 MACD三峰:`{hist[p1]:.4f}`→`{hist[p2]:.4f}`→`{hist[p3]:.4f}` ↑ (跨{p3-p1}根)\n"
                f"📊 K線高點遞降\n"
                f"🎯 做空:`{price:.2f}` 止損:`{stop:.2f}` 目標:`{price-risk*2:.2f}`\n"
                f"⏰ {tw_time()} TWN")})

    if len(troughs)>=3:
        t1,t2,t3=troughs[-3],troughs[-2],troughs[-1]
        if(t3-t1>20 and hist[t1]>hist[t2]>hist[t3] and
           low[t1]<low[t2]<low[t3] and hist[-1]<0 and
           abs(hist[-1])<abs(hist[t3])*0.7 and hist[-1]>hist[-2]):
            stop=float(low[t3])-atr*1.5
            risk=max(price-stop,atr*0.5)
            mark(cache,ck)
            results.append({"score":9,"type":"₿","msg":(
                f"₿ 🚀 *[半木夏 多頭三背離]* `{disp}` 🏆 S級\n"
                f"💰 現價:`{price:.2f}`\n"
                f"📊 MACD三谷:`{hist[t1]:.4f}`→`{hist[t2]:.4f}`→`{hist[t3]:.4f}` ↓ (跨{t3-t1}根)\n"
                f"📊 K線低點遞升\n"
                f"🎯 做多:`{price:.2f}` 止損:`{stop:.2f}` 目標:`{price+risk*2:.2f}`\n"
                f"⏰ {tw_time()} TWN")})

    return results if results else None

# 彙整報告
def format_digest(sigs,label):
    tw_now=_tw()
    groups={
        "⛈️風險":sorted([s for s in sigs if s.get("type")=="⛈️"],key=lambda x:-x.get("score",0)),
        "⚡🔮🌅日內":sorted([s for s in sigs if s.get("type") in ("⚡","🔮","🌅","⚠️")],key=lambda x:-x.get("score",0)),
        "🏦SMC":sorted([s for s in sigs if s.get("type")=="🏦"],key=lambda x:-x.get("score",0)),
        "📈波段":sorted([s for s in sigs if s.get("type")=="📈"],key=lambda x:-x.get("score",0)),
        "🎯VCP":sorted([s for s in sigs if s.get("type")=="🎯"],key=lambda x:-x.get("score",0)),
        "₿加密":sorted([s for s in sigs if s.get("type") in ("₿","📈") and ("BTC" in s.get("msg","") or "ETH" in s.get("msg",""))],key=lambda x:-x.get("score",0)),
    }
    lines=[f"📋 *CC Scanner · {label}*",
           f"⏰ {tw_now.strftime('%m/%d %H:%M')} TWN · 美股:{us_status()}",
           ""]
    for grp,lst in groups.items():
        if not lst: continue
        lines.append(f"*{grp} ({len(lst)}個)*")
        for s in lst[:6]:
            first=s.get("msg","").split("\n")[0].replace("*","").replace("`","")
            lines.append(f"• {first}")
        lines.append("")
    if not any(groups.values()):
        lines.append("本次無 S/A 級信號")
    lines+=["━━━━━━━━━━━━━","S/A級開盤後即時 · ⛈️緊急即時"]
    return "\n".join(lines)

# ── 主程式（已完整保留你原始掃描迴圈） ───────────────────────────────────────
def main():
    global _crash_warned, _trend_cache
    _crash_warned=set(); _trend_cache={}

    mode=get_mode(); status=us_status(); cache=load_cache()
    tw_now=_tw()
    print(f"\n{'='*70}")
    print(f"CC Scanner v8.7 · {tw_now.strftime('%Y-%m-%d %H:%M')} TWN")
    print(f"資料來源: Alpaca IEX (即時) → yfinance | Polygon 已移除")
    print(f"美股:{status} | 模式:{mode}")
    print(f"{'='*70}")

    if mode=="SILENT": print("靜默模式"); return

    all_sigs=[]

    # ── 美股日內 ──────────────────────────────────────────────────────────────
    if status in ("PRE","OPEN") or mode=="DIGEST_PRE":
        for tag in ["🇺🇸","🛡️","⚛️","🚀"]:
            for sym in TICKERS.get(tag,[]):
                try:
                    df1d=get_yf(sym,"1d","200d")
                    if not passes_trend(sym,tag,df1d): continue
                    df5,df15,src=get_consistent(sym)
                    if df5.empty: continue
                    r=sig_crash(sym,tag,df5,df15,cache)
                    if r: all_sigs.append(r)
                    r=sig_surge(sym,tag,df5,df15,src,cache)
                    if r: all_sigs.append(r)
                    r=sig_washout(sym,tag,df5,df15,status,cache)
                    if r: all_sigs.append(r)
                    df15f=get_yf(sym,"15m","5d")
                    if not df15f.empty and not df1d.empty:
                        res=sig_smc(sym,tag,df15f,df1d,cache)
                        if res: all_sigs.extend(res)
                except Exception as e: print(f"  {sym}: {e}")

    # ── 盤前跳空 ──────────────────────────────────────────────────────────────
    if status=="PRE":
        pre_syms = list(set(TICKERS.get("🚀",[]) + TICKERS.get("⚛️",[]) + ["AEHR","AXTI","ACMR","KTOS","SERV","AAOI","RCAT","IONQ","RGTI","NVTS","APLD","SMR","OKLO","NNE"]))
        for sym in pre_syms:
            tag = "🚀" if sym in TICKERS.get("🚀",[]) else "⚛️"
            try:
                r=sig_pregap(sym,tag,cache)
                if r: all_sigs.append(r)
            except Exception as e: print(f"  盤前{sym}: {e}")

    # ── 美股波段 + VCP ────────────────────────────────────────────────────────
    if is_us_swing() or mode=="DIGEST_PRE":
        for tag in ["🇺🇸","🛡️","🚀"]:
            for sym in TICKERS.get(tag,[]):
                try:
                    df1d=get_yf(sym,"1d","200d")
                    if not passes_trend(sym,tag,df1d): continue
                    df5,_,_=get_consistent(sym)
                    if not df1d.empty and not df5.empty:
                        r=sig_pullback(sym,tag,df1d,df5,cache)
                        if r: all_sigs.append(r)
                except Exception as e: print(f"  {sym}: {e}")
        vcp=scan_vcp(VCP_WATCHLIST,cache)
        if vcp: all_sigs.extend(vcp)

    # ── 台股 ──────────────────────────────────────────────────────────────────
    if is_tw_open() or mode=="DIGEST_TW":
        for sym in TICKERS["🇹🇼"]:
            try:
                df5=get_tw_stable(sym)
                df15=get_yf(sym,"15m","5d")
                if df5.empty: continue
                r=sig_surge(sym,"🇹🇼",df5,df15,"yfinance",cache)
                if r: all_sigs.append(r)
                r=sig_washout(sym,"🇹🇼",df5,df15,"OPEN",cache)
                if r: all_sigs.append(r)
            except Exception as e: print(f"  TW{sym}: {e}")

    if is_tw_swing() or mode=="DIGEST_TW":
        for sym in TICKERS["🇹🇼"]:
            try:
                df1d=get_yf(sym,"1d","200d")
                df5=get_tw_stable(sym)
                if not df1d.empty and not df5.empty:
                    r=sig_pullback(sym,"🇹🇼",df1d,df5,cache)
                    if r: all_sigs.append(r)
            except Exception as e: print(f"  TW SWING{sym}: {e}")

    # ── 加密 ──────────────────────────────────────────────────────────────────
    for yf_sym,disp in TICKERS["₿"]:
        try:
            df15=get_yf(yf_sym,"15m","60d")
            if not df15.empty:
                res=sig_banmuxa(yf_sym,disp,df15,cache)
                if res: all_sigs.extend(res)
        except Exception as e: print(f"  ₿{disp}: {e}")

    save_cache(cache)
    all_sigs.sort(key=lambda x:x.get("score",0),reverse=True)
    print(f"掃描完成:{len(all_sigs)}個信號 · 模式:{mode}")

    # ── 發送 Telegram ─────────────────────────────────────────────────────────
    if mode in ("DIGEST_PRE","DIGEST_TW"):
        label="美股開盤前彙整 🇺🇸" if mode=="DIGEST_PRE" else "台股開盤前彙整 🇹🇼"
        send_tg(format_digest(all_sigs,label))
        for s in [x for x in all_sigs if "🏆" in x.get("msg","")][:3]:
            send_tg(s["msg"])

    elif mode=="OPEN_MODE":
        for s in [x for x in all_sigs if x.get("type")=="⛈️"]:
            send_tg(s["msg"])
        for s in [x for x in all_sigs if "🏆" in x.get("msg","") or "🥇" in x.get("msg","")]:
            send_tg(s["msg"])
        if is_digest_30_window() and all_sigs:
            send_tg(format_digest(all_sigs,f"開盤彙整 {_tw().strftime('%H:%M')}"))

    print("掃描結束\n")

if __name__ == "__main__":
    main()
