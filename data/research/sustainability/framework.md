# Sustainability Framework — what "sustainable" actually means

Drafted before research results land, so we have shared definitions to interpret them against.

## Why "sustainable" not "fix"

A "fix" optimizes for the next 3 months of paper P&L. A sustainable system is designed to survive **5 years of regime changes, drawdowns, life events, and your own behavioral lapses** without needing constant babysitting or emergency rewrites.

Most retail algo systems die from one of these failure modes — not because the alpha was bad, but because the system wasn't designed to keep running:

| Failure mode | Why it kills |
|---|---|
| Overfit to historical window | Works in backtest, dies in live regime change |
| Concentration risk | One sector blow-up = portfolio blow-up |
| No drawdown discipline | User pulls plug at -30% right before recovery |
| Maintenance debt | Constant code changes to keep it working = unsustainable |
| Cost spiral | More turnover → more costs → worse returns → more tweaks |
| Tax inefficiency | Short-term gains at 15% destroy returns at scale |
| Single point of failure | Angel down OR machine dies → you can't trade |
| Goal drift | Chasing recent winners = optimizing for noise |

## 8 dimensions of sustainability

Each phase of the plan must demonstrably contribute to ≥3 of these:

1. **Financial sustainability** — positive risk-adjusted returns CONSISTENTLY across regimes. Not just one good year.
2. **Psychological sustainability** — drawdowns small enough that you don't pull the plug. -10 to -20% is the realistic ceiling for retail self-managed.
3. **Operational sustainability** — runs reliably without daily intervention. Failure-tolerant.
4. **Cognitive sustainability** — every decision is auditable; you understand WHY any trade was made.
5. **Capital sustainability** — survives tail events (COVID-style shocks, geopolitical, demonetization). Built-in tail-risk gates.
6. **Time sustainability** — doesn't require monitoring during work hours. Push notifications on critical events only.
7. **Cost sustainability** — total cost (transactions + infra + tax) stays under 1.5% of capital annually as scale grows.
8. **Knowledge sustainability** — system improves over time from accumulated data. Shadow logging → validated → live → measured.

## Architecture target — "Multi-sleeve risk-managed system"

```
┌─────────────────────────────────────────────────────────────────┐
│  STRATEGY ENSEMBLE — 3 sleeves with low correlation             │
│   - Momentum sleeve: trending names, ride winners               │
│   - Quality/value sleeve: stable compounders                    │
│   - News-event sleeve: catalyst-driven (validated separately)   │
│   Each sleeve gets its own risk budget (e.g. 50/30/20)          │
└─────────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────────┐
│  RISK MANAGER — applies to all sleeves uniformly                │
│   - Volatility-targeted position sizing (target 15% portfolio   │
│     vol; high-vol stocks get smaller slots)                     │
│   - Sector concentration cap (max 30% in one industry)          │
│   - Position correlation cap (no 3+ highly correlated holdings) │
│   - Portfolio drawdown circuit breaker (halt new entries at     │
│     -8% from peak; resume after 50% recovery)                   │
│   - Tail-event halt (VIX > 30 OR Nifty -3% in a day = day off)  │
│   - Tax-aware exits (avoid short-term capital gains > 1y rule)  │
└─────────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────────┐
│  EXECUTION — what we already have, hardened                     │
│   - Pending opens/closes (Option C, already shipped)            │
│   - Slippage tracking (intended vs actual fill price)           │
│   - Symmetric next-day-open close fills (Phase 4C)              │
└─────────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────────┐
│  MONITORING + AUDIT                                             │
│   - Daily P&L attribution per sleeve                            │
│   - Anomaly detection (unusual DD, churn, sector skew)          │
│   - Telegram alerts ONLY on critical events (no noise)          │
│   - Single-button kill switch                                   │
│   - Audit log: every decision with full reasoning preserved     │
└─────────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────────┐
│  CONTINUOUS VALIDATION                                          │
│   - Weekly auto-backtest (walk-forward) of running config       │
│   - Monthly performance attribution report                      │
│   - Quarterly strategy review: which sleeves earned their risk  │
│     budget? Reallocate. Defund failing sleeves. Add new ones.   │
└─────────────────────────────────────────────────────────────────┘
```

## Capital deployment ladder (from paper to real money)

Real-money go-live shouldn't be a single moment. It's a graduation through gates:

| Stage | Capital | Duration | Gates to pass |
|---|---|---|---|
| Paper trading | ₹10K virtual | Already running | System uptime > 95%, no critical bugs in 30d |
| Real money — micro | ₹50K | 60 days | Paper Sharpe > 0.7 over 60d; max DD < 12% |
| Real money — small | ₹2L | 90 days | Live Sharpe > 0.6 with Sharpe > paper Sharpe ÷ 1.5; alpha vs Nifty positive |
| Real money — meaningful | ₹10L | 6 months | Live alpha sustained; max DD < 15% |
| Real money — scaled | ₹25L+ | 12 months | All gates passed; cost ratio < 1.5%; one full regime cycle observed |

At ANY stage, if drawdown exceeds the stage's max-DD threshold for >30 days, system halts and we revisit.

## What this framework asks the research to answer

1. **Is momentum_agg stable across regimes?** — Q1 will show 12mo rolling CAGR
2. **Does an ensemble actually reduce DD?** — Q3 vs Q4 (single + risk overlay)
3. **Are the strategies uncorrelated enough to ensemble?** — Q2 correlation matrix
4. **How much DD can risk overlay actually save?** — Q4 vs momentum_agg baseline

If results say "no" to any of these, the sustainability plan adapts:
- No regime stability → need stronger regime gates / smaller exposure
- No correlation benefit → ensemble doesn't help; pick best single strategy
- DD overlay barely works → need stricter sizing or accept the ceiling on capital

## Anti-patterns this framework explicitly rejects

- **"Tweak parameters until backtest looks great"** — overfitting machine
- **"Add more rules to fix edge cases"** — patch debt; brittle
- **"Use last 6 months of data to retune"** — chasing noise
- **"Build features without measuring"** — what we did wrong with V3 layers
- **"Single strategy + leverage to scale return"** — recipe for blowup
- **"Aggressive DD limits that trigger constantly"** — death by 1000 cuts
