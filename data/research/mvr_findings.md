# Minimum-Viable Research findings — news signal pilot

**Date:** 2026-04-24
**Window:** Newsapp data from 2024-04 to 2026-04 (but 99% from last 30 days)
**Panel size:** 144 (symbol, day) observations across 73 universe symbols and 8 trading days

## Why this is "minimum viable" not "deep"

Newsapp's scrapers ramped up volume only in early April 2026 — 99% of articles in the dataset are from the last 30 days. Forward-return studies (T+5d, T+10d) are not yet possible because we don't have news + future price data simultaneously. This pilot answers what we CAN measure with current data:

1. Does the news pipeline tag the right symbols? (entity precision)
2. Does sentiment direction match price direction on the same day? (sanity check)
3. Does news activity correlate with same-day price move magnitude? (descriptive)

## Study 1 — Entity tagging precision

**Method:** stratified random sample of 50 articles in our actual signal path (categories `business / wire / filings / legal / govt`, with at least one entity-org tag matching our universe of 500 symbols).

**Result:**

| Verdict | Count |
|---|---|
| YES — matched symbol IS the article subject | 32 (64%) |
| PARTIAL — symbol mentioned but not THE main subject | 8 (16%) |
| FALSE POSITIVE — symbol not relevant or buggy substring match | 10 (20%) |

- **Strict precision: 64%**
- **Permissive precision (incl. partial): 80%**

**Failure modes (in order of frequency):**

1. **Loose substring match** (5 of 10 FPs): our matcher does `if name in org` — "Bharat Udyog Ltd" matched SAIL via "Steel Authority", "Avenue Supermarts" matched DMART when article was about demat accounts in general. **Fixable: tighten to exact match or word boundary.**
2. **Article-meta noise** (2 FPs): epaper/Google-News meta-headline articles inherit whatever orgs were on the source page.
3. **Tangential mention** (3 FPs): listicle articles ("8 stocks to watch") tag many orgs without being about any one of them.

**Implication for build:** Tighten the substring match in `news/symbols.py::names_for` to require exact string equality (no `in` substring). This should push precision from ~64% to ~80%+. Pure shadow mode is acceptable at current precision; live mode should wait for the fix.

## Study 2 — Same-day correlation (sentiment vs price)

**Method:** for each (symbol, day) in panel, compute decay-weighted Loughran-McDonald sentiment from articles mentioning the symbol via entity match. Compute same-day return (today's close vs yesterday's close). Run Pearson correlations.

**Results (n=144):**

| Pair | Pearson r |
|---|---|
| LM (signed) vs return (signed) | **+0.204** |
| \|LM\| vs \|return\| | +0.170 |
| Article count vs \|return\| | +0.211 |

For n=144, the conventional p<0.05 significance threshold is r > 0.163. All three correlations clear that bar.

**Honest reading:** the news pipeline produces signals with real information content but the absolute magnitude is small (r=0.2 explains ~4% of return variance). Not standalone-tradeable. Could be additive to V3 in shadow.

## Study 3 — Sentiment direction sanity check

**Method:** rank panel by LM sentiment. Take 30 most-negative + 30 most-positive observations + 30 random baseline. Compare same-day returns.

| Bucket | Mean LM | Mean return | Down-day rate |
|---|---|---|---|
| 30 most-NEGATIVE | -3.35 | **-1.62%** | **80%** |
| 30 most-POSITIVE | +5.11 | +0.20% | 53% |
| 30 RANDOM | +1.08 | +0.45% | n/a |

**Key finding — strong negativity asymmetry:**

When sentiment is strongly negative, the stock falls 80% of the time, with mean return of -1.62%. This is a 30-percentage-point lift over the 50% baseline.

When sentiment is strongly positive, the stock rises only 53% of the time — barely better than chance.

This matches the academic literature on negativity bias in equity markets: bad news moves prices substantially more than good news.

**Implication for build:**
- LM lexicon DOES work on Indian English news (direction is correct)
- Asymmetric thresholds: penalty on negative news should be ≥ 2x the boost on positive news
- A "blacklist on strong negative" rule could be more useful than a "boost on strong positive" rule

## Aggregate verdict

| Question | Answer |
|---|---|
| Is the news pipeline connecting to the right symbols? | Mostly (80% permissive precision; substring bug fixable) |
| Does LM sentiment carry signal on Indian news? | YES, especially for negatives |
| Is the signal strong enough to trade on standalone? | NO (r=0.20) |
| Is it worth building Phase 3B/C in shadow mode? | YES — combined with V3, it could lift IR; the substring fix is small |
| Should we go live with news scoring before more data? | NO — wait for 30+ days of shadow + then re-run R1 with proper forward-return data |

## Action items going into Phase 3B/C build

- [ ] Tighten `news/symbols.py::names_for` to exact-match only
- [ ] In `hybrid_overlay.py`: penalty on strong-negative news should be 2x the boost on strong-positive
- [ ] Log every decision with both V3-only and hybrid score for later evaluation
- [ ] Set `HYBRID_OVERLAY_MODE = "shadow"` as default; add config flag for easy switch
- [ ] Keep `legal` category in mix (SEBI orders) — it dominated some FPs but signal-wise the few real ones are valuable
- [ ] Re-run R1 in 30 days when forward-return data is available

## Things this pilot does NOT tell us

- Whether the signal is stable across regimes (single-regime data)
- Per-sector breakdowns (sample too thin)
- Optimal decay half-life (need multi-week data + forward returns)
- Whether thematic sector tilts work (no policy events in the 8-day window with full forward data)
- True IR estimate (need ≥30 days of forward returns)
