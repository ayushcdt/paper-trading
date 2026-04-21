"""
Main Analysis Generator
Runs all analysis and uploads to Vercel dashboard
"""

import json
import requests
import numpy as np
from datetime import datetime
from pathlib import Path
from logzero import logger, logfile


class NumpyEncoder(json.JSONEncoder):
    """Handle numpy types in JSON serialization"""
    def default(self, obj):
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

from config import VERCEL_CONFIG
from stock_picker import run_stock_picker, run_stock_picker_v2, run_stock_picker_v3
from market_analyzer import run_market_analysis
from paper.runner import run_paper_runner
from news.feed import fetch_news_snapshot
from alerts.notifier import check_and_alert

# Setup logging
LOG_DIR = Path(__file__).parent.parent / "data"
LOG_DIR.mkdir(exist_ok=True)
logfile(str(LOG_DIR / "analysis.log"), maxBytes=1e6, backupCount=3)

DATA_DIR = Path(__file__).parent.parent / "data"
HISTORY_DIR = DATA_DIR / "history"


def save_local(data: dict, filename: str):
    """Save data locally as JSON"""
    filepath = DATA_DIR / filename
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, cls=NumpyEncoder)
    logger.info(f"Saved locally: {filepath}")


def save_history_snapshot(data: dict) -> Path:
    """
    Snapshot the combined analysis with a timestamp filename so we can build
    a track record over time (used by /performance dashboard page).
    """
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    fp = HISTORY_DIR / f"{ts}.json"
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, cls=NumpyEncoder)
    logger.info(f"Snapshot saved: {fp}")
    return fp


def upload_history_to_vercel(data: dict) -> bool:
    """Push this snapshot to a date-keyed slot in Redis (via Vercel API)."""
    try:
        url = f"{VERCEL_CONFIG['app_url']}/api/history"
        date_key = datetime.now().strftime("%Y-%m-%d")
        payload = {"date": date_key, "snapshot": data}
        response = requests.post(
            url,
            data=json.dumps(payload, cls=NumpyEncoder),
            headers={
                "Content-Type": "application/json",
                "x-api-key": VERCEL_CONFIG["secret_key"],
            },
            timeout=30,
        )
        if response.status_code == 200:
            logger.info("History snapshot uploaded to Vercel")
            return True
        logger.warning(f"History upload failed: {response.status_code} - {response.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"History upload error (non-fatal): {e}")
        return False


def upload_to_vercel(data: dict) -> bool:
    """Upload analysis data to Vercel dashboard (auth via x-api-key)"""
    try:
        url = f"{VERCEL_CONFIG['app_url']}/api/update-data"
        json_data = json.dumps(data, cls=NumpyEncoder)

        response = requests.post(
            url,
            data=json_data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": VERCEL_CONFIG["secret_key"],
            },
            timeout=30,
        )

        if response.status_code == 200:
            logger.info("Successfully uploaded to Vercel")
            return True
        if response.status_code == 401:
            logger.error("Upload rejected (401): UPDATE_SECRET mismatch between local config and Vercel env var")
            return False
        logger.error(f"Upload failed: {response.status_code} - {response.text}")
        return False

    except requests.exceptions.ConnectionError:
        logger.warning("Vercel app unreachable. Data saved locally only.")
        return False
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return False


