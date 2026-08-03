// 与后端 API 对应的类型定义

export interface User {
  id: number
  username: string
}

export interface UserProfile {
  risk_preference: string
  watchlist: string[]
  updated_at: string | null
}

export interface AuthResponse {
  token: string
  user: User
  profile: UserProfile
}

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
  change_pct: number | null
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

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  created_at: string
  tool_calls?: { name: string; args: Record<string, unknown> }[]
}

export interface ChatSession {
  id: number
  title: string
  created_at: string
  msg_count: number
}

export interface ChatReply {
  reply: string
  tool_calls: { name: string; args: Record<string, unknown> }[]
  session_id: number
}

export interface KlineBar {
  date: string
  open: number
  close: number
  high: number
  low: number
  volume: number
}

export interface QuoteResponse {
  brief: Record<string, unknown>
  kline: KlineBar[]
  tech: Record<string, unknown>
}
