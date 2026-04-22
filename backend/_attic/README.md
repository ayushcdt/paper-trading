# `_attic/`

Dead code kept around for reference. Not imported anywhere; not on any
runtime path. Safe to delete in the future once you're sure nothing depends
on the historical context it captures.

| File | Why retired |
|---|---|
| `angel_client.py` | Superseded by `data_fetcher.AngelDataFetcher` (singleton, DB-cached, broader symbol coverage). No remaining importers. |
| `backtest_v1.py` | V1 walk-forward backtest. Returns -32% over 10y (broken signals). Replaced by `scripts/backtest_v2.py` (in-sample winner) and `scripts/backtest_v3.py` (the live engine). Output blob `backtest_v1` is still served from `data/backtest_results.json` if anyone wants to compare. |

If you reach for something in here, the right move is usually to delete it
fully — not pull it back into the live tree.
