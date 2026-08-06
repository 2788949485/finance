// 后端 API 封装
import type {
  AnalysisResult, AuthResponse, ChatMessage, ChatReply, ChatSession,
  HistoryItem, LLMConfig, NewsItem, QuoteResponse, SearchItem, UserProfile,
  AlertItem, SentimentData, DCFResult, PortfolioPosition, PortfolioSummary,
  TransactionItem, BacktestResult,
} from './types'

const TOKEN_KEY = 'financecrew_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const resp = await fetch(url, { headers, ...options })
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const data = await resp.json()
      if (data.detail) detail = data.detail
    } catch { /* ignore */ }
    if (resp.status === 401 && !url.includes('/auth/')) {
      setToken(null)
    }
    throw new Error(detail)
  }
  return resp.json() as Promise<T>
}

export const api = {
  // 通用 GET / POST（带 token，自动 json）
  get: <T = any>(url: string) => request<T>(url),
  post: <T = any>(url: string, body?: any) =>
    request<T>(url, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),

  getConfig: () => request<LLMConfig>('/api/config'),

  saveConfig: (cfg: LLMConfig) =>
    request<LLMConfig>('/api/config', { method: 'PUT', body: JSON.stringify(cfg) }),

  getProviders: () => request<Record<string, { base_url: string; model: string }>>('/api/providers'),

  runAnalysis: (ticker: string, topic?: string) =>
    request<AnalysisResult>('/api/analysis', {
      method: 'POST',
      body: JSON.stringify({ ticker, topic: topic || null }),
    }),

  streamAnalysis: async (
    ticker: string, topic: string | undefined,
    onEvent: (ev: any) => void,
    mode: string = 'standard'
  ): Promise<void> => {
    const token = getToken()
    const resp = await fetch('/api/analysis/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
      body: JSON.stringify({ ticker, topic: topic || null, mode }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const reader = resp.body!.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const parts = buf.split('\n\n')
      buf = parts.pop() ?? ''
      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data: ')) continue
        try { onEvent(JSON.parse(line.slice(6))) } catch { /* skip */ }
      }
    }
  },

  getAnalysis: (id: number) => request<{ result: AnalysisResult | null }>(`/api/analysis/${id}`),

  getHistory: () => request<HistoryItem[]>('/api/history'),

  deleteHistory: (id: number) => request<{ status: string }>(`/api/history/${id}`, { method: 'DELETE' }),

  getQuote: (symbol: string, days = 60, mode = 'day', fresh = 0, all = 0) =>
    request<QuoteResponse>(`/api/quote/${symbol}?days=${days}&mode=${mode}&fresh=${fresh}&all=${all}`),

  getNews: (symbol: string) => request<{ symbol: string; news: NewsItem[] }>(`/api/news/${symbol}`),

  getIndustry: (symbol: string) => request<{ peers: { code: string; name: string; pe: number; pb: number; change_pct: number; market_cap: number; is_target: boolean }[]; avg_pe: number | null; avg_pb: number | null }>(`/api/industry/${symbol}`),

  search: (q: string) => request<{ query: string; results: SearchItem[] }>(`/api/search/${encodeURIComponent(q)}`),

  health: () => request<{ status: string }>('/api/health'),

  getHotStocks: () => request<{ code: string; name: string; change_pct: number }[]>('/api/hot'),

  // 认证
  register: (username: string, password: string, inviteCode?: string) =>
    request<AuthResponse>('/api/auth/register', { method: 'POST', body: JSON.stringify({ username, password, invite_code: inviteCode || '' }) }),

  login: (username: string, password: string) =>
    request<AuthResponse>('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),

  me: () => request<{ user: { id: number; username: string }; profile: UserProfile }>('/api/auth/me'),

  getProfile: () => request<UserProfile>('/api/auth/profile'),

  saveProfile: (patch: Partial<UserProfile>) =>
    request<UserProfile>('/api/auth/profile', { method: 'PUT', body: JSON.stringify(patch) }),

  // 对话
  newChat: () => request<{ session_id: number }>('/api/chat/session', { method: 'POST' }),

  listChats: () => request<ChatSession[]>('/api/chat/sessions'),

  deleteChat: (sessionId: number) =>
    request<{ deleted: number }>(`/api/chat/${sessionId}`, { method: 'DELETE' }),

  chatMessages: (sessionId: number) => request<ChatMessage[]>(`/api/chat/${sessionId}/messages`),

  searchChat: (q: string) => request<{ id: number; session_id: number; role: string; content: string; created_at: string; session_title: string }[]>(`/api/chat/search?q=${encodeURIComponent(q)}`),

  sendChat: (message: string, sessionId?: number) =>
    request<ChatReply>(`/api/chat`, { method: 'POST', body: JSON.stringify({ message, session_id: sessionId }) }),

  // 流式对话（SSE）：返回解析后的完整回复
  streamChat: async (message: string, sessionId: number | undefined, onEvent: (ev: any) => void): Promise<string> => {
    const token = getToken()
    const resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
      body: JSON.stringify({ message, session_id: sessionId }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const reader = resp.body!.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    let reply = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const parts = buf.split('\n\n')
      buf = parts.pop() ?? ''
      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data: ')) continue
        try {
          const ev = JSON.parse(line.slice(6))
          if (ev.type === 'msg') reply = ev.content
          onEvent(ev)
        } catch { /* 忽略坏帧 */ }
      }
    }
    return reply
  },

  // 价格预警
  listAlerts: (status = 'all') =>
    request<AlertItem[]>(`/api/alerts?status=${status}`),

  createAlert: (symbol: string, symbolName: string, alertType: string, threshold: number) =>
    request<AlertItem>('/api/alerts', {
      method: 'POST',
      body: JSON.stringify({ symbol, symbol_name: symbolName, alert_type: alertType, threshold }),
    }),

  deleteAlert: (id: number) =>
    request<{ status: string }>(`/api/alerts/${id}`, { method: 'DELETE' }),

  reactivateAlert: (id: number) =>
    request<{ status: string }>(`/api/alerts/${id}/reactivate`, { method: 'POST' }),

  checkAlerts: () =>
    request<{ triggered: AlertItem[]; count: number }>('/api/alerts/check', { method: 'POST' }),

  // 情绪面数据
  getSentiment: (symbol: string) =>
    request<SentimentData>(`/api/sentiment/${symbol}`),

  // DCF估值
  getDCF: (symbol: string) =>
    request<DCFResult>(`/api/dcf/${symbol}`),

  // 投资组合
  getPortfolio: () =>
    request<{ positions: PortfolioPosition[]; summary: PortfolioSummary }>(`/api/portfolio`),

  buyStock: (symbol: string, shares: number, price: number, date?: string) =>
    request<{ symbol: string; action: string }>(`/api/portfolio/buy`, {
      method: 'POST', body: JSON.stringify({ symbol, shares, price, date: date || '' }),
    }),

  sellStock: (symbol: string, shares: number, price: number, date?: string) =>
    request<{ symbol: string; action: string }>(`/api/portfolio/sell`, {
      method: 'POST', body: JSON.stringify({ symbol, shares, price, date: date || '' }),
    }),

  removePosition: (symbol: string) =>
    request<{ status: string }>(`/api/portfolio/${symbol}`, { method: 'DELETE' }),

  getTransactions: () =>
    request<TransactionItem[]>(`/api/portfolio/transactions`),

  // 回测
  getBacktest: (symbol: string, strategy: string, days: number, enable_cost: number = 1, params?: Record<string, any>) => {
    let url = `/api/backtest/${symbol}?strategy=${strategy}&days=${days}&enable_cost=${enable_cost}`
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== null && v !== '') url += `&${k}=${v}`
      }
    }
    return request<BacktestResult>(url)
  },

  // 多LLM对比
  compareLLM: (prompt: string, models: { name: string; base_url: string; api_key: string; model: string }[]) =>
    request<{ results: { name: string; model: string; response: string; latency_ms: number; error: string }[] }>(
      '/api/llm-compare', { method: 'POST', body: JSON.stringify({ prompt, models }) }
    ),

  // 修改密码
  changePassword: (oldPassword: string, newPassword: string) =>
    request<{ status: string }>('/api/auth/change-password', {
      method: 'POST', body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    }),

  // per-user LLM 配置
  getUserLLMConfig: () =>
    request<LLMConfig & { api_key: string }>('/api/auth/llm-config'),

  saveUserLLMConfig: (cfg: Partial<LLMConfig>) =>
    request<LLMConfig & { api_key: string }>('/api/auth/llm-config', {
      method: 'PUT', body: JSON.stringify(cfg),
    }),

  // 管理员
  isAdmin: () => request<{ is_admin: boolean }>('/api/auth/is-admin'),
  adminUsers: () => request<any[]>('/api/admin/users'),
  toggleUserActive: (id: number) => request<{ status: string }>(`/api/admin/users/${id}/toggle-active`, { method: 'POST' }),
  setUserAdmin: (id: number, isAdmin: boolean) => request<{ status: string }>(`/api/admin/users/${id}/set-admin`, { method: 'POST', body: JSON.stringify({ is_admin: isAdmin }) }),
  createInvite: (note: string) => request<{ code: string }>('/api/admin/invite-codes', { method: 'POST', body: JSON.stringify({ note }) }),
  adminInvites: () => request<any[]>('/api/admin/invite-codes'),
  adminAuditLogs: () => request<any[]>('/api/admin/audit-logs'),
  adminStats: () => request<Record<string, any>>('/api/admin/stats'),
}