def generate_mf_recommendations() -> list:
    """
    Generate mutual fund recommendations
    Based on asset allocation principles
    """
    return [
        {
            "category": "Large Cap Index",
            "funds": [
                {
                    "name": "UTI Nifty 50 Index Fund - Direct",
                    "amc": "UTI",
                    "expense_ratio": 0.10,
                    "recommendation": "CORE_HOLDING",
                    "allocation_pct": 25,
                    "reasoning": "Lowest cost Nifty 50 exposure. Core holding for any portfolio. Provides broad market exposure with minimal tracking error."
                },
                {
                    "name": "HDFC Index Fund - Nifty 50 Plan - Direct",
                    "amc": "HDFC",
                    "expense_ratio": 0.10,
                    "recommendation": "ALTERNATIVE",
                    "allocation_pct": 0,
                    "reasoning": "Alternative to UTI with similar performance. Choose based on existing AMC relationships."
                }
            ]
        },
        {
            "category": "Large & Mid Cap",
            "funds": [
                {
                    "name": "Nifty Next 50 Index Fund - Direct",
                    "amc": "UTI/ICICI",
                    "expense_ratio": 0.15,
                    "recommendation": "CORE_HOLDING",
                    "allocation_pct": 15,
                    "reasoning": "Exposure to next 50 companies by market cap. Higher growth potential than Nifty 50 with moderate risk."
                }
            ]
        },
        {
            "category": "Flexi Cap",
            "funds": [
                {
                    "name": "Parag Parikh Flexi Cap Fund - Direct",
                    "amc": "PPFAS",
                    "expense_ratio": 0.63,
                    "recommendation": "HIGH_CONVICTION",
                    "allocation_pct": 20,
                    "reasoning": "Excellent fund management with value investing philosophy. International diversification built-in. Consistent long-term performer."
                },
                {
                    "name": "HDFC Flexi Cap Fund - Direct",
                    "amc": "HDFC",
                    "expense_ratio": 0.74,
                    "recommendation": "ALTERNATIVE",
                    "allocation_pct": 0,
                    "reasoning": "Strong fund manager with long track record. Good for those preferring pure domestic exposure."
                }
            ]
        },
        {
            "category": "Mid Cap",
            "funds": [
                {
                    "name": "Nifty Midcap 150 Index Fund - Direct",
                    "amc": "Motilal Oswal",
                    "expense_ratio": 0.20,
                    "recommendation": "CORE_HOLDING",
                    "allocation_pct": 15,
                    "reasoning": "Low-cost midcap exposure. Historically outperforms large caps over long periods. Higher volatility - suitable for 7+ year horizon."
                }
            ]
        },
        {
            "category": "Small Cap",
            "funds": [
                {
                    "name": "Nippon India Small Cap Fund - Direct",
                    "amc": "Nippon",
                    "expense_ratio": 0.67,
                    "recommendation": "SATELLITE",
                    "allocation_pct": 10,
                    "reasoning": "Top-performing small cap fund. High volatility but excellent long-term returns. Only for aggressive investors with 10+ year horizon."
                }
            ]
        },
        {
            "category": "International",
            "funds": [
                {
                    "name": "Motilal Oswal S&P 500 Index Fund - Direct",
                    "amc": "Motilal Oswal",
                    "expense_ratio": 0.49,
                    "recommendation": "CORE_HOLDING",
                    "allocation_pct": 10,
                    "reasoning": "US market exposure for geographic diversification. Rupee depreciation acts as natural hedge. Access to global tech leaders."
                }
            ]
        },
        {
            "category": "Debt",
            "funds": [
                {
                    "name": "HDFC Short Term Debt Fund - Direct",
                    "amc": "HDFC",
                    "expense_ratio": 0.25,
                    "recommendation": "STABILITY",
                    "allocation_pct": 5,
                    "reasoning": "For stability and emergency fund allocation. Low volatility with ~7% expected returns. Park funds for tactical deployment."
                }
            ]
        }
    ]


