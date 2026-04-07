"""
CC Market Scanner v7.1
數據源:Alpaca API(美股即時)/ yfinance(台股、加密、日線)
標籤: 權值 | 資安 | 核能 | 妖股 | 中概 | 台股 | ₿加密
策略: 暴跌預兆 | 暴漲預兆 | 強勢突破 | WASHOUT | 波段PULLBACK
等級:S級/A級(B級已移除)
"""

import requests
import pandas as pd
import ta
import yfinance as yf
import os
from datetime import datetime, date
import pytz
# ── Token ─────────────────────────────────────────────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
ALPACA_KEY = os.environ.get("ALPACA_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")
ALPACA_BASE = "https://data.alpaca.markets/v2"
# ── 監控名單 ──────────────────────────────────────────────────────────────────
TICKERS = {
" ": ["NVDA","AVGO","ANET","VRT","VST","TSLA","AMD","AMZN","AAPL","META","MSFT","GOOGL","PLTR","CRDO"],
" ": ["PANW","FTNT","CRWD"],
" ": ["SMR","OKLO","NNE"],
" ": ["COIN","MSTR","MARA","CLSK","HOOD","SOFI","APLD","IONQ","RGTI","NVTS","AAOI","RCAT"],
# 移除:ONDS(流動性不足)、PATH(陷阱股)、PL(流動性差)
" ": ["BABA","PDD","FUTU"],
" ": ["2330.TW","00631L.TW"],
"₿": [("BTC-USD","BTC/USDT"),("ETH-BTC","ETH/BTC")],
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
# ── 時間判斷 ──────────────────────────────────────────────────────────────────
def _now_ny():
    return datetime.now(pytz.timezone("America/New_York"))
def _now_tw():
    return datetime.now(pytz.timezone("Asia/Taipei"))
def us_market_status():
    ny = _now_ny()
d = ny.strftime("%Y-%m-%d")
if ny.weekday() >= 5 or d in US_HOLIDAYS:
    return "CLOSED"
m = ny.hour * 60 + ny.minute
if 240 <= m < 570: return "PRE"
if 570 <= m < 930: return "OPEN"
if 930 <= m < 1200: return "POST"
    return "CLOSED"
def is_tw_open():
tw = _now_tw()
d = tw.strftime("%Y-%m-%d")
if tw.weekday() >= 5 or d in TW_HOLIDAYS: return False
m = tw.hour * 60 + tw.minute
    return 540 <= m < 810
def is_tw_swing():
    tw = _now_tw()
d = tw.strftime("%Y-%m-%d")
if tw.weekday() >= 5 or d in TW_HOLIDAYS: return False
m = tw.hour * 60 + tw.minute
    return 780 <= m < 810
def is_us_swing():
    ny = _now_ny()
d = ny.strftime("%Y-%m-%d")
if ny.weekday() >= 5 or d in US_HOLIDAYS: return False
m = ny.hour * 60 + ny.minute
    return 900 <= m < 930
# ── Telegram ──────────────────────────────────────────────────────────────────
def send_tg(msg):
