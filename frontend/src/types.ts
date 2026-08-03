// 与后端 API 对应的类型定义

export interface AnalystView {
  role: string
  title: string
  summary: string
  score: number
  evidence: string[]
  risk_points: string[]
}

export interface DebateRound {
  topic: string
  positions: string[]
}

export interface RiskReview {
  approved: boolean
  verdict: string
  max_position_pct: number
  stop_loss_pct: number
}

export interface TradePlan {
  action: string
  target_price: number | null
  stop_loss: number | null
  position_pct: number
  reasoning: string
  risk_warnings: string[]
}

export interface AnalysisResult {
  id: number | null
  ticker: string
  name: string
  price: number | null
  created_at: string
  status: string
  consensus_score: number
  consensus_verdict: string
  analyst_views: AnalystView[]
  debate: DebateRound[]
  risk_review: RiskReview | null
  trade_plan: TradePlan | null
  disclaimer: string
}

export interface LLMConfig {
  provider: string
  base_url: string
  api_key: string
  model: string
  temperature: number
  max_tokens: number
}

export interface HistoryItem {
  id: number
  ticker: string
  created_at: string
  status: string
}
