"""
Macro overlay -- adds the global/INR context that drives Indian markets:
- USD/INR (rupee strength; weak rupee hurts importers, helps IT/pharma exporters)
- US 10Y yield (FII flow driver; high yield = capital flight risk from EMs)
- Brent crude (inflation + OMC + paint/aviation cost driver)
- India VIX trend (already in market_analyzer; we add 30d percentile here)
- FII/DII net flows (best-effort -- NSE blocks bots; degrades to "unavailable")

Each indicator carries a `status` so the dashboard can flag stale/missing data.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from logzero import logger


CACHE_PATH = Path(__file__).parent.parent / "data" / "macro_cache.json"
CACHE_TTL_HOURS = 4  # macro doesn't change minute-to-minute


def _yf_latest(ticker: str, lookback_days: int = 30) -> dict:
    """Return latest close + change_pct for a yfinance ticker, or {} on failure."""
    try:
        df = yf.download(
            ticker,
            period=f"{lookback_days}d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if df.empty or len(df) < 2:
            return {}
        # yfinance returns multi-index columns when given a single ticker in some versions
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        latest = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        change_pct = ((latest - prev) / prev) * 100 if prev else 0
        # 30d trend: latest vs 30-day average
        trend_30d = ((latest - float(close.mean())) / float(close.mean())) * 100
        return {
            "value": round(latest, 2),
            "change_pct": round(change_pct, 2),
            "trend_30d_pct": round(trend_30d, 2),
            "status": "ok",
        }
    except Exception as e:
        logger.warning(f"yfinance fetch failed for {ticker}: {e}")
        return {"status": "unavailable", "error": str(e)}


def fetch_usd_inr() -> dict:
    out = _yf_latest("INR=X")
    if out.get("status") == "ok":
        # Annotate: rising INR=X means rupee weakening
        out["interpretation"] = (
            "Rupee weakening -- bearish for importers/oil/aviation, bullish for IT/pharma exporters"
            if out["change_pct"] > 0.2
            else "Rupee strengthening -- bullish for importers/oil, bearish for exporters"
            if out["change_pct"] < -0.2
            else "Rupee stable"
        )
    return out


def fetch_us_10y() -> dict:
    out = _yf_latest("^TNX")
    if out.get("status") == "ok":
        # ^TNX is US 10Y in basis-points/10 (i.e., 4.5% shows as 45). Normalize.
        if out["value"] > 20:
            out["value"] = round(out["value"] / 10, 2)
        out["unit"] = "%"
        out["interpretation"] = (
            "Yields rising -- FII outflow risk from EMs"
            if out["trend_30d_pct"] > 2
            else "Yields falling -- favorable for FII inflows to India"
            if out["trend_30d_pct"] < -2
            else "Yields range-bound"
        )
    return out


def fetch_brent() -> dict:
    out = _yf_latest("BZ=F")
    if out.get("status") == "ok":
        out["unit"] = "USD/bbl"
        out["interpretation"] = (
            "Crude rising -- inflationary, hurts OMCs/paints/aviation, helps upstream"
            if out["trend_30d_pct"] > 5
            else "Crude falling -- disinflationary tailwind for India"
            if out["trend_30d_pct"] < -5
            else "Crude stable"
        )
    return out


def fetch_fii_dii() -> dict:
    """
    Best-effort scrape of NSE FII/DII data. NSE blocks plain bots so we use
    browser headers + a session that touches the homepage first to grab cookies.
    Degrades to {'status': 'unavailable'} -- the dashboard will flag it.
    """
    try:
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/123.0 Safari/537.36",
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.nseindia.com/reports/fii-dii",
            }
        )
        # Warm-up to get cookies
        s.get("https://www.nseindia.com/", timeout=8)
        r = s.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=10)
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            raise ValueError("empty response")

        # Latest day's FII + DII equity activity
        fii = next((row for row in rows if "FII" in row.get("category", "").upper()), None)
        dii = next((row for row in rows if "DII" in row.get("category", "").upper()), None)
        if not fii or not dii:
            raise ValueError("could not locate FII/DII rows")

        return {
            "status": "ok",
            "date": fii.get("date"),
            "fii_net_cr": round(float(fii.get("netValue", 0)), 2),
            "dii_net_cr": round(float(dii.get("netValue", 0)), 2),
        }
    except Exception as e:
        logger.warning(f"NSE FII/DII fetch failed (NSE blocks scrapers; expected): {e}")
        return {"status": "unavailable", "error": str(e)}


def _load_cache() -> dict | None:
    if not CACHE_PATH.exists():
        return None
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(cache["fetched_at"])
        if datetime.now() - fetched_at < timedelta(hours=CACHE_TTL_HOURS):
            return cache["data"]
    except Exception:
        return None
    return None


def _save_cache(data: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps({"fetched_at": datetime.now().isoformat(), "data": data}, indent=2),
        encoding="utf-8",
    )


def run_macro_analysis(use_cache: bool = True) -> dict:
    """Returns combined macro snapshot. Cached for CACHE_TTL_HOURS."""
    if use_cache:
        cached = _load_cache()
        if cached is not None:
            logger.info("Using cached macro data (<4h old)")
            cached["from_cache"] = True
            return cached

    logger.info("Fetching macro indicators ...")
    data = {
        "generated_at": datetime.now().isoformat(),
        "from_cache": False,
        "usd_inr": fetch_usd_inr(),
        "us_10y": fetch_us_10y(),
        "brent_crude": fetch_brent(),
        "fii_dii": fetch_fii_dii(),
    }
    _save_cache(data)
    return data


def macro_stance_contribution(macro: dict) -> int:
    """
    Translate macro into stance score contribution. Conservative -- macro is
    context, not a primary signal.
    """
    score = 0

    # Crude: rising oil = inflation pressure on Indian equities
    crude = macro.get("brent_crude", {})
    if crude.get("status") == "ok":
        if crude["trend_30d_pct"] > 10:
            score -= 5
        elif crude["trend_30d_pct"] < -10:
            score += 3

    # US 10Y: rising yields hurt EM equities
    yld = macro.get("us_10y", {})
    if yld.get("status") == "ok":
        if yld["trend_30d_pct"] > 5:
            score -= 5
        elif yld["trend_30d_pct"] < -5:
            score += 3

    # USD/INR: weak rupee pressures imports/inflation
    inr = macro.get("usd_inr", {})
    if inr.get("status") == "ok":
        if inr["trend_30d_pct"] > 2:
            score -= 3

    # FII/DII: heaviest signal when available
    fd = macro.get("fii_dii", {})
    if fd.get("status") == "ok":
        net = fd.get("fii_net_cr", 0)
        if net > 3000:
            score += 8
        elif net > 1000:
            score += 4
        elif net < -3000:
            score -= 8
        elif net < -1000:
            score -= 4

    return score


if __name__ == "__main__":
    import pprint

    out = run_macro_analysis(use_cache=False)
    pprint.pprint(out)
    print(f"\nStance contribution: {macro_stance_contribution(out)}")
