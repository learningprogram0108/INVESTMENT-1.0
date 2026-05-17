"""
資料擷取層 v3
完全移除 yfinance，改用：
  - Alpha Vantage API → VOO、VIX、US10Y、US02Y
  - TWSE Open API    → 0050、00679B（台灣證交所官方）
  - FRED API         → 總經指標（不變）
"""

import time
import requests
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

AV_BASE = "https://www.alphavantage.co/query"
TWSE_BASE = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
STOOQ_BASE = "https://stooq.com/q/d/l/"


# ─────────────────────────────────────────────
# Alpha Vantage 通用擷取
# ─────────────────────────────────────────────

def _av_get(params: dict, av_key: str, retries: int = 3) -> dict:
    params["apikey"] = av_key
    for attempt in range(retries):
        try:
            if attempt > 0:
                time.sleep(15 * attempt)
            r = requests.get(AV_BASE, params=params, timeout=15)
            data = r.json()
            if "Note" in data or "Information" in data:
                # 日配額不會在 60s 內恢復，直接跳過節省時間
                print(f"  [AV] rate limit（日配額已滿），跳過")
                return {}
            return data
        except Exception as e:
            print(f"  [AV ERR] {e}")
            if attempt < retries - 1:
                time.sleep(10)
    return {}


def av_daily_close(symbol: str, av_key: str, days: int = 400) -> pd.Series:
    """
    Alpha Vantage 每日收盤價 → pd.Series（時間升冪）
    使用 full 輸出確保有 400+ 筆，讓 EMA200 有足夠暖機資料
    """
    data = _av_get({
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "outputsize": "full",
    }, av_key)

    ts = data.get("Time Series (Daily)", {})
    if not ts:
        print(f"  [AV] {symbol} 無資料")
        return pd.Series(dtype=float)

    records = {date: float(v["4. close"]) for date, v in ts.items()}
    series = pd.Series(records).sort_index()
    return series.tail(days)


def av_quote(symbol: str, av_key: str) -> dict:
    """Alpha Vantage 即時報價"""
    data = _av_get({
        "function": "GLOBAL_QUOTE",
        "symbol": symbol,
    }, av_key)
    return data.get("Global Quote", {})


# ─────────────────────────────────────────────
# TWSE 台灣證交所（0050、00679B）
# ─────────────────────────────────────────────

def twse_daily_close(stock_no: str, months: int = 14) -> pd.Series:
    """
    台灣證交所 Open API 擷取歷史日收盤價
    若 TWSE 被封鎖則回傳空 Series，由上層改用 Alpha Vantage
    months=14 確保有足夠資料計算 EMA 200（約 200 交易日）
    """
    all_records = {}
    now = datetime.now()

    for i in range(months):
        target = now - timedelta(days=30 * i)
        date_str = target.strftime("%Y%m01")
        try:
            r = requests.get(
                TWSE_BASE,
                params={"stockNo": stock_no, "date": date_str},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Accept": "application/json",
                    "Referer": "https://www.twse.com.tw/",
                },
                timeout=10
            )
            if r.status_code == 403:
                print(f"  [TWSE] {stock_no} 403 封鎖，改用 Alpha Vantage")
                return pd.Series(dtype=float)
            data = r.json()
            if data.get("stat") != "OK":
                continue
            fields = data.get("fields", [])
            rows   = data.get("data", [])
            try:
                date_idx  = fields.index("日期")
                close_idx = fields.index("收盤價")
            except ValueError:
                date_idx, close_idx = 0, 6
            for row in rows:
                try:
                    raw_date  = row[date_idx]
                    raw_close = row[close_idx].replace(",", "")
                    parts = raw_date.split("/")
                    year  = int(parts[0]) + 1911
                    date_key = f"{year}-{parts[1]}-{parts[2]}"
                    all_records[date_key] = float(raw_close)
                except Exception:
                    continue
            time.sleep(0.8)
        except Exception as e:
            print(f"  [TWSE] {stock_no} {date_str}: {e}")
            return pd.Series(dtype=float)

    if not all_records:
        return pd.Series(dtype=float)
    series = pd.Series(all_records).sort_index()
    return series


def twse_latest_close(stock_no: str) -> tuple[float, float]:
    """取最新兩日收盤價 (current, prev)"""
    series = twse_daily_close(stock_no, months=2)
    if len(series) < 2:
        return 0.0, 0.0
    return float(series.iloc[-1]), float(series.iloc[-2])


# ─────────────────────────────────────────────
# Stooq（台股 ETF 免費備援）
# ─────────────────────────────────────────────