def run_full_analysis():
    """
    Run complete analysis workflow
    """
    logger.info("=" * 50)
    logger.info(f"Starting analysis at {datetime.now().isoformat()}")
    logger.info("=" * 50)

    # 1. Market Analysis
    logger.info("\n[1/3] Running market analysis...")
    market_analysis = run_market_analysis()
    save_local(market_analysis, "market.json")

    # 2. Stock Picks -- V3 adaptive (regime classifier + variants + guardrails)
    logger.info("\n[2/3] Running V3 adaptive stock picker...")
    v3 = run_stock_picker_v3(max_picks=15)
    stocks_data = {
        "generated_at": datetime.now().isoformat(),
        "generated_by": "Artha 2.0 -- V3 Adaptive",
        "strategy_version": "v3-adaptive",
        "validation_status": "Backtested 2015-2025; use judgment before committing real capital",
        "market_condition": market_analysis.get('stance', {}).get('stance', 'UNKNOWN'),
        "regime": v3.get("regime"),
        "regime_reason": v3.get("regime_reason"),
        "regime_inputs": v3.get("regime_inputs"),
        "variant": v3.get("variant"),
        "variant_reason": v3.get("variant_reason"),
        "deploy_pct": v3.get("deploy_pct"),
        "kill_switch_active": v3.get("kill_switch_active"),
        "kill_switch_reason": v3.get("kill_switch_reason"),
        "picks": v3.get("picks", []),
    }
    save_local(stocks_data, "stocks.json")

    # 3. MF Recommendations
    logger.info("\n[3/3] Generating MF recommendations...")
    mf_data = {
        "generated_at": datetime.now().isoformat(),
        "recommendations": generate_mf_recommendations(),
        "allocation_note": "Suggested allocation for moderate risk investor with 7+ year horizon. Adjust based on your risk profile and goals."
    }
    save_local(mf_data, "mutualfunds.json")

    # News enrichment FIRST so it lands in the saved file (was a bug -- save came before)
    logger.info("\n[News] Fetching news flow + sentiment for picks...")
    news_block = {"status": "skipped"}
    news_snap = None
    try:
        pick_symbols = [p["symbol"] for p in stocks_data.get("picks", [])]
        news_snap = fetch_news_snapshot(pick_symbols)
        news_block = {
            "status": news_snap.status,
            "article_count": news_snap.article_count,
            "macro": news_snap.macro,
            "symbol_mentions": news_snap.symbol_mentions,
            "earnings_titles": news_snap.earnings_titles[:10],
        }
        logger.info(f"News: {news_snap.article_count} articles, status={news_snap.status}")
    except Exception as e:
        logger.warning(f"News fetch failed (non-fatal): {e}")
        news_block = {"status": "error", "error": str(e)}

    # Combine all data
    combined_data = {
        "generated_at": datetime.now().isoformat(),
        "market": market_analysis,
        "stocks": stocks_data,
        "mutualfunds": mf_data,
        "news": news_block,
    }

    # Save combined locally
    save_local(combined_data, "analysis_combined.json")

    # Append to local history archive
    save_history_snapshot(combined_data)

    # Paper-trade reconciliation: opens/closes virtual positions based on current picks
    logger.info("\n[Paper] Reconciling virtual portfolio with today's picks...")
    try:
        paper_snap = run_paper_runner()
        combined_data["paper_portfolio"] = {
            "equity": paper_snap["current_equity"],
            "total_pnl_pct": paper_snap["total_pnl_pct"],
            "open_positions": paper_snap["open_positions_count"],
        }
    except Exception as e:
        logger.warning(f"Paper runner failed (non-fatal): {e}")

    # Upload to Vercel
    logger.info("\nUploading to Vercel dashboard...")
    upload_success = upload_to_vercel(combined_data)
    upload_history_to_vercel(combined_data)

    # Sync all auxiliary JSON blobs (paper portfolio, variant health, etc.)
    try:
        from scripts.sync_to_vercel import sync_all
        sync_all()
    except Exception as e:
        logger.warning(f"Blob sync failed (non-fatal): {e}")

    # Fire alerts for any state changes since last run
    try:
        target_json_path = DATA_DIR / "target_state.json"
        target_state = json.loads(target_json_path.read_text(encoding="utf-8")) if target_json_path.exists() else None
        guardrail_path = DATA_DIR / "guardrail_state.json"
        guardrail_state = json.loads(guardrail_path.read_text(encoding="utf-8")) if guardrail_path.exists() else None
        paper_path = DATA_DIR / "paper_portfolio.json"
        paper_snap = json.loads(paper_path.read_text(encoding="utf-8")) if paper_path.exists() else None
        news_snap_obj = news_snap if 'news_snap' in locals() else None
        n_alerts = check_and_alert(
            stocks_data=stocks_data,
            paper_snap=paper_snap,
            news_snap=news_snap_obj,
            guardrail_state=guardrail_state,
            target_state=target_state,
        )
        if n_alerts:
            logger.info(f"Alerts dispatched: {n_alerts}")
    except Exception as e:
        logger.warning(f"Alert dispatch failed (non-fatal): {e}")

    # Summary
    logger.info("\n" + "=" * 50)
    logger.info("ANALYSIS COMPLETE")
    logger.info("=" * 50)
    logger.info(f"Market Stance: {market_analysis.get('stance', {}).get('stance', 'N/A')}")
    logger.info(f"Nifty: {market_analysis.get('nifty', {}).get('value', 'N/A')}")
    logger.info(f"Stock Picks: {len(stocks_data.get('picks', []))} (variant: {stocks_data.get('variant', 'n/a')}, regime: {stocks_data.get('regime', 'n/a')})")
    logger.info(f"Vercel Upload: {'Success' if upload_success else 'Failed/Pending'}")
    logger.info(f"Local files saved to: {DATA_DIR}")
    logger.info("=" * 50)

    return combined_data


if __name__ == "__main__":
    run_full_analysis()
