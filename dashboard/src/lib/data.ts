import { Redis } from '@upstash/redis'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || '',
  token: process.env.KV_REST_API_TOKEN || '',
})

// Types
export interface StockPick {
  rank: number
  symbol: string
  name: string
  sector: string
  cmp: number
  target: number
  target_2: number
  stop_loss: number
  risk_pct: number
  upside_pct: number
  conviction: 'HIGH' | 'MEDIUM' | 'LOW'
  fundamentals_status?: 'ok' | 'cached' | 'stale' | 'unavailable'
  scores: {
    quality: number | null
    momentum: number
    technical: number
    overall: number
  }
  momentum: {
    rs_6m: number
    rs_3m: number
  }
  technicals: {
    trend: string
    rsi: number
    volume_ratio: number
    setup: string
  }
  levels: {
    support: number[]
    resistance: number[]
    atr: number
  }
  reasoning: string
}

export interface MacroIndicator {
  status: 'ok' | 'unavailable'
  value?: number
  unit?: string
  change_pct?: number
  trend_30d_pct?: number
  interpretation?: string
}

export interface MarketData {
  nifty: {
    value: number
    change: number
    change_pct: number
    trend: string
    above_20ema: boolean
    above_50ema: boolean
    above_200ema: boolean
    rsi: number
    support: number[]
    resistance: number[]
    distance_200dma: number
  }
  banknifty: {
    value: number
    change: number
    change_pct: number
    trend: string
  }
  vix: {
    value: number
    interpretation: string
    message: string
  }
  sectors: {
    leaders: { name: string; return_1w: number }[]
    laggards: { name: string; return_1w: number }[]
  }
  macro?: {
    generated_at: string
    from_cache: boolean
    usd_inr: MacroIndicator
    us_10y: MacroIndicator
    brent_crude: MacroIndicator
    fii_dii: {
      status: 'ok' | 'unavailable'
      date?: string
      fii_net_cr?: number
      dii_net_cr?: number
    }
  }
  stance: {
    stance: string
    score: number
    cash_recommendation: number
    contributions?: Record<string, number>
  }
  outlook: string
  strategy: string
}

export interface MFRecommendation {
  category: string
  funds: {
    name: string
    amc: string
    expense_ratio: number
    recommendation: string
    allocation_pct: number
    reasoning: string
  }[]
}

export interface AnalysisData {
  generated_at: string
  market: MarketData
  stocks: {
    generated_at: string
    market_condition: string
    picks: StockPick[]
  }
  mutualfunds: {
    generated_at: string
    recommendations: MFRecommendation[]
    allocation_note: string
  }
}

// Default/demo data when KV is empty
const demoData: AnalysisData = {
  generated_at: new Date().toISOString(),
  market: {
    nifty: {
      value: 22150,
      change: 98.5,
      change_pct: 0.45,
      trend: 'BULLISH',
      above_20ema: true,
      above_50ema: true,
      above_200ema: true,
      rsi: 58,
      support: [21800, 21500],
      resistance: [22400, 22700],
      distance_200dma: 8.5
    },
    banknifty: {
      value: 47200,
      change: 150,
      change_pct: 0.32,
      trend: 'BULLISH'
    },
    vix: {
      value: 14.2,
      interpretation: 'LOW_FEAR',
      message: 'Low volatility - favorable for trending markets'
    },
    sectors: {
      leaders: [
        { name: 'IT', return_1w: 3.2 },
        { name: 'Pharma', return_1w: 2.8 },
        { name: 'Auto', return_1w: 1.9 }
      ],
      laggards: [
        { name: 'Realty', return_1w: -2.1 },
        { name: 'PSU', return_1w: -1.5 },
        { name: 'Metal', return_1w: -0.8 }
      ]
    },
    stance: {
      stance: 'CAUTIOUSLY_BULLISH',
      score: 45,
      cash_recommendation: 15
    },
    outlook: 'Nifty 50 at 22,150 maintains bullish trend. Price is 8.5% above 200 DMA - long-term trend intact. Key support at 21,800. Resistance at 22,400. India VIX at 14.2 - Low volatility - favorable for trending markets. Sector leadership from IT, Pharma, Auto. Weakness in Realty, PSU, Metal.',
    strategy: 'Deploy capital gradually. Keep 15% cash for volatility. Focus on quality stocks with strong fundamentals. Overweight: IT, Pharma, Auto. Underweight/Avoid: Realty, PSU, Metal.'
  },
  stocks: {
    generated_at: new Date().toISOString(),
    market_condition: 'CAUTIOUSLY_BULLISH',
    picks: [
      {
        rank: 1,
        symbol: 'RELIANCE',
        name: 'Reliance Industries Ltd',
        sector: 'Oil & Gas',
        cmp: 2450.50,
        target: 2750,
        target_2: 2900,
        stop_loss: 2320,
        risk_pct: 5.3,
        upside_pct: 12.2,
        conviction: 'HIGH',
        scores: { quality: 82, momentum: 88, technical: 75, overall: 81.5 },
        momentum: { rs_6m: 1.18, rs_3m: 1.12 },
        technicals: { trend: 'BULLISH', rsi: 52, volume_ratio: 1.8, setup: 'Near 20 EMA support | RSI healthy (52) | High volume (1.8x)' },
        levels: { support: [2380, 2320], resistance: [2550, 2750], atr: 45 },
        reasoning: 'Strong momentum with 18% RS over Nifty. Pulled back to 20 EMA offering low-risk entry. Quality score 82/100. O2C segment growth driving earnings.'
      },
      {
        rank: 2,
        symbol: 'TCS',
        name: 'Tata Consultancy Services',
        sector: 'IT',
        cmp: 3850,
        target: 4200,
        target_2: 4400,
        stop_loss: 3680,
        risk_pct: 4.4,
        upside_pct: 9.1,
        conviction: 'HIGH',
        scores: { quality: 90, momentum: 75, technical: 80, overall: 81 },
        momentum: { rs_6m: 1.12, rs_3m: 1.08 },
        technicals: { trend: 'STRONG_BULLISH', rsi: 55, volume_ratio: 1.4, setup: 'Strong uptrend | RSI healthy (55)' },
        levels: { support: [3750, 3680], resistance: [4000, 4200], atr: 58 },
        reasoning: 'IT sector leader showing strength. Excellent quality score of 90. Strong deal wins and margin expansion story.'
      }
    ]
  },
  mutualfunds: {
    generated_at: new Date().toISOString(),
    recommendations: [],
    allocation_note: 'Run analysis to get MF recommendations'
  }
}

export async function getAnalysisData(): Promise<AnalysisData> {
  try {
    if (process.env.KV_REST_API_URL && process.env.KV_REST_API_TOKEN) {
      const data = await redis.get<AnalysisData>('analysis')
      if (data) {
        return data
      }
    }
  } catch (error) {
    console.error('Error fetching from Redis:', error)
  }

  // Return demo data if Redis is empty or not configured
  return demoData
}

export async function setAnalysisData(data: AnalysisData): Promise<boolean> {
  try {
    await redis.set('analysis', data)
    return true
  } catch (error) {
    console.error('Error saving to Redis:', error)
    return false
  }
}
