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

export interface MinutePoint {
  time: string
  price: number
  avg: number | null
  volume: number | null
}

export interface QuoteResponse {
  brief: Record<string, unknown>
  kline: KlineBar[] | MinutePoint[]
  tech: Record<string, unknown>
  last_close?: number | null
  data_date?: string
  is_today?: boolean
}

export interface NewsItem {
  title: string
  time: string
}

export interface SearchItem {
  market: string
  code: string
  name: string
  type: string
}

export interface AlertItem {
  id: number
  user_id: number
  symbol: string
  symbol_name: string
  alert_type: string  // price_above / price_below / change_pct_up / change_pct_down
  threshold: number
  operator: string
  status: string  // active / triggered / expired
  message: string
  created_at: string
  triggered_at: string | null
  current_price?: number
}

export interface SentimentData {
  hot_rank_trend?: { time: string; rank: number }[]
  xq_followers?: number | null
  vol_ratio?: number | null
  price_5d_chg?: number | null
  momentum?: number | null
  sentiment_score?: number | null
  error?: string
}

export interface DCFResult {
  current_price: number
  intrinsic_value: number
  upside_pct: number
  terminal_value: number
  verdict: string
  assumptions: {
    base_growth: number
    terminal_growth: number
    discount_rate: number
    fcf_margin: number
    net_profit: number
    revenue: number
  }
  projections: { year: number; growth_rate: number; fcf: number; pv: number }[]
  error?: string
}

export interface PortfolioPosition {
  id: number
  symbol: string
  symbol_name: string
  shares: number
  avg_cost: number
  current_price: number | null
  market_value: number | null
  cost: number | null
  pnl: number | null
  pnl_pct: number | null
  change_pct: number | null
}

export interface PortfolioSummary {
  total_market_value: number
  total_cost: number
  total_pnl: number
  total_pnl_pct: number
  position_count: number
}

export interface TransactionItem {
  id: number
  symbol: string
  symbol_name: string
  action: string
  shares: number
  price: number
  total: number
  date: string
  note: string
}

export interface BacktestResult {
  strategy: string
  symbol: string
  period: string
  initial_capital: number
  final_value: number
  total_return: number
  benchmark_return: number
  excess_return: number
  max_drawdown: number
  trades: number
  win_rate: number
  trades_log: { date: string; action: string; price: number; shares: number }[]
  equity_curve: { date: string; value: number }[]
  error?: string
}