def stooq_daily_close(symbol: str, days: int = 500) -> pd.Series:
    """
    Stooq.com 免費日線資料，作為 TWSE 之後的第二備援
    symbol: 如 '0050.tw', '00679b.tw'（小寫）
    """
    from io import StringIO
    try:
        r = requests.get(
            STOOQ_BASE,
            params={"s": symbol.lower(), "i": "d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )
        if r.status_code != 200 or len(r.text.strip()) < 10:
            print(f"  [Stooq] {symbol} 空回應")
            return pd.Series(dtype=float)

        # Stooq 有時會在 CSV 前插入錯誤說明行，找到含 "Date" 的標頭行
        lines = r.text.strip().splitlines()
        header_idx = next(
            (i for i, l in enumerate(lines) if "Date" in l and "Close" in l),
            None
        )
        if header_idx is None:
            print(f"  [Stooq] {symbol} 無 CSV 標頭（可能為維護頁面或無資料）")
            return pd.Series(dtype=float)

        csv_text = "\n".join(lines[header_idx:])
        df = pd.read_csv(StringIO(csv_text), on_bad_lines="skip")

        if "Date" not in df.columns or "Close" not in df.columns:
            print(f"  [Stooq] {symbol} 欄位異常")
            return pd.Series(dtype=float)
        df = df.sort_values("Date").dropna(subset=["Close"])
        series = pd.Series(
            df["Close"].astype(float).values,
            index=df["Date"].values,
        )
        series = series[series > 0]
        if series.empty:
            return pd.Series(dtype=float)
        print(f"  [Stooq] {symbol} 取得 {len(series)} 筆")
        return series.tail(days)
    except Exception as e:
        print(f"  [Stooq] {symbol}: {e}")
        return pd.Series(dtype=float)


# ─────────────────────────────────────────────
# VIX
# ─────────────────────────────────────────────

def _vix_bollinger(series: pd.Series) -> tuple[float, float, bool]:
    """計算 VIX 布林上軌與突破訊號"""
    current = float(series.iloc[-1])
    if len(series) >= 20:
        sma20    = float(series.rolling(20).mean().iloc[-1])
        std20    = float(series.rolling(20).std().iloc[-1])
        upper    = sma20 + 2 * std20
        bb_break = current > upper
    else:
        upper    = current * 1.3
        bb_break = False
    return round(current, 2), round(upper, 2), bb_break


def fetch_vix_av(av_key: str) -> tuple[float, float, bool]:
    """
    VIX 擷取：先走 Stooq（真實 ^VIX），失敗才用 AV VIXY 代理
    """
    # 1. Stooq 直接取 CBOE VIX 指數
    series = stooq_daily_close("^vix", days=60)
    if not series.empty:
        print(f"  [VIX] Stooq ^vix 取得 {len(series)} 筆")
        return _vix_bollinger(series)

    # 2. AV VIXY 代理（AV 免費版不支援 ^VIX index）
    print("  [VIX] Stooq 失敗，改用 AV VIXY...")
    series = av_daily_close("VIXY", av_key, days=60)
    if series.empty:
        series = av_daily_close("UVXY", av_key, days=60)
    if series.empty:
        print("  [VIX] 所有來源失敗，使用預設值 20.0")
        return 20.0, 30.0, False

    return _vix_bollinger(series)


# ─────────────────────────────────────────────
# Alpha Vantage 殖利率（TREASURY_YIELD）
# ─────────────────────────────────────────────

def fetch_treasury_av(av_key: str) -> tuple[float, float]:
    """US10Y 與 US02Y 殖利率（%）"""
    us10y, us02y = 4.3, 4.0
    try:
        data10 = _av_get({
            "function": "TREASURY_YIELD",
            "interval": "daily",
            "maturity": "10year",
        }, av_key)
        vals10 = data10.get("data", [])
        if vals10:
            us10y = round(float(vals10[0]["value"]), 3)
    except Exception as e:
        print(f"  [AV] US10Y: {e}")

    time.sleep(13)   # AV 免費版 5 req/min → 每次間隔 13s

    try:
        data02 = _av_get({
            "function": "TREASURY_YIELD",
            "interval": "daily",
            "maturity": "2year",
        }, av_key)
        vals02 = data02.get("data", [])
        if vals02:
            us02y = round(float(vals02[0]["value"]), 3)
    except Exception as e:
        print(f"  [AV] US02Y: {e}")

    return us10y, us02y


# ─────────────────────────────────────────────
# FRED（不變）
# ─────────────────────────────────────────────

def fetch_fred(series_id: str, api_key: str, limit: int = 24) -> list:
    if not api_key or api_key.lower() == "skip":
        return []
    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(5 * attempt)
            r = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": series_id, "api_key": api_key,
                        "file_type": "json", "sort_order": "desc",
                        "limit": limit},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20
            )
            if not r.text or r.text.strip() == "":
                print(f"  [FRED] {series_id} 空回應，重試...")
                continue
            obs = r.json().get("observations", [])
            vals = []
            for o in reversed(obs):
                try:
                    v = o.get("value", ".")
                    if v != ".":
                        vals.append(float(v))
                except (ValueError, KeyError):
                    pass
            if vals:
                return vals
        except Exception as e:
            print(f"  [FRED] {series_id} attempt {attempt+1}: {e}")
    return []




def av_company_overview(symbol: str, av_key: str) -> dict:
    """
    Alpha Vantage COMPANY_OVERVIEW
    取得 ForwardPE、TrailingPE、PERatio 等估值數據
    """
    data = _av_get({
        "function": "COMPANY_OVERVIEW",
        "symbol": symbol,
    }, av_key)
    return data


def fetch_cape_erp(symbol: str, av_key: str, rf: float) -> tuple:
    """
    從 AV OVERVIEW 抓取真實 PE，計算 CAPE 代理值與 ERP
    回傳 (cape, erp, predicted_10y_return)
    """
    overview = av_company_overview(symbol, av_key)
    if not overview:
        return None, None, None

    # 優先用 Forward PE，其次 Trailing PE
    fpe = None
    for key in ["ForwardPE", "TrailingPE", "PERatio"]:
        val = overview.get(key)
        if val and val != "None" and val != "-":
            try:
                fpe = float(val)
                break
            except ValueError:
                pass

    if not fpe or fpe <= 0:
        return None, None, None

    # CAPE 代理：用 Trailing PE × 0.95（保守估計）
    from src.quant_engine import calc_erp, calc_cape_10y
    cape = round(fpe * 0.95, 1)
    erp  = calc_erp(fpe, rf)
    pred = calc_cape_10y(cape)
    return cape, erp, pred

def fetch_hy_spread_fred(fred_key: str) -> float:
    data = fetch_fred("BAMLH0A0HYM2", fred_key, limit=5)
    return round(data[-1], 2) if data else 4.5
