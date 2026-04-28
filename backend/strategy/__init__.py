"""Production strategy modules — what runs live.

Currently exports:
  momentum_picker.run_momentum_picker  -- replaces the old V3 adaptive picker.
    Backtest evidence: research/sustainability backtest Q4 (4.3y window).
    Beats V3 adaptive on every metric: +21.69% CAGR vs +3.55% (V3),
    Sharpe 1.35 vs 0.45, Max DD -16% vs -20%.
"""
